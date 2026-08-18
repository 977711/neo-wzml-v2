# This file is a part of NEO-WZML (github.com/irisXDR/NEO-WZML)

from bot.core.config_manager import Config


class BotCommands:
    StartCommand = "start"
    LoginCommand = "login"

    _static_commands = {
        "Mirror": ["mirror", "m"],
        "QbMirror": ["qbmirror", "qm"],
        "JdMirror": ["jdmirror", "jm"],
        "Ytdl": ["ytdl", "y"],
        "UpHoster": ["uphoster", "up"],
        "Leech": ["leech", "l"],
        "QbLeech": ["qbleech", "ql"],
        "JdLeech": ["jdleech", "jl"],
        "YtdlLeech": ["ytdlleech", "yl"],
        "Clone": ["clone", "cl"],
        "Count": "count",
        "Delete": "del",
        "List": "list",
        "Search": "search",
        "Users": "users",
        "CancelTask": ["cancel", "c"],
        "CancelAll": ["cancelall", "call"],
        "ForceStart": ["forcestart", "fs"],
        "Status": ["status", "s", "statusall", "sall"],
        "MediaInfo": ["mediainfo", "mi"],
        "Ping": "ping",
        "Restart": ["restart", "r", "restartall"],
        "RestartSessions": ["restartses", "rses"],
        "Stats": ["stats", "st"],
        "Help": ["help", "h"],
        "Log": "log",
        "Shell": "shell",
        "AExec": "aexec",
        "Exec": "exec",
        "ClearLocals": "clearlocals",
        "Rss": "rss",
        "Authorize": ["authorize", "a"],
        "UnAuthorize": ["unauthorize", "ua"],
        "AddSudo": ["addsudo", "as"],
        "RmSudo": ["rmsudo", "rs"],
        "SudoList": "sudolist",
        "BotSet": ["bsetting", "bs"],
        "UserSet": ["usetting", "us"],
        "Select": ["select", "sel"],
        "SpeedTest": ["speedtest", "stest"],
        "Plugins": "plugins",
        "GDClean": ["gdclean", "gc"],
    }

    @classmethod
    def get_commands(cls):
        commands = cls._static_commands.copy()

        from bot.core.plugin_manager import get_plugin_manager
        from bot.core.config_manager import Config

        if Config.SHOW_EXTRA_CMDS:
            if isinstance(commands["Mirror"], list):
                commands["Mirror"] = commands["Mirror"] + ["zipmirror", "zm", "unzipmirror", "uzm"]
            if isinstance(commands["QbMirror"], list):
                commands["QbMirror"] = commands["QbMirror"] + ["qbzipmirror", "qzm", "qbunzipmirror", "quzm"]
            if isinstance(commands["JdMirror"], list):
                commands["JdMirror"] = commands["JdMirror"] + ["jdzipmirror", "jzm", "jdunzipmirror", "juzm"]
            if isinstance(commands["UpHoster"], list):
                commands["UpHoster"] = commands["UpHoster"] + ["zipuphoster", "zup", "unzipuphoster", "uzup"]
            if isinstance(commands["Leech"], list):
                commands["Leech"] = commands["Leech"] + ["zipleech", "zl", "unzipleech", "uzl"]
            if isinstance(commands["QbLeech"], list):
                commands["QbLeech"] = commands["QbLeech"] + ["qbzipleech", "qzl", "qbunzipleech", "quzl"]
            if isinstance(commands["JdLeech"], list):
                commands["JdLeech"] = commands["JdLeech"] + ["jdzipleech", "jzl", "jdunzipleech", "juzl"]

        plugin_manager = get_plugin_manager()
        if plugin_manager:
            for plugin_info in plugin_manager.list_plugins():
                if plugin_info.enabled and plugin_info.commands:
                    for cmd in plugin_info.commands:
                        if cmd == "speedtest":
                            commands["SpeedTest"] = ["speedtest", "stest"]
                        elif cmd == "stest":
                            if "SpeedTest" not in commands:
                                commands["SpeedTest"] = ["speedtest", "stest"]
                            elif "stest" not in commands["SpeedTest"]:
                                commands["SpeedTest"].append("stest")

        return commands

    @classmethod
    def _build_command_vars(cls):
        commands = cls.get_commands()

        for key, cmds in commands.items():
            setattr(
                cls,
                f"{key}Command",
                (
                    [
                        (
                            f"{cmd}{Config.CMD_SUFFIX}"
                            if cmd not in ["restartall", "statusall", "sall"]
                            else cmd
                        )
                        for cmd in cmds
                    ]
                    if isinstance(cmds, list)
                    else f"{cmds}{Config.CMD_SUFFIX}"
                ),
            )

    @classmethod
    def refresh_commands(cls):
        cls._build_command_vars()
