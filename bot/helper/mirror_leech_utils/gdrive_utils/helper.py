# This file is a part of NEO-WZML (github.com/irisXDR/NEO-WZML)

from google.oauth2 import service_account
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError
from google_auth_httplib2 import AuthorizedHttp
from googleapiclient.http import build_http
from json import loads as json_loads, JSONDecodeError
from logging import getLogger, ERROR
from os import path as ospath, listdir
from pickle import load as pload
from random import randrange
from re import search as re_search
from time import sleep as time_sleep
from urllib.parse import parse_qs, urlparse
from tenacity import (
    retry,
    wait_exponential,
    stop_after_attempt,
    retry_if_exception_type,
)

from bot.core.config_manager import Config
from bot.helper.ext_utils.links_utils import is_gdrive_id

LOGGER = getLogger(__name__)
getLogger("googleapiclient.discovery").setLevel(ERROR)

RATE_LIMIT_WAIT_BASE = 5
RATE_LIMIT_WAIT_MAX = 30
RATE_LIMIT_MAX_RETRIES = 6


class GoogleDriveHelper:
    def __init__(self):
        self._OAUTH_SCOPE = ["https://www.googleapis.com/auth/drive"]
        self.token_path = "token.pickle"
        self.G_DRIVE_DIR_MIME_TYPE = "application/vnd.google-apps.folder"
        self.G_DRIVE_BASE_DOWNLOAD_URL = (
            "https://drive.google.com/uc?id={}&export=download"
        )
        self.G_DRIVE_DIR_BASE_DOWNLOAD_URL = "https://drive.google.com/drive/folders/{}"
        self.is_uploading = False
        self.is_downloading = False
        self.is_cloning = False
        self.sa_index = 0
        self.sa_count = 1
        self.sa_number = 100
        self.alt_auth = False
        self.service = None
        self.total_files = 0
        self.total_folders = 0
        self.file_processed_bytes = 0
        self.proc_bytes = 0
        self.total_time = 0
        self.status = None
        self.update_interval = 3
        self.use_sa = Config.USE_SERVICE_ACCOUNTS

        self.original_link = ""
        self.user_token_attempted = False
        self.global_token_attempted = False
        self.explicit_prefix_used = False

    @property
    def speed(self):
        try:
            return self.proc_bytes / self.total_time
        except Exception:
            return 0

    @property
    def processed_bytes(self):
        return self.proc_bytes

    async def progress(self):
        if self.status is not None:
            chunk_size = (
                self.status.total_size * self.status.progress()
                - self.file_processed_bytes
            )
            self.file_processed_bytes = self.status.total_size * self.status.progress()
            self.proc_bytes += chunk_size
            self.total_time += self.update_interval

    def authorize(self):
        credentials = None
        if self.use_sa:
            json_files = listdir("accounts")
            self.sa_number = len(json_files)
            self.sa_index = randrange(self.sa_number)
            LOGGER.info(f"Authorizing with {json_files[self.sa_index]} service account")
            credentials = service_account.Credentials.from_service_account_file(
                f"accounts/{json_files[self.sa_index]}", scopes=self._OAUTH_SCOPE
            )
        elif ospath.exists(self.token_path):
            LOGGER.info(f"Authorize with {self.token_path}")
            with open(self.token_path, "rb") as f:
                credentials = pload(f)
        else:
            LOGGER.error("token.pickle not found!")
        if credentials is None:
            raise RuntimeError(
                "Google Drive authorization failed: no usable credentials. "
                "Either upload `token.pickle` via /botsettings, or enable "
                "USE_SERVICE_ACCOUNTS with accounts/*.json present."
            )
        authorized_http = AuthorizedHttp(credentials, http=build_http())
        # SECURITY: TLS verify stays on unless GDRIVE_DISABLE_TLS_VERIFY=1.
        import os as _os
        if _os.environ.get("GDRIVE_DISABLE_TLS_VERIFY") == "1":
            authorized_http.http.disable_ssl_certificate_validation = True
        return build("drive", "v3", http=authorized_http, cache_discovery=False)

    def switch_service_account(self):
        if self.sa_index == self.sa_number - 1:
            self.sa_index = 0
        else:
            self.sa_index += 1
        self.sa_count += 1
        LOGGER.info(f"Switching to {self.sa_index} index")
        self.service = self.authorize()

    @staticmethod
    def _is_rate_limit_error(err):
        if isinstance(err, HttpError):
            if err.resp.status == 429:
                return True
            try:
                if err.resp.get("content-type", "").startswith("application/json"):
                    parsed = json_loads(err.content)
                    reason = (
                        parsed.get("error", {})
                        .get("errors", [{}])[0]
                        .get("reason", "")
                    )
                    if reason in ("rateLimitExceeded", "userRateLimitExceeded"):
                        return True
            except (JSONDecodeError, TypeError, AttributeError, IndexError):
                pass
        err_str = str(err).lower()
        return "ratelimitexceeded" in err_str or "rate limit exceeded" in err_str

    def _rate_limit_sleep(self, attempt, context=""):
        wait_time = min(
            RATE_LIMIT_WAIT_BASE * (2 ** (attempt - 1)), RATE_LIMIT_WAIT_MAX
        )
        LOGGER.warning(
            f"Rate limit hit{f' ({context})' if context else ''}. "
            f"Sleeping {wait_time}s (attempt {attempt}/{RATE_LIMIT_MAX_RETRIES})..."
        )
        time_sleep(wait_time)
        return wait_time

    def get_id_from_url(self, link, user_id=""):
        self.original_link = link
        self.user_token_attempted = False
        self.global_token_attempted = False
        self.explicit_prefix_used = False

        if user_id and link.startswith("mtp:"):
            self.use_sa = False
            self.token_path = f"tokens/{user_id}.pickle"
            self.explicit_prefix_used = True
            link = link.replace("mtp:", "", 1)
            LOGGER.info(f"Explicit mtp: prefix - Using user token: {self.token_path}")
        elif link.startswith("sa:"):
            self.use_sa = True
            self.explicit_prefix_used = True
            link = link.replace("sa:", "", 1)
            LOGGER.info("Explicit sa: prefix - Using service accounts")
        elif link.startswith("tp:"):
            self.use_sa = False
            self.token_path = "token.pickle"
            self.explicit_prefix_used = True
            link = link.replace("tp:", "", 1)
            LOGGER.info("Explicit tp: prefix - Using global token.pickle")
        else:
            self.explicit_prefix_used = False
            user_token_exists = self._check_user_token_exists(user_id)

            if user_token_exists:
                self.use_sa = False
                self.token_path = f"tokens/{user_id}.pickle"
                self.user_token_attempted = True
                LOGGER.info(f"No prefix - User token detected: {self.token_path}")
            else:
                self.use_sa = Config.USE_SERVICE_ACCOUNTS
                self.token_path = "token.pickle"
                if self.use_sa:
                    LOGGER.info("No prefix - No user token, using service accounts")
                else:
                    LOGGER.info("No prefix - No user token, using global token.pickle")
                    self.global_token_attempted = True

        if is_gdrive_id(link):
            return link
        if "folders" in link or "file" in link:
            regex = r"https:\/\/drive\.google\.com\/(?:drive(.*?)\/folders\/|file(.*?)?\/d\/)([-\w]+)"
            res = re_search(regex, link)
            if res is None:
                raise IndexError("G-Drive ID not found.")
            return res.group(3)
        parsed = urlparse(link)
        return parse_qs(parsed.query)["id"][0]

    def set_permission(self, file_id):
        permissions = {
            "role": "reader",
            "type": "anyone",
            "value": None,
            "withLink": True,
        }
        try:
            self.service.permissions().create(
                fileId=file_id, body=permissions, supportsAllDrives=True
            ).execute()
            return True
        except Exception as e:
            error_str = str(e)
            if "cannotModifyInheritedPermission" in error_str:
                LOGGER.warning(
                    f"Cannot set public permission on Team Drive file {file_id}. "
                    "File will be accessible to Team Drive members only."
                )
                return False
            raise

    @retry(
        wait=wait_exponential(multiplier=2, min=3, max=20),
        stop=stop_after_attempt(5),
        retry=retry_if_exception_type(Exception),
    )
    def get_file_metadata(self, file_id):
        return (
            self.service.files()
            .get(
                fileId=file_id,
                supportsAllDrives=True,
                fields="name, id, mimeType, size",
            )
            .execute()
        )

    @retry(
        wait=wait_exponential(multiplier=2, min=3, max=20),
        stop=stop_after_attempt(5),
        retry=retry_if_exception_type(Exception),
    )
    def get_files_by_folder_id(self, folder_id, item_type=""):
        page_token = None
        files = []
        if not item_type:
            q = f"'{folder_id}' in parents and trashed = false"
        elif item_type == "folders":
            q = f"'{folder_id}' in parents and mimeType = '{self.G_DRIVE_DIR_MIME_TYPE}' and trashed = false"
        else:
            q = f"'{folder_id}' in parents and mimeType != '{self.G_DRIVE_DIR_MIME_TYPE}' and trashed = false"
        while True:
            response = (
                self.service.files()
                .list(
                    supportsAllDrives=True,
                    includeItemsFromAllDrives=True,
                    q=q,
                    spaces="drive",
                    pageSize=200,
                    fields="nextPageToken, files(id, name, mimeType, size, shortcutDetails)",
                    orderBy="folder, name",
                    pageToken=page_token,
                )
                .execute()
            )
            files.extend(response.get("files", []))
            page_token = response.get("nextPageToken")
            if page_token is None:
                break
        return files

    @retry(
        wait=wait_exponential(multiplier=2, min=3, max=20),
        stop=stop_after_attempt(5),
        retry=retry_if_exception_type(Exception),
    )
    def create_directory(self, directory_name, dest_id):
        file_metadata = {
            "name": directory_name,
            "description": "Uploaded by Mirror-leech-telegram-bot",
            "mimeType": self.G_DRIVE_DIR_MIME_TYPE,
        }
        if dest_id is not None:
            file_metadata["parents"] = [dest_id]
        file = (
            self.service.files()
            .create(body=file_metadata, supportsAllDrives=True)
            .execute()
        )
        file_id = file.get("id")
        if not Config.IS_TEAM_DRIVE:
            self.set_permission(file_id)
        LOGGER.info(f"Created G-Drive Folder:\nName: {file.get('name')}\nID: {file_id}")
        return file_id

    def _check_user_token_exists(self, user_id):
        if not user_id:
            return False

        from os import path as ospath
        from bot import user_data

        user_token_path = f"tokens/{user_id}.pickle"

        if ospath.exists(user_token_path):
            LOGGER.debug(f"User token file exists - {user_token_path}")
            return True

        if user_id in user_data and user_data[user_id].get("TOKEN_PICKLE"):
            if ospath.exists(user_token_path):
                return True

        if ospath.exists(user_token_path):
            return True

        LOGGER.debug(f"No token found for user {user_id}")
        return False

    def escapes(self, estr):
        chars = ["\\", "'", '"', r"\a", r"\b", r"\f", r"\n", r"\r", r"\t"]
        for char in chars:
            estr = estr.replace(char, f"\\{char}")
        return estr.strip()

    async def cancel_task(self):
        self.listener.is_cancelled = True
        if self.is_downloading:
            LOGGER.info(f"Cancelling Download: {self.listener.name}")
            await self.listener.on_download_error("Stopped by user!")
        elif self.is_cloning:
            LOGGER.info(f"Cancelling Clone: {self.listener.name}")
            await self.listener.on_upload_error(
                "your clone has been stopped and cloned data has been deleted!"
            )
        elif self.is_uploading:
            await self.listener.on_upload_error(
                "your upload has been stopped and uploaded data has been deleted!"
            )

    def retry_with_global_token(self):
        if self.global_token_attempted:
            return False
        from os import path as ospath
        if not ospath.exists("token.pickle"):
            LOGGER.warning("Global token.pickle not found - cannot fallback")
            return False
        self.use_sa = False
        self.token_path = "token.pickle"
        self.global_token_attempted = True
        self.user_token_attempted = False
        LOGGER.info("Fallback: Retrying with global token.pickle")
        return True

    def retry_with_user_token(self, user_id):
        if self.user_token_attempted:
            return False
        if not self._check_user_token_exists(user_id):
            LOGGER.warning(f"User token not found for user {user_id} - cannot fallback")
            return False
        self.use_sa = False
        self.token_path = f"tokens/{user_id}.pickle"
        self.user_token_attempted = True
        self.global_token_attempted = False
        LOGGER.info(f"Fallback: Retrying with user token: {self.token_path}")
        return True
