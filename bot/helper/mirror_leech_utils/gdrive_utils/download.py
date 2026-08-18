# This file is a part of NEO-WZML (github.com/irisXDR/NEO-WZML)

from googleapiclient.errors import HttpError
from googleapiclient.http import MediaIoBaseDownload
from io import FileIO
from json import loads as json_loads, JSONDecodeError
from logging import getLogger
from os import makedirs, path as ospath
from tenacity import (
    retry,
    wait_exponential,
    stop_after_attempt,
    retry_if_exception_type,
    RetryError,
)

from bot.helper.ext_utils.bot_utils import async_to_sync
from bot.helper.ext_utils.bot_utils import SetInterval
from bot.helper.mirror_leech_utils.gdrive_utils.helper import GoogleDriveHelper

LOGGER = getLogger(__name__)


class GoogleDriveDownload(GoogleDriveHelper):
    def __init__(self, listener, path):
        self.listener = listener
        self._updater = None
        self._path = path
        super().__init__()
        self.is_downloading = True
        self.gdrive_file_id = None

    def download(self):
        if self.user_token_attempted or self.global_token_attempted:
            from bot.helper.ext_utils.links_utils import is_gdrive_id
            from re import search as re_search
            from urllib.parse import parse_qs, urlparse
            
            link = self.listener.link
            if is_gdrive_id(link):
                file_id = link
            elif "folders" in link or "file" in link:
                regex = r"https:\/\/drive\.google\.com\/(?:drive(.*?)\/folders\/|file(.*?)?\/d\/)([-\w]+)"
                res = re_search(regex, link)
                if res is None:
                    raise IndexError("G-Drive ID not found.")
                file_id = res.group(3)
            else:
                parsed = urlparse(link)
                file_id = parse_qs(parsed.query)["id"][0]
            LOGGER.info(f"Using inherited token: {self.token_path}")
        else:
            file_id = self.get_id_from_url(self.listener.link, self.listener.user_id)

        self.gdrive_file_id = file_id
        self.service = self.authorize()
        self._updater = SetInterval(self.update_interval, self.progress)

        try:
            self._attempt_download(file_id)
        except Exception as err:
            if not self._handle_download_error(err, first_attempt=True):
                self._cleanup_and_error(err)
                return

        self._updater.cancel()
        if self.listener.is_cancelled:
            return
        async_to_sync(self.listener.on_download_complete)
        return

    def _attempt_download(self, file_id):
        meta = self.get_file_metadata(file_id)
        if meta.get("mimeType") == self.G_DRIVE_DIR_MIME_TYPE:
            self._download_folder(file_id, self._path, self.listener.name)
        else:
            makedirs(self._path, exist_ok=True)
            self._download_file(
                file_id, self._path, self.listener.name, meta.get("mimeType")
            )

    def _handle_download_error(self, err, first_attempt=True):
        if isinstance(err, RetryError):
            LOGGER.info(f"Total Attempts: {err.last_attempt.attempt_number}")
            err = err.last_attempt.exception()

        err_str = str(err).replace(">", "").replace("<", "")
        error_category = self._categorize_error(err_str)
        LOGGER.error(f"GDrive Download Failed: {error_category} - {err_str}")

        should_retry = False
        retry_reason = ""

        if self.user_token_attempted and not self.global_token_attempted:
            if self.retry_with_global_token():
                should_retry = True
                retry_reason = "User token failed, retrying with global token"
        elif self.global_token_attempted and not self.user_token_attempted:
            if self.retry_with_user_token(self.listener.user_id):
                should_retry = True
                retry_reason = "Global token failed, retrying with user token"
        elif self.use_sa and not self.alt_auth:
            self.alt_auth = True
            self.use_sa = False
            self.token_path = "token.pickle"
            self.global_token_attempted = True
            should_retry = True
            retry_reason = "Service account failed, retrying with global token"
        elif self.explicit_prefix_used and not self.global_token_attempted:
            if self.retry_with_global_token():
                should_retry = True
                retry_reason = "Explicit prefix failed, retrying with global token"

        if should_retry:
            LOGGER.info(f"Retry Triggered: {retry_reason}")
            self._updater.cancel()
            self.service = self.authorize()
            self._updater = SetInterval(self.update_interval, self.progress)

            try:
                file_id = self.gdrive_file_id
                self._attempt_download(file_id)
                self._updater.cancel()
                if self.listener.is_cancelled:
                    return True
                async_to_sync(self.listener.on_download_complete)
                return True
            except Exception as retry_err:
                LOGGER.error(f"Retry Failed: {str(retry_err)}")
                self._updater.cancel()
                return self._handle_download_error(retry_err, first_attempt=False)

        return False

    def _categorize_error(self, err_str):
        if "downloadQuotaExceeded" in err_str or "dailyLimitExceeded" in err_str:
            return "Download Quota Exceeded"
        elif "rateLimitExceeded" in err_str or "Quota exceeded" in err_str:
            return "Rate Limit Exceeded"
        elif "File not found" in err_str or "fileNotDownloadable" in err_str:
            return "File Not Found"
        elif "insufficientFilePermissions" in err_str:
            return "Insufficient Permissions"
        elif "invalid_credentials" in err_str or "authError" in err_str:
            return "Authentication Failed"
        else:
            return "API Error"

    def _cleanup_and_error(self, err):
        self._updater.cancel()
        if isinstance(err, RetryError):
            LOGGER.info(f"Total Attempts: {err.last_attempt.attempt_number}")
            err = err.last_attempt.exception()
        err_str = str(err).replace(">", "").replace("<", "")
        error_msg = self._user_friendly_error(err_str)
        async_to_sync(self.listener.on_download_error, error_msg)
        self.listener.is_cancelled = True

    def _user_friendly_error(self, err_str):
        if "downloadQuotaExceeded" in err_str or "dailyLimitExceeded" in err_str:
            return (
                "⚠️ Download Quota Exceeded\n\n"
                "This file has exceeded its daily download limit set by Google.\n\n"
                "Solutions: Wait 12-24 hours before retrying\n"
            )
        elif "rateLimitExceeded" in err_str or "Quota exceeded" in err_str:
            return (
                "⚠️ Rate Limit Exceeded\n\n"
                "Google Drive API rate limit has been reached.\n\n"
                "Solution: Wait a few minutes before retrying."
            )
        elif "File not found" in err_str:
            return "File not found or access denied. The account may not have permission."
        elif "insufficientFilePermissions" in err_str:
            return "Insufficient permissions to access this file."
        else:
            return f"GDrive Download Failed: {err_str}"

    def _download_folder(self, folder_id, path, folder_name):
        folder_name = folder_name.replace("/", "")
        if not ospath.exists(f"{path}/{folder_name}"):
            makedirs(f"{path}/{folder_name}")
        path += f"/{folder_name}"
        result = self.get_files_by_folder_id(folder_id)
        if len(result) == 0:
            return
        result = sorted(result, key=lambda k: k["name"])
        for item in result:
            file_id = item["id"]
            filename = item["name"]
            shortcut_details = item.get("shortcutDetails")
            if shortcut_details is not None:
                file_id = shortcut_details["targetId"]
                mime_type = shortcut_details["targetMimeType"]
            else:
                mime_type = item.get("mimeType")
            if mime_type == self.G_DRIVE_DIR_MIME_TYPE:
                self._download_folder(file_id, path, filename)
            elif not ospath.isfile(
                ospath.join(path, filename)
            ) and not filename.strip().lower().endswith(
                tuple(self.listener.excluded_extensions)
            ):
                self._download_file(file_id, path, filename, mime_type)
            if self.listener.is_cancelled:
                break

    @retry(
        wait=wait_exponential(multiplier=2, min=3, max=20),
        stop=stop_after_attempt(5),
        retry=retry_if_exception_type(Exception),
    )
    def _download_file(self, file_id, path, filename, mime_type, export=False):
        if export:
            request = self.service.files().export_media(
                fileId=file_id, mimeType="application/pdf"
            )
        else:
            request = self.service.files().get_media(
                fileId=file_id, supportsAllDrives=True, acknowledgeAbuse=True
            )
        filename = filename.replace("/", "")
        if export:
            filename = f"{filename}.pdf"
        if len(filename.encode()) > 255:
            ext = ospath.splitext(filename)[1]
            filename = f"{filename[:245]}{ext}"

            if self.listener.name.strip().endswith(ext):
                self.listener.name = filename
        if self.listener.is_cancelled:
            return
        fh = FileIO(f"{path}/{filename}", "wb")
        try:
            downloader = MediaIoBaseDownload(fh, request, chunksize=100 * 1024 * 1024)
            done = False
            retries = 0
            while not done:
                if self.listener.is_cancelled:
                    break
                try:
                    self.status, done = downloader.next_chunk()
                except HttpError as err:
                    LOGGER.error(err)
                    if err.resp.status in [500, 502, 503, 504, 429] and retries < 15:
                        retries += 1
                        if err.resp.status == 429:
                            self._rate_limit_sleep(retries, "download chunk")
                        continue
                    if err.resp.get("content-type", "").startswith("application/json"):
                        try:
                            reason = (
                                json_loads(err.content)
                                .get("error", {})
                                .get("errors", [{}])[0]
                                .get("reason", "")
                            )
                        except (JSONDecodeError, TypeError, AttributeError, IndexError):
                            reason = ""
                        if reason and "fileNotDownloadable" in reason and "document" in mime_type:
                            return self._download_file(
                                file_id, path, filename, mime_type, True
                            )
                        if reason not in [
                            "downloadQuotaExceeded",
                            "dailyLimitExceeded",
                        ]:
                            raise err
                        if self.use_sa:
                            if self.sa_count >= self.sa_number:
                                LOGGER.info(
                                    f"Reached maximum number of service accounts switching, which is {self.sa_count}"
                                )
                                raise err
                            else:
                                if self.listener.is_cancelled:
                                    return
                                self.switch_service_account()
                                LOGGER.info(f"Got: {reason}, Trying Again...")
                                return self._download_file(
                                    file_id, path, filename, mime_type
                                )
                        else:
                            LOGGER.error(f"Got: {reason}")
                            raise err
        finally:
            try:
                fh.close()
            except Exception:
                pass
        self.file_processed_bytes = 0
