# This file is a part of NEO-WZML (github.com/irisXDR/NEO-WZML)

from logging import getLogger
from tenacity import RetryError

from bot.helper.mirror_leech_utils.gdrive_utils.helper import GoogleDriveHelper

LOGGER = getLogger(__name__)


class GoogleDriveCount(GoogleDriveHelper):
    def __init__(self):
        super().__init__()

    def count(self, link, user_id):
        try:
            file_id = self.get_id_from_url(link, user_id)
        except (KeyError, IndexError):
            return (
                "Google Drive ID could not be found in the provided link",
                None,
                None,
                None,
                None,
            )
        self.service = self.authorize()
        LOGGER.info(f"File ID: {file_id}")
        try:
            return self._proceed_count(file_id)
        except Exception as err:
            if isinstance(err, RetryError):
                LOGGER.info(f"Total Attempts: {err.last_attempt.attempt_number}")
                err = err.last_attempt.exception()
            err_str = str(err).replace(">", "").replace("<", "")

            if self.user_token_attempted and not self.global_token_attempted:
                from os import path as ospath
                if ospath.exists("token.pickle"):
                    LOGGER.info(f"User token failed, trying global token.pickle...")
                    self.use_sa = False
                    self.token_path = "token.pickle"
                    self.global_token_attempted = True
                    self.user_token_attempted = False
                    self.service = self.authorize()
                    return self._retry_count(file_id, link, user_id)

            if self.use_sa and not self.alt_auth:
                self.alt_auth = True
                self.use_sa = False
                self.token_path = "token.pickle"
                self.global_token_attempted = True
                LOGGER.info("Service account failed, trying global token.pickle...")
                self.service = self.authorize()
                return self._retry_count(file_id, link, user_id)

            if self.global_token_attempted and not self.user_token_attempted:
                if self._check_user_token_exists(user_id):
                    LOGGER.info(f"Global token failed, trying user token...")
                    self.use_sa = False
                    self.token_path = f"tokens/{user_id}.pickle"
                    self.user_token_attempted = True
                    self.global_token_attempted = False
                    self.service = self.authorize()
                    return self._retry_count(file_id, link, user_id)

            if "File not found" in err_str:
                msg = "File not found."
            else:
                msg = f"Error.\n{err_str}"
        return msg, None, None, None, None
    
    def _retry_count(self, file_id, link, user_id):
        try:
            return self._proceed_count(file_id)
        except Exception as err:
            if isinstance(err, RetryError):
                LOGGER.info(f"Total Attempts: {err.last_attempt.attempt_number}")
                err = err.last_attempt.exception()
            return self._handle_count_error(err, file_id, link, user_id)
    
    def _handle_count_error(self, err, file_id, link, user_id):
        err_str = str(err).replace(">", "").replace("<", "")

        if self.user_token_attempted and not self.global_token_attempted:
            from os import path as ospath
            if ospath.exists("token.pickle"):
                LOGGER.info(f"User token failed (retry), trying global token.pickle...")
                self.use_sa = False
                self.token_path = "token.pickle"
                self.global_token_attempted = True
                self.user_token_attempted = False
                self.service = self.authorize()
                return self._retry_count(file_id, link, user_id)

        if self.use_sa and not self.alt_auth:
            self.alt_auth = True
            self.use_sa = False
            self.token_path = "token.pickle"
            self.global_token_attempted = True
            LOGGER.info("Service account failed (retry), trying global token.pickle...")
            self.service = self.authorize()
            return self._retry_count(file_id, link, user_id)

        if self.global_token_attempted and not self.user_token_attempted:
            if self._check_user_token_exists(user_id):
                LOGGER.info(f"Global token failed (retry), trying user token...")
                self.use_sa = False
                self.token_path = f"tokens/{user_id}.pickle"
                self.user_token_attempted = True
                self.global_token_attempted = False
                self.service = self.authorize()
                return self._retry_count(file_id, link, user_id)

        if "File not found" in err_str:
            msg = "File not found."
        else:
            msg = f"Error.\n{err_str}"
        return msg, None, None, None, None

    def _proceed_count(self, file_id):
        meta = self.get_file_metadata(file_id)
        name = meta["name"]
        LOGGER.info(f"Counting: {name}")
        mime_type = meta.get("mimeType")
        if mime_type == self.G_DRIVE_DIR_MIME_TYPE:
            self._gdrive_directory(meta)
            mime_type = "Folder"
        else:
            if mime_type is None:
                mime_type = "File"
            self.total_files += 1
            self._gdrive_file(meta)
        return name, mime_type, self.proc_bytes, self.total_files, self.total_folders

    def _gdrive_file(self, filee):
        size = int(filee.get("size", 0))
        self.proc_bytes += size

    def _gdrive_directory(self, drive_folder):
        files = self.get_files_by_folder_id(drive_folder["id"])
        if len(files) == 0:
            return
        for filee in files:
            shortcut_details = filee.get("shortcutDetails")
            if shortcut_details is not None:
                mime_type = shortcut_details["targetMimeType"]
                file_id = shortcut_details["targetId"]
                filee = self.get_file_metadata(file_id)
            else:
                mime_type = filee.get("mimeType")
            if mime_type == self.G_DRIVE_DIR_MIME_TYPE:
                self.total_folders += 1
                self._gdrive_directory(filee)
            else:
                self.total_files += 1
                self._gdrive_file(filee)
