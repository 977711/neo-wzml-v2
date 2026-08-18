# This file is a part of NEO-WZML (github.com/irisXDR/NEO-WZML)

from bot.helper.ext_utils.bot_utils import new_task, sync_to_async
from bot.helper.ext_utils.links_utils import is_gdrive_link
from bot.helper.ext_utils.status_utils import get_readable_file_size
from bot.helper.mirror_leech_utils.gdrive_utils.count import GoogleDriveCount
from bot.helper.mirror_leech_utils.gdrive_utils.clean import GoogleDriveClean
from bot.helper.telegram_helper.button_build import ButtonMaker
from bot.helper.telegram_helper.filters import CustomFilters
from bot.helper.telegram_helper.message_utils import edit_message, send_message
from bot.core.config_manager import Config


@new_task
async def gdclean_node(_, message):
    args = message.text.split()
    if len(args) > 1:
        link = args[1].strip()
    elif reply_to := message.reply_to_message:
        link = reply_to.text.split(maxsplit=1)[0].strip()
    else:
        link = f"https://drive.google.com/drive/folders/{Config.GDRIVE_ID}"

    if not is_gdrive_link(link):
        return await send_message(
            message, "Send Gdrive link along with command or by replying to the link by command"
        )

    msg = await send_message(message, "<i>Fetching ...</i>")
    gd = GoogleDriveCount()
    name, mime_type, size, files, folders = await sync_to_async(
        gd.count, link, message.from_user.id
    )

    if mime_type is None:
        await edit_message(msg, name)
        return

    try:
        drive_id = gd.get_id_from_url(link, message.from_user.id)
    except (KeyError, IndexError):
        return await send_message(
            message, "Google Drive ID could not be found in the provided link"
        )

    buttons = ButtonMaker()
    buttons.data_button("Move to Bin", f"gdclean clear {drive_id} trash")
    buttons.data_button("Permanent Clean", f"gdclean clear {drive_id}")
    buttons.data_button("Stop", f"gdclean stop", "footer")

    text = f"""<b>Google Drive Clean/Trash</b>

<b>Name:</b> {name}
<b>Size:</b> {get_readable_file_size(size)}
<b>Files:</b> {files} | <b>Folders:</b> {folders}

<b>NOTES:</b>
1. All files are permanently deleted if Permanent Del, not moved to trash.
2. Folder doesn't gets Deleted.
3. Delete files of custom folder via giving link along with cmd, but it should have delete permissions.
4. Move to Bin Moves all your files to trash but can be restored again if have permissions.

<code>Choose the Required Action below to Clean your Drive!</code>"""

    await edit_message(msg, text, buttons.build_menu(2))


@new_task
async def gdclean_callback(_, query):
    message = query.message
    user_id = query.from_user.id
    data = query.data.split()

    if user_id != Config.OWNER_ID:
        await query.answer(text="Not Owner!", show_alert=True)
        return

    if data[1] == "clear":
        await query.answer()
        await edit_message(message, "<i>Processing Drive Clean / Trash...</i>")
        drive = GoogleDriveClean()
        trash_mode = len(data) == 4 and data[3] == "trash"
        msg = await sync_to_async(drive.driveclean, data[2], trash_mode, user_id)
        await edit_message(message, msg)
    elif data[1] == "stop":
        await query.answer()
        await edit_message(message, "<b>DriveClean Stopped!</b>")
