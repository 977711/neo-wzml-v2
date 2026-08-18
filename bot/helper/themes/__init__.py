#!/usr/bin/env python3
# This file is a part of NEO-WZML (github.com/irisXDR/NEO-WZML)

"""Dynamic theme loading for BotTheme."""
from os import listdir
from importlib import import_module
from random import choice as rchoice

from bot.core.config_manager import Config
from bot import LOGGER
from bot.helper.themes import neo_minimal

AVL_THEMES = {}

try:
    for theme in listdir("bot/helper/themes"):
        if theme.startswith("neo_") and theme.endswith(".py"):
            try:
                AVL_THEMES[theme[4:-3]] = import_module(f"bot.helper.themes.{theme[:-3]}")
                LOGGER.info(f"Loaded theme: {theme[4:-3]}")
            except Exception as e:
                LOGGER.error(f"Failed to load theme {theme}: {e}")
except Exception as e:
    LOGGER.error(f"Error discovering themes: {e}")


def BotTheme(var_name, **format_vars):
    text = None
    theme_ = Config.BOT_THEME

    if theme_ in AVL_THEMES:
        text = getattr(AVL_THEMES[theme_].NeoStyle(), var_name, None)
        if text is None:
            LOGGER.error(
                f"{var_name} not found in {theme_} theme. "
                f"Please recheck with Official Repo. Using neo_minimal fallback."
            )
    elif theme_ == "random":
        rantheme = rchoice(list(AVL_THEMES.values()))
        LOGGER.info(f"Random Theme Chosen: {rantheme.__name__}")
        text = getattr(rantheme.NeoStyle(), var_name, None)

    if text is None:
        text = getattr(neo_minimal.NeoStyle(), var_name, None)
        if text is None:
            LOGGER.error(f"Theme variable {var_name} not found in any theme!")
            return f"[MISSING_THEME: {var_name}]"

    try:
        return text.format_map(format_vars)
    except KeyError as e:
        LOGGER.error(f"Missing format variable {e} in {var_name}")
        return text
    except Exception as e:
        LOGGER.error(f"Error formatting {var_name}: {e}")
        return text


__all__ = ["BotTheme", "AVL_THEMES"]
