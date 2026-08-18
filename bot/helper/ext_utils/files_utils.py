# This file is a part of NEO-WZML (github.com/irisXDR/NEO-WZML)

from aioshutil import rmtree as aiormtree, move
from asyncio import create_subprocess_exec, sleep, wait_for
from asyncio.subprocess import PIPE
from contextlib import suppress
from psutil import disk_usage
from os import path as ospath, readlink, walk
from re import I, escape, search as re_search, split as re_split

from aiofiles.os import (
    listdir,
    remove,
    rmdir,
    symlink,
    makedirs as aiomakedirs,
    path as aiopath,
    readlink as aioreadlink,
)
from magic import Magic

from bot import DOWNLOAD_DIR, LOGGER
from bot.core.torrent_manager import TorrentManager
from bot.helper.ext_utils.bot_utils import cmd_exec, sync_to_async
from bot.helper.ext_utils.exceptions import NotSupportedExtractionArchive

ARCH_EXT = [
    ".tar.bz2",
    ".tar.gz",
    ".bz2",
    ".gz",
    ".tar.xz",
    ".tar",
    ".tbz2",
    ".tgz",
    ".lzma2",
    ".zip",
    ".7z",
    ".z",
    ".rar",
    ".iso",
    ".wim",
    ".cab",
    ".apm",
    ".arj",
    ".chm",
    ".cpio",
    ".cramfs",
    ".deb",
    ".dmg",
    ".fat",
    ".hfs",
    ".lzh",
    ".lzma",
    ".mbr",
    ".msi",
    ".mslz",
    ".nsis",
    ".ntfs",
    ".rpm",
    ".squashfs",
    ".udf",
    ".vhd",
    ".xar",
    ".zst",
    ".zstd",
    ".cbz",
    ".apfs",
    ".ar",
    ".qcow",
    ".macho",
    ".exe",
    ".dll",
    ".sys",
    ".pmd",
    ".swf",
    ".swfc",
    ".simg",
    ".vdi",
    ".vhdx",
    ".vmdk",
    ".gzip",
    ".lzma86",
    ".sha256",
    ".sha512",
    ".sha224",
    ".sha384",
    ".sha1",
    ".md5",
    ".crc32",
    ".crc64",
]

VIDEO_EXTENSIONS = (
    '.mp4', '.mkv', '.avi', '.mov', '.wmv', '.flv', '.webm',
    '.m4v', '.mpg', '.mpeg', '.3gp', '.ogv', '.ts', '.m2ts'
)
MIN_VIDEO_SIZE = 100 * 1024 * 1024


async def check_strict_file_mode(file_path, file_name):
    from bot.core.config_manager import Config

    if not Config.STRICT_FILE_MODE:
        return True, None

    try:
        file_size = await aiopath.getsize(file_path)
        is_video = file_name.lower().endswith(VIDEO_EXTENSIONS)

        if not is_video:
            return False, "non-video file"

        if file_size < MIN_VIDEO_SIZE:
            size_mb = file_size / (1024 * 1024)
            return False, f"video smaller than 100MB ({size_mb:.2f}MB)"

        return True, None
    except Exception as e:
        LOGGER.error(f"Error checking file in STRICT_FILE_MODE: {file_path} - {e}")
        return False, f"strict-mode check failed: {e}"


FIRST_SPLIT_REGEX = (
    r"\.part0*1\.rar$|\.7z\.0*1$|\.zip\.0*1$|^(?!.*\.part\d+\.rar$).*\.rar$"
)

SPLIT_REGEX = r"\.r\d+$|\.7z\.\d+$|\.z\d+$|\.zip\.\d+$|\.part\d+\.rar$"


def is_first_archive_split(file):
    return bool(re_search(FIRST_SPLIT_REGEX, file.lower(), I))


def is_archive(file):
    return file.strip().lower().endswith(tuple(ARCH_EXT))


def is_archive_split(file):
    return bool(re_search(SPLIT_REGEX, file.lower(), I))


async def clean_target(opath):
    if await aiopath.exists(opath):
        LOGGER.info(f"Cleaning Target: {opath}")
        try:
            if await aiopath.isdir(opath):
                await aiormtree(opath, ignore_errors=True)
            else:
                await remove(opath)
        except Exception as e:
            LOGGER.error(str(e))


async def clean_download(opath):
    if await aiopath.exists(opath):
        try:
            await aiormtree(opath, ignore_errors=True)
        except Exception as e:
            LOGGER.error(str(e))


async def clean_all():
    await TorrentManager.remove_all()
    await (await create_subprocess_exec("rm", "-rf", DOWNLOAD_DIR)).wait()
    await aiomakedirs(DOWNLOAD_DIR, exist_ok=True)


async def clean_unwanted(opath):
    from bot.core.config_manager import Config

    LOGGER.info(f"Cleaning unwanted files/folders: {opath}")
    for dirpath, _, files in await sync_to_async(walk, opath, topdown=False):
        for filee in files:
            f_path = ospath.join(dirpath, filee)

            if Config.STRICT_FILE_MODE:
                try:
                    file_size = await aiopath.getsize(f_path)
                    is_video = filee.lower().endswith(VIDEO_EXTENSIONS)

                    if not is_video:
                        LOGGER.info(f"STRICT_FILE_MODE: Removing non-video file: {f_path}")
                        try:
                            await remove(f_path)
                        except FileNotFoundError:
                            pass
                        continue
                    elif file_size < MIN_VIDEO_SIZE:
                        LOGGER.info(f"STRICT_FILE_MODE: Removing video smaller than 100MB ({file_size / (1024*1024):.2f}MB): {f_path}")
                        try:
                            await remove(f_path)
                        except FileNotFoundError:
                            pass
                        continue
                except FileNotFoundError:
                    # Race with concurrent cleanup; nothing to do.
                    continue
                except Exception as e:
                    LOGGER.error(f"Error checking file in STRICT_FILE_MODE: {f_path} - {e}")

            if filee.strip().endswith(".parts") and filee.startswith("."):
                try:
                    await remove(f_path)
                except FileNotFoundError:
                    pass
                except OSError as e:
                    LOGGER.warning(f"clean_unwanted: failed to remove {f_path}: {e}")
        if dirpath.strip().endswith(".unwanted"):
            await aiormtree(dirpath, ignore_errors=True)
    for dirpath, _, _ in await sync_to_async(walk, opath, topdown=False):
        try:
            if not await listdir(dirpath):
                await rmdir(dirpath)
        except FileNotFoundError:
            # First walk may have already removed the directory.
            continue
        except OSError as e:
            LOGGER.warning(f"clean_unwanted: failed to rmdir {dirpath}: {e}")


async def check_storage_threshold(size, threshold, io_task=False, alloc=False):
    free = (await sync_to_async(disk_usage, DOWNLOAD_DIR)).free
    return free >= (threshold + (size * (2 if io_task else 1) if not alloc else 0))


async def get_path_size(opath):
    total_size = 0
    if await aiopath.isfile(opath):
        if await aiopath.islink(opath):
            target = await aioreadlink(opath)
            if not ospath.isabs(target):
                target = ospath.normpath(ospath.join(ospath.dirname(opath), target))
            opath = target
        return await aiopath.getsize(opath)
    for root, _, files in await sync_to_async(walk, opath):
        for f in files:
            abs_path = ospath.join(root, f)
            if await aiopath.islink(abs_path):
                target = await aioreadlink(abs_path)
                if not ospath.isabs(target):
                    target = ospath.normpath(ospath.join(ospath.dirname(abs_path), target))
                abs_path = target
            total_size += await aiopath.getsize(abs_path)
    return total_size


async def count_files_and_folders(opath):
    total_files = 0
    total_folders = 0
    for _, dirs, files in await sync_to_async(walk, opath):
        total_files += len(files)
        total_folders += len(dirs)
    return total_folders, total_files


def get_base_name(orig_path):
    extension = next(
        (ext for ext in ARCH_EXT if orig_path.strip().lower().endswith(ext)), ""
    )
    if extension != "":
        return re_split(f"{extension}$", orig_path, maxsplit=1, flags=I)[0]
    else:
        raise NotSupportedExtractionArchive("File format not supported for extraction")


async def create_recursive_symlink(source, destination):
    if await aiopath.isdir(source):
        await aiomakedirs(destination, exist_ok=True)
        for item in await listdir(source):
            item_source = ospath.join(source, item)
            item_dest = ospath.join(destination, item)
            await create_recursive_symlink(item_source, item_dest)
    elif await aiopath.isfile(source):
        try:
            await symlink(source, destination)
        except FileExistsError:
            LOGGER.error(f"Shortcut already exists: {destination}")
        except Exception as e:
            LOGGER.error(f"Error creating shortcut for {source}: {e}")


def get_mime_type(file_path):
    if ospath.islink(file_path):
        target = readlink(file_path)
        if not ospath.isabs(target):
            target = ospath.normpath(
                ospath.join(ospath.dirname(file_path), target)
            )
        file_path = target
    mime = Magic(mime=True)
    mime_type = mime.from_file(file_path)
    mime_type = mime_type or "text/plain"
    return mime_type


async def remove_excluded_files(fpath, ee):
    for root, _, files in await sync_to_async(walk, fpath):
        if root.strip().endswith("/yt-dlp-thumb"):
            continue
        for f in files:
            if f.strip().lower().endswith(tuple(ee)):
                await remove(ospath.join(root, f))


async def move_and_merge(source, destination, mid):
    if not await aiopath.exists(destination):
        await aiomakedirs(destination, exist_ok=True)
    for item in await listdir(source):
        item = item.strip()
        src_path = f"{source}/{item}"
        dest_path = f"{destination}/{item}"
        if await aiopath.isdir(src_path):
            if await aiopath.exists(dest_path):
                await move_and_merge(src_path, dest_path, mid)
            else:
                await move(src_path, dest_path)
        else:
            if item.endswith((".aria2", ".!qB")):
                continue
            if await aiopath.exists(dest_path):
                dest_path = f"{destination}/{mid}-{item}"
            await move(src_path, dest_path)


async def join_files(opath):
    from shlex import quote as shquote

    files = await listdir(opath)
    results = []
    exists = False
    for file_ in files:
        if re_search(r"\.0+2$", file_) and await sync_to_async(
            get_mime_type, f"{opath}/{file_}"
        ) not in ["application/x-7z-compressed", "application/zip"]:
            exists = True
            final_name = file_.rsplit(".", 1)[0]
            fpath = f"{opath}/{final_name}"
            quoted = shquote(fpath)
            cmd = f'cat {quoted}.* > {quoted}'
            _, stderr, code = await cmd_exec(cmd, True)
            if code != 0:
                LOGGER.error(f"Failed to join {final_name}, stderr: {stderr}")
                if await aiopath.isfile(fpath):
                    await remove(fpath)
            else:
                results.append(final_name)

    if not exists:
        LOGGER.warning("No files to join!")
    elif results:
        LOGGER.info("Join Completed!")
        for res in results:
            for file_ in files:
                if re_search(rf"{escape(res)}\.0[0-9]+$", file_):
                    await remove(f"{opath}/{file_}")


async def split_file(f_path, split_size, listener):
    out_path = f"{f_path}."
    if listener.is_cancelled:
        return False
    listener.subproc = await create_subprocess_exec(
        "split",
        "--numeric-suffixes=1",
        "--suffix-length=3",
        f"--bytes={split_size}",
        f_path,
        out_path,
        stderr=PIPE,
    )
    _, stderr = await listener.subproc.communicate()
    code = listener.subproc.returncode
    if listener.is_cancelled:
        return False
    if code == -9:
        listener.is_cancelled = True
        return False
    elif code != 0:
        try:
            stderr = stderr.decode().strip()
        except Exception:
            stderr = "Unable to decode the error!"
        LOGGER.error(f"{stderr}. Split Document: {f_path}")
    return True


class SevenZ:
    def __init__(self, listener):
        self._listener = listener
        self._processed_bytes = 0
        self._percentage = "0%"

    @property
    def processed_bytes(self):
        return self._processed_bytes

    @property
    def progress(self):
        return self._percentage

    async def _sevenz_progress(self):
        pattern = r"(\d+)\s+bytes|Total Physical Size\s*=\s*(\d+)"
        while not (
            self._listener.subproc.returncode is not None
            or self._listener.is_cancelled
            or self._listener.subproc.stdout.at_eof()
        ):
            try:
                line = await wait_for(self._listener.subproc.stdout.readline(), 2)
            except Exception:
                break
            line = line.decode().strip()
            if match := re_search(pattern, line):
                self._listener.subsize = int(match[1] or match[2])
            await sleep(0.05)
        s = b""
        while not (
            self._listener.is_cancelled
            or self._listener.subproc.returncode is not None
            or self._listener.subproc.stdout.at_eof()
        ):
            try:
                char = await wait_for(self._listener.subproc.stdout.read(1), 60)
            except Exception:
                break
            if not char:
                break
            s += char
            if char == b"%":
                try:
                    self._percentage = s.decode().rsplit(" ", 1)[-1].strip()
                    self._processed_bytes = (
                        int(self._percentage.strip("%")) / 100
                    ) * self._listener.subsize
                except Exception:
                    self._processed_bytes = 0
                    self._percentage = "0%"
                s = b""
            await sleep(0.05)

        self._processed_bytes = 0
        self._percentage = "0%"

    async def extract(self, f_path, t_path, pswd):
        cmd = [
            "7z",
            "x",
            f"-p{pswd}",
            f_path,
            f"-o{t_path}",
            "-aot",
            "-xr!@PaxHeader",
            "-bsp1",
            "-bse1",
            "-bb3",
        ]
        if not pswd:
            del cmd[2]
        if self._listener.is_cancelled:
            return False
        self._listener.subproc = await create_subprocess_exec(
            *cmd,
            stdout=PIPE,
            stderr=PIPE,
        )
        await self._sevenz_progress()
        _, stderr = await self._listener.subproc.communicate()
        code = self._listener.subproc.returncode
        if self._listener.is_cancelled:
            return False
        if code == -9:
            self._listener.is_cancelled = True
            return False
        elif code != 0:
            try:
                stderr = stderr.decode().strip()
            except Exception:
                stderr = "Unable to decode the error!"
            LOGGER.error(f"{stderr}. Unable to extract archive!. Path: {f_path}")
        return code

    async def zip(self, dl_path, up_path, pswd):
        size = await get_path_size(dl_path)
        if self._listener.equal_splits:
            parts = -(-size // self._listener.split_size)
            split_size = (size // parts) + (size % parts)
        else:
            split_size = self._listener.split_size
        cmd = [
            "7z",
            f"-v{split_size}b",
            "a",
            "-mx=0",
            f"-p{pswd}",
            up_path,
            dl_path,
            "-bsp1",
            "-bse1",
            "-bb3",
        ]
        if self._listener.is_leech and int(size) > self._listener.split_size:
            if not pswd:
                del cmd[4]
            LOGGER.info(f"Zip: orig_path: {dl_path}, zip_path: {up_path}.0*")
        else:
            del cmd[1]
            if not pswd:
                del cmd[3]
            LOGGER.info(f"Zip: orig_path: {dl_path}, zip_path: {up_path}")
        if self._listener.is_cancelled:
            return False
        self._listener.subproc = await create_subprocess_exec(
            *cmd, stdout=PIPE, stderr=PIPE
        )
        await self._sevenz_progress()
        _, stderr = await self._listener.subproc.communicate()
        code = self._listener.subproc.returncode
        if self._listener.is_cancelled:
            return False
        if code == -9:
            self._listener.is_cancelled = True
            return False
        elif code == 0:
            await clean_target(dl_path)
            return up_path
        else:
            if await aiopath.exists(up_path):
                await remove(up_path)
            try:
                stderr = stderr.decode().strip()
            except Exception:
                stderr = "Unable to decode the error!"
            LOGGER.error(f"{stderr}. Unable to zip this path: {dl_path}")
            return dl_path
