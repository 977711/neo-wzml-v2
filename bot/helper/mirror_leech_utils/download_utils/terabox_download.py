# This file is a part of NEO-WZML (github.com/irisXDR/NEO-WZML)

import os
import posixpath
import re
from asyncio import Lock as AsyncLock, gather, sleep
from secrets import token_hex
from time import time

from aiofiles.os import makedirs, remove, path as aiopath

try:
    from terabox import (
        TeraboxClient,
        TeraboxError,
        TeraboxPasswordError,
        TeraboxCancelled,
        TeraboxFile,
    )
    from terabox.constants import APP_ID, CHANNEL, CLIENT_TYPE, EP_QUOTA, WEB
    _TERABOX_AVAILABLE = True
except ImportError:  # pragma: no cover - depends on base image build
    _TERABOX_AVAILABLE = False

from bot import task_dict, task_dict_lock, bot_loop
from bot.core.config_manager import Config
from bot.helper.ext_utils.bot_utils import (
    terabox_selection_buttons,
    get_valid_base_url,
)
from bot.helper.ext_utils.task_manager import (
    check_running_tasks,
    stop_duplicate_check,
    limit_checker,
)
from bot.helper.ext_utils.files_utils import check_storage_threshold
from bot.helper.ext_utils.status_utils import get_readable_file_size
from bot.helper.mirror_leech_utils.status_utils.terabox_status import (
    TeraboxDownloadStatus,
)
from bot.helper.mirror_leech_utils.status_utils.queue_status import QueueStatus
from bot.helper.telegram_helper.message_utils import (
    send_status_message,
    send_message,
)
from bot.helper.listeners.terabox_listener import TeraboxDownloadTracker
from web.terabox_selection_store import (
    write_state as _store_write,
    read_state as _store_read,
    delete_state as _store_delete,
)

_COOKIE_FILE = "terabox.txt"
_SELECTION_TTL_SECONDS = 30 * 60
_COOKIE_PROBE_TTL = 10 * 60

_terabox_selections = {}
_active_terabox_links = set()
_active_lock = AsyncLock()
_sweeper_task = None
_cookie_probe_cache = {}

_UNSAFE_NAME_CHARS = re.compile(r'[/\\\x00:*?"<>|]')


def _normalize_link(link: str) -> str:
    return (link or "").strip().rstrip("/")


async def _reserve_terabox_link(link: str):
    key = _normalize_link(link)
    async with _active_lock:
        if key in _active_terabox_links:
            return None
        _active_terabox_links.add(key)
    return key


async def _discard_terabox_link(link_key: str) -> None:
    if not link_key:
        return
    async with _active_lock:
        _active_terabox_links.discard(link_key)


def _sanitize_name(name) -> str:
    if not name:
        return "unnamed"
    cleaned = _UNSAFE_NAME_CHARS.sub("_", str(name)).strip()
    cleaned = cleaned.rstrip(". ").lstrip()
    return cleaned or "unnamed"


def _build_client(cookie_file):
    return TeraboxClient(
        cookie_file=os.path.abspath(cookie_file),
    )


def _user_cookie_file(listener) -> str:
    return f"terabox_cookies/{listener.user_id}.txt"


def _looks_premium(value) -> bool:
    premium_keys = (
        "vip", "svip", "premium", "is_vip", "is_svip", "is_premium",
        "isVip", "isSvip", "isPremium", "vip_type", "vipType",
        "member_type", "memberType", "is_member", "isMember",
        "member_status", "memberStatus",
    )
    if isinstance(value, dict):
        for key, item in value.items():
            if key in premium_keys:
                if isinstance(item, bool) and item:
                    return True
                if isinstance(item, (int, float)) and item > 0:
                    return True
                if isinstance(item, str) and item.strip().lower() not in (
                    "", "0", "false", "free", "normal", "none", "null",
                ):
                    return True
            if _looks_premium(item):
                return True
    elif isinstance(value, list):
        return any(_looks_premium(x) for x in value)
    return False


async def _probe_cookie(cookie_file: str, label: str) -> dict:
    """Return cookie health/premium info, cached by file mtime."""
    try:
        mtime = os.path.getmtime(cookie_file)
    except OSError:
        return {"path": cookie_file, "label": label, "usable": False, "premium": False}
    cache_key = (os.path.abspath(cookie_file), mtime)
    cached = _cookie_probe_cache.get(cache_key)
    now = time()
    if cached and now - cached.get("at", 0) < _COOKIE_PROBE_TTL:
        return dict(cached["data"])

    client = TeraboxClient(cookie_file=os.path.abspath(cookie_file))
    data = {"path": cookie_file, "label": label, "usable": False, "premium": False}
    try:
        await client.login()
        await client.ensure_upload_ready()
        base = f"https://{client._upload_region}"
        quota = await client.session.get_json(
            base + EP_QUOTA,
            params={
                "app_id": APP_ID,
                "web": WEB,
                "channel": CHANNEL,
                "clienttype": CLIENT_TYPE,
                "checkfree": "1",
            },
            referer=base + "/",
        )
        data.update({
            "usable": True,
            "premium": _looks_premium(quota),
            "region": client._upload_region,
        })
    except Exception as e:
        data["error"] = str(e)
    finally:
        await client.aclose()

    _cookie_probe_cache[cache_key] = {"at": now, "data": dict(data)}
    return data


async def _select_download_cookie(listener) -> str:
    candidates = []
    user_cookie = _user_cookie_file(listener)
    if await aiopath.exists(user_cookie):
        candidates.append((user_cookie, "User Cookie"))
    if await aiopath.exists(_COOKIE_FILE):
        candidates.append((_COOKIE_FILE, "Owner Cookie"))
    if not candidates:
        return ""
    if len(candidates) == 1:
        cookie, label = candidates[0]
        listener.terabox_cookie = cookie
        listener.terabox_cookie_source = label
        return cookie

    probes = await gather(*(_probe_cookie(path, label) for path, label in candidates))
    user = next((p for p in probes if p["label"] == "User Cookie"), None)
    owner = next((p for p in probes if p["label"] == "Owner Cookie"), None)
    order = []
    if user and user.get("usable") and user.get("premium"):
        order.append(user)
    if owner and owner.get("usable") and owner.get("premium"):
        order.append(owner)
    if user and user.get("usable"):
        order.append(user)
    if owner and owner.get("usable"):
        order.append(owner)
    if not order:
        order = [user or owner]
    chosen = order[0]
    listener.terabox_cookie = chosen["path"]
    listener.terabox_cookie_source = chosen["label"]
    return chosen["path"]


def _build_file_list_meta(entries):
    return [
        {
            "name": f.name,
            "path": f.path,
            "size": f.size,
            "is_dir": f.is_dir,
            "id": str(f.fs_id),
        }
        for f in entries
    ]


def _dest_for(base_path, dest_root, orig_top, file, is_single):
    if is_single:
        return os.path.join(base_path, _sanitize_name(dest_root))
    parts = [p for p in file.path.lstrip("/").split("/") if p]
    if orig_top and parts and parts[0] == orig_top and len(parts) > 1:
        parts = parts[1:]
    safe_parts = [_sanitize_name(p) for p in parts] or [_sanitize_name(file.name)]
    return os.path.join(base_path, _sanitize_name(dest_root), *safe_parts)


async def _sweep_stale_selections():
    while True:
        try:
            await sleep(60)
            now = time()
            stale = [
                gid for gid, st in list(_terabox_selections.items())
                if now - st.get("created_at", 0) >= _SELECTION_TTL_SECONDS
            ]
            for gid in stale:
                state = _terabox_selections.pop(gid, None)
                if not state:
                    continue
                _store_delete(gid)
                await _discard_terabox_link(state.get("link_key", ""))
                client = state.get("client")
                if client is not None:
                    try:
                        await client.aclose()
                    except Exception:
                        pass
                listener = state.get("listener")
                if listener is not None:
                    try:
                        await listener.on_download_error(
                            "Terabox file selection expired (no choice in "
                            f"{_SELECTION_TTL_SECONDS // 60} minutes). "
                            "Re-send the link if you still want it."
                        )
                    except Exception:
                        pass
        except Exception:
            pass


def _ensure_sweeper_running():
    global _sweeper_task
    if _sweeper_task is not None and not _sweeper_task.done():
        return
    try:
        _sweeper_task = bot_loop.create_task(_sweep_stale_selections())
    except Exception:
        pass


async def _download_files(listener, client, path, files, tracker, is_single,
                          orig_top, dests=None):
    if dests is None:
        dests = [_dest_for(path, listener.name, orig_top, f, is_single) for f in files]

    try:
        await client.reserve_files(list(zip(dests, (f.size for f in files))))
    except TeraboxError as e:
        await listener.on_download_error(str(e))
        return

    failed = []
    completed_any = False
    for f, dest in zip(files, dests):
        if tracker.is_cancelled or listener.is_cancelled:
            return
        tracker.start_file()
        try:
            await client.download_file(
                f, dest,
                progress_cb=tracker.on_progress,
                cancel_event=tracker.cancel_event,
            )
            tracker.finish_file(f.size)
            completed_any = True
        except TeraboxCancelled:
            return
        except TeraboxError as e:
            failed.append((f.name, str(e)))
            try:
                if await aiopath.exists(dest):
                    await remove(dest)
            except Exception:
                pass

    if tracker.is_cancelled or listener.is_cancelled:
        return

    if not completed_any:
        reasons = "; ".join(f"{n}: {r}" for n, r in failed[:5])
        await listener.on_download_error(
            f"All Terabox file(s) failed to download. {reasons}"
        )
        return
    await listener.on_download_complete()


async def _start_web_selection(listener, client, result, path, gid, link_key, prompt):
    _ensure_sweeper_running()
    tracker = TeraboxDownloadTracker(listener)
    tb_gid = f"terabox_{gid}"
    _terabox_selections[gid] = {
        "result": result,
        "client": client,
        "listener": listener,
        "download_path": path,
        "link_key": link_key,
        "tracker": tracker,
        "created_at": time(),
    }

    _gid = gid
    _link_key = link_key

    async def _cleanup_selection():
        state = _terabox_selections.pop(_gid, None)
        _store_delete(_gid)
        await _discard_terabox_link(_link_key)
        if state and state.get("client") is not None:
            await state["client"].aclose()

    tracker._cleanup_selection = _cleanup_selection

    file_list_meta = _build_file_list_meta(result.files)
    if not _store_write(gid, file_list_meta, []):
        _terabox_selections.pop(gid, None)
        tracker._cleanup_selection = None
        await listener.on_download_error("Failed to persist selection state — disk full?")
        return False

    buttons = terabox_selection_buttons(tb_gid)
    await send_message(listener.message, prompt, buttons)
    listener.size = result.total_size
    async with task_dict_lock:
        task_dict[listener.mid] = TeraboxDownloadStatus(
            listener, tracker, tb_gid, "dl"
        )
    return True

def get_terabox_selection_owner_id(gid):
    state = _terabox_selections.get(gid)
    if not state:
        return None
    listener = state.get("listener")
    return getattr(listener, "user_id", None)


async def add_terabox_download(listener, path):
    if not _TERABOX_AVAILABLE:
        await listener.on_download_error(
            "teraboxSDK is not installed in this image. Pull the latest image from irisxdr/neo-wzml."
        )
        return
    if not Config.TERABOX_ENABLED:
        await listener.on_download_error(
            "Terabox downloads are currently disabled by the bot owner."
        )
        return
    cookie_file = await _select_download_cookie(listener)
    if not cookie_file:
        await listener.on_download_error(
            "Terabox is not configured. Upload your <b>terabox.txt</b> cookie in "
            "User Settings, or have the owner add a global one in Bot Settings "
            "\u2192 Private Files."
        )
        return

    link_key = await _reserve_terabox_link(listener.link)
    if link_key is None:
        await listener.on_download_error(
            "This Terabox link is already being downloaded! Wait for it to finish."
        )
        return

    client = None
    _handoff = False
    try:
        gid = token_hex(5)
        await makedirs(path, exist_ok=True)
        client = _build_client(cookie_file)

        try:
            await client.login()
        except TeraboxError as e:
            await listener.on_download_error(f"Terabox login failed: {e}")
            return

        try:
            result = await client.resolve(listener.link, recursive=True)
        except TeraboxPasswordError as e:
            await listener.on_download_error(str(e))
            return
        except TeraboxError as e:
            await listener.on_download_error(f"Terabox: {e}")
            return

        files = result.file_entries
        if not files:
            await listener.on_download_error(
                "No downloadable files found in this Terabox link."
            )
            return

        listener.name = listener.name or _sanitize_name(result.name)
        is_single = (not result.is_folder) and len(files) == 1

        # --- web-based file selection for multi-file folders ----------------
        if listener.select and result.is_folder and len(files) > 1:
            if not get_valid_base_url():
                await listener.on_download_error(
                    "BASE_URL must be a public http(s) URL to use Terabox file selection."
                )
                return
            _handoff = await _start_web_selection(
                listener, client, result, path, gid, link_key,
                "Your Terabox folder is ready. Choose files then press Done "
                "Selecting to start downloading.",
            )
            return

        # --- straight download (single file or whole folder) ----------------
        listener.size = sum(f.size for f in files)

        msg, button = await stop_duplicate_check(listener)
        if msg:
            await listener.on_download_error(msg, button)
            return

        if limit_exceeded := await limit_checker(listener):
            await listener.on_download_error(limit_exceeded, is_limit=True)
            return

        added_to_queue, event = await check_running_tasks(listener)
        if added_to_queue:
            async with task_dict_lock:
                task_dict[listener.mid] = QueueStatus(listener, gid, "Dl")
            await listener.on_download_start()
            if listener.multi <= 1:
                await send_status_message(listener.message)
            await event.wait()
            if listener.is_cancelled:
                return

        reserve = Config.STORAGE_LIMIT * 1024**3
        if listener.size and not await check_storage_threshold(listener.size, reserve):
            await listener.on_download_error(
                " • <b>Required Disk:</b> "
                f"{get_readable_file_size(reserve + listener.size)}\n"
                f" • <b>Storage Reserve:</b> {get_readable_file_size(reserve)}\n"
                " • <i>Insufficient disk space for this Task, use other bots</i>",
                is_limit=True,
            )
            return

        tracker = TeraboxDownloadTracker(listener)
        async with task_dict_lock:
            task_dict[listener.mid] = TeraboxDownloadStatus(
                listener, tracker, gid, "dl"
            )

        await listener.on_download_start()
        if listener.multi <= 1:
            await send_status_message(listener.message)

        await _download_files(
            listener, client, path, files, tracker, is_single, result.name
        )

    except Exception as e:
        if not _handoff:
            await listener.on_download_error(f"Internal error: {e}")
    finally:
        if not _handoff:
            await _discard_terabox_link(link_key)
            if client is not None:
                await client.aclose()


def _account_client(listener):
    cookie = listener.terabox_cookie or _COOKIE_FILE
    return TeraboxClient(cookie_file=os.path.abspath(cookie))


async def _expand_account_selection(client, selection):
    multi = len(selection) > 1
    pairs = []
    for sel in selection:
        if sel.get("is_dir"):
            folder = (sel.get("path") or "/").rstrip("/")
            res = await client.walk_account_dir(folder or "/")
            top = _sanitize_name(sel.get("name") or "TeraBox")
            for f in res.file_entries:
                if folder and f.path.startswith(folder):
                    inside = f.path[len(folder):].lstrip("/")
                elif not folder:  # whole-account (root) selection
                    inside = f.path.lstrip("/")
                else:
                    inside = f.name
                inside = inside or f.name
                rel = posixpath.join(top, inside) if multi else inside
                pairs.append((f, rel))
        else:
            f = TeraboxFile(
                name=sel.get("name") or "file",
                path=sel.get("path") or "",
                fs_id=str(sel.get("fs_id", "") or ""),
                size=int(sel.get("size", 0) or 0),
                is_dir=False,
            )
            rel = posixpath.join(_sanitize_name(f.name), f.name) if multi else f.name
            pairs.append((f, rel))
    return pairs


async def add_terabox_account_download(listener, path):

    if not _TERABOX_AVAILABLE:
        await listener.on_download_error(
            "teraboxSDK is not installed in this image. Pull the latest image from irisxdr/neo-wzml."
        )
        return
    if not Config.TERABOX_ENABLED:
        await listener.on_download_error(
            "Terabox is currently disabled by the bot owner."
        )
        return

    use_web = bool(getattr(listener, "_tbx_web", False)) and bool(get_valid_base_url())
    selection = list(getattr(listener, "_tbx_selection", []) or [])
    if not use_web and not selection:
        await listener.on_download_error("No TeraBox selection was made.")
        return

    client = _account_client(listener)
    handoff = False
    try:
        try:
            await client.login()
        except TeraboxError as e:
            await listener.on_download_error(f"Terabox: {e}")
            return

        if use_web:
            await makedirs(path, exist_ok=True)
            try:
                result = await client.walk_account_dir("/")
            except TeraboxError as e:
                await listener.on_download_error(f"Terabox: {e}")
                return
            if not result.file_entries:
                await listener.on_download_error(
                    "Your TeraBox account has no files to select."
                )
                return
            listener.name = listener.name or _sanitize_name(result.name or "TeraBox")
            gid = token_hex(5)
            handoff = await _start_web_selection(
                listener, client, result, path, gid, "",
                "Your TeraBox account is ready. Open the selector, choose files, "
                "then press Done Selecting to start downloading.",
            )
            return

        try:
            files_rel = await _expand_account_selection(client, selection)
        except TeraboxError as e:
            await listener.on_download_error(f"Terabox: {e}")
            return

        files = [f for f, _ in files_rel]
        if not files:
            await listener.on_download_error(
                "No downloadable files in the selected TeraBox item(s)."
            )
            return

        is_single = len(selection) == 1 and not selection[0].get("is_dir")
        default_name = (
            selection[0].get("name")
            if len(selection) == 1
            else f"Terabox_{len(selection)}_items"
        )
        listener.name = listener.name or _sanitize_name(default_name)

        base = _sanitize_name(listener.name)
        if is_single:
            dests = [os.path.join(path, base)]
        else:
            dests = [
                os.path.join(path, base, *[
                    _sanitize_name(p) for p in rel.split("/") if p
                ])
                for _, rel in files_rel
            ]

        listener.size = sum(f.size for f in files)

        msg, button = await stop_duplicate_check(listener)
        if msg:
            await listener.on_download_error(msg, button)
            return
        if limit_exceeded := await limit_checker(listener):
            await listener.on_download_error(limit_exceeded, is_limit=True)
            return

        gid = token_hex(5)
        added_to_queue, event = await check_running_tasks(listener)
        if added_to_queue:
            async with task_dict_lock:
                task_dict[listener.mid] = QueueStatus(listener, gid, "Dl")
            await listener.on_download_start()
            if listener.multi <= 1:
                await send_status_message(listener.message)
            await event.wait()
            if listener.is_cancelled:
                return

        reserve = Config.STORAGE_LIMIT * 1024**3
        if listener.size and not await check_storage_threshold(listener.size, reserve):
            await listener.on_download_error(
                " • <b>Required Disk:</b> "
                f"{get_readable_file_size(reserve + listener.size)}\n"
                f" • <b>Storage Reserve:</b> {get_readable_file_size(reserve)}\n"
                " • <i>Insufficient disk space for this Task, use other bots</i>",
                is_limit=True,
            )
            return

        tracker = TeraboxDownloadTracker(listener)
        async with task_dict_lock:
            task_dict[listener.mid] = TeraboxDownloadStatus(
                listener, tracker, gid, "dl"
            )
        await listener.on_download_start()
        if listener.multi <= 1:
            await send_status_message(listener.message)

        await _download_files(
            listener, client, path, files, tracker,
            is_single=is_single, orig_top="", dests=dests,
        )
    except Exception as e:
        if not listener.is_cancelled:
            await listener.on_download_error(f"Internal error: {e}")
    finally:
        if client is not None and not handoff:
            await client.aclose()


async def resume_terabox_with_selection(gid):
    state = _terabox_selections.pop(gid, None)
    if not state:
        _store_delete(gid)
        return

    listener = state["listener"]
    client = state["client"]
    result = state["result"]
    path = state["download_path"]
    link_key = state.get("link_key", "")
    tracker = state["tracker"]
    tracker._cleanup_selection = None

    try:
        store_data = _store_read(gid)
        selected_ids = set(store_data.get("selected_ids", []) if store_data else [])
        _store_delete(gid)

        if not selected_ids:
            await listener.on_download_error("No files selected")
            return

        selected = [
            f for f in result.file_entries if str(f.fs_id) in selected_ids
        ]
        if not selected:
            await listener.on_download_error("No valid files in selection")
            return

        listener.size = sum(f.size for f in selected)

        if limit_exceeded := await limit_checker(listener):
            await listener.on_download_error(limit_exceeded, is_limit=True)
            return

        msg, button = await stop_duplicate_check(listener)
        if msg:
            await listener.on_download_error(msg, button)
            return

        reserve = Config.STORAGE_LIMIT * 1024**3
        if listener.size and not await check_storage_threshold(listener.size, reserve):
            await listener.on_download_error(
                " • <b>Required Disk:</b> "
                f"{get_readable_file_size(reserve + listener.size)}\n"
                f" • <b>Storage Reserve:</b> {get_readable_file_size(reserve)}\n"
                " • <i>Insufficient disk space for this Task, use other bots</i>",
                is_limit=True,
            )
            return

        async with task_dict_lock:
            task_dict[listener.mid] = TeraboxDownloadStatus(
                listener, tracker, f"terabox_{gid}", "dl"
            )
        await listener.on_download_start()
        if listener.multi <= 1:
            await send_status_message(listener.message)

        await _download_files(
            listener, client, path, selected, tracker,
            is_single=False, orig_top=result.name,
        )

    except Exception as e:
        if not listener.is_cancelled:
            await listener.on_download_error(f"Internal error: {e}")
    finally:
        await _discard_terabox_link(link_key)
        if client is not None:
            await client.aclose()


async def cancel_terabox_selection(gid):
    state = _terabox_selections.pop(gid, None)
    _store_delete(gid)
    if not state:
        return
    tracker = state.get("tracker")
    if tracker is not None:
        tracker._cleanup_selection = None
    await _discard_terabox_link(state.get("link_key", ""))
    client = state.get("client")
    if client is not None:
        await client.aclose()
    await state["listener"].on_download_error("Cancelled by user")
