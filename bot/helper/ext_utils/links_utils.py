# This file is a part of NEO-WZML (github.com/irisXDR/NEO-WZML)

from re import match as re_match
from base64 import urlsafe_b64decode, urlsafe_b64encode


def is_magnet(url: str):
    return bool(
        re_match(
            r"^magnet:\?.*xt=urn:(btih|btmh):([a-zA-Z0-9]{32,40}|[a-z2-7]{32}).*", url
        )
    )


_URL_MAX_LEN = 8192
# Length-capped to prevent catastrophic backtracking on pathological inputs.
_URL_RE = (
    r"^(?!/)"
    r"(rtmps?://|mms://|rtsp://|https?://|ftp://)?"
    r"([^/:\s]+:[^/@\s]+@)?"
    r"(www\.)?"
    r"([^/:\s]+\.[^/:\s]+)"
    r"(:\d+)?"
    r"(/\S*)?"
    r"$"
)


def is_url(url: str):
    if not isinstance(url, str) or not url or len(url) > _URL_MAX_LEN:
        return False
    return bool(re_match(_URL_RE, url))


def is_gdrive_link(url: str):
    return "drive.google.com" in url or "drive.usercontent.google.com" in url


def is_telegram_link(url: str):
    return url.startswith(("https://t.me/", "tg://openmessage?user_id="))


def is_mega_link(url: str):
    return "mega.nz" in url or "mega.co.nz" in url


_TERABOX_DOMAINS = (
    "terabox.com",
    "terabox.app",
    "teraboxapp.com",
    "1024terabox.com",
    "1024tera.com",
    "freeterabox.com",
    "nephobox.com",
    "4funbox.com",
    "mirrobox.com",
    "momerybox.com",
    "teraboxlink.com",
    "teraboxshare.com",
    "terasharelink.com",
    "terafileshare.com",
    "gibibox.com",
    "goaibox.com",
    "dubox.com",
)


def is_terabox_link(url: str):
    if not isinstance(url, str) or not url:
        return False
    return any(domain in url for domain in _TERABOX_DOMAINS)


def get_mega_link_type(url):
    return "folder" if "folder" in url or "/#F!" in url else "file"


def is_share_link(url: str):
    return bool(
        re_match(
            r"https?:\/\/.+\.gdtot\.\S+|https?:\/\/(filepress|filebee|appdrive|gdflix)\.\S+",
            url,
        )
    )


def is_rclone_path(path: str):
    return bool(
        re_match(
            r"^(mrcc:)?(?!(magnet:|mtp:|sa:|tp:))(?![- ])[a-zA-Z0-9_\. -]+(?<! ):(?!.*\/\/).*$|^rcl$",
            path,
        )
    )


def is_gdrive_id(id_: str):
    if not isinstance(id_, str):
        return False
    return bool(
        re_match(
            r"^(tp:|sa:|mtp:)?(?:[a-zA-Z0-9-_]{33}|[a-zA-Z0-9_-]{19})$|^gdl$|^(tp:|mtp:)?root$",
            id_,
        )
    )


def encode_slink(string):
    return (urlsafe_b64encode(string.encode("ascii")).decode("ascii")).strip("=")


def decode_slink(b64_str):
    return urlsafe_b64decode(
        (b64_str.strip("=") + "=" * (-len(b64_str.strip("=")) % 4)).encode("ascii")
    ).decode("ascii")
