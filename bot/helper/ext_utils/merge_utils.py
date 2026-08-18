# This file is a part of NEO-WZML (github.com/irisXDR/NEO-WZML)

from asyncio import create_task, create_subprocess_exec, sleep
from asyncio.subprocess import PIPE
from contextlib import suppress
from os import path as ospath, walk
from time import time

from aiofiles import open as aiopen
from aiofiles.os import remove, path as aiopath
from natsort import natsorted

from bot import LOGGER, DOWNLOAD_DIR
from bot.core.config_manager import BinConfig
from bot.helper.ext_utils.bot_utils import sync_to_async


VIDEO_EXTS = (".mp4", ".mkv", ".avi", ".mov", ".webm", ".ts", ".m2ts")


class MergeVideos:
    def __init__(self, listener):
        self._listener = listener
        self._processed_bytes = 0
        self._start_time = 0
        self.error = ""

    @property
    def processed_bytes(self):
        return self._processed_bytes

    async def _track_progress(self, output_path):
        self._start_time = time()
        while (
            self._listener.subproc is not None
            and self._listener.subproc.returncode is None
            and not self._listener.is_cancelled
        ):
            try:
                self._processed_bytes = ospath.getsize(output_path)
            except OSError:
                pass
            await sleep(1)
        with suppress(OSError):
            self._processed_bytes = ospath.getsize(output_path)

    def _escape_concat_path(self, f_path):
        return f_path.replace("\\", "\\\\").replace("'", "'\\''")

    async def _get_available_output_path(self, dirpath, name):
        output_path = ospath.join(dirpath, f"{name}_merged.mkv")
        if not await aiopath.exists(output_path):
            return output_path
        count = 1
        while True:
            output_path = ospath.join(dirpath, f"{name}_merged_{count}.mkv")
            if not await aiopath.exists(output_path):
                return output_path
            count += 1

    async def merge(self, dl_path, gid):
        dirpath = dl_path if await aiopath.isdir(dl_path) else ospath.dirname(dl_path)
        base_name = ospath.basename(dl_path)
        name, _ = ospath.splitext(base_name)
        output_path = await self._get_available_output_path(dirpath, name)
        files = [
            ospath.join(root, file_)
            for root, _, filenames in await sync_to_async(walk, dirpath)
            for file_ in filenames
        ]
        videos = []
        for f_path in files:
            if (
                f_path != output_path
                and f_path.lower().endswith(VIDEO_EXTS)
                and "\n" not in f_path
                and "\r" not in f_path
                and await aiopath.isfile(f_path)
            ):
                videos.append(f_path)
        videos = natsorted(videos)
        if not videos:
            LOGGER.warning(f"MergeVideos: no video files found in {dirpath}")
            return None

        concat_path = ospath.join(DOWNLOAD_DIR, f"madara_{self._listener.mid}.txt")
        try:
            async with aiopen(concat_path, "w", encoding="utf-8") as f:
                for v in videos:
                    await f.write(f"file '{self._escape_concat_path(v)}'\n")

            cmd = [
                BinConfig.FFMPEG_NAME, "-hide_banner", "-loglevel", "error",
                "-f", "concat", "-safe", "0",
                "-i", concat_path,
                "-c", "copy",
                output_path,
            ]

            self._listener.subproc = await create_subprocess_exec(
                *cmd, stdout=PIPE, stderr=PIPE
            )
            progress_task = create_task(self._track_progress(output_path))
            _, stderr = await self._listener.subproc.communicate()
            with suppress(Exception):
                await progress_task
            code = self._listener.subproc.returncode

            if code != 0:
                try:
                    stderr = stderr.decode().strip()
                except Exception:
                    stderr = ""
                self.error = f"Merge failed: {stderr or 'ffmpeg exited with error'}"
                LOGGER.error(f"MergeVideos: ffmpeg failed: {stderr}")
                return None

            for v in videos:
                try:
                    await remove(v)
                except Exception:
                    pass

            return output_path
        except Exception as e:
            self.error = f"Merge failed: {e}"
            LOGGER.error(f"MergeVideos: merge failed: {e}")
            return None
        finally:
            try:
                await remove(concat_path)
            except Exception:
                pass
