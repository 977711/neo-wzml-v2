# This file is a part of NEO-WZML (github.com/irisXDR/NEO-WZML)

from sys import exit
from importlib import import_module
from logging import (
    FileHandler,
    StreamHandler,
    INFO,
    basicConfig,
    error as log_error,
    info as log_info,
    getLogger,
    ERROR,
)
from os import path, remove, environ
from pymongo.mongo_client import MongoClient
from pymongo.server_api import ServerApi
from subprocess import run as srun, call as scall

getLogger("pymongo").setLevel(ERROR)

# Keep this in sync with `bot/__init__.py`'s `var_list`. These keys are read
# from environment variables and overlaid on top of `config.py` defaults.
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

# Runtime self-update keys are read from DB when present.
_DB_TRUTH_KEYS = ("UPSTREAM_REPO", "UPSTREAM_BRANCH", "AUTO_UPDATE", "UPDATE_PKGS")


def _is_truthy(value):
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return value != 0
    if isinstance(value, str):
        return value.strip().lower() in ("true", "1", "yes", "on")
    return bool(value)


if path.exists("log.txt"):
    with open("log.txt", "r+") as f:
        f.truncate(0)

if path.exists("rlog.txt"):
    remove("rlog.txt")

basicConfig(
    format="[%(asctime)s] [%(levelname)s] - %(message)s",
    datefmt="%d-%b-%y %I:%M:%S %p",
    handlers=[FileHandler("log.txt"), StreamHandler()],
    level=INFO,
)
try:
    settings = import_module("config")
    config_file = {
        key: value.strip() if isinstance(value, str) else value
        for key, value in vars(settings).items()
        if not key.startswith("__")
    }
except ModuleNotFoundError:
    log_info("Config.py file is not Added! Checking ENVs..")
    config_file = {}

env_updates = {
    key: value.strip() if isinstance(value, str) else value
    for key, value in environ.items()
    if key in var_list
}
if env_updates:
    log_info("Config data is updated with ENVs!")
    config_file.update(env_updates)

BOT_TOKEN = config_file.get("BOT_TOKEN", "")
if not BOT_TOKEN:
    log_error("BOT_TOKEN variable is missing! Exiting now")
    exit(1)

BOT_ID = BOT_TOKEN.split(":", 1)[0]

if DATABASE_URL := config_file.get("DATABASE_URL", "").strip():
    try:
        conn = MongoClient(DATABASE_URL, server_api=ServerApi("1"))
        db = conn.neowzml
        config_dict = db.settings.config.find_one({"_id": BOT_ID}) or {}
        if config_dict:
            for key in _DB_TRUTH_KEYS:
                if key in config_dict:
                    config_file[key] = config_dict[key]
        else:
            log_info(
                "No saved config in MongoDB yet — using local config.py / env "
                "for self-update settings."
            )
        conn.close()
    except Exception as e:
        log_error(f"Database ERROR: {e}")

AUTO_UPDATE = _is_truthy(config_file.get("AUTO_UPDATE", False))

UPSTREAM_REPO = (config_file.get("UPSTREAM_REPO") or "").strip() if AUTO_UPDATE else ""
UPSTREAM_BRANCH = (config_file.get("UPSTREAM_BRANCH") or "").strip() or "wzv3"

if UPSTREAM_REPO:
    if path.exists(".git"):
        srun(["rm", "-rf", ".git"])

    update = srun(
        [
            f"git init -q \
                     && git config --global user.email 89005882+irisXDR@users.noreply.github.com \
                     && git config --global user.name アイリス \
                     && git add . \
                     && git commit -sm update -q \
                     && git remote add origin {UPSTREAM_REPO} \
                     && git fetch origin -q \
                     && git reset --hard origin/{UPSTREAM_BRANCH} -q"
        ],
        shell=True,
    )

    repo = UPSTREAM_REPO.split("/")
    UPSTREAM_REPO = f"https://github.com/{repo[-2]}/{repo[-1]}"
    if update.returncode == 0:
        log_info("Successfully updated with Latest Updates !")
    else:
        log_error("Something went Wrong ! Recheck your details or Ask Support !")
    log_info(f"UPSTREAM_REPO: {UPSTREAM_REPO} | UPSTREAM_BRANCH: {UPSTREAM_BRANCH}")
elif AUTO_UPDATE:
    log_info(
        "AUTO_UPDATE is enabled but UPSTREAM_REPO is empty — skipping git update. "
        "Set UPSTREAM_REPO via the bot's settings UI or local config to enable updates."
    )


UPDATE_PKGS = _is_truthy(config_file.get("UPDATE_PKGS", True))
if UPDATE_PKGS:
    scall("uv pip install -U -r requirements.txt", shell=True)
    log_info("Successfully Updated all the Packages !")
