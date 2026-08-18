# This file is a part of NEO-WZML (github.com/irisXDR/NEO-WZML)

import os
import random
import re
from asyncio import Lock as AsyncLock, sleep
from secrets import token_hex
from time import time

from aiofiles.os import makedirs, remove, path as aiopath
from aioshutil import rmtree

from mega import MegaApi, MegaStringList

try:
    # Mega SDK >= 4.x; omitted in some patched builds.
    from mega import MegaCancelToken
except ImportError:  # pragma: no cover - depends on SDK build flags
    MegaCancelToken = None

from bot import LOGGER, task_dict, task_dict_lock
from bot.core.config_manager import Config
from bot.helper.ext_utils.bot_utils import get_mega_link_type, get_valid_base_url
from bot.helper.ext_utils.bot_utils import sync_to_async

from bot.helper.ext_utils.task_manager import (
    check_running_tasks,
    stop_duplicate_check,
    limit_checker,
)
from bot.helper.ext_utils.files_utils import check_storage_threshold
from bot.helper.ext_utils.status_utils import get_readable_file_size
from bot.helper.mirror_leech_utils.status_utils.mega_status import MegaDownloadStatus
from bot.helper.mirror_leech_utils.status_utils.queue_status import QueueStatus
from bot.helper.telegram_helper.message_utils import (
    send_status_message,
    send_message,
)
from bot.helper.listeners.mega_listener import (
    MegaAppListener,
    MegaFolderListener,
    AsyncMega,
    friendly_mega_error,
)
from bot.helper.ext_utils.bot_utils import mega_selection_buttons
from web.mega_selection_store import (
    write_state as _store_write,
    read_state as _store_read,
    delete_state as _store_delete,
)

_mega_selections = {}
_active_mega_links = set()
_active_mega_links_lock = AsyncLock()

_SELECTION_TTL_SECONDS = 30 * 60
_INCOMPLETE_RETRY_LIMIT = 4

_sweeper_task = None

_UNSAFE_NAME_CHARS = re.compile(r'[/\\\x00:*?"<>|]')


def _normalize_mega_link(link: str) -> str:
    """Strip whitespace and trailing slash to normalize duplicate guard."""
    return link.strip().rstrip("/")


async def _reserve_mega_link(link: str) -> str | None:
    """Atomically check and reserve a normalized link."""
    key = _normalize_mega_link(link)
    async with _active_mega_links_lock:
        if key in _active_mega_links:
            return None
        _active_mega_links.add(key)
    return key


def _has_mega_folder_key(link):
    if "#F!" in link:
        marker = link.rsplit("!", 1)
        return len(marker) == 2 and bool(marker[1])
    if "/folder/" in link:
        marker = link.rsplit("#", 1)
        return len(marker) == 2 and bool(marker[1])
    return False


def _make_cancel_token():
    """Best-effort MegaCancelToken factory."""
    if MegaCancelToken is None:
        return None
    try:
        return MegaCancelToken.createInstance()
    except Exception as e:
        LOGGER.error(f"Mega: failed to create cancel token: {e}")
        return None


async def _close_mega(async_api, mega_base):
    """Logout APIs and remove SDK cache dir."""
    if async_api is None:
        return

    try:
        await async_api.logout()
    except Exception as e:
        LOGGER.error(f"Mega logout cleanup failed: {e}")

    # Manual detach has caused SDK segfaults.

    try:
        if mega_base and os.path.basename(os.path.dirname(mega_base)) == ".mega_sdk":
            await rmtree(mega_base, ignore_errors=True)
    except Exception as e:
        LOGGER.error(f"Mega SDK cache cleanup failed: {e}")


async def _discard_link(link_key: str) -> None:
    """Remove link key from active set."""
    if not link_key:
        return
    async with _active_mega_links_lock:
        _active_mega_links.discard(link_key)


def _sanitize_name(name):
    """Normalize node names for filesystem paths."""
    if not name:
        return "unnamed"
    cleaned = _UNSAFE_NAME_CHARS.sub("_", str(name)).strip()
    # Windows rejects trailing dots/spaces
    cleaned = cleaned.rstrip(". ").lstrip()
    return cleaned or "unnamed"


def _node_is_folder(node):
    try:
        return bool(node.isFolder())
    except Exception:
        return node.getType() == 1


async def _cleanup_partial_download(download_path, name):
    if not name:
        return
    target = os.path.join(download_path, name)
    try:
        if await aiopath.isdir(target):
            await rmtree(target, ignore_errors=True)
        elif await aiopath.exists(target):
            await remove(target)
    except Exception as e:
        LOGGER.error(f"Mega: failed to cleanup partial download {target}: {e}")


def _retry_backoff(attempt):
    """Exponential backoff with jitter, capped at 60s."""
    base = min(60, 5 * (2 ** attempt))
    return base + random.uniform(0, 3)


def _walk_mega_tree(api, node):
    """Iterative folder walk (no recursion depth cap). Each file dict's
    ``node`` is authorizeNode-owned for startDownload on folder_api.
    ``handle_b64`` is the base64-encoded handle for the SDK filter API."""
    entries = []
    if node is None:
        return entries

    _children_keepalive = []

    stack = [(node, "/")]
    while stack:
        parent_node, prefix = stack.pop()
        try:
            children = api.getChildren(parent_node)
        except Exception as e:
            LOGGER.error(f"Mega getChildren failed at {prefix!r}: {e}")
            continue
        if children is None:
            continue
        _children_keepalive.append(children)

        try:
            count = children.size()
        except Exception as e:
            LOGGER.error(f"Mega children.size() failed at {prefix!r}: {e}")
            continue

        local_children = []
        for i in range(count):
            try:
                child = children.get(i)
            except Exception as e:
                LOGGER.error(
                    f"Mega children.get({i}) failed at {prefix!r}: {e}"
                )
                continue
            if child is None:
                continue
            local_children.append(child)

        for child in local_children:
            try:
                handle = str(child.getHandle())
                handle_b64 = MegaApi.handleToBase64(child.getHandle())
                raw_name = child.getName()
                name = _sanitize_name(raw_name)
                size = child.getSize()
                is_dir = bool(child.isFolder()) or child.getType() == 1
            except Exception as e:
                LOGGER.error(
                    f"Mega: skipping malformed child node under {prefix!r}: {e}"
                )
                continue
            if is_dir:
                entries.append({
                    "name": name,
                    "size": 0,
                    "path": prefix,
                    "id": handle,
                    "handle_b64": handle_b64,
                    "is_dir": True,
                    "node": None,
                })
            else:
                try:
                    owned = api.authorizeNode(child)
                except Exception as e:
                    LOGGER.error(
                        f"Mega: authorizeNode raised for '{name}' "
                        f"(handle={handle}): {e}; skipping"
                    )
                    continue
                if owned is None:
                    LOGGER.warning(
                        f"Mega: failed to authorize file node '{name}' "
                        f"(handle={handle}); skipping"
                    )
                    continue
                entries.append({
                    "name": name,
                    "size": size,
                    "path": prefix,
                    "id": handle,
                    "handle_b64": handle_b64,
                    "is_dir": False,
                    "node": owned,
                })

        for child in reversed(local_children):
            try:
                if bool(child.isFolder()) or child.getType() == 1:
                    sub_name = _sanitize_name(child.getName())
                    stack.append((child, f"{prefix}{sub_name}/"))
            except Exception:
                continue

    return entries


async def _sweep_stale_selections():
    """Background TTL cleanup for abandoned MEGA selection state."""
    LOGGER.info("Mega selection sweeper: started")
    while True:
        try:
            await sleep(60)
            now = time()
            stale = []
            for gid, state in list(_mega_selections.items()):
                created_at = state.get("created_at", 0)
                if now - created_at >= _SELECTION_TTL_SECONDS:
                    stale.append(gid)
            for gid in stale:
                state = _mega_selections.pop(gid, None)
                if not state:
                    continue
                LOGGER.warning(
                    f"Mega selection {gid} expired after "
                    f"{_SELECTION_TTL_SECONDS}s; releasing resources"
                )
                _store_delete(gid)
                link_key = state.get("link_key", "")
                await _discard_link(link_key)
                try:
                    await _close_mega(
                        state.get("async_api"), state.get("mega_base", "")
                    )
                except Exception as e:
                    LOGGER.error(
                        f"Mega sweeper: close failed for {gid}: {e}"
                    )
                listener = state.get("listener")
                if listener is not None:
                    try:
                        await listener.on_download_error(
                            "Mega file selection expired (no choice in "
                            f"{_SELECTION_TTL_SECONDS // 60} minutes). "
                            "Re-send the link if you still want it."
                        )
                    except Exception as e:
                        LOGGER.error(
                            f"Mega sweeper: notify failed for {gid}: {e}"
                        )
        except Exception as e:
            LOGGER.error(f"Mega selection sweeper iteration failed: {e}", exc_info=True)


def _ensure_sweeper_running():
    global _sweeper_task
    if _sweeper_task is not None and not _sweeper_task.done():
        return
    try:
        from bot import bot_loop
        _sweeper_task = bot_loop.create_task(_sweep_stale_selections())
    except Exception as e:
        LOGGER.error(f"Mega: could not start selection sweeper: {e}")


async def add_mega_download(listener, path):
    if not Config.MEGA_ENABLED:
        await listener.on_download_error("Mega.nz downloads are currently disabled by the bot owner.")
        return

    link_key = await _reserve_mega_link(listener.link)
    if link_key is None:
        await listener.on_download_error(
            "This Mega link is already being downloaded! Wait for it to finish."
        )
        return

    async_api = None
    mega_base = ""
    _handoff = False
    cancel_token = None

    try:
        gid = token_hex(5)
        await makedirs(path, exist_ok=True)
        mega_base = os.path.join(os.path.dirname(path.rstrip("/")), ".mega_sdk", gid)
        mega_main = os.path.join(mega_base, "main")
        await makedirs(mega_main, exist_ok=True)
        app_key = ""
        ua = "NEO-WZML"
        workers = 4

        async_api = AsyncMega()
        async_api.api = api = MegaApi(app_key, mega_main, ua, workers)
        folder_api = None

        mega_listener = MegaAppListener(async_api, listener)
        async_api._mega_listener = mega_listener
        api.addListener(mega_listener)

        if (MEGA_EMAIL := Config.MEGA_EMAIL) and (MEGA_PASSWORD := Config.MEGA_PASSWORD):
            await async_api.login(MEGA_EMAIL, MEGA_PASSWORD)
            if mega_listener.error:
                await listener.on_download_error(mega_listener.error)
                return
            await async_api.fetchNodes()
            if mega_listener.error:
                await listener.on_download_error(mega_listener.error)
                return

        if get_mega_link_type(listener.link) == "file":
            await async_api.getPublicNode(listener.link)
            node = mega_listener.public_node
            if not node:
                await listener.on_download_error("Failed to get public MEGA node")
                return
        else:
            if not _has_mega_folder_key(listener.link):
                await listener.on_download_error("MEGA folder link is missing its decryption key")
                return

            mega_folder = os.path.join(mega_base, "folder")
            await makedirs(mega_folder, exist_ok=True)
            async_api.folder_api = folder_api = MegaApi(app_key, mega_folder, ua, workers)
            folder_listener = MegaFolderListener(mega_listener)
            async_api._folder_listener = folder_listener
            folder_api.addListener(folder_listener)

            if MEGA_EMAIL and MEGA_PASSWORD:
                auth = await sync_to_async(api.getAccountAuth)
                if auth:
                    await sync_to_async(folder_api.setAccountAuth, auth)

            await async_api.loginToFolder(listener.link)
            if mega_listener.error:
                await listener.on_download_error(mega_listener.error)
                return

            await async_api.fetchNodes(folder_api, source="folder")
            if mega_listener.error:
                await listener.on_download_error(mega_listener.error)
                return

            mega_listener.node = await sync_to_async(folder_api.getRootNode)
            if not mega_listener.node:
                await listener.on_download_error("Failed to get folder root node")
                return
            node = await sync_to_async(folder_api.authorizeNode, mega_listener.node)
            if not node:
                await listener.on_download_error("Failed to authorize MEGA folder node")
                return

        if mega_listener.error:
            await listener.on_download_error(mega_listener.error)
            return

        listener.name = listener.name or _sanitize_name(node.getName())

        if listener.select and get_mega_link_type(listener.link) == "folder":
            if not get_valid_base_url():
                await listener.on_download_error(
                    "BASE_URL must be a public http(s) URL to use Mega file selection."
                )
                return

            _ensure_sweeper_running()
            file_list = await sync_to_async(_walk_mega_tree, folder_api, node)
            file_nodes = [f for f in file_list if not f["is_dir"]]
            if not file_nodes:
                await listener.on_download_error("Folder contains no files")
                return
            mega_gid = f"mega_{gid}"
            _mega_selections[gid] = {
                "file_list": file_list,
                "async_api": async_api,
                "folder_api": folder_api,
                "mega_listener": mega_listener,
                "listener": listener,
                "mega_base": mega_base,
                "download_path": path,
                "link_key": link_key,
                "node": node,
                "created_at": time(),
            }
            mega_listener._selection_gid = gid

            _gid = gid
            _link_key = link_key

            async def _cleanup_selection():
                state = _mega_selections.pop(_gid, None)
                _store_delete(_gid)
                await _discard_link(_link_key)
                if state:
                    await _close_mega(state["async_api"], state["mega_base"])

            mega_listener._cleanup_selection = _cleanup_selection

            file_list_metadata = [
                {k: v for k, v in f.items() if k != "node"} for f in file_list
            ]
            if not _store_write(gid, file_list_metadata, []):
                LOGGER.error(
                    f"Mega selection: failed to write store file for gid {gid}"
                )
                _mega_selections.pop(gid, None)
                mega_listener._cleanup_selection = None
                await listener.on_download_error(
                    "Failed to persist selection state — disk full?"
                )
                return

            buttons = mega_selection_buttons(mega_gid)
            await send_message(
                listener.message,
                "Your Mega folder is ready. Choose files then press Done Selecting to start downloading.",
                buttons,
            )
            listener.size = await sync_to_async(folder_api.getSize, node)
            async with task_dict_lock:
                task_dict[listener.mid] = MegaDownloadStatus(
                    listener, mega_listener, mega_gid, "dl"
                )
            _handoff = True
            return

        msg, button = await stop_duplicate_check(listener)
        if msg:
            await listener.on_download_error(msg, button)
            return

        is_folder_download = _node_is_folder(node)

        if is_folder_download and folder_api:
            file_entries = await sync_to_async(_walk_mega_tree, folder_api, node)
            file_entries = [f for f in file_entries if not f["is_dir"]]
            if not file_entries:
                await listener.on_download_error("Folder contains no files")
                return

            listener.size = sum(f["size"] for f in file_entries)
        else:
            listener.size = await sync_to_async(
                folder_api.getSize if folder_api else api.getSize, node
            )

        if limit_exceeded := await limit_checker(listener):
            await listener.on_download_error(limit_exceeded, is_limit=True)
            return

        added_to_queue, event = await check_running_tasks(listener)
        if added_to_queue:
            LOGGER.info(f"Added to Queue/Download: {listener.name}")
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

        async with task_dict_lock:
            task_dict[listener.mid] = MegaDownloadStatus(listener, mega_listener, gid, "dl")

        if added_to_queue:
            LOGGER.info(f"Start Queued Download from Mega: {listener.name}")
        else:
            LOGGER.info(f"Download from Mega: {listener.name}")
        await listener.on_download_start()
        if listener.multi <= 1:
            await send_status_message(listener.message)

        if is_folder_download and folder_api:
            folder_dest = os.path.join(path, listener.name)
            await makedirs(folder_dest, exist_ok=True)

            cancel_token = _make_cancel_token()
            mega_listener._cancel_token = cancel_token
            mega_listener._sdk_folder_download = True
            mega_listener._sdk_folder_total_files = len(file_entries)
            mega_listener._sdk_folder_failed_files = []
            mega_listener._sdk_folder_downloaded_bytes = 0
            mega_listener._sub_transfers = {}
            mega_listener._caller_manages_completion = True
            async_api._download_is_folder = True

            await async_api.startDownload(
                node, path, listener.name, None, False, cancel_token,
                3, 2, False,
            )
            await async_api.wait_for_transfer()

            if mega_listener.is_cancelled or listener.is_cancelled:
                return
            failed = mega_listener._sdk_folder_failed_files
            if failed and len(failed) >= len(file_entries):
                listener.mega_skipped_files = [
                    (name, friendly_mega_error(err)) for name, err in failed
                ]
                await listener.on_download_error(
                    f"All {len(failed)} file(s) in this MEGA folder were "
                    f"blocked or unavailable. Per-file reasons follow below."
                )
                return
            if failed:
                LOGGER.info(
                    "Mega folder-dl: %d file(s) failed out of %d",
                    len(failed), len(file_entries),
                )
                listener.mega_skipped_files = [
                    (name, friendly_mega_error(err)) for name, err in failed
                ]
                await listener.on_download_complete()
                return
            if mega_listener.error:
                await listener.on_download_error(
                    friendly_mega_error(mega_listener.error)
                )
                return
            await listener.on_download_complete()

        else:
            for attempt in range(_INCOMPLETE_RETRY_LIMIT + 1):
                cancel_token = _make_cancel_token()
                mega_listener._cancel_token = cancel_token
                mega_listener.error = None
                mega_listener.retryable_error = None
                mega_listener._bytes_transferred = 0
                mega_listener._total_downloaded_bytes = 0

                await async_api.startDownload(
                    node, path, listener.name, None, False, cancel_token,
                    3,
                    2,
                    False,
                )
                await async_api.wait_for_transfer()

                if listener.is_cancelled or mega_listener.is_cancelled:
                    return
                if not mega_listener.retryable_error:
                    return
                if attempt >= _INCOMPLETE_RETRY_LIMIT:
                    await listener.on_download_error(
                        friendly_mega_error(mega_listener.retryable_error)
                    )
                    return

                LOGGER.warning(
                    "Mega download incomplete; retrying %s/%s: %s",
                    attempt + 1,
                    _INCOMPLETE_RETRY_LIMIT,
                    mega_listener.retryable_error,
                )
                await _cleanup_partial_download(path, listener.name)
                await sleep(_retry_backoff(attempt))

    except Exception as e:
        LOGGER.error(f"Unexpected error in add_mega_download: {e}", exc_info=True)
        if not _handoff:
            await listener.on_download_error(f"Internal error: {e}")
    finally:
        if not _handoff:
            await _discard_link(link_key)
            await _close_mega(async_api, mega_base)

def get_mega_selection_owner_id(gid):
    state = _mega_selections.get(gid)
    if not state:
        return None
    listener = state.get("listener")
    return getattr(listener, "user_id", None)


async def resume_mega_with_selection(gid):
    state = _mega_selections.pop(gid, None)
    if not state:
        _store_delete(gid)
        return

    listener = state["listener"]
    file_list = state["file_list"]
    async_api = state["async_api"]
    folder_api = state["folder_api"]
    mega_listener = state["mega_listener"]
    mega_base = state["mega_base"]
    download_path = state["download_path"]
    link_key = state.get("link_key", "")
    node = state.get("node")

    mega_listener._cleanup_selection = None

    cancel_token = None
    try:
        store_data = _store_read(gid)
        selected_ids = set(store_data.get("selected_ids", []) if store_data else [])
        _store_delete(gid)

        if not selected_ids:
            await listener.on_download_error("No files selected")
            return

        selected_entries = [
            f for f in file_list if f["id"] in selected_ids and not f["is_dir"]
        ]
        if not selected_entries:
            await listener.on_download_error("No valid files in selection")
            return

        listener.size = sum(f["size"] for f in selected_entries)
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

        await listener.on_download_start()
        if listener.multi <= 1:
            await send_status_message(listener.message)

        # Build SDK filter with selected file handles
        filter_list = MegaStringList.createInstance()
        for entry in selected_entries:
            handle_b64 = entry.get("handle_b64")
            if handle_b64:
                filter_list.add(handle_b64)
        await sync_to_async(folder_api.setFolderDownloadFilter, filter_list)

        folder_dest = os.path.join(download_path, listener.name)
        await makedirs(folder_dest, exist_ok=True)

        cancel_token = _make_cancel_token()
        mega_listener._cancel_token = cancel_token
        mega_listener._sdk_folder_download = True
        mega_listener._sdk_folder_total_files = len(selected_entries)
        mega_listener._sdk_folder_failed_files = []
        mega_listener._sdk_folder_downloaded_bytes = 0
        mega_listener._sub_transfers = {}
        mega_listener._caller_manages_completion = True
        async_api._download_is_folder = True

        await async_api.startDownload(
            node, download_path, listener.name, None, False, cancel_token,
            3, 2, False,
        )
        await async_api.wait_for_transfer()

        await sync_to_async(folder_api.clearFolderDownloadFilter)

        if mega_listener.is_cancelled or listener.is_cancelled:
            return
        failed = mega_listener._sdk_folder_failed_files
        if failed and len(failed) >= len(selected_entries):
            listener.mega_skipped_files = [
                (name, friendly_mega_error(err)) for name, err in failed
            ]
            await listener.on_download_error(
                f"All {len(failed)} selected file(s) were blocked or "
                f"unavailable. Per-file reasons follow below."
            )
            return
        if failed:
            LOGGER.info(
                "Mega selection-dl: %d file(s) failed out of %d",
                len(failed), len(selected_entries),
            )
            listener.mega_skipped_files = [
                (name, friendly_mega_error(err)) for name, err in failed
            ]
            await listener.on_download_complete()
            return
        if mega_listener.error:
            await listener.on_download_error(
                friendly_mega_error(mega_listener.error)
            )
            return
        # Full success — caller drives completion.
        await listener.on_download_complete()

    except Exception as e:
        LOGGER.error(f"Unexpected error in resume_mega_with_selection: {e}", exc_info=True)
        if not listener.is_cancelled:
            await listener.on_download_error(f"Internal error: {e}")
    finally:
        await _discard_link(link_key)
        await _close_mega(async_api, mega_base)


async def cancel_mega_selection(gid):
    state = _mega_selections.pop(gid, None)
    _store_delete(gid)
    if not state:
        return
    mega_listener = state.get("mega_listener")
    if mega_listener is not None:
        mega_listener._cleanup_selection = None
    link_key = state.get("link_key", "")
    await _discard_link(link_key)
    await _close_mega(state["async_api"], state["mega_base"])
    await state["listener"].on_download_error("Cancelled by user")
