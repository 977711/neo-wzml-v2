# This file is a part of NEO-WZML (github.com/irisXDR/NEO-WZML)

from asyncio import Event
from time import time


class TeraboxDownloadTracker:
    def __init__(self, listener):
        self.listener = listener
        self.cancel_event = Event()
        self.is_cancelled = False

        self._completed_bytes = 0
        self._current_bytes = 0
        self._speed = 0.0
        self._ema = 0.0
        self._last_total = 0
        self._last_time = time()

        self._cleanup_selection = None

    def start_file(self) -> None:
        self._current_bytes = 0

    def finish_file(self, size: int) -> None:
        self._completed_bytes += max(0, int(size or 0))
        self._current_bytes = 0

    def on_progress(self, written: int, total: int) -> None:
        self._current_bytes = max(0, int(written or 0))
        now = time()
        dt = now - self._last_time
        if dt >= 1.0:
            cur_total = self._completed_bytes + self._current_bytes
            inst = (cur_total - self._last_total) / dt if dt > 0 else 0.0
            self._ema = (0.3 * inst + 0.7 * self._ema) if self._ema else inst
            self._speed = max(0.0, self._ema)
            self._last_total = cur_total
            self._last_time = now

    @property
    def downloaded_bytes(self) -> int:
        return self._completed_bytes + self._current_bytes

    @property
    def speed(self) -> float:
        if self._last_time and (time() - self._last_time) > 8:
            return 0.0
        return self._speed

    async def cancel_task(self) -> None:
        if self.is_cancelled:
            return
        self.is_cancelled = True
        self.cancel_event.set()

        cleanup = self._cleanup_selection
        self._cleanup_selection = None
        if cleanup:
            try:
                await cleanup()
            except Exception:
                pass

        await self.listener.on_download_error("Download stopped by user!")
