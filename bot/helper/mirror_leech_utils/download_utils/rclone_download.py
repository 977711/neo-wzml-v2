# This file is a part of NEO-WZML (github.com/irisXDR/NEO-WZML)

from asyncio import gather, sleep
from json import loads
from secrets import token_hex
from time import time
from aiofiles import open as aiopen
from aiofiles.os import remove

from bot import task_dict, task_dict_lock, LOGGER, bot_loop
from bot.core.config_manager import BinConfig
from bot.helper.ext_utils.bot_utils import (
    cmd_exec,
    get_valid_base_url,
    rclone_selection_buttons,
)
from bot.helper.ext_utils.task_manager import (
    check_running_tasks,
    stop_duplicate_check,
    limit_checker,
)
from bot.helper.mirror_leech_utils.rclone_utils.transfer import RcloneTransferHelper
from bot.helper.mirror_leech_utils.status_utils.queue_status import QueueStatus
from bot.helper.mirror_leech_utils.status_utils.rclone_status import RcloneStatus
from bot.helper.telegram_helper.message_utils import (
    send_status_message,
    send_message,
)
from web.rclone_selection_store import (
    write_state as _rcl_store_write,
    read_state as _rcl_store_read,
    delete_state as _rcl_store_delete,
)

_RCLONE_SELECT_TTL = 30 * 60
_rclone_selections = {}
_rcl_sweeper_task = None


async def add_rclone_download(listener, path):
    if listener.link.startswith("mrcc:"):
        listener.link = listener.link.split("mrcc:", 1)[1]
        config_path = f"rclone/{listener.user_id}.conf"
    else:
        config_path = "rclone.conf"

    if ":" not in listener.link:
        await listener.on_download_error(
            "Invalid Rclone link — expected format `<remote>:<path>`."
        )
        return
    remote, listener.link = listener.link.split(":", 1)
    listener.link = listener.link.strip("/")
    rclone_select = False
    if listener.link.startswith("rclone_select"):
        rclone_select = True
        rpath = ""
    else:
        rpath = listener.link

    cmd1 = [
        BinConfig.RCLONE_NAME,
        "lsjson",
        "--fast-list",
        "--stat",
        "--no-mimetype",
        "--no-modtime",
        "--config",
        config_path,
        f"{remote}:{rpath}",
    ]
    cmd2 = [
        BinConfig.RCLONE_NAME,
        "size",
        "--fast-list",
        "--json",
        "--config",
        config_path,
        f"{remote}:{rpath}",
    ]
    if rclone_select:
        cmd2.extend(("--files-from", listener.link))
        res = await cmd_exec(cmd2)
        if res[2] != 0:
            if res[2] != -9:
                msg = f"Error: While getting rclone stat/size. Path: {remote}:{listener.link}. Stderr: {res[1][:4000]}"
                await listener.on_download_error(msg)
            return
        try:
            rsize = loads(res[0])
        except Exception as err:
            await listener.on_download_error(f"RcloneDownload JsonLoad: {err}")
            return
        if not listener.name:
            listener.name = listener.link
        path += listener.name
    else:
        res1, res2 = await gather(cmd_exec(cmd1), cmd_exec(cmd2))
        if res1[2] != 0 or res2[2] != 0:
            if res1[2] != -9:
                err = res1[1] or res2[1]
                msg = f"Error: While getting rclone stat/size. Path: {remote}:{listener.link}. Stderr: {err[:4000]}"
                await listener.on_download_error(msg)
            return
        try:
            rstat = loads(res1[0])
            rsize = loads(res2[0])
        except Exception as err:
            await listener.on_download_error(f"RcloneDownload JsonLoad: {err}")
            return
        if rstat["IsDir"]:
            if not listener.name:
                listener.name = (
                    listener.link.rsplit("/", 1)[-1] if listener.link else remote
                )
            path += listener.name
        else:
            listener.name = listener.name or listener.link.rsplit("/", 1)[-1]
    listener.size = rsize["bytes"]
    gid = token_hex(5)

    if not rclone_select:
        msg, button = await stop_duplicate_check(listener)
        if msg:
            await listener.on_download_error(msg, button)
            return
        if limit_exceeded := await limit_checker(listener):
            await listener.on_download_error(limit_exceeded, is_limit=True)
            return

    add_to_queue, event = await check_running_tasks(listener)
    if add_to_queue:
        LOGGER.info(f"Added to Queue/Download: {listener.name}")
        async with task_dict_lock:
            task_dict[listener.mid] = QueueStatus(listener, gid, "dl")
        await listener.on_download_start()
        if listener.multi <= 1:
            await send_status_message(listener.message)
        await event.wait()
        if listener.is_cancelled:
            return

    RCTransfer = RcloneTransferHelper(listener)
    async with task_dict_lock:
        task_dict[listener.mid] = RcloneStatus(listener, RCTransfer, gid, "dl")

    if add_to_queue:
        LOGGER.info(f"Start Queued Download with rclone: {listener.link}")
    else:
        await listener.on_download_start()
        if listener.multi <= 1:
            await send_status_message(listener.message)
        LOGGER.info(f"Download with rclone: {listener.link}")

    await RCTransfer.download(remote, config_path, path)
    if rclone_select:
        await remove(listener.link)


async def _sweep_rclone_selections():
    while True:
        try:
            await sleep(60)
            now = time()
            stale = [
                gid for gid, st in list(_rclone_selections.items())
                if now - st.get("created_at", 0) >= _RCLONE_SELECT_TTL
            ]
            for gid in stale:
                state = _rclone_selections.pop(gid, None)
                _rcl_store_delete(gid)
                if not state:
                    continue
                listener = state.get("listener")
                if listener is not None:
                    try:
                        await listener.on_download_error(
                            "Rclone file selection expired (no choice in "
                            f"{_RCLONE_SELECT_TTL // 60} minutes). Re-run the command."
                        )
                    except Exception as e:
                        LOGGER.error(f"rclone sweeper notify failed for {gid}: {e}")
        except Exception as e:
            LOGGER.error(f"rclone selection sweeper iteration failed: {e}")


def _ensure_rcl_sweeper():
    global _rcl_sweeper_task
    if _rcl_sweeper_task is not None and not _rcl_sweeper_task.done():
        return
    try:
        _rcl_sweeper_task = bot_loop.create_task(_sweep_rclone_selections())
    except Exception as e:
        LOGGER.error(f"rclone: could not start selection sweeper: {e}")


def _parse_rclone_link(link, user_id):
    """Return (remote:path, config_path, is_user_config) for a (m)rcc link."""
    if link.startswith("mrcc:"):
        return link.split("mrcc:", 1)[1], f"rclone/{user_id}.conf", True
    return link, "rclone.conf", False


async def add_rclone_web_selection(listener, path):
    """Recursively list the chosen Rclone folder and hand off to the web file
    selector. A single file (or no public BASE_URL) downloads directly."""
    if not get_valid_base_url():
        await add_rclone_download(listener, path)
        return
    raw, config_path, user_cfg = _parse_rclone_link(listener.link, listener.user_id)
    if ":" not in raw or "rclone_select" in raw:
        await add_rclone_download(listener, path)
        return
    remote, rpath = raw.split(":", 1)
    rpath = rpath.strip("/")
    target = f"{remote}:{rpath}"

    stat_cmd = [
        BinConfig.RCLONE_NAME, "lsjson", "--stat", "--no-mimetype",
        "--no-modtime", "--config", config_path, target,
    ]
    res = await cmd_exec(stat_cmd)
    if res[2] != 0:
        if res[2] != -9:
            await listener.on_download_error(
                f"Rclone stat failed. Path: {target}. Stderr: {res[1][:3000]}"
            )
        return
    try:
        stat = loads(res[0])
    except Exception:
        stat = {}
    if not stat.get("IsDir", False):
        await add_rclone_download(listener, path)
        return

    ls_cmd = [
        BinConfig.RCLONE_NAME, "lsjson", "-R", "--files-only", "--fast-list",
        "--no-mimetype", "--no-modtime", "--config", config_path, target,
    ]
    res = await cmd_exec(ls_cmd)
    if res[2] != 0:
        if res[2] != -9:
            await listener.on_download_error(
                f"Rclone listing failed. Path: {target}. Stderr: {res[1][:3000]}"
            )
        return
    try:
        entries = loads(res[0])
    except Exception as err:
        await listener.on_download_error(f"RcloneSelect JsonLoad: {err}")
        return

    file_list = []
    for it in entries:
        if it.get("IsDir"):
            continue
        rel = (it.get("Path") or "").strip("/")
        if not rel:
            continue
        full = f"{rpath}/{rel}" if rpath else rel
        file_list.append({
            "name": it.get("Name") or rel.rsplit("/", 1)[-1],
            "path": full,
            "size": it.get("Size", 0) or 0,
            "is_dir": False,
            "id": full,
        })
    if not file_list:
        await listener.on_download_error("No files found in this Rclone folder.")
        return

    _ensure_rcl_sweeper()
    gid = token_hex(5)
    _rclone_selections[gid] = {
        "listener": listener,
        "config_path": config_path,
        "user_cfg": user_cfg,
        "remote": remote,
        "folder": rpath,
        "download_path": path,
        "created_at": time(),
    }
    if not _rcl_store_write(gid, file_list, []):
        _rclone_selections.pop(gid, None)
        await listener.on_download_error(
            "Failed to persist selection state — disk full?"
        )
        return

    listener.size = sum(f["size"] for f in file_list)
    buttons = rclone_selection_buttons(f"rclone_{gid}")
    await send_message(
        listener.message,
        "Your Rclone folder is ready. Open the selector, choose files, then "
        "press Done Selecting to start downloading.",
        buttons,
    )
    LOGGER.info(
        "Rclone web selection: %d file(s) from %s", len(file_list), target
    )

def get_rclone_selection_owner_id(gid):
    state = _rclone_selections.get(gid)
    if not state:
        return None
    listener = state.get("listener")
    return getattr(listener, "user_id", None)


async def resume_rclone_with_selection(gid):
    state = _rclone_selections.pop(gid, None)
    if not state:
        _rcl_store_delete(gid)
        return
    listener = state["listener"]
    remote = state["remote"]
    folder = state["folder"]
    path = state["download_path"]
    user_cfg = state["user_cfg"]
    try:
        store_data = _rcl_store_read(gid)
        selected = list(store_data.get("selected_ids", []) if store_data else [])
        _rcl_store_delete(gid)
        if not selected:
            await listener.on_download_error("No files selected")
            return
        sel_file = f"rclone_select_{gid}.txt"
        async with aiopen(sel_file, "w") as f:
            await f.write("\n".join(selected) + "\n")
        prefix = "mrcc:" if user_cfg else ""
        listener.link = f"{prefix}{remote}:{sel_file}"
        if not listener.name:
            listener.name = folder.rsplit("/", 1)[-1] if folder else remote
        LOGGER.info(f"Rclone download (selected {len(selected)} files): {listener.name}")
        await add_rclone_download(listener, path)
    except Exception as e:
        LOGGER.error(f"resume_rclone_with_selection: {e}", exc_info=True)
        if not listener.is_cancelled:
            await listener.on_download_error(f"Internal error: {e}")


async def cancel_rclone_selection(gid):
    state = _rclone_selections.pop(gid, None)
    _rcl_store_delete(gid)
    if not state:
        return
    listener = state.get("listener")
    if listener is not None:
        await listener.on_download_error("Cancelled by user")
