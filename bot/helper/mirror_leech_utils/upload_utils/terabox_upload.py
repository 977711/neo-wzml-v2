# This file is a part of NEO-WZML (github.com/irisXDR/NEO-WZML)

import os
import posixpath
from mimetypes import guess_type
from asyncio import Event
from time import time

from aiofiles.os import path as aiopath

try:
    from terabox import TeraboxClient, TeraboxError, TeraboxCancelled
    _TERABOX_AVAILABLE = True
except ImportError:  # pragma: no cover - depends on base image build
    _TERABOX_AVAILABLE = False

_SESSION_PATH = ".terabox_upload_session.json"


def _posix_join(base: str, *parts) -> str:
    segs = [base] + list(parts)
    joined = "/".join(s.strip("/") for s in segs if s and s.strip("/"))
    return "/" + joined if joined else "/"


class TeraboxUpload:
    def __init__(self, listener, path):
        self.listener = listener
        self._path = path
        self._completed = 0          # bytes from fully finished files
        self._current = 0            # progress of the file in flight
        self.speed = 0.0
        self._ema = 0.0
        self._last_total = 0
        self._last_time = time()
        self.is_cancelled = False
        self._cancel_event = Event()
        self._client = None

    @property
    def processed_bytes(self) -> int:
        return self._completed + self._current

    def _on_progress(self, done: int, total: int) -> None:
        self._current = max(0, int(done or 0))
        now = time()
        dt = now - self._last_time
        if dt >= 1.0:
            cur = self._completed + self._current
            inst = (cur - self._last_total) / dt if dt > 0 else 0.0
            self._ema = (0.3 * inst + 0.7 * self._ema) if self._ema else inst
            self.speed = max(0.0, self._ema)
            self._last_total = cur
            self._last_time = now

    def _gather_files(self):
        base = self.listener.terabox_upload_path or "/"
        name = (self.listener.name or os.path.basename(self._path)).strip("/")
        items = []
        if os.path.isfile(self._path):
            items.append((self._path, _posix_join(base, name)))
            return items, 1, 0
        folders = 0
        for root, dirs, files in os.walk(self._path):
            folders += len(dirs)
            for fname in files:
                local = os.path.join(root, fname)
                rel = os.path.relpath(local, self._path).replace(os.sep, "/")
                items.append((local, _posix_join(base, name, rel)))
        return items, len(items), folders + 1

    async def _make_share_link(self, uploaded, base, name) -> str:
        if not uploaded:
            return ""
        try:
            if self.listener.is_file:
                fid, path = uploaded[0]
                return await self._client.create_share_link([fid], [path])
            folder_path = _posix_join(base, name)
            folder_fid = None
            try:
                for e in await self._client.region_list_dir(base or "/"):
                    if (e.get("server_filename") == name
                            and int(e.get("isdir", 0)) == 1):
                        folder_fid = e.get("fs_id")
                        break
            except Exception:
                folder_fid = None
            if folder_fid:
                return await self._client.create_share_link(
                    [folder_fid], [folder_path]
                )
            return await self._client.create_share_link(
                [f for f, _ in uploaded], [p for _, p in uploaded]
            )
        except Exception:
            return ""

    async def upload(self):
        if not _TERABOX_AVAILABLE:
            await self.listener.on_upload_error(
                "teraboxSDK is not installed in this image; cannot upload to TeraBox."
            )
            return
        cookie_file = getattr(self.listener, "terabox_cookie", "") or ""
        if not cookie_file or not await aiopath.exists(cookie_file):
            await self.listener.on_upload_error(
                "No TeraBox cookie configured for upload. Add your <b>terabox.txt</b> "
                "in User Settings (or the owner's global one) to upload to TeraBox."
            )
            return

        self._client = TeraboxClient(
            cookie_file=os.path.abspath(cookie_file), session_path=_SESSION_PATH,
        )
        try:
            try:
                await self._client.ensure_upload_ready()
            except TeraboxError as e:
                await self.listener.on_upload_error(f"TeraBox upload auth failed: {e}")
                return

            items, total_files, total_folders = self._gather_files()
            if not items:
                await self.listener.on_upload_error("Nothing to upload.")
                return

            uploaded = []
            done_files = 0
            for local, remote in items:
                if self.is_cancelled or self.listener.is_cancelled:
                    return
                try:
                    fsize = os.path.getsize(local)
                except OSError:
                    fsize = 0
                self._current = 0
                info = await self._client.upload_file(
                    local, remote,
                    progress_cb=self._on_progress,
                    cancel_event=self._cancel_event,
                )
                self._completed += fsize
                self._current = 0
                done_files += 1
                if info.get("fs_id"):
                    uploaded.append((info["fs_id"], remote))

            if self.is_cancelled or self.listener.is_cancelled:
                return

            self.listener.private_link = True
            base = self.listener.terabox_upload_path or "/"
            name = (self.listener.name or "").strip("/")
            if self.listener.is_file:
                mime_type = guess_type(self._path)[0] or "application/octet-stream"
                display = uploaded[0][1] if uploaded else _posix_join(base, name)
            else:
                mime_type = "Folder"
                display = _posix_join(base, name)

            link = await self._make_share_link(uploaded, base, name)

            await self.listener.on_upload_complete(
                link or None, total_files, total_folders, mime_type,
                rclone_path=display,
            )
        except TeraboxCancelled:
            return
        except TeraboxError as e:
            await self.listener.on_upload_error(f"TeraBox upload failed: {e}")
        except Exception as e:
            await self.listener.on_upload_error(f"TeraBox upload error: {e}")
        finally:
            if self._client is not None:
                try:
                    await self._client.aclose()
                except Exception:
                    pass

    async def cancel_task(self):
        if self.is_cancelled:
            return
        self.is_cancelled = True
        self._cancel_event.set()
        await self.listener.on_upload_error("Upload stopped by user!")
