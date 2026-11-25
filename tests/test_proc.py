"""Every child process the app starts is started without a window of its own.

A windowed build has no console, so Windows hands each child that has one - ffmpeg
at a crop, three Pythons at the options page - a console window of its own. Only a
Windows machine shows it, so the rule is read off the source instead.
"""

from __future__ import annotations

import os
import re
import subprocess
from pathlib import Path

from daisy2telmi import proc

PACKAGE = Path(proc.__file__).parent
CALL = re.compile(r"subprocess\.(?:run|Popen)\((?:[^()]|\([^()]*\))*\)", re.S)


def _calls() -> list[tuple[str, str]]:
    found: list[tuple[str, str]] = []
    for path in sorted(PACKAGE.glob("*.py")):
        for call in CALL.findall(path.read_text(encoding="utf-8")):
            found.append((path.name, " ".join(call.split())))
    return found


def test_no_child_process_is_asked_with_a_console_window() -> None:
    missing = [f"{name}: {call[:70]}" for name, call in _calls() if "creationflags" not in call]
    assert missing == []


def test_no_child_output_is_read_through_the_console_code_page() -> None:
    # `text=True` alone decodes with the console's code page, which on a French
    # Windows machine turns an accented file or voice name into gibberish.
    misread = [f"{name}: {call[:70]}" for name, call in _calls() if "text=True" in call and "UTF8" not in call]
    assert misread == []


def test_the_flag_is_a_flag_only_where_windows_reads_it() -> None:
    assert proc.NO_WINDOW == (subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
