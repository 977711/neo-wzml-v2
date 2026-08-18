# This file is a part of NEO-WZML (github.com/irisXDR/NEO-WZML)

from aiofiles import open as aiopen
from aiofiles.os import remove as aioremove
from contextlib import redirect_stdout, suppress
from io import StringIO, BytesIO
from os import path as ospath, getcwd, chdir
from secrets import token_hex
from textwrap import indent
from traceback import format_exc
from re import match

from bot import LOGGER
from bot.core.tg_client import TgClient
from bot.helper.ext_utils.bot_utils import sync_to_async, new_task
from bot.helper.telegram_helper.message_utils import send_file, send_message

namespaces = {}


def _ns_key(message):
    sender = message.from_user or message.sender_chat
    sender_id = sender.id if sender is not None else 0
    return (message.chat.id, sender_id)


def namespace_of(message):
    key = _ns_key(message)
    if key not in namespaces:
        namespaces[key] = {
            "__name__": "__main__",
            "__file__": "<exec>",
            "__builtins__": globals()["__builtins__"],
            "bot": TgClient.bot,
            "message": message,
            "user": message.from_user or message.sender_chat,
            "chat": message.chat,
        }

    return namespaces[key]


def log_input(message):
    LOGGER.info(
        f"IN: {message.text} (user={(message.from_user or message.sender_chat).id}, chat={message.chat.id})"
    )


async def send(msg, message):
    if len(str(msg)) > 2000:
        with BytesIO(str.encode(msg)) as out_file:
            out_file.name = "output.txt"
            await send_file(message, out_file)
    else:
        LOGGER.info(f"OUT: '{msg}'")
        if not msg or msg == "\n":
            msg = "MessageEmpty"
        elif not bool(match(r"<(spoiler|b|i|code|s|u|/a)>", msg)):
            msg = f"<code>{msg}</code>"
        await send_message(message, msg)


@new_task
async def aioexecute(_, message):
    await send(await do("aexec", message), message)


@new_task
async def execute(_, message):
    await send(await do("exec", message), message)


def cleanup_code(code):
    if code.startswith("```") and code.endswith("```"):
        return "\n".join(code.split("\n")[1:-1])
    return code.strip("` \n")


async def do(func, message):
    log_input(message)
    content = message.text.split(maxsplit=1)[-1]
    body = cleanup_code(content)
    env = namespace_of(message)

    chdir(getcwd())
    _tmp_path = ospath.join(
        getcwd(), "bot/modules", f"temp_{token_hex(6)}.txt"
    )
    try:
        async with aiopen(_tmp_path, "w") as temp:
            await temp.write(body)

        stdout = StringIO()

        try:
            if func == "exec":
                exec(f"def func():\n{indent(body, '  ')}", env)
            else:
                exec(f"async def func():\n{indent(body, '  ')}", env)
        except Exception as e:
            return f"{e.__class__.__name__}: {e}"

        rfunc = env["func"]

        try:
            with redirect_stdout(stdout):
                func_return = (
                    await sync_to_async(rfunc) if func == "exec" else await rfunc()
                )
        except Exception:
            value = stdout.getvalue()
            return f"{value}{format_exc()}"
        else:
            value = stdout.getvalue()
            result = None
            if func_return is None:
                if value:
                    result = f"{value}"
                else:
                    with suppress(Exception):
                        result = f"{repr(await sync_to_async(eval, body, env))}"
            else:
                result = f"{value}{func_return}"
            if result:
                return result
    finally:
        with suppress(FileNotFoundError, OSError):
            await aioremove(_tmp_path)


@new_task
async def clear(_, message):
    log_input(message)
    global namespaces
    key = _ns_key(message)
    namespaces.pop(key, None)
    await send("Locals Cleared.", message)
