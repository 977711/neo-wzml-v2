# This file is a part of NEO-WZML (github.com/irisXDR/NEO-WZML)

from aiofiles import open as aiopen
from aiofiles.os import remove


def filter_links(links_list, bulk_start, bulk_end):
    start = bulk_start if bulk_start > 0 else None
    end = bulk_end if bulk_end > 0 else None
    return links_list[start:end]


def get_links_from_message(text):
    links_list = text.split("\n")
    return [item.strip() for item in links_list if len(item) != 0]


async def get_links_from_file(message):
    """Download the replied .txt, return one stripped link per line, and
    always clean up the temp file even if reading/parsing raises."""
    links_list = []
    text_file_dir = await message.download()
    try:
        async with aiopen(text_file_dir, "r+") as f:
            lines = await f.readlines()
            links_list.extend(line.strip() for line in lines if len(line) != 0)
    finally:
        try:
            await remove(text_file_dir)
        except Exception:
            pass
    return links_list


def _safe_bulk_int(value, default=0):
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


async def extract_bulk_links(message, bulk_start, bulk_end):
    bulk_start = _safe_bulk_int(bulk_start, 0)
    bulk_end = _safe_bulk_int(bulk_end, 0)
    links_list = []
    if reply_to := message.reply_to_message:
        if (file_ := reply_to.document) and (file_.mime_type == "text/plain"):
            links_list = await get_links_from_file(reply_to)
        elif text := reply_to.text:
            links_list = get_links_from_message(text)
    return filter_links(links_list, bulk_start, bulk_end) if links_list else links_list
