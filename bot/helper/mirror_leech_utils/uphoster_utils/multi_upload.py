# This file is a part of NEO-WZML (github.com/irisXDR/NEO-WZML)

from asyncio import gather
from logging import getLogger
from os import path as ospath, walk as oswalk

from aiofiles.os import listdir, makedirs, path as aiopath, remove as aioremove, rmdir
from bot.helper.ext_utils.bot_utils import sync_to_async
from bot.helper.ext_utils.files_utils import check_strict_file_mode

from bot.helper.mirror_leech_utils.uphoster_utils.gofile_utils.upload import (
    GoFileUpload,
)
from bot.helper.mirror_leech_utils.uphoster_utils.buzzheavier_utils.upload import (
    BuzzHeavierUpload,
)
from bot.helper.mirror_leech_utils.uphoster_utils.pixeldrain_utils.upload import (
    PixelDrainUpload,
)

LOGGER = getLogger(__name__)


async def filter_for_strict_file_mode(path):
    from bot.core.config_manager import Config

    if not Config.STRICT_FILE_MODE:
        return path, 0, 0

    if await aiopath.isfile(path):
        is_allowed, reason = await check_strict_file_mode(path, ospath.basename(path))
        if not is_allowed:
            LOGGER.info(f"STRICT_FILE_MODE: Skipping {reason}: {path}")
            await aioremove(path)
            return None, 0, 1
        return path, 1, 0

    valid_count = 0
    filtered_count = 0
    for dirpath, _, files in await sync_to_async(oswalk, path):
        for file_ in files:
            f_path = ospath.join(dirpath, file_)
            is_allowed, reason = await check_strict_file_mode(f_path, file_)
            if not is_allowed:
                LOGGER.info(f"STRICT_FILE_MODE: Skipping {reason}: {f_path}")
                await aioremove(f_path)
                filtered_count += 1
            else:
                valid_count += 1

    for dirpath, _, files in await sync_to_async(oswalk, path, topdown=False):
        if not await listdir(dirpath):
            await rmdir(dirpath)

    if valid_count == 0:
        return None, 0, filtered_count

    return path, valid_count, filtered_count


class MultiUphosterUpload:
    def __init__(self, listener, path, services):
        self.listener = listener
        self.path = path
        self.services = services
        self.uploaders = []
        self._processed_bytes = 0
        self._speed = 0
        self.is_cancelled = False
        self.results = {}
        self.failed = []

        for service in services:
            if service == "gofile":
                self.uploaders.append(GoFileUpload(ProxyListener(self, "gofile"), path))
            elif service == "buzzheavier":
                self.uploaders.append(
                    BuzzHeavierUpload(ProxyListener(self, "buzzheavier"), path)
                )
            elif service == "pixeldrain":
                self.uploaders.append(
                    PixelDrainUpload(ProxyListener(self, "pixeldrain"), path)
                )

    @property
    def speed(self):
        return sum(u.speed for u in self.uploaders)

    @property
    def processed_bytes(self):
        if not self.uploaders:
            return 0
        return sum(u.processed_bytes for u in self.uploaders) / len(self.uploaders)

    async def upload(self):
        filtered_path, valid_count, filtered_count = await filter_for_strict_file_mode(self.path)
        if filtered_path is None:
            await self.listener.on_upload_error(
                f"Task failed! STRICT_FILE_MODE is enabled but no files meet the criteria (videos ≥100MB). {filtered_count} file(s) filtered."
            )
            return

        if filtered_path != self.path:
            self.path = filtered_path
            self.uploaders = []
            for service in self.services:
                if service == "gofile":
                    self.uploaders.append(GoFileUpload(ProxyListener(self, "gofile"), self.path))
                elif service == "buzzheavier":
                    self.uploaders.append(
                        BuzzHeavierUpload(ProxyListener(self, "buzzheavier"), self.path)
                    )
                elif service == "pixeldrain":
                    self.uploaders.append(
                        PixelDrainUpload(ProxyListener(self, "pixeldrain"), self.path)
                    )

        tasks = [u.upload() for u in self.uploaders]
        results = await gather(*tasks, return_exceptions=True)
        for service, u, res in zip(self.services, self.uploaders, results):
            if not isinstance(res, BaseException):
                continue
            LOGGER.error(
                "Multi-upload: %s raised %s: %s",
                service,
                res.__class__.__name__,
                res,
                exc_info=res,
            )
            try:
                await self.on_upload_error(
                    service, f"{res.__class__.__name__}: {res}"
                )
            except Exception:
                LOGGER.exception(
                    "Multi-upload: failed to register error for %s", service
                )

    async def cancel_task(self):
        self.is_cancelled = True
        tasks = [u.cancel_task() for u in self.uploaders]
        await gather(*tasks)

    async def on_upload_complete(
        self, service, link, files, folders, mime_type, dir_id
    ):
        self.results[service] = {
            "link": link,
            "files": files,
            "folders": folders,
            "mime_type": mime_type,
            "dir_id": dir_id,
        }
        await self._check_completion()

    async def on_upload_error(self, service, error):
        LOGGER.error(f"Upload failed for {service}: {error}")
        self.failed.append(service)
        self.results[service] = {"error": error}
        await self._check_completion()

    async def _check_completion(self):
        if len(self.results) == len(self.uploaders):
            if len(self.failed) == len(self.uploaders):
                error_details = []
                for service, result in self.results.items():
                    if "error" in result:
                        error_details.append(f"{service}: {result['error']}")

                if len(error_details) == 1:
                    error_msg = error_details[0]
                else:
                    error_msg = "All uploads failed:\n" + "\n".join(error_details)
                await self.listener.on_upload_error(error_msg)
            else:
                successful = [
                    (svc, v) for svc, v in self.results.items() if "error" not in v
                ]
                primary_svc, primary = successful[0]
                merged_files = {}
                folders_count = 0
                for _, ok in successful:
                    if isinstance(ok.get("files"), dict):
                        merged_files.update(ok["files"])
                    if isinstance(ok.get("folders"), int):
                        folders_count += ok["folders"]
                mime_type = primary.get("mime_type")
                dir_id = primary.get("dir_id", "")
                await self.listener.on_upload_complete(
                    self.results,
                    merged_files or primary.get("files", {}),
                    folders_count or primary.get("folders", 0),
                    mime_type,
                    dir_id,
                )


class ProxyListener:
    def __init__(self, multi_uploader, service):
        self.multi_uploader = multi_uploader
        self.service = service
        self.is_cancelled = False

    def __getattr__(self, name):
        return getattr(self.multi_uploader.listener, name)

    async def on_upload_complete(self, link, files, folders, mime_type, dir_id=""):
        await self.multi_uploader.on_upload_complete(
            self.service, link, files, folders, mime_type, dir_id
        )

    async def on_upload_error(self, error):
        await self.multi_uploader.on_upload_error(self.service, error)
