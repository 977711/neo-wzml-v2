# This file is a part of NEO-WZML (github.com/irisXDR/NEO-WZML)

from aiofiles.os import remove, path as aiopath
from asyncio import iscoroutinefunction

from bot import (
    task_dict,
    task_dict_lock,
    user_data,
    LOGGER,
)
from bot.core.config_manager import Config
from bot.core.torrent_manager import TorrentManager
from bot.helper.ext_utils.bot_utils import (
    bt_selection_buttons,
    new_task,
)
from bot.helper.ext_utils.task_manager import limit_checker
from bot.helper.ext_utils.status_utils import get_task_by_gid, MirrorStatus
from bot.helper.telegram_helper.message_utils import (
    send_message,
    send_status_message,
    delete_message,
)


@new_task
async def select(_, message):
    if not Config.BASE_URL:
        await send_message(message, "Base URL not defined!")
        return
    sender = message.from_user or message.sender_chat
    if sender is None:
        await send_message(message, "Could not identify sender.")
        return
    user_id = sender.id
    msg = message.text.split("_", maxsplit=1)
    if len(msg) > 1:
        gid = msg[1].split("@", maxsplit=1)[0]
        task = await get_task_by_gid(gid)
        if task is None:
            await send_message(message, f"GID: <code>{gid}</code> Not Found.")
            return
    elif reply_to_id := message.reply_to_message_id:
        async with task_dict_lock:
            task = task_dict.get(reply_to_id)
        if task is None:
            await send_message(message, "This is not an active task!")
            return
    elif len(msg) == 1:
        msg = (
            "Reply to an active /cmd which was used to start the download or add gid along with cmd\n\n"
            + "This command mainly for selection incase you decided to select files from already added torrent. "
            + "But you can always use /cmd with arg `s` to select files before download start."
        )
        await send_message(message, msg)
        return
    if (
        Config.OWNER_ID != user_id
        and task.listener.user_id != user_id
        and (user_id not in user_data or not user_data[user_id].get("SUDO"))
    ):
        await send_message(message, "This task is not for you!")
        return
    if not iscoroutinefunction(task.status):
        await send_message(message, "The task have finshed the download stage!")
        return
    if await task.status() not in [
        MirrorStatus.STATUS_DOWNLOAD,
        MirrorStatus.STATUS_PAUSED,
        MirrorStatus.STATUS_QUEUEDL,
    ]:
        await send_message(
            message,
            "Task should be in download or pause (incase message deleted by wrong) or queued status (incase you have used torrent file)!",
        )
        return
    if task.name().startswith("[METADATA]") or task.name().startswith("Trying"):
        await send_message(message, "Try after downloading metadata finished!")
        return

    try:
        if task.listener.is_qbit:
            id_ = task.hash()
        else:
            id_ = task.gid()

        if not task.queued:
            await task.update()
            if task.listener.is_qbit:
                await TorrentManager.qbittorrent.torrents.stop([id_])
            else:
                try:
                    await TorrentManager.aria2.forcePause(id_)
                except Exception as e:
                    LOGGER.error(
                        f"{e} Error in pause, this mostly happens after abuse aria2"
                    )
        task.listener.select = True
    except Exception:
        await send_message(message, "This is not a bittorrent task!")
        return

    SBUTTONS = bt_selection_buttons(id_)
    msg = "Your download paused. Choose files then press Done Selecting button to resume downloading."
    await send_message(message, msg, SBUTTONS)


@new_task
async def confirm_selection(_, query):
    sender = query.from_user or getattr(query, "sender_chat", None)
    if sender is None:
        await query.answer("Could not identify sender.", show_alert=True)
        return
    user_id = sender.id
    data = query.data.split()
    if len(data) < 3:
        await query.answer("Malformed selection request.", show_alert=True)
        return
    message = query.message

    if data[2].startswith("mega_"):
        from bot.helper.mirror_leech_utils.download_utils.mega_download import (
            resume_mega_with_selection,
            cancel_mega_selection,
            get_mega_selection_owner_id,
        )
        real_gid = data[2].replace("mega_", "", 1)
        owner_id = get_mega_selection_owner_id(real_gid)
        if owner_id is None:
            await query.answer("This task has been cancelled!", show_alert=True)
            await delete_message(message)
            return
        if user_id != owner_id:
            await query.answer("This task is not for you!", show_alert=True)
            return
        if data[1] == "pin":
            if len(data) >= 4:
                await query.answer(data[3], show_alert=True)
            else:
                await query.answer("Missing PIN value.", show_alert=True)
        elif data[1] == "done":
            await query.answer()
            await resume_mega_with_selection(real_gid)
            await delete_message(message)
        else:
            await delete_message(message)
            await cancel_mega_selection(real_gid)
        return

    if data[2].startswith("terabox_"):
        from bot.helper.mirror_leech_utils.download_utils.terabox_download import (
            resume_terabox_with_selection,
            cancel_terabox_selection,
            get_terabox_selection_owner_id,
        )
        real_gid = data[2].replace("terabox_", "", 1)
        owner_id = get_terabox_selection_owner_id(real_gid)
        if owner_id is None:
            await query.answer("This task has been cancelled!", show_alert=True)
            await delete_message(message)
            return
        if user_id != owner_id:
            await query.answer("This task is not for you!", show_alert=True)
            return
        if data[1] == "pin":
            if len(data) >= 4:
                await query.answer(data[3], show_alert=True)
            else:
                await query.answer("Missing PIN value.", show_alert=True)
        elif data[1] == "done":
            await query.answer()
            await resume_terabox_with_selection(real_gid)
            await delete_message(message)
        else:
            await delete_message(message)
            await cancel_terabox_selection(real_gid)
        return

    if data[2].startswith("rclone_"):
        from bot.helper.mirror_leech_utils.download_utils.rclone_download import (
            resume_rclone_with_selection,
            cancel_rclone_selection,
            get_rclone_selection_owner_id,
        )
        real_gid = data[2].replace("rclone_", "", 1)
        owner_id = get_rclone_selection_owner_id(real_gid)
        if owner_id is None:
            await query.answer("This task has been cancelled!", show_alert=True)
            await delete_message(message)
            return
        if user_id != owner_id:
            await query.answer("This task is not for you!", show_alert=True)
            return
        if data[1] == "pin":
            if len(data) >= 4:
                await query.answer(data[3], show_alert=True)
            else:
                await query.answer("Missing PIN value.", show_alert=True)
        elif data[1] == "done":
            await query.answer()
            await resume_rclone_with_selection(real_gid)
            await delete_message(message)
        else:
            await delete_message(message)
            await cancel_rclone_selection(real_gid)
        return

    task = await get_task_by_gid(data[2])
    if task is None:
        await query.answer("This task has been cancelled!", show_alert=True)
        await delete_message(message)
        return
    if user_id != task.listener.user_id:
        await query.answer("This task is not for you!", show_alert=True)
    elif data[1] == "pin":
        if len(data) >= 4:
            await query.answer(data[3], show_alert=True)
        else:
            await query.answer("Missing PIN value.", show_alert=True)
    elif data[1] == "done":
        if len(data) < 4:
            await query.answer("Missing torrent id.", show_alert=True)
            return
        await query.answer()
        id_ = data[3]
        if hasattr(task, "seeding"):
            if task.listener.is_qbit:
                tor_info = (
                    await TorrentManager.qbittorrent.torrents.info(hashes=[id_])
                )[0]
                path = tor_info.content_path.rsplit("/", 1)[0]
                res = await TorrentManager.qbittorrent.torrents.files(id_)
                for f in res:
                    if f.priority == 0:
                        f_paths = [f"{path}/{f.name}", f"{path}/{f.name}.!qB"]
                        for f_path in f_paths:
                            if await aiopath.exists(f_path):
                                try:
                                    await remove(f_path)
                                except Exception:
                                    pass
                if not task.queued:
                    await TorrentManager.qbittorrent.torrents.start([id_])
            else:
                res = await TorrentManager.aria2.getFiles(id_)
                for f in res:
                    if f["selected"] == "false" and await aiopath.exists(f["path"]):
                        try:
                            await remove(f["path"])
                        except Exception:
                            pass
                if not task.queued:
                    try:
                        await TorrentManager.aria2.unpause(id_)
                    except Exception as e:
                        LOGGER.error(
                            f"{e} Error in resume, this mostly happens after abuse aria2. Try to use select cmd again!"
                        )

        try:
            if task.listener.is_qbit:
                res = await TorrentManager.qbittorrent.torrents.files(id_)
                size = sum(f.size for f in res if f.priority != 0)
            else:
                res = await TorrentManager.aria2.getFiles(id_)
                size = sum(int(f["length"]) for f in res if f["selected"] == "true")
            
            task.listener.size = size
            if limit_exceeded := await limit_checker(task.listener):
                await task.listener.on_download_error(limit_exceeded, is_limit=True)
                await delete_message(message)
                return
        except Exception as e:
            LOGGER.error(f"Error checking limit in confirm_selection: {e}")

        await send_status_message(message)
        await delete_message(message)
    else:
        await delete_message(message)
        await task.cancel_task()
