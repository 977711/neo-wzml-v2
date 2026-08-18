# This file is a part of NEO-WZML (github.com/irisXDR/NEO-WZML)

from pyrogram.filters import regex
from pyrogram.handlers import CallbackQueryHandler

from bot import bot_cache
from bot.core.tg_client import TgClient
from bot.helper.ext_utils.bot_utils import fetch_user_dumps
from bot.helper.telegram_helper.message_utils import edit_message


async def dump_category_callback(_, query):
    message = query.message
    user_id = query.from_user.id
    data = query.data.split()

    if user_id != int(data[1]):
        return await query.answer("Not Yours!", show_alert=True)

    msg_id = int(data[2])
    if msg_id not in bot_cache:
        return await query.answer("Session expired!", show_alert=True)

    action = data[3]

    if action == "dcancel":
        bot_cache[msg_id][2] = True
        await query.answer("Cancelled!")
        return

    if action == "ddone":
        if not bot_cache[msg_id][0]:
            return await query.answer("Select at least 1 dump!", show_alert=True)
        bot_cache[msg_id][1] = True
        await query.answer("Selection confirmed!")
        return

    if action == "All":
        ldumps = await fetch_user_dumps(user_id)
        bot_cache[msg_id][0] = list(ldumps.values())
        selected_names = list(ldumps.keys())
        await query.answer("All dumps selected!")
        bot_cache[msg_id][1] = True
        sel_text = "\n".join(f"☑ {n}" for n in selected_names)
        await edit_message(
            message,
            f"<b>All dumps selected</b>\n\n{sel_text}\n\n<i>Starting task...</i>",
        )
        return

    from bot.helper.telegram_helper.button_build import ButtonMaker

    dump_name = action.replace("_", " ")
    ldumps = await fetch_user_dumps(user_id)
    if dump_name not in ldumps:
        return await query.answer("Dump not found!", show_alert=True)

    selected = bot_cache[msg_id][0]
    dump_id = ldumps[dump_name]

    if dump_id in selected:
        selected.remove(dump_id)
        await query.answer(f"Removed: {dump_name}")
    else:
        if len(selected) >= 3:
            return await query.answer("Max 3 dumps allowed!", show_alert=True)
        selected.append(dump_id)
        await query.answer(f"Added: {dump_name}")

    buttons = ButtonMaker()
    for _name in ldumps.keys():
        prefix = "☑" if ldumps[_name] in selected else "☐"
        buttons.data_button(
            f"{prefix} {_name}",
            f"dcat {user_id} {msg_id} {_name.replace(' ', '_')}",
        )

    buttons.data_button("Select All", f"dcat {user_id} {msg_id} All", "header")
    buttons.data_button("Cancel", f"dcat {user_id} {msg_id} dcancel", "footer")
    buttons.data_button("Done (60)", f"dcat {user_id} {msg_id} ddone", "footer")

    sel_names = [n for n, cid in ldumps.items() if cid in selected]
    sel_display = "\n".join(f"☑ {n}" for n in sel_names) if sel_names else "None"
    await edit_message(
        message,
        f"<b>Select up to 3 dump categories (click to toggle)</b>\n\n"
        f"<i>Selected ({len(selected)}/3):</i>\n{sel_display}\n\n"
        f"<i>Timeout will use ALL dumps if nothing selected.</i>",
        buttons.build_menu(3),
    )


TgClient.bot.add_handler(
    CallbackQueryHandler(dump_category_callback, filters=regex(r"^dcat"))
)
