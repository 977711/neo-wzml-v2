# This file is a part of NEO-WZML (github.com/irisXDR/NEO-WZML)

from pyrogram.filters import create
from pyrogram.enums import ChatType
from pyrogram.types import CallbackQuery

from bot import auth_chats, sudo_users, user_data
from bot.core.config_manager import Config
from bot.helper.telegram_helper.tg_utils import chat_info


def _message_of(update):
    # Handle both Message and CallbackQuery uniformly.
    if isinstance(update, CallbackQuery):
        return update.message
    return update


class CustomFilters:
    async def owner_filter(self, _, update):
        user = update.from_user or getattr(update, "sender_chat", None)
        if user is None:
            return False
        return user.id == Config.OWNER_ID

    owner = create(owner_filter)

    async def authorized_user(self, _, update):
        user = update.from_user or getattr(update, "sender_chat", None)
        if user is None:
            return False
        uid = user.id
        msg = _message_of(update)
        chat_id = msg.chat.id if msg is not None and msg.chat else 0
        thread_id = (
            msg.message_thread_id
            if msg is not None
            and getattr(msg, "is_topic_message", False)
            else None
        )

        if Config.STRICT_AUTH_MODE:
            return bool(
                uid == Config.OWNER_ID
                or uid in sudo_users
                or (
                    uid in user_data
                    and (
                        user_data[uid].get("AUTH", False)
                        or user_data[uid].get("SUDO", False)
                    )
                )
            )

        return bool(
            uid == Config.OWNER_ID
            or (
                uid in user_data
                and (
                    user_data[uid].get("AUTH", False)
                    or user_data[uid].get("SUDO", False)
                )
            )
            or (
                chat_id in user_data
                and user_data[chat_id].get("AUTH", False)
                and (
                    thread_id is None
                    or thread_id in user_data[chat_id].get("thread_ids", [])
                )
            )
            or uid in sudo_users
            or uid in auth_chats
            or chat_id in auth_chats
            and (
                auth_chats[chat_id]
                and thread_id
                and thread_id in auth_chats[chat_id]
                or not auth_chats[chat_id]
            )
        )

    authorized = create(authorized_user)

    async def authorized_usetting(self, _, update):
        user = update.from_user or getattr(update, "sender_chat", None)
        if user is None:
            return False
        uid = user.id
        is_exists = False
        if await CustomFilters.authorized("", update):
            is_exists = True
        else:
            msg = _message_of(update)
            chat = getattr(msg, "chat", None)
            if chat is not None and chat.type == ChatType.PRIVATE:
                for channel_id in user_data:
                    if not (
                        user_data[channel_id].get("is_auth")
                        and str(channel_id).startswith("-100")
                    ):
                        continue
                    try:
                        if await (await chat_info(str(channel_id))).get_member(uid):
                            is_exists = True
                            break
                    except Exception:
                        continue
        return is_exists

    authorized_uset = create(authorized_usetting)

    async def sudo_user(self, _, update):
        user = update.from_user or getattr(update, "sender_chat", None)
        if user is None:
            return False
        uid = user.id
        return bool(
            uid == Config.OWNER_ID
            or uid in user_data
            and user_data[uid].get("SUDO")
            or uid in sudo_users
        )

    sudo = create(sudo_user)
