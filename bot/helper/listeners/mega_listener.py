# This file is a part of NEO-WZML (github.com/irisXDR/NEO-WZML)

from asyncio import Event, wait_for, TimeoutError as AsyncTimeoutError
from time import time
from mega import MegaApi, MegaListener, MegaRequest, MegaTransfer, MegaError

from bot import LOGGER, bot_loop, task_dict, task_dict_lock
from bot.helper.ext_utils.bot_utils import async_to_sync, sync_to_async
from bot.helper.mirror_leech_utils.status_utils.mega_status import MegaDownloadStatus


# Cap for MegaRequest callbacks so a dropped SDK callback cannot hang the bot forever.
_REQUEST_TIMEOUT_SECONDS = 300


# Raw SDK error codes -> human-readable reasons.
MEGA_FRIENDLY_ERRORS = {
    "-16": "File(s) Banned",
    "-9": "File(s) not found or deleted",
    "-11": "File(s) Access Denied",
    "-15": "Decryption key error",
    "-13": "Incomplete transfer",
    "-6": "Too many requests",
}


def friendly_mega_error(raw_error):
    """SDK error to a friendly message."""
    if not raw_error:
        return raw_error
    stripped = str(raw_error).lstrip()
    for code, friendly in MEGA_FRIENDLY_ERRORS.items():
        if stripped.startswith(code):
            return friendly
    return raw_error


class AsyncMega:
    def __init__(self):
        self.api = None
        self.folder_api = None
        self.continue_event = Event()
        self._transfer_event = Event()
        self._expected_request_type = None
        self._expected_request_source = None
        self._download_is_folder = False

    def _request_type_for_name(self, name):
        request_types = {
            "login": getattr(MegaRequest, "TYPE_LOGIN", None),
            "loginToFolder": getattr(MegaRequest, "TYPE_LOGIN", None),
            "fetchNodes": getattr(MegaRequest, "TYPE_FETCH_NODES", None),
            "getPublicNode": getattr(MegaRequest, "TYPE_GET_PUBLIC_NODE", None),
            "logout": getattr(MegaRequest, "TYPE_LOGOUT", None),
        }
        return request_types.get(name)

    def _request_type_for(self, function):
        return self._request_type_for_name(getattr(function, "__name__", ""))

    async def run(self, function, *args, expected_type=None, expected_source="main", **kwargs):
        self.continue_event.clear()
        self._expected_request_type = (
            self._request_type_for(function) if expected_type is None else expected_type
        )
        self._expected_request_source = expected_source
        try:
            await sync_to_async(function, *args, **kwargs)
            try:
                await wait_for(self.continue_event.wait(), timeout=_REQUEST_TIMEOUT_SECONDS)
            except AsyncTimeoutError:
                listener = getattr(self, "_mega_listener", None)
                msg = (
                    f"Mega SDK timed out after {_REQUEST_TIMEOUT_SECONDS}s "
                    f"waiting for {getattr(function, '__name__', 'request')} "
                    f"({expected_source})"
                )
                LOGGER.error(msg)
                if listener is not None and not listener.error:
                    listener.error = msg
                self._transfer_event.set()
        finally:
            self._expected_request_type = None
            self._expected_request_source = None

    async def wait_for_transfer(self):
        await self._transfer_event.wait()

    async def logout(self):
        await self.run(
            self.api.logout,
            expected_type=self._request_type_for_name("logout"),
            expected_source="main",
        )
        if self.folder_api:
            await self.run(
                self.folder_api.logout,
                expected_type=self._request_type_for_name("logout"),
                expected_source="folder",
            )

    async def fetchNodes(self, api=None, source="main"):
        api = api or self.api
        return await self.run(
            api.fetchNodes,
            expected_type=self._request_type_for_name("fetchNodes"),
            expected_source=source,
        )

    async def loginToFolder(self, *args, **kwargs):
        return await self.run(
            self.folder_api.loginToFolder,
            *args,
            expected_type=self._request_type_for_name("loginToFolder"),
            expected_source="folder",
            **kwargs,
        )

    def _download_api(self):
        return self.folder_api if self.folder_api else self.api

    async def startDownload(self, node, localPath, name, listener, startFirst, cancelToken, collisionCheck, collisionResolution, undelete):
        dl_api = self._download_api()
        self.continue_event.clear()
        self._transfer_event.clear()
        self._expected_request_type = None
        self._expected_request_source = None
        try:
            self._download_is_folder = bool(node.isFolder())
        except Exception:
            self._download_is_folder = node.getType() == 1
        if hasattr(self, "_mega_listener"):
            self._mega_listener._name = name
            try:
                self._mega_listener._target_handle = node.getHandle()
            except Exception:
                self._mega_listener._target_handle = None
        await sync_to_async(
            dl_api.startDownload,
            node, localPath, name, listener, startFirst, cancelToken,
            collisionCheck, collisionResolution, undelete,
        )
        try:
            await wait_for(self.continue_event.wait(), timeout=_REQUEST_TIMEOUT_SECONDS)
        except AsyncTimeoutError:
            pass

    def __getattr__(self, name):
        attr = getattr(self.api, name)
        if callable(attr):

            async def wrapper(*args, **kwargs):
                return await self.run(
                    attr,
                    *args,
                    expected_type=self._request_type_for_name(name),
                    **kwargs,
                )

            return wrapper
        return attr


class MegaAppListener(MegaListener):
    def __init__(self, async_api: AsyncMega, listener):
        self._async_api = async_api
        self.continue_event = async_api.continue_event
        self._transfer_event = async_api._transfer_event
        self.node = None
        self.public_node = None
        self.listener = listener
        self.is_cancelled = False
        self.error = None
        self.retryable_error = None
        self._bytes_transferred = 0
        self._total_downloaded_bytes = 0
        self._speed = 0
        self._smoothed_speed = 0
        self._last_speed_time = 0
        self._name = ""
        self._target_handle = None
        self._files_remaining = 0
        self._selection_gid = None
        self._cleanup_selection = None
        self._cancel_token = None
        self._caller_manages_completion = False
        self._parallel_mode = False
        self._file_states = {}
        self._sdk_folder_download = False
        self._sub_transfers = {}
        self._sdk_folder_downloaded_bytes = 0
        self._sdk_folder_failed_files = []  # [(name, error), ...]
        self._sdk_folder_total_files = 0
        super().__init__()

    def register_parallel_transfer(self, node_handle, cancel_token=None):
        """Register a file for parallel tracking. Returns an Event to await."""
        evt = Event()
        self._file_states[node_handle] = {
            'event': evt, 'bytes': 0, 'speed': 0,
            'error': None, 'retryable_error': None,
            'cancel_token': cancel_token,
        }
        return evt

    def unregister_parallel_transfer(self, node_handle, accumulate=True):
        """Remove handle from tracking. Accumulate bytes only on success."""
        state = self._file_states.pop(node_handle, None)
        if state and accumulate:
            self._total_downloaded_bytes += state['bytes']

    def _signal_parallel_event(self, node_handle):
        state = self._file_states.get(node_handle)
        if state:
            try:
                bot_loop.call_soon_threadsafe(state['event'].set)
            except Exception:
                pass

    def _signal_all_parallel_events(self):
        for state in self._file_states.values():
            try:
                bot_loop.call_soon_threadsafe(state['event'].set)
            except Exception:
                pass

    @property
    def speed(self):
        if self._last_speed_time and (time() - self._last_speed_time) > 2:
            return 0
        return self._smoothed_speed

    @property
    def downloaded_bytes(self):
        if self._sdk_folder_download:
            active = sum(s['bytes'] for s in self._sub_transfers.values())
            return self._sdk_folder_downloaded_bytes + active
        if self._parallel_mode:
            active = sum(s['bytes'] for s in self._file_states.values())
            return self._total_downloaded_bytes + active
        return self._total_downloaded_bytes + self._bytes_transferred

    def _set_request_event(self):
        try:
            bot_loop.call_soon_threadsafe(self.continue_event.set)
        except Exception as e:
            LOGGER.error(f"Mega request event signal failed: {e}")

    def _set_transfer_event(self):
        try:
            bot_loop.call_soon_threadsafe(self._transfer_event.set)
        except Exception as e:
            LOGGER.error(f"Mega transfer event signal failed: {e}")

    def _is_expected_request(self, request_type):
        expected = self._async_api._expected_request_type
        return expected is None or request_type == expected

    def _is_expected_source(self, source):
        expected = self._async_api._expected_request_source
        return expected is None or source == expected

    def _is_sub_transfer(self, transfer):
        try:
            return transfer.getFolderTransferTag() != -1
        except Exception:
            return False

    def _is_target_transfer(self, transfer):
        if self._sdk_folder_download:
            try:
                return transfer.isFolderTransfer()
            except Exception:
                return False
        if self._async_api._download_is_folder:
            try:
                return transfer.isFolderTransfer()
            except Exception:
                return False
        if self._target_handle is not None:
            try:
                return transfer.getNodeHandle() == self._target_handle
            except Exception:
                pass
        try:
            return transfer.getFileName() == self._name
        except Exception:
            return False

    def onRequestStart(self, api, request):
        pass

    def onRequestUpdate(self, api, request):
        pass

    def onRequestFinish(self, api, request, error, source="main"):
        try:
            request_type = request.getType()
            err_code = error.getErrorCode() if error else MegaError.API_OK
            if err_code != MegaError.API_OK:
                if self.is_cancelled:
                    self._set_request_event()
                    self._set_transfer_event()
                    return
                if err_code in (MegaError.API_EAGAIN, MegaError.API_ERATELIMIT):
                    return
                if not (
                    self._is_expected_request(request_type)
                    and self._is_expected_source(source)
                ):
                    return
                self.error = f"{err_code} {error.toString()}"
                LOGGER.error(f"Mega onRequestFinishError: {self.error}")
                self._set_request_event()
                self._set_transfer_event()
                return

            if request_type == MegaRequest.TYPE_GET_PUBLIC_NODE:
                self.public_node = request.getPublicMegaNode()
                if self.public_node:
                    self._name = self.public_node.getName()
            elif request_type == MegaRequest.TYPE_FETCH_NODES:
                self.node = api.getRootNode()
                if self.node:
                    self._name = self.node.getName()

            if self._is_expected_request(request_type) and self._is_expected_source(source):
                self._set_request_event()
        except Exception as e:
            self.error = f"Mega request callback exception: {e}"
            LOGGER.error(self.error, exc_info=True)
            self._set_request_event()
            self._set_transfer_event()

    def onRequestTemporaryError(self, api, request, error: MegaError, source="main"):
        try:
            if self.is_cancelled:
                self._set_request_event()
                return
        except Exception as e:
            LOGGER.error(f"Mega request temporary-error callback exception: {e}", exc_info=True)

    def onTransferStart(self, api, transfer):
        try:
            if self._sdk_folder_download:
                if self._is_sub_transfer(transfer):
                    handle = transfer.getNodeHandle()
                    try:
                        name = transfer.getFileName()
                    except Exception:
                        name = "?"
                    self._sub_transfers[handle] = {
                        'bytes': 0, 'speed': 0,
                        'error': None, 'retryable_error': None,
                        'name': name,
                    }
                    return
                if self._is_target_transfer(transfer):
                    self._set_request_event()
                return
            if self._parallel_mode:
                return  # per-handle state already initialised
            if not self._is_target_transfer(transfer):
                return
            self._bytes_transferred = 0
            self._set_request_event()
        except Exception as e:
            LOGGER.error(f"Mega transfer start callback exception: {e}", exc_info=True)

    def onTransferUpdate(self, api: MegaApi, transfer: MegaTransfer):
        try:
            if self._sdk_folder_download:
                handle = transfer.getNodeHandle()
                state = self._sub_transfers.get(handle)
                if state is None:
                    return
                if self.is_cancelled:
                    token = self._cancel_token
                    if token:
                        try:
                            if not token.isCancelled():
                                token.cancel()
                        except Exception:
                            pass
                    return
                state['speed'] = transfer.getSpeed()
                state['bytes'] = transfer.getTransferredBytes()
                total_speed = sum(s['speed'] for s in self._sub_transfers.values())
                alpha = 0.3
                self._smoothed_speed = alpha * total_speed + (1 - alpha) * self._smoothed_speed
                self._last_speed_time = time()
                return
            if self._parallel_mode:
                handle = transfer.getNodeHandle()
                state = self._file_states.get(handle)
                if state is None:
                    return
                if self.is_cancelled:
                    token = state.get('cancel_token')
                    if token:
                        try:
                            if not token.isCancelled():
                                token.cancel()
                        except Exception:
                            pass
                    return
                state['speed'] = transfer.getSpeed()
                state['bytes'] = transfer.getTransferredBytes()
                total_speed = sum(s['speed'] for s in self._file_states.values())
                alpha = 0.3
                self._smoothed_speed = alpha * total_speed + (1 - alpha) * self._smoothed_speed
                self._last_speed_time = time()
                return
            if not self._is_target_transfer(transfer):
                return
            if self.is_cancelled:
                token = self._cancel_token
                if token is not None:
                    try:
                        if not token.isCancelled():
                            token.cancel()
                    except Exception as cancel_err:
                        LOGGER.error(
                            f"Mega cancel-token signal failed, falling back: {cancel_err}"
                        )
                        token = None
                if token is None and not self._async_api._download_is_folder:
                    try:
                        api.cancelTransfer(transfer, None)
                    except Exception as cancel_err:
                        LOGGER.error(
                            f"Mega cancelTransfer failed: {cancel_err}"
                        )
                return
            self._speed = transfer.getSpeed()
            alpha = 0.3
            self._smoothed_speed = alpha * self._speed + (1 - alpha) * self._smoothed_speed
            self._last_speed_time = time()
            self._bytes_transferred = transfer.getTransferredBytes()
            self._set_request_event()
        except Exception as e:
            LOGGER.error(f"Mega transfer update callback exception: {e}", exc_info=True)

    def onTransferFinish(self, api: MegaApi, transfer: MegaTransfer, error):
        try:
            err_code = error.getErrorCode() if error else MegaError.API_OK
            if self._sdk_folder_download:
                if self._is_sub_transfer(transfer):
                    handle = transfer.getNodeHandle()
                    state = self._sub_transfers.pop(handle, None)
                    if state is None:
                        return
                    if self.is_cancelled:
                        return
                    if err_code == MegaError.API_OK:
                        self._sdk_folder_downloaded_bytes += state['bytes']
                    else:
                        state['error'] = f"{err_code} {error.toString()}"
                        if err_code == MegaError.API_EINCOMPLETE:
                            state['retryable_error'] = state['error']
                        self._sdk_folder_failed_files.append(
                            (state['name'], state['error'])
                        )
                    return
                if self._is_target_transfer(transfer):
                    self._sdk_folder_download = False
                    if self.is_cancelled:
                        self._set_transfer_event()
                        return
                    if err_code != MegaError.API_OK:
                        if not self._sdk_folder_failed_files:
                            self.error = f"{err_code} {error.toString()}"
                        self._set_transfer_event()
                        return
                    if not self._caller_manages_completion:
                        async_to_sync(self.listener.on_download_complete)
                    self._set_transfer_event()
                return
            if self._parallel_mode:
                handle = transfer.getNodeHandle()
                state = self._file_states.get(handle)
                if state is None:
                    return
                if self.is_cancelled:
                    self._signal_all_parallel_events()
                    return
                if err_code != MegaError.API_OK:
                    state['error'] = f"{err_code} {error.toString()}"
                    if err_code == MegaError.API_EINCOMPLETE:
                        state['retryable_error'] = state['error']
                self._signal_parallel_event(handle)
                return
            if self.is_cancelled:
                self._set_transfer_event()
                return
            is_target_transfer = self._is_target_transfer(transfer)
            if err_code != MegaError.API_OK:
                if not is_target_transfer:
                    return
                self.error = f"{err_code} {error.toString()}"
                try:
                    fname = transfer.getFileName()
                except Exception:
                    fname = "?"
                if err_code == MegaError.API_EINCOMPLETE and not self.listener.is_cancelled:
                    LOGGER.warning(
                        f"Mega transfer incomplete: {self.error} (file={fname})"
                    )
                    self.retryable_error = self.error
                    self._set_transfer_event()
                    return
                if self._caller_manages_completion:
                    self._set_transfer_event()
                    return
                LOGGER.error(
                    f"Mega onTransferFinishError: {self.error} "
                    f"(file={fname}, handle={getattr(transfer, 'getNodeHandle', lambda: '?')()})"
                )
                if not self.is_cancelled:
                    self.is_cancelled = True
                    async_to_sync(
                        self.listener.on_download_error,
                        friendly_mega_error(self.error),
                    )
                self._set_transfer_event()
                return
            if transfer.isFinished() and is_target_transfer:
                if self._files_remaining > 0:
                    self._total_downloaded_bytes += self._bytes_transferred
                    self._bytes_transferred = 0
                    self._files_remaining -= 1
                    self._set_transfer_event()
                    if self._files_remaining > 0:
                        return
                else:
                    self._set_transfer_event()
                if not self._caller_manages_completion:
                    async_to_sync(self.listener.on_download_complete)
        except Exception as e:
            LOGGER.error(f"onTransferFinish exception: {e}")
            if self._parallel_mode:
                self._signal_all_parallel_events()
            else:
                self._set_transfer_event()

    def onTransferTemporaryError(self, api, transfer, error):
        try:
            if self.is_cancelled:
                return
            err_code = error.getErrorCode() if error else 0
            err_str = error.toString() if error else "unknown"
            try:
                filen = transfer.getFileName()
            except Exception:
                filen = "?"

            if err_code == MegaError.API_EOVERQUOTA:
                msg = f"TransferTempError: Over quota ({filen}): {err_str}"
                self.error = msg
                if not self.is_cancelled:
                    self.is_cancelled = True
                    async_to_sync(
                        self.listener.on_download_error,
                        friendly_mega_error(msg),
                    )
                if self._sdk_folder_download:
                    self._set_transfer_event()
                elif self._parallel_mode:
                    self._signal_all_parallel_events()
                else:
                    self._set_transfer_event()
                return

            if self._sdk_folder_download or self._parallel_mode or self._caller_manages_completion:
                return

            LOGGER.warning(
                f"Mega transient transfer error (will retry): "
                f"{err_code} {err_str} ({filen})"
            )
        except Exception as e:
            LOGGER.error(
                f"Mega transfer temporary-error callback exception: {e}",
                exc_info=True,
            )

    async def cancel_task(self):
        if self.is_cancelled:
            return
        self.is_cancelled = True
        if self._sdk_folder_download:
            token = self._cancel_token
            if token:
                try:
                    if not token.isCancelled():
                        token.cancel()
                except Exception:
                    pass
        elif self._parallel_mode:
            for state in self._file_states.values():
                token = state.get('cancel_token')
                if token:
                    try:
                        if not token.isCancelled():
                            token.cancel()
                    except Exception:
                        pass
            self._signal_all_parallel_events()
        else:
            token = self._cancel_token
            if token is not None:
                try:
                    if not token.isCancelled():
                        token.cancel()
                except Exception as e:
                    LOGGER.error(f"Mega cancel-token cancel failed: {e}")
        self._set_request_event()
        self._set_transfer_event()

        cleanup = self._cleanup_selection
        self._cleanup_selection = None
        if cleanup:
            try:
                await cleanup()
            except Exception as e:
                LOGGER.error(f"Mega selection cleanup failed: {e}", exc_info=True)

        await self.listener.on_download_error("Download Canceled by user")

    def onUsersUpdate(self, api, users):
        pass

    def onUserAlertsUpdate(self, api, alerts):
        pass

    def onNodesUpdate(self, api, nodes):
        pass

    def onAccountUpdate(self, api):
        pass

    def onSetsUpdate(self, api, sets):
        pass

    def onSetElementsUpdate(self, api, elements):
        pass

    def onContactRequestsUpdate(self, api, requests):
        pass

    def onReloadNeeded(self, api):
        pass

    def onSyncFileStateChanged(self, *args):
        pass

    def onSyncAdded(self, *args):
        pass

    def onSyncDeleted(self, *args):
        pass

    def onSyncStateChanged(self, *args):
        pass

    def onSyncStatsUpdated(self, *args):
        pass

    def onGlobalSyncStateChanged(self, api):
        pass

    def onSyncRemoteRootChanged(self, *args):
        pass

    def onBackupStateChanged(self, *args):
        pass

    def onBackupStart(self, *args):
        pass

    def onBackupFinish(self, *args):
        pass

    def onBackupUpdate(self, *args):
        pass

    def onBackupTemporaryError(self, *args):
        pass

    def onChatsUpdate(self, api, chats):
        pass

    def onEvent(self, api, event):
        pass

    def onMountAdded(self, *args):
        pass

    def onMountChanged(self, *args):
        pass

    def onMountDisabled(self, *args):
        pass

    def onMountEnabled(self, *args):
        pass

    def onMountRemoved(self, *args):
        pass


class MegaFolderListener(MegaListener):

    def __init__(self, main_listener: MegaAppListener):
        self._main = main_listener
        super().__init__()

    def onRequestStart(self, api, request):
        pass

    def onRequestFinish(self, api, request, error):
        self._main.onRequestFinish(api, request, error, source="folder")

    def onRequestUpdate(self, api, request):
        pass

    def onRequestTemporaryError(self, api, request, error):
        self._main.onRequestTemporaryError(api, request, error, source="folder")

    def onTransferStart(self, api, transfer):
        self._main.onTransferStart(api, transfer)

    def onTransferUpdate(self, api, transfer):
        self._main.onTransferUpdate(api, transfer)

    def onTransferFinish(self, api, transfer, error):
        self._main.onTransferFinish(api, transfer, error)

    def onTransferTemporaryError(self, api, transfer, error):
        self._main.onTransferTemporaryError(api, transfer, error)

    def onUsersUpdate(self, api, users):
        pass

    def onUserAlertsUpdate(self, api, alerts):
        pass

    def onNodesUpdate(self, api, nodes):
        pass

    def onAccountUpdate(self, api):
        pass

    def onSetsUpdate(self, api, sets):
        pass

    def onSetElementsUpdate(self, api, elements):
        pass

    def onContactRequestsUpdate(self, api, requests):
        pass

    def onReloadNeeded(self, api):
        pass

    def onSyncFileStateChanged(self, *args):
        pass

    def onSyncAdded(self, *args):
        pass

    def onSyncDeleted(self, *args):
        pass

    def onSyncStateChanged(self, *args):
        pass

    def onSyncStatsUpdated(self, *args):
        pass

    def onGlobalSyncStateChanged(self, api):
        pass

    def onSyncRemoteRootChanged(self, *args):
        pass

    def onBackupStateChanged(self, *args):
        pass

    def onBackupStart(self, *args):
        pass

    def onBackupFinish(self, *args):
        pass

    def onBackupUpdate(self, *args):
        pass

    def onBackupTemporaryError(self, *args):
        pass

    def onChatsUpdate(self, api, chats):
        pass

    def onEvent(self, api, event):
        pass

    def onMountAdded(self, *args):
        pass

    def onMountChanged(self, *args):
        pass

    def onMountDisabled(self, *args):
        pass

    def onMountEnabled(self, *args):
        pass

    def onMountRemoved(self, *args):
        pass
