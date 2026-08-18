# This file is a part of NEO-WZML (github.com/irisXDR/NEO-WZML)

from uvloop import install

install()

from asyncio import new_event_loop, set_event_loop

bot_loop = new_event_loop()
set_event_loop(bot_loop)

from subprocess import run as srun
from os import getcwd
from asyncio import Lock
from logging import (
    ERROR,
    INFO,
    WARNING,
    FileHandler,
    StreamHandler,
    basicConfig,
    getLogger,
)
from os import cpu_count
from time import time

from apscheduler.schedulers.asyncio import AsyncIOScheduler

from .core.config_manager import BinConfig

getLogger("requests").setLevel(WARNING)
getLogger("urllib3").setLevel(WARNING)
getLogger("pyrogram").setLevel(ERROR)
getLogger("apscheduler").setLevel(ERROR)
getLogger("httpx").setLevel(WARNING)
getLogger("pymongo").setLevel(WARNING)
getLogger("aiohttp").setLevel(WARNING)


bot_start_time = time()

basicConfig(
    format="[%(asctime)s] [%(levelname)s] - %(message)s",
    datefmt="%d-%b-%y %I:%M:%S %p",
    handlers=[FileHandler("log.txt"), StreamHandler()],
    level=INFO,
)

LOGGER = getLogger(__name__)
cpu_no = cpu_count()
threads = max(1, cpu_no // 2)
cores = ",".join(str(i) for i in range(threads))

bot_cache = {}
DOWNLOAD_DIR = "/usr/src/app/downloads/"
intervals = {"status": {}, "qb": "", "jd": "", "stopAll": False}
qb_torrents = {}
jd_downloads = {}
user_data = {}
aria2_options = {}
qbit_options = {}
QBIT_DEFAULT_WEB_PASSWORD = "adminadmin"
queued_dl = {}
queued_up = {}
status_dict = {}
task_dict = {}
rss_dict = {}
shortener_dict = {}
# Keys that may be supplied via environment variables (in addition to
# config.py); keep this list in sync with `update.py`'s `var_list`.
var_list = [
    "BOT_TOKEN",
    "TELEGRAM_API",
    "TELEGRAM_HASH",
    "OWNER_ID",
    "DATABASE_URL",
    "BASE_URL",
    "UPSTREAM_REPO",
    "UPSTREAM_BRANCH",
    "AUTO_UPDATE",
    "UPDATE_PKGS",
]
auth_chats = {}
excluded_extensions = ["aria2", "!qB"]
drives_names = []
drives_ids = []
index_urls = []
sudo_users = []
non_queued_dl = set()
non_queued_up = set()
multi_tags = set()
task_dict_lock = Lock()
queue_dict_lock = Lock()
qb_listener_lock = Lock()
jd_listener_lock = Lock()
cpu_eater_lock = Lock()
same_directory_lock = Lock()

list_drives_dict = {}
categories_dict = {}
extra_buttons = {}
shorteners_list = []

try:
    srun([BinConfig.QBIT_NAME, "-d", f"--profile={getcwd()}"], check=False)
    qb_start_time = time()
    LOGGER.info(f"qBittorrent process started: {BinConfig.QBIT_NAME}")
except FileNotFoundError:
    qb_start_time = 0
    LOGGER.error(
        f"qBittorrent binary not found: {BinConfig.QBIT_NAME}. "
        "qBittorrent features will be unavailable until installed."
    )
except Exception as e:
    qb_start_time = 0
    LOGGER.error(f"Failed to start qBittorrent ({BinConfig.QBIT_NAME}): {e}")

async def load_v15_configs():
    from aiofiles import open as aiopen
    from aiofiles.os import path as aiopath

    def _parse_drive_line(line):
        stripped = line.strip()
        if not stripped:
            return None
        parts = stripped.split()
        if len(parts) < 2:
            return None
        sep = 2 if parts[-1].startswith("http") else 1
        temp = stripped.rsplit(maxsplit=sep)
        if len(temp) < sep + 1:
            return None
        return temp[0], temp[1], (temp[2] if sep == 2 else "")

    if await aiopath.exists("list_drives.txt"):
        try:
            async with aiopen("list_drives.txt", "r") as f:
                lines = await f.readlines()
            for line in lines:
                parsed = _parse_drive_line(line)
                if parsed is None:
                    continue
                raw_name, drive_id, index_link = parsed
                # casefold() lowercases, so compare against lowercase target
                name = "Main Custom" if raw_name.casefold() == "main" else raw_name
                list_drives_dict[name] = {
                    "drive_id": drive_id,
                    "index_link": index_link,
                }
        except Exception as e:
            LOGGER.error(f"Error loading list_drives.txt: {e}")

    if await aiopath.exists("categories.txt"):
        try:
            async with aiopen("categories.txt", "r") as f:
                lines = await f.readlines()
            for line in lines:
                parsed = _parse_drive_line(line)
                if parsed is None:
                    continue
                raw_name, drive_id, index_link = parsed
                name = "Root Custom" if raw_name.casefold() == "root" else raw_name
                categories_dict[name] = {
                    "drive_id": drive_id,
                    "index_link": index_link,
                }
        except Exception as e:
            LOGGER.error(f"Error loading categories.txt: {e}")

    if await aiopath.exists("buttons.txt"):
        try:
            async with aiopen("buttons.txt", "r") as f:
                lines = await f.readlines()
            for line in lines:
                temp = line.strip().rsplit(maxsplit=1)
                if len(temp) < 2:
                    continue
                if len(extra_buttons) >= 20:
                    break
                if temp[1].startswith("http"):
                    extra_buttons[temp[0]] = temp[1]
        except Exception as e:
            LOGGER.error(f"Error loading buttons.txt: {e}")

    if await aiopath.exists("shorteners.txt"):
        try:
            async with aiopen("shorteners.txt", "r") as f:
                lines = await f.readlines()
            for line in lines:
                temp = line.strip().split()
                if len(temp) == 2:
                    shorteners_list.append({"domain": temp[0], "api_key": temp[1]})
        except Exception as e:
            LOGGER.error(f"Error loading shorteners.txt: {e}")

bot_loop.create_task(load_v15_configs())

scheduler = AsyncIOScheduler(event_loop=bot_loop)
