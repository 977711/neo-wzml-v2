# This file is a part of NEO-WZML (github.com/irisXDR/NEO-WZML)

from ast import literal_eval
from base64 import b64encode
from html import escape as html_escape
from re import match as re_match

from aiofiles.os import path as aiopath
from bot.core.config_manager import Config
from bot.core.tg_client import TgClient

from bot import DOWNLOAD_DIR, LOGGER, bot_loop, task_dict_lock
from bot.helper.ext_utils.bot_utils import (
    COMMAND_USAGE,
    arg_parser,
    get_content_type,
    sync_to_async,
    fetch_user_dumps,
)
from bot.helper.ext_utils.exceptions import DirectDownloadLinkException
from bot.helper.ext_utils.links_utils import (
    is_gdrive_id,
    is_gdrive_link,
    is_mega_link,
    is_terabox_link,
    is_magnet,
    is_rclone_path,
    is_telegram_link,
    is_url,
)
from bot.helper.ext_utils.task_manager import pre_task_check, register_task_for_limit_check
from bot.helper.ext_utils.db_handler import database
from bot.helper.listeners.task_listener import TaskListener
from bot.helper.mirror_leech_utils.download_utils.aria2_download import (
    add_aria2_download,
)
from bot.helper.mirror_leech_utils.download_utils.direct_downloader import (
    add_direct_download,
)
from bot.helper.mirror_leech_utils.download_utils.direct_link_generator import (
    direct_link_generator,
)
from bot.helper.mirror_leech_utils.download_utils.gd_download import add_gd_download
from bot.helper.mirror_leech_utils.download_utils.jd_download import add_jd_download
from bot.helper.mirror_leech_utils.download_utils.mega_download import add_mega_download
from bot.helper.mirror_leech_utils.download_utils.terabox_download import (
    add_terabox_download,
    add_terabox_account_download,
)
from bot.helper.mirror_leech_utils.download_utils.qbit_download import add_qb_torrent
from bot.helper.mirror_leech_utils.download_utils.rclone_download import (
    add_rclone_download,
    add_rclone_web_selection,
)
from bot.helper.mirror_leech_utils.download_utils.telegram_download import (
    TelegramDownloadHelper,
)
from bot.helper.telegram_helper.message_utils import (
    auto_delete_message,
    delete_links,
    get_tg_link_message,
    open_dump_btns,
    send_message,
)


class Mirror(TaskListener):
    def __init__(
        self,
        client,
        message,
        is_qbit=False,
        is_leech=False,
        is_jd=False,
        is_uphoster=False,
        same_dir=None,
        bulk=None,
        multi_tag=None,
        options="",
        **kwargs,
    ):
        if same_dir is None:
            same_dir = {}
        if bulk is None:
            bulk = []
        self.message = message
        self.client = client
        self.multi_tag = multi_tag
        self.options = options
        self.same_dir = same_dir
        self.bulk = bulk
        super().__init__()
        self.is_qbit = is_qbit
        self.is_leech = is_leech
        self.is_jd = is_jd
        self.is_uphoster = is_uphoster

    async def new_event(self):
        raw_text = self.message.text or self.message.caption or ""
        if not raw_text.strip():
            await send_message(
                self.message, COMMAND_USAGE["mirror"][0], COMMAND_USAGE["mirror"][1]
            )
            return

        text = raw_text.split("\n")
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
            "-doc": False,
            "-med": False,
            "-d": False,
            "-j": False,
            "-s": False,
            "-b": False,
            "-e": False,
            "-z": False,
            "-zim": False,
            "-zipimage": False,
            "-zipimages": False,
            "-sv": False,
            "-ss": False,
            "-f": False,
            "-fd": False,
            "-fu": False,
            "-hl": False,
            "-bt": False,
            "-ut": False,
            "-i": 0,
            "link": "",
            "-n": "",
            "-m": "",
            "-meta": "",
            "-up": "",
            "-rcf": "",
            "-au": "",
            "-ap": "",
            "-h": "",
            "-t": "",
            "-ca": "",
            "-cv": "",
            "-ud": "",

            "-tl": "",
            "-ff": set(),
            "-mv": False,
        }

        arg_parser(input_list[1:], args)

        if Config.DISABLE_BULK and args.get("-b", False):
            await database.remove_shared_task(self.message.id, TgClient.ID, user_id=self.user_id)
            await send_message(self.message, "Bulk downloads are currently disabled.")
            return

        if Config.DISABLE_MULTI and int(args.get("-i", 1)) > 1:
            await database.remove_shared_task(self.message.id, TgClient.ID, user_id=self.user_id)
            await send_message(
                self.message,
                "Multi-downloads are currently disabled. Please try without the -i flag.",
            )
            return

        if Config.DISABLE_SEED and args.get("-d", False):
            await database.remove_shared_task(self.message.id, TgClient.ID, user_id=self.user_id)
            await send_message(
                self.message,
                "Seeding is currently disabled. Please try without the -d flag.",
            )
            return

        if Config.DISABLE_FF_MODE and args.get("-ff"):
            await database.remove_shared_task(self.message.id, TgClient.ID, user_id=self.user_id)
            await send_message(self.message, "FFmpeg commands are currently disabled.")
            return

        self.select = args["-s"]
        self.seed = args["-d"]
        self.name = args["-n"]
        self.custom_name = bool(args["-n"])
        self.up_dest = args["-up"]
        self.rc_flags = args["-rcf"]
        self.link = args["link"]

        cmd = input_list[0].split("@")[0].lower()
        extract_from_cmd = "uz" in cmd or "unzip" in cmd
        compress_from_cmd = not extract_from_cmd and ("z" in cmd or "zip" in cmd)
        
        self.compress = args["-z"] or compress_from_cmd
        self.zip_images = (
            args["-zim"] or args["-zipimage"] or args["-zipimages"]
        )
        self.extract = args["-e"] or extract_from_cmd
        self.join = args["-j"]
        self.thumb = args["-t"]
        self.sample_video = args["-sv"]
        self.screen_shots = args["-ss"] if Config.SCREENSHOTS_MODE else False
        self.force_run = args["-f"]
        self.force_download = args["-fd"]
        self.force_upload = args["-fu"]
        self.convert_audio = args["-ca"]
        self.convert_video = args["-cv"]

        self.merge_video = args["-mv"]

        self.hybrid_leech = args["-hl"]
        self.thumbnail_layout = args["-tl"]
        self.as_doc = args["-doc"]
        self.as_med = args["-med"]
        self.folder_name = f"/{args['-m']}".rstrip("/") if len(args["-m"]) > 0 else ""
        self.bot_trans = args["-bt"]
        self.user_trans = args["-ut"]
        self.metadata_dict = self.default_metadata_dict.copy()
        self.audio_metadata_dict = self.audio_metadata_dict.copy()
        self.video_metadata_dict = self.video_metadata_dict.copy()
        self.subtitle_metadata_dict = self.subtitle_metadata_dict.copy()
        if args["-meta"]:
            meta = self.metadata_processor.parse_string(args["-meta"])
            self.metadata_dict = self.metadata_processor.merge_dicts(
                self.metadata_dict, meta
            )

        headers = args["-h"]
        is_bulk = args["-b"]

        bulk_start = 0
        bulk_end = 0
        ratio = None
        seed_time = None
        reply_to = None
        file_ = None
        session = ""

        try:
            self.multi = int(args["-i"])
        except Exception:
            self.multi = 0

        try:
            if args["-ff"]:
                if isinstance(args["-ff"], set):
                    self.ffmpeg_cmds = args["-ff"]
                else:
                    parsed = literal_eval(args["-ff"])
                    if not isinstance(parsed, (list, tuple, set, dict, str)):
                        raise ValueError(
                            f"Unsupported -ff payload type: {type(parsed).__name__}"
                        )
                    self.ffmpeg_cmds = parsed
        except Exception as e:
            self.ffmpeg_cmds = None
            LOGGER.error(f"Invalid -ff value (must be a literal list/dict/string): {e}")

        if not isinstance(self.seed, bool):
            dargs = self.seed.split(":")
            ratio = dargs[0] or None
            if len(dargs) == 2:
                seed_time = dargs[1] or None
            self.seed = True

        if not isinstance(is_bulk, bool):
            dargs = is_bulk.split(":")
            bulk_start = dargs[0] or 0
            if len(dargs) == 2:
                bulk_end = dargs[1] or 0
            is_bulk = True

        if not is_bulk:
            if self.multi > 0:
                if self.folder_name:
                    async with task_dict_lock:
                        if self.folder_name in self.same_dir:
                            self.same_dir[self.folder_name]["tasks"].add(self.mid)
                            for fd_name in self.same_dir:
                                if fd_name != self.folder_name:
                                    self.same_dir[fd_name]["total"] -= 1
                        elif self.same_dir:
                            self.same_dir[self.folder_name] = {
                                "total": self.multi,
                                "tasks": {self.mid},
                            }
                            for fd_name in self.same_dir:
                                if fd_name != self.folder_name:
                                    self.same_dir[fd_name]["total"] -= 1
                        else:
                            self.same_dir = {
                                self.folder_name: {
                                    "total": self.multi,
                                    "tasks": {self.mid},
                                }
                            }
                elif self.same_dir:
                    async with task_dict_lock:
                        for fd_name in self.same_dir:
                            self.same_dir[fd_name]["total"] -= 1
        else:
            await self.init_bulk(input_list, bulk_start, bulk_end, Mirror)
            await database.remove_shared_task(
                self.message.id, TgClient.ID, user_id=self.user_id
            )
            return

        if len(self.bulk) != 0:
            del self.bulk[0]

        await self.run_multi(input_list, Mirror)

        await self.get_tag(text)

        path = f"{DOWNLOAD_DIR}{self.mid}{self.folder_name}"

        if not self.link and (reply_to := self.message.reply_to_message):
            if reply_to.text:
                self.link = reply_to.text.split("\n", 1)[0].strip()
        if is_telegram_link(self.link):
            try:
                reply_to, session = await get_tg_link_message(self.link)
            except Exception as e:
                await database.remove_shared_task(self.message.id, TgClient.ID, user_id=self.user_id)
                await send_message(self.message, f"ERROR: {e}")
                await self.remove_from_same_dir()
                await delete_links(self.message)
                return

        if isinstance(reply_to, list):
            self.bulk = reply_to
            b_msg = input_list[:1]
            self.options = " ".join(input_list[1:])
            b_msg.append(f"{self.bulk[0]} -i {len(self.bulk)} {self.options}")
            nextmsg = await send_message(self.message, " ".join(b_msg))
            nextmsg = await self.client.get_messages(
                chat_id=self.message.chat.id, message_ids=nextmsg.id
            )
            if self.message.from_user:
                nextmsg.from_user = self.user
            else:
                nextmsg.sender_chat = self.user
            await Mirror(
                self.client,
                nextmsg,
                self.is_qbit,
                self.is_leech,
                self.is_jd,
                self.is_uphoster,
                self.same_dir,
                self.bulk,
                self.multi_tag,
                self.options,
            ).new_event()
            await database.remove_shared_task(
                self.message.id, TgClient.ID, user_id=self.user_id
            )
            return

        if reply_to:
            file_ = (
                reply_to.document
                or reply_to.photo
                or reply_to.video
                or reply_to.audio
                or reply_to.voice
                or reply_to.video_note
                or reply_to.sticker
                or reply_to.animation
                or None
            )
            self.file_details = {"caption": reply_to.caption}

            if file_ is None:
                if reply_text := reply_to.text:
                    self.link = reply_text.split("\n", 1)[0].strip()
                else:
                    reply_to = None
            elif reply_to.document and (
                file_.mime_type == "application/x-bittorrent"
                or file_.file_name.endswith((".torrent", ".dlc"))
            ):
                self.link = await reply_to.download()
                file_ = None

        if (
            not self.link
            and file_ is None
            or is_telegram_link(self.link)
            and reply_to is None
            or file_ is None
            and not is_url(self.link)
            and not is_magnet(self.link)
            and not await aiopath.exists(self.link)
            and not is_rclone_path(self.link)
            and not is_gdrive_id(self.link)
            and not is_gdrive_link(self.link)
            and not is_mega_link(self.link)
            and self.link != "tbx"
        ):
            await database.remove_shared_task(self.message.id, TgClient.ID, user_id=self.user_id)
            await send_message(
                self.message, COMMAND_USAGE["mirror"][0], COMMAND_USAGE["mirror"][1]
            )
            await self.remove_from_same_dir()
            await delete_links(self.message)
            return

        if len(self.link) > 0 and not is_terabox_link(self.link):
            LOGGER.info(self.link)

        if self.is_leech:
            user_dump = args["-ud"]
            ldumps = await fetch_user_dumps(self.user_id)
            if ldumps:
                if user_dump:
                    if user_dump.lower() == "all":
                        self.selected_dumps = list(ldumps.values())
                    else:
                        names = [n.strip() for n in user_dump.split(",")]
                        if len(names) > 3:
                            await database.remove_shared_task(self.message.id, TgClient.ID, user_id=self.user_id)
                            await send_message(
                                self.message,
                                f"Max 3 dumps allowed, {len(names)} provided."
                            )
                            await self.remove_from_same_dir()
                            await delete_links(self.message)
                            return
                        selected = []
                        for name in names:
                            dump_id = next(
                                (
                                    dump_id
                                    for name_, dump_id in ldumps.items()
                                    if name.lower() == name_.lower()
                                ),
                                None,
                            )
                            if dump_id is None:
                                await database.remove_shared_task(self.message.id, TgClient.ID, user_id=self.user_id)
                                await send_message(
                                    self.message,
                                    f"Dump '{name}' not found. Available: {', '.join(ldumps.keys())}"
                                )
                                await self.remove_from_same_dir()
                                await delete_links(self.message)
                                return
                            if dump_id not in selected:
                                selected.append(dump_id)
                        self.selected_dumps = selected
                elif len(ldumps) == 1:
                    self.selected_dumps = [next(iter(ldumps.values()))]
                else:
                    dump_chat, is_cancelled = await open_dump_btns(self.message)
                    if is_cancelled:
                        await database.remove_shared_task(self.message.id, TgClient.ID, user_id=self.user_id)
                        await self.remove_from_same_dir()
                        await delete_links(self.message)
                        return
                    self.selected_dumps = dump_chat

        try:
            await self.before_start()
        except Exception as e:
            await database.remove_shared_task(self.message.id, TgClient.ID, user_id=self.user_id)
            await send_message(
                self.message,
                html_escape(str(e) or e.__class__.__name__),
            )
            await self.remove_from_same_dir()
            await delete_links(self.message)
            return

        self._set_mode_engine()

        if (
            not self.is_jd
            and not self.is_qbit
            and not is_magnet(self.link)
            and not is_rclone_path(self.link)
            and not is_gdrive_link(self.link)
            and not self.link.endswith(".torrent")
            and file_ is None
            and not is_gdrive_id(self.link)
            and not is_mega_link(self.link)
            and not is_terabox_link(self.link)
            and not self.is_terabox_account
        ):
            content_type = await get_content_type(self.link)
            if content_type is None or re_match(r"text/html|text/plain", content_type):
                try:
                    self.link = await sync_to_async(direct_link_generator, self.link)
                    if isinstance(self.link, tuple):
                        self.link, headers = self.link
                    elif isinstance(self.link, str):
                        LOGGER.info(f"Generated link: {self.link}")
                except DirectDownloadLinkException as e:
                    e = str(e)
                    if "This link requires a password!" not in e:
                        LOGGER.info(e)
                    if e.startswith("ERROR:"):
                        await database.remove_shared_task(self.message.id, TgClient.ID, user_id=self.user_id)
                        await send_message(self.message, html_escape(e))
                        await self.remove_from_same_dir()
                        await delete_links(self.message)
                        return
                except Exception as e:
                    await database.remove_shared_task(self.message.id, TgClient.ID, user_id=self.user_id)
                    await send_message(
                        self.message,
                        html_escape(str(e) or e.__class__.__name__),
                    )
                    await self.remove_from_same_dir()
                    await delete_links(self.message)
                    return

        await self.send_processing()
        try:
            await delete_links(self.message)
            if file_ is not None:
                await TelegramDownloadHelper(self).add_download(
                    reply_to, f"{path}/", session
                )
            elif isinstance(self.link, dict):
                await add_direct_download(self, path)
            elif self.is_jd:
                await add_jd_download(self, path)
            elif self.is_qbit:
                await add_qb_torrent(self, path, ratio, seed_time)
            elif is_rclone_path(self.link):
                if getattr(self, "_rcl_web", False):
                    await add_rclone_web_selection(self, f"{path}/")
                else:
                    await add_rclone_download(self, f"{path}/")
            elif is_gdrive_link(self.link) or is_gdrive_id(self.link):
                await add_gd_download(self, path)
            elif is_mega_link(self.link):
                await add_mega_download(self, f"{path}/")
            elif self.is_terabox_account:
                await add_terabox_account_download(self, f"{path}/")
            elif is_terabox_link(self.link):
                await add_terabox_download(self, f"{path}/")
            else:
                ussr = args["-au"]
                pssw = args["-ap"]
                if ussr or pssw:
                    auth = f"{ussr}:{pssw}"
                    headers += (
                        f" authorization: Basic {b64encode(auth.encode()).decode('ascii')}"
                    )
                await add_aria2_download(self, path, headers, ratio, seed_time)
        finally:
            await self.remove_processing()


async def mirror(client, message):
    bot_loop.create_task(Mirror(client, message).new_event())


async def qb_mirror(client, message):
    bot_loop.create_task(Mirror(client, message, is_qbit=True).new_event())


async def jd_mirror(client, message):
    bot_loop.create_task(Mirror(client, message, is_jd=True).new_event())


async def leech(client, message):
    if Config.DISABLE_LEECH:
        await message.reply("The Leech command is currently disabled.")
        return
    bot_loop.create_task(Mirror(client, message, is_leech=True).new_event())


async def qb_leech(client, message):
    bot_loop.create_task(
        Mirror(client, message, is_qbit=True, is_leech=True).new_event()
    )


async def jd_leech(client, message):
    bot_loop.create_task(Mirror(client, message, is_leech=True, is_jd=True).new_event())
