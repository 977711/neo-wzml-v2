# This file is a part of NEO-WZML (github.com/irisXDR/NEO-WZML)

from googleapiclient.errors import HttpError
from logging import getLogger

from bot.helper.ext_utils.status_utils import get_readable_file_size
from bot.helper.mirror_leech_utils.gdrive_utils.helper import (
    GoogleDriveHelper,
    RATE_LIMIT_MAX_RETRIES,
)

LOGGER = getLogger(__name__)


class GoogleDriveClean(GoogleDriveHelper):
    def __init__(self):
        super().__init__()
        self.total_files = 0
        self.total_bytes = 0

    def driveclean(self, drive_id: str, trash: bool, user_id: str = ""):
        msg = ""
        query = f"'{drive_id}' in parents and trashed = false"
        page_token = None

        try:
            self.service = self.authorize()
        except Exception as err:
            LOGGER.error(f"DriveClean: Authorization failed - {err}")
            return f"Error: Failed to authorize. {err}"

        while True:
            try:
                response = (
                    self.service.files()
                    .list(
                        q=query,
                        spaces="drive",
                        fields="nextPageToken, files(id, name, size)",
                        pageToken=page_token,
                        includeItemsFromAllDrives=True,
                        supportsAllDrives=True,
                    )
                    .execute()
                )

                files = response.get("files", [])
                for file in files:
                    self.total_files += 1
                    self.total_bytes += int(file.get("size", 0))

                    for attempt in range(1, RATE_LIMIT_MAX_RETRIES + 1):
                        try:
                            if trash:
                                self.service.files().update(
                                    fileId=file["id"], body={"trashed": True}
                                ).execute()
                            else:
                                self.service.files().delete(
                                    fileId=file["id"], supportsAllDrives=True
                                ).execute()
                            break
                        except HttpError as err:
                            if (
                                self._is_rate_limit_error(err)
                                and attempt < RATE_LIMIT_MAX_RETRIES
                            ):
                                self._rate_limit_sleep(attempt, "drive clean")
                                continue
                            raise

                page_token = response.get("nextPageToken")
                if page_token is None:
                    msg = (
                        "Successfully Moved Folder/Drive to Bin"
                        if trash
                        else "Successfully Cleaned Folder/Drive"
                    )
                    msg += f"\n\n<b>Total Files:</b> <code>{self.total_files}</code>\n<b>Total Size:</b> <code>{get_readable_file_size(self.total_bytes)}</code>"
                    break

            except HttpError as err:
                if self._is_rate_limit_error(err):
                    self._rate_limit_sleep(1, "drive clean list")
                    continue
                LOGGER.error(f"DriveClean: Error during processing - {err}")
                msg = f"Error: {str(err).replace('>', '').replace('<', '')}"
                break
            except Exception as err:
                LOGGER.error(f"DriveClean: Error during processing - {err}")
                msg = f"Error: {str(err).replace('>', '').replace('<', '')}"
                break

        return msg
