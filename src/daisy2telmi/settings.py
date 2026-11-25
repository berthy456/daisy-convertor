"""Saved options, shared by the command line and the window.

The file is small JSON in the platform's config folder so a GUI can remember a
language and a speech engine between runs; the command line reads it first and
lets flags override it.
"""

from __future__ import annotations

import ctypes
import json
import logging
import os
import re
import subprocess
import sys
from pathlib import Path

from . import __version__
from .i18n import LANGUAGES
from .proc import NO_WINDOW, UTF8

log = logging.getLogger(__name__)

DEFAULTS: dict = {
    "captions": False,
    "cut": "chapter",
    "cover_crop": "center",
    # The folder the last file list was left in, so the next one opens there.
    "last_dir": None,
    "language": "en",
    "announce": "prompt",
    "autoplay": False,
    "card_total": True,
    "card_negative": False,
    "frameless": True,
    "format": "telmi",
    "tts": "auto",
    "voice": None,
    "speed": "normal",
    "expression": "natural",
    "command": None,
    "python": None,
    "clip": None,
    "device": "auto",
    # A whole chapter is not read the way a card's few words are. Empty takes the
    # card's engine, voice, clip and speed.
    "read_voice": None,
    "read_clip": None,
    "read_speed": None,
    "input_dir": None,
    "output_dir": None,
}


# The Windows primary language of each language the app has words for.
_WINDOWS_LANGUAGES = {
    0x01: "ar",
    0x04: "zh",
    0x05: "nl",
    0x07: "de",
    0x09: "en",
    0x0A: "es",
    0x0C: "fr",
    0x0F: "it",
    0x16: "pt",
    0x1F: "tr",
}


def _apple_languages() -> str:
    try:
        return subprocess.run(
            ["defaults", "read", "-g", "AppleLanguages"], capture_output=True, creationflags=NO_WINDOW, **UTF8
        ).stdout
    except OSError:
        return ""


def system_language() -> str:
    """The language the machine itself is set to, when the app has words for it.

    A window opened from a terminal sees LANG; one opened from the desktop does not,
    so each system is asked its own way.
    """
    for name in ("LC_ALL", "LC_MESSAGES", "LANGUAGE", "LANG"):
        found = re.match(r"([a-z][a-z])", (os.environ.get(name) or "").lower())
        if found and found.group(1) in LANGUAGES:
            return found.group(1)
    if sys.platform == "win32":
        return _WINDOWS_LANGUAGES.get(ctypes.windll.kernel32.GetUserDefaultUILanguage() & 0x3F, "en")
    if sys.platform == "darwin":
        # Finder starts an app from launchd, which knows the user's language but
        # leaves it out of the environment.
        found = re.search(r'"([a-z][a-z])', _apple_languages().lower())
        if found and found.group(1) in LANGUAGES:
            return found.group(1)
    return "en"


def config_dir() -> Path:
    if sys.platform == "win32":
        base = Path(os.environ.get("APPDATA", Path.home() / "AppData" / "Roaming"))
    else:
        base = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config"))
    return base / "daisy2telmi"


def config_path() -> Path:
    return config_dir() / "config.json"


def data_dir() -> Path:
    """Where the big downloads go: a gigabyte of speech model does not belong in the config folder."""
    if sys.platform == "win32":
        base = Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData" / "Local"))
    elif sys.platform == "darwin":
        base = Path.home() / "Library" / "Application Support"
    else:
        base = Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local" / "share"))
    return base / "daisy2telmi"


def speech_env(engine: str) -> Path:
    """The Python environment the app builds for one speech engine when asked to.

    Each engine gets its own: Chatterbox and Coqui want different torch versions,
    and one environment holding both spends the install arguing about it.
    """
    return data_dir() / engine


def speech_voices() -> Path:
    """Where the speech voices the app downloads live, next to the models."""
    return data_dir() / "voices"


# The window drew its own bar by default from 1.4 on. A settings file written
# before that carries the old default for it, which no one chose.
_OWN_BAR_FROM = (1, 4)


def _written_before(stamp: object) -> bool:
    if not isinstance(stamp, str):
        return True
    return tuple(int(part) for part in re.split(r"[^0-9]+", stamp) if part) < _OWN_BAR_FROM


def load() -> dict:
    """The saved options, with the machine's own language where nothing was saved."""
    options = dict(DEFAULTS, language=system_language())
    try:
        stored = json.loads(config_path().read_text(encoding="utf-8"))
    except FileNotFoundError:
        return options
    except (OSError, ValueError) as exc:
        log.warning("ignoring unreadable %s: %s", config_path(), exc)
        return options
    if isinstance(stored, dict):
        options.update({key: value for key, value in stored.items() if key in DEFAULTS})
        if _written_before(stored.get("written_by")):
            options["frameless"] = DEFAULTS["frameless"]
    return options


def save(options: dict) -> Path:
    path = config_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {key: options.get(key, DEFAULTS[key]) for key in DEFAULTS}
    payload["written_by"] = __version__
    path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    return path
