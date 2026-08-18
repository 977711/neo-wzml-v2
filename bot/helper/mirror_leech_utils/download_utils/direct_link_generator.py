import os
import re
import time
from base64 import b64decode, b64encode
from hashlib import sha256
from http.cookiejar import MozillaCookieJar
from json import loads
from os import path as ospath
from random import choice, randint
from re import findall, match, search
from time import sleep
from urllib.parse import parse_qs, quote, unquote, urljoin, urlparse
from uuid import uuid4

import requests
from bs4 import BeautifulSoup as B
from cloudscraper import create_scraper
from lxml.etree import HTML
from requests import Session, get, post
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

from ....core.config_manager import Config
from ...ext_utils.exceptions import DirectDownloadLinkException
from ...ext_utils.help_messages import PASSWORD_ERROR_MESSAGE
from ...ext_utils.links_utils import is_share_link
from ...ext_utils.status_utils import speed_string_to_bytes


def safe_int_size(size):
    """Convert size to integer safely, handling various formats"""
    if size is None:
        return 0
    try:
        if isinstance(size, (int, float)):
            return int(size)
        if isinstance(size, str):
            stripped = size.strip()
            if stripped.isdigit():
                return int(stripped)
            try:
                return int(float(stripped))
            except ValueError:
                try:
                    return speed_string_to_bytes(stripped)
                except Exception:  # pylint: disable=broad-exception-caught
                    return 0
    except (ValueError, TypeError):
        pass
    return 0


PROXY_PREFIX = Config.PROXY_PREFIX
PROXY_URL = Config.PROXY_URL
proxies = {"http": PROXY_URL, "https": PROXY_URL}

user_agent = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:122.0) Gecko/20100101 Firefox/122.0"
)

TERABOX_PREMIUM_HOST = "d8.freeterabox.com"
BYPASSBOT_BASE_URL = "https://dl.bypassbot.workers.dev/"


def decode64(value):
    encoded = str(value).strip()
    encoded += "=" * (-len(encoded) % 4)
    return b64decode(encoded, altchars=b"-_").decode("utf-8")


def _wrap_bypassbot_download(url):
    if not url:
        return url
    if url.startswith(BYPASSBOT_BASE_URL):
        return url
    try:
        parsed = urlparse(str(url).strip())
    except Exception:
        return url
    if not parsed.scheme or not parsed.netloc:
        return url
    if "download.aspx" not in (parsed.path or "").lower():
        return url
    if not parsed.query:
        return url
    encoded = b64encode(url.encode("utf-8")).decode("utf-8")
    return f"{BYPASSBOT_BASE_URL}{encoded}"


def _rewrite_terabox_premium_url(raw_url):
    if not raw_url:
        return raw_url
    try:
        parsed = urlparse(str(raw_url).strip())
    except Exception:
        return raw_url
    if not parsed.scheme or not parsed.netloc:
        return raw_url
    host = (parsed.hostname or "").lower()
    if not host or host == TERABOX_PREMIUM_HOST:
        return raw_url
    premium_aliases = (
        "1024tera.com",
        "nephobox.com",
        "momerybox.com",
        "freeterabox.com",
    )
    if not any(
        host == alias or host.endswith(f".{alias}") for alias in premium_aliases
    ):
        return raw_url
    return parsed._replace(netloc=TERABOX_PREMIUM_HOST).geturl()


def _safe_json_response(response, source_name):
    try:
        return response.json()
    except ValueError:
        status_code = getattr(response, "status_code", "unknown")
        text = (getattr(response, "text", "") or "").strip()
        preview = " ".join(text.split())[:180]
        if preview:
            raise DirectDownloadLinkException(
                f"ERROR: {source_name} returned non-JSON response (status {status_code}): {preview}"
            )
        raise DirectDownloadLinkException(
            f"ERROR: {source_name} returned non-JSON response (status {status_code})."
        )


def _is_api_success(value):
    if isinstance(value, bool):
        return value
    if value is None:
        return False
    if isinstance(value, (int, float)):
        return int(value) in (0, 1, 200)
    text = str(value).strip().lower()
    return text in (
        "success",
        "successfully",
        "ok",
        "true",
        "1",
        "0",
        "200",
        "valid",
        "completed",
        "done",
    )


def _collect_terabox_entries(payload):
    entries = []

    def _is_file_entry(item):
        if not isinstance(item, dict):
            return False
        if any(
            item.get(key)
            for key in (
                "directlink",
                "direct_link",
                "dlink",
                "proxylink",
                "download_url",
                "url",
            )
        ):
            return True
        return any(
            key.startswith("proxy_download") and item.get(key) for key in item.keys()
        )

    def _walk(value):
        if isinstance(value, list):
            for child in value:
                _walk(child)
            return
        if not isinstance(value, dict):
            return
        if _is_file_entry(value):
            entries.append(value)
            return
        for key in ("contents", "files", "list", "data", "result", "records", "items"):
            child = value.get(key)
            if child is not None:
                _walk(child)

    _walk(payload)
    return entries


GOFILE_API_TOKEN_FALLBACK = "LyagEWOR9dFMJnfO0G2i0mSd9qwuqIYN"
GOFILE_API_XWT_FALLBACK = (
    "87bf2e76a7736153e94c430706b921a2c5420ca8e19da3b7cd964f7deacd862f"
)
GOFILE_WEBSITE_TOKEN_CACHE = ""
gofile_token_cache = None

debrid_link_sites = [
    "1fichier.com",
    "anonfiles.com",
    "bayfiles.com",
    "clicknupload.link",
    "clicknupload.org",
    "clicknupload.co",
    "clicknupload.cc",
    "clicknupload.download",
    "clicknupload.club",
    "dailyuploads.net",
    "ddl.to",
    "ddownload.com",
    "ddownload.link",
    "drop.download",
    "dropbox.com",
    "dropboxusercontent.com",
    "easyupload.io",
    "emload.com",
    "file.al",
    "fileaxa.com",
    "filecat.net",
    "filedot.to",
    "filedot.xyz",
    "filextras.com",
    "filer.net",
    "filespace.com",
    "filestore.me",
    "gigapeta.com",
    "gofile.io",
    "hexupload.net",
    "hitfile.net",
    "htfl.net",
    "hulkshare.com",
    "isra.cloud",
    "katfile.com",
    "kshared.com",
    "mediafire.com",
    "mega.nz",
    "mega.co.nz",
    "mexashare.com",
    "mixdrop.co",
    "mixdrop.to",
    "mixdrop.sx",
    "mixdrop.club",
    "modsbase.com",
    "nelion.me",
    "pixeldrain.com",
    "prefiles.com",
    "racaty.net",
    "rapidgator.net",
    "rapidgator.asia",
    "rg.to",
    "scribd.com",
    "send.cm",
    "sharemods.com",
    "silkfiles.com",
    "soundcloud.com",
    "streamtape.com",
    "tezfiles.com",
    "turb.cc",
    "turb.to",
    "turbobit.net",
    "turbobit.cc",
    "turbobit.pw",
    "turbobit.online",
    "turbobit.ru",
    "turbobit.live",
    "trubobit.com",
    "turboblt.co",
    "uloz.to",
    "ulozto.net",
    "ulozto.sk",
    "ulozto.cz",
    "upload.ee",
    "uploadhaven.com",
    "up-4ever.com",
    "up-4ever.net",
    "uptobox.com",
    "uptobox.fr",
    "uptobox.eu",
    "uptobox.link",
    "uptostream.com",
    "uptostream.fr",
    "uptostream.eu",
    "uptostream.link",
    "upvid.pro",
    "upvid.live",
    "upvid.host",
    "upvid.biz",
    "upvid.cloud",
    "uqload.com",
    "uqload.co",
    "uqload.io",
    "userload.co",
    "uploadgig.com",
    "usersdrive.com",
    "vidoza.net",
    "voe.sx",
    "voe-unblock.com",
    "voeunblock1.com",
    "voeunblock2.com",
    "voeunblock3.com",
    "voeunbl0ck.com",
    "voeunblck.com",
    "voeunblk.com",
    "voe-un-block.com",
    "voeun-block.net",
    "workupload.com",
    "world-bytez.com",
    "worldbytez.com",
    "world-files.com",
    "wupfile.com",
    "zippyshare.com",
]


def direct_link_generator(link):
    """direct links generator"""
    link = str(link).strip()
    bypassed = _wrap_bypassbot_download(link)
    if bypassed != link:
        return bypassed
    domain = urlparse(link).hostname
    if not domain:
        raise DirectDownloadLinkException("ERROR: Invalid URL")
    elif Config.DEBRID_LINK_API and any(x in domain for x in debrid_link_sites):
        return debrid_link(link)
    elif "yadi.sk" in link or "disk.yandex." in link:
        return yandex_disk(link)
    elif (
        "gdlink.dev" in domain
        or "gdflix.dad" in domain
        or "vifix.site/gdflix" in domain
        or "gdflix.dev" in domain
        or "gdflix.app" in domain
    ):
        return gdflix(link)
    elif "driveseed" in domain:
        return gdflix(link)
    elif any(
        x in domain
        for x in [
            "hubcloud",
            "hubcloud.fit",
            "hubcloud.one",
            "hubcloud.pro",
            "hubcloud.cc",
            "hubcloud.link",
            "hubcloud.xyz",
            "hubcloud.in",
        ]
    ):
        return hubcloud(link)
    elif "vifix.site/hubcloud" in domain:
        return hubcloud(link)
    elif "buzzheavier.com" in domain:
        return buzzheavier(link)
    elif "devuploads" in domain:
        return devuploads(link)
    elif "lulacloud.com" in domain:
        return lulacloud(link)
    elif "fuckingfast.co" in domain:
        return fuckingfast_dl(link)
    elif "mediafire.com" in domain:
        return mediafire(link)
    elif "osdn.net" in domain:
        return osdn(link)
    elif "sourceforge.net" in domain:
        return sourceforge(link)
    elif "github.com" in domain:
        return github(link)
    elif "hxfile.co" in domain:
        return hxfile(link)
    elif "1drv.ms" in domain:
        return onedrive(link)
    elif any(
        x in domain
        for x in [
            "pixeldrain.com",
            "pixeldra.in",
            "pixeldrain.net",
            "cdn.pixeldrain.eu.cc",
        ]
    ):
        return pixeldrain(link)
    elif "racaty" in domain:
        return racaty(link)
    elif "1fichier.com" in domain:
        return fichier(link)
    elif "solidfiles.com" in domain:
        return solidfiles(link)
    elif "krakenfiles.com" in domain:
        return krakenfiles(link)
    elif "upload.ee" in domain:
        return uploadee(link)
    elif "z-lib.gd" in domain:
        return zlib(link)
    elif "uploadhaven" in domain:
        return uploadhaven(link)
    elif "gofile.io" in domain:
        return gofile(link)
    elif "send.cm" in domain:
        return send_cm(link)
    elif "tmpsend.com" in domain:
        return tmpsend(link)
    elif "easyupload.io" in domain:
        return easyupload(link)
    elif "mediafile.cc" in domain:
        return mediafile(link)
    elif "sharemods.com" in domain:
        return sharemods(link)
    elif "streamvid.net" in domain:
        return streamvid(link)
    elif "shrdsk.me" in domain:
        return shrdsk(link)
    elif "u.pcloud.link" in domain:
        return pcloud(link)
    elif "qiwi.gg" in domain:
        return qiwi(link)
    elif "mp4upload.com" in domain:
        return mp4upload(link)
    elif "transfer.it" in domain:
        return transfer_it(link)
    elif "berkasdrive.com" in domain:
        return berkasdrive(link)
    elif "swisstransfer.com" in domain:
        return swisstransfer(link)
    elif "instagram.com" in domain:
        return instagram(link)
    elif "apkadmin.com" in domain:
        return apkadmin(link)
    elif any(x in domain for x in ["akmfiles.com", "akmfls.xyz"]):
        return akmfiles(link)
    elif any(
        x in domain
        for x in [
            "dood.watch",
            "doodstream.com",
            "dood.to",
            "dood.so",
            "dood.cx",
            "dood.la",
            "dood.ws",
            "dood.sh",
            "doodstream.co",
            "dood.pm",
            "dood.wf",
            "dood.re",
            "dood.video",
            "dooood.com",
            "dood.yt",
            "doods.yt",
            "dood.stream",
            "doods.pro",
            "ds2play.com",
            "d0o0d.com",
            "ds2video.com",
            "do0od.com",
            "d000d.com",
        ]
    ):
        return doods(link)
    elif any(x in domain for x in ["vide10.com", "vide4.com", "vide9.com"]):
        return videq(link)
    elif any(
        x in domain
        for x in [
            "streamtape.com",
            "streamtape.co",
            "streamtape.cc",
            "streamtape.to",
            "streamtape.net",
            "streamta.pe",
            "streamtape.xyz",
        ]
    ):
        return streamtape(link)
    elif any(x in domain for x in ["wetransfer.com", "we.tl"]):
        return wetransfer(link)
    elif any(
        x in domain
        for x in [
            "terabox.com",
            "nephobox.com",
            "4funbox.com",
            "mirrobox.com",
            "momerybox.com",
            "teraboxapp.com",
            "1024tera.com",
            "terabox.app",
            "gibibox.com",
            "goaibox.com",
            "terasharelink.com",
            "teraboxlink.com",
            "freeterabox.com",
            "1024terabox.com",
            "teraboxshare.com",
            "terafileshare.com",
            "terabox.club",
        ]
    ):
        return terabox(link)
    elif any(
        x in domain
        for x in [
            "filelions.co",
            "filelions.site",
            "filelions.live",
            "filelions.to",
            "mycloudz.cc",
            "cabecabean.lol",
            "filelions.online",
            "embedwish.com",
            "kitabmarkaz.xyz",
            "wishfast.top",
            "streamwish.to",
            "kissmovies.net",
        ]
    ):
        return filelions_and_streamwish(link)
    elif any(x in domain for x in ["streamhub.ink", "streamhub.to"]):
        return streamhub(link)
    elif any(
        x in domain
        for x in [
            "linkbox.to",
            "lbx.to",
            "teltobx.net",
            "telbx.net",
        ]
    ):
        return linkBox(link)
    elif is_share_link(link):
        if "gdtot" in domain:
            return gdtot(link)
        elif "filepress" in domain:
            return filepress(link)
        else:
            return sharer_scraper(link)
    elif any(
        x in domain
        for x in [
            "anonfiles.com",
            "zippyshare.com",
            "letsupload.io",
            "hotfile.io",
            "bayfiles.com",
            "megaupload.nz",
            "letsupload.cc",
            "filechan.org",
            "myfile.is",
            "vshare.is",
            "rapidshare.nu",
            "lolabits.se",
            "openload.cc",
            "share-online.is",
            "upvid.cc",
            "uptobox.com",
            "uptobox.fr",
        ]
    ):
        raise DirectDownloadLinkException(f"ERROR: R.I.P {domain}")
    else:
        raise DirectDownloadLinkException(f"No Direct link function found for {link}")


def get_captcha_token(session, params):
    recaptcha_api = "https://www.google.com/recaptcha/api2"
    res = session.get(f"{recaptcha_api}/anchor", params=params)
    anchor_html = HTML(res.text)
    if not (anchor_token := anchor_html.xpath('//input[@id="recaptcha-token"]/@value')):
        return
    params["c"] = anchor_token[0]
    params["reason"] = "q"
    res = session.post(f"{recaptcha_api}/reload", params=params)
    if token := findall(r'"rresp","(.*?)"', res.text):
        return token[0]


def transfer_it(url):
    parsed = urlparse(url)
    match_obj = match(r"^/t/([A-Za-z0-9_-]+)$", parsed.path.rstrip("/"))
    if not match_obj:
        raise DirectDownloadLinkException("ERROR: Invalid Transfer.it link")

    xh = match_obj.group(1)
    pw = parse_qs(parsed.query).get("pw", [""])[0].strip()
    headers = {
        "Content-Type": "application/json",
        "User-Agent": user_agent,
        "Origin": "https://transfer.it",
        "Referer": "https://transfer.it/",
    }

    def _call_api(payload, include_transfer=False):
        params = {"id": str(int(time.time() * 1000))}
        if include_transfer:
            params["x"] = xh
            if pw:
                params["pw"] = pw

        try:
            resp = post(
                "https://bt7.api.mega.co.nz/cs",
                params=params,
                json=[payload],
                headers=headers,
            )
        except Exception as e:
            raise DirectDownloadLinkException(f"ERROR: Transfer.it request failed: {e}")

        if resp.status_code != 200:
            raise DirectDownloadLinkException(
                f"ERROR: Transfer.it API returned HTTP {resp.status_code}"
            )

        try:
            result = resp.json()[0]
        except Exception as e:
            raise DirectDownloadLinkException(
                f"ERROR: Invalid response from Transfer.it: {e}"
            )

        if isinstance(result, int) and result < 0:
            if result in {-16, -9}:
                raise DirectDownloadLinkException(
                    "ERROR: File expired, password is invalid, or file was not found"
                )
            raise DirectDownloadLinkException(
                f"ERROR: Transfer.it API returned error code {result}"
            )
        return result

    info = _call_api({"a": "xi", "xh": xh})
    listing = _call_api({"a": "f", "c": 1, "r": 1}, include_transfer=True)
    nodes = listing.get("f") or []
    file_nodes = [node for node in nodes if node.get("t") == 0 and node.get("h")]

    if not file_nodes:
        raise DirectDownloadLinkException(
            "ERROR: No downloadable files found on Transfer.it"
        )

    if len(file_nodes) == 1:
        target_handle = file_nodes[0]["h"]
    else:
        target_handle = info.get("z") or info.get("zp")
        if not target_handle:
            raise DirectDownloadLinkException(
                "ERROR: Transfer.it folder links without a downloadable archive are not supported"
            )

    result = _call_api(
        {"a": "g", "n": target_handle, "pt": 1, "g": 1, "ssl": 1},
        include_transfer=True,
    )
    direct_link = result.get("g")
    if not direct_link or not str(direct_link).startswith("http"):
        raise DirectDownloadLinkException(
            "ERROR: Failed to resolve Transfer.it direct link"
        )

    file_name = ""
    title_b64 = str(info.get("t") or "").strip()
    if title_b64:
        try:
            file_name = b64decode(title_b64).decode("utf-8", "replace").strip()
        except Exception:
            file_name = ""

    if not file_name and len(file_nodes) == 1:
        file_name = str(file_nodes[0].get("name") or "").strip()

    if not file_name:
        return direct_link

    safe_name = quote(file_name, safe="")
    return (
        direct_link,
        [f"Content-Disposition: attachment; filename*=UTF-8''{safe_name}"],
        file_name,
    )


def debrid_link(url):
    cget = create_scraper().request
    resp = cget(
        "POST",
        f"https://debrid-link.com/api/v2/downloader/add?access_token={Config.DEBRID_LINK_API}",
        data={"url": url},
        proxies=proxies,
    ).json()

    if resp["success"] is not True:
        raise DirectDownloadLinkException(
            f"ERROR: {resp['error']} & ERROR ID: {resp['error_id']}"
        )

    if isinstance(resp["value"], dict):
        return PROXY_PREFIX + resp["value"]["downloadUrl"]

    if isinstance(resp["value"], list):
        details = {
            "contents": [],
            "title": unquote(url.rstrip("/").split("/")[-1]),
            "total_size": 0,
        }
        for dl in resp["value"]:
            if dl.get("expired", False):
                continue
            file_size = safe_int_size(dl.get("size", 0))
            item = {
                "path": ospath.join(details["title"]),
                "filename": dl["name"],
                "url": PROXY_PREFIX + dl["downloadUrl"],
                "size": file_size,
            }
            details["total_size"] += file_size
            details["contents"].append(item)
        return details


def terabox_sonza(url: str):
    if "/file/" in url:
        return url

    api_url = f"https://terabox.sonzaix.xyz/api?key=sonzaix&url={quote(url)}"
    try:
        with Session() as session:
            response = session.get(api_url, timeout=30)
            resp = _safe_json_response(response, "TeraBox Sonza API")
    except DirectDownloadLinkException:
        raise
    except Exception as err:
        raise DirectDownloadLinkException(f"ERROR: {err}") from err

    details = {"contents": [], "title": "", "total_size": 0}
    files = _collect_terabox_entries(resp)

    status_raw = resp.get("status")
    if status_raw is None:
        status_raw = resp.get("ok")
    if status_raw is None:
        status_raw = resp.get("success")

    if not _is_api_success(status_raw) and not files:
        message = (
            resp.get("message")
            or resp.get("error")
            or "Terabox API returned failure status"
        )
        raise DirectDownloadLinkException(f"ERROR: {message} (status: {status_raw})")

    if not files:
        raise DirectDownloadLinkException("ERROR: No files returned from API")

    for data in files:
        if not isinstance(data, dict):
            continue
        proxylink = (
            data.get(f"proxy_download{randint(1, 6)}")
            or data.get("proxylink")
            or data.get("directlink")
            or data.get("dlink")
            or data.get("url")
        )
        proxylink = _rewrite_terabox_premium_url(proxylink)
        if not proxylink:
            continue
        size = safe_int_size(data.get("size"))
        item = {
            "path": data.get("path", ""),
            "filename": data.get("filename") or data.get("name") or "Terabox File",
            "url": proxylink,
            "size": size,
        }
        details["contents"].append(item)
        details["total_size"] += size
    if not details["contents"]:
        raise DirectDownloadLinkException("ERROR: Failed to parse Terabox API response")
    details["title"] = files[0].get("filename") or files[0].get("name") or "Terabox"
    if len(details["contents"]) == 1:
        return details["contents"][0]["url"]
    return details


def terabox(url, password=None):
    """TeraBox direct link generator using pilli-beta style API parsing."""
    if "/file/" in url:
        return url
    if "::" in url:
        password = url.split("::")[-1]
        url = url.split("::")[0]

    api_url = "https://terascrape.vercel.app/api"
    params = {"apikey": "pikocak", "url": url}
    if password:
        params["password"] = password

    try:
        response = get(api_url, params=params, timeout=60)
        req = _safe_json_response(response, "TeraBox API")
    except Exception:
        return terabox_scrape(url)

    if not isinstance(req, dict):
        raise DirectDownloadLinkException("ERROR: Respon API tidak valid.")

    details = {"contents": [], "title": "", "total_size": 0, "source": "terabox"}
    details["source_url"] = req.get("request_url") or req.get("source_url") or url
    if password:
        details["source_password"] = password

    header = ""
    header_lines = []
    download_headers = req.get("download_headers")
    if isinstance(download_headers, dict):
        for key, value in download_headers.items():
            key_text = str(key).strip()
            value_text = str(value).strip() if value is not None else ""
            if key_text and value_text:
                header_lines.append(f"{key_text}: {value_text}")
    elif isinstance(download_headers, (list, tuple, set)):
        for item in download_headers:
            line = str(item).strip()
            if line and ":" in line:
                header_lines.append(line)
    elif download_headers:
        text = str(download_headers).strip()
        if text:
            parts = (
                text.replace("\n", "|").split("|")
                if "|" in text or "\n" in text
                else [text]
            )
            for part in parts:
                line = part.strip()
                if line and ":" in line:
                    header_lines.append(line)
    if header_lines:
        header = "\n".join(header_lines)
        details["header"] = header

    entries = _collect_terabox_entries(req)

    status_raw = req.get("status")
    if status_raw is None:
        status_raw = req.get("ok")
    if status_raw is None:
        status_raw = req.get("success")

    if not _is_api_success(status_raw) and not entries:
        message = req.get("message") or req.get("error") or "Link File tidak ditemukan!"
        try:
            return terabox_scrape(url)
        except Exception as fallback_error:
            raise DirectDownloadLinkException(
                f"ERROR: {message} (status: {status_raw}) | fallback: {fallback_error}"
            )
    for data in entries:
        if not isinstance(data, dict):
            continue
        direct_url = (
            data.get("directlink")
            or data.get("dlink")
            or data.get("proxylink")
            or data.get("download_url")
            or data.get("url")
        )
        direct_url = _rewrite_terabox_premium_url(direct_url)
        if not direct_url:
            continue
        filename = data.get("filename") or data.get("name")
        if not filename:
            file_path = data.get("path") or ""
            filename = ospath.basename(file_path) if file_path else "file"
        item = {
            "path": data.get("path", ""),
            "filename": filename,
            "url": direct_url,
            "source": "terabox",
            "source_url": url,
        }
        if password:
            item["source_password"] = password
        size = data.get("size")
        if size is not None:
            try:
                size_int = int(size)
            except (TypeError, ValueError):
                size_int = 0
            item["size"] = size_int
            details["total_size"] += size_int
        details["contents"].append(item)

    if not details["contents"]:
        try:
            return terabox_scrape(url)
        except Exception as fallback_error:
            raise DirectDownloadLinkException(
                f"ERROR: Link File tidak ditemukan! | fallback: {fallback_error}"
            )

    details["title"] = req.get("title") or req.get("extracted_shorturl") or ""
    if not details["total_size"]:
        total_size = req.get("total_size")
        if total_size is not None:
            try:
                details["total_size"] = int(total_size)
            except (TypeError, ValueError):
                pass

    if len(details["contents"]) == 1:
        if header:
            return details["contents"][0]["url"], header
        return details["contents"][0]["url"]
    return details


def _resolve_terabox_cookie_file():
    for candidate in (
        "terabox.txt",
        "cookies/terabox.txt",
        "cookies.txt",
        "pilla/terabox.txt",
        "pilla/cookies/terabox.txt",
        "pilla/cookies.txt",
    ):
        if ospath.isfile(candidate):
            return candidate
    return None


def terabox_scrape(url: str):
    cookie_file = _resolve_terabox_cookie_file()
    if not cookie_file:
        raise DirectDownloadLinkException(
            "ERROR: terabox cookies file not found (tried terabox.txt, cookies/terabox.txt, cookies.txt, pilla/terabox.txt, pilla/cookies/terabox.txt, pilla/cookies.txt)"
        )
    try:
        jar = MozillaCookieJar(cookie_file)
        jar.load(ignore_discard=True, ignore_expires=True)
        cookies = {}
        for cookie in jar:
            cookies[cookie.name] = cookie.value
    except Exception as e:
        raise DirectDownloadLinkException(f"ERROR: {e.__class__.__name__}")

    def _fetch_link(folder=""):
        with Session() as ses:
            try:
                _res = ses.get(url, cookies=cookies)
                token_match = search(r'window\.jsToken\s*=\s*"([^"]+)"', _res.text)
                if not token_match:
                    token_match = search(r"window\\.jsToken.*?%22(.*?)%22", _res.text)
                jsToken = token_match.group(1) if token_match else ""
                if "%" in jsToken:
                    jsToken = unquote(jsToken)

                parsed_share = urlparse(_res.url)
                shortUrl = parse_qs(parsed_share.query).get("surl", [""])[0]
                if not shortUrl:
                    path_parts = [part for part in parsed_share.path.split("/") if part]
                    if path_parts:
                        shortUrl = path_parts[-1]

                params = {
                    "app_id": "250528",
                    "jsToken": jsToken,
                    "shorturl": shortUrl,
                }
                if folder:
                    params["dir"] = folder
                else:
                    params["root"] = "1"
                list_response = ses.get(
                    "https://terabox.app/share/list", params=params, cookies=cookies
                )
                data = _safe_json_response(list_response, "TeraBox share/list API")
            except DirectDownloadLinkException:
                raise
            except Exception as e:
                raise DirectDownloadLinkException(
                    f"ERROR: {e.__class__.__name__} saat mengambil data dari TeraBox"
                )

        if data.get("errno") in [0, "0"] and data.get("list"):
            for file in data.get("list", []):
                if not details["title"]:
                    if "title" in data and data["title"]:
                        details["title"] = data["title"].split("/")[-1]
                    else:
                        details["title"] = shortUrl
                if file["isdir"] in [1, "1"]:
                    _fetch_link(folder=file["path"])
                else:
                    filepaths = file["path"].split("/")
                    item = {
                        "path": "/".join(filepaths[:-1]),
                        "filename": filepaths[-1],
                        "url": _rewrite_terabox_premium_url(file["dlink"]),
                    }
                    details["contents"].append(item)
                    details["total_size"] += int(file["size"])

        else:
            raise DirectDownloadLinkException("ERROR: Link File tidak ditemukan!")

    details = {"contents": [], "title": f"", "total_size": 0}
    cookie_value = "; ".join(
        f"{key}={value}" for key, value in cookies.items() if key and value is not None
    )
    header_lines = []
    if cookie_value:
        header_lines.append(f"Cookie: {cookie_value}")
    header_lines.append(f"User-Agent: {user_agent}")
    header_lines.append("Referer: https://www.terabox.com/")
    details["header"] = "\n".join(header_lines)
    try:
        _fetch_link()
    except Exception as e:
        raise DirectDownloadLinkException(f"ERROR: {e}")
    return details


GDFLIX_DOMAINN = "https://new10.gdflix.dad"


def gdflix(url):
    """
    Fetches downloadable links from a GDFlix page.
    Returns direct download links in the same format as gofile:
    - Single file: (url, headers) or url string
    - Pack/folder: {"contents": [...], "title": "...", "total_size": 0}
    Uses proxy from get_hubcloud_proxy().
    """
    from bs4 import BeautifulSoup
    import re

    try:
        from curl_cffi import requests as c_requests
    except ImportError:
        raise DirectDownloadLinkException("curl_cffi not installed!")

    def _wrap(link):
        return (link, {"User-Agent": user_agent})

    code = url.split("/")[-1] if not url.endswith("/") else url.split("/")[-2]

    # Parse the original URL to get domain
    parsed_url = urlparse(url)
    original_domain = parsed_url.netloc
    scheme = parsed_url.scheme or "https"

    # Only reconstruct URL if it's not already a proper file/pack URL
    if "/file/" not in url and "/pack/" not in url:
        # Use original domain if it's a gdflix domain, otherwise use default
        if any(x in original_domain for x in ["gdflix", "gdlink", "vifix"]):
            url = f"{scheme}://{original_domain}/file/{code}"
        else:
            url = f"{GDFLIX_DOMAINN}/file/{code}"
    # If URL already has /file/ or /pack/, use it as-is

    # Setup client
    client = c_requests.Session()
    client.headers.update(
        {
            "User-Agent": user_agent,
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "en-US,en;q=0.5",
            "DNT": "1",
            "Connection": "keep-alive",
            "Upgrade-Insecure-Requests": "1",
        }
    )

    parsed = urlparse(url)
    if "gdlink" in parsed.netloc:
        res = client.get(url, verify=False, impersonate="chrome110")
        soup = BeautifulSoup(res.text, "html.parser")
        gdflix_btn = soup.find("a", href=lambda x: x and "gdflix" in x)
        if gdflix_btn:
            new_url = gdflix_btn["href"]
            if new_url.endswith(".net") or new_url.endswith(".dad"):
                new_url = f"{new_url}/file/{url.split('/')[-1]}"
            return gdflix(new_url)
        if "/c/s/" in res.url:
            url = "https://" + res.url.split("/c/s/")[-1]
        else:
            url = res.url

    try:
        res = client.get(url, timeout=30, verify=False, impersonate="chrome110")
    except Exception as e:
        raise DirectDownloadLinkException(f"ERROR: Request failed: {e}")

    url = res.url
    domain = urlparse(url).netloc
    dcode = url.split("/")[-1]
    soup = BeautifulSoup(res.text, "html.parser")

    # Handle pack/folder URLs
    if "/pack/" in url:
        title_tag = soup.find("h3")
        title = title_tag.text if title_tag else f"GDFlix_Pack_{code}"
        details = {"contents": [], "title": title, "total_size": 0}

        all_links = soup.select('a[href^="/file/"]')
        for link in all_links:
            temp_url = f"https://{domain}{link['href']}"
            try:
                # Fetch the individual file page to get size
                file_res = client.get(temp_url, timeout=30)
                file_soup = BeautifulSoup(file_res.text, "html.parser")

                # Extract filename and size from file page
                name_elem = file_soup.find(
                    "li",
                    class_="list-group-item",
                    string=lambda text: text and "Name :" in text,
                )
                file_name = (
                    name_elem.text.split("Name : ")[-1]
                    if name_elem
                    else link.get_text(strip=True)
                    or f"File_{len(details['contents']) + 1}"
                )

                size_elem = file_soup.find(
                    "li",
                    class_="list-group-item",
                    string=lambda text: text and "Size :" in text,
                )
                size_str = size_elem.text.split("Size : ")[-1] if size_elem else "0"

                # Parse size string to bytes (e.g., "1.5 GB" -> bytes)
                file_size = 0
                try:
                    size_str = size_str.strip().upper()
                    if "GB" in size_str:
                        file_size = int(
                            float(size_str.replace("GB", "").strip())
                            * 1024
                            * 1024
                            * 1024
                        )
                    elif "MB" in size_str:
                        file_size = int(
                            float(size_str.replace("MB", "").strip()) * 1024 * 1024
                        )
                    elif "KB" in size_str:
                        file_size = int(
                            float(size_str.replace("KB", "").strip()) * 1024
                        )
                    elif "B" in size_str:
                        file_size = int(float(size_str.replace("B", "").strip()))
                except (ValueError, TypeError):
                    file_size = 0

                # Get direct download link using recursive call
                result = gdflix(temp_url)
                dl_url = None

                if isinstance(result, tuple):
                    dl_url = result[0]
                elif isinstance(result, str):
                    dl_url = result
                elif isinstance(result, dict):
                    # Nested pack
                    nested_contents = result.get("contents", [])
                    if nested_contents:
                        details["contents"].extend(nested_contents)
                        details["total_size"] += result.get("total_size", 0)
                    continue

                if dl_url:
                    details["contents"].append(
                        {
                            "path": "",
                            "filename": file_name,
                            "url": dl_url,
                        }
                    )
                    details["total_size"] += file_size

            except Exception:
                continue

        if not details["contents"]:
            raise DirectDownloadLinkException("ERROR: No download links found in pack")
        return details

    # Single file handling - extract title and size - try multiple methods
    title = None

    # Method 1: Look for list-group-item with Name
    title_elem = soup.find(
        "li", class_="list-group-item", string=lambda text: text and "Name :" in text
    )
    if title_elem:
        title = title_elem.text.split("Name : ")[-1]

    # Method 2: Try h2 tag
    if not title:
        h2_tag = soup.find("h2")
        if h2_tag:
            h2_text = h2_tag.get_text(strip=True)
            if "File Size" in h2_text:
                title = h2_text.split("File Size")[0].strip()
            else:
                title = h2_text.strip()

    # Method 3: Try h3 tag
    if not title:
        h3_tag = soup.find("h3")
        if h3_tag:
            title = h3_tag.get_text(strip=True)

    # Method 4: Fallback to code
    if not title:
        title = f"GDFlix_File_{code}"

    size_elem = soup.find(
        "li", class_="list-group-item", string=lambda text: text and "Size :" in text
    )
    size = size_elem.text.split("Size : ")[-1] if size_elem else "Unknown"

    # Priority order for download links
    # 1. Cloud Download (.dev links)
    cloud_dl = soup.find(
        lambda tag: (
            tag.name == "a"
            and "cloud download" in tag.get_text(strip=True).lower()
            and ".dev" in tag.get("href", "")
        )
    )
    if cloud_dl:
        href = cloud_dl["href"]
        if "/?url=" in href:
            dl_link = href.split("/?url=", maxsplit=1)[1]
            if dl_link.startswith("https%3A"):
                dl_link = unquote(dl_link)
            return _wrap(dl_link)
        return _wrap(href)

    # 2. Fast Cloud Download (xfile/zfile pages need POST request)
    fast_dl = soup.find(
        lambda tag: (
            tag.name == "a"
            and "fast cloud" in tag.get_text(strip=True).lower()
            and ("xfile" in tag.get("href", "") or "zfile" in tag.get("href", ""))
        )
    )
    if fast_dl:
        try:
            zfile_url = f"https://{domain}" + fast_dl["href"]
            res3 = client.get(
                zfile_url, timeout=30, verify=False, impersonate="chrome110"
            )

            # zfile/xfile pages need POST request to get actual download link
            soup3 = BeautifulSoup(res3.text, "html.parser")

            # Check if page has post-based download (new1.gdflix.app style)
            if re.search(r"async function generate", res3.text):
                # Extract the key from JavaScript
                key_match = re.search(
                    r'formData\.append\("key",\s*"([^"]+)"\)', res3.text
                )
                key = key_match.group(1) if key_match else ""

                # Make POST request to get download link
                post_data = {"action": "cloud", "key": key, "action_token": ""}
                post_headers = {"x-token": domain}
                post_res = client.post(
                    res3.url,
                    data=post_data,
                    headers=post_headers,
                    timeout=30,
                    verify=False,
                    impersonate="chrome110",
                )

                if post_res.status_code == 200:
                    try:
                        json_data = loads(post_res.text)
                        download_url = json_data.get("visit_url") or json_data.get(
                            "url"
                        )
                        if download_url:
                            # Convert relative URL to absolute
                            if not download_url.startswith("http"):
                                download_url = f"https://{domain}{download_url}"

                            # If URL is another zfile/xfile token URL, follow it recursively
                            if "/zfile/" in download_url or "/xfile/" in download_url:
                                try:
                                    token_res = client.get(
                                        download_url,
                                        timeout=30,
                                        verify=False,
                                        impersonate="chrome110",
                                    )
                                    token_soup = BeautifulSoup(
                                        token_res.text, "html.parser"
                                    )

                                    # Check if token page also needs POST request
                                    if re.search(
                                        r"async function generate", token_res.text
                                    ):
                                        key_match2 = re.search(
                                            r'formData\.append\("key",\s*"([^"]+)"\)',
                                            token_res.text,
                                        )
                                        key2 = key_match2.group(1) if key_match2 else ""

                                        post_data2 = {
                                            "action": "cloud",
                                            "key": key2,
                                            "action_token": "",
                                        }
                                        post_headers2 = {"x-token": domain}
                                        post_res2 = client.post(
                                            token_res.url,
                                            data=post_data2,
                                            headers=post_headers2,
                                            timeout=30,
                                            verify=False,
                                            impersonate="chrome110",
                                        )

                                        if post_res2.status_code == 200:
                                            try:
                                                json_data2 = loads(post_res2.text)
                                                final_url = json_data2.get(
                                                    "visit_url"
                                                ) or json_data2.get("url")
                                                if final_url:
                                                    if not final_url.startswith("http"):
                                                        final_url = f"https://{domain}{final_url}"
                                                    return final_url
                                            except Exception:
                                                pass

                                    # Look for actual download links (Google Drive, GoFile, etc.)
                                    for a in token_soup.find_all("a", href=True):
                                        href = a.get("href", "")
                                        if any(
                                            x in href
                                            for x in [
                                                "drive.google.com",
                                                "googleapis.com",
                                                "gofile.io",
                                                "1fichier.com",
                                                "pixeldrain",
                                                "mega.nz",
                                                "workers.dev",
                                            ]
                                        ):
                                            return _wrap(href)
                                except Exception:
                                    pass

                            # Don't return download_url here - might be HTML page
                            # Let it fall through to wfile endpoint
                    except Exception:
                        pass

        except Exception:
            pass

    # 3. Instant DL (CDN)
    instant_dl = soup.find(
        lambda tag: (
            tag.name == "a"
            and "instant dl" in tag.get_text(strip=True).lower()
            and "cdn" in tag.get("href", "")
        )
    )
    if instant_dl:
        try:
            res4 = client.get(instant_dl["href"], timeout=30)
            final_url = res4.url.split("?url=")[-1]
            if final_url.startswith("http"):
                return _wrap(final_url)
            # Try to extract from page
            ddd = urlparse(res4.url).netloc
            match1 = re.search(r'href\s?=\s?"([^\"]+)"', res4.text)
            if match1:
                res6 = client.get("https://" + ddd + match1.group(1), timeout=30)
                soup6 = BeautifulSoup(res6.text, "html.parser")
                ff = soup6.select_one(
                    'a[href^="https://video-downloads.googleusercontent.com"]'
                )
                if ff:
                    return _wrap(ff["href"])
        except Exception:
            pass

    # 3.5. GoFlix intermediate page - fetch to extract actual download link
    goflix = soup.find(
        lambda tag: tag.name == "a" and "goflix.sbs" in tag.get("href", "")
    )
    if goflix and goflix.get("href"):
        try:
            goflix_res = client.get(
                goflix["href"], timeout=30, verify=False, impersonate="chrome110"
            )
            if goflix_res.status_code == 200:
                goflix_soup = BeautifulSoup(goflix_res.text, "html.parser")

                # First try to extract gofile.io link
                for a in goflix_soup.find_all("a", href=True):
                    href = a.get("href", "")
                    if "gofile.io" in href:
                        return _wrap(href)

                # Fallback to other hosts
                for a in goflix_soup.find_all("a", href=True):
                    href = a.get("href", "")
                    if any(h in href for h in ["1fichier.com", "pixeldrain"]):
                        return _wrap(href)
        except Exception:
            pass

    # 4. GoFile
    go_ = soup.find(
        lambda tag: tag.name == "a" and "gofile" in tag.get_text(strip=True).lower()
    )
    if go_ and go_.get("href") and "multiup.php" not in go_["href"]:
        try:
            res2 = client.get(go_["href"], timeout=30)
            match = re.search(r"https://gofile\.io/d/\w+", res2.text)
            if match:
                return _wrap(match.group())
        except Exception:
            pass

    # 5. PixelDrain
    pixeldrain = soup.find(
        lambda tag: tag.name == "a" and "pixeldrain" in tag.get_text(strip=True).lower()
    )
    if pixeldrain and pixeldrain.get("href"):
        return _wrap(pixeldrain["href"])

    # 6. MGT Server
    mgt_server = soup.find(
        lambda tag: tag.name == "a" and "mgt" in tag.get_text(strip=True).lower()
    )
    if mgt_server and mgt_server.get("href"):
        return _wrap(mgt_server["href"])

    # 7. Try wfile endpoint
    try:
        lnks = f"https://{domain}/wfile/{dcode}"
        res5 = client.get(lnks, timeout=30)
        soup4 = BeautifulSoup(res5.text, "html.parser")
        d_j = soup4.find_all(
            lambda tag: (
                tag.name == "a"
                and "download" in tag.get_text(strip=True).lower()
                and ".dev" in tag.get("href", "")
            )
        )
        for i in d_j:
            if i.get("href"):
                return _wrap(i["href"])
    except Exception:
        pass

    raise DirectDownloadLinkException("ERROR: No valid download links found")


def get_hubcloud_proxy():
    """
    Returns proxy configuration for Hubcloud and Gdflix downloads.
    """
    from bot.modules.proxy import get_default_proxy, get_translate_proxy

    proxy_url = get_translate_proxy() or get_default_proxy()
    return {"http": proxy_url, "https": proxy_url} if proxy_url else {}


def get_random_proxy():
    """
    Returns proxy host, port, username, password for hubcloud/gdflix.
    Uses smart sticky proxy from proxy.py.
    """
    from bot.modules.proxy import get_default_proxy, get_translate_proxy

    proxy_url = get_translate_proxy() or get_default_proxy()

    if not proxy_url:
        return "", 0, "", ""  # Direct connection

    try:
        parsed = urlparse(proxy_url)
        host = parsed.hostname or ""
        port = parsed.port or 0
        username = unquote(parsed.username or "")
        password = unquote(parsed.password or "")
        if not host or not port:
            return "", 0, "", ""
        return host, int(port), username, password
    except Exception:
        return "", 0, "", ""


def get_cf_clearance(domain, prox_):
    """
    Returns cookies and headers for Cloudflare bypass.
    Uses cloudscraper to get cf_clearance cookie.
    """
    import cloudscraper

    scraper = cloudscraper.create_scraper(
        browser={"browser": "chrome", "platform": "windows", "desktop": True}
    )

    # Only apply proxy if it is valid; otherwise use direct connection.
    try:
        host = (prox_ or {}).get("host")
        port = int((prox_ or {}).get("port") or 0)
        username = (prox_ or {}).get("username") or ""
        password = (prox_ or {}).get("password") or ""
    except Exception:
        host, port, username, password = "", 0, "", ""

    if host and port > 0:
        if username and password:
            proxy_url = f"http://{username}:{password}@{host}:{port}"
        else:
            proxy_url = f"http://{host}:{port}"
        scraper.proxies.update({"http": proxy_url, "https": proxy_url})

    try:
        scraper.get(f"https://{domain}/", timeout=30)
        cookies = dict(scraper.cookies)
        headers = {
            "User-Agent": scraper.headers.get("User-Agent", user_agent),
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
            "Accept-Language": "en-US,en;q=0.5",
            "DNT": "1",
            "Connection": "keep-alive",
            "Upgrade-Insecure-Requests": "1",
        }
        return cookies, headers
    except Exception:
        headers = {
            "User-Agent": user_agent,
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
            "Accept-Language": "en-US,en;q=0.5",
            "DNT": "1",
            "Connection": "keep-alive",
            "Upgrade-Insecure-Requests": "1",
        }
        return {}, headers


HUBCLOUD_DOMAIN = "https://hubcloud.foo"


def hubcloud(url):
    """
    Fetches direct download links from HubCloud domains.
    """
    # Quick API Bypass Fallback from v1
    try:
        from requests import get as r_get
        api_response = r_get(f"http://hubcloud.cfd/bypass?url={url}", timeout=10).json()
        if "links" in api_response and api_response["links"]:
            links = sorted(api_response["links"], key=lambda x: x.get("priority", 0), reverse=True)
            return links[0]["url"]
    except Exception:
        pass # Fall back to v2's curl_cffi scraping if API fails

    from bs4 import BeautifulSoup
    import re

    try:
        from curl_cffi import requests as c_requests
    except ImportError:
        raise DirectDownloadLinkException("curl_cffi not installed!")

    code = url.split("/")[-1] if not url.endswith("/") else url.split("/")[-2]

    # Determine URL type and normalize
    if "/drive/packs/" in url:
        url = f"{HUBCLOUD_DOMAIN}/drive/packs/{code}"
    elif "/video/packs/" in url:
        url = f"{HUBCLOUD_DOMAIN}/video/packs/{code}"
    elif "/drive/" in url or "vifix" in url:
        url = f"{HUBCLOUD_DOMAIN}/drive/{code}"
    elif "/video/" in url:
        url = f"{HUBCLOUD_DOMAIN}/video/{code}"

    # Setup client with proxy (only if proxy is configured)
    host, port, username, password = get_random_proxy()
    client = c_requests.Session()

    if host and int(port or 0) > 0:
        if username and password:
            proxy_url = f"http://{username}:{password}@{host}:{port}"
        else:
            proxy_url = f"http://{host}:{port}"
        client.proxies.update({"http": proxy_url, "https": proxy_url})

    try:
        res = client.get(url, timeout=30, verify=False, impersonate="chrome110")
    except Exception as e:
        raise DirectDownloadLinkException(f"ERROR: Request failed: {e}")

    domain = urlparse(res.url).netloc
    soup = BeautifulSoup(res.text, "html.parser")

    # Handle packs (folder downloads)
    if "/packs/" in url:
        file_type_match = re.search(r"window\.open\(\s*['\"]([^'\"]+)['\"]", res.text)
        if not file_type_match:
            raise DirectDownloadLinkException(
                "ERROR: Could not determine pack file type"
            )

        file_type_segment = re.search(
            r"(?:^|/)(drive|video)(?:/|$)", file_type_match.group(1)
        )
        if not file_type_segment:
            raise DirectDownloadLinkException("ERROR: Invalid pack file type path")

        pack_file_type = file_type_segment.group(1)
        json_match = re.search(
            r"const\s+packData\s*=\s*JSON\.parse\(`({.+?})`\);", res.text, re.DOTALL
        )
        if not json_match:
            raise DirectDownloadLinkException("ERROR: Could not parse pack data")

        pack_info = loads(json_match.group(1))
        title = pack_info["pack"]["pack_name"]

        details = {
            "title": title,
            "total_size": 0,
            "contents": [],
            "header": f"Referer: {HUBCLOUD_DOMAIN}/",
        }

        # Extract files with their sizes and names from pack data
        files_data = pack_info.get("files", [])

        for item in files_data:
            share_id = item.get("share_id")
            file_name = item.get("file_name", f"File_{share_id}")
            file_size = item.get("file_size", 0)

            if not share_id:
                continue

            link = f"https://{domain}/{pack_file_type}/{share_id}"

            try:
                result = hubcloud(link)
                dl_url = None

                if isinstance(result, str):
                    dl_url = result
                elif isinstance(result, tuple):
                    dl_url = result[0]
                elif isinstance(result, dict):
                    # Nested pack - extend contents
                    nested_contents = result.get("contents", [])
                    if nested_contents:
                        details["contents"].extend(nested_contents)
                        details["total_size"] += result.get("total_size", 0)
                    continue

                if dl_url:
                    details["contents"].append(
                        {
                            "path": "",
                            "filename": file_name,
                            "url": dl_url,
                        }
                    )

                    # Add file size to total
                    try:
                        details["total_size"] += int(file_size)
                    except (ValueError, TypeError):
                        pass

            except Exception:
                continue

        if not details["contents"]:
            raise DirectDownloadLinkException("ERROR: No download links found in pack")
        return details

    # Single file handling
    card_header = soup.find("div", class_="card-header")
    title = card_header.text.strip() if card_header else f"HubCloud_File_{code}"

    size_elem = soup.find("i", id="size")
    size = size_elem.text.strip() if size_elem else "Unknown"

    # Find the token link (HubCloud HTML changes frequently, so use multiple fallbacks)
    anchor_href = ""

    anchor = soup.find("a", href=lambda x: x and "token" in x.lower())
    if anchor and anchor.get("href"):
        anchor_href = anchor["href"]

    if not anchor_href:
        anchor = soup.find("a", id="download", attrs={"x-href": True})
        if anchor:
            try:
                anchor["href"] = decode64(anchor["x-href"])
                anchor_href = anchor["href"]
            except (ValueError, TypeError, UnicodeDecodeError):
                pass

    if not anchor_href:
        # Try to pull a token URL from raw HTML/JS
        candidates = []
        patterns = [
            r'href\s*=\s*["\"]([^"\"]*(?:\?|&)token[^"\"]*)["\"]',
            r'href\s*=\s*["\"]([^"\"]*/token/[^"\"]*)["\"]',
            r'(https?://[^\s"\']*(?:\?|&)token=[^\s"\']+)',
            r'(https?://[^\s"\']*/token/[^\s"\']+)',
        ]
        for pat in patterns:
            try:
                for m in re.findall(pat, res.text, flags=re.IGNORECASE):
                    if not m:
                        continue
                    # Avoid common non-download tokens
                    low = m.lower()
                    if "csrf" in low or "turnstile" in low or "recaptcha" in low:
                        continue
                    candidates.append(m)
            except Exception:
                continue

        # Prefer URLs that look like real navigation links
        for cand in candidates:
            if "token=" in cand.lower() or "/token/" in cand.lower():
                anchor_href = cand
                break

    if not anchor_href:
        low_html = (res.text or "").lower()
        if any(
            k in low_html
            for k in ["just a moment", "cloudflare", "cf-chl", "cf-turnstile"]
        ):
            raise DirectDownloadLinkException(
                "ERROR: HubCloud blocked/anti-bot page (no token link). Try enabling/using proxy and retry."
            )
        raise DirectDownloadLinkException("ERROR: No token link found")

    if not anchor_href.startswith("http"):
        anchor_href = f"https://{domain}" + anchor_href

    try:
        res1 = client.get(
            anchor_href, timeout=30, verify=False, impersonate="chrome110"
        )
    except Exception as e:
        raise DirectDownloadLinkException(f"ERROR: Failed to get download page: {e}")

    soup1 = BeautifulSoup(res1.text, "html.parser")
    anchors = soup1.find_all("a")

    dl_links = {}
    for i in anchors:
        if not (i.get("href") or i.get("id") == "mega"):
            continue

        href = i.get("href", "")
        link_domain = urlparse(href).netloc
        text = i.get_text(strip=True)

        if "pixeldrain" in link_domain:
            dl_links["Pixeldrain"] = href
        elif "bzzhr.co" in link_domain:
            dl_links["BuzzServer"] = href
        elif "FSL Server" in text:
            dl_links["FSL Server"] = href
        elif "FSLv2 Server" in text:
            dl_links["FSLv2 Server"] = href
        elif "Download File" in text:
            dl_links["DL Server"] = href
        elif "ZipDisk" in text:
            dl_links["ZipDisk Server"] = href
        elif "Mega Server" in text:
            dl_links["Mega Server"] = href
        elif "TRS Server" in text:
            script = i.find_next("script")
            if script and script.string:
                location = re.search(
                    r"window\.location\.href\s*=\s*'([^']+)'", script.string
                )
                if location:
                    try:
                        tresp = client.get(
                            location.group(1),
                            allow_redirects=False,
                            timeout=10,
                            verify=False,
                            impersonate="chrome110",
                        )
                        loc = tresp.headers.get("Location", "")
                        if loc:
                            dl_links["TRS Server"] = loc
                    except Exception:
                        pass
        elif "10Gbps" in text:
            if "storage.googleapis.com/" in href:
                dl_links["10Gbps Server"] = href
                continue
            try:
                res1 = client.get(
                    href,
                    allow_redirects=False,
                    timeout=10,
                    verify=False,
                    impersonate="chrome110",
                )
                location = res1.headers.get("Location", "")
                if location.startswith("https://video-downloads"):
                    dl_links["10Gbps Server"] = location
                    continue
                if "?link=https://video-downloads" in location:
                    dl_links["10Gbps Server"] = location.split("?link=")[-1]
                    continue
                if location:
                    res111 = client.get(
                        location,
                        allow_redirects=False,
                        timeout=10,
                        verify=False,
                        impersonate="chrome110",
                    )
                    location = res111.headers.get("Location")
                    if location:
                        dl_links["10Gbps Server"] = location.split("?link=")[-1]
            except Exception:
                pass

    if not dl_links:
        raise DirectDownloadLinkException("ERROR: No download links found")

    # Priority order for returning links
    priority = [
        "10Gbps Server",
        "FSL Server",
        "FSLv2 Server",
        "DL Server",
        "BuzzServer",
        "Pixeldrain",
        "ZipDisk Server",
        "Mega Server",
        "TRS Server",
    ]

    for server in priority:
        if server in dl_links:
            return dl_links[server]

    # Return first available link
    return next(iter(dl_links.values()))


def buzzheavier(link):
    """
    Generate a direct download link for buzzheavier URLs.
    @param link: URL from buzzheavier
    @return: Direct download link
    """
    link = link if link.endswith("/") else link + "/"
    client = create_scraper()
    try:
        res = client.get(
            link + "download", headers={"hx-current-url": link, "referer": link}
        )
    except Exception as e:
        raise DirectDownloadLinkException(f"ERROR: {e.__class__.__name__}") from e

    domain = urlparse(link).netloc
    redirect_url = res.headers.get("Hx-Redirect", "None")

    if redirect_url == "None":
        raise DirectDownloadLinkException("ERROR: Direct link not found")

    if not redirect_url.startswith("http"):
        return f"https://{domain}{redirect_url}"
    return redirect_url


def zlib(url):
    return f"https://zlib.fasto.workers.dev/?url={url}"


def fuckingfast_dl(url):
    """
    Generate a direct download link for fuckingfast.co URLs.
    @param url: URL from fuckingfast.co
    @return: Direct download link
    """
    session = Session()
    url = url.strip()

    try:
        response = session.get(url)
        content = response.text
        pattern = r'window\.open\((["\'])(https://fuckingfast\.co/dl/[^"\']+)\1'
        match = search(pattern, content)

        if not match:
            raise DirectDownloadLinkException(
                "ERROR: Could not find download link in page"
            )

        direct_url = match.group(2)
        return direct_url

    except Exception as e:
        raise DirectDownloadLinkException(f"ERROR: {str(e)}") from e
    finally:
        session.close()


def apkadmin(url: str) -> str:
    with create_scraper() as session:
        try:
            req = session.get(url).text
            soup = B(req, "lxml")
            op = soup.find("input", {"name": "op"})["value"]
            ids = soup.find("input", {"name": "id"})["value"]
            post = session.post(
                url,
                data={
                    "op": op,
                    "id": ids,
                    "rand": " ",
                    "referer": " ",
                    "method_free": " ",
                    "method_premium": " ",
                },
            ).text
            soup = B(post, "lxml")
            link = soup.find("div", {"class": "text text-center"})
            direct_link = link.find("a")["href"]
            return direct_link
        except:
            session.close()
            raise DirectDownloadLinkException(f"ERROR: Link File tidak ditemukan!")


def devuploads(url):
    """
    Generate a direct download link for devuploads.com URLs.
    @param url: URL from devuploads.com
    @return: Direct download link
    """
    try:
        params = {
            "apikey": "sikocak",
            "url": url,
        }
        with Session() as session:
            try:
                data = session.get(
                    "https://scraper.pika.web.id/devuploads", params=params
                ).json()
            except Exception as e:
                raise DirectDownloadLinkException(f"ERROR: {e.__class__.__name__}")

        details = {"contents": [], "title": f"", "total_size": 0}
        if data["status"] == "success":
            file_size = int(data["bytes"])
            item = {
                "path": "",
                "filename": data["filename"],
                "url": data["proxylink"],
                "size": file_size,
            }
            details["contents"].append(item)
            details["total_size"] += file_size
            details["title"] = data["filename"]
        else:
            raise DirectDownloadLinkException(f"ERROR: {data['message']}")
        return details
    except Exception:
        pattern = r"^https?://devuploads\.com/.*"
        if not match(pattern, url):
            raise DirectDownloadLinkException(
                "ERROR: Invalid URL, use link format <code>https://devuploads.com/xxxxxxxx</code>"
            )

        import os as _os
        proxy_env = (_os.environ.get("GUJJU_PROXY_URL") or "").strip()
        if proxy_env:
            proxies = {"http": proxy_env, "https": proxy_env}
        else:
            proxies = get_hubcloud_proxy()

        with Session() as session:
            res = session.get(url)
            html = HTML(res.text)
            if not html.xpath("//input[@name]"):
                raise DirectDownloadLinkException("ERROR: Unable to find link data")

            title = html.xpath("//title/text()")[0]

            data = {i.get("name"): i.get("value") for i in html.xpath("//input[@name]")}
            resp = session.get(
                "https://du2.devuploads.com/dlhash.php",
                headers={
                    "Origin": "https://gujjukhabar.in",
                    "Referer": "https://gujjukhabar.in/",
                },
            )
            if not resp.text:
                raise DirectDownloadLinkException("ERROR: Unable to find ipp value")
            data["ipp"] = resp.text.strip()
            if not data.get("rand"):
                raise DirectDownloadLinkException("ERROR: Unable to find rand value")
            randpost = session.post(
                "https://devuploads.com/token/token.php",
                data={"rand": data["rand"], "msg": ""},
                headers={
                    "Origin": "https://gujjukhabar.in",
                    "Referer": "https://gujjukhabar.in/",
                },
            )
            if not randpost:
                raise DirectDownloadLinkException("ERROR: Unable to find xd value")
            data["xd"] = randpost.text.strip()
            res = session.post(url, data=data, proxies=proxies)
            html = HTML(res.text)
            if not html.xpath("//input[@name='orilink']/@value"):
                raise DirectDownloadLinkException("ERROR: Unable to find Direct Link")
            direct_link = html.xpath("//input[@name='orilink']/@value")[0]

            with session.head(direct_link, allow_redirects=True) as head_res:
                size = head_res.headers.get("content-length")
                if size:
                    size = int(size)

                filename = title
                if "content-disposition" in head_res.headers:
                    cd = head_res.headers.get("content-disposition")
                    if "filename=" in cd:
                        filename = cd.split("filename=")[-1].strip('"')

            details = {"contents": [], "title": filename, "total_size": size or 0}
            item = {
                "path": "",
                "filename": filename,
                "url": direct_link,
                "size": size or 0,
            }
            details["contents"].append(item)
            return details


def sharemods(url: str) -> str:
    """Resolve sharemods links using standard form submission."""

    with create_scraper() as session:
        try:
            page = session.get(url).text
            tree = HTML(page)
            op = tree.xpath('//input[@name="op"]/@value')
            ids = tree.xpath('//input[@name="id"]/@value')
            if not op or not ids:
                raise DirectDownloadLinkException(
                    "ERROR: Unable to parse ShareMods form"
                )
            payload = {
                "op": op[0],
                "id": ids[0],
                "rand": " ",
                "referer": " ",
                "method_free": " ",
                "method_premium": " ",
            }
            post_page = session.post(url, data=payload).text
            link = HTML(post_page).xpath('//a[@id="downloadbtn"]/@href')
            if not link:
                raise DirectDownloadLinkException(
                    "ERROR: ShareMods download link not found"
                )
            return link[0]
        except DirectDownloadLinkException:
            raise
        except Exception as err:
            raise DirectDownloadLinkException(f"ERROR: {err}") from err


def sourceforge(url: str) -> str:
    with Session() as session:
        try:
            if "master.dl.sourceforge.net" in url:
                return f"{url}?viasf=1"
            if url.endswith("/download"):
                url = url.rsplit("/download", 1)[0]
            matches = findall(r"\bhttps?://sourceforge\.net\S+", url)
            if not matches:
                raise DirectDownloadLinkException("ERROR: SourceForge link not found")
            link = matches[0]
            file_id = findall(r"files(.*)", link)[0]
            project = findall(r"projects?/(.*?)/files", link)[0]
            response = session.get(
                "https://sourceforge.net/settings/mirror_choices",
                params={
                    "projectname": project,
                    "filename": file_id,
                },
                timeout=30,
            ).content
            soup = B(response, "html.parser")
            mirror_list = soup.find("ul", {"id": "mirrorList"})
            if not mirror_list:
                raise DirectDownloadLinkException("ERROR: Unable to fetch mirror list")
            mirrors = [
                item["id"] for item in mirror_list.findAll("li") if item.get("id")
            ]
            if not mirrors:
                raise DirectDownloadLinkException("ERROR: No mirrors available")
            preferred = "ixpeering" if "ixpeering" in mirrors else None
            if "autoselect" in mirrors:
                mirrors.remove("autoselect")
            chosen = preferred or choice(mirrors)
            return f"https://{chosen}.dl.sourceforge.net/project/{project}/{file_id}?viasf=1"
        except DirectDownloadLinkException:
            raise
        except Exception as err:
            raise DirectDownloadLinkException(f"ERROR: {err}") from err


def mediafile(url):
    """
    Generate a direct download link for mediafile.cc URLs.
    @param url: URL from mediafile.cc
    @return: Direct download link
    """
    try:
        res = get(url, allow_redirects=True)
        match = search(r"href='([^']+)'", res.text)
        if not match:
            raise DirectDownloadLinkException("ERROR: Unable to find link data")
        download_url = match.group(1)
        sleep(60)
        res = get(download_url, headers={"Referer": url}, cookies=res.cookies)
        postvalue = search(r"showFileInformation(.*);", res.text)
        if not postvalue:
            raise DirectDownloadLinkException("ERROR: Unable to find post value")
        postid = postvalue.group(1).replace("(", "").replace(")", "")
        response = post(
            "https://mediafile.cc/account/ajax/file_details",
            data={"u": postid},
            headers={"X-Requested-With": "XMLHttpRequest"},
        )
        html = response.json()["html"]
        return [
            i for i in findall(r'https://[^\s"\']+', html) if "download_token" in i
        ][1]
    except Exception as e:
        raise DirectDownloadLinkException(f"ERROR: {str(e)}") from e


def lulacloud(url):
    """
    Generate a direct download link for www.lulacloud.com URLs.
    @param url: URL from www.lulacloud.com
    @return: Direct download link
    """
    session = Session()
    try:
        res = session.post(url, headers={"Referer": url}, allow_redirects=False)
        return res.headers["location"]
    except Exception as e:
        raise DirectDownloadLinkException(f"ERROR: {str(e)}") from e
    finally:
        session.close()


def mediafire(url, session=None):
    if "/folder/" in url:
        return mediafireFolder(url)
    if "::" in url:
        _password = url.split("::")[-1]
        url = url.split("::")[-2]
    else:
        _password = ""
    if final_link := findall(
        r"https?:\/\/download\d+\.mediafire\.com\/\S+\/\S+\/\S+", url
    ):
        return final_link[0]

    def _repair_download(url, session):
        try:
            html = HTML(session.get(url).text)
            if new_link := html.xpath('//a[@id="continue-btn"]/@href'):
                return mediafire(f"https://mediafire.com/{new_link[0]}")
        except Exception as e:
            raise DirectDownloadLinkException(f"ERROR: {e.__class__.__name__}") from e

    if session is None:
        session = create_scraper()
        parsed_url = urlparse(url)
        url = f"{parsed_url.scheme}://{parsed_url.netloc}{parsed_url.path}"
    try:
        html = HTML(session.get(url).text)
    except Exception as e:
        session.close()
        raise DirectDownloadLinkException(f"ERROR: {e.__class__.__name__}") from e
    if error := html.xpath('//p[@class="notranslate"]/text()'):
        session.close()
        raise DirectDownloadLinkException(f"ERROR: {error[0]}")
    if html.xpath("//div[@class='passwordPrompt']"):
        if not _password:
            session.close()
            raise DirectDownloadLinkException(
                f"ERROR: {PASSWORD_ERROR_MESSAGE}".format(url)
            )
        try:
            html = HTML(session.post(url, data={"downloadp": _password}).text)
        except Exception as e:
            session.close()
            raise DirectDownloadLinkException(f"ERROR: {e.__class__.__name__}") from e
        if html.xpath("//div[@class='passwordPrompt']"):
            session.close()
            raise DirectDownloadLinkException("ERROR: Wrong password.")
    if not (final_link := html.xpath('//a[@aria-label="Download file"]/@href')):
        if repair_link := html.xpath("//a[@class='retry']/@href"):
            return _repair_download(repair_link[0], session)
        raise DirectDownloadLinkException(
            "ERROR: No links found in this page Try Again"
        )
    if final_link[0].startswith("//"):
        final_url = f"https://{final_link[0][2:]}"
        if _password:
            final_url += f"::{_password}"
        return mediafire(final_url, session)
    session.close()
    return final_link[0]


def osdn(url):
    with create_scraper() as session:
        try:
            html = HTML(session.get(url).text)
        except Exception as e:
            raise DirectDownloadLinkException(f"ERROR: {e.__class__.__name__}") from e
        if not (direct_link := html.xpath('//a[@class="mirror_link"]/@href')):
            raise DirectDownloadLinkException("ERROR: Direct link not found")
        return f"https://osdn.net{direct_link[0]}"


def yandex_disk(url: str) -> str:
    """Yandex.Disk direct link generator
    Based on https://github.com/wldhx/yadisk-direct"""
    try:
        link = findall(r"\b(https?://(yadi\.sk|disk\.yandex\.(com|ru))\S+)", url)[0][0]
    except IndexError:
        return "No Yandex.Disk links found\n"
    api = "https://cloud-api.yandex.net/v1/disk/public/resources/download?public_key={}"
    try:
        return get(api.format(link)).json()["href"]
    except KeyError as e:
        raise DirectDownloadLinkException(
            "ERROR: File not found/Download limit reached"
        ) from e


def github(url):
    """GitHub direct links generator"""
    try:
        findall(r"\bhttps?://.*github\.com.*releases\S+", url)[0]
    except IndexError as e:
        raise DirectDownloadLinkException("No GitHub Releases links found") from e
    with create_scraper() as session:
        _res = session.get(url, stream=True, allow_redirects=False)
        if "location" in _res.headers:
            return _res.headers["location"]
        raise DirectDownloadLinkException("ERROR: Can't extract the link")


def hxfile(url):
    if not ospath.isfile("hxfile.txt"):
        raise DirectDownloadLinkException("ERROR: hxfile.txt (cookies) Not Found!")
    try:
        jar = MozillaCookieJar()
        jar.load("hxfile.txt")
    except Exception as e:
        raise DirectDownloadLinkException(f"ERROR: {e.__class__.__name__}") from e
    cookies = {cookie.name: cookie.value for cookie in jar}
    with Session() as session:
        try:
            if url.strip().endswith(".html"):
                url = url[:-5]
            file_code = url.split("/")[-1]
            html = HTML(
                session.post(
                    url,
                    data={"op": "download2", "id": file_code},
                    cookies=cookies,
                ).text
            )
        except Exception as e:
            raise DirectDownloadLinkException(f"ERROR: {e.__class__.__name__}") from e
    if direct_link := html.xpath("//a[@class='btn btn-dow']/@href"):
        header = f"Referer: {url}"
        return direct_link[0], header
    raise DirectDownloadLinkException("ERROR: Direct download link not found")


def onedrive(link):
    """Onedrive direct link generator
    By https://github.com/junedkh"""
    with create_scraper() as session:
        try:
            link = session.get(link).url
            parsed_link = urlparse(link)
            link_data = parse_qs(parsed_link.query)
        except Exception as e:
            raise DirectDownloadLinkException(f"ERROR: {e.__class__.__name__}") from e
        if not link_data:
            raise DirectDownloadLinkException("ERROR: Unable to find link_data")
        folder_id = link_data.get("resid")
        if not folder_id:
            raise DirectDownloadLinkException("ERROR: folder id not found")
        folder_id = folder_id[0]
        authkey = link_data.get("authkey")
        if not authkey:
            raise DirectDownloadLinkException("ERROR: authkey not found")
        authkey = authkey[0]
        boundary = uuid4()
        headers = {"content-type": f"multipart/form-data;boundary={boundary}"}
        data = f"--{boundary}\r\nContent-Disposition: form-data;name=data\r\nPrefer: Migration=EnableRedirect;FailOnMigratedFiles\r\nX-HTTP-Method-Override: GET\r\nContent-Type: application/json\r\n\r\n--{boundary}--"
        try:
            resp = session.get(
                f"https://api.onedrive.com/v1.0/drives/{folder_id.split('!', 1)[0]}/items/{folder_id}?$select=id,@content.downloadUrl&ump=1&authKey={authkey}",
                headers=headers,
                data=data,
            ).json()
        except Exception as e:
            raise DirectDownloadLinkException(f"ERROR: {e.__class__.__name__}") from e
    if "@content.downloadUrl" not in resp:
        raise DirectDownloadLinkException("ERROR: Direct link not found")
    return resp["@content.downloadUrl"]


def pixeldrain(url: str) -> str:
    match = search(
        r"(?:pixeldrain\.(?:com|net|dev)/(?:u|api/file)/|pixeldra\.in/(?:u|api/file)/|cdn\.pixeldrain\.eu\.cc/)([A-Za-z0-9_-]+)",
        str(url),
    )
    if not match:
        raise DirectDownloadLinkException("ERROR: Invalid PixelDrain link")
    return f"https://cdn.pixeldrain.eu.cc/{match.group(1)}"


def streamtape(url):
    splitted_url = url.split("/")
    _id = splitted_url[4] if len(splitted_url) >= 6 else splitted_url[-1]
    try:
        with Session() as session:
            html = HTML(session.get(url).text)
    except Exception as e:
        raise DirectDownloadLinkException(f"ERROR: {e.__class__.__name__}") from e
    script = html.xpath(
        "//script[contains(text(),'ideoooolink')]/text()"
    ) or html.xpath("//script[contains(text(),'ideoolink')]/text()")
    if not script:
        raise DirectDownloadLinkException("ERROR: requeries script not found")
    if not (link := findall(r"(&expires\S+)'", script[0])):
        raise DirectDownloadLinkException("ERROR: Download link not found")
    return f"https://streamtape.com/get_video?id={_id}{link[-1]}"


def racaty(url):
    with create_scraper() as session:
        try:
            url = session.get(url).url
            json_data = {"op": "download2", "id": url.split("/")[-1]}
            html = HTML(session.post(url, data=json_data).text)
        except Exception as e:
            raise DirectDownloadLinkException(f"ERROR: {e.__class__.__name__}") from e
    if direct_link := html.xpath("//a[@id='uniqueExpirylink']/@href"):
        return direct_link[0]
    else:
        raise DirectDownloadLinkException("ERROR: Direct link not found")


def uploadhaven(url):
    """
    Generate a direct download link for uploadhaven.com URLs.
    @param url: URL from uploadhaven.com
    @return: Direct download link
    """
    try:
        res = get(url, headers={"Referer": "http://steamunlocked.net/"})
        html = HTML(res.text)
        if not html.xpath('//form[@method="POST"]//input'):
            raise DirectDownloadLinkException("ERROR: Unable to find link data")
        data = {
            i.get("name"): i.get("value")
            for i in html.xpath('//form[@method="POST"]//input')
        }
        sleep(15)
        res = post(url, data=data, headers={"Referer": url}, cookies=res.cookies)
        html = HTML(res.text)
        if not html.xpath('//div[@class="alert alert-success mb-0"]//a'):
            raise DirectDownloadLinkException("ERROR: Unable to find link data")
        a = html.xpath('//div[@class="alert alert-success mb-0"]//a')[0]
        return a.get("href")
    except Exception as e:
        raise DirectDownloadLinkException(f"ERROR: {str(e)}") from e


def fichier(link):
    """1Fichier direct link generator
    Based on https://github.com/Maujar
    """
    regex = r"^([http:\/\/|https:\/\/]+)?.*1fichier\.com\/\?.+"
    gan = match(regex, link)
    if not gan:
        raise DirectDownloadLinkException("ERROR: The link you entered is wrong!")
    if "::" in link:
        pswd = link.split("::")[-1]
        url = link.split("::")[-2]
    else:
        pswd = None
        url = link
    cget = create_scraper().request
    try:
        if pswd is None:
            req = cget("post", url)
        else:
            pw = {"pass": pswd}
            req = cget("post", url, data=pw)
    except Exception as e:
        raise DirectDownloadLinkException(f"ERROR: {e.__class__.__name__}") from e
    if req.status_code == 404:
        raise DirectDownloadLinkException(
            "ERROR: File not found/The link you entered is wrong!"
        )
    html = HTML(req.text)
    if dl_url := html.xpath('//a[@class="ok btn-general btn-orange"]/@href'):
        return dl_url[0]
    if not (ct_warn := html.xpath('//div[@class="ct_warn"]')):
        raise DirectDownloadLinkException(
            "ERROR: Error trying to generate Direct Link from 1fichier!"
        )
    if len(ct_warn) == 3:
        str_2 = ct_warn[-1].text
        if "you must wait" in str_2.lower():
            if numbers := [int(word) for word in str_2.split() if word.isdigit()]:
                raise DirectDownloadLinkException(
                    f"ERROR: 1fichier is on a limit. Please wait {numbers[0]} minute."
                )
            else:
                raise DirectDownloadLinkException(
                    "ERROR: 1fichier is on a limit. Please wait a few minutes/hour."
                )
        elif "protect access" in str_2.lower():
            raise DirectDownloadLinkException(
                f"ERROR:\n{PASSWORD_ERROR_MESSAGE.format(link)}"
            )
        else:
            raise DirectDownloadLinkException(
                "ERROR: Failed to generate Direct Link from 1fichier!"
            )
    elif len(ct_warn) == 4:
        str_1 = ct_warn[-2].text
        str_3 = ct_warn[-1].text
        if "you must wait" in str_1.lower():
            if numbers := [int(word) for word in str_1.split() if word.isdigit()]:
                raise DirectDownloadLinkException(
                    f"ERROR: 1fichier is on a limit. Please wait {numbers[0]} minute."
                )
            else:
                raise DirectDownloadLinkException(
                    "ERROR: 1fichier is on a limit. Please wait a few minutes/hour."
                )
        elif "bad password" in str_3.lower():
            raise DirectDownloadLinkException(
                "ERROR: The password you entered is wrong!"
            )
    raise DirectDownloadLinkException(
        "ERROR: Error trying to generate Direct Link from 1fichier!"
    )


def solidfiles(url):
    """Solidfiles direct link generator
    Based on https://github.com/Xonshiz/SolidFiles-Downloader
    By https://github.com/Jusidama18"""
    with create_scraper() as session:
        try:
            headers = {
                "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_9_4) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/36.0.1985.125 Safari/537.36"
            }
            pageSource = session.get(url, headers=headers).text
            mainOptions = str(
                search(r"viewerOptions\'\,\ (.*?)\)\;", pageSource).group(1)
            )
            return loads(mainOptions)["downloadUrl"]
        except Exception as e:
            raise DirectDownloadLinkException(f"ERROR: {e.__class__.__name__}") from e


def krakenfiles(url):
    with Session() as session:
        try:
            _res = session.get(url)
        except Exception as e:
            raise DirectDownloadLinkException(f"ERROR: {e.__class__.__name__}") from e
        html = HTML(_res.text)
        if post_url := html.xpath('//form[@id="dl-form"]/@action'):
            post_url = f"https://krakenfiles.com{post_url[0]}"
        else:
            raise DirectDownloadLinkException("ERROR: Unable to find post link.")
        if token := html.xpath('//input[@id="dl-token"]/@value'):
            data = {"token": token[0]}
        else:
            raise DirectDownloadLinkException("ERROR: Unable to find token for post.")
        try:
            _json = session.post(post_url, data=data).json()
        except Exception as e:
            raise DirectDownloadLinkException(
                f"ERROR: {e.__class__.__name__} While send post request"
            ) from e
    if _json["status"] != "ok":
        raise DirectDownloadLinkException(
            "ERROR: Unable to find download after post request"
        )
    return _json["url"]


def uploadee(url):
    with create_scraper() as session:
        try:
            html = HTML(session.get(url).text)
        except Exception as e:
            raise DirectDownloadLinkException(f"ERROR: {e.__class__.__name__}") from e
    if link := html.xpath("//a[@id='d_l']/@href"):
        return link[0]
    else:
        raise DirectDownloadLinkException("ERROR: Direct Link not found")


def filepress(url):
    with create_scraper() as session:
        try:
            url = session.get(url).url
            raw = urlparse(url)
            json_data = {
                "id": raw.path.split("/")[-1],
                "method": "publicDownlaod",
            }
            api = f"{raw.scheme}://{raw.hostname}/api/file/downlaod/"
            res2 = session.post(
                api,
                headers={"Referer": f"{raw.scheme}://{raw.hostname}"},
                json=json_data,
            ).json()
            json_data2 = {
                "id": res2["data"],
                "method": "publicUserDownlaod",
            }
            api2 = "https://new2.filepress.store/api/file/downlaod2/"
            res = session.post(
                api2,
                headers={"Referer": f"{raw.scheme}://{raw.hostname}"},
                json=json_data2,
            ).json()
        except Exception as e:
            raise DirectDownloadLinkException(f"ERROR: {e.__class__.__name__}") from e
    if "data" not in res:
        raise DirectDownloadLinkException(f"ERROR: {res['statusText']}")
    return f"https://drive.google.com/uc?id={res['data']}&export=download"


def gdtot(url):
    cget = create_scraper().request
    try:
        res = cget("GET", f"https://gdtot.pro/file/{url.split('/')[-1]}")
    except Exception as e:
        raise DirectDownloadLinkException(f"ERROR: {e.__class__.__name__}") from e
    token_url = HTML(res.text).xpath(
        "//a[contains(@class,'inline-flex items-center justify-center')]/@href"
    )
    if not token_url:
        try:
            url = cget("GET", url).url
            p_url = urlparse(url)
            res = cget(
                "GET", f"{p_url.scheme}://{p_url.hostname}/ddl/{url.split('/')[-1]}"
            )
        except Exception as e:
            raise DirectDownloadLinkException(f"ERROR: {e.__class__.__name__}") from e
        if (
            drive_link := findall(r"myDl\('(.*?)'\)", res.text)
        ) and "drive.google.com" in drive_link[0]:
            return drive_link[0]
        else:
            raise DirectDownloadLinkException(
                "ERROR: Drive Link not found, Try in your broswer"
            )
    token_url = token_url[0]
    try:
        token_page = cget("GET", token_url)
    except Exception as e:
        raise DirectDownloadLinkException(
            f"ERROR: {e.__class__.__name__} with {token_url}"
        ) from e
    path = findall(r'\("(.*?)"\)', token_page.text)
    if not path:
        raise DirectDownloadLinkException("ERROR: Cannot bypass this")
    path = path[0]
    raw = urlparse(token_url)
    final_url = f"{raw.scheme}://{raw.hostname}{path}"
    return sharer_scraper(final_url)


def sharer_scraper(url):
    cget = create_scraper().request
    try:
        url = cget("GET", url).url
        raw = urlparse(url)
        header = {
            "useragent": "Mozilla/5.0 (Windows; U; Windows NT 5.1; en-US) AppleWebKit/534.10 (KHTML, like Gecko) Chrome/7.0.548.0 Safari/534.10"
        }
        res = cget("GET", url, headers=header)
    except Exception as e:
        raise DirectDownloadLinkException(f"ERROR: {e.__class__.__name__}") from e
    key = findall(r'"key",\s+"(.*?)"', res.text)
    if not key:
        raise DirectDownloadLinkException("ERROR: Key not found!")
    key = key[0]
    if not HTML(res.text).xpath("//button[@id='drc']"):
        raise DirectDownloadLinkException(
            "ERROR: This link don't have direct download button"
        )
    boundary = uuid4()
    headers = {
        "Content-Type": f"multipart/form-data; boundary=----WebKitFormBoundary{boundary}",
        "x-token": raw.hostname,
        "useragent": "Mozilla/5.0 (Windows; U; Windows NT 5.1; en-US) AppleWebKit/534.10 (KHTML, like Gecko) Chrome/7.0.548.0 Safari/534.10",
    }

    data = (
        f'------WebKitFormBoundary{boundary}\r\nContent-Disposition: form-data; name="action"\r\n\r\ndirect\r\n'
        f'------WebKitFormBoundary{boundary}\r\nContent-Disposition: form-data; name="key"\r\n\r\n{key}\r\n'
        f'------WebKitFormBoundary{boundary}\r\nContent-Disposition: form-data; name="action_token"\r\n\r\n\r\n'
        f"------WebKitFormBoundary{boundary}--\r\n"
    )
    try:
        res = cget("POST", url, cookies=res.cookies, headers=headers, data=data).json()
    except Exception as e:
        raise DirectDownloadLinkException(f"ERROR: {e.__class__.__name__}") from e
    if "url" not in res:
        raise DirectDownloadLinkException(
            "ERROR: Drive Link not found, Try in your broswer"
        )
    if "drive.google.com" in res["url"] or "drive.usercontent.google.com" in res["url"]:
        return res["url"]
    try:
        res = cget("GET", res["url"])
    except Exception as e:
        raise DirectDownloadLinkException(f"ERROR: {e.__class__.__name__}") from e
    if (drive_link := HTML(res.text).xpath("//a[contains(@class,'btn')]/@href")) and (
        "drive.google.com" in drive_link[0]
        or "drive.usercontent.google.com" in drive_link[0]
    ):
        return drive_link[0]
    else:
        raise DirectDownloadLinkException(
            "ERROR: Drive Link not found, Try in your broswer"
        )


def wetransfer(url):
    with create_scraper() as session:
        try:
            url = session.get(url).url
            splited_url = url.split("/")
            json_data = {"security_hash": splited_url[-1], "intent": "entire_transfer"}
            res = session.post(
                f"https://wetransfer.com/api/v4/transfers/{splited_url[-2]}/download",
                json=json_data,
            ).json()
        except Exception as e:
            raise DirectDownloadLinkException(f"ERROR: {e.__class__.__name__}") from e
    if "direct_link" in res:
        return res["direct_link"]
    elif "message" in res:
        raise DirectDownloadLinkException(f"ERROR: {res['message']}")
    elif "error" in res:
        raise DirectDownloadLinkException(f"ERROR: {res['error']}")
    else:
        raise DirectDownloadLinkException("ERROR: cannot find direct link")


def akmfiles(url):
    with create_scraper() as session:
        try:
            html = HTML(
                session.post(
                    url,
                    data={"op": "download2", "id": url.split("/")[-1]},
                ).text
            )
        except Exception as e:
            raise DirectDownloadLinkException(f"ERROR: {e.__class__.__name__}") from e
    if direct_link := html.xpath("//a[contains(@class,'btn btn-dow')]/@href"):
        return direct_link[0]
    else:
        raise DirectDownloadLinkException("ERROR: Direct link not found")


def shrdsk(url):
    with create_scraper() as session:
        try:
            _json = session.get(
                f"https://us-central1-affiliate2apk.cloudfunctions.net/get_data?shortid={url.split('/')[-1]}",
            ).json()
        except Exception as e:
            raise DirectDownloadLinkException(f"ERROR: {e.__class__.__name__}") from e
        if "download_data" not in _json:
            raise DirectDownloadLinkException("ERROR: Download data not found")
        try:
            _res = session.get(
                f"https://shrdsk.me/download/{_json['download_data']}",
                allow_redirects=False,
            )
            if "Location" in _res.headers:
                return _res.headers["Location"]
        except Exception as e:
            raise DirectDownloadLinkException(f"ERROR: {e.__class__.__name__}") from e
    raise DirectDownloadLinkException("ERROR: cannot find direct link in headers")


def linkBox(url: str):
    parsed_url = urlparse(url)
    try:
        shareToken = parsed_url.path.split("/")[-1]
    except Exception:
        raise DirectDownloadLinkException("ERROR: invalid URL")

    details = {"contents": [], "title": "", "total_size": 0}

    def __singleItem(session, itemId):
        try:
            _json = session.get(
                "https://www.linkbox.to/api/file/detail",
                params={"itemId": itemId},
            ).json()
        except Exception as e:
            raise DirectDownloadLinkException(f"ERROR: {e.__class__.__name__}") from e
        data = _json["data"]
        if not data:
            if "msg" in _json:
                raise DirectDownloadLinkException(f"ERROR: {_json['msg']}")
            raise DirectDownloadLinkException("ERROR: data not found")
        itemInfo = data["itemInfo"]
        if not itemInfo:
            raise DirectDownloadLinkException("ERROR: itemInfo not found")
        filename = itemInfo["name"]
        sub_type = itemInfo.get("sub_type")
        if sub_type and not filename.strip().endswith(sub_type):
            filename += f".{sub_type}"
        if not details["title"]:
            details["title"] = filename

        size = 0
        if "size" in itemInfo:
            size = itemInfo["size"]
            if isinstance(size, str) and size.isdigit():
                size = float(size)
            details["total_size"] += size

        item = {
            "path": "",
            "filename": filename,
            "url": itemInfo["url"],
            "size": size,
        }
        details["contents"].append(item)

    def __fetch_links(session, _id=0, folderPath=""):
        params = {
            "shareToken": shareToken,
            "pageSize": 1000,
            "pid": _id,
        }
        try:
            _json = session.get(
                "https://www.linkbox.to/api/file/share_out_list",
                params=params,
            ).json()
        except Exception as e:
            raise DirectDownloadLinkException(f"ERROR: {e.__class__.__name__}") from e
        data = _json["data"]
        if not data:
            if "msg" in _json:
                raise DirectDownloadLinkException(f"ERROR: {_json['msg']}")
            raise DirectDownloadLinkException("ERROR: data not found")
        try:
            if data["shareType"] == "singleItem":
                return __singleItem(session, data["itemId"])
        except Exception:
            pass
        if not details["title"]:
            details["title"] = data["dirName"]
        contents = data["list"]
        if not contents:
            return
        for content in contents:
            if content["type"] == "dir" and "url" not in content:
                if not folderPath:
                    newFolderPath = ospath.join(details["title"], content["name"])
                else:
                    newFolderPath = ospath.join(folderPath, content["name"])
                if not details["title"]:
                    details["title"] = content["name"]
                __fetch_links(session, content["id"], newFolderPath)
            elif "url" in content:
                if not folderPath:
                    folderPath = details["title"]
                filename = content["name"]
                if (
                    sub_type := content.get("sub_type")
                ) and not filename.strip().endswith(sub_type):
                    filename += f".{sub_type}"

                size = 0
                if "size" in content:
                    size = content["size"]
                    if isinstance(size, str) and size.isdigit():
                        size = float(size)
                    details["total_size"] += size

                item = {
                    "path": ospath.join(folderPath),
                    "filename": filename,
                    "url": content["url"],
                    "size": size,
                }
                details["contents"].append(item)

    try:
        with Session() as session:
            __fetch_links(session)
    except DirectDownloadLinkException as e:
        raise e
    return details


def gofile(url):
    """GoFile direct link generator with dynamic websiteToken fetching."""
    try:
        if "::" in url:
            _password = url.split("::")[-1]
            _password = sha256(_password.encode("utf-8")).hexdigest()
            url = url.split("::")[-2]
        else:
            _password = ""
        _id = url.split("/")[-1]
    except Exception as e:
        raise DirectDownloadLinkException(f"ERROR: {e.__class__.__name__}")

    def __generate_website_token(account_token=""):
        bucket = int(time.time() // 14400)
        preimage = f"{user_agent}::en-US::{account_token}::{bucket}::9844d94d963d30"
        return sha256(preimage.encode("utf-8")).hexdigest()

    def __get_website_token(session):
        """Dynamically fetch the websiteToken from GoFile's website."""
        global GOFILE_WEBSITE_TOKEN_CACHE
        if GOFILE_WEBSITE_TOKEN_CACHE:
            return GOFILE_WEBSITE_TOKEN_CACHE
        try:
            main_url = "https://gofile.io/"
            headers = {
                "User-Agent": user_agent,
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                "Accept-Language": "en-US,en;q=0.5",
                "Accept-Encoding": "gzip, deflate, br",
                "Referer": "https://gofile.io/",
            }
            response = session.get(main_url, headers=headers, timeout=10)
            if response.status_code == 200:
                patterns = [
                    r'appdata\.wt\s*=\s*["\']([^"\']+)["\']',
                    r'wt:\s*["\']([^"\']+)["\']',
                    r'"wt"\s*:\s*["\']([^"\']+)["\']',
                    r'websiteToken["\s:=]+["\']([^"\']+)["\']',
                ]
                for pattern in patterns:
                    wt_match = search(pattern, response.text)
                    if wt_match:
                        GOFILE_WEBSITE_TOKEN_CACHE = wt_match.group(1)
                        return GOFILE_WEBSITE_TOKEN_CACHE

            js_urls = [
                "https://gofile.io/dist/js/config.js",
                "https://gofile.io/dist/js/global.js",
                "https://gofile.io/dist/js/alljs.js",
            ]
            for js_url in js_urls:
                try:
                    js_response = session.get(js_url, headers=headers, timeout=10)
                    if js_response.status_code == 200:
                        for pattern in patterns:
                            wt_match = search(pattern, js_response.text)
                            if wt_match:
                                GOFILE_WEBSITE_TOKEN_CACHE = wt_match.group(1)
                                return GOFILE_WEBSITE_TOKEN_CACHE
                except Exception:
                    continue
        except Exception:
            pass
        GOFILE_WEBSITE_TOKEN_CACHE = "9844d94d963d30"
        return GOFILE_WEBSITE_TOKEN_CACHE

    def __get_token(session, force_new=False):
        """Get a valid GoFile token from cache/config/fallback or create a guest token."""
        global gofile_token_cache
        guest_wt = __generate_website_token("")
        headers = {
            "User-Agent": user_agent,
            "Accept-Encoding": "gzip, deflate, br",
            "Accept": "*/*",
            "Connection": "keep-alive",
            "Origin": "https://gofile.io",
            "Referer": "https://gofile.io/",
            "X-Website-Token": guest_wt,
            "X-BL": "en-US",
        }
        accounts_website = "https://api.gofile.io/accounts/website"

        def _validate(token_value):
            test_headers = dict(headers)
            test_headers["Authorization"] = f"Bearer {token_value}"
            test_res = session.get(
                accounts_website, headers=test_headers, timeout=10
            ).json()
            return test_res.get("status") == "ok"

        if not force_new and gofile_token_cache:
            try:
                if _validate(gofile_token_cache):
                    return gofile_token_cache
            except Exception:
                pass
            gofile_token_cache = None

        if not force_new:
            for candidate in [
                (Config.GOFILE_TOKEN or "").strip(),
                GOFILE_API_TOKEN_FALLBACK,
            ]:
                if not candidate:
                    continue
                try:
                    if _validate(candidate):
                        gofile_token_cache = candidate
                        return gofile_token_cache
                except Exception:
                    continue

        __url = "https://api.gofile.io/accounts"
        try:
            __res = session.post(__url, headers=headers).json()
            if __res["status"] != "ok":
                raise DirectDownloadLinkException("ERROR: Failed to get token.")
            gofile_token_cache = __res["data"]["token"]
            return gofile_token_cache
        except Exception as e:
            raise e

    def __resolve_api_token(session):
        return __get_token(session)

    def __resolve_x_website_token(api_token, bucket_shift=0):
        if not api_token:
            return wt
        try:
            bucket = int((time.time() / 14400) // 1) + int(bucket_shift)
            preimage = f"{user_agent}::en-US::{api_token}::{bucket}::9844d94d963d30"
            return sha256(preimage.encode("utf-8")).hexdigest()
        except Exception:
            if api_token == GOFILE_API_TOKEN_FALLBACK and GOFILE_API_XWT_FALLBACK:
                return GOFILE_API_XWT_FALLBACK
            return wt

    def __append_items_from_data(data, folderPath=""):
        if not isinstance(data, dict):
            return 0

        data_type = data.get("type")
        data_name = data.get("name") or data.get("code") or _id
        if not details["title"]:
            details["title"] = data_name

        added = 0

        if data_type == "file" and (data.get("link") or data.get("directLink")):
            download_url = data.get("link") or data.get("directLink")
            current_path = folderPath or ""
            details["contents"].append(
                {
                    "path": ospath.join(current_path),
                    "filename": data.get("name") or _id,
                    "url": download_url,
                }
            )
            if "size" in data:
                size = data["size"]
                if isinstance(size, str) and size.isdigit():
                    size = float(size)
                details["total_size"] += size
            return 1

        raw_children = data.get("children", {})
        if isinstance(raw_children, dict):
            children = list(raw_children.values())
        elif isinstance(raw_children, list):
            children = raw_children
        else:
            children = []

        for alt_key in ("contents", "items", "files"):
            alt_val = data.get(alt_key)
            if isinstance(alt_val, list):
                children.extend(alt_val)

        for content in children:
            if not isinstance(content, dict):
                continue

            content_type = content.get("type", "")
            content_name = content.get("name") or content.get("code") or _id

            is_folder = content_type == "folder" or bool(content.get("children"))
            if is_folder:
                if not content.get("public", True):
                    continue
                sub_id = content.get("id") or content.get("code")
                if not sub_id:
                    continue
                if not folderPath:
                    newFolderPath = ospath.join(content_name)
                else:
                    newFolderPath = ospath.join(folderPath, content_name)
                __fetch_links(session, sub_id, newFolderPath, retry=False)
                continue

            current_path = folderPath or ""
            download_url = content.get("link", "") or content.get("directLink", "")

            if not download_url and "directLinks" in content:
                direct_links = content.get("directLinks", {})
                if isinstance(direct_links, dict) and direct_links:
                    first_key = next(iter(direct_links), None)
                    if first_key:
                        dl_info = direct_links[first_key]
                        if isinstance(dl_info, dict):
                            download_url = dl_info.get("directLink", "")
                        elif isinstance(dl_info, str):
                            download_url = dl_info

            if not download_url:
                continue

            item = {
                "path": ospath.join(current_path),
                "filename": content_name,
                "url": download_url,
            }
            if "size" in content:
                size = content["size"]
                if isinstance(size, str) and size.isdigit():
                    size = float(size)
                details["total_size"] += size
            details["contents"].append(item)
            added += 1

        return added

    def __fetch_links_from_html(session, content_id):
        page_url = f"https://gofile.io/d/{content_id}"
        headers = {
            "User-Agent": user_agent,
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Referer": "https://gofile.io/",
        }
        try:
            html = session.get(page_url, headers=headers, timeout=12).text
        except Exception:
            return False

        raw = html.replace("\\/", "/")
        links = []
        for link in findall(
            r"https://[A-Za-z0-9.-]*gofile\.io/download/[^\"'\s<]+", raw
        ):
            if link not in links:
                links.append(link)

        if not links:
            return False

        if not details["title"]:
            details["title"] = content_id

        for idx, dlink in enumerate(links, start=1):
            filename = urlparse(dlink).path.split("/")[-1] or f"file_{idx}"
            details["contents"].append(
                {
                    "path": "",
                    "filename": filename,
                    "url": dlink,
                }
            )
        return True

    def __fetch_links(session, _id, folderPath="", retry=True, rate_retry=2):
        nonlocal token, xwt
        _url = f"https://api.gofile.io/contents/{_id}"
        headers = {
            "User-Agent": user_agent,
            "Accept-Encoding": "gzip, deflate, br",
            "Accept": "*/*",
            "Accept-Language": "en-US,en;q=0.5",
            "Connection": "keep-alive",
            "Authorization": f"Bearer {token}",
            "X-Website-Token": xwt,
            "X-BL": "en-US",
            "Origin": "https://gofile.io",
            "Referer": "https://gofile.io/",
        }
        params = {"cache": "true"}
        if _password:
            params["password"] = _password
        try:
            _json = session.get(_url, headers=headers, params=params).json()
        except Exception as e:
            raise DirectDownloadLinkException(f"ERROR: {e.__class__.__name__}")

        status = _json.get("status", "")

        if status == "error-rateLimit":
            if rate_retry > 0:
                sleep(2)
                __fetch_links(
                    session,
                    _id,
                    folderPath,
                    retry=retry,
                    rate_retry=rate_retry - 1,
                )
                return
            if not folderPath and __fetch_links_from_html(session, _id):
                return
            raise DirectDownloadLinkException(
                "ERROR: GoFile API rate limited. Please retry after a short while."
            )

        if status in ("error-unauth", "error-forbidden", "error-tokenInvalid"):
            global gofile_token_cache
            gofile_token_cache = None
            if retry:
                try:
                    token = __get_token(session, force_new=True)
                    xwt = __resolve_x_website_token(token)
                    details["header"] = [
                        f"Cookie: accountToken={token}",
                        f"X-Website-Token: {xwt}",
                        f"User-Agent: {user_agent}",
                        "Accept: */*",
                        "Connection: keep-alive",
                        "Referer: https://gofile.io/",
                    ]
                    __fetch_links(
                        session,
                        _id,
                        folderPath,
                        retry=False,
                        rate_retry=rate_retry,
                    )
                    return
                except Exception:
                    raise DirectDownloadLinkException(
                        "ERROR: GoFile token revoked and failed to create new token."
                    )
            raise DirectDownloadLinkException("ERROR: GoFile token revoked.")

        if status == "error-token" and retry:
            alt_xwt = __resolve_x_website_token(token, bucket_shift=-1)
            if alt_xwt != xwt:
                xwt = alt_xwt
                details["header"] = [
                    f"Cookie: accountToken={token}",
                    f"X-Website-Token: {xwt}",
                    f"User-Agent: {user_agent}",
                    "Accept: */*",
                    "Connection: keep-alive",
                    "Referer: https://gofile.io/",
                ]
                __fetch_links(
                    session,
                    _id,
                    folderPath,
                    retry=False,
                    rate_retry=rate_retry,
                )
                return

        if status == "error-passwordRequired":
            raise DirectDownloadLinkException(
                f"ERROR:\n{PASSWORD_ERROR_MESSAGE.format(url)}"
            )
        if status == "error-passwordWrong":
            raise DirectDownloadLinkException("ERROR: This password is wrong !")
        if status == "error-notFound":
            raise DirectDownloadLinkException(
                "ERROR: File not found on gofile's server"
            )
        if status == "error-notPublic":
            raise DirectDownloadLinkException("ERROR: This folder is not public")
        if status in ("error-notPremium", "error-token"):
            try:
                anon_headers = {
                    "User-Agent": user_agent,
                    "Accept-Encoding": "gzip, deflate, br",
                    "Accept": "*/*",
                    "Accept-Language": "en-US,en;q=0.5",
                    "Connection": "keep-alive",
                    "X-Website-Token": xwt,
                    "X-BL": "en-US",
                    "Origin": "https://gofile.io",
                    "Referer": "https://gofile.io/",
                }
                anon_params = dict(params)
                anon_params["wt"] = wt
                anon_json = session.get(
                    _url, headers=anon_headers, params=anon_params
                ).json()
                if anon_json.get("status") == "ok":
                    __append_items_from_data(anon_json["data"], folderPath)
                    return
            except Exception:
                pass

            if not folderPath and __fetch_links_from_html(session, _id):
                return

            raise DirectDownloadLinkException(
                "ERROR: GoFile API access blocked and public fallback failed for this link."
            )

        data = _json.get("data", {})
        added = __append_items_from_data(data, folderPath)
        if not added and not folderPath:
            if __fetch_links_from_html(session, _id):
                return
            raise DirectDownloadLinkException(
                "ERROR: Unable to extract downloadable files from GoFile response."
            )

    details = {"contents": [], "title": "", "total_size": 0}
    with Session() as session:
        try:
            wt = __get_website_token(session)
            token = __resolve_api_token(session)
            xwt = __resolve_x_website_token(token)
        except Exception as e:
            raise DirectDownloadLinkException(f"ERROR: {e.__class__.__name__}")
        details["header"] = [
            f"Cookie: accountToken={token}",
            f"X-Website-Token: {xwt}",
            f"User-Agent: {user_agent}",
            "Accept: */*",
            "Connection: keep-alive",
            "Referer: https://gofile.io/",
        ]
        try:
            __fetch_links(session, _id)
        except Exception as e:
            raise DirectDownloadLinkException(e)

    if len(details["contents"]) == 1:
        return (details["contents"][0]["url"], details["header"])
    return details


def mediafireFolder(url):
    if "::" in url:
        _password = url.split("::")[-1]
        url = url.split("::")[-2]
    else:
        _password = ""
    try:
        raw = url.split("/", 4)[-1]
        folderkey = raw.split("/", 1)[0]
        folderkey = folderkey.split(",")
    except Exception:
        raise DirectDownloadLinkException("ERROR: Could not parse ")
    if len(folderkey) == 1:
        folderkey = folderkey[0]
    details = {"contents": [], "title": "", "total_size": 0, "header": ""}

    session = create_scraper()
    adapter = HTTPAdapter(
        max_retries=Retry(total=10, read=10, connect=10, backoff_factor=0.3)
    )
    session.mount("http://", adapter)
    session.mount("https://", adapter)
    session = create_scraper(
        browser={"browser": "firefox", "platform": "windows", "mobile": False},
        delay=10,
        sess=session,
    )
    folder_infos = []

    def __get_info(folderkey):
        try:
            if isinstance(folderkey, list):
                folderkey = ",".join(folderkey)
            _json = session.post(
                "https://www.mediafire.com/api/1.5/folder/get_info.php",
                data={
                    "recursive": "yes",
                    "folder_key": folderkey,
                    "response_format": "json",
                },
            ).json()
        except Exception as e:
            raise DirectDownloadLinkException(
                f"ERROR: {e.__class__.__name__} While getting info"
            )
        _res = _json["response"]
        if "folder_infos" in _res:
            folder_infos.extend(_res["folder_infos"])
        elif "folder_info" in _res:
            folder_infos.append(_res["folder_info"])
        elif "message" in _res:
            raise DirectDownloadLinkException(f"ERROR: {_res['message']}")
        else:
            raise DirectDownloadLinkException("ERROR: something went wrong!")

    try:
        __get_info(folderkey)
    except Exception as e:
        raise DirectDownloadLinkException(e)

    details["title"] = folder_infos[0]["name"]

    def __scraper(url):
        session = create_scraper()
        parsed_url = urlparse(url)
        url = f"{parsed_url.scheme}://{parsed_url.netloc}{parsed_url.path}"

        def __repair_download(url):
            try:
                html = HTML(session.get(url).text)
                if new_link := html.xpath('//a[@id="continue-btn"]/@href'):
                    return __scraper(f"https://mediafire.com/{new_link[0]}")
            except Exception:
                return

        try:
            html = HTML(session.get(url).text)
        except Exception:
            return
        if html.xpath("//div[@class='passwordPrompt']"):
            if not _password:
                raise DirectDownloadLinkException(
                    f"ERROR: {PASSWORD_ERROR_MESSAGE}".format(url)
                )
            try:
                html = HTML(session.post(url, data={"downloadp": _password}).text)
            except Exception:
                return
            if html.xpath("//div[@class='passwordPrompt']"):
                return
        if final_link := html.xpath('//a[@aria-label="Download file"]/@href'):
            if final_link[0].startswith("//"):
                return __scraper(f"https://{final_link[0][2:]}")
            return final_link[0]
        if repair_link := html.xpath("//a[@class='retry']/@href"):
            return __repair_download(repair_link[0])

    def __get_content(folderKey, folderPath="", content_type="folders"):
        try:
            params = {
                "content_type": content_type,
                "folder_key": folderKey,
                "response_format": "json",
            }
            _json = session.get(
                "https://www.mediafire.com/api/1.5/folder/get_content.php",
                params=params,
            ).json()
        except Exception as e:
            raise DirectDownloadLinkException(
                f"ERROR: {e.__class__.__name__} While getting content"
            )
        _res = _json["response"]
        if "message" in _res:
            raise DirectDownloadLinkException(f"ERROR: {_res['message']}")
        _folder_content = _res["folder_content"]
        if content_type == "folders":
            folders = _folder_content["folders"]
            for folder in folders:
                if folderPath:
                    newFolderPath = ospath.join(folderPath, folder["name"])
                else:
                    newFolderPath = ospath.join(folder["name"])
                __get_content(folder["folderkey"], newFolderPath)
            __get_content(folderKey, folderPath, "files")
        else:
            files = _folder_content["files"]
            for file in files:
                item = {}
                if not (_url := __scraper(file["links"]["normal_download"])):
                    continue
                item["filename"] = file["filename"]
                if not folderPath:
                    folderPath = details["title"]
                item["path"] = ospath.join(folderPath)
                item["url"] = _url

                size = 0
                if "size" in file:
                    size = file["size"]
                    if isinstance(size, str) and size.isdigit():
                        size = float(size)
                    details["total_size"] += size
                item["size"] = size
                details["contents"].append(item)

    try:
        for folder in folder_infos:
            __get_content(folder["folderkey"], folder["name"])
    except Exception as e:
        raise DirectDownloadLinkException(e)
    finally:
        session.close()
    if len(details["contents"]) == 1:
        return (details["contents"][0]["url"], details["header"])
    return details


def cf_bypass(url):
    "DO NOT ABUSE THIS"
    try:
        data = {"cmd": "request.get", "url": url, "maxTimeout": 60000}
        _json = post(
            "https://cf.jmdkh.eu.org/v1",
            headers={"Content-Type": "application/json"},
            json=data,
        ).json()
        if _json["status"] == "ok":
            return _json["solution"]["response"]
    except Exception as e:
        e
    raise DirectDownloadLinkException("ERROR: Con't bypass cloudflare")


def send_cm_file(url, file_id=None):
    if "::" in url:
        _password = url.split("::")[-1]
        url = url.split("::")[-2]
    else:
        _password = ""
    _passwordNeed = False
    with create_scraper() as session:
        if file_id is None:
            try:
                html = HTML(session.get(url).text)
            except Exception as e:
                raise DirectDownloadLinkException(
                    f"ERROR: {e.__class__.__name__}"
                ) from e
            if html.xpath("//input[@name='password']"):
                _passwordNeed = True
            if not (file_id := html.xpath("//input[@name='id']/@value")):
                raise DirectDownloadLinkException("ERROR: file_id not found")
        try:
            data = {"op": "download2", "id": file_id}
            if _password and _passwordNeed:
                data["password"] = _password
            _res = session.post("https://send.cm/", data=data, allow_redirects=False)
            if "Location" in _res.headers:
                return (_res.headers["Location"], "Referer: https://send.cm/")
        except Exception as e:
            raise DirectDownloadLinkException(f"ERROR: {e.__class__.__name__}") from e
        if _passwordNeed:
            raise DirectDownloadLinkException(
                f"ERROR:\n{PASSWORD_ERROR_MESSAGE.format(url)}"
            )
        raise DirectDownloadLinkException("ERROR: Direct link not found")


def send_cm(url):
    if "/d/" in url:
        return send_cm_file(url)
    elif "/s/" not in url:
        file_id = url.split("/")[-1]
        return send_cm_file(url, file_id)
    splitted_url = url.split("/")
    details = {
        "contents": [],
        "title": "",
        "total_size": 0,
        "header": "Referer: https://send.cm/",
    }
    if len(splitted_url) == 5:
        url += "/"
        splitted_url = url.split("/")
    if len(splitted_url) >= 7:
        details["title"] = splitted_url[5]
    else:
        details["title"] = splitted_url[-1]
    session = Session()

    def __collectFolders(html):
        folders = []
        folders_urls = html.xpath("//h6/a/@href")
        folders_names = html.xpath("//h6/a/text()")
        for folders_url, folders_name in zip(folders_urls, folders_names):
            folders.append(
                {
                    "folder_link": folders_url.strip(),
                    "folder_name": folders_name.strip(),
                }
            )
        return folders

    def __getFile_link(file_id):
        try:
            _res = session.post(
                "https://send.cm/",
                data={"op": "download2", "id": file_id},
                allow_redirects=False,
            )
            if "Location" in _res.headers:
                return _res.headers["Location"]
        except Exception:
            pass

    def __getFiles(html):
        files = []
        hrefs = html.xpath('//tr[@class="selectable"]//a/@href')
        file_names = html.xpath('//tr[@class="selectable"]//a/text()')
        sizes = html.xpath('//tr[@class="selectable"]//span/text()')
        for href, file_name, size_text in zip(hrefs, file_names, sizes):
            files.append(
                {
                    "file_id": href.split("/")[-1],
                    "file_name": file_name.strip(),
                    "size": speed_string_to_bytes(size_text.strip()),
                }
            )
        return files

    def __writeContents(html_text, folderPath=""):
        folders = __collectFolders(html_text)
        for folder in folders:
            _html = HTML(cf_bypass(folder["folder_link"]))
            __writeContents(_html, ospath.join(folderPath, folder["folder_name"]))
        files = __getFiles(html_text)
        for file in files:
            if not (link := __getFile_link(file["file_id"])):
                continue
            item = {
                "url": link,
                "filename": file["filename"],
                "path": folderPath,
                "size": file["size"],
            }
            details["total_size"] += file["size"]
            details["contents"].append(item)

    try:
        mainHtml = HTML(cf_bypass(url))
    except DirectDownloadLinkException as e:
        session.close()
        raise e
    except Exception as e:
        session.close()
        raise DirectDownloadLinkException(
            f"ERROR: {e.__class__.__name__} While getting mainHtml"
        )
    try:
        __writeContents(mainHtml, details["title"])
    except DirectDownloadLinkException as e:
        session.close()
        raise e
    except Exception as e:
        session.close()
        raise DirectDownloadLinkException(
            f"ERROR: {e.__class__.__name__} While writing Contents"
        )
    session.close()
    if len(details["contents"]) == 1:
        return (details["contents"][0]["url"], details["header"])
    return details


def doods(url):
    if "/e/" in url:
        url = url.replace("/e/", "/d/")
    parsed_url = urlparse(url)
    with create_scraper() as session:
        try:
            html = HTML(session.get(url).text)
        except Exception as e:
            raise DirectDownloadLinkException(
                f"ERROR: {e.__class__.__name__} While fetching token link"
            ) from e
        if not (link := html.xpath("//div[@class='download-content']//a/@href")):
            raise DirectDownloadLinkException(
                "ERROR: Token Link not found or maybe not allow to download! open in browser."
            )
        link = f"{parsed_url.scheme}://{parsed_url.hostname}{link[0]}"
        sleep(2)
        try:
            _res = session.get(link)
        except Exception as e:
            raise DirectDownloadLinkException(
                f"ERROR: {e.__class__.__name__} While fetching download link"
            ) from e
    if not (link := search(r"window\.open\('(\S+)'", _res.text)):
        raise DirectDownloadLinkException("ERROR: Download link not found try again")
    return (link.group(1), f"Referer: {parsed_url.scheme}://{parsed_url.hostname}/")


def easyupload(url):
    if "::" in url:
        _password = url.split("::")[-1]
        url = url.split("::")[-2]
    else:
        _password = ""
    file_id = url.split("/")[-1]
    with create_scraper() as session:
        try:
            _res = session.get(url)
        except Exception as e:
            raise DirectDownloadLinkException(f"ERROR: {e.__class__.__name__}")
        first_page_html = HTML(_res.text)
        if (
            first_page_html.xpath("//h6[contains(text(),'Password Protected')]")
            and not _password
        ):
            raise DirectDownloadLinkException(
                f"ERROR:\n{PASSWORD_ERROR_MESSAGE.format(url)}"
            )
        if not (
            match := search(
                r"https://eu(?:[1-9][0-9]?|100)\.easyupload\.io/action\.php", _res.text
            )
        ):
            raise DirectDownloadLinkException(
                "ERROR: Failed to get server for EasyUpload Link"
            )
        action_url = match.group()
        session.headers.update({"referer": "https://easyupload.io/"})
        recaptcha_params = {
            "k": "6LfWajMdAAAAAGLXz_nxz2tHnuqa-abQqC97DIZ3",
            "ar": "1",
            "co": "aHR0cHM6Ly9lYXN5dXBsb2FkLmlvOjQ0Mw..",
            "hl": "en",
            "v": "0hCdE87LyjzAkFO5Ff-v7Hj1",
            "size": "invisible",
            "cb": "c3o1vbaxbmwe",
        }
        if not (captcha_token := get_captcha_token(session, recaptcha_params)):
            raise DirectDownloadLinkException("ERROR: Captcha token not found")
        try:
            data = {
                "type": "download-token",
                "url": file_id,
                "value": _password,
                "captchatoken": captcha_token,
                "method": "regular",
            }
            json_resp = session.post(url=action_url, data=data).json()
        except Exception as e:
            raise DirectDownloadLinkException(f"ERROR: {e.__class__.__name__}") from e
    if "download_link" in json_resp:
        return json_resp["download_link"]
    elif "data" in json_resp:
        raise DirectDownloadLinkException(
            f"ERROR: Failed to generate direct link due to {json_resp['data']}"
        )
    raise DirectDownloadLinkException(
        "ERROR: Failed to generate direct link from EasyUpload."
    )


def filelions_and_streamwish(url):
    parsed_url = urlparse(url)
    hostname = parsed_url.hostname
    scheme = parsed_url.scheme
    if any(
        x in hostname
        for x in [
            "filelions.co",
            "filelions.live",
            "filelions.to",
            "filelions.site",
            "cabecabean.lol",
            "filelions.online",
            "mycloudz.cc",
        ]
    ):
        apiKey = Config.FILELION_API
        apiUrl = "https://vidhideapi.com"
    elif any(
        x in hostname
        for x in [
            "embedwish.com",
            "kissmovies.net",
            "kitabmarkaz.xyz",
            "wishfast.top",
            "streamwish.to",
        ]
    ):
        apiKey = Config.STREAMWISH_API
        apiUrl = "https://api.streamwish.com"
    if not apiKey:
        raise DirectDownloadLinkException(
            f"ERROR: API is not provided get it from {scheme}://{hostname}"
        )
    file_code = url.split("/")[-1]
    quality = ""
    if bool(file_code.strip().endswith(("_o", "_h", "_n", "_l"))):
        spited_file_code = file_code.rsplit("_", 1)
        quality = spited_file_code[1]
        file_code = spited_file_code[0]
    url = f"{scheme}://{hostname}/{file_code}"
    with Session() as session:
        try:
            _res = session.get(
                f"{apiUrl}/api/file/direct_link",
                params={"key": apiKey, "file_code": file_code, "hls": "1"},
            ).json()
        except Exception as e:
            raise DirectDownloadLinkException(f"ERROR: {e.__class__.__name__}") from e
    if _res["status"] != 200:
        raise DirectDownloadLinkException(f"ERROR: {_res['msg']}")
    result = _res["result"]
    if not result["versions"]:
        raise DirectDownloadLinkException("ERROR: File Not Found")
    error = "\nProvide a quality to download the video\nAvailable Quality:"
    for version in result["versions"]:
        if quality == version["name"]:
            return version["url"]
        elif version["name"] == "l":
            error += "\nLow"
        elif version["name"] == "n":
            error += "\nNormal"
        elif version["name"] == "o":
            error += "\nOriginal"
        elif version["name"] == "h":
            error += "\nHD"
        error += f" <code>{url}_{version['name']}</code>"
    raise DirectDownloadLinkException(f"ERROR: {error}")


def streamvid(url: str):
    file_code = url.split("/")[-1]
    parsed_url = urlparse(url)
    url = f"{parsed_url.scheme}://{parsed_url.hostname}/d/{file_code}"
    quality_defined = bool(url.strip().endswith(("_o", "_h", "_n", "_l")))
    with create_scraper() as session:
        try:
            html = HTML(session.get(url).text)
        except Exception as e:
            raise DirectDownloadLinkException(f"ERROR: {e.__class__.__name__}") from e
        if quality_defined:
            data = {}
            if not (inputs := html.xpath('//form[@id="F1"]//input')):
                raise DirectDownloadLinkException("ERROR: No inputs found")
            for i in inputs:
                if key := i.get("name"):
                    data[key] = i.get("value")
            try:
                html = HTML(session.post(url, data=data).text)
            except Exception as e:
                raise DirectDownloadLinkException(
                    f"ERROR: {e.__class__.__name__}"
                ) from e
            if not (
                script := html.xpath(
                    '//script[contains(text(),"document.location.href")]/text()'
                )
            ):
                if error := html.xpath(
                    '//div[@class="alert alert-danger"][1]/text()[2]'
                ):
                    raise DirectDownloadLinkException(f"ERROR: {error[0]}")
                raise DirectDownloadLinkException(
                    "ERROR: direct link script not found!"
                )
            if directLink := findall(r'document\.location\.href="(.*)"', script[0]):
                return directLink[0]
            raise DirectDownloadLinkException(
                "ERROR: direct link not found! in the script"
            )
        elif (qualities_urls := html.xpath('//div[@id="dl_versions"]/a/@href')) and (
            qualities := html.xpath('//div[@id="dl_versions"]/a/text()[2]')
        ):
            error = "\nProvide a quality to download the video\nAvailable Quality:"
            for quality_url, quality in zip(qualities_urls, qualities):
                error += f"\n{quality.strip()} <code>{quality_url}</code>"
            raise DirectDownloadLinkException(f"ERROR: {error}")
        elif error := html.xpath('//div[@class="not-found-text"]/text()'):
            raise DirectDownloadLinkException(f"ERROR: {error[0]}")
        raise DirectDownloadLinkException("ERROR: Something went wrong")


def streamhub(url):
    file_code = url.split("/")[-1]
    parsed_url = urlparse(url)
    url = f"{parsed_url.scheme}://{parsed_url.hostname}/d/{file_code}"
    with create_scraper() as session:
        try:
            html = HTML(session.get(url).text)
        except Exception as e:
            raise DirectDownloadLinkException(f"ERROR: {e.__class__.__name__}") from e
        if not (inputs := html.xpath('//form[@name="F1"]//input')):
            raise DirectDownloadLinkException("ERROR: No inputs found")
        data = {}
        for i in inputs:
            if key := i.get("name"):
                data[key] = i.get("value")
        session.headers.update({"referer": url})
        sleep(1)
        try:
            html = HTML(session.post(url, data=data).text)
        except Exception as e:
            raise DirectDownloadLinkException(f"ERROR: {e.__class__.__name__}") from e
        if directLink := html.xpath(
            '//a[@class="btn btn-primary btn-go downloadbtn"]/@href'
        ):
            return directLink[0]
        if error := html.xpath('//div[@class="alert alert-danger"]/text()[2]'):
            raise DirectDownloadLinkException(f"ERROR: {error[0]}")
        raise DirectDownloadLinkException("ERROR: direct link not found!")


def pcloud(url):
    with create_scraper() as session:
        try:
            res = session.get(url)
        except Exception as e:
            raise DirectDownloadLinkException(f"ERROR: {e.__class__.__name__}") from e
    if link := findall(r".downloadlink.:..(https:.*)..", res.text):
        return link[0].replace(r"\/", "/")
    raise DirectDownloadLinkException("ERROR: Direct link not found")


def tmpsend(url):
    parsed_url = urlparse(url)
    if any(x in parsed_url.path for x in ["thank-you", "download"]):
        query_params = parse_qs(parsed_url.query)
        if file_id := query_params.get("d"):
            file_id = file_id[0]
    elif not (file_id := parsed_url.path.strip("/")):
        raise DirectDownloadLinkException("ERROR: Invalid URL format")
    referer_url = f"https://tmpsend.com/thank-you?d={file_id}"
    header = f"Referer: {referer_url}"
    download_link = f"https://tmpsend.com/download?d={file_id}"
    return download_link, header


def qiwi(url):
    """qiwi.gg link generator
    based on https://github.com/aenulrofik"""
    with Session() as session:
        file_id = url.split("/")[-1]
        try:
            res = session.get(url).text
        except Exception as e:
            raise DirectDownloadLinkException(f"ERROR: {e.__class__.__name__}") from e
        tree = HTML(res)
        if name := tree.xpath('//h1[@class="page_TextHeading__VsM7r"]/text()'):
            ext = name[0].split(".")[-1]
            return f"https://spyderrock.com/{file_id}.{ext}"
        else:
            raise DirectDownloadLinkException("ERROR: File not found")


def mp4upload(url):
    with Session() as session:
        try:
            url = url.replace("embed-", "")
            req = session.get(url).text
            tree = HTML(req)
            inputs = tree.xpath("//input")
            header = {"Referer": "https://www.mp4upload.com/"}
            data = {input.get("name"): input.get("value") for input in inputs}
            if not data:
                raise DirectDownloadLinkException("ERROR: File Not Found!")
            post = session.post(
                url,
                data=data,
                headers={
                    "User-Agent": user_agent,
                    "Referer": "https://www.mp4upload.com/",
                },
            ).text
            tree = HTML(post)
            inputs = tree.xpath('//form[@name="F1"]//input')
            data = {
                input.get("name"): input.get("value").replace(" ", "")
                for input in inputs
            }
            if not data:
                raise DirectDownloadLinkException("ERROR: File Not Found!")
            data["referer"] = url
            direct_link = session.post(url, data=data).url
            return direct_link, header
        except Exception:
            raise DirectDownloadLinkException("ERROR: File Not Found!")


def berkasdrive(url):
    """berkasdrive.com link generator
    by https://github.com/aenulrofik"""
    with Session() as session:
        try:
            sesi = session.get(url).text
        except Exception as e:
            raise DirectDownloadLinkException(f"ERROR: {e.__class__.__name__}") from e
    html = HTML(sesi)
    if link := html.xpath("//script")[0].text.split('"')[1]:
        return b64decode(link).decode("utf-8")
    else:
        raise DirectDownloadLinkException("ERROR: File Not Found!")


def swisstransfer(link):
    matched_link = match(
        r"https://www\.swisstransfer\.com/d/([\w-]+)(?:\:\:(\w+))?", link
    )
    if not matched_link:
        raise DirectDownloadLinkException(
            f"ERROR: Invalid SwissTransfer link format {link}"
        )

    transfer_id, password = matched_link.groups()
    password = password or ""

    def encode_password(password):
        return b64encode(password.encode("utf-8")).decode("utf-8") if password else ""

    def getfile(transfer_id, password):
        url = f"https://www.swisstransfer.com/api/links/{transfer_id}"
        headers = {
            "User-Agent": "Mozilla/5.0",
            "Authorization": encode_password(password) if password else "",
            "Content-Type": "application/json" if not password else "",
        }
        response = get(url, headers=headers)

        if response.status_code == 200:
            try:
                return response.json(), headers
            except ValueError:
                raise DirectDownloadLinkException(
                    f"ERROR: Error parsing JSON response {response.text}"
                )
        raise DirectDownloadLinkException(
            f"ERROR: Error fetching file details {response.status_code}, {response.text}"
        )

    def gettoken(password, containerUUID, fileUUID):
        url = "https://www.swisstransfer.com/api/generateDownloadToken"
        headers = {
            "User-Agent": "Mozilla/5.0",
            "Content-Type": "application/json",
        }
        body = {
            "password": password,
            "containerUUID": containerUUID,
            "fileUUID": fileUUID,
        }

        response = post(url, headers=headers, json=body)

        if response.status_code == 200:
            return response.text.strip().replace('"', "")
        raise DirectDownloadLinkException(
            f"ERROR: Error generating download token {response.status_code}, {response.text}"
        )

    data, headers = getfile(transfer_id, password)
    if not data:
        return None

    try:
        container_uuid = data["data"]["containerUUID"]
        download_host = data["data"]["downloadHost"]
        files = data["data"]["container"]["files"]
        folder_name = data["data"]["container"]["message"] or "unknown"
    except (KeyError, IndexError, TypeError) as e:
        raise DirectDownloadLinkException(f"ERROR: Error parsing file details {e}")

    total_size = sum(file["fileSizeInBytes"] for file in files)

    if len(files) == 1:
        file = files[0]
        file_uuid = file["UUID"]
        token = gettoken(password, container_uuid, file_uuid)
        download_url = f"https://{download_host}/api/download/{transfer_id}/{file_uuid}?token={token}"
        return download_url, "User-Agent:Mozilla/5.0"

    contents = []
    for file in files:
        file_uuid = file["UUID"]
        file_name = file["fileName"]
        file_size = file["fileSizeInBytes"]

        token = gettoken(password, container_uuid, file_uuid)
        if not token:
            continue

        download_url = f"https://{download_host}/api/download/{transfer_id}/{file_uuid}?token={token}"
        contents.append(
            {"filename": file_name, "path": "", "url": download_url, "size": file_size}
        )

    return {
        "contents": contents,
        "title": folder_name,
        "total_size": total_size,
        "header": "User-Agent:Mozilla/5.0",
    }


def videq(url: str):
    """Scrape videq links using videq_scraper module; supports single files and folders."""
    from .videq_scraper import (
        videq as videq_scrape,
        videq_folder as videq_folder_scrape,
    )

    if "/f/" in url:
        return videq_folder_scrape(url)
    return videq_scrape(url)


def videq_folder(url: str):
    """Scrape videq folder links using videq_scraper module."""
    from .videq_scraper import videq_folder as videq_folder_scrape

    return videq_folder_scrape(url)


def instagram(link: str) -> str:
    """
    Fetches the direct video download URL from an Instagram post.

    Args:
        link (str): The Instagram post URL.

    Returns:
        str: The direct video URL.

    Raises:
        DirectDownloadLinkException: If any error occurs during the process.
    """
    api_url = Config.INSTADL_API or "https://instagramcdn.vercel.app"
    full_url = f"{api_url}/api/video?postUrl={link}"

    try:
        response = get(full_url)
        response.raise_for_status()
        data = response.json()

        if (
            data.get("status") == "success"
            and "data" in data
            and "videoUrl" in data["data"]
        ):
            return data["data"]["videoUrl"]

        raise DirectDownloadLinkException("ERROR: Failed to retrieve video URL.")

    except Exception as e:
        raise DirectDownloadLinkException(f"ERROR: {e}")
