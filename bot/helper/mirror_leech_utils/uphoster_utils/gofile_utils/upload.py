# This file is a part of NEO-WZML (github.com/irisXDR/NEO-WZML)

from io import BufferedReader
from json import JSONDecodeError
from logging import getLogger
from os import path as ospath
from os import walk as oswalk
from pathlib import Path
from random import choice

from aiofiles.os import path as aiopath
from aiohttp import ClientSession
from aiohttp.client_exceptions import ContentTypeError
from tenacity import (
    RetryError,
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

from bot.core.config_manager import Config
from bot.helper.ext_utils.bot_utils import SetInterval, sync_to_async

LOGGER = getLogger(__name__)


class ProgressFileReader(BufferedReader):
    def __init__(self, filename, read_callback=None):
        super().__init__(open(filename, "rb"))
        self.__read_callback = read_callback
        self.length = Path(filename).stat().st_size

    def read(self, size=None):
        size = size or (self.length - self.tell())
        if self.__read_callback:
            self.__read_callback(self.tell())
        return super().read(size)


class GoFileUpload:
    def __init__(self, listener, path):
        self.listener = listener
        self._updater = None
        self._path = path
        self._is_errored = False
        self.api_url = "https://api.gofile.io/"
        self.__processed_bytes = 0
        self.last_uploaded = 0
        self.total_time = 0
        self.total_files = 0
        self.total_folders = 0
        self.is_uploading = True
        self.update_interval = 3

        from bot import user_data

        user_dict = user_data.get(self.listener.user_id, {})
        self.token = user_dict.get("GOFILE_TOKEN") or Config.GOFILE_API
        self.folder_id = (
            user_dict.get("GOFILE_FOLDER_ID") or Config.GOFILE_FOLDER_ID or ""
        )

    @property
    def speed(self):
        try:
            return self.__processed_bytes / self.total_time
        except Exception:
            return 0

    @property
    def processed_bytes(self):
        return self.__processed_bytes

    def __progress_callback(self, current):
        chunk_size = current - self.last_uploaded
        self.last_uploaded = current
        self.__processed_bytes += chunk_size

    async def progress(self):
        self.total_time += self.update_interval

    @staticmethod
    async def is_goapi(token):
        if token is None:
            return False

        headers = {"Authorization": f"Bearer {token}"}
        async with (
            ClientSession() as session,
            session.get(
                "https://api.gofile.io/accounts/website", headers=headers
            ) as resp,
        ):
            res = await resp.json()
            return res.get("status") == "ok"

    async def __resp_handler(self, response):
        if (api_resp := response.get("status", "")) == "ok":
            return response["data"]
        LOGGER.error(f"GoFile API Error Response: {response}")
        error_msg = (
            response.get("error")
            or response.get("message")
            or response.get("data", {}).get("error")
            or response.get("data", {}).get("message")
            or (api_resp.split("-")[1] if "error-" in api_resp else None)
            or f"Status: {api_resp}"
        )
        raise Exception(f"GoFile Error: {error_msg}")

    @staticmethod
    def _folder_id(content_data):
        if not isinstance(content_data, dict):
            raise Exception(
                "GoFile API returned an unexpected response (no folder data)"
            )
        fid = content_data.get("id") or content_data.get("folderId")
        if not fid:
            raise Exception(
                f"GoFile API response missing folder id: {content_data!r}"
            )
        return fid

    async def __getServer(self):
        async with ClientSession() as session:
            async with session.get(f"{self.api_url}servers") as resp:
                res = await resp.json()
                data = res.get("data", {})
                servers = data.get("servers") or data.get("serversAllZone", [])
                if not servers:
                    LOGGER.error(f"GoFile servers response: {res}")
                    raise Exception("No GoFile servers available")
                return servers

    async def __getAccount(self, check_account=False):
        if self.token is None:
            raise Exception("GoFile API token not found!")

        headers = {"Authorization": f"Bearer {self.token}"}
        async with (
            ClientSession() as session,
            session.get(f"{self.api_url}accounts/website", headers=headers) as resp,
        ):
            res = await resp.json()
            if check_account:
                return res["status"] == "ok"
            return await self.__resp_handler(res)

    async def __setOptions(self, contentId, option, value):
        if self.token is None:
            raise Exception("GoFile API token not found!")

        if option not in [
            "name",
            "description",
            "tags",
            "public",
            "expiry",
            "password",
        ]:
            raise Exception(f"Invalid GoFile Option Specified: {option}")

        headers = {"Authorization": f"Bearer {self.token}"}
        async with (
            ClientSession() as session,
            session.put(
                url=f"{self.api_url}contents/{contentId}/update",
                json={
                    "attribute": option,
                    "attributeValue": value,
                },
                headers=headers,
            ) as resp,
        ):
            return await self.__resp_handler(await resp.json())

    @retry(
        wait=wait_exponential(multiplier=2, min=4, max=8),
        stop=stop_after_attempt(3),
        retry=retry_if_exception_type(Exception),
    )
    async def upload_aiohttp(self, url, file_path, req_file, data, target_filename=None):
        from aiohttp import FormData
        
        with ProgressFileReader(
            filename=file_path, read_callback=self.__progress_callback
        ) as file:
            form = FormData()
            for key, value in data.items():
                form.add_field(key, str(value))

            upload_filename = target_filename or ospath.basename(file_path)
            form.add_field(req_file, file, filename=upload_filename)
            
            async with ClientSession() as session:
                async with session.post(url, data=form) as resp:
                    if resp.status == 200:
                        try:
                            return await resp.json()
                        except ContentTypeError:
                            return {
                                "status": "ok",
                                "data": {"downloadPage": "Uploaded"},
                            }
                        except JSONDecodeError:
                            return {
                                "status": "ok",
                                "data": {"downloadPage": "Uploaded"},
                            }
                    else:
                        raise Exception(f"HTTP {resp.status}: {await resp.text()}")
        return None

    async def create_folder(self, parentFolderId, folderName):
        if self.token is None:
            raise Exception("GoFile API token not found!")

        headers = {"Authorization": f"Bearer {self.token}"}
        async with (
            ClientSession() as session,
            session.post(
                url=f"{self.api_url}contents/createfolder",
                json={
                    "parentFolderId": parentFolderId,
                    "folderName": folderName,
                },
                headers=headers,
            ) as resp,
        ):
            return await self.__resp_handler(await resp.json())

    async def upload_file(
        self,
        path: str,
        folderId: str = "",
        description: str = "",
        password: str = "",
        tags: str = "",
        expire: str = "",
    ):
        if password and len(password) < 4:
            raise ValueError("Password Length must be greater than 4")

        servers = await self.__getServer()
        server = choice(servers)["name"]
        req_dict = {}

        if self.token:
            req_dict["token"] = self.token
        if folderId:
            req_dict["folderId"] = folderId
        if description:
            req_dict["description"] = description
        if password:
            req_dict["password"] = password
        if tags:
            req_dict["tags"] = tags
        if expire:
            req_dict["expire"] = expire

        if self.listener.is_cancelled:
            return None

        upload_filename = ospath.basename(path).replace(" ", ".")

        upload_file = await self.upload_aiohttp(
            f"https://{server}.gofile.io/uploadfile",
            path,
            "file",
            req_dict,
            target_filename=upload_filename,
        )
        return await self.__resp_handler(upload_file)

    async def _upload_dir(
        self, input_directory, parent_folder_id=None, root_folder_id=None
    ):
        if parent_folder_id is None:
            if self.folder_id:
                parent_folder_id = self.folder_id
                main_folder_code = self.folder_id
            else:
                if root_folder_id is None:
                    account_data = await self.__getAccount()
                    root_folder_id = account_data["rootFolder"]

                folder_data = await self.create_folder(
                    root_folder_id, ospath.basename(input_directory)
                )
                folder_id_value = self._folder_id(folder_data)
                await self.__setOptions(
                    contentId=folder_id_value, option="public", value="true"
                )
                parent_folder_id = folder_id_value
                main_folder_code = folder_data.get("code") or folder_id_value
        else:
            main_folder_code = None

        folder_ids = {".": parent_folder_id}

        for root, _dirs, files in await sync_to_async(oswalk, input_directory):
            if self.listener.is_cancelled:
                break

            rel_path = ospath.relpath(root, input_directory)
            current_folder_id = folder_ids.get(
                ospath.dirname(rel_path), parent_folder_id
            )

            if rel_path != ".":
                folder_name = ospath.basename(rel_path)
                curr_folder_data = await self.create_folder(
                    current_folder_id, folder_name
                )
                curr_folder_id_value = self._folder_id(curr_folder_data)
                await self.__setOptions(
                    contentId=curr_folder_id_value,
                    option="public",
                    value="true",
                )
                folder_ids[rel_path] = curr_folder_id_value
                current_folder_id = curr_folder_id_value
                self.total_folders += 1

            for file in files:
                if self.listener.is_cancelled:
                    break

                file_path = ospath.join(root, file)
                await self.upload_file(file_path, current_folder_id)
                self.total_files += 1

        return main_folder_code

    async def upload(self):
        try:
            LOGGER.info(f"GoFile Uploading: {self._path}")
            self._updater = SetInterval(self.update_interval, self.progress)

            if not self.token:
                raise ValueError(
                    "GoFile API token not configured! Please set your GoFile token in user settings or configure a global token."
                )

            await self._upload_process()

        except Exception as err:
            if isinstance(err, RetryError):
                LOGGER.info(f"Total Attempts: {err.last_attempt.attempt_number}")
                err = err.last_attempt.exception()
            err = str(err).replace(">", "").replace("<", "")
            LOGGER.error(err)
            await self.listener.on_upload_error(err)
            self._is_errored = True
        finally:
            if self._updater:
                self._updater.cancel()

    async def _upload_process(self):
        try:
            account_data = await self.__getAccount()
        except Exception as e:
            raise Exception(f"GoFile Account Error: {e}") from e

        if await aiopath.isfile(self._path):
            folder_id = self.folder_id or account_data["rootFolder"]
            file_result = await self.upload_file(
                path=self._path, folderId=folder_id
            )
            if file_result and file_result.get("downloadPage"):
                link = file_result["downloadPage"]
                mime_type = "File"
                self.total_files = 1
            else:
                raise ValueError("Failed to upload file to GoFile")
        elif await aiopath.isdir(self._path):
            folder_code = await self._upload_dir(
                self._path, root_folder_id=account_data["rootFolder"]
            )
            if folder_code:
                link = f"https://gofile.io/d/{folder_code}"
                mime_type = "Folder"
            else:
                raise ValueError("Failed to upload folder to GoFile")
        else:
            raise ValueError("Invalid file path!")

        if self.listener.is_cancelled:
            return

        LOGGER.info(f"Uploaded To GoFile: {self.listener.name}")
        await self.listener.on_upload_complete(
            link,
            self.total_files,
            self.total_folders,
            mime_type,
            dir_id="",
        )

    async def cancel_task(self):
        self.listener.is_cancelled = True
        if self.is_uploading:
            LOGGER.info(f"Cancelling GoFile Upload: {self.listener.name}")
            await self.listener.on_upload_error("GoFile upload has been cancelled!")
