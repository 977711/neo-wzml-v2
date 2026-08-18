# This file is a part of NEO-WZML (github.com/irisXDR/NEO-WZML)

from ast import literal_eval
from asyncio import sleep
from functools import partial
from html import escape
from io import BytesIO
from os import getcwd
from re import sub
from time import time

from aiofiles.os import makedirs, remove
from aiofiles.os import path as aiopath
from pyrogram.filters import create
from pyrogram.handlers import MessageHandler

from bot.helper.ext_utils.status_utils import get_readable_file_size
from bot.helper.themes import BotTheme

from bot import auth_chats, excluded_extensions, sudo_users, user_data
from bot.core.config_manager import Config
from bot.core.tg_client import TgClient
from bot.helper.ext_utils.bot_utils import (
    get_size_bytes,
    new_task,
    update_user_ldata,
)
from bot.helper.ext_utils.db_handler import database
from bot.helper.ext_utils.media_utils import create_thumb
from bot.helper.telegram_helper.button_build import ButtonMaker
from bot.helper.telegram_helper.message_utils import (
    delete_message,
    edit_message,
    send_file,
    send_message,
)
from bot.helper.telegram_helper.tg_utils import chat_info

handler_dict = {}

leech_options = [
    "THUMBNAIL",
    "AUTO_THUMBNAIL",
    "LEECH_DUMP_CHAT",
    "LDUMP",
    "LEECH_PREFIX",
    "LEECH_SUFFIX",
    "LEECH_NAME_SWAP",
    "LEECH_CAPTION_STYLE",
    "LEECH_CAPTION",
    "THUMBNAIL_LAYOUT",
]
uphoster_options = [
    "GOFILE_TOKEN",
    "GOFILE_FOLDER_ID",
    "BUZZHEAVIER_TOKEN",
    "BUZZHEAVIER_FOLDER_ID",
    "PIXELDRAIN_KEY",
]
rclone_options = ["RCLONE_CONFIG", "RCLONE_PATH", "RCLONE_FLAGS"]
gdrive_options = ["TOKEN_PICKLE", "GDRIVE_ID", "INDEX_URL", "USER_TDS"]
ffset_options = [
    "FFMPEG_CMDS",
    "METADATA",
    "AUDIO_METADATA",
    "VIDEO_METADATA",
    "SUBTITLE_METADATA",
    "MERGE_VIDEO",
]
advanced_options = [
    "EXCLUDED_EXTENSIONS",

    "YT_DLP_OPTIONS",
    "UPLOAD_PATHS",
    "USER_COOKIE_FILE",
]

mirror_options = [
    "MIRROR_PREFIX",
    "MIRROR_SUFFIX",
    "MIRROR_NAME_SWAP",
]

fname_dict = {
    "LEECH_PREFIX": "Prefix",
    "LEECH_SUFFIX": "Suffix",
    "LEECH_NAME_SWAP": "Name Swap",
    "LEECH_CAPTION": "Caption",
    "LDUMP": "Dump",
    "THUMBNAIL": "Thumbnail",
    "AUTO_THUMBNAIL": "Auto Thumbnail",
    "THUMBNAIL_LAYOUT": "Thumbnail Layout",
    "LEECH_DUMP_CHAT": "Dump",
    "MIRROR_PREFIX": "Prefix",
    "MIRROR_SUFFIX": "Suffix",
    "MIRROR_NAME_SWAP": "Name Swap",
    "RCLONE_CONFIG": "RClone",
    "RCLONE_PATH": "Rclone Path",
    "RCLONE_FLAGS": "Rclone Flags",
    "TOKEN_PICKLE": "Token Pickle",
    "GDRIVE_ID": "GDrive ID",
    "INDEX_URL": "Index URL",
    "USER_TDS": "User TDs",
    "FFMPEG_CMDS": "FFmpeg Commands",
    "METADATA": "Metadata",
    "AUDIO_METADATA": "Audio Metadata",
    "VIDEO_METADATA": "Video Metadata",
    "SUBTITLE_METADATA": "Subtitle Metadata",
    "MERGE_VIDEO": "Merge Video",
    "YT_DLP_OPTIONS": "YT-DLP Options",
    "USER_COOKIE_FILE": "Cookie File",
    "EXCLUDED_EXTENSIONS": "Excluded Extensions",
    "UPLOAD_PATHS": "Upload Paths",
    "GOFILE_TOKEN": "GoFile Token",
    "GOFILE_FOLDER_ID": "GoFile Folder",
    "BUZZHEAVIER_TOKEN": "BuzzHeavier Token",
    "BUZZHEAVIER_FOLDER_ID": "BuzzHeavier Folder",
    "PIXELDRAIN_KEY": "PixelDrain Key",
}

user_settings_text = {
    "THUMBNAIL": (
        "Photo or Doc",
        "Custom Thumbnail is used as the thumbnail for the files you upload to telegram in media or document mode.",
        "<i>Send a photo to save it as custom thumbnail.</i> \n • <b>Time Left:</b> <code>60 sec</code>",
    ),
    "RCLONE_CONFIG": (
        "File",
        "RClone is a command-line program to sync files and directories to and from different cloud storage providers like GDrive, OneDrive, etc.",
        "<i>Send your <code>rclone.conf</code> file to use as your Upload Dest to RClone.</i> \n • <b>Time Left:</b> <code>60 sec</code>",
    ),
    "TOKEN_PICKLE": (
        "File",
        "Your personal Google Drive authentication token. Allows uploading to your own GDrive without using bot owner's credentials.",
        """<i>Send your <code>token.pickle</code> file to upload to your personal Google Drive.</i>

<b>How to generate:</b>
1. Use the <code>generate_token.py</code> script
2. Follow Google OAuth authentication flow
3. Save the generated token.pickle file

 • <b>Time Left:</b> <code>60 sec</code>""",
    ),
    "TERABOX_COOKIE": (
        "File",
        "Your TeraBox session cookie. Lets you upload finished tasks to your own TeraBox account with `-up tbx`.",
        """<i>Send your <code>terabox.txt</code> cookie export (a browser \u201cGet cookies.txt\u201d export of your logged-in TeraBox session) to upload to your personal TeraBox account.</i>

Then mirror with <code>-up tbx</code>, or set TeraBox as your default upload.

 • <b>Time Left:</b> <code>60 sec</code>""",
    ),
    "LEECH_DUMP_CHAT": (
        "Chat ID/Username",
        "Set where your leeched files will be uploaded. Can be a group, channel, or PM.",
        """<i>Send the destination for your leeched files.</i>

<b>Formats:</b>
• <code>-1001234567890</code> - Chat/Channel ID (use -100 prefix)
• <code>@channelname</code> - Public channel username
• <code>pm</code> - Send to your private messages

<b>Prefixes for upload method:</b>
• <code>b:</code> - Force upload via bot session
• <code>u:</code> - Force upload via user session  
• <code>h:</code> - Hybrid (auto-select based on file size)

<b>For topics:</b> <code>chat_id|topic_id</code>

<b>Examples:</b>
<code>-1001234567890</code>
<code>b:@mychannel</code>
<code>h:-1001234567890|5</code>

 • <b>Time Left:</b> <code>60 sec</code>""",
    ),
    "LDUMP": (
        "Dictionary",
        "Multiple custom leech dump destinations. Upload to multiple chats at once.",
        """Send leech destinations in format: <code>title chat_id/@username</code> (one per line).
Examples:
<code>Main Dump -1001234567890</code>
<code>Backup @mychannel</code>
<code>Personal pm</code>

 • <b>Time Left:</b> <code>60 sec</code>""",
    ),
    "LEECH_PREFIX": (
        "Text",
        "Leech Filename Prefix is the Front Part attached with the Filename of the Leech Files.",
        "Send Leech Filename Prefix. You can add HTML tags. Example: <code>@mychannel</code>.</i> \n • <b>Time Left:</b> <code>60 sec</code>",
    ),
    "LEECH_SUFFIX": (
        "Text",
        "Leech Filename Suffix is the End Part attached with the Filename of the Leech Files.",
        "Send Leech Filename Suffix. You can add HTML tags. Example: <code>@mychannel</code>.</i> \n • <b>Time Left:</b> <code>60 sec</code>",
    ),
    "LEECH_NAME_SWAP": (
        "Text",
        "Leech Filename Name Swap uses regex patterns to remove/manipulate parts of filenames.",
        """<i>Send Leech Filename Name Swap patterns.</i>
<b>Format:</b> pattern:replacement|pattern2:replacement2
<b>Example:</b> <code>\\[.*?\\]::|www\\.\\S+::</code> (remove brackets and URLs)
 • <b>Time Left:</b> <code>60 sec</code>""",
    ),
    "LEECH_CAPTION_STYLE": (
        "Selection",
        "Automatically wrap your leech captions with style tags. Skipped if your caption contains HTML tags.",
        "<i>Select your preferred caption style from the options below.</i>",
    ),
    "LEECH_CAPTION": (
        "Text",
        "Leech Caption is the Custom Caption on the Leech Files Uploaded by the bot.",
        "Send Leech Caption. You can add HTML tags. Example: <code>@mychannel</code>.</i> \n • <b>Time Left:</b> <code>60 sec</code>",
    ),
    "THUMBNAIL_LAYOUT": (
        "Text",
        "Create grid thumbnails from multiple video frames. Format: widthxheight (e.g., 3x3 for 9 frames). Only works for videos without custom thumbnails.",
        "Send Thumbnail Layout. Format: <code>widthxheight</code> (e.g., <code>3x3</code> for 3×3 grid with 9 frames). Examples: <code>2x2</code> (4 frames), <code>3x3</code> (9 frames), <code>4x3</code> (12 frames).</i> \n • <b>Time Left:</b> <code>60 sec</code>",
    ),
    "RCLONE_PATH": (
        "Text",
        "Default rclone path to which you want to upload all the mirrors using rclone.",
        "Send Rclone Path. If you want to use your rclone config edit using owner/user config from usetting or add mrcc: before rclone path. Example mrcc:remote:folder. </i> \n • <b>Time Left:</b> <code>60 sec</code>",
    ),
    "RCLONE_FLAGS": (
        "Text",
        "Additional rclone flags for transfer operations. Format: key:value|key|key:value",
        "key:value|key|key|key:value . Check here all <a href='https://rclone.org/flags/'>RcloneFlags</a>\nEx: --buffer-size:8M|--drive-starred-only",
    ),
    "GDRIVE_ID": (
        "Text",
        "This is the Folder/TeamDrive ID of the Google Drive OR root to which you want to upload all the mirrors.",
        "Send Gdrive ID. If you want to use your token.pickle edit using owner/user token from usetting or add mtp: before the id. Example: mtp:F435RGGRDXXXXXX . </i> \n • <b>Time Left:</b> <code>60 sec</code>",
    ),
    "MIRROR_PREFIX": (
        "Text",
        "Mirror Filename Prefix is the Front Part attached with the Filename of the Mirrored Files.",
        "Send Mirror Filename Prefix. You can add HTML tags. Example: <code>@mychannel</code>.</i> \n • <b>Time Left:</b> <code>60 sec</code>",
    ),
    "MIRROR_SUFFIX": (
        "Text",
        "Mirror Filename Suffix is the End Part attached with the Filename of the Mirrored Files.",
        "Send Mirror Filename Suffix. You can add HTML tags. Example: <code>@mychannel</code>.</i> \n • <b>Time Left:</b> <code>60 sec</code>",
    ),
    "MIRROR_NAME_SWAP": (
        "Text",
        "Mirror Filename Name Swap uses regex patterns to remove/manipulate parts of filenames.",
        """<i>Send Mirror Filename Name Swap patterns.</i>
<b>Format:</b> pattern:replacement|pattern2:replacement2
<b>Example:</b> <code>\\[.*?\\]::|www\\.\\S+::</code> (remove brackets and URLs)
 • <b>Time Left:</b> <code>60 sec</code>""",
    ),
    "INDEX_URL": (
        "URL",
        "Index URL for browsing your Google Drive files. Refer to https://gitlab.com/ParveenBhadooOfficial/Google-Drive-Index.",
        "Send Index URL for your gdrive option. </i> \n • <b>Time Left:</b> <code>60 sec</code>",
    ),
    "USER_TDS": (
        "Dictionary",
        "User TD Mode allows you to upload to your own Team Drives using the bot's global Service Account.",
        """Send User TD details in format: <code>name drive_id index_link(optional)</code> (one per line).
Examples:
<code>MainTD 0APxxxxxxxxxxxx1xx</code>
<code>BackupTD 0APxxxxxxxxxxxx2xx https://example.com</code>

<b>Note:</b>
• Drive ID must be valid (will be verified)
• Names can have spaces
• To delete specific TD, send just the name(s)

<i>SA Email:</i> <code>{}</code>
 • <b>Time Left:</b> <code>60 sec</code>""".format(Config.USER_TD_SA if hasattr(Config, 'USER_TD_SA') and Config.USER_TD_SA else "Not configured by owner"),
    ),
    "UPLOAD_PATHS": (
        "Dictionary",
        "Create shortcuts for frequently used upload destinations. Quickly select from saved paths instead of typing full paths each time.",
        """<i>Send a dictionary of named upload paths.</i>

<b>Format:</b> <code>{"name": "path", "name2": "path2"}</code>

<b>Supported path types:</b>
• Rclone: <code>remote:folder/subfolder</code>
• GDrive ID: <code>0APxxxxxxxxxx</code>
• Telegram: <code>-1001234567890</code> or <code>@channel</code>
• User config: <code>mrcc:remote:</code> or <code>mtp:gdrive_id</code>

<b>Example:</b>
<code>{"Movies": "gdrive:Movies/2024", "Backup": "0APxxxxx", "Channel": "@mychannel"}</code>

 • <b>Time Left:</b> <code>60 sec</code>""",
    ),
    "EXCLUDED_EXTENSIONS": (
        "Text",
        "File extensions that won't upload/clone. Separate them by space.",
        "Send exluded extenions seperated by space without dot at beginning. </i> \n • <b>Time Left:</b> <code>60 sec</code>",
    ),

    "YT_DLP_OPTIONS": (
        "Text",
        "YT-DLP Options is the Custom Quality for the extraction of videos from the yt-dlp supported sites.",
        """Format: {key: value, key: value, key: value}.
Example: {"format": "bv*+mergeall[vcodec=none]", "nocheckcertificate": True, "playliststart": 10, "fragment_retries": float("inf"), "matchtitle": "S13", "writesubtitles": True, "live_from_start": True, "postprocessor_args": {"ffmpeg": ["-threads", "4"]}, "wait_for_video": (5, 100), "download_ranges": [{"start_time": 0, "end_time": 10}]}
Check all yt-dlp api options from this <a href='https://github.com/yt-dlp/yt-dlp/blob/master/yt_dlp/YoutubeDL.py#L184'>FILE</a> or use this <a href='https://t.me/mltb_official_channel/177'>script</a> to convert cli arguments to api options.

<i>Send dict of YT-DLP Options according to format.</i> \n • <b>Time Left:</b> <code>60 sec</code>""",
    ),
    "FFMPEG_CMDS": (
        "Text",
        "FFmpeg commands for media processing and conversion.",
        """Dict of list values of ffmpeg commands. You can set multiple ffmpeg commands for all files before upload. Don't write ffmpeg at beginning, start directly with the arguments.
Examples: {"subtitle": ["-i mltb.mkv -c copy -c:s srt mltb.mkv", "-i mltb.video -c copy -c:s srt mltb"], "convert": ["-i mltb.m4a -c:a libmp3lame -q:a 2 mltb.mp3", "-i mltb.audio -c:a libmp3lame -q:a 2 mltb.mp3"], extract: ["-i mltb -map 0:a -c copy mltb.mka -map 0:s -c copy mltb.srt"]}
Notes:
- Add `-del` to the list which you want from the bot to delete the original files after command run complete!
- To execute one of those lists in bot for example, you must use -ff subtitle (list key) or -ff convert (list key)
Here I will explain how to use mltb.* which is reference to files you want to work on.
1. First cmd: the input is mltb.mkv so this cmd will work only on mkv videos and the output is mltb.mkv also so all outputs is mkv. -del will delete the original media after complete run of the cmd.
2. Second cmd: the input is mltb.video so this cmd will work on all videos and the output is only mltb so the extenstion is same as input files.
3. Third cmd: the input in mltb.m4a so this cmd will work only on m4a audios and the output is mltb.mp3 so the output extension is mp3.
4. Fourth cmd: the input is mltb.audio so this cmd will work on all audios and the output is mltb.mp3 so the output extension is mp3.

<i>Send dict of FFMPEG_CMDS Options according to format.</i> \n • <b>Time Left:</b> <code>60 sec</code>
""",
    ),
    "METADATA_CMDS": (
        "Key=Value Format",
        "Legacy metadata format. Use the new METADATA, AUDIO_METADATA, VIDEO_METADATA, or SUBTITLE_METADATA options instead for better control.",
        """<i>This setting is deprecated. Please use the newer metadata options:</i>

• <b>METADATA</b> - Global file metadata
• <b>AUDIO_METADATA</b> - Audio track metadata
• <b>VIDEO_METADATA</b> - Video track metadata
• <b>SUBTITLE_METADATA</b> - Subtitle track metadata

<b>Legacy format (still works):</b>
<code>title="My Title"</code>

 • <b>Time Left:</b> <code>60 sec</code>
""",
    ),
    "METADATA": (
        "Global Metadata (key=value|key=value)",
        "Apply metadata to all media files with dynamic variables.",
        """<i>Send metadata as</i> <code>key=value|key2=value2</code>

<b>Dynamic Variables:</b>
• <code>{filename}</code> - Original filename
• <code>{basename}</code> - Name without extension
• <code>{audiolang}</code> - Audio language (English/Hindi etc.)
• <code>{year}</code> - Year from filename

<b>Example:</b>
<code>title={basename}|artist={audiolang} Version|year={year}</code>

⏱ <b>Time Left:</b> <code>60 sec</code>""",
    ),
    "AUDIO_METADATA": (
        "Audio Stream Metadata",
        "Metadata applied to each audio track separately.",
        """<i>Audio stream metadata with per-track language support</i>

<b>Example:</b>
<code>language={audiolang}|title=Audio - {audiolang}</code>

⏱ <b>Time Left:</b> <code>60 sec</code>""",
    ),
    "VIDEO_METADATA": (
        "Video Stream Metadata",
        "Metadata applied to video streams.",
        """<i>Video stream metadata for visual tracks</i>

<b>Example:</b>
<code>title={basename}|comment=HD Video</code>

⏱ <b>Time Left:</b> <code>60 sec</code>""",
    ),
    "SUBTITLE_METADATA": (
        "Subtitle Stream Metadata",
        "Metadata applied to each subtitle track separately.",
        """<i>Subtitle stream metadata with per-track language support</i>

<b>Example:</b>
<code>language={sublang}|title=Subtitles - {sublang}</code>

⏱ <b>Time Left:</b> <code>60 sec</code>""",
    ),
    "USER_COOKIE_FILE": (
        "File",
        "User's YT-DLP Cookie File to authenticate access to websites and youtube.",
        "<i>Send your cookie file (e.g., cookies.txt or abc.txt).</i> \n • <b>Time Left:</b> <code>60 sec</code>",
    ),
    "GOFILE_TOKEN": (
        "String",
        "Gofile API Token for account authentication. Allows uploads to your account.",
        """<i>Send your Gofile API Token.</i>
<b>Get it from:</b> https://gofile.io/myProfile
 • <b>Time Left:</b> <code>60 sec</code>""",
    ),
    "GOFILE_FOLDER_ID": (
        "String",
        "Gofile Folder ID for upload destination. Files will be uploaded to this specific folder.",
        "<i>Send your Gofile Folder ID. If empty, uploads to Root folder.</i> \n • <b>Time Left:</b> <code>60 sec</code>",
    ),
    "BUZZHEAVIER_TOKEN": (
        "String",
        "BuzzHeavier API Token (Account ID). Required for uploading to your account.",
        """<i>Send your BuzzHeavier API Token (Account ID).</i>
<b>Get it from:</b> https://buzzheavier.com/account (Account ID at top)
 • <b>Time Left:</b> <code>60 sec</code>""",
    ),
    "BUZZHEAVIER_FOLDER_ID": (
        "String",
        "BuzzHeavier Folder ID for upload destination.",
        "<i>Send your BuzzHeavier Folder ID. Files will be organized in this folder.</i> \n • <b>Time Left:</b> <code>60 sec</code>",
    ),
    "PIXELDRAIN_KEY": (
        "String",
        "PixelDrain API Key for account association.",
        """<i>Send your PixelDrain API Key.</i>
<b>Get it from:</b> https://pixeldrain.com/user/api_keys
 • <b>Time Left:</b> <code>60 sec</code>""",
    ),
    "DEFAULT_UPLOAD": (
        "Selection",
        "Set your default upload destination. Overrides bot's default setting.",
        """<i>Select your preferred upload destination from the options below:</i>

<b>Options:</b>
• <b>gd</b> - Google Drive
• <b>rc</b> - Rclone
• <b>ddl</b> - Direct DDL Hosters

<b>Note:</b> This can be overridden per-task using the -up flag.""",
    ),
    "USER_TOKENS": (
        "Toggle",
        "Allow using your own Telegram account for uploads (User Session). Requires USER_SESSION_STRING from bot owner.",
        """<i>Enable to use your Telegram User Session for uploads.</i>

<b>When enabled:</b>
• Uploads using your Telegram account
• Can upload files larger than 2GB
• Access to private channels

<b>Requirements:</b>
• Bot owner must set up USER_SESSION_STRING
• You must be logged in as a user on Telegram

<b>Toggle:</b> <code>t</code> (enable) / <code>f</code> (disable)""",
    ),
    "USE_DEFAULT_COOKIE": (
        "Toggle",
        "Choose between using owner's cookie or your own cookie for authenticated downloads.",
        """<i>Select whose cookie to use for downloads requiring authentication.</i>

<b>Options:</b>
• <b>Owner's Cookie</b> (f) - Use bot owner's stored cookies
• <b>Your Cookie</b> (t) - Use your personal cookies (must be saved in USER_COOKIE_FILE)

<b>Toggle:</b> <code>t</code> (your cookie) / <code>f</code> (owner's cookie)

<b>Use case:</b> Useful when owner has premium cookies and users want to benefit from them.""",
    ),
    "DUMP_MODE": (
        "Toggle",
        "Temporarily disable LDUMP (multiple leech destinations) without removing your configuration.",
        """<i>Control whether to use multiple leech destinations or single destination.</i>

<b>When enabled (t):</b>
• Uses all configured LDUMP destinations
• Uploads to multiple chats at once

<b>When disabled (f):</b>
• Uses only LEECH_DUMP_CHAT destination
• Single upload destination

<b>Toggle:</b> <code>t</code> (enable LDUMP) / <code>f</code> (disable LDUMP)

<b>Note:</b> This is a temporary override. Your LDUMP configuration is preserved.""",
    ),
    "AS_DOCUMENT": (
        "Toggle",
        "Upload files as Telegram documents instead of streamable media. Preserves original filename.",
        """<i>Choose file upload type for Telegram leech.</i>

<b>Document (f):</b>
• Files uploaded as documents
• Original filename preserved
• No streaming in Telegram
• Works for files up to 2GB

<b>Media (t):</b>
• Files uploaded as streamable video/audio
• Can be streamed in Telegram
• Filename may change
• Better for large video files

<b>Toggle:</b> <code>t</code> (media) / <code>f</code> (document)""",
    ),
    "EQUAL_SPLITS": (
        "Toggle",
        "Split files into equal-sized parts instead of max-size parts. Better for bandwidth management.",
        """<i>Choose how files are split during leech.</i>

<b>Equal Splits (t):</b>
• Files split into equal-sized parts
• Each part has same size (except last)
• Better for consistent bandwidth

<b>Max Size (f):</b>
• Files split at maximum size limit (LEECH_SPLIT_SIZE)
• May have different sized parts
• More efficient for small files

<b>Toggle:</b> <code>t</code> (equal) / <code>f</code> (max size)

<b>Example:</b> 100MB file with limit 50MB
• Equal: 50MB + 50MB
• Max: 50MB + 50MB (same in this case, differs for other sizes)""",
    ),
    "STOP_DUPLICATE": (
        "Toggle",
        "Check for duplicate files/folders in Google Drive before uploading. Prevents wasting storage space.",
        """<i>Prevent duplicate uploads to your Google Drive.</i>

<b>When enabled (t):</b>
• Bot checks if file/folder already exists
• Compares name and size
• Skips upload if duplicate found
• Saves storage space and time

<b>When disabled (f):</b>
• No duplicate check performed
• Files always uploaded
• May create duplicates

<b>Note:</b> Only applies to Google Drive uploads.

<b>Toggle:</b> <code>t</code> (enable) / <code>f</code> (disable)""",
    ),
    "TD_MODE": (
        "Toggle",
        "Enable Team Drive mode for uploading to your personal Team Drives using bot's Service Account.",
        """<i>Upload to your personal Team Drives using bot's global Service Account.</i>

<b>Requirements:</b>
• Bot owner must enable USER_TD_MODE
• You must configure your Team Drives in USER_TDS

<b>When enabled (t):</b>
• Upload uses your Team Drive IDs from USER_TDS
• Uses bot's Service Account for uploads

<b>When disabled (f):</b>
• Uses bot's GDRIVE_ID or your personal token.pickle

<b>Note:</b> Only works if bot owner has enabled User TD Mode globally.

<b>Toggle:</b> <code>t</code> (enable) / <code>f</code> (disable)""",
    ),
    "UPHOSTER_SERVICE": (
        "Multi-Select",
        "Select which DDL hoster services to use for multi-upload. Can select multiple services.",
        """<i>Select which uphoster services to use when uploading to DDL hosters.</i>

<b>Available Services:</b>
• <b>gofile</b> - Gofile.com
• <b>buzzheavier</b> - BuzzHeavier.com
• <b>pixeldrain</b> - PixelDrain.com

<b>Format:</b> Comma-separated service names
<b>Examples:</b>
• <code>gofile</code> - Only Gofile
• <code>gofile,buzzheavier</code> - Both Gofile and BuzzHeavier
• <code>gofile,buzzheavier,pixeldrain</code> - All three

<b>Instructions:</b>
1. Send service names separated by comma
2. You can select 1, 2, or all 3 services
3. Configure API tokens/folders for each service in their respective settings

<b>Note:</b> Multi-upload splits files across all selected services.""",
    ),
}


async def get_user_settings(from_user, stype="main"):
    user_id = from_user.id
    user_name = from_user.mention(style="html")
    buttons = ButtonMaker()
    rclone_conf = f"rclone/{user_id}.conf"
    token_pickle = f"tokens/{user_id}.pickle"
    user_dict = user_data.get(user_id, {})

    if stype == "main":
        buttons.data_button(
            "General Settings", f"userset {user_id} general", position="header"
        )
        buttons.data_button("Mirror Settings", f"userset {user_id} mirror")
        buttons.data_button("Leech Settings", f"userset {user_id} leech")
        buttons.data_button("Uphoster Settings", f"userset {user_id} uphoster")
        buttons.data_button("FF Media Settings", f"userset {user_id} ffset")
        buttons.data_button(
            "Misc Settings", f"userset {user_id} advanced", position="l_body"
        )

        if user_dict and any(
            key in user_dict
            for key in list(user_settings_text.keys())
            + [
                "USER_TOKENS",
                "AS_DOCUMENT",
                "EQUAL_SPLITS",
                "STOP_DUPLICATE",
                "DEFAULT_UPLOAD",
            ]
        ):
            buttons.data_button(
                "Reset All", f"userset {user_id} confirm_reset_all", position="footer"
            )
        buttons.data_button("Close", f"userset {user_id} close", position="footer")

        text = BotTheme(
            "USER_SETTING",
            NAME=user_name,
            ID=user_id,
            USERNAME=f"@{from_user.username}" if from_user.username else "N/A",
            DC=from_user.dc_id,
        )

        btns = buttons.build_menu(2)

    elif stype == "general":
        if user_dict.get("DEFAULT_UPLOAD", ""):
            default_upload = user_dict["DEFAULT_UPLOAD"]
        elif "DEFAULT_UPLOAD" not in user_dict:
            default_upload = Config.DEFAULT_UPLOAD
        _du_names = {"gd": "GDRIVE API", "rc": "RCLONE", "tbx": "TERABOX"}
        # Cycle: rc -> gd -> tbx -> rc
        _du_next = {"rc": "gd", "gd": "tbx", "tbx": "rc"}
        cur_du = default_upload if default_upload in _du_names else "rc"
        du = _du_names[cur_du]
        dur = _du_names[_du_next[cur_du]]
        buttons.data_button(
            f"Swap to {dur} Mode", f"userset {user_id} {cur_du}"
        )

        user_tokens = user_dict.get("USER_TOKENS", False)
        tr = "USER" if user_tokens else "OWNER"
        trr = "OWNER" if user_tokens else "USER"
        buttons.data_button(
            f"Swap to {trr} token/config",
            f"userset {user_id} tog USER_TOKENS {'f' if user_tokens else 't'}",
        )

        buttons.data_button("Back", f"userset {user_id} back", "footer")
        buttons.data_button("Close", f"userset {user_id} close", "footer")

        def_cookies = user_dict.get("USE_DEFAULT_COOKIE", False)
        cookie_mode = "Owner's Cookie" if def_cookies else "User's Cookie"
        buttons.data_button(
            f"Swap to {'OWNER' if not def_cookies else 'USER'}'s Cookie File",
            f"userset {user_id} tog USE_DEFAULT_COOKIE {'f' if def_cookies else 't'}",
        )
        btns = buttons.build_menu(1)

        text = f""" • <b>Name:</b> {user_name}
 • <b>Default Upload Package:</b> <b>{du}</b>
 • <b>Default Usage Mode:</b> <b>{tr}'s</b> token/config
 • <b>yt Cookies Mode:</b> <b>{cookie_mode}</b>
"""

    elif stype == "leech":
        buttons.data_button("Leech Prefix", f"userset {user_id} menu LEECH_PREFIX")
        if user_dict.get("LEECH_PREFIX", False):
            lprefix = user_dict["LEECH_PREFIX"]
        elif "LEECH_PREFIX" not in user_dict and Config.LEECH_PREFIX:
            lprefix = Config.LEECH_PREFIX
        else:
            lprefix = "Not Exists"

        buttons.data_button("Leech Suffix", f"userset {user_id} menu LEECH_SUFFIX")
        if user_dict.get("LEECH_SUFFIX", False):
            lsuffix = user_dict["LEECH_SUFFIX"]
        elif "LEECH_SUFFIX" not in user_dict and Config.LEECH_SUFFIX:
            lsuffix = Config.LEECH_SUFFIX
        else:
            lsuffix = "Not Exists"

        buttons.data_button("Leech Name Swap", f"userset {user_id} menu LEECH_NAME_SWAP")
        if user_dict.get("LEECH_NAME_SWAP", False):
            lremname = user_dict["LEECH_NAME_SWAP"]
        elif "LEECH_NAME_SWAP" not in user_dict and hasattr(Config, "LEECH_NAME_SWAP"):
            lremname = Config.LEECH_NAME_SWAP
        else:
            lremname = "Not Exists"

        buttons.data_button("Leech Caption Style", f"userset {user_id} caption_style")
        caption_style = user_dict.get("LEECH_CAPTION_STYLE", "")
        if caption_style:
            from bot.helper.ext_utils.bot_utils import CAPTION_STYLE_NAMES
            style_name = CAPTION_STYLE_NAMES.get(caption_style, caption_style.capitalize())
        else:
            style_name = "Not set"

        buttons.data_button("Leech Caption", f"userset {user_id} menu LEECH_CAPTION")
        if user_dict.get("LEECH_CAPTION", False):
            caption_data = user_dict["LEECH_CAPTION"]
            if isinstance(caption_data, dict):
                lcap = caption_data.get("text", "Not Exists")
            else:
                lcap = caption_data
        elif "LEECH_CAPTION" not in user_dict and Config.LEECH_CAPTION:
            lcap = Config.LEECH_CAPTION
        else:
            lcap = "Not Exists"

        buttons.data_button("Leech Dump", f"userset {user_id} ldump_list")
        ldumps = user_dict.get("LDUMP", {})
        ldump_count = len(ldumps)
        if ldump_count > 0:
            ldump_msg = f"{ldump_count} set"
        else:
            ldump_msg = "Not set"

        if ldump_count > 0:
            dump_mode = user_dict.get("DUMP_MODE", True)
            if dump_mode:
                buttons.data_button("Disable Dump Mode", f"userset {user_id} tog DUMP_MODE f")
                dump_mode_msg = "Enabled"
            else:
                buttons.data_button("Enable Dump Mode", f"userset {user_id} tog DUMP_MODE t")
                dump_mode_msg = "Disabled"
        else:
            dump_mode_msg = "N/A (No LDUMP set)"

        thumbpath = f"thumbnails/{user_id}.jpg"
        buttons.data_button("Thumbnail", f"userset {user_id} menu THUMBNAIL")
        thumbmsg = "Exists" if await aiopath.exists(thumbpath) else "Not Exists"

        buttons.data_button(
            "Thumbnail Layout", f"userset {user_id} menu THUMBNAIL_LAYOUT"
        )
        if user_dict.get("THUMBNAIL_LAYOUT", False):
            thumb_layout = user_dict["THUMBNAIL_LAYOUT"]
        elif "THUMBNAIL_LAYOUT" not in user_dict and Config.THUMBNAIL_LAYOUT:
            thumb_layout = Config.THUMBNAIL_LAYOUT
        else:
            thumb_layout = "None"

        if user_dict.get("AUTO_THUMBNAIL", False):
            buttons.data_button("Disable Auto Thumbnail", f"userset {user_id} tog AUTO_THUMBNAIL f")
            auto_thumb = "Enabled"
        else:
            buttons.data_button("Enable Auto Thumbnail", f"userset {user_id} tog AUTO_THUMBNAIL t")
            auto_thumb = "Disabled"

        if (
            user_dict.get("AS_DOCUMENT", False)
            or "AS_DOCUMENT" not in user_dict
            and Config.AS_DOCUMENT
        ):
            ltype = "DOCUMENT"
            buttons.data_button("Send As Media", f"userset {user_id} tog AS_DOCUMENT f")
        else:
            ltype = "MEDIA"
            buttons.data_button(
                "Send As Document", f"userset {user_id} tog AS_DOCUMENT t"
            )

        if (
            user_dict.get("EQUAL_SPLITS", False)
            or "EQUAL_SPLITS" not in user_dict
            and Config.EQUAL_SPLITS
        ):
            buttons.data_button(
                "Disable Equal Splits", f"userset {user_id} tog EQUAL_SPLITS f"
            )
            equal_splits = "Enabled"
        else:
            buttons.data_button(
                "Enable Equal Splits", f"userset {user_id} tog EQUAL_SPLITS t"
            )
            equal_splits = "Disabled"

        buttons.data_button("Back", f"userset {user_id} back", "footer")
        buttons.data_button("Close", f"userset {user_id} close", "footer")
        btns = buttons.build_menu(2)

        text = BotTheme(
            "LEECH",
            NAME=user_name,
            DL="∞",
            LTYPE=ltype,
            THUMB=thumbmsg,
            EQUAL_SPLIT=equal_splits,
            LCAPTION=escape(lcap),
            LPREFIX=escape(lprefix),
            LSUFFIX=escape(lsuffix),
            LCAPTIONSTYLE=escape(style_name),
            LDUMP=ldump_msg,
            LREMNAME=escape(lremname),
            LMETA="N/A",
        )

        text += f"\n • <b>Auto Thumbnail:</b> <b>{auto_thumb}</b>"
        text += f"\n • <b>Dump Mode:</b> <b>{dump_mode_msg}</b>"
        text += "\n\n<blockquote expandable><b>➜ Thumbnail Priority:</b>"
        text += "\n 1️⃣ Custom Thumbnail (highest)"
        text += "\n 2️⃣ Auto Thumbnail (TMDB)"
        text += "\n 3️⃣ Thumbnail Layout (grid from frames)"
        text += "\n 4️⃣ Random Video Thumbnail (fallback)"
        text += "\n\n<i>Note: Auto Thumbnail works for videos only.</i></blockquote>"

    elif stype == "caption_style":
        buttons = ButtonMaker()
        from bot.helper.ext_utils.bot_utils import CAPTION_STYLE_NAMES

        current_style = user_dict.get("LEECH_CAPTION_STYLE", "")
        current_name = CAPTION_STYLE_NAMES.get(current_style, current_style.capitalize()) if current_style else "None"

        text = f""" • <b>Name:</b> {user_name}
 • <b>Current Caption Style:</b> <b>{current_name}</b>

<i>Select a caption style from the options below.</i>"""

        for style_key, style_name in CAPTION_STYLE_NAMES.items():
            is_current = "✓" if style_key == current_style else ""
            buttons.data_button(
                f"{style_name} {is_current}",
                f"userset {user_id} set_style {style_key}"
            )

        if current_style:
            buttons.data_button("Disable Style", f"userset {user_id} set_style", position="header")

        buttons.data_button("Back", f"userset {user_id} back leech", "footer")
        buttons.data_button("Close", f"userset {user_id} close", "footer")
        btns = buttons.build_menu(2)

    elif stype == "uphoster":
        uphoster_service = user_dict.get("UPHOSTER_SERVICE", "gofile")
        buttons.data_button(
            "Change Destination ⇋",
            f"userset {user_id} uphoster_destinations",
        )
        buttons.data_button("Gofile Tools", f"userset {user_id} gofile")
        buttons.data_button("BuzzHeavier Tools", f"userset {user_id} buzzheavier")
        buttons.data_button("PixelDrain Tools", f"userset {user_id} pixeldrain")
        buttons.data_button("Back", f"userset {user_id} back", "footer")
        buttons.data_button("Close", f"userset {user_id} close", "footer")
        btns = buttons.build_menu(1)

        destinations = [s.capitalize() for s in uphoster_service.split(",")]
        text = f"""<b>✦ UPHOSTER SETTINGS</b>
<i>Configure your Direct Download Link (DDL) hosting services.</i>

 • <b>Current Destination:</b> {', '.join(destinations)}"""

    elif stype == "pixeldrain":
        buttons.data_button("PixelDrain Key", f"userset {user_id} menu PIXELDRAIN_KEY")
        buttons.data_button("Back", f"userset {user_id} back uphoster", "footer")
        buttons.data_button("Close", f"userset {user_id} close", "footer")
        btns = buttons.build_menu(1)

        if user_dict.get("PIXELDRAIN_KEY", False):
            pdtoken = user_dict["PIXELDRAIN_KEY"]
        elif Config.PIXELDRAIN_KEY:
            pdtoken = Config.PIXELDRAIN_KEY
        else:
            pdtoken = "None"

        text = f""" • <b>PixelDrain Key:</b> <code>{pdtoken}</code>"""

    elif stype == "buzzheavier":
        buttons.data_button(
            "BuzzHeavier Token", f"userset {user_id} menu BUZZHEAVIER_TOKEN"
        )
        buttons.data_button(
            "BuzzHeavier Folder ID", f"userset {user_id} menu BUZZHEAVIER_FOLDER_ID"
        )
        buttons.data_button("Back", f"userset {user_id} back uphoster", "footer")
        buttons.data_button("Close", f"userset {user_id} close", "footer")
        btns = buttons.build_menu(1)

        if user_dict.get("BUZZHEAVIER_TOKEN", False):
            bztoken = user_dict["BUZZHEAVIER_TOKEN"]
        elif Config.BUZZHEAVIER_API:
            bztoken = Config.BUZZHEAVIER_API
        else:
            bztoken = "None"

        if user_dict.get("BUZZHEAVIER_FOLDER_ID", False):
            bzfolder = user_dict["BUZZHEAVIER_FOLDER_ID"]
        else:
            bzfolder = "None"

        text = f""" • <b>BuzzHeavier Token:</b> <code>{bztoken}</code>
 • <b>BuzzHeavier Folder ID:</b> <code>{bzfolder}</code>"""

    elif stype == "gofile":
        buttons.data_button("Gofile Token", f"userset {user_id} menu GOFILE_TOKEN")
        buttons.data_button(
            "Gofile Folder ID", f"userset {user_id} menu GOFILE_FOLDER_ID"
        )
        buttons.data_button("Back", f"userset {user_id} back uphoster", "footer")
        buttons.data_button("Close", f"userset {user_id} close", "footer")
        btns = buttons.build_menu(1)

        if user_dict.get("GOFILE_TOKEN", False):
            gftoken = user_dict["GOFILE_TOKEN"]
        elif Config.GOFILE_API:
            gftoken = Config.GOFILE_API
        else:
            gftoken = "None"

        if user_dict.get("GOFILE_FOLDER_ID", False):
            gffolder = user_dict["GOFILE_FOLDER_ID"]
        elif Config.GOFILE_FOLDER_ID:
            gffolder = Config.GOFILE_FOLDER_ID
        else:
            gffolder = "None (Uploads to Root)"

        text = f""" • <b>Gofile Token:</b> <code>{gftoken}</code>
 • <b>Gofile Folder ID:</b> <code>{gffolder}</code>"""

    elif stype == "rclone":
        buttons.data_button("Rclone Config", f"userset {user_id} menu RCLONE_CONFIG")
        buttons.data_button(
            "Default Rclone Path", f"userset {user_id} menu RCLONE_PATH"
        )
        buttons.data_button("Rclone Flags", f"userset {user_id} menu RCLONE_FLAGS")

        buttons.data_button("Back", f"userset {user_id} back mirror", "footer")
        buttons.data_button("Close", f"userset {user_id} close", "footer")

        rccmsg = "Exists" if await aiopath.exists(rclone_conf) else "Not Exists"
        if user_dict.get("RCLONE_PATH", False):
            rccpath = user_dict["RCLONE_PATH"]
        elif Config.RCLONE_PATH:
            rccpath = Config.RCLONE_PATH
        else:
            rccpath = "None"
        btns = buttons.build_menu(1)

        if user_dict.get("RCLONE_FLAGS", False):
            rcflags = user_dict["RCLONE_FLAGS"]
        elif "RCLONE_FLAGS" not in user_dict and Config.RCLONE_FLAGS:
            rcflags = Config.RCLONE_FLAGS
        else:
            rcflags = "None"

        text = f""" • <b>Rclone Config:</b> <b>{rccmsg}</b>
 • <b>Rclone Flags:</b> <code>{rcflags}</code>
 • <b>Rclone Path:</b> <code>{rccpath}</code>"""

    elif stype == "gdrive":
        buttons.data_button("token.pickle", f"userset {user_id} menu TOKEN_PICKLE")
        buttons.data_button("Default Gdrive ID", f"userset {user_id} menu GDRIVE_ID")
        buttons.data_button("Index URL", f"userset {user_id} menu INDEX_URL")
        buttons.data_button("User TDs", f"userset {user_id} user_tds")

        if Config.USER_TD_MODE:
            if user_dict.get("TD_MODE", False):
                buttons.data_button("Disable TD Mode", f"userset {user_id} tog TD_MODE f", "header")
                td_mode_msg = "Enabled"
            else:
                buttons.data_button("Enable TD Mode", f"userset {user_id} tog TD_MODE t", "header")
                td_mode_msg = "Disabled"
        else:
            td_mode_msg = "Force Disabled"

        if (
            user_dict.get("STOP_DUPLICATE", False)
            or "STOP_DUPLICATE" not in user_dict
            and Config.STOP_DUPLICATE
        ):
            buttons.data_button(
                "Disable Stop Duplicate", f"userset {user_id} tog STOP_DUPLICATE f"
            )
            sd_msg = "Enabled"
        else:
            buttons.data_button(
                "Enable Stop Duplicate",
                f"userset {user_id} tog STOP_DUPLICATE t",
                "l_body",
            )
            sd_msg = "Disabled"
        buttons.data_button("Back", f"userset {user_id} back mirror", "footer")
        buttons.data_button("Close", f"userset {user_id} close", "footer")

        tokenmsg = "Exists" if await aiopath.exists(token_pickle) else "Not Exists"
        if user_dict.get("GDRIVE_ID", False):
            gdrive_id = user_dict["GDRIVE_ID"]
        elif GDID := Config.GDRIVE_ID:
            gdrive_id = GDID
        else:
            gdrive_id = "None"
        index = user_dict["INDEX_URL"] if user_dict.get("INDEX_URL", False) else "None"

        user_tds = user_dict.get("USER_TDS", {})
        td_count = len(user_tds)
        if td_count > 0:
            td_msg = f"{td_count} TD(s)"
        else:
            td_msg = "Not set"

        btns = buttons.build_menu(2)

        text = f""" • <b>Gdrive Token:</b> <b>{tokenmsg}</b>
 • <b>Gdrive ID:</b> <code>{gdrive_id}</code>
 • <b>Index URL:</b> <code>{index}</code>
 • <b>User TD Mode:</b> <b>{td_mode_msg}</b>
 • <b>User TDs:</b> <b>{td_msg}</b>
 • <b>Stop Duplicate:</b> <b>{sd_msg}</b>"""
    elif stype == "mirror":
        buttons.data_button("RClone Tools", f"userset {user_id} rclone")
        rccmsg = "Exists" if await aiopath.exists(rclone_conf) else "Not Exists"
        if user_dict.get("RCLONE_PATH", False):
            rccpath = user_dict["RCLONE_PATH"]
        elif RP := Config.RCLONE_PATH:
            rccpath = RP
        else:
            rccpath = "None"

        buttons.data_button("GDrive Tools", f"userset {user_id} gdrive")
        tokenmsg = "Exists" if await aiopath.exists(token_pickle) else "Not Exists"
        if user_dict.get("GDRIVE_ID", False):
            gdrive_id = user_dict["GDRIVE_ID"]
        elif GI := Config.GDRIVE_ID:
            gdrive_id = GI
        else:
            gdrive_id = "None"

        if user_dict.get("MIRROR_PREFIX", False):
            mprefix = user_dict["MIRROR_PREFIX"]
        elif "MIRROR_PREFIX" not in user_dict and hasattr(Config, "MIRROR_PREFIX"):
            mprefix = Config.MIRROR_PREFIX
        else:
            mprefix = "Not Exists"

        if user_dict.get("MIRROR_SUFFIX", False):
            msuffix = user_dict["MIRROR_SUFFIX"]
        elif "MIRROR_SUFFIX" not in user_dict and hasattr(Config, "MIRROR_SUFFIX"):
            msuffix = Config.MIRROR_SUFFIX
        else:
            msuffix = "Not Exists"

        if user_dict.get("MIRROR_NAME_SWAP", False):
            mremname = user_dict["MIRROR_NAME_SWAP"]
        elif "MIRROR_NAME_SWAP" not in user_dict and hasattr(Config, "MIRROR_NAME_SWAP"):
            mremname = Config.MIRROR_NAME_SWAP
        else:
            mremname = "Not Exists"

        index = user_dict["INDEX_URL"] if user_dict.get("INDEX_URL", False) else "None"
        if (
            user_dict.get("STOP_DUPLICATE", False)
            or "STOP_DUPLICATE" not in user_dict
            and Config.STOP_DUPLICATE
        ):
            sd_msg = "Enabled"
        else:
            sd_msg = "Disabled"

        buttons.data_button("Terabox Cookie", f"userset {user_id} menu TERABOX_COOKIE")
        tbxmsg = (
            "Exists"
            if await aiopath.exists(f"terabox_cookies/{user_id}.txt")
            else "Not Exists"
        )

        buttons.data_button("Mirror Prefix", f"userset {user_id} menu MIRROR_PREFIX")
        buttons.data_button("Mirror Suffix", f"userset {user_id} menu MIRROR_SUFFIX")
        buttons.data_button("Mirror Name Swap", f"userset {user_id} menu MIRROR_NAME_SWAP")
        buttons.data_button("Back", f"userset {user_id} back", "footer")
        buttons.data_button("Close", f"userset {user_id} close", "footer")
        btns = buttons.build_menu(2)

        text = f""" • <b>Rclone Config:</b> <b>{rccmsg}</b>
 • <b>Rclone Path:</b> <code>{rccpath}</code>
 • <b>Gdrive Token:</b> <b>{tokenmsg}</b>
 • <b>Gdrive ID:</b> <code>{gdrive_id}</code>
 • <b>Terabox Cookie:</b> <b>{tbxmsg}</b>
 • <b>Index Link:</b> <code>{index}</code>
 • <b>Mirror Prefix:</b> <code>{mprefix}</code>
 • <b>Mirror Suffix:</b> <code>{msuffix}</code>
 • <b>Mirror Name Swap:</b> <code>{mremname}</code>
 • <b>Stop Duplicate:</b> <b>{sd_msg}</b>
"""

    elif stype == "ffset":
        buttons.data_button(
            "FFmpeg Cmds", f"userset {user_id} menu FFMPEG_CMDS", "header"
        )

        ffc = user_dict.get("FFMPEG_CMDS")
        if not ffc and "FFMPEG_CMDS" not in user_dict and Config.FFMPEG_CMDS:
            ffc = Config.FFMPEG_CMDS
        if isinstance(ffc, dict):
            ffc_count = len(ffc)
            ffc_preview = ", ".join(list(ffc.keys())[:3])
            if len(ffc) > 3:
                ffc_preview += "..."
        else:
            ffc_count = 0
            ffc_preview = "None"

        buttons.data_button("Metadata", f"userset {user_id} menu METADATA")
        metadata_setting = user_dict.get("METADATA")
        display_meta_val = "<b>Not Set</b>"
        if isinstance(metadata_setting, dict) and metadata_setting:
            display_meta_val = f"<b>{len(metadata_setting)} tag(s)</b>"
        elif isinstance(metadata_setting, str) and metadata_setting:  # Legacy
            display_meta_val = "<code>Legacy</code>"

        buttons.data_button("Audio Metadata", f"userset {user_id} menu AUDIO_METADATA")
        audio_meta_setting = user_dict.get("AUDIO_METADATA")
        display_audio_meta = "<b>Not Set</b>"
        if isinstance(audio_meta_setting, dict) and audio_meta_setting:
            display_audio_meta = f"<b>{len(audio_meta_setting)} tag(s)</b>"

        buttons.data_button("Video Metadata", f"userset {user_id} menu VIDEO_METADATA")
        video_meta_setting = user_dict.get("VIDEO_METADATA")
        display_video_meta = "<b>Not Set</b>"
        if isinstance(video_meta_setting, dict) and video_meta_setting:
            display_video_meta = f"<b>{len(video_meta_setting)} tag(s)</b>"

        buttons.data_button(
            "Subtitle Metadata", f"userset {user_id} menu SUBTITLE_METADATA"
        )
        subtitle_meta_setting = user_dict.get("SUBTITLE_METADATA")
        display_subtitle_meta = "<b>Not Set</b>"
        if isinstance(subtitle_meta_setting, dict) and subtitle_meta_setting:
            display_subtitle_meta = f"<b>{len(subtitle_meta_setting)} tag(s)</b>"

        merge_video_setting = user_dict.get("MERGE_VIDEO", False)
        if merge_video_setting:
            buttons.data_button("Disable Merge Video", f"userset {user_id} tog MERGE_VIDEO f")
        else:
            buttons.data_button("Enable Merge Video", f"userset {user_id} tog MERGE_VIDEO t")

        buttons.data_button("Back", f"userset {user_id} back", "footer")
        buttons.data_button("Close", f"userset {user_id} close", "footer")
        btns = buttons.build_menu(2)

        text = f"""<b>✦ FF MEDIA SETTINGS</b>
<i>Configure FFmpeg commands and metadata tagging for media files.</i>

 • <b>FFmpeg Presets:</b> {ffc_count} configured (<code>{ffc_preview}</code>)
 • <b>Global Metadata:</b> {display_meta_val}
 • <b>Audio Metadata:</b> {display_audio_meta}
 • <b>Video Metadata:</b> {display_video_meta}
 • <b>Subtitle Metadata:</b> {display_subtitle_meta}
 • <b>Merge Video:</b> {'Enabled' if merge_video_setting else 'Disabled'}

<blockquote><i>💡 Metadata is applied to files during upload. Use dynamic variables like {{filename}}, {{basename}}, {{audiolang}}.</i></blockquote>"""

    elif stype == "advanced":
        buttons.data_button(
            "Excluded Extensions", f"userset {user_id} menu EXCLUDED_EXTENSIONS"
        )
        if user_dict.get("EXCLUDED_EXTENSIONS", False):
            ex_ex = user_dict["EXCLUDED_EXTENSIONS"]
        elif "EXCLUDED_EXTENSIONS" not in user_dict:
            ex_ex = excluded_extensions
        else:
            ex_ex = ["!qB", "aria2"]

        if ex_ex and ex_ex != ["!qB", "aria2"]:
            ex_ex_count = len([e for e in ex_ex if e not in ["!qB", "aria2"]])
            ex_ex_display = f"<b>{ex_ex_count} extension(s)</b>"
        else:
            ex_ex_display = "<b>None</b>"

        buttons.data_button("YT-DLP Options", f"userset {user_id} menu YT_DLP_OPTIONS")
        ytopt = user_dict.get("YT_DLP_OPTIONS")
        if not ytopt and "YT_DLP_OPTIONS" not in user_dict and Config.YT_DLP_OPTIONS:
            ytopt = Config.YT_DLP_OPTIONS
        if isinstance(ytopt, dict) and ytopt:
            ytopt_display = f"<b>{len(ytopt)} option(s)</b>"
        else:
            ytopt_display = "<b>Not Set</b>"

        upload_paths = user_dict.get("UPLOAD_PATHS", {})
        if not upload_paths and "UPLOAD_PATHS" not in user_dict and Config.UPLOAD_PATHS:
            upload_paths = Config.UPLOAD_PATHS
        if isinstance(upload_paths, dict) and upload_paths:
            up_count = len(upload_paths)
            up_display = f"<b>{up_count} path(s)</b>"
        else:
            up_display = "<b>None</b>"
        buttons.data_button("Upload Paths", f"userset {user_id} menu UPLOAD_PATHS")

        yt_cookie_path = f"cookies/{user_id}/cookies.txt"
        user_cookie_msg = (
            "<b>Exists</b>" if await aiopath.exists(yt_cookie_path) else "<b>Not Set</b>"
        )
        buttons.data_button(
            "YT Cookie File", f"userset {user_id} menu USER_COOKIE_FILE"
        )

        buttons.data_button("Back", f"userset {user_id} back", "footer")
        buttons.data_button("Close", f"userset {user_id} close", "footer")
        btns = buttons.build_menu(1)

        text = f"""<b>✦ MISC SETTINGS</b>
<i>Advanced options for file handling, downloads, and authentication.</i>

 • <b>Excluded Extensions:</b> {ex_ex_display}
 • <b>Upload Paths:</b> {up_display}
 • <b>YT-DLP Options:</b> {ytopt_display}
 • <b>YT Cookie File:</b> {user_cookie_msg}

<blockquote><i>💡 Excluded extensions are skipped during upload. Upload paths are shortcuts for frequently used destinations.</i></blockquote>"""

    return text, btns


async def update_user_settings(query, stype="main"):
    handler_dict[query.from_user.id] = False
    msg, button = await get_user_settings(query.from_user, stype)
    await edit_message(query.message, msg, button)


@new_task
async def send_user_settings(_, message):
    from_user = message.from_user
    handler_dict[from_user.id] = False
    msg, button = await get_user_settings(from_user)
    await send_message(message, msg, button)


@new_task
async def add_file(_, message, ftype, rfunc):
    user_id = message.from_user.id
    handler_dict[user_id] = False
    if ftype == "THUMBNAIL":
        des_dir = await create_thumb(message, user_id)
    elif ftype == "RCLONE_CONFIG":
        rpath = f"{getcwd()}/rclone/"
        await makedirs(rpath, exist_ok=True)
        des_dir = f"{rpath}{user_id}.conf"
        await message.download(file_name=des_dir)
    elif ftype == "TOKEN_PICKLE":
        tpath = f"{getcwd()}/tokens/"
        await makedirs(tpath, exist_ok=True)
        des_dir = f"{tpath}{user_id}.pickle"
        await message.download(file_name=des_dir)
    elif ftype == "USER_COOKIE_FILE":
        cpath = f"{getcwd()}/cookies/{user_id}"
        await makedirs(cpath, exist_ok=True)
        des_dir = f"{cpath}/cookies.txt"
        await message.download(file_name=des_dir)
    elif ftype == "TERABOX_COOKIE":
        tbpath = f"{getcwd()}/terabox_cookies/"
        await makedirs(tbpath, exist_ok=True)
        des_dir = f"{tbpath}{user_id}.txt"
        await message.download(file_name=des_dir)
    await delete_message(message)
    update_user_ldata(user_id, ftype, des_dir)
    await rfunc()
    await database.update_user_doc(user_id, ftype, des_dir)


@new_task
async def add_one(_, message, option, rfunc):
    user_id = message.from_user.id
    handler_dict[user_id] = False
    user_dict = user_data.get(user_id, {})
    value = message.text
    if value.startswith("{") and value.endswith("}"):
        try:
            value = literal_eval(value)
            if not isinstance(value, dict):
                raise ValueError(
                    f"Value must be a literal dict, got: {type(value).__name__}"
                )
            if user_dict[option]:
                user_dict[option].update(value)
            else:
                update_user_ldata(user_id, option, value)
        except Exception as e:
            await send_message(message, f"Invalid dict literal: {e}")
            return
    else:
        await send_message(message, "It must be Dict!")
        return
    await delete_message(message)
    await rfunc()
    await database.update_user_data(user_id)


@new_task
async def remove_one(_, message, option, rfunc):
    user_id = message.from_user.id
    handler_dict[user_id] = False
    user_dict = user_data.get(user_id, {})
    names = message.text.split("/")
    for name in names:
        if name in user_dict[option]:
            del user_dict[option][name]
    await delete_message(message)
    await rfunc()
    await database.update_user_data(user_id)


@new_task
async def set_option(_, message, option, rfunc):
    user_id = message.from_user.id
    handler_dict[user_id] = False
    value = message.text
    if option == "EXCLUDED_EXTENSIONS":
        fx = value.split()
        value = ["aria2", "!qB"]
        for x in fx:
            x = x.lstrip(".")
            value.append(x.strip().lower())
    elif option in [
        "METADATA",
        "AUDIO_METADATA",
        "VIDEO_METADATA",
        "SUBTITLE_METADATA",
    ]:
        parsed_metadata_dict = {}
        if value and isinstance(value, str):
            if value.strip() == "":
                value = {}
            else:
                parts = []
                current = ""
                i = 0
                while i < len(value):
                    if value[i] == "\\" and i + 1 < len(value) and value[i + 1] == "|":
                        current += "|"
                        i += 2
                    elif value[i] == "|":
                        parts.append(current)
                        current = ""
                        i += 1
                    else:
                        current += value[i]
                        i += 1
                if current:
                    parts.append(current)

                for part in parts:
                    if "=" in part:
                        key, val_str = part.split("=", 1)
                        parsed_metadata_dict[key.strip()] = val_str.strip()
                if not parsed_metadata_dict and value.strip() != "":
                    await send_message(
                        message,
                        "Malformed metadata string. Format: key1=value1|key2=value2. Use \\| to escape pipe characters.",
                    )
                    return
                value = parsed_metadata_dict
        else:
            value = {}

    elif option in ["UPLOAD_PATHS", "FFMPEG_CMDS", "YT_DLP_OPTIONS"]:
        if value.startswith("{") and value.endswith("}"):
            try:
                value = literal_eval(sub(r"\s+", " ", value))
                if not isinstance(value, dict):
                    raise ValueError(
                        f"Value must be a literal dict, got: {type(value).__name__}"
                    )
            except Exception as e:
                await send_message(message, f"Invalid dict literal: {e}")
                return
        else:
            await send_message(message, "It must be dict!")
            return
    update_user_ldata(user_id, option, value)
    await delete_message(message)
    await rfunc()
    await database.update_user_data(user_id)


@new_task
async def set_ldump(_, message, user_id, rfunc):
    handler_dict[user_id] = False
    value = message.text
    user_dict = user_data.get(user_id, {})
    ldumps = user_dict.get("LDUMP", {})

    for dump_item in value.split("\n"):
        if dump_item == "":
            continue

        dump_info = dump_item.rsplit(
            maxsplit=(1 if dump_item.split()[-1].startswith(("-100", "@", "pm")) else 0)
        )

        if len(dump_info) < 2:
            continue

        title = dump_info[0]
        chat_str = dump_info[1]

        for existing_title in list(ldumps.keys()):
            if title.casefold() == existing_title.casefold():
                del ldumps[existing_title]

        topic_suffix = ""
        chat_str_bare = chat_str
        if "|" in chat_str:
            chat_str_bare, topic_id_part = chat_str.split("|", 1)
            if topic_id_part.lstrip("-").isdigit():
                topic_suffix = f"|{int(topic_id_part)}"

        if chat_str_bare.lower() == "pm":
            ldumps[title] = "pm"
        else:
            chat = await chat_info(chat_str_bare)
            if chat:
                ldumps[title] = f"{chat.id}{topic_suffix}" if topic_suffix else chat.id
            else:
                await send_message(message, f"⚠ Invalid chat: {chat_str_bare}. Skipping.")

    update_user_ldata(user_id, "LDUMP", ldumps)
    await delete_message(message)
    await rfunc()
    await database.update_user_data(user_id)


@new_task
async def set_user_td(_, message, user_id, rfunc):
    handler_dict[user_id] = False
    value = message.text
    user_dict = user_data.get(user_id, {})
    user_tds = user_dict.get("USER_TDS", {})

    for td_item in value.split("\n"):
        if td_item == "":
            continue

        parts = td_item.split()

        if len(parts) < 2:
            continue

        td_name = parts[0]
        if len(parts) > 2 and parts[-1].startswith(("http://", "https://")):
            drive_id = " ".join(parts[1:-1])
            index_link = parts[-1]
        else:
            drive_id = " ".join(parts[1:])
            index_link = ""

        if not drive_id or len(drive_id) < 10:
            await send_message(message, f"⚠ Invalid drive ID format for '{td_name}'. Skipping.")
            continue

        for existing_name in list(user_tds.keys()):
            if td_name.casefold() == existing_name.casefold():
                del user_tds[existing_name]

        user_tds[td_name] = {
            "drive_id": drive_id,
            "index_link": index_link
        }

    update_user_ldata(user_id, "USER_TDS", user_tds)
    await delete_message(message)
    await rfunc()
    await database.update_user_data(user_id)


async def get_menu(option, message, user_id):
    handler_dict[user_id] = False
    user_dict = user_data.get(user_id, {})

    file_dict = {
        "THUMBNAIL": f"thumbnails/{user_id}.jpg",
        "RCLONE_CONFIG": f"rclone/{user_id}.conf",
        "TOKEN_PICKLE": f"tokens/{user_id}.pickle",
        "USER_COOKIE_FILE": f"cookies/{user_id}/cookies.txt",
        "TERABOX_COOKIE": f"terabox_cookies/{user_id}.txt",
    }

    buttons = ButtonMaker()
    if option in [
        "THUMBNAIL", "RCLONE_CONFIG", "TOKEN_PICKLE", "USER_COOKIE_FILE",
        "TERABOX_COOKIE",
    ]:
        key = "file"
    else:
        key = "set"
    buttons.data_button(
        "Change" if user_dict.get(option, False) else "Set",
        f"userset {user_id} {key} {option}",
    )
    if user_dict.get(option, False):
        if option == "THUMBNAIL":
            buttons.data_button(
                "View Thumb", f"userset {user_id} view THUMBNAIL", "header"
            )
        elif option in ["YT_DLP_OPTIONS", "FFMPEG_CMDS", "UPLOAD_PATHS"]:
            buttons.data_button(
                "Add One", f"userset {user_id} addone {option}", "header"
            )
            buttons.data_button(
                "Remove One", f"userset {user_id} rmone {option}", "header"
            )

        if key != "file":  # TODO: option default val check
            buttons.data_button("Reset", f"userset {user_id} reset {option}")
        elif await aiopath.exists(file_dict[option]):
            buttons.data_button("Remove", f"userset {user_id} remove {option}")
    if option in leech_options:
        back_to = "leech"
    elif option in rclone_options:
        back_to = "rclone"
    elif option in gdrive_options:
        back_to = "gdrive"
    elif option in ffset_options:
        back_to = "ffset"
    elif option in advanced_options:
        back_to = "advanced"
    elif option in mirror_options:
        back_to = "mirror"
    else:
        back_to = "back"
    buttons.data_button("Back", f"userset {user_id} {back_to}", "footer")
    buttons.data_button("Close", f"userset {user_id} close", "footer")
    val = user_dict.get(option)
    if option in file_dict and await aiopath.exists(file_dict[option]):
        val = "<b>Exists</b>"
    elif option == "METADATA":
        current_meta_val = user_dict.get(option)
        if isinstance(current_meta_val, dict) and current_meta_val:
            val = ", ".join(
                f"{k}={escape(str(v))}" for k, v in current_meta_val.items()
            )
            val = f"<code>{val}</code>"
        elif isinstance(current_meta_val, str) and current_meta_val:
            val = (
                f"<code>{escape(current_meta_val)}</code> [<i>Legacy, needs re-set</i>]"
            )
        elif not current_meta_val:
            val = "<b>Not Set</b>"

        if val is None:
            val = "<b>Not Exists</b>"

    display_name = fname_dict.get(option, option)
    text = f"✦ <b><u>{display_name} Settings :</u></b>\n\n"

    text += f"➜ <b>Current Value :</b> {val if val else '<i>Not Set</i>'}\n\n"

    text += f"➜ <b>Description :</b> <i>{user_settings_text[option][1]}</i>"

    if option in ["METADATA", "AUDIO_METADATA", "VIDEO_METADATA", "SUBTITLE_METADATA"]:
        text += """

➜ <b>Dynamic Variables:</b>
   • <code>{filename}</code> - Full filename
   • <code>{basename}</code> - Filename without extension
   • <code>{extension}</code> - File extension
   • <code>{audiolang}</code> - Audio language
   • <code>{sublang}</code> - Subtitle language
   • <code>{year}</code> - Year from filename
"""

    if option == "FFMPEG_CMDS":
        text += """

➜ <b>File Patterns:</b>
   • <code>mltb.mkv</code> - Match only .mkv files
   • <code>mltb.video</code> - Match all video files
   • <code>mltb.audio</code> - Match all audio files
   • <code>mltb</code> - Output same extension as input

➜ <b>Special Flag:</b> Add <code>-del</code> to delete originals after processing
"""
    await edit_message(message, text, buttons.build_menu(2))


async def event_handler(client, query, pfunc, rfunc, photo=False, document=False):
    user_id = query.from_user.id
    handler_dict[user_id] = True
    start_time = update_time = time()

    async def event_filter(_, __, event):
        if photo:
            mtype = event.photo or event.document
        elif document:
            mtype = event.document
        else:
            mtype = event.text
        user = event.from_user or event.sender_chat
        return bool(
            user.id == user_id and event.chat.id == query.message.chat.id and mtype
        )

    handler = client.add_handler(
        MessageHandler(pfunc, filters=create(event_filter)), group=-1
    )

    while handler_dict[user_id]:
        await sleep(0.5)
        if time() - start_time > 60:
            handler_dict[user_id] = False
            await rfunc()
        elif time() - update_time > 8 and handler_dict[user_id]:
            update_time = time()
            msg = await client.get_messages(query.message.chat.id, query.message.id)
            text = msg.text.split("\n")
            text[-1] = (
                f" • <b>Time Left:</b> <code>{round(60 - (time() - start_time), 2)} sec</code>"
            )
            await edit_message(msg, "\n".join(text), msg.reply_markup)
    client.remove_handler(*handler)


@new_task
async def edit_user_settings(client, query):
    from_user = query.from_user
    user_id = from_user.id
    name = from_user.mention
    message = query.message
    data = query.data.split()

    handler_dict[user_id] = False
    thumb_path = f"thumbnails/{user_id}.jpg"
    rclone_conf = f"rclone/{user_id}.conf"
    token_pickle = f"tokens/{user_id}.pickle"
    yt_cookie_path = f"cookies/{user_id}/cookies.txt"

    user_dict = user_data.get(user_id, {})
    if user_id != int(data[1]):
        return await query.answer("Not Yours!", show_alert=True)
    elif data[2] == "setevent":
        await query.answer()
    elif data[2] in [
        "general",
        "mirror",
        "leech",
        "uphoster",
        "gofile",
        "buzzheavier",
        "pixeldrain",
        "ffset",
        "advanced",
        "gdrive",
        "rclone",
    ]:
        await query.answer()
        await update_user_settings(query, data[2])
    elif data[2] == "uphoster_destinations":
        await query.answer()
        user_dict = user_data.get(user_id, {})
        uphoster_service = user_dict.get("UPHOSTER_SERVICE", "gofile")
        selected_services = uphoster_service.split(",") if uphoster_service else []

        if len(data) > 3:
            service = data[3]
            if service in selected_services:
                if len(selected_services) > 1:
                    selected_services.remove(service)
                else:
                    await query.answer(
                        "At least one destination must be selected!", show_alert=True
                    )
            else:
                selected_services.append(service)
            new_services = ",".join(selected_services)
            update_user_ldata(user_id, "UPHOSTER_SERVICE", new_services)
            await database.update_user_data(user_id)
            selected_services = new_services.split(",")
        else:
            selected_services = (
                uphoster_service.split(",") if uphoster_service else ["gofile"]
            )

        buttons = ButtonMaker()
        for service in ["gofile", "buzzheavier", "pixeldrain"]:
            state = "✓" if service in selected_services else ""
            buttons.data_button(
                f"{service.capitalize()} {state}",
                f"userset {user_id} uphoster_destinations {service}",
            )

        buttons.data_button("Back", f"userset {user_id} back uphoster", "footer")
        buttons.data_button("Close", f"userset {user_id} close", "footer")

        text = f"""<b>Select Uphoster Destinations:</b>"""
        await edit_message(message, text, buttons.build_menu(1))
    elif data[2] == "caption_style":
        await query.answer()
        await update_user_settings(query, stype="caption_style")
    elif data[2] == "set_style":
        style = data[3] if len(data) > 3 else ""
        if style:
            update_user_ldata(user_id, "LEECH_CAPTION_STYLE", style)
            await query.answer(f"Caption style set to {style.capitalize()}!", show_alert=True)
        else:
            update_user_ldata(user_id, "LEECH_CAPTION_STYLE", "")
            await query.answer("Caption style disabled!", show_alert=True)
        await database.update_user_data(user_id)
        await update_user_settings(query, stype="caption_style")
    elif data[2] == "menu":
        await query.answer()
        await get_menu(data[3], message, user_id)
    elif data[2] == "tog":
        await query.answer()
        if data[3] == "TD_MODE":
            if not Config.USER_TD_MODE:
                return await query.answer(
                    "User TD Mode is disabled by bot owner!",                     show_alert=True
                )
            if data[4] == "t":
                user_tds = user_dict.get("USER_TDS", {})
                if not user_tds:
                    return await query.answer(
                        "Set at least one TD first before enabling TD Mode!", show_alert=True
                    )
            update_user_ldata(user_id, data[3], data[4] == "t")
            await update_user_settings(query, stype="gdrive")
            await database.update_user_data(user_id)
            return

        update_user_ldata(user_id, data[3], data[4] == "t")
        if data[3] == "STOP_DUPLICATE":
            back_to = "gdrive"
        elif data[3] in ["USER_TOKENS", "USE_DEFAULT_COOKIE"]:
            back_to = "general"
        elif data[3] == "MERGE_VIDEO":
            back_to = "ffset"
        else:
            back_to = "leech"
        await update_user_settings(query, stype=back_to)
        await database.update_user_data(user_id)
    elif data[2] == "file":
        await query.answer()
        buttons = ButtonMaker()
        text = user_settings_text[data[3]][2]
        buttons.data_button("Stop", f"userset {user_id} menu {data[3]} stop")
        buttons.data_button("Back", f"userset {user_id} menu {data[3]}", "footer")
        buttons.data_button("Close", f"userset {user_id} close", "footer")
        prompt_title = data[3].replace("_", " ").title()
        new_message_text = f"<b>Set {prompt_title}</b>\n\n{text}"
        await edit_message(message, new_message_text, buttons.build_menu(1))
        rfunc = partial(get_menu, data[3], message, user_id)
        pfunc = partial(add_file, ftype=data[3], rfunc=rfunc)
        await event_handler(
            client,
            query,
            pfunc,
            rfunc,
            photo=data[3] == "THUMBNAIL",
            document=data[3] != "THUMBNAIL",
        )
    elif data[2] == "ldump_list":
        await query.answer()
        if len(data) > 3 and data[3] == "stop":
            await update_user_settings(query, "leech")
            return
        ldumps = user_dict.get("LDUMP", {})
        buttons = ButtonMaker()
        if ldumps:
            for dump_name, dump_chat in ldumps.items():
                buttons.data_button(
                    f"✗ {dump_name}", f"userset {user_id} ldump_rm {dump_name}"
                )
        buttons.data_button("Add Dump", f"userset {user_id} ldump_add")
        buttons.data_button("Back", f"userset {user_id} back leech", "footer")
        buttons.data_button("Close", f"userset {user_id} close", "footer")

        if ldumps:
            dump_list = "\n".join([f" • <b>{name}</b>: <code>{chat}</code>" for name, chat in ldumps.items()])
            text = f"""<b>Custom Leech Dumps</b>
 • <b>Name:</b> {name}

{dump_list}
 • <b>Total:</b> {len(ldumps)} dump(s)"""
        else:
            text = f"""<b>Custom Leech Dumps</b>
 • <b>Name:</b> {name}

 • <b>No dumps configured yet.</b>"""

        await edit_message(message, text, buttons.build_menu(2))
    elif data[2] == "ldump_add":
        await query.answer()
        if len(data) > 3 and data[3] == "stop":
            await update_user_settings(query, "leech")
            return
        buttons = ButtonMaker()
        text = user_settings_text["LDUMP"][2]
        buttons.data_button("Stop", f"userset {user_id} ldump_list stop")
        buttons.data_button("Back", f"userset {user_id} ldump_list", "footer")
        buttons.data_button("Close", f"userset {user_id} close", "footer")
        await edit_message(message, f"<b>Add Custom Dumps</b>\n\n{text}", buttons.build_menu(1))
        rfunc = partial(update_user_settings, query, "leech")
        pfunc = partial(set_ldump, user_id=user_id, rfunc=rfunc)
        await event_handler(client, query, pfunc, rfunc)
    elif data[2] == "ldump_rm":
        dump_name = data[3]
        await query.answer(f"Removed {dump_name}!", show_alert=True)
        ldumps = user_dict.get("LDUMP", {})
        if dump_name in ldumps:
            del ldumps[dump_name]
            update_user_ldata(user_id, "LDUMP", ldumps)
            await database.update_user_data(user_id)
        ldumps = user_dict.get("LDUMP", {})
        buttons = ButtonMaker()
        if ldumps:
            for dump_name2, dump_chat in ldumps.items():
                buttons.data_button(
                    f"✗ {dump_name2}", f"userset {user_id} ldump_rm {dump_name2}"
                )
        buttons.data_button("Add Dump", f"userset {user_id} ldump_add")
        buttons.data_button("Back", f"userset {user_id} back leech", "footer")
        buttons.data_button("Close", f"userset {user_id} close", "footer")

        if ldumps:
            dump_list = "\n".join([f" • <b>{name}</b>: <code>{chat}</code>" for name, chat in ldumps.items()])
            text = f"""<b>Custom Leech Dumps</b>
 • <b>Name:</b> {name}

{dump_list}
 • <b>Total:</b> {len(ldumps)} dump(s)"""
        else:
            text = f"""<b>Custom Leech Dumps</b>
 • <b>Name:</b> {name}

 • <b>No dumps configured yet.</b>"""

        await edit_message(message, text, buttons.build_menu(2))
    elif data[2] == "user_tds":
        await query.answer()
        if len(data) > 3 and data[3] == "stop":
            await update_user_settings(query, "gdrive")
            return

        user_tds = user_dict.get("USER_TDS", {})
        buttons = ButtonMaker()
        if user_tds:
            for td_name, td_info in user_tds.items():
                buttons.data_button(
                    f"✗ {td_name}", f"userset {user_id} td_rm {td_name}"
                )
        buttons.data_button("Add TD", f"userset {user_id} td_add")
        buttons.data_button("Back", f"userset {user_id} back gdrive", "footer")
        buttons.data_button("Close", f"userset {user_id} close", "footer")

        if user_tds:
            td_list = "\n".join([
                f" • <b>{name}</b>:\n"
                f"   • <b>Drive ID:</b> <code>{info['drive_id']}</code>\n"
                f"   • <b>Index:</b> <code>{info.get('index_link', 'None')}</code>"
                for name, info in user_tds.items()
            ])
            text = f"""<b>User Team Drives</b>
 • <b>Name:</b> {name}

{td_list}
 • <b>Total:</b> {len(user_tds)} TD(s)"""
        else:
            text = f"""<b>User Team Drives</b>
 • <b>Name:</b> {name}

 • <b>No TDs configured yet.</b>"""

        await edit_message(message, text, buttons.build_menu(2))
    elif data[2] == "td_add":
        await query.answer()
        if len(data) > 3 and data[3] == "stop":
            await update_user_settings(query, "gdrive")
            return

        buttons = ButtonMaker()
        text = user_settings_text["USER_TDS"][2]
        buttons.data_button("Stop", f"userset {user_id} user_tds stop")
        buttons.data_button("Back", f"userset {user_id} user_tds", "footer")
        buttons.data_button("Close", f"userset {user_id} close", "footer")
        await edit_message(message, f"<b>Add User Team Drive</b>\n\n{text}", buttons.build_menu(1))
        rfunc = partial(update_user_settings, query, "gdrive")
        pfunc = partial(set_user_td, user_id=user_id, rfunc=rfunc)
        await event_handler(client, query, pfunc, rfunc)
    elif data[2] == "td_rm":
        td_name = data[3]
        await query.answer(f"Removed {td_name}!", show_alert=True)
        user_tds = user_dict.get("USER_TDS", {})
        if td_name in user_tds:
            del user_tds[td_name]
            update_user_ldata(user_id, "USER_TDS", user_tds)
            await database.update_user_data(user_id)
        user_tds = user_dict.get("USER_TDS", {})
        buttons = ButtonMaker()
        if user_tds:
            for td_name2, td_info in user_tds.items():
                buttons.data_button(
                    f"✗ {td_name2}", f"userset {user_id} td_rm {td_name2}"
                )
        buttons.data_button("Add TD", f"userset {user_id} td_add")
        buttons.data_button("Back", f"userset {user_id} back gdrive", "footer")
        buttons.data_button("Close", f"userset {user_id} close", "footer")

        if user_tds:
            td_list = "\n".join([
                f" • <b>{name}</b>:\n"
                f"   • <b>Drive ID:</b> <code>{info['drive_id']}</code>\n"
                f"   • <b>Index:</b> <code>{info.get('index_link', 'None')}</code>"
                for name, info in user_tds.items()
            ])
            text = f"""<b>User Team Drives</b>
 • <b>Name:</b> {name}

{td_list}
 • <b>Total:</b> {len(user_tds)} TD(s)"""
        else:
            text = f"""<b>User Team Drives</b>
 • <b>Name:</b> {name}

 • <b>No TDs configured yet.</b>"""

        await edit_message(message, text, buttons.build_menu(2))
    elif data[2] in ["set", "addone", "rmone"]:
        await query.answer()
        buttons = ButtonMaker()
        if data[2] == "set":
            text = user_settings_text[data[3]][2]
            func = set_option
        elif data[2] == "addone":
            text = f"Add one or more string key and value to {data[3]}. Example: {{'key 1': 62625261, 'key 2': 'value 2'}}. Timeout: 60 sec"
            func = add_one
        elif data[2] == "rmone":
            text = f"Remove one or more key from {data[3]}. Example: key 1/key2/key 3. Timeout: 60 sec"
            func = remove_one
        buttons.data_button("Stop", f"userset {user_id} menu {data[3]} stop")
        buttons.data_button("Back", f"userset {user_id} menu {data[3]}", "footer")
        buttons.data_button("Close", f"userset {user_id} close", "footer")
        await edit_message(
            message, message.text.html + "\n\n" + text, buttons.build_menu(1)
        )
        rfunc = partial(get_menu, data[3], message, user_id)
        pfunc = partial(func, option=data[3], rfunc=rfunc)
        await event_handler(client, query, pfunc, rfunc)
    elif data[2] == "remove":
        await query.answer("Removed!", show_alert=True)
        if data[3] in [
            "THUMBNAIL",
            "RCLONE_CONFIG",
            "TOKEN_PICKLE",
            "USER_COOKIE_FILE",
        ]:
            if data[3] == "THUMBNAIL":
                fpath = thumb_path
            elif data[3] == "RCLONE_CONFIG":
                fpath = rclone_conf
            elif data[3] == "USER_COOKIE_FILE":
                fpath = yt_cookie_path
            else:
                fpath = token_pickle
            if await aiopath.exists(fpath):
                await remove(fpath)
            del user_dict[data[3]]
            await database.update_user_doc(user_id, data[3])
        else:
            update_user_ldata(user_id, data[3], "")
            await database.update_user_data(user_id)
        await get_menu(data[3], message, user_id)
    elif data[2] == "reset":
        await query.answer("Reset Done!", show_alert=True)
        user_dict.pop(data[3], None)
        await database.update_user_data(user_id)
        await get_menu(data[3], message, user_id)
    elif data[2] == "confirm_reset_all":
        await query.answer()
        buttons = ButtonMaker()
        buttons.data_button("Yes", f"userset {user_id} do_reset_all yes")
        buttons.data_button("No", f"userset {user_id} do_reset_all no")
        buttons.data_button("Close", f"userset {user_id} close", "footer")
        text = "<i>Are you sure you want to reset all your user settings?</i>"
        await edit_message(query.message, text, buttons.build_menu(2))
    elif data[2] == "do_reset_all":
        if data[3] == "yes":
            await query.answer("Reset Done!", show_alert=True)
            user_dict = user_data.get(user_id, {})
            for k in list(user_dict.keys()):
                if k not in ("SUDO", "AUTH", "VERIFY_TOKEN", "VERIFY_TIME"):
                    del user_dict[k]
            for fpath in [thumb_path, rclone_conf, token_pickle, yt_cookie_path]:
                if await aiopath.exists(fpath):
                    await remove(fpath)
            await update_user_settings(query)
            await database.update_user_data(user_id)
        else:
            await query.answer("Reset Cancelled.", show_alert=True)
            await update_user_settings(query)
    elif data[2] == "view":
        await query.answer()
        await send_file(message, thumb_path, name)
    elif data[2] in ["gd", "rc", "tbx"]:
        await query.answer()
        du = {"rc": "gd", "gd": "tbx", "tbx": "rc"}.get(data[2], "rc")
        update_user_ldata(user_id, "DEFAULT_UPLOAD", du)
        await update_user_settings(query, stype="general")
        await database.update_user_data(user_id)
    elif data[2] == "back":
        await query.answer()
        stype = data[3] if len(data) == 4 else "main"
        await update_user_settings(query, stype)
    else:
        await query.answer()
        await delete_message(message, message.reply_to_message)


@new_task
async def get_users_settings(_, message):
    msg = ""
    if auth_chats:
        msg += f"AUTHORIZED_CHATS: {auth_chats}\n"
    if sudo_users:
        msg += f"SUDO_USERS: {sudo_users}\n\n"
    if user_data:
        for u, d in user_data.items():
            kmsg = f"\n<b>{u}:</b>\n"
            if vmsg := "".join(
                f"{k}: <code>{v or None}</code>\n" for k, v in d.items()
            ):
                msg += kmsg + vmsg
        if not msg:
            await send_message(message, "No users data!")
            return
        msg_ecd = msg.encode()
        if len(msg_ecd) > 4000:
            with BytesIO(msg_ecd) as ofile:
                ofile.name = "users_settings.txt"
                await send_file(message, ofile)
        else:
            await send_message(message, msg)
    else:
        await send_message(message, "No users data!")
