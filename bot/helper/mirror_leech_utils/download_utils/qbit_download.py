# This file is a part of NEO-WZML (github.com/irisXDR/NEO-WZML)

from aiofiles.os import remove, path as aiopath
from aiofiles import open as aiopen
from asyncio import sleep, TimeoutError
from aioqbt.api import AddFormBuilder
from aioqbt.exc import AQError
from aiohttp.client_exceptions import ClientError

from bot import (
    task_dict,
    task_dict_lock,
    LOGGER,
    qb_torrents,
    DOWNLOAD_DIR,
)
from bot.core.config_manager import Config
from bot.core.torrent_manager import TorrentManager
from bot.helper.ext_utils.bot_utils import bt_selection_buttons
from bot.helper.ext_utils.task_manager import check_running_tasks
from bot.helper.listeners.qbit_listener import on_download_start
from bot.helper.mirror_leech_utils.qbit_compat import (
    PAUSED_STATES,
    is_metadata_state,
    torrent_tags,
)
from bot.helper.mirror_leech_utils.status_utils.qbit_status import QbittorrentStatus
from bot.helper.telegram_helper.message_utils import (
    send_message,
    delete_message,
    send_status_message,
)

from re import search as re_search
from base64 import b16encode, b32decode
from bot.helper.ext_utils.status_utils import get_task_by_gid


def _get_hash_magnet(mgt: str):
    m = re_search(r'(?<=xt=urn:btih:)[a-zA-Z0-9]+', mgt)
    if not m:
        # `btmh` (multihash) magnets are valid too but unsupported here.
        raise ValueError(f"No btih hash found in magnet URI: {mgt!r}")
    hash_ = m.group(0)
    if len(hash_) == 32:
        hash_ = b16encode(b32decode(hash_.upper())).decode()
    return hash_


async def add_qb_torrent(listener, path, ratio, seed_time):
    if Config.DISABLE_TORRENTS:
        await listener.on_download_error("Torrents are disabled in the configuration.")
        return

    try:
        form = AddFormBuilder.with_client(TorrentManager.qbittorrent)

        downloaded_torrent = None

        if await aiopath.exists(listener.link):
            async with aiopen(listener.link, "rb") as f:
                data = await f.read()
                form = form.include_file(data)
            downloaded_torrent = listener.link
        elif listener.link.lower().startswith("magnet:"):
            form = form.include_url(listener.link)
        elif listener.link.lower().startswith(("http://", "https://")):
            await listener.on_download_error(
                "⚠️ HTTP torrent links are not supported.\n\n"
                "Please send one of the following:\n"
                "• Torrent file (.torrent)\n"
                "• Magnet link (magnet:?xt=...)\n"
            )
            return
        else:
            await listener.on_download_error(
                "⚠️ Invalid torrent link format.\n\n"
                "Please send one of the following:\n"
                "• Torrent file (.torrent)\n"
                "• Magnet link (magnet:?xt=...)\n"
            )
            return
        form = form.savepath(path).tags([f"{listener.mid}"])
        add_to_queue, event = await check_running_tasks(listener)
        if add_to_queue:
            form = form.stopped(add_to_queue)
        if ratio:
            form = form.ratio_limit(ratio)
        if seed_time:
            form = form.seeding_time_limit(int(seed_time))
        try:
            await TorrentManager.qbittorrent.torrents.add(form.build())
        except (ClientError, TimeoutError, Exception, AQError) as e:
            zombie_found = False
            if listener.link.startswith("magnet:"):
                try:
                    hash_ = _get_hash_magnet(listener.link)
                    tor_info_list = await TorrentManager.qbittorrent.torrents.info(
                        hashes=[hash_]
                    )
                    if tor_info_list:
                        task = await get_task_by_gid(hash_[:12])
                        if not task:
                            LOGGER.info(
                                "Removing Duplicated Zombie Torrent: "
                                f"{tor_info_list[0].name} - Hash: {hash_}"
                            )
                            await TorrentManager.qbittorrent.torrents.delete([hash_], True)
                            tags = torrent_tags(tor_info_list[0])
                            if tags:
                                await TorrentManager.qbittorrent.torrents.delete_tags(
                                    tags=tags
                                )
                            await sleep(0.5)
                            await TorrentManager.qbittorrent.torrents.add(form.build())
                            zombie_found = True
                except Exception as e2:
                    LOGGER.error(f"Error checking duplicate: {e2}")

            if not zombie_found:
                LOGGER.error(
                    f"{e}. {listener.mid}. Already added torrent or unsupported link/file type!"
                )
                await listener.on_download_error(
                    "Torrent already added by a user. Please try again later!"
                )
                return
        tor_info = await TorrentManager.qbittorrent.torrents.info(tag=f"{listener.mid}")
        if len(tor_info) == 0:
            from time import time as _now
            deadline = _now() + 120
            while True:
                if add_to_queue and event.is_set():
                    add_to_queue = False
                tor_info = await TorrentManager.qbittorrent.torrents.info(
                    tag=f"{listener.mid}"
                )
                if len(tor_info) > 0:
                    break
                if _now() > deadline:
                    LOGGER.error(
                        f"qBit add: torrent {listener.mid} never appeared in "
                        "info() after 120s; aborting"
                    )
                    await listener.on_download_error(
                        "qBittorrent failed to register the torrent."
                    )
                    return
                await sleep(1)
        tor_info = tor_info[0]
        listener.name = listener.name or tor_info.name
        ext_hash = tor_info.hash

        async with task_dict_lock:
            task_dict[listener.mid] = QbittorrentStatus(listener, queued=add_to_queue)
        await on_download_start(f"{listener.mid}")

        if add_to_queue:
            LOGGER.info(f"Added to Queue/Download: {tor_info.name} - Hash: {ext_hash}")
        else:
            LOGGER.info(f"QbitDownload started: {tor_info.name} - Hash: {ext_hash}")

        await listener.on_download_start()

        if Config.BASE_URL and listener.select:
            if listener.link.startswith("magnet:"):
                metamsg = "Downloading Metadata, wait then you can select files. Use torrent file to avoid this wait."
                meta = await send_message(listener.message, metamsg)
                while True:
                    tor_info = await TorrentManager.qbittorrent.torrents.info(
                        tag=f"{listener.mid}"
                    )
                    if len(tor_info) == 0:
                        await delete_message(meta)
                        return
                    try:
                        tor_info = tor_info[0]
                        if not (
                            is_metadata_state(tor_info.state)
                            or tor_info.state in PAUSED_STATES
                        ):
                            await delete_message(meta)
                            break
                    except Exception:
                        await delete_message(meta)
                        return

            ext_hash = tor_info.hash
            if not add_to_queue:
                await TorrentManager.qbittorrent.torrents.stop([ext_hash])
            SBUTTONS = bt_selection_buttons(ext_hash)
            msg = "Your download paused. Choose files then press Done Selecting button to start downloading."
            await send_message(listener.message, msg, SBUTTONS)
        elif listener.multi <= 1:
            await send_status_message(listener.message)

        if event is not None:
            if not event.is_set():
                await event.wait()
                if listener.is_cancelled:
                    return
                async with task_dict_lock:
                    task = task_dict.get(listener.mid)
                    if task is None:
                        LOGGER.info(
                            f"qBittorrent queue wake: task {listener.mid} no longer in task_dict; skipping start."
                        )
                        return
                    task.queued = False
                LOGGER.info(
                    f"Start Queued Download from Qbittorrent: {tor_info.name} - Hash: {ext_hash}"
                )
            await on_download_start(f"{listener.mid}")
            await TorrentManager.qbittorrent.torrents.start([ext_hash])
    except (ClientError, TimeoutError, Exception, AQError) as e:
        if f"{listener.mid}" in qb_torrents:
            del qb_torrents[f"{listener.mid}"]
        await listener.on_download_error(f"{e}")
    finally:
        if downloaded_torrent and await aiopath.exists(downloaded_torrent):
            await remove(downloaded_torrent)
