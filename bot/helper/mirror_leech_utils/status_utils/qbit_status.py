# This file is a part of NEO-WZML (github.com/irisXDR/NEO-WZML)

from asyncio import sleep, gather

from bot import LOGGER, qb_torrents, qb_listener_lock
from bot.core.torrent_manager import TorrentManager
from bot.helper.ext_utils.status_utils import (
    MirrorStatus,
    EngineStatus,
    get_readable_file_size,
    get_readable_time,
)
from bot.helper.mirror_leech_utils.qbit_compat import (
    CHECKING_STATES,
    PAUSED_STATES,
    QUEUE_DOWNLOAD_STATES,
    QUEUE_UPLOAD_STATES,
    SEEDING_STATES,
    first_torrent_tag,
    is_metadata_state,
    seconds_value,
)


async def get_download(tag, old_info=None):
    try:
        res = (await TorrentManager.qbittorrent.torrents.info(tag=tag))[0]
        return res or old_info
    except Exception as e:
        LOGGER.error(f"{e}: Qbittorrent, while getting torrent info. Tag: {tag}")
        return old_info


class QbittorrentStatus:
    def __init__(self, listener, seeding=False, queued=False):
        self.queued = queued
        self.seeding = seeding
        self.listener = listener
        self._info = None
        self.engine = EngineStatus().STATUS_QBIT

    async def update(self):
        self._info = await get_download(f"{self.listener.mid}", self._info)

    def progress(self):
        if not self._info:
            return "0%"
        return f"{round(self._info.progress * 100, 2)}%"

    def processed_bytes(self):
        if not self._info:
            return "0 B"
        return get_readable_file_size(self._info.downloaded)

    def speed(self):
        if not self._info:
            return "0 B/s"
        return f"{get_readable_file_size(self._info.dlspeed)}/s"

    def name(self):
        if not self._info:
            return self.listener.name
        if is_metadata_state(self._info.state):
            return f"[METADATA]{self.listener.name}"
        else:
            return self.listener.name

    def size(self):
        if not self._info:
            return "0 B"
        return get_readable_file_size(self._info.size)

    def eta(self):
        if not self._info:
            return "-"
        return get_readable_time(seconds_value(self._info.eta))

    async def status(self):
        await self.update()
        if not self._info:
            return MirrorStatus.STATUS_DOWNLOAD
        state = self._info.state
        if state in QUEUE_DOWNLOAD_STATES or self.queued:
            return MirrorStatus.STATUS_QUEUEDL
        elif state in QUEUE_UPLOAD_STATES:
            return MirrorStatus.STATUS_QUEUEUP
        elif state in PAUSED_STATES:
            return MirrorStatus.STATUS_PAUSED
        elif state in CHECKING_STATES:
            return MirrorStatus.STATUS_CHECK
        elif state in SEEDING_STATES and self.seeding:
            return MirrorStatus.STATUS_SEED
        else:
            return MirrorStatus.STATUS_DOWNLOAD

    def seeders_num(self):
        if not self._info:
            return 0
        return self._info.num_seeds

    def leechers_num(self):
        if not self._info:
            return 0
        return self._info.num_leechs

    def uploaded_bytes(self):
        if not self._info:
            return "0 B"
        return get_readable_file_size(self._info.uploaded)

    def seed_speed(self):
        if not self._info:
            return "0 B/s"
        return f"{get_readable_file_size(self._info.upspeed)}/s"

    def ratio(self):
        if not self._info:
            return "0"
        return f"{round(self._info.ratio, 3)}"

    def seeding_time(self):
        if not self._info:
            return "-"
        return get_readable_time(seconds_value(self._info.seeding_time))

    def task(self):
        return self

    def gid(self):
        h = self.hash()
        return h[:12] if h else None

    def hash(self):
        return self._info.hash if self._info else None

    async def cancel_task(self):
        self.listener.is_cancelled = True
        await self.update()
        if not self._info:
            LOGGER.warning("Cannot cancel qBittorrent task: torrent info is None")
            await self.listener.on_download_error("Task already cancelled or removed!")
            return
        await TorrentManager.qbittorrent.torrents.stop([self._info.hash])
        if not self.seeding:
            if self.queued:
                LOGGER.info(f"Cancelling QueueDL: {self.name()}")
                msg = "task have been removed from queue/download"
            else:
                LOGGER.info(f"Cancelling Download: {self._info.name}")
                msg = "Stopped by user!"
            tag = first_torrent_tag(self._info)
            cleanup = [
                self.listener.on_download_error(msg),
                TorrentManager.qbittorrent.torrents.delete([self._info.hash], True),
            ]
            if tag:
                cleanup.append(
                    TorrentManager.qbittorrent.torrents.delete_tags(tags=[tag])
                )
            await sleep(0.3)
            await gather(*cleanup)
            async with qb_listener_lock:
                if tag and tag in qb_torrents:
                    del qb_torrents[tag]
