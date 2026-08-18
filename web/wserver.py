# This file is a part of NEO-WZML (github.com/irisXDR/NEO-WZML)

from uvloop import install

install()

from asyncio import sleep, to_thread
from hashlib import blake2b
from urllib.parse import urlparse
from contextlib import asynccontextmanager
from logging import INFO, WARNING, FileHandler, StreamHandler, basicConfig, getLogger

from aioaria2 import Aria2HttpClient
from aiohttp.client_exceptions import ClientError
from aioqbt.client import create_client
from fastapi import FastAPI, Request, HTTPException
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.templating import Jinja2Templates
from aioqbt.exc import AQError

from web.nodes import (
    extract_file_ids,
    make_tree,
    make_mega_tree,
    make_terabox_tree,
    make_rclone_tree,
)
from web.mega_selection_store import (
    get_file_list as get_mega_file_list,
    update_selected_ids as set_mega_selected_ids,
)
from web.terabox_selection_store import (
    get_file_list as get_terabox_file_list,
    update_selected_ids as set_terabox_selected_ids,
)
from web.rclone_selection_store import (
    get_file_list as get_rclone_file_list,
    update_selected_ids as set_rclone_selected_ids,
)
from aiohttp import ClientSession

getLogger("httpx").setLevel(WARNING)
getLogger("aiohttp").setLevel(WARNING)


def _derive_web_pin(token):
    digits = "".join(n for n in str(token) if n.isdigit())
    if len(digits) >= 4:
        return digits[:4]
    h = blake2b(str(token).encode("utf-8"), digest_size=4).hexdigest()
    return "".join(c for c in h if c.isdigit())[:4].zfill(4)

aria2 = None
qbittorrent = None
proxy_session: ClientSession | None = None
import os as _os
import secrets as _secrets

SERVICES = {
    "qbit": {
        "url": _os.environ.get("QBIT_WEB_URL", "http://localhost:8090"),
        "password": _os.environ.get("QBIT_WEB_PASSWORD") or _secrets.token_urlsafe(16),
    },
}


@asynccontextmanager
async def lifespan(app: FastAPI):
    global aria2, qbittorrent, proxy_session
    aria2 = Aria2HttpClient("http://localhost:6800/jsonrpc")
    qbittorrent = await create_client(
        SERVICES["qbit"]["url"].rstrip("/") + "/api/v2/"
    )
    proxy_session = ClientSession(auto_decompress=True)
    yield
    await aria2.close()
    await qbittorrent.close()
    if proxy_session is not None:
        await proxy_session.close()


app = FastAPI(lifespan=lifespan)


templates = Jinja2Templates(directory="web/templates/")

basicConfig(
    format="[%(asctime)s] [%(levelname)s] - %(message)s",
    datefmt="%d-%b-%y %I:%M:%S %p",
    handlers=[FileHandler("log.txt"), StreamHandler()],
    level=INFO,
)

LOGGER = getLogger(__name__)


async def re_verify(paused, resumed, hash_id):
    k = 0
    while True:
        res = await qbittorrent.torrents.files(hash_id)
        verify = True
        for i in res:
            if i.index in paused and i.priority != 0:
                verify = False
                break
            if i.index in resumed and i.priority == 0:
                verify = False
                break
        if verify:
            break
        LOGGER.info("Reverification Failed! Correcting stuff...")
        await sleep(0.5)
        if paused:
            try:
                await qbittorrent.torrents.file_prio(
                    hash=hash_id, id=paused, priority=0
                )
            except (ClientError, TimeoutError, Exception, AQError) as e:
                LOGGER.error(f"{e} Errored in reverification paused!")
        if resumed:
            try:
                await qbittorrent.torrents.file_prio(
                    hash=hash_id, id=resumed, priority=1
                )
            except (ClientError, TimeoutError, Exception, AQError) as e:
                LOGGER.error(f"{e} Errored in reverification resumed!")
        k += 1
        if k > 5:
            return False
    LOGGER.info(f"Verified! Hash: {hash_id}")
    return True


@app.get("/app/files", response_class=HTMLResponse)
async def files(request: Request):
    return templates.TemplateResponse(request, "page.html")


@app.api_route(
    "/app/files/torrent", methods=["GET", "POST"], response_class=HTMLResponse
)
async def handle_torrent(request: Request):
    params = request.query_params

    if not (gid := params.get("gid")):
        return JSONResponse(
            {
                "files": [],
                "engine": "",
                "error": "GID is missing",
                "message": "GID not specified",
            }
        )

    if not (pin := params.get("pin")):
        return JSONResponse(
            {
                "files": [],
                "engine": "",
                "error": "Pin is missing",
                "message": "PIN not specified",
            }
        )

    code = _derive_web_pin(gid)
    if len(code) < 4 or code != pin:
        return JSONResponse(
            {
                "files": [],
                "engine": "",
                "error": "Invalid pin",
                "message": "The PIN you entered is incorrect",
            }
        )

    if request.method == "POST":
        if not (mode := params.get("mode")):
            return JSONResponse(
                {
                    "files": [],
                    "engine": "",
                    "error": "Mode is not specified",
                    "message": "Mode is not specified",
                }
            )
        data = await request.json()
        if mode == "rename":
            if len(gid) > 20:
                await handle_rename(gid, data)
                content = {
                    "files": [],
                    "engine": "",
                    "error": "",
                    "message": "Rename successfully.",
                }
            else:
                content = {
                    "files": [],
                    "engine": "",
                    "error": "Rename failed.",
                    "message": "Cannot rename aria2c torrent file",
                }
        else:
            selected_files, unselected_files = extract_file_ids(data)
            if len(gid) > 20:
                await set_qbittorrent(gid, selected_files, unselected_files)
            else:
                selected_files = ",".join(selected_files)
                await set_aria2(gid, selected_files)
            content = {
                "files": [],
                "engine": "",
                "error": "",
                "message": "Your selection has been submitted successfully.",
            }
    else:
        try:
            if len(gid) > 20:
                res = await qbittorrent.torrents.files(gid)
                content = make_tree(res, "qbittorrent")
            else:
                res = await aria2.getFiles(gid)
                op = await aria2.getOption(gid)
                fpath = f"{op['dir']}/"
                content = make_tree(res, "aria2", fpath)
        except (ClientError, TimeoutError, Exception, AQError) as e:
            LOGGER.error(str(e))
            content = {
                "files": [],
                "engine": "",
                "error": "Error getting files",
                "message": str(e),
            }
    return JSONResponse(content)


@app.api_route(
    "/app/files/mega", methods=["GET", "POST"], response_class=HTMLResponse
)
async def handle_mega(request: Request):
    params = request.query_params
    gid_raw = params.get("gid", "")

    if not gid_raw:
        return JSONResponse({
            "files": [], "engine": "", "error": "GID is missing",
            "message": "GID not specified",
        })

    if not (pin := params.get("pin")):
        return JSONResponse({
            "files": [], "engine": "", "error": "Pin is missing",
            "message": "PIN not specified",
        })

    gid = gid_raw.replace("mega_", "", 1) if gid_raw.startswith("mega_") else gid_raw
    code = _derive_web_pin(gid_raw)
    if len(code) < 4 or code != pin:
        return JSONResponse({
            "files": [], "engine": "", "error": "Invalid pin",
            "message": "The PIN you entered is incorrect",
        })

    if request.method == "POST":
        data = await request.json()
        selected_files, _ = extract_file_ids(data)
        ok = await to_thread(set_mega_selected_ids, gid, set(selected_files))
        return JSONResponse({
            "files": [], "engine": "", "error": "" if ok else "GID not found",
            "message": "Selection submitted" if ok else "Task expired",
        })
    else:
        file_list = await to_thread(get_mega_file_list, gid)
        if file_list is None:
            return JSONResponse({
                "files": [], "engine": "", "error": "Not found",
                "message": "Task not found or expired",
            })
        content = make_mega_tree(file_list)
        return JSONResponse(content)


@app.api_route(
    "/app/files/terabox", methods=["GET", "POST"], response_class=HTMLResponse
)
async def handle_terabox(request: Request):
    params = request.query_params
    gid_raw = params.get("gid", "")

    if not gid_raw:
        return JSONResponse({
            "files": [], "engine": "", "error": "GID is missing",
            "message": "GID not specified",
        })

    if not (pin := params.get("pin")):
        return JSONResponse({
            "files": [], "engine": "", "error": "Pin is missing",
            "message": "PIN not specified",
        })

    gid = gid_raw.replace("terabox_", "", 1) if gid_raw.startswith("terabox_") else gid_raw
    code = _derive_web_pin(gid_raw)
    if len(code) < 4 or code != pin:
        return JSONResponse({
            "files": [], "engine": "", "error": "Invalid pin",
            "message": "The PIN you entered is incorrect",
        })

    if request.method == "POST":
        data = await request.json()
        selected_files, _ = extract_file_ids(data)
        ok = await to_thread(set_terabox_selected_ids, gid, set(selected_files))
        return JSONResponse({
            "files": [], "engine": "", "error": "" if ok else "GID not found",
            "message": "Selection submitted" if ok else "Task expired",
        })
    else:
        file_list = await to_thread(get_terabox_file_list, gid)
        if file_list is None:
            return JSONResponse({
                "files": [], "engine": "", "error": "Not found",
                "message": "Task not found or expired",
            })
        content = make_terabox_tree(file_list)
        return JSONResponse(content)


@app.api_route(
    "/app/files/rclone", methods=["GET", "POST"], response_class=HTMLResponse
)
async def handle_rclone(request: Request):
    params = request.query_params
    gid_raw = params.get("gid", "")

    if not gid_raw:
        return JSONResponse({
            "files": [], "engine": "", "error": "GID is missing",
            "message": "GID not specified",
        })

    if not (pin := params.get("pin")):
        return JSONResponse({
            "files": [], "engine": "", "error": "Pin is missing",
            "message": "PIN not specified",
        })

    gid = gid_raw.replace("rclone_", "", 1) if gid_raw.startswith("rclone_") else gid_raw
    code = _derive_web_pin(gid_raw)
    if len(code) < 4 or code != pin:
        return JSONResponse({
            "files": [], "engine": "", "error": "Invalid pin",
            "message": "The PIN you entered is incorrect",
        })

    if request.method == "POST":
        data = await request.json()
        selected_files, _ = extract_file_ids(data)
        ok = await to_thread(set_rclone_selected_ids, gid, set(selected_files))
        return JSONResponse({
            "files": [], "engine": "", "error": "" if ok else "GID not found",
            "message": "Selection submitted" if ok else "Task expired",
        })
    else:
        file_list = await to_thread(get_rclone_file_list, gid)
        if file_list is None:
            return JSONResponse({
                "files": [], "engine": "", "error": "Not found",
                "message": "Task not found or expired",
            })
        content = make_rclone_tree(file_list)
        return JSONResponse(content)


async def handle_rename(gid, data):
    try:
        _type = data["type"]
        del data["type"]
        if _type == "file":
            await qbittorrent.torrents.rename_file(hash=gid, **data)
        else:
            await qbittorrent.torrents.rename_folder(hash=gid, **data)
    except (ClientError, TimeoutError, Exception, AQError) as e:
        LOGGER.error(f"{e} Errored in renaming")


async def set_qbittorrent(gid, selected_files, unselected_files):
    if unselected_files:
        try:
            await qbittorrent.torrents.file_prio(
                hash=gid, id=unselected_files, priority=0
            )
        except (ClientError, TimeoutError, Exception, AQError) as e:
            LOGGER.error(f"{e} Errored in paused")
    if selected_files:
        try:
            await qbittorrent.torrents.file_prio(
                hash=gid, id=selected_files, priority=1
            )
        except (ClientError, TimeoutError, Exception, AQError) as e:
            LOGGER.error(f"{e} Errored in resumed")
    await sleep(0.5)
    if not await re_verify(unselected_files, selected_files, gid):
        LOGGER.error(f"Verification Failed! Hash: {gid}")


async def set_aria2(gid, selected_files):
    res = await aria2.changeOption(gid, {"select-file": selected_files})
    if res == "OK":
        LOGGER.info(f"Verified! Gid: {gid}")
    else:
        LOGGER.info(f"Verification Failed! Report! Gid: {gid}")


@app.get("/", response_class=HTMLResponse)
async def homepage(request: Request):
    return templates.TemplateResponse(request, "landing.html")


def rewrite_location(location: str, proxy_prefix: str) -> str:
    parsed = urlparse(location)
    if not parsed.netloc:
        return proxy_prefix + location
    if parsed.hostname in ["localhost", "127.0.0.1"]:
        return proxy_prefix + parsed.path
    return location


async def proxy_fetch(
    method: str, url: str, headers: dict, params: dict, body: bytes, proxy_prefix: str
):
    session = proxy_session or ClientSession(auto_decompress=True)
    async with session.request(
        method,
        url,
        headers=headers,
        params=params,
        data=body,
        allow_redirects=False,
    ) as upstream:
        if upstream.status in (301, 302, 303, 307, 308) and upstream.headers.get(
            "Location"
        ):
            loc = upstream.headers["Location"]
            new_loc = rewrite_location(loc, proxy_prefix)
            return HTMLResponse(
                status_code=upstream.status, headers={"Location": new_loc}
            )
        content = await upstream.read()
        media_type = upstream.headers.get("Content-Type", "text/html")
        resp_headers = {
            k: v
            for k, v in upstream.headers.items()
            if k.lower() not in ["content-length", "content-encoding"]
        }
        return HTMLResponse(
            content=content,
            status_code=upstream.status,
            headers=resp_headers,
            media_type=media_type,
        )


async def protected_proxy(
    service: str, path: str, request: Request, password: str = None
):
    service_info = SERVICES.get(service)
    if not service_info:
        raise HTTPException(status_code=404, detail="Service not found")
    if "password" in service_info and password != service_info["password"]:
        raise HTTPException(status_code=403, detail="Unauthorized access")
    base = service_info["url"]
    url = f"{base}/{path}" if path else base
    headers = {k: v for k, v in request.headers.items() if k.lower() != "host"}
    body = await request.body()
    return await proxy_fetch(
        request.method, url, headers, dict(request.query_params), body, f"/{service}"
    )


@app.api_route("/qbit/{path:path}", methods=["GET", "POST", "PUT", "DELETE", "PATCH"])
async def qbittorrent_proxy(path: str = "", request: Request = None):
    password = request.query_params.get("pass") or request.cookies.get("qbit_pass")
    if not password:
        raise HTTPException(status_code=403, detail="Missing password")
    response = await protected_proxy("qbit", path, request, password)
    if "pass" in request.query_params:
        response.set_cookie("qbit_pass", password)
    return response


@app.exception_handler(Exception)
async def page_not_found(_, exc):
    LOGGER.error("Unhandled web exception: %s: %s", type(exc).__name__, exc, exc_info=True)
    return HTMLResponse(
        "<h1>404: Task not found! Mostly wrong input.</h1>",
        status_code=404,
    )
