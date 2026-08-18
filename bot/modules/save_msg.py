# This file is a part of NEO-WZML (github.com/irisXDR/NEO-WZML)

from pyrogram.filters import regex
from pyrogram.handlers import CallbackQueryHandler

from bot import user_data
from bot.helper.telegram_helper.button_build import ButtonMaker


async def save_message(_, query):
    user_id = query.from_user.id
    user_dict = user_data.get(user_id, {})
    data = query.data.split()

    if len(data) < 2:
        await query.answer("Invalid save format!", show_alert=True)
        return

    target = data[1]

    if target.lower() == "pm":
        target_id = user_id
    elif target.startswith("-100") or target.startswith("@"):
        if target.lstrip("-").isdigit():
            target_id = int(target)
        else:
            target_id = target
    else:
        await query.answer("Invalid target!", show_alert=True)
        return

    try:
        buttons = ButtonMaker()
        original_markup = query.message.reply_markup
        if original_markup and original_markup.inline_keyboard:
            for row in original_markup.inline_keyboard:
                for btn in row:
                    if btn.callback_data and not btn.callback_data.startswith("save"):
                        buttons.data_button(
                            btn.text,
                            btn.callback_data
                        )
                    elif btn.url:
                        buttons.url_button(
                            btn.text,
                            btn.url
                        )
            new_markup = buttons.build_menu(2) if buttons.buttons else None
        else:
            new_markup = None

        await query.message.copy(
            target_id,
            reply_markup=new_markup
        )
        await query.answer("Message Saved Successfully!", show_alert=True)
    except Exception as e:
        from bot import LOGGER
        LOGGER.error(f"Failed to save message: {e}")
        await query.answer(
            f"Failed to save: {str(e)}",
            show_alert=True
        )


save_handler = CallbackQueryHandler(
    save_message,
    filters=regex("^save"),
)
