<!-- markdownlint-disable MD012 MD013 MD033 MD036 MD040 MD041 MD060 -->

<div align="center">
  <a href="https://github.com/irisXDR/NEO-WZML">
    <img src="https://iili.io/FLRJNMG.th.png" alt="NEO-WZML Logo" width="140" />
  </a>

# NEO-WZML

**A multi-functional Telegram bot to download from anywhere — torrents, Mega, TeraBox, YouTube, Google Drive, rclone, etc — and upload to Telegram, Cloud Drives, TeraBox, DDLs, or any rclone remote. Built-in FFmpeg processing, archive handling, torrent search, RSS monitoring, and web UI for file selection. Based on WZML-X**

[![Version](https://img.shields.io/badge/Version-1.1.1-2ea043)](https://github.com/irisXDR/NEO-WZML)
[![Python](https://img.shields.io/badge/Python-3.11%2B-3776AB?logo=python&logoColor=white)](https://www.python.org/)
[![Docker image](https://img.shields.io/docker/image-size/irisxdr/neo-wzml/latest?logo=docker&label=Docker%20Image&labelColor=161b22&color=2496ed)](https://hub.docker.com/r/irisxdr/neo-wzml)
[![FastAPI](https://img.shields.io/badge/FastAPI-009688?logo=fastapi&logoColor=white)](https://fastapi.tiangolo.com/)
[![MongoDB](https://img.shields.io/badge/MongoDB-47A248?logo=mongodb&logoColor=white)](https://www.mongodb.com/)
[![License: AGPL v3](https://img.shields.io/badge/License-AGPL%20v3-2ea043.svg)](LICENSE)
[![Telegram](https://img.shields.io/badge/Telegram-Channel-2ea043?logo=telegram&labelColor=161b22)](https://t.me/Chiheisen)

[Channel](https://t.me/Chiheisen) · [Support Group](https://t.me/ChiheisenUnion) · [Issues](https://github.com/irisXDR/NEO-WZML/issues) · [Docker Hub](https://hub.docker.com/r/irisxdr/neo-wzml)

</div>

> 🚧 **Status:** NEO-WZML is active and evolving. Public releases are expected to be usable, but if you hit a bug, please open an issue with logs and the command you ran.

---

## 📚 Table of Contents

- [✨ Why NEO-WZML](#-why-neo-wzml)
- [🚀 Highlights](#-highlights)
- [⚡ Quick Start](#-quick-start)
- [💬 Commands](#-commands)
- [🧭 Deployment Notes](#-deployment-notes)
- [🆚 What NEO-WZML Adds](#-what-neo-wzml-adds)
- [🔍 Troubleshooting](#-troubleshooting)
- [🤝 Support](#-support)
- [💰 Sponsors and Donations](#-sponsors-and-donations)
- [🙏 Credits](#-credits)
- [📄 License](#-license)

---

## ✨ Why NEO-WZML

NEO-WZML is built for people who move a lot of files through Telegram and cloud storage. It combines the classic mirror/leech workflow with modern file selection, persistent user settings, strong queue controls, and practical media tools.

- 🔌 **One bot, many sources:** direct links, torrents, Mega, TeraBox, Google Drive, JDownloader, yt-dlp, Telegram messages, and rclone remotes.
- 🎯 **Multiple upload targets:** Telegram leech, Google Drive, TeraBox, rclone remotes, GoFile, BuzzHeavier, and PixelDrain.
- 🌐 **Web file selection:** pick torrent files, Mega folder files, rclone folder files, and TeraBox account files before downloading.
- 🎬 **Media-ready:** split, convert, merge videos, sample videos, screenshots, metadata, thumbnails, and custom FFmpeg pipelines.
- 🗜️ **Archive workflow:** extract, password-protected ZIPs, image-only ZIPs, split archive handling, and 7z-backed progress.
- 🛡️ **Operational controls:** MongoDB persistence, queues, per-user limits, cooldowns, verification, auth gates, and safe group behavior.
- 🐳 **Docker-first deployment:** Compose setup with optional Gluetun scaffolding for VPN-routed torrent traffic.

---

## 🚀 Highlights

### 🌐 Download Engines

| Engine | Sources | Strength |
|--------|---------|----------|
| Aria2c | Direct links, magnets, torrents | Fast generic downloads |
| qBittorrent | Magnets and `.torrent` files | Search, selection, seeding |
| MegaSDK | Mega file and folder links | Native Mega downloads and folder selection |
| TeraBoxSDK | TeraBox share links and account files | Native TeraBox downloads, account browsing, web file selector |
| Google Drive | Files and folders | OAuth, service accounts, Team Drives |
| yt-dlp | YouTube and supported sites | Formats, playlists, audio extraction |
| Telegram | Messages and chat files | Large Telegram file handling |
| rclone | Any configured remote | Cloud download, cloud transfer, and web file selector |
| JDownloader | Premium hosts and containers | Host capture and CAPTCHA-aware flows |

### ☁️ Upload Targets

| Target | Notes |
|--------|-------|
| Telegram | Leech as media or document, with captions, thumbnails, dump chats, and splitting |
| Google Drive | OAuth, service accounts, Team Drives, duplicate checks, and index links |
| TeraBox | Upload to your TeraBox account via cookie auth (`-up tbx`), with optional folder path |
| rclone | Upload to any configured remote, including user configs via `mrcc:` |
| DDL hosts | Upload to GoFile, BuzzHeavier, PixelDrain, or multiple hosts |

### 🎛️ Processing Tools

| Feature | What it does |
|---------|--------------|
| `-z` / `-e` | Compress or extract before upload |
| `-zim` | ZIP only images into `Images.zip`, keeping videos/files normal |
| `-mv` | Merge folder videos into one `.mkv` with FFmpeg concat |
| `-ff` | Run configured FFmpeg command presets |
| `-ss` / `-sv` | Generate screenshots or sample videos |
| Metadata tools | Apply title, audio, video, and subtitle metadata |
| Filename rules | Prefixes, suffixes, regex swaps, and cleanup rules |

---

## ⚡ Quick Start

```bash
git clone https://github.com/irisXDR/NEO-WZML.git
cd NEO-WZML

cp sample_config.py config.py
# Edit config.py and set the required values listed below.

docker compose up -d --build
docker compose logs -f
```

Stop the stack:

```bash
docker compose down
```

### 🔑 Required Configuration

Create `config.py` from `sample_config.py` and set these first:

| Variable | Purpose |
|----------|---------|
| `BOT_TOKEN` | Telegram bot token from BotFather |
| `OWNER_ID` | Telegram numeric user id of the owner |
| `TELEGRAM_API` | API id from [my.telegram.org](https://my.telegram.org) |
| `TELEGRAM_HASH` | API hash from [my.telegram.org](https://my.telegram.org) |
| `DATABASE_URL` | MongoDB connection string |

Recommended for the full experience:

| Variable | Purpose |
|----------|---------|
| `BASE_URL` | Public URL for torrent, Mega, rclone, and TeraBox web file selection |
| `RCLONE_PATH` or `GDRIVE_ID` | Default cloud upload destination |
| `LEECH_DUMP_CHAT` | Default Telegram leech destination |
| `MEGA_EMAIL` / `MEGA_PASSWORD` | Optional Mega account for better Mega workflows |
| `TERABOX_ENABLED` | Enable TeraBox integration (default: `True`) |
| `DEFAULT_UPLOAD` | Default upload cycling: `rc` → `gd` → `tbx` (TeraBox) |

> 🔐 Keep tokens, OAuth files, MongoDB URLs, rclone configs, Mega accounts, TeraBox cookies, and service-account JSONs out of public commits.

---

## 💬 Commands

Send `/help` inside Telegram for the complete live command list.

| Command | Description |
|---------|-------------|
| `/mirror <link>` | Download and upload to cloud |
| `/leech <link>` | Download and upload to Telegram |
| `/qb` / `/qbleech` | Use qBittorrent for torrent workflows |
| `/jd` / `/jdleech` | Use JDownloader |
| `/ytdl` / `/ytdlleech` | Download with yt-dlp |
| `/clone <link>` | Clone supported cloud links/remotes |
| `/status` | View active and queued tasks |
| `/stats` | View bot, system, and component stats |
| `/list <query>` | Search Google Drive |
| `/count <link>` | Count Google Drive files and size |
| `/usettings` | User-specific settings |
| `/bsetting` | Owner configuration panel |
| `/tbx` / `tbx` | Browse your TeraBox account (interactive web file selector) |

### 🧩 Common Arguments

| Argument | Meaning |
|----------|---------|
| `-n <name>` | Rename before upload |
| `-s` | Select torrent/Mega files before downloading |
| `-z [password]` | ZIP before upload |
| `-e [password]` | Extract before upload |
| `-zim` / `-zipimages` | ZIP only images into one archive |
| `-mv` | Merge videos in a folder |
| `-up <destination>` | Override upload destination |
| `-up tbx` | Upload to your TeraBox account (requires `terabox.txt` cookie) |
| `-i <N>` | Process consecutive messages as a multi-task |
| `-ud <name[,name]\|all>` | Select configured Telegram dump destinations |

Examples:

```text
/mirror magnet:?xt=urn:btih:... -s -z
/leech https://example.com/folder -zim
/leech gdrive-folder-link -mv
/mirror link -up remote:path -n CustomName
```

---

## 🧭 Deployment Notes

- 🌐 Port `880` serves the FastAPI web UI, file selector, and qBittorrent proxy.
- 🔗 Port `8880` is used for `rclone serve` when configured.
- 🛡️ `docker-compose.yml` includes an optional Gluetun VPN scaffold for torrent traffic.
- 🍃 MongoDB is required for persistent settings, user data, and task metadata.
- 🧾 `sample_config.py` is the source of truth for advanced configuration.
- ⚙️ `update.py` supports a small environment-variable override allow-list for container deployments.

---

## 🆚 What NEO-WZML Adds

NEO-WZML is based on WZML-X and focuses on deployment reliability, modern selection flows, and practical operator controls.

| Area | NEO-WZML |
|------|----------|
| TeraBox | Native TeraBoxSDK integration — download, upload, account browsing, web file selector |
| Mega | Native MegaSDK 8.1.1 and web folder selection |
| Selection | Torrent, Mega, rclone, and TeraBox selection through the built-in web UI |
| Limits | Universal task locks, per-user ceilings, queues, and cooldowns |
| Media | Auto thumbnails, metadata, screenshots, sample videos, merge video, custom FFmpeg |
| Archives | Extract, ZIP, password ZIP, image-only ZIP |
| Uploads | Telegram, Drive, TeraBox, rclone, and multi-DDL host uploads |
| UX | Instant "Processing..." ack, save buttons, dump selection, filename formatting, ownership guards |
| Deployment | Docker bridge networking with optional VPN routing scaffold |

Removed from this fork: NZB/SABnzbd, YouTube upload, IMDB, and broadcast modules.

---

## 🔍 Troubleshooting

### 💥 Bot exits at startup

- Confirm `BOT_TOKEN`, `OWNER_ID`, `TELEGRAM_API`, `TELEGRAM_HASH`, and `DATABASE_URL`.
- Check `log.txt` for the first stack trace.
- If using Mongo Atlas, allow-list the server or container egress IP.

### 🌐 File selector does not open

- Set `BASE_URL` to a public URL reachable from your browser.
- Publish port `880` or put it behind a reverse proxy.
- If pincode validation is confusing during setup, temporarily disable `WEB_PINCODE`.

### ☁️ Google Drive tasks fail

- Confirm `token.pickle` or service-account JSONs are valid.
- Make sure the upload destination exists and the auth principal has access.
- Drive API quota errors are usually temporary; retry after the quota window resets.

### 🧲 Torrents stall

- Check tracker reachability from inside the container.
- Use the qBittorrent web proxy on port `880` to inspect live state.
- Configure Gluetun if your host or ISP blocks torrent traffic.

---

## 🤝 Support

- 📢 Telegram channel: [Chiheisen](https://t.me/Chiheisen)
- 💬 Support group: [ChiheisenUnion](https://t.me/ChiheisenUnion)
- 🐞 Bugs and feature requests: [GitHub Issues](https://github.com/irisXDR/NEO-WZML/issues)

If you report a bug, include:

- The command you ran.
- The relevant log lines.
- Whether the task was mirror, leech, clone, torrent, Mega, or GDrive.
- Your deployment method.

---

## 💰 Sponsors and Donations

If NEO-WZML saves you time, consider supporting development:

[🧸 Support the project — アイリス](https://telegram.me/irisXDR)

---

## 🙏 Credits

| Role | Person |
|------|--------|
| Owner | [irisXDR](https://github.com/irisXDR) |
| WZML-X developers | [SilentDemonSD](https://github.com/SilentDemonSD), [rjriajul](https://github.com/rjriajul), [CodeWithWeeb](https://github.com/weebzone), [Maverick](https://github.com/MajnuRangeela) |
| Original project | [anasty17](https://github.com/anasty17) |
| PyroBlack developers | [eyMarv](https://github.com/eyMarv), [Delivrance](https://github.com/delivrance) |

> Some AI Tools have been used to create certain portions of this repository.
---

## 📄 License

[GNU Affero General Public License v3.0](LICENSE)

<div align="center">
  <em>Made with ❤️ by <a href="https://telegram.me/irisXDR">irisXDR</a></em><br/>
  <em>Based on <a href="https://github.com/SilentDemonSD/WZML-X">WZML-X</a> · Powered by <a href="https://telegram.me/Chiheisen">Chiheisen</a></em>
</div>
