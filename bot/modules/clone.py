# This file is a part of NEO-WZML (github.com/irisXDR/NEO-WZML)

from asyncio import gather
from json import loads
from secrets import token_hex

from aiofiles.os import remove

from bot import LOGGER, bot_loop, task_dict, task_dict_lock
from bot.core.config_manager import BinConfig
from bot.core.tg_client import TgClient
from bot.helper.themes import BotTheme
from bot.helper.ext_utils.bot_utils import (
    COMMAND_USAGE,
    arg_parser,
    cmd_exec,
    sync_to_async,
)
from bot.helper.ext_utils.exceptions import DirectDownloadLinkException
from bot.helper.ext_utils.links_utils import (
    is_gdrive_id,
    is_gdrive_link,
    is_rclone_path,
    is_share_link,
    is_terabox_link,
)
from bot.helper.ext_utils.task_manager import (
    pre_task_check,
    stop_duplicate_check,
    limit_checker,
    register_task_for_limit_check,
)
from bot.helper.ext_utils.db_handler import database
from bot.helper.ext_utils.status_utils import get_readable_file_size
from bot.helper.listeners.task_listener import TaskListener
from bot.helper.mirror_leech_utils.download_utils.direct_link_generator import (
    direct_link_generator,
)
from bot.helper.mirror_leech_utils.gdrive_utils.clone import GoogleDriveClone
from bot.helper.mirror_leech_utils.gdrive_utils.count import GoogleDriveCount
from bot.helper.mirror_leech_utils.rclone_utils.transfer import RcloneTransferHelper
from bot.helper.mirror_leech_utils.status_utils.gdrive_status import GoogleDriveStatus
from bot.helper.mirror_leech_utils.status_utils.rclone_status import RcloneStatus
from bot.helper.telegram_helper.message_utils import (
    auto_delete_message,
    delete_links,
    delete_message,
    send_message,
    send_status_message,
)


class Clone(TaskListener):
    def __init__(
        self,
        client,
        message,
        bulk=None,
        multi_tag=None,
        options="",
        **kwargs,
    ):
        if bulk is None:
            bulk = []
        self.message = message
        self.client = client
        self.multi_tag = multi_tag
        self.options = options
        self.same_dir = {}
        self.bulk = bulk
        super().__init__()
        self.is_clone = True

    async def new_event(self):
        text = self.message.text.split("\n")
        input_list = text[0].split(" ")

        check_msg, check_button = await pre_task_check(self.message)
        if check_msg:
            await delete_links(self.message)
            await auto_delete_message(
                await send_message(self.message, check_msg, check_button)
            )
            return

        registered, limit_msg = await register_task_for_limit_check(
            self.user_id, self.message
        )
        if not registered:
            await delete_links(self.message)
            await auto_delete_message(await send_message(self.message, limit_msg))
            return

        args = {
            "link": "",
            "-i": 0,
            "-b": False,
            "-n": "",
            "-up": "",
            "-rcf": "",
            "-sync": False,
        }

        arg_parser(input_list[1:], args)

        try:
            self.multi = int(args["-i"])
        except Exception:
            self.multi = 0

        self.up_dest = args["-up"]
        self.rc_flags = args["-rcf"]
        self.link = args["link"]
        self.name = args["-n"]
        self.custom_name = bool(args["-n"])

        is_bulk = args["-b"]
        sync = args["-sync"]
        bulk_start = 0
        bulk_end = 0

        if not isinstance(is_bulk, bool):
            dargs = is_bulk.split(":")
            bulk_start = dargs[0] or 0
            if len(dargs) == 2:
                bulk_end = dargs[1] or 0
            is_bulk = True

        if is_bulk:
            await self.init_bulk(input_list, bulk_start, bulk_end, Clone)
            await database.remove_shared_task(
                self.message.id, TgClient.ID, user_id=self.user_id
            )
            return

        await self.get_tag(text)

        if not self.link and (reply_to := self.message.reply_to_message):
            self.link = reply_to.text.split("\n", 1)[0].strip()

        await self.run_multi(input_list, Clone)

        if len(self.link) == 0:
            await database.remove_shared_task(self.message.id, TgClient.ID, user_id=self.user_id)
            await send_message(
                self.message, COMMAND_USAGE["clone"][0], COMMAND_USAGE["clone"][1]
            )
            await delete_links(self.message)
            return
        if not is_terabox_link(self.link):
            LOGGER.info(self.link)
        try:
            await self.before_start()
        except Exception as e:
            await database.remove_shared_task(self.message.id, TgClient.ID, user_id=self.user_id)
            await send_message(self.message, e)
            await delete_links(self.message)
            return

        self._set_mode_engine()

        await self.send_processing()
        try:
            await delete_links(self.message)
            await self._proceed_to_clone(sync)
        finally:
            await self.remove_processing()

    async def _proceed_to_clone(self, sync):
        if is_share_link(self.link):
            try:
                self.link = await sync_to_async(direct_link_generator, self.link)
                LOGGER.info(f"Generated link: {self.link}")
            except DirectDownloadLinkException as e:
                LOGGER.error(str(e))
                if str(e).startswith("ERROR:"):
                    await database.remove_shared_task(self.message.id, TgClient.ID, user_id=self.user_id)
                    await send_message(self.message, str(e))
                    return
        if is_gdrive_link(self.link) or is_gdrive_id(self.link):
            self.name, mime_type, self.size, files, _ = await sync_to_async(
                GoogleDriveCount().count, self.link, self.user_id
            )
            if mime_type is None:
                await database.remove_shared_task(self.message.id, TgClient.ID, user_id=self.user_id)
                await send_message(self.message, self.name)
                return
            msg, button = await stop_duplicate_check(self)
            if msg:
                await database.remove_shared_task(self.message.id, TgClient.ID, user_id=self.user_id)
                await send_message(self.message, msg, button)
                return
            if limit_exceeded := await limit_checker(self):
                await database.remove_shared_task(self.message.id, TgClient.ID, user_id=self.user_id)
                await send_message(
                    self.message,
                    f"""<i><b>Limit Breached!</b></i>
 • <b>Task Size:</b> {get_readable_file_size(self.size)}
 • <b>Mode:</b> {self.mode[0]} - {self.mode[1]}

{limit_exceeded}""",
                )
                return
            await self.on_download_start()
            LOGGER.info(f"Clone Started: Name: {self.name} - Source: {self.link}")
            drive = GoogleDriveClone(self)
            if files <= 10:
                msg = await send_message(
                    self.message, f"Cloning: <code>{self.link}</code>"
                )
            else:
                msg = ""
                gid = token_hex(5)
                async with task_dict_lock:
                    task_dict[self.mid] = GoogleDriveStatus(self, drive, gid, "cl")
                if self.multi <= 1:
                    await send_status_message(self.message)
            flink, mime_type, files, folders, dir_id = await sync_to_async(drive.clone)
            if msg:
                await delete_message(msg)
            if not flink:
                return
            await self.on_upload_complete(
                flink, files, folders, mime_type, dir_id=dir_id
            )
            LOGGER.info(f"Cloning Done: {self.name}")
        elif is_rclone_path(self.link):
            if self.link.startswith("mrcc:"):
                self.link = self.link.replace("mrcc:", "", 1)
                self.up_dest = self.up_dest.replace("mrcc:", "", 1)
                config_path = f"rclone/{self.user_id}.conf"
            else:
                config_path = "rclone.conf"

            remote, src_path = self.link.split(":", 1)
            self.link = src_path.strip("/")
            if self.link.startswith("rclone_select"):
                mime_type = "Folder"
                src_path = ""
                if not self.name:
                    self.name = self.link
            else:
                src_path = self.link
                cmd = [
                    BinConfig.RCLONE_NAME,
                    "lsjson",
                    "--fast-list",
                    "--stat",
                    "--no-modtime",
                    "--config",
                    config_path,
                    f"{remote}:{src_path}",
                ]
                res = await cmd_exec(cmd)
                if res[2] != 0:
                    if res[2] != -9:
                        await database.remove_shared_task(self.message.id, TgClient.ID, user_id=self.user_id)
                        msg = f"Error: While getting rclone stat. Path: {remote}:{src_path}. Stderr: {res[1][:4000]}"
                        await send_message(self.message, msg)
                    return
                rstat = loads(res[0])
                if rstat["IsDir"]:
                    if not self.name:
                        self.name = src_path.rsplit("/", 1)[-1] if src_path else remote
                    self.up_dest += (
                        self.name if self.up_dest.endswith(":") else f"/{self.name}"
                    )
                    mime_type = "Folder"
                else:
                    if not self.name:
                        self.name = src_path.rsplit("/", 1)[-1]
                    mime_type = rstat["MimeType"]

            await self.on_download_start()

            RCTransfer = RcloneTransferHelper(self)
            LOGGER.info(
                f"Clone Started: Name: {self.name} - Source: {self.link} - Destination: {self.up_dest}"
            )
            gid = token_hex(5)
            async with task_dict_lock:
                task_dict[self.mid] = RcloneStatus(self, RCTransfer, gid, "cl")
            if self.multi <= 1:
                await send_status_message(self.message)
            method = "sync" if sync else "copy"
            flink, destination = await RCTransfer.clone(
                config_path,
                remote,
                src_path,
                mime_type,
                method,
            )
            if self.link.startswith("rclone_select"):
                await remove(self.link)
            if not destination:
                return
            LOGGER.info(f"Cloning Done: {self.name}")
            cmd1 = [
                BinConfig.RCLONE_NAME,
                "lsf",
                "--fast-list",
                "-R",
                "--files-only",
                "--config",
                config_path,
                destination,
            ]
            cmd2 = [
                BinConfig.RCLONE_NAME,
                "lsf",
                "--fast-list",
                "-R",
                "--dirs-only",
                "--config",
                config_path,
                destination,
            ]
            cmd3 = [
                BinConfig.RCLONE_NAME,
                "size",
                "--fast-list",
                "--json",
                "--config",
                config_path,
                destination,
            ]
            res1, res2, res3 = await gather(
                cmd_exec(cmd1),
                cmd_exec(cmd2),
                cmd_exec(cmd3),
            )
            if res1[2] != 0 or res2[2] != 0 or res3[2] != 0:
                if res1[2] == -9:
                    return
                files = None
                folders = None
                self.size = 0
                error = res1[1] or res2[1] or res3[1]
                msg = f"Error: While getting rclone stat. Path: {destination}. Stderr: {error[:4000]}"
                await self.on_upload_error(msg)
            else:
                files = len(res1[0].split("\n"))
                folders = len(res2[0].strip().split("\n")) if res2[0] else 0
                rsize = loads(res3[0])
                self.size = rsize["bytes"]
                await self.on_upload_complete(
                    flink, files, folders, mime_type, destination
                )
        else:
            await database.remove_shared_task(self.message.id, TgClient.ID, user_id=self.user_id)
            await send_message(
                self.message, COMMAND_USAGE["clone"][0], COMMAND_USAGE["clone"][1]
            )


async def clone_node(client, message):
    bot_loop.create_task(Clone(client, message).new_event())
