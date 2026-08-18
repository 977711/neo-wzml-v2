# This file is a part of NEO-WZML (github.com/irisXDR/NEO-WZML)

from asyncio import Event
from time import time

from bot import (
    LOGGER,
    bot_cache,
    non_queued_dl,
    non_queued_up,
    queue_dict_lock,
    queued_dl,
    queued_up,
    user_data,
)
from bot.core.config_manager import Config
from bot.helper.ext_utils.db_handler import database
from bot.helper.mirror_leech_utils.gdrive_utils.search import GoogleDriveSearch
from bot.helper.telegram_helper.filters import CustomFilters
from bot.helper.telegram_helper.tg_utils import check_botpm, forcesub, verify_token
from pyrogram.enums import ChatType
from bot.helper.ext_utils.bot_utils import get_telegraph_list, sync_to_async, safe_int, getdailytasks
from bot.helper.ext_utils.files_utils import get_base_name, check_storage_threshold
from bot.helper.ext_utils.links_utils import is_gdrive_id
from bot.helper.ext_utils.status_utils import get_readable_time, get_readable_file_size, get_specific_tasks
from bot.helper.themes import BotTheme


async def stop_duplicate_check(listener):
    if (
        isinstance(listener.up_dest, int)
        or listener.is_leech
        or listener.select
        or not is_gdrive_id(listener.up_dest)
        or (listener.up_dest.startswith("mtp:") and listener.stop_duplicate)
        or not listener.stop_duplicate
        or listener.same_dir
    ):
        return False, None

    name = listener.name
    LOGGER.info(f"Checking File/Folder if already in Drive: {name}")

    if listener.compress:
        name = f"{name}.zip"
    elif listener.extract:
        try:
            name = get_base_name(name)
        except Exception:
            name = None

    if name is not None:
        telegraph_content, contents_no = await sync_to_async(
            GoogleDriveSearch(stop_dup=True, no_multi=listener.is_clone).drive_list,
            name,
            listener.up_dest,
            listener.user_id,
        )
        if telegraph_content:
            msg = BotTheme("STOP_DUPLICATE", content=contents_no)
            button = await get_telegraph_list(telegraph_content)
            return msg, button

    return False, None


async def check_running_tasks(listener, state="dl"):
    all_limit = safe_int(Config.QUEUE_ALL)
    state_limit = (
        safe_int(Config.QUEUE_DOWNLOAD)
        if state == "dl"
        else safe_int(Config.QUEUE_UPLOAD)
    )
    event = None
    is_over_limit = False
    async with queue_dict_lock:
        if state == "up" and listener.mid in non_queued_dl:
            non_queued_dl.remove(listener.mid)
        if (
            (all_limit or state_limit)
            and not listener.force_run
            and not (listener.force_upload and state == "up")
            and not (listener.force_download and state == "dl")
        ):
            dl_count = len(non_queued_dl)
            up_count = len(non_queued_up)
            t_count = dl_count if state == "dl" else up_count
            is_over_limit = (
                all_limit
                and dl_count + up_count >= all_limit
                and (not state_limit or t_count >= state_limit)
            ) or (state_limit and t_count >= state_limit)
            if is_over_limit:
                event = Event()
                if state == "dl":
                    queued_dl[listener.mid] = event
                else:
                    queued_up[listener.mid] = event
        if not is_over_limit:
            if state == "up":
                non_queued_up.add(listener.mid)
            else:
                non_queued_dl.add(listener.mid)

    return is_over_limit, event


async def start_dl_from_queued(mid: int):
    queued_dl[mid].set()
    del queued_dl[mid]
    non_queued_dl.add(mid)


async def start_up_from_queued(mid: int):
    queued_up[mid].set()
    del queued_up[mid]
    non_queued_up.add(mid)


async def start_from_queued():
    if all_limit := safe_int(Config.QUEUE_ALL):
        dl_limit = safe_int(Config.QUEUE_DOWNLOAD)
        up_limit = safe_int(Config.QUEUE_UPLOAD)
        async with queue_dict_lock:
            dl = len(non_queued_dl)
            up = len(non_queued_up)
            all_ = dl + up
            if all_ < all_limit:
                f_tasks = all_limit - all_
                if queued_up and (not up_limit or up < up_limit):
                    for index, mid in enumerate(list(queued_up.keys()), start=1):
                        await start_up_from_queued(mid)
                        f_tasks -= 1
                        if f_tasks == 0 or (up_limit and index >= up_limit - up):
                            break
                if queued_dl and (not dl_limit or dl < dl_limit) and f_tasks != 0:
                    for index, mid in enumerate(list(queued_dl.keys()), start=1):
                        await start_dl_from_queued(mid)
                        if (dl_limit and index >= dl_limit - dl) or index == f_tasks:
                            break
        return

    if up_limit := Config.QUEUE_UPLOAD:
        async with queue_dict_lock:
            up = len(non_queued_up)
            if queued_up and up < up_limit:
                f_tasks = up_limit - up
                for index, mid in enumerate(list(queued_up.keys()), start=1):
                    await start_up_from_queued(mid)
                    if index == f_tasks:
                        break
    else:
        async with queue_dict_lock:
            if queued_up:
                for mid in list(queued_up.keys()):
                    await start_up_from_queued(mid)

    if dl_limit := Config.QUEUE_DOWNLOAD:
        async with queue_dict_lock:
            dl = len(non_queued_dl)
            if queued_dl and dl < dl_limit:
                f_tasks = dl_limit - dl
                for index, mid in enumerate(list(queued_dl.keys()), start=1):
                    await start_dl_from_queued(mid)
                    if index == f_tasks:
                        break
    else:
        async with queue_dict_lock:
            if queued_dl:
                for mid in list(queued_dl.keys()):
                    await start_dl_from_queued(mid)


async def limit_checker(listener, yt_playlist=0):
    if await CustomFilters.sudo("", listener.message):
        LOGGER.info("SUDO User. Skipping Size Limit...")
        return

    user_id, size = listener.user_id, listener.size

    async def recurr_limits(limits):
        nonlocal yt_playlist, size
        limit_exceeded = ""
        for condition, attr, name in limits:
            if condition and (limit := getattr(Config, attr, 0)):
                if attr == "PLAYLIST_LIMIT":
                    if yt_playlist >= limit:
                        limit_exceeded = f" • <b>{name} Limit Count:</b> {limit}"
                        LOGGER.info(
                            f"{name} Limit Breached: {listener.name} & Count: {yt_playlist}"
                        )
                        break
                else:
                    byte_limit = limit * 1024**3
                    if size >= byte_limit:
                        limit_exceeded = f" • <b>{name} Limit:</b> {get_readable_file_size(byte_limit)}"
                        LOGGER.info(
                            f"{name} Limit Breached: {listener.name} & Size: {get_readable_file_size(size)}"
                        )
                        break
        return limit_exceeded

    limits = [
        (listener.is_torrent or listener.is_qbit, "TORRENT_LIMIT", "Torrent"),
        (listener.is_mega, "MEGA_LIMIT", "Mega"),
        (getattr(listener, "is_terabox", False), "TERABOX_LIMIT", "Terabox"),
        (listener.is_gdrive or is_gdrive_id(listener.up_dest), "GDRIVE_LIMIT", "GDrive"),
        (listener.is_clone, "CLONE_LIMIT", "Clone"),
        (listener.is_jd, "JD_LIMIT", "JDownloader"),
        (listener.is_rclone or (not listener.is_leech and not is_gdrive_id(listener.up_dest) and listener.up_dest), "RCLONE_LIMIT", "RClone"),
        (listener.is_ytdlp, "YTDLP_LIMIT", "YT-DLP"),
        (bool(yt_playlist), "PLAYLIST_LIMIT", "Playlist"),
        (True, "DIRECT_LIMIT", "Direct"),
    ]
    limit_exceeded = await recurr_limits(limits)
    daily_task_ok = True

    if not limit_exceeded:
        extra_limits = [
            (listener.is_leech, "LEECH_LIMIT", "Leech"),
            (listener.compress, "ARCHIVE_LIMIT", "Archive"),
            (listener.extract, "EXTRACT_LIMIT", "Extract"),
        ]
        limit_exceeded = await recurr_limits(extra_limits)

        if not listener.is_clone and size:
            limit = Config.STORAGE_LIMIT * 1024**3
            if not await check_storage_threshold(
                size, limit, any([listener.compress, listener.extract])
            ):
                required = limit + size * (
                    2 if any([listener.compress, listener.extract]) else 1
                )
                limit_exceeded = (
                    f" • <b>Required Disk:</b> {get_readable_file_size(required)}\n"
                    f" • <b>Storage Reserve:</b> {get_readable_file_size(limit)}\n"
                    " • <i>Insufficient disk space for this Task, use other bots</i>"
                )

        if Config.DAILY_TASK_LIMIT and Config.DAILY_TASK_LIMIT <= await getdailytasks(
            user_id
        ):
            limit_exceeded = (
                f" • <b>Daily Task Limit:</b> {Config.DAILY_TASK_LIMIT} tasks\n"
                f" • <i>You have exhausted all your Daily Task Limits</i>"
            )
            daily_task_ok = False

        if (DAILY_MIRROR_LIMIT := Config.DAILY_MIRROR_LIMIT) and not listener.is_leech:
            limit = DAILY_MIRROR_LIMIT * 1024**3
            if size >= (
                limit - await getdailytasks(user_id, check_mirror=True)
            ) or limit <= await getdailytasks(user_id, check_mirror=True):
                limit_exceeded = (
                    f" • <b>Daily Mirror Limit:</b> {get_readable_file_size(limit)}\n"
                    f" • <i>You have exhausted all your Daily Mirror Limit</i>"
                )
            elif not listener.is_leech:
                msize = await getdailytasks(user_id, upmirror=size, check_mirror=True)
                LOGGER.info(
                    f"User : {user_id} | Daily Mirror Size : {get_readable_file_size(msize)}"
                )

        if (DAILY_LEECH_LIMIT := Config.DAILY_LEECH_LIMIT) and listener.is_leech:
            limit = DAILY_LEECH_LIMIT * 1024**3
            if size >= (
                limit - await getdailytasks(user_id, check_leech=True)
            ) or limit <= await getdailytasks(user_id, check_leech=True):
                limit_exceeded = (
                    f" • <b>Daily Leech Limit:</b> {get_readable_file_size(limit)}\n"
                    f" • <i>You have exhausted all your Daily Leech Limit</i>"
                )
            elif listener.is_leech:
                lsize = await getdailytasks(user_id, upleech=size, check_leech=True)
                LOGGER.info(
                    f"User : {user_id} | Daily Leech Size : {get_readable_file_size(lsize)}"
                )

    if limit_exceeded:
        return limit_exceeded + f"\n • <b>Task By:</b> {listener.tag}"

    if (
        listener.user_id
        and Config.DAILY_TASK_LIMIT
        and daily_task_ok
    ):
        await getdailytasks(listener.user_id, increase_task=True)


async def user_interval_check(user_id):
    bot_cache.setdefault("time_interval", {})
    if (time_interval := bot_cache["time_interval"].get(user_id, False)) and (
        time() - time_interval
    ) < (UTI := Config.USER_TIME_INTERVAL):
        return UTI - (time() - time_interval)
    bot_cache["time_interval"][user_id] = time()
    return None


async def pre_task_check(message):
    LOGGER.info("Running Pre Task Checks ...")
    msg = []
    button = None
    if await CustomFilters.sudo("", message):
        return msg, button
    sender = message.from_user or message.sender_chat
    if sender is None:
        return ["Could not identify task sender."], None
    user_id = sender.id
    if Config.RSS_CHAT and user_id == int(Config.RSS_CHAT):
        return msg, button
    user_dict = user_data.get(user_id, {})
    if message.chat.type != ChatType.BOT:
        if ids := Config.FORCE_SUB_IDS:
            _msg, button = await forcesub(message, ids, button)
            if _msg:
                msg.append(_msg)
        if Config.BOT_PM or user_dict.get("BOT_PM"):
            _msg, button = await check_botpm(message, button)
            if _msg:
                msg.append(_msg)
    if (uti := Config.USER_TIME_INTERVAL) and (
        ut := await user_interval_check(user_id)
    ):
        msg.append(
            f" • <b>Waiting Time:</b> {get_readable_time(ut)}\n • <i>User's Time Interval Restrictions:</i> {get_readable_time(uti)}"
        )
    bmax_tasks = safe_int(user_dict.get("bmax_tasks", Config.BOT_MAX_TASKS))
    bot_tasks_count = len(await get_specific_tasks("All", False))
    bot_limit_reached = False
    if bmax_tasks > 0 and bot_tasks_count >= bmax_tasks:
        bot_limit_reached = True
        msg.append(
            f" • <b>Bot Tasks Limit Reached:</b> {bot_tasks_count} / {bmax_tasks} tasks"
        )

    maxtask = safe_int(user_dict.get("maxtask", Config.USER_MAX_TASKS))
    user_limit_reached = False
    if maxtask > 0:
        user_tasks_count = len(await get_specific_tasks("All", user_id))
        if user_tasks_count >= maxtask:
            user_limit_reached = True
            msg.append(
                f" • <b>User Tasks Limit Reached:</b> {user_tasks_count} / {maxtask} tasks"
            )

    if user_limit_reached or bot_limit_reached:
        msg.append("<blockquote>Use other bots to add tasks.</blockquote>")

    token_msg, button = await verify_token(user_id, button)
    if token_msg is not None:
        msg.append(token_msg)

    if msg:
        username = message.from_user.mention
        final_msg = f"<blockquote><b><i>✦ Task Checks</i></b></blockquote>\n • <b>Name:</b> {username}\n"
        final_msg += "\n".join(msg)
        if button is not None:
            button = button.build_menu(2)
        return final_msg, button

    return None, None


async def register_task_for_limit_check(user_id, message):
    from bot.core.tg_client import TgClient

    mid = message.id
    bot_id = TgClient.ID

    try:
        user_dict = user_data.get(user_id, {})
        universal_maxtask = await database.get_universal_max_tasks()
        user_override = safe_int(user_dict.get("universal_maxtask", 0))
        if user_override > 0:
            universal_maxtask = user_override

        if universal_maxtask <= 0:
            await database.add_shared_task(user_id, mid, bot_id, "")
            return True, None

        acquired, current_count = await database.acquire_universal_task_slot(
            user_id, mid, bot_id, universal_maxtask
        )

        if not acquired:
            LOGGER.info(
                f"User {user_id} at universal task limit ({current_count}/{universal_maxtask}). "
                "Attempting orphan cleanup..."
            )
            cleaned = await database.cleanup_orphaned_tasks(user_id)
            if cleaned > 0:
                acquired, current_count = await database.acquire_universal_task_slot(
                    user_id, mid, bot_id, universal_maxtask
                )

        if not acquired:
            username = (message.from_user or message.sender_chat).mention
            limit_msg = (
                "<blockquote><b><i>✦ Task Checks</i></b></blockquote>\n"
                f" • <b>Name:</b> {username}\n"
                f" • <b>Universal Tasks Limit Reached:</b> {current_count} / {universal_maxtask} tasks\n"
                " • <i>Please wait for a task to complete.</i>"
            )
            return False, limit_msg

        try:
            await database.add_shared_task(user_id, mid, bot_id, "")
        except Exception:
            await database.release_universal_task_slot(user_id, mid, bot_id)
            raise

        LOGGER.debug(f"Registered task {mid} for universal limit check. User: {user_id}")
    except Exception as e:
        LOGGER.error(f"Failed to register task {mid} for limit check: {e}")
        return False, "Internal error while registering task. Please try again."

    return True, None


async def unregister_task_on_error(message_id):
    from bot.core.tg_client import TgClient

    bot_id = TgClient.ID

    try:
        await database.remove_shared_task(message_id, bot_id)
        LOGGER.debug(f"Unregistered task {message_id} due to setup error")
    except Exception as e:
        LOGGER.error(f"Failed to unregister task {message_id}: {e}")
