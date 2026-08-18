# This file is a part of NEO-WZML (github.com/irisXDR/NEO-WZML)

from datetime import datetime, timezone
from importlib import import_module
from time import time


def _utcnow():
    # datetime.utcnow() deprecated in 3.12; keep naive for Mongo TTL compat.
    return datetime.now(timezone.utc).replace(tzinfo=None)

from aiofiles import open as aiopen
from aiofiles.os import path as aiopath
from motor.motor_asyncio import AsyncIOMotorClient
from pymongo import ReturnDocument
from pymongo.errors import OperationFailure, PyMongoError
from pymongo.server_api import ServerApi

from bot import LOGGER, QBIT_DEFAULT_WEB_PASSWORD, qbit_options, rss_dict, user_data
from bot.core.config_manager import Config
from bot.core.tg_client import TgClient


def _safe_int(value, default=0):
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


class DbManager:
    def __init__(self):
        self._return = True
        self._conn = None
        self.db = None
        self._universal_max_tasks_cache = None
        self._universal_max_tasks_cache_ts = 0.0

    async def connect(self):
        try:
            if self._conn is not None:
                await self._conn.close()
            self._conn = AsyncIOMotorClient(
                Config.DATABASE_URL, server_api=ServerApi("1")
            )
            self.db = self._conn.neowzml
            self._return = False
        except PyMongoError as e:
            LOGGER.error(f"Error in DB connection: {e}")
            self.db = None
            self._return = True
            self._conn = None

    async def disconnect(self):
        self._return = True
        if self._conn is not None:
            await self._conn.close()
        self._conn = None

    async def update_deploy_config(self):
        if self._return:
            return
        settings = import_module("config")
        config_file = {
            key: value.strip() if isinstance(value, str) else value
            for key, value in vars(settings).items()
            if not key.startswith("__")
        }
        await self.db.settings.deployConfig.replace_one(
            {"_id": TgClient.ID}, config_file, upsert=True
        )

    async def update_config(self, dict_):
        if self._return:
            return
        await self.db.settings.config.update_one(
            {"_id": TgClient.ID}, {"$set": dict_}, upsert=True
        )

    async def update_universal_max_tasks(self, value: int):
        if self._return:
            return

        value = _safe_int(value, 0)
        if value < 0:
            value = 0

        await self.db.settings.config.update_one(
            {"_id": "GLOBAL"},
            {"$set": {"UNIVERSAL_MAX_TASKS": value}},
            upsert=True,
        )
        await self.update_config({"UNIVERSAL_MAX_TASKS": value})

        self._universal_max_tasks_cache = value
        self._universal_max_tasks_cache_ts = time()

    async def get_universal_max_tasks(self, cache_ttl: int = 30) -> int:
        if self._return:
            return _safe_int(Config.UNIVERSAL_MAX_TASKS, 0)

        now = time()
        if (
            self._universal_max_tasks_cache is not None
            and (now - self._universal_max_tasks_cache_ts) < cache_ttl
        ):
            return self._universal_max_tasks_cache

        doc = await self.db.settings.config.find_one(
            {"_id": "GLOBAL"}, {"_id": 0, "UNIVERSAL_MAX_TASKS": 1}
        )
        value = _safe_int((doc or {}).get("UNIVERSAL_MAX_TASKS"), 0)
        if value < 0:
            value = 0

        if value <= 0:
            fallback_doc = await self.db.settings.config.find_one(
                {"_id": {"$ne": "GLOBAL"}, "UNIVERSAL_MAX_TASKS": {"$gt": 0}},
                {"_id": 0, "UNIVERSAL_MAX_TASKS": 1},
                sort=[("UNIVERSAL_MAX_TASKS", 1)],
            )
            fb_value = _safe_int((fallback_doc or {}).get("UNIVERSAL_MAX_TASKS"), 0)
            if fb_value > 0:
                value = fb_value
                await self.db.settings.config.update_one(
                    {"_id": "GLOBAL"},
                    {"$set": {"UNIVERSAL_MAX_TASKS": value}},
                    upsert=True,
                )

        self._universal_max_tasks_cache = value
        self._universal_max_tasks_cache_ts = now
        return value

    async def update_aria2(self, key, value):
        if self._return:
            return
        await self.db.settings.aria2c.update_one(
            {"_id": TgClient.ID}, {"$set": {key: value}}, upsert=True
        )

    async def update_qbittorrent(self, key, value):
        if self._return:
            return
        if key == "web_ui_password":
            value = QBIT_DEFAULT_WEB_PASSWORD
        await self.db.settings.qbittorrent.update_one(
            {"_id": TgClient.ID}, {"$set": {key: value}}, upsert=True
        )

    async def save_qbit_settings(self):
        if self._return:
            return
        qbit_options["web_ui_password"] = QBIT_DEFAULT_WEB_PASSWORD
        await self.db.settings.qbittorrent.update_one(
            {"_id": TgClient.ID}, {"$set": qbit_options}, upsert=True
        )

    async def update_private_file(self, path):
        if self._return:
            return
        db_path = path.replace(".", "__")
        if await aiopath.exists(path):
            async with aiopen(path, "rb") as pf:
                pf_bin = await pf.read()
            await self.db.settings.files.update_one(
                {"_id": TgClient.ID}, {"$set": {db_path: pf_bin}}, upsert=True
            )
            if path == "config.py":
                await self.update_deploy_config()
        else:
            await self.db.settings.files.update_one(
                {"_id": TgClient.ID}, {"$unset": {db_path: ""}}, upsert=True
            )

    async def update_user_data(self, user_id):
        if self._return:
            return
        data = user_data.get(user_id, {})
        data = data.copy()
        for key in ("THUMBNAIL", "RCLONE_CONFIG", "TOKEN_PICKLE", "USER_COOKIE_FILE"):
            data.pop(key, None)
        pipeline = [
            {
                "$replaceRoot": {
                    "newRoot": {
                        "$mergeObjects": [
                            data,
                            {
                                "$arrayToObject": {
                                    "$filter": {
                                        "input": {"$objectToArray": "$$ROOT"},
                                        "as": "field",
                                        "cond": {
                                            "$in": [
                                                "$$field.k",
                                                [
                                                    "THUMBNAIL",
                                                    "RCLONE_CONFIG",
                                                    "TOKEN_PICKLE",
                                                    "USER_COOKIE_FILE",
                                                ],
                                            ]
                                        },
                                    }
                                }
                            },
                        ]
                    }
                }
            }
        ]
        await self.db.users[TgClient.ID].update_one(
            {"_id": user_id}, pipeline, upsert=True
        )

    async def update_user_doc(self, user_id, key, path=""):
        if self._return:
            return
        if path:
            async with aiopen(path, "rb") as doc:
                doc_bin = await doc.read()
            await self.db.users[TgClient.ID].update_one(
                {"_id": user_id}, {"$set": {key: doc_bin}}, upsert=True
            )
        else:
            await self.db.users[TgClient.ID].update_one(
                {"_id": user_id}, {"$unset": {key: ""}}, upsert=True
            )

    async def rss_update_all(self):
        if self._return:
            return
        for user_id in list(rss_dict.keys()):
            await self.db.rss[TgClient.ID].replace_one(
                {"_id": user_id}, rss_dict[user_id], upsert=True
            )

    async def rss_update(self, user_id):
        if self._return:
            return
        await self.db.rss[TgClient.ID].replace_one(
            {"_id": user_id}, rss_dict[user_id], upsert=True
        )

    async def rss_delete(self, user_id):
        if self._return:
            return
        await self.db.rss[TgClient.ID].delete_one({"_id": user_id})

    async def add_incomplete_task(self, cid, link, tag):
        if self._return:
            return
        await self.db.tasks[TgClient.ID].insert_one(
            {"_id": link, "cid": cid, "tag": tag}
        )

    async def get_pm_uids(self):
        if self._return:
            return []
        return [doc["_id"] async for doc in self.db.pm_users[TgClient.ID].find({})]

    async def set_pm_users(self, user_id):
        if self._return:
            return
        if not bool(await self.db.pm_users[TgClient.ID].find_one({"_id": user_id})):
            await self.db.pm_users[TgClient.ID].insert_one({"_id": user_id})
            LOGGER.info(f"New PM User Added : {user_id}")

    async def rm_pm_user(self, user_id):
        if self._return:
            return
        await self.db.pm_users[TgClient.ID].delete_one({"_id": user_id})

    async def rm_complete_task(self, link):
        if self._return:
            return
        await self.db.tasks[TgClient.ID].delete_one({"_id": link})

    async def get_incomplete_tasks(self):
        notifier_dict = {}
        if self._return:
            return notifier_dict
        if await self.db.tasks[TgClient.ID].find_one():
            rows = self.db.tasks[TgClient.ID].find({})
            async for row in rows:
                if row["cid"] in list(notifier_dict.keys()):
                    if row["tag"] in list(notifier_dict[row["cid"]]):
                        notifier_dict[row["cid"]][row["tag"]].append(row["_id"])
                    else:
                        notifier_dict[row["cid"]][row["tag"]] = [row["_id"]]
                else:
                    notifier_dict[row["cid"]] = {row["tag"]: [row["_id"]]}
        await self.db.tasks[TgClient.ID].drop()
        return notifier_dict

    async def trunc_table(self, name):
        if self._return:
            return
        await self.db[name][TgClient.ID].drop()

    async def add_shared_task(self, user_id, task_id, bot_id, bot_name=""):
        if self._return:
            return
        await self.db.shared_tasks.update_one(
            {"_id": f"{bot_id}:{task_id}"},
            {
                "$set": {
                    "user_id": user_id,
                    "task_id": task_id,
                    "bot_id": bot_id,
                    "bot_name": bot_name,
                    "timestamp": _utcnow(),
                }
            },
            upsert=True,
        )

    async def remove_shared_task(self, task_id, bot_id, user_id=None):
        if self._return:
            return

        doc_id = f"{bot_id}:{task_id}"

        doc = await self.db.shared_tasks.find_one({"_id": doc_id})
        if user_id is None and doc and "user_id" in doc:
            user_id = doc["user_id"]

        if user_id is not None:
            try:
                await self.release_universal_task_slot(user_id, task_id, bot_id)
            except Exception as e:
                LOGGER.debug(
                    f"Failed to release universal task slot for task {task_id}: {e}"
                )

        try:
            await self.db.shared_tasks.delete_one({"_id": doc_id})
        except PyMongoError as e:
            LOGGER.debug(f"Failed to delete shared_task {doc_id}: {e}")

    async def get_user_shared_task_count(self, user_id):
        if self._return:
            return 0
        count = await self.db.shared_tasks.count_documents({"user_id": user_id})
        return count

    async def acquire_universal_task_slot(self, user_id, task_id, bot_id, limit):
        """Reserve slot; returns (acquired, current_count)."""
        if self._return:
            return True, 0
        if limit <= 0:
            return True, 0

        task_key = f"{bot_id}:{task_id}"
        now = _utcnow()

        try:
            before = await self.db.universal_task_locks.find_one_and_update(
                {"_id": user_id},
                [
                    {"$set": {"tasks": {"$ifNull": ["$tasks", []]}}},
                    {
                        "$set": {
                            "tasks": {
                                "$cond": [
                                    {
                                        "$or": [
                                            {"$in": [task_key, "$tasks"]},
                                            {"$lt": [{"$size": "$tasks"}, limit]},
                                        ]
                                    },
                                    {"$setUnion": ["$tasks", [task_key]]},
                                    "$tasks",
                                ]
                            },
                            "updated_at": now,
                        }
                    },
                    {"$set": {"count": {"$size": "$tasks"}}},
                ],
                upsert=True,
                return_document=ReturnDocument.BEFORE,
            )
        except PyMongoError as e:
            LOGGER.warning(
                f"Universal slot pipeline update failed for user {user_id}. "
                f"Attempting fallback. Error: {e}"
            )
            try:
                return await self._acquire_universal_task_slot_fallback(
                    user_id, task_key, limit, now
                )
            except PyMongoError as e2:
                LOGGER.error(
                    f"Error acquiring universal task slot for user {user_id} "
                    f"(fallback also failed; rejecting task to honour the limit): {e2}"
                )
                return False, 0

        if before is None:
            return True, 1

        tasks = before.get("tasks") or []
        if task_key in tasks:
            return True, len(tasks)

        current = len(tasks)
        if current < limit:
            return True, current + 1
        return False, current

    async def _acquire_universal_task_slot_fallback(self, user_id, task_key, limit, now):
        if limit <= 0:
            return True, 0

        cond_filter = {
            "_id": user_id,
            "$or": [
                {"tasks": task_key},
                {"$expr": {"$lt": [{"$size": {"$ifNull": ["$tasks", []]}}, limit]}},
            ],
        }
        update = {"$addToSet": {"tasks": task_key}, "$set": {"updated_at": now}}

        res = await self.db.universal_task_locks.update_one(cond_filter, update, upsert=False)
        if res.matched_count:
            doc = await self.db.universal_task_locks.find_one(
                {"_id": user_id}, {"_id": 0, "tasks": 1}
            )
            tasks = (doc or {}).get("tasks") or []
            await self.db.universal_task_locks.update_one(
                {"_id": user_id}, {"$set": {"count": len(tasks)}}
            )
            return True, len(tasks)

        doc = await self.db.universal_task_locks.find_one(
            {"_id": user_id}, {"_id": 0, "tasks": 1}
        )
        if doc is None:
            try:
                await self.db.universal_task_locks.insert_one(
                    {"_id": user_id, "tasks": [task_key], "updated_at": now, "count": 1}
                )
                return True, 1
            except PyMongoError:
                res = await self.db.universal_task_locks.update_one(
                    cond_filter, update, upsert=False
                )
                doc = await self.db.universal_task_locks.find_one(
                    {"_id": user_id}, {"_id": 0, "tasks": 1}
                )
                tasks = (doc or {}).get("tasks") or []
                await self.db.universal_task_locks.update_one(
                    {"_id": user_id}, {"$set": {"count": len(tasks)}}
                )
                return (task_key in tasks) or bool(res.matched_count), len(tasks)

        tasks = doc.get("tasks") or []
        if task_key in tasks:
            return True, len(tasks)

        current = len(tasks)
        if current < limit:
            await self.db.universal_task_locks.update_one(cond_filter, update, upsert=False)
            doc = await self.db.universal_task_locks.find_one(
                {"_id": user_id}, {"_id": 0, "tasks": 1}
            )
            tasks = (doc or {}).get("tasks") or []
            await self.db.universal_task_locks.update_one(
                {"_id": user_id}, {"$set": {"count": len(tasks)}}
            )
            return task_key in tasks, len(tasks)

        return False, current

    async def release_universal_task_slot(self, user_id, task_id, bot_id):
        if self._return:
            return

        task_key = f"{bot_id}:{task_id}"
        now = _utcnow()

        try:
            await self.db.universal_task_locks.update_one(
                {"_id": user_id},
                [
                    {"$set": {"tasks": {"$ifNull": ["$tasks", []]}}},
                    {
                        "$set": {
                            "tasks": {"$setDifference": ["$tasks", [task_key]]},
                            "updated_at": now,
                        }
                    },
                    {"$set": {"count": {"$size": "$tasks"}}},
                ],
            )
        except PyMongoError as e:
            try:
                await self.db.universal_task_locks.update_one(
                    {"_id": user_id},
                    {"$pull": {"tasks": task_key}, "$set": {"updated_at": now}},
                )
            except PyMongoError as e2:
                LOGGER.debug(
                    f"Error releasing universal task slot for user {user_id}: {e}; fallback failed: {e2}"
                )

    async def create_shared_tasks_indexes(self):
        if self._return:
            return

        await self.db.shared_tasks.create_index([("user_id", 1), ("timestamp", -1)])
        await self.db.shared_tasks.create_index([("bot_id", 1)])

        try:
            await self.db.shared_tasks.create_index([("timestamp", 1)], expireAfterSeconds=10800)
        except OperationFailure as e:
            if e.code == 85 and "IndexOptionsConflict" in str(e):
                LOGGER.warning("TTL index exists with old options, recreating...")
                await self.db.shared_tasks.drop_index("timestamp_1")
                await self.db.shared_tasks.create_index([("timestamp", 1)], expireAfterSeconds=10800)
                LOGGER.info("TTL index updated to 3 hours (10800 seconds)")
            else:
                raise

        LOGGER.info("Shared tasks indexes created successfully")

    async def cleanup_bot_shared_tasks(self, bot_id):
        if self._return:
            return

        deleted = 0
        async for doc in self.db.shared_tasks.find({"bot_id": bot_id}):
            user_id = doc.get("user_id")
            task_id = doc.get("task_id")

            if user_id is not None and task_id is not None:
                try:
                    await self.release_universal_task_slot(user_id, task_id, bot_id)
                except Exception:
                    pass

            try:
                await self.db.shared_tasks.delete_one({"_id": doc["_id"]})
                deleted += 1
            except PyMongoError:
                continue

        if deleted > 0:
            LOGGER.info(f"Cleaned up {deleted} orphaned shared tasks for bot {bot_id}")

    async def refresh_task_timestamp(self, task_id, bot_id):
        if self._return:
            return
        await self.db.shared_tasks.update_one(
            {"_id": f"{bot_id}:{task_id}"}, {"$set": {"timestamp": _utcnow()}}
        )

    async def refresh_universal_task_lock(self, user_id, task_id, bot_id):
        if self._return:
            return
        await self.db.universal_task_locks.update_one(
            {"_id": user_id, "tasks": f"{bot_id}:{task_id}"},
            {"$set": {"updated_at": _utcnow()}},
        )

    async def create_active_tasks_indexes(self):
        if self._return:
            return

        try:
            await self.db.bot_active_tasks.create_index(
                [("last_updated", 1)], expireAfterSeconds=300
            )
        except OperationFailure as e:
            if e.code == 85 and "IndexOptionsConflict" in str(e):
                LOGGER.warning("TTL index exists with old options, recreating...")
                await self.db.bot_active_tasks.drop_index("last_updated_1")
                await self.db.bot_active_tasks.create_index(
                    [("last_updated", 1)], expireAfterSeconds=300
                )
            else:
                raise

        try:
            await self.db.bot_active_tasks.create_index([("_id", 1)])
        except OperationFailure as e:
            if e.code != 85:
                raise

        LOGGER.info("Bot active tasks indexes created successfully")

    async def create_universal_task_locks_indexes(self):
        if self._return:
            return

        try:
            await self.db.universal_task_locks.create_index(
                [("updated_at", 1)], expireAfterSeconds=43200
            )
        except OperationFailure as e:
            if e.code == 85 and "IndexOptionsConflict" in str(e):
                LOGGER.warning(
                    "TTL index on universal_task_locks exists with old options, recreating..."
                )
                await self.db.universal_task_locks.drop_index("updated_at_1")
                await self.db.universal_task_locks.create_index(
                    [("updated_at", 1)], expireAfterSeconds=43200
                )
            else:
                raise

        LOGGER.info("Universal task locks index created successfully")

    async def update_active_tasks(self, bot_id, task_list):
        if self._return:
            return
        await self.db.bot_active_tasks.update_one(
            {"_id": bot_id},
            {
                "$set": {
                    "active_tasks": task_list,
                    "last_updated": _utcnow(),
                }
            },
            upsert=True,
        )

    async def get_all_active_tasks(self):
        if self._return:
            return {}
        result = {}
        async for doc in self.db.bot_active_tasks.find({}):
            result[doc["_id"]] = doc.get("active_tasks", [])
        return result

    async def get_user_shared_tasks(self, user_id):
        if self._return:
            return []
        tasks = []
        async for doc in self.db.shared_tasks.find({"user_id": user_id}):
            tasks.append(
                {
                    "bot_id": doc["bot_id"],
                    "task_id": doc["task_id"],
                    "timestamp": doc.get("timestamp"),
                }
            )
        return tasks

    async def cleanup_orphaned_tasks(self, user_id, min_age_seconds: int = 300):
        """Remove shared_tasks absent from bot registries (min_age avoids startup races)."""
        if self._return:
            return 0

        db_tasks = await self.get_user_shared_tasks(user_id)
        if not db_tasks:
            return 0

        all_active = await self.get_all_active_tasks()

        orphans = []
        now_dt = _utcnow()
        now_ts = time()
        for task in db_tasks:
            bot_id = task["bot_id"]
            task_id = task["task_id"]
            ts = task.get("timestamp")

            if isinstance(ts, datetime):
                age_seconds = (now_dt - ts).total_seconds()
            elif isinstance(ts, (int, float)):
                age_seconds = now_ts - ts
            else:
                age_seconds = float("inf")

            if age_seconds < min_age_seconds:
                continue

            if bot_id not in all_active or task_id not in all_active[bot_id]:
                orphans.append(task)

        cleaned = 0
        for orphan in orphans:
            await self.remove_shared_task(orphan["task_id"], orphan["bot_id"])
            cleaned += 1

        if cleaned > 0:
            LOGGER.info(
                f"Cleaned up {cleaned} orphaned task(s) for user {user_id} "
                f"(tasks: {[o['task_id'] for o in orphans]})"
            )

        return cleaned

    async def reconcile_universal_task_locks(self, min_age_seconds: int = 300):
        """Drop stale universal_task_lock slots (min_age + CAS on updated_at)."""
        if self._return:
            return 0

        cleaned_slots = 0
        now = _utcnow()

        async for doc in self.db.universal_task_locks.find({}):
            user_id = doc["_id"]
            updated_at = doc.get("updated_at")

            if isinstance(updated_at, datetime):
                age = (now - updated_at).total_seconds()
                if age < min_age_seconds:
                    continue

            tasks = doc.get("tasks") or []
            if not tasks:
                await self.db.universal_task_locks.delete_one(
                    {"_id": user_id, "updated_at": updated_at}
                )
                continue

            cursor = self.db.shared_tasks.find(
                {"_id": {"$in": tasks}}, {"_id": 1}
            )
            existing_set = {d["_id"] async for d in cursor}
            still_valid = [t for t in tasks if t in existing_set]
            doc_cleaned = len(tasks) - len(still_valid)

            if doc_cleaned == 0:
                continue

            if still_valid:
                res = await self.db.universal_task_locks.update_one(
                    {"_id": user_id, "updated_at": updated_at},
                    {
                        "$set": {
                            "tasks": still_valid,
                            "count": len(still_valid),
                            "updated_at": now,
                        }
                    },
                )
                if res.modified_count:
                    cleaned_slots += doc_cleaned
            else:
                res = await self.db.universal_task_locks.delete_one(
                    {"_id": user_id, "updated_at": updated_at}
                )
                if res.deleted_count:
                    cleaned_slots += doc_cleaned

        if cleaned_slots > 0:
            LOGGER.info(
                f"Reconciliation cleaned {cleaned_slots} orphaned universal task slots"
            )

        return cleaned_slots

    async def migrate_legacy_timestamps(self):
        """Convert legacy float timestamps to datetime so TTL indexes apply."""
        if self._return:
            return

        try:
            res1 = await self.db.shared_tasks.update_many(
                {"timestamp": {"$type": "double"}},
                {"$set": {"timestamp": _utcnow()}},
            )
            if res1.modified_count:
                LOGGER.info(
                    f"Migrated {res1.modified_count} shared_tasks from numeric timestamp to datetime"
                )
        except Exception as e:
            LOGGER.error(f"Failed to migrate shared_tasks timestamps: {e}")

        try:
            res2 = await self.db.bot_active_tasks.update_many(
                {"last_updated": {"$type": "double"}},
                {"$set": {"last_updated": _utcnow()}},
            )
            if res2.modified_count:
                LOGGER.info(
                    f"Migrated {res2.modified_count} bot_active_tasks from numeric last_updated to datetime"
                )
        except Exception as e:
            LOGGER.error(f"Failed to migrate bot_active_tasks timestamps: {e}")

        try:
            res3 = await self.db.universal_task_locks.update_many(
                {"updated_at": {"$type": "double"}},
                {"$set": {"updated_at": _utcnow()}},
            )
            if res3.modified_count:
                LOGGER.info(
                    f"Migrated {res3.modified_count} universal_task_locks from numeric updated_at to datetime"
                )
        except Exception as e:
            LOGGER.error(f"Failed to migrate universal_task_locks timestamps: {e}")


database = DbManager()
