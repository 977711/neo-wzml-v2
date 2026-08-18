# This file is a part of NEO-WZML (github.com/irisXDR/NEO-WZML)

from os import path as ospath
from re import sub as re_sub
from bot import LOGGER


async def format_filename(file_name, user_dict, is_leech=False):
    from bot.core.config_manager import Config

    if is_leech:
        prefix_key = "LEECH_PREFIX"
        suffix_key = "LEECH_SUFFIX"
        name_swap_key = "LEECH_NAME_SWAP"
    else:
        prefix_key = "MIRROR_PREFIX"
        suffix_key = "MIRROR_SUFFIX"
        name_swap_key = "MIRROR_NAME_SWAP"

    prefix = (
        user_dict.get(prefix_key, "")
        or getattr(Config, prefix_key, "")
        or ""
    )

    suffix = (
        user_dict.get(suffix_key, "")
        or getattr(Config, suffix_key, "")
        or ""
    )

    name_swap = (
        user_dict.get(name_swap_key, "")
        or getattr(Config, name_swap_key, "")
        or ""
    )

    original_file = file_name

    file_name = re_sub(
        r"www\.[a-zA-Z0-9-]+\.[a-zA-Z]{2,6}",
        "",
        file_name,
    )
    file_name = re_sub(r"^\s*[-_~]+\s*", "", file_name).strip()

    # name_swap: regex |pattern:replacement[:count]
    if name_swap:
        try:
            if not name_swap.startswith("|"):
                name_swap = f"|{name_swap}"

            name_swap = name_swap.replace("\\s", " ")
            patterns = name_swap.split("|")

            name_without_ext = ospath.splitext(file_name)[0]
            file_ext = ospath.splitext(file_name)[1]

            for pattern_str in patterns[1:]:
                args = pattern_str.split(":")

                if len(args) >= 2:
                    pattern = args[0]
                    replacement = args[1]
                    count = int(args[2]) if len(args) > 2 else 0

                    try:
                        name_without_ext = re_sub(
                            pattern,
                            replacement,
                            name_without_ext,
                            count
                        )
                    except Exception as e:
                        LOGGER.error(f"Name Swap pattern error: {e}")
                elif len(args) == 1:
                    try:
                        name_without_ext = re_sub(args[0], "", name_without_ext)
                    except Exception as e:
                        LOGGER.error(f"Name Swap removal error: {e}")

            file_name = name_without_ext + file_ext
            LOGGER.info(f"Applied name_swap: {original_file} -> {file_name}")

        except Exception as e:
            LOGGER.error(f"Name Swap processing error: {e}")
            file_name = original_file

    if prefix:
        prefix_clean = prefix.replace("\\s", " ")
        prefix_check = re_sub(r"<.*?>", "", prefix_clean)

        if not file_name.startswith(prefix_check):
            file_name = f"{prefix_clean}{file_name}"
            LOGGER.debug(f"Applied prefix: {prefix_clean}")

    if suffix:
        suffix_clean = suffix.replace("\\s", " ")
        name_without_ext = ospath.splitext(file_name)[0]
        file_ext = ospath.splitext(file_name)[1]

        file_name = f"{name_without_ext}{suffix_clean}{file_ext}"
        LOGGER.debug(f"Applied suffix: {suffix_clean}")

    return file_name
