# This file is a part of NEO-WZML (github.com/irisXDR/NEO-WZML)

from asyncio import (
    CancelledError,
    create_subprocess_exec,
    create_subprocess_shell,
    run_coroutine_threadsafe,
    sleep,
)
from asyncio.subprocess import PIPE
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from functools import partial, wraps
from os import cpu_count
from urllib.parse import urlsplit

from httpx import AsyncClient

from bot import LOGGER, bot_loop, user_data
from bot.core.config_manager import Config
from bot.helper.themes import BotTheme
from bot.helper.telegram_helper.button_build import ButtonMaker
from bot.helper.ext_utils.help_messages import (
    CLONE_HELP_DICT,
    MIRROR_HELP_DICT,
    YT_HELP_DICT,
)
from bot.helper.ext_utils.telegraph_helper import telegraph

COMMAND_USAGE = {}

_CPU = cpu_count() or 4
THREAD_POOL = ThreadPoolExecutor(max_workers=min(64, _CPU * 8))


class SetInterval:
    def __init__(self, interval, action, *args, **kwargs):
        self.interval = interval
        self.action = action
        self.task = bot_loop.create_task(self._set_interval(*args, **kwargs))

    async def _set_interval(self, *args, **kwargs):
        while True:
            await sleep(self.interval)
            try:
                await self.action(*args, **kwargs)
            except CancelledError:
                raise
            except Exception as e:
                # Log interval task errors so they don't die silently
                LOGGER.error(
                    "SetInterval action %s raised: %s",
                    getattr(self.action, "__name__", repr(self.action)),
                    e,
                    exc_info=True,
                )

    def cancel(self):
        self.task.cancel()


def _build_command_usage(help_dict, command_key):
    buttons = ButtonMaker()
    cmd_list = list(help_dict.keys())[1:]
    cmd_pages = [cmd_list[i : i + 10] for i in range(0, len(cmd_list), 10)]
    temp_store = []

    for i, page in enumerate(cmd_pages):
        for name in page:
            buttons.data_button(name, f"help {command_key} {name} {i}")
        if len(cmd_pages) > 1:
            if i > 0:
                buttons.data_button(BotTheme("PREVIOUS"), f"help pre {command_key} {i - 1}")
            if i < len(cmd_pages) - 1:
                buttons.data_button(BotTheme("NEXT"), f"help nex {command_key} {i + 1}")
        buttons.data_button("Close", "help close", "footer")
        temp_store.append(buttons.build_menu(2))
        buttons.reset()

    COMMAND_USAGE[command_key] = [help_dict["main"], *temp_store]


def create_help_buttons():
    _build_command_usage(MIRROR_HELP_DICT, "mirror")
    _build_command_usage(YT_HELP_DICT, "yt")
    _build_command_usage(CLONE_HELP_DICT, "clone")


def _parse_version_tuple(v):
    if not v:
        return None
    head = str(v).split("-", 1)[0].split("+", 1)[0].strip()
    if head.startswith(("v", "V")):
        head = head[1:]
    parts = head.split(".")
    out = []
    for p in parts:
        digits = ""
        for c in p:
            if c.isdigit():
                digits += c
            else:
                break
        if digits == "":
            return None
        out.append(int(digits))
    return tuple(out) if out else None


def compare_versions(v1, v2):
    if not v1 or not v2:
        return "Version check unavailable"

    parsed1 = _parse_version_tuple(v1)
    parsed2 = _parse_version_tuple(v2)
    if parsed1 is None or parsed2 is None:
        return "Version check unavailable"

    return (
        "New Version Update is Available! Check Now!"
        if parsed1 < parsed2
        else (
            "More Updated! Kindly Contribute in Official"
            if parsed1 > parsed2
            else "Already up to date with latest version"
        )
    )


def _derive_web_pin(token):
    digits = "".join(n for n in str(token) if n.isdigit())
    if len(digits) >= 4:
        return digits[:4]
    from hashlib import blake2b

    h = blake2b(str(token).encode("utf-8"), digest_size=4).hexdigest()
    return "".join(c for c in h if c.isdigit())[:4].zfill(4)


def get_valid_base_url():
    base_url = (Config.BASE_URL or "").strip().rstrip("/")
    if not base_url:
        return ""
    parsed = urlsplit(base_url)
    if parsed.scheme not in ("http", "https") or not parsed.netloc:
        LOGGER.warning("Invalid BASE_URL for web selection: %r", Config.BASE_URL)
        return ""
    return base_url


def bt_selection_buttons(id_):
    gid = id_[:12] if len(id_) > 25 else id_
    pin = _derive_web_pin(id_)
    base_url = get_valid_base_url()
    buttons = ButtonMaker()
    if Config.WEB_PINCODE:
        buttons.url_button("Select Files", f"{base_url}/app/files?gid={id_}")
        buttons.data_button("Pincode", f"sel pin {gid} {pin}")
    else:
        buttons.url_button(
            "Select Files", f"{base_url}/app/files?gid={id_}&pin={pin}"
        )
    buttons.data_button("Done Selecting", f"sel done {gid} {id_}")
    buttons.data_button("Cancel", f"sel cancel {gid}")
    return buttons.build_menu(2)


def mega_selection_buttons(gid):
    pin = _derive_web_pin(gid)
    base_url = get_valid_base_url()
    buttons = ButtonMaker()
    if Config.WEB_PINCODE:
        buttons.url_button(
            "Select Files", f"{base_url}/app/files?gid={gid}&type=mega"
        )
        buttons.data_button("Pincode", f"sel pin {gid} {pin}")
    else:
        buttons.url_button(
            "Select Files",
            f"{base_url}/app/files?gid={gid}&pin={pin}&type=mega",
        )
    buttons.data_button("Done Selecting", f"sel done {gid} {gid}")
    buttons.data_button("Cancel", f"sel cancel {gid}")
    return buttons.build_menu(2)


def terabox_selection_buttons(gid):
    pin = _derive_web_pin(gid)
    base_url = get_valid_base_url()
    buttons = ButtonMaker()
    if Config.WEB_PINCODE:
        buttons.url_button(
            "Select Files", f"{base_url}/app/files?gid={gid}&type=terabox"
        )
        buttons.data_button("Pincode", f"sel pin {gid} {pin}")
    else:
        buttons.url_button(
            "Select Files",
            f"{base_url}/app/files?gid={gid}&pin={pin}&type=terabox",
        )
    buttons.data_button("Done Selecting", f"sel done {gid} {gid}")
    buttons.data_button("Cancel", f"sel cancel {gid}")
    return buttons.build_menu(2)


def rclone_selection_buttons(gid):
    pin = _derive_web_pin(gid)
    base_url = get_valid_base_url()
    buttons = ButtonMaker()
    if Config.WEB_PINCODE:
        buttons.url_button(
            "Select Files", f"{base_url}/app/files?gid={gid}&type=rclone"
        )
        buttons.data_button("Pincode", f"sel pin {gid} {pin}")
    else:
        buttons.url_button(
            "Select Files",
            f"{base_url}/app/files?gid={gid}&pin={pin}&type=rclone",
        )
    buttons.data_button("Done Selecting", f"sel done {gid} {gid}")
    buttons.data_button("Cancel", f"sel cancel {gid}")
    return buttons.build_menu(2)


async def get_telegraph_list(telegraph_content):
    path = [
        (
            await telegraph.create_page(
                title="Mirror-Leech-Bot Drive Search", content=content
            )
        )["path"]
        for content in telegraph_content
    ]
    if len(path) > 1:
        await telegraph.edit_telegraph(path, telegraph_content)
    buttons = ButtonMaker()
    buttons.url_button("🔎 VIEW", f"https://telegra.ph/{path[0]}")
    return buttons.build_menu(1)


def arg_parser(items, arg_base):
    if not items:
        return

    arg_start = -1
    i = 0
    total = len(items)

    bool_arg_set = {
        "-b",
        "-e",
        "-z",
        "-zim",
        "-zipimage",
        "-zipimages",
        "-s",
        "-j",
        "-d",
        "-sv",
        "-ss",
        "-f",
        "-fd",
        "-fu",
        "-sync",
        "-hl",
        "-doc",
        "-med",
        "-ut",
        "-bt",
        "-yt",
        "-mv",
    }
    if Config.DISABLE_BULK and "-b" in items:
        arg_base["-b"] = False

    if Config.DISABLE_MULTI and "-i" in items:
        arg_base["-i"] = 0

    if Config.DISABLE_SEED and "-d" in items:
        arg_base["-d"] = False

    while i < total:
        part = items[i]

        if part in arg_base:
            if arg_start == -1:
                arg_start = i

            if (
                i + 1 == total
                and part in bool_arg_set
                or part
                in [
                    "-s",
                    "-j",
                    "-f",
                    "-fd",
                    "-fu",
                    "-sync",
                    "-hl",
                    "-doc",
                    "-med",
                    "-ut",
                    "-bt",
                    "-yt",
                    "-mv",
                ]
            ):
                arg_base[part] = True
            else:
                sub_list = []
                for j in range(i + 1, total):
                    if items[j] in arg_base:
                        if part == "-c" and items[j] == "-c":
                            sub_list.append(items[j])
                            continue
                        if part in bool_arg_set and not sub_list:
                            arg_base[part] = True
                            break
                        if not sub_list:
                            break
                        check = " ".join(sub_list).strip()
                        if check.startswith("[") and check.endswith("]"):
                            break
                        elif not check.startswith("["):
                            break
                    sub_list.append(items[j])
                if sub_list:
                    value = " ".join(sub_list)
                    if part == "-ff" and not value.strip().startswith("["):
                        arg_base[part].add(value)
                    else:
                        arg_base[part] = value
                    i += len(sub_list)

        i += 1

    if "link" in arg_base:
        link_items = items[:arg_start] if arg_start != -1 else items
        if link_items:
            arg_base["link"] = " ".join(link_items)


def get_size_bytes(size):
    size = size.lower()
    if "k" in size:
        size = int(float(size.split("k")[0]) * 1024)
    elif "m" in size:
        size = int(float(size.split("m")[0]) * 1048576)
    elif "g" in size:
        size = int(float(size.split("g")[0]) * 1073741824)
    elif "t" in size:
        size = int(float(size.split("t")[0]) * 1099511627776)
    else:
        size = 0
    return size


async def get_content_type(url):
    try:
        async with AsyncClient(follow_redirects=True, verify=False) as client:
            response = await client.head(url)
            content_type = response.headers.get("Content-Type")
            if content_type:
                return content_type
            async with client.stream("GET", url) as response:
                return response.headers.get("Content-Type")
    except Exception:
        return None


def update_user_ldata(id_, key, value):
    user_data.setdefault(id_, {})
    user_data[id_][key] = value


async def fetch_user_tds(user_id):
    user_dict = user_data.get(user_id, {})
    if Config.USER_TD_MODE and user_dict.get("TD_MODE", False):
        return user_dict.get("USER_TDS", {})
    return {}


async def fetch_user_dumps(user_id):
    user_dict = user_data.get(user_id, {})
    dumps = user_dict.get("LDUMP", {})
    if dumps and not isinstance(dumps, dict):
        update_user_ldata(user_id, "LDUMP", {})
        return {}
    return dumps if dumps else {}


async def cmd_exec(cmd, shell=False, timeout=None):
    """Run a subprocess and return `(stdout, stderr, returncode)`.

    `timeout` is a wall-clock deadline in seconds. When set, a runaway
    child (network hang, deadlocked tool, etc.) is terminated and stderr
    is annotated so the caller can surface a clear "took too long" reason
    instead of the worker silently hanging forever. Defaults to no timeout
    to preserve historical behaviour for callers that haven't opted in.
    """
    if shell:
        proc = await create_subprocess_shell(cmd, stdout=PIPE, stderr=PIPE)
    else:
        proc = await create_subprocess_exec(*cmd, stdout=PIPE, stderr=PIPE)
    timed_out = False
    try:
        if timeout is None:
            stdout, stderr = await proc.communicate()
        else:
            from asyncio import wait_for as _wf, TimeoutError as _AT
            try:
                stdout, stderr = await _wf(proc.communicate(), timeout=timeout)
            except _AT:
                timed_out = True
                try:
                    proc.terminate()
                except ProcessLookupError:
                    pass
                try:
                    await wait_for_proc_exit(proc, 5)
                except Exception:
                    with _suppress_proc_error():
                        proc.kill()
                stdout, stderr = b"", b""
    except CancelledError:
        # Terminate subprocess on cancellation to avoid zombies
        try:
            proc.terminate()
        except ProcessLookupError:
            pass
        try:
            await wait_for_proc_exit(proc, 5)
        except Exception:
            with _suppress_proc_error():
                proc.kill()
        raise
    try:
        stdout = stdout.decode(errors="replace").strip()
    except Exception:
        stdout = "Unable to decode the response!"
    try:
        stderr = stderr.decode(errors="replace").strip()
    except Exception:
        stderr = "Unable to decode the error!"
    if timed_out:
        stderr = (
            (stderr + "\n" if stderr else "")
            + f"cmd_exec: timed out after {timeout}s and was terminated."
        )
        return stdout, stderr, proc.returncode if proc.returncode is not None else -1
    return stdout, stderr, proc.returncode


async def wait_for_proc_exit(proc, timeout):
    from asyncio import wait_for as _wf

    return await _wf(proc.wait(), timeout=timeout)


from contextlib import contextmanager as _ctxmgr


@_ctxmgr
def _suppress_proc_error():
    try:
        yield
    except ProcessLookupError:
        pass


def new_task(func):
    @wraps(func)
    async def wrapper(*args, **kwargs):
        task = bot_loop.create_task(func(*args, **kwargs))
        return task

    return wrapper


async def sync_to_async(func, *args, wait=True, **kwargs):
    pfunc = partial(func, *args, **kwargs)
    future = bot_loop.run_in_executor(THREAD_POOL, pfunc)
    return await future if wait else future


def async_to_sync(func, *args, wait=True, **kwargs):
    future = run_coroutine_threadsafe(func(*args, **kwargs), bot_loop)
    return future.result() if wait else future


def loop_thread(func):
    @wraps(func)
    def wrapper(*args, wait=False, **kwargs):
        future = run_coroutine_threadsafe(func(*args, **kwargs), bot_loop)
        return future.result() if wait else future

    return wrapper


def safe_int(value, default=0):
    try:
        return int(value)
    except (ValueError, TypeError):
        return default


async def getdailytasks(
    user_id,
    increase_task=False,
    upleech=0,
    upmirror=0,
    check_mirror=False,
    check_leech=False,
):
    task, lsize, msize = 0, 0, 0
    if user_id in user_data and user_data[user_id].get("dly_tasks"):
        userdate, task, lsize, msize = user_data[user_id]["dly_tasks"]
        nowdate = datetime.now()
        if userdate.date() < nowdate.date():
            task, lsize, msize = 0, 0, 0
            if increase_task:
                task = 1
            elif upleech != 0:
                lsize += upleech
            elif upmirror != 0:
                msize += upmirror
        elif increase_task:
            task += 1
        elif upleech != 0:
            lsize += upleech
        elif upmirror != 0:
            msize += upmirror
    elif increase_task:
        task += 1
    elif upleech != 0:
        lsize += upleech
    elif upmirror != 0:
        msize += upmirror

    update_user_ldata(user_id, "dly_tasks", [datetime.now(), task, lsize, msize])

    if check_leech:
        return lsize
    elif check_mirror:
        return msize
    return task


CAPTION_STYLES = {
    "bold": "<b>{caption}</b>",
    "italics": "<i>{caption}</i>",
    "mono": "<code>{caption}</code>",
    "underline": "<u>{caption}</u>",
    "strike": "<s>{caption}</s>",
    "spoiler": "<tg-spoiler>{caption}</tg-spoiler>",
}

CAPTION_STYLE_NAMES = {
    "bold": "Bold",
    "italics": "Italics",
    "mono": "Monospace",
    "underline": "Underline",
    "strike": "Strikethrough",
    "spoiler": "Spoiler",
}


def has_html_tags(text):
    import re
    return bool(re.search(r'<[^>]+>', text))


def apply_caption_style(caption, style):
    if style and style in CAPTION_STYLES:
        return CAPTION_STYLES[style].format(caption=caption)
    return caption


def get_mega_link_type(url):
    return "folder" if "folder" in url or "/#F!" in url else "file"
