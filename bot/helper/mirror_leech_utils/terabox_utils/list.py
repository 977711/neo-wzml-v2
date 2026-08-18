# This file is a part of NEO-WZML (github.com/irisXDR/NEO-WZML)

import os
from asyncio import wait_for, Event
from functools import partial
from time import time

from pyrogram.filters import regex, user
from pyrogram.handlers import CallbackQueryHandler

from bot.helper.ext_utils.bot_utils import new_task
from bot.helper.ext_utils.status_utils import (
    get_readable_file_size,
    get_readable_time,
)
from bot.helper.telegram_helper.button_build import ButtonMaker
from bot.helper.telegram_helper.message_utils import (
    send_message,
    edit_message,
    delete_message,
)

try:
    from terabox import TeraboxClient, TeraboxError
    _TERABOX_AVAILABLE = True
except ImportError:  # pragma: no cover - SDK only present in the built image
    _TERABOX_AVAILABLE = False

LIST_LIMIT = 8


@new_task
async def terabox_cookie_updates(_, query, obj):
    await query.answer()
    data = query.data.split()
    if len(data) < 2:
        return
    if data[1] == "cancel":
        obj.error = "Task has been cancelled!"
        obj.listener.is_cancelled = True
        obj.event.set()
        await delete_message(query.message)
        obj._reply_to = None
        return
    if data[1] == "user":
        obj.cookie_path = obj.user_cookie
        obj.cookie_label = "User Cookie"
    elif data[1] == "owner":
        obj.cookie_path = obj.owner_cookie
        obj.cookie_label = "Owner Cookie"
    else:
        return
    await delete_message(query.message)
    obj._reply_to = None
    obj.event.set()


class TeraboxCookieSelector:
    def __init__(self, listener, *, purpose: str):
        self.listener = listener
        self.purpose = purpose
        self.user_cookie = f"terabox_cookies/{listener.user_id}.txt"
        self.owner_cookie = "terabox.txt"
        self.cookie_path = ""
        self.cookie_label = ""
        self.error = ""
        self.event = Event()
        self._reply_to = None
        self._timeout = 120
        self._time = time()

    async def _event_handler(self):
        pfunc = partial(terabox_cookie_updates, obj=self)
        handler = self.listener.client.add_handler(
            CallbackQueryHandler(
                pfunc, filters=regex("^tbc") & user(self.listener.user_id)
            ),
            group=-1,
        )
        try:
            await wait_for(self.event.wait(), timeout=self._timeout)
        except Exception:
            self.error = "Timed Out. Task has been cancelled!"
            self.listener.is_cancelled = True
            self.event.set()
        finally:
            self.listener.client.remove_handler(*handler)

    async def select(self):
        from aiofiles.os import path as aiopath

        has_user = await aiopath.exists(self.user_cookie)
        has_owner = await aiopath.exists(self.owner_cookie)
        if has_user and not has_owner:
            self.cookie_path = self.user_cookie
            self.cookie_label = "User Cookie"
            return self.cookie_path
        if has_owner and not has_user:
            self.cookie_path = self.owner_cookie
            self.cookie_label = "Owner Cookie"
            return self.cookie_path
        if not has_user and not has_owner:
            self.error = (
                "No TeraBox cookie found. Upload your terabox.txt in User "
                "Settings, or have the owner add a global one."
            )
            return ""

        buttons = ButtonMaker()
        buttons.data_button("User Cookie", "tbc user")
        buttons.data_button("Owner Cookie", "tbc owner")
        buttons.data_button("Cancel", "tbc cancel", position="footer")
        msg = (
            "Choose TeraBox cookie source:"
            f"\nTransfer Type: <i>{self.purpose}</i>"
            "\n\n<b>User Cookie</b>: your personal TeraBox account."
            "\n<b>Owner Cookie</b>: the bot owner's global TeraBox account."
            f"\n\nTimeout: {get_readable_time(self._timeout - (time() - self._time))}"
        )
        self._reply_to = await send_message(self.listener.message, msg, buttons.build_menu(2))
        await self._event_handler()
        if self._reply_to is not None:
            await delete_message(self._reply_to)
        return self.cookie_path


@new_task
async def terabox_path_updates(_, query, obj):
    await query.answer()
    message = query.message
    data = query.data.split()
    if data[1] == "cancel":
        obj.error = "Task has been cancelled!"
        obj.selection = []
        obj.listener.is_cancelled = True
        obj.event.set()
        await delete_message(message)
        return
    if obj.query_proc:
        return
    obj.query_proc = True
    if data[1] == "pre":
        obj.iter_start -= LIST_LIMIT * obj.page_step
        await obj.get_path_buttons()
    elif data[1] == "nex":
        obj.iter_start += LIST_LIMIT * obj.page_step
        await obj.get_path_buttons()
    elif data[1] == "ps":
        if obj.page_step != int(data[2]):
            obj.page_step = int(data[2])
            await obj.get_path_buttons()
    elif data[1] == "sel":
        obj.select = not obj.select
        await obj.get_path_buttons()
    elif data[1] == "clr":
        obj.selected = {}
        await obj.get_path_buttons()
    elif data[1] == "back":
        await obj.back_from_path()
    elif data[1] == "root":
        obj.path = "/"
        await obj.get_path()
    elif data[1] == "pa":
        index = int(data[3])
        entry = obj.path_list[index]
        if obj.select:
            if entry.path in obj.selected:
                del obj.selected[entry.path]
            else:
                obj.selected[entry.path] = obj._meta(entry)
            await obj.get_path_buttons()
        elif data[2] == "fo":
            obj.path = entry.path
            await obj.get_path()
        else:
            obj.selection = [obj._meta(entry)]
            await delete_message(message)
            obj.event.set()
    elif data[1] == "cur":
        name = os.path.basename(obj.path.rstrip("/")) or "TeraBox"
        obj.selection = [
            {"path": obj.path or "/", "name": name, "size": 0, "is_dir": True}
        ]
        await delete_message(message)
        obj.event.set()
    elif data[1] == "dl":
        obj.selection = list(obj.selected.values())
        await delete_message(message)
        obj.event.set()
    obj.query_proc = False


class TeraboxList:
    def __init__(self, listener):
        self.listener = listener
        self.client = None
        self.event = Event()
        self._reply_to = None
        self._time = time()
        self._timeout = 240
        self.query_proc = False
        self.path = "/"
        self.path_list = []
        self.iter_start = 0
        self.page_step = 1
        self.select = False
        self.selected = {}
        self.selection = []
        self.error = ""

    @staticmethod
    def _meta(entry):
        return {
            "path": entry.path,
            "name": entry.name,
            "size": entry.size,
            "is_dir": entry.is_dir,
        }

    async def _event_handler(self):
        pfunc = partial(terabox_path_updates, obj=self)
        handler = self.listener.client.add_handler(
            CallbackQueryHandler(
                pfunc, filters=regex("^tbq") & user(self.listener.user_id)
            ),
            group=-1,
        )
        try:
            await wait_for(self.event.wait(), timeout=self._timeout)
        except Exception:
            self.selection = []
            self.error = "Timed Out. Task has been cancelled!"
            self.listener.is_cancelled = True
            self.event.set()
        finally:
            self.listener.client.remove_handler(*handler)

    async def _send_list_message(self, msg, button):
        if not self.listener.is_cancelled:
            if self._reply_to is None:
                self._reply_to = await send_message(self.listener.message, msg, button)
            else:
                await edit_message(self._reply_to, msg, button)

    async def get_path_buttons(self):
        items_no = len(self.path_list)
        pages = (items_no + LIST_LIMIT - 1) // LIST_LIMIT
        if items_no <= self.iter_start:
            self.iter_start = 0
        elif self.iter_start < 0 or self.iter_start > items_no:
            self.iter_start = LIST_LIMIT * (pages - 1)
        page = (self.iter_start / LIST_LIMIT) + 1 if self.iter_start != 0 else 1
        buttons = ButtonMaker()
        for index, entry in enumerate(
            self.path_list[self.iter_start : LIST_LIMIT + self.iter_start]
        ):
            orig_index = index + self.iter_start
            name = entry.name
            if entry.is_dir:
                ptype = "fo"
                name = f"📁 {name}"
            else:
                ptype = "fi"
                name = f"[{get_readable_file_size(entry.size)}] {name}"
            if self.select and entry.path in self.selected:
                name = f"✅ {name}"
            buttons.data_button(name, f"tbq pa {ptype} {orig_index}")
        if items_no > LIST_LIMIT:
            for i in [1, 2, 4, 6, 10, 30, 50, 100]:
                buttons.data_button(i, f"tbq ps {i}", position="header")
            buttons.data_button("Previous", "tbq pre", position="footer")
            buttons.data_button("Next", "tbq nex", position="footer")
        buttons.data_button("Download This Folder", "tbq cur", position="footer")
        buttons.data_button(
            f"Select: {'Enabled' if self.select else 'Disabled'}",
            "tbq sel",
            position="footer",
        )
        if self.selected:
            buttons.data_button(
                f"Download Selected ({len(self.selected)})", "tbq dl", position="footer"
            )
            buttons.data_button("Clear Selection", "tbq clr", position="footer")
        if self.path not in ("", "/"):
            buttons.data_button("Back", "tbq back", position="footer")
            buttons.data_button("Back To Root", "tbq root", position="footer")
        buttons.data_button("Cancel", "tbq cancel", position="footer")
        button = buttons.build_menu(f_cols=2)
        msg = "Choose a TeraBox file or folder to leech/mirror:"
        msg += f"\n\nItems: {items_no}"
        if items_no > LIST_LIMIT:
            msg += f" | Page: {int(page)}/{pages} | Step: {self.page_step}"
        if self.select:
            msg += "\n<i>Select mode ON — tap items to (de)select.</i>"
        msg += f"\n\nCurrent Path: <code>{self.path}</code>"
        msg += f"\nTimeout: {get_readable_time(self._timeout - (time() - self._time))}"
        await self._send_list_message(msg, button)

    async def get_path(self):
        if self.listener.is_cancelled:
            return
        try:
            entries = await self.client.list_account_dir(self.path or "/")
        except Exception as e:
            self.error = f"TeraBox listing failed: {e}"
            self.selection = []
            self.event.set()
            return
        self.path_list = sorted(
            entries, key=lambda x: (not x.is_dir, x.name.lower())
        )
        self.iter_start = 0
        await self.get_path_buttons()

    async def back_from_path(self):
        parent = os.path.dirname(self.path.rstrip("/"))
        self.path = parent or "/"
        await self.get_path()

    async def get_terabox_path(self):
        if not _TERABOX_AVAILABLE:
            self.error = (
                "teraboxSDK is not installed in this image. Rebuild the base "
                "image to enable TeraBox browsing."
            )
            return []
        cookie = self.listener.terabox_cookie or await self.listener._terabox_cookie_path()
        if not cookie:
            self.error = (
                "No TeraBox cookie found. Upload your terabox.txt in User "
                "Settings, or have the owner add a global one in Bot Settings."
            )
            return []
        self.listener.terabox_cookie = cookie
        self.client = TeraboxClient(cookie_file=os.path.abspath(cookie))
        try:
            try:
                await self.client.login()
            except TeraboxError as e:
                self.error = f"TeraBox login failed: {e}"
                return []
            await self.get_path()
            if not self.event.is_set():
                await self._event_handler()
        finally:
            await self.client.aclose()
        if self._reply_to is not None:
            await delete_message(self._reply_to)
        return self.selection
