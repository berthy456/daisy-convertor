"""Saved options, shared by the command line and the window.

The file is small JSON in the platform's config folder so a GUI can remember a
language and a speech engine between runs; the command line reads it first and
lets flags override it.
"""

from __future__ import annotations

import json
import logging
import os
import sys
from pathlib import Path

log = logging.getLogger(__name__)

DEFAULTS: dict = {
    "cut": "chapter",
    "cover_crop": "center",
    "language": "en",
    "announce": "prompt",
    "autoplay": False,
    "card_total": True,
    "format": "telmi",
    "tts": "chatterbox",
    "voice": None,
    "command": None,
    "python": None,
    "clip": None,
    "device": "auto",
}


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


def chatterbox_env() -> Path:
    """The Python environment the app builds for Chatterbox when asked to."""
    return data_dir() / "chatterbox"


def load() -> dict:
    options = dict(DEFAULTS)
    try:
        stored = json.loads(config_path().read_text(encoding="utf-8"))
    except FileNotFoundError:
        return options
    except (OSError, ValueError) as exc:
        log.warning("ignoring unreadable %s: %s", config_path(), exc)
        return options
    if isinstance(stored, dict):
        options.update({key: value for key, value in stored.items() if key in DEFAULTS})
    return options


def save(options: dict) -> Path:
    path = config_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {key: options.get(key, DEFAULTS[key]) for key in DEFAULTS}
    path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    return path
