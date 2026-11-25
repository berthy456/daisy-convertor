"""Frozen entry point for the PyInstaller build."""

import os
import sys


def attach_parent_console() -> None:
    """Give the windowed Windows build the terminal it was started from.

    A `--windowed` build carries no console of its own: started from cmd, `sys.stdout`
    is None and the whole log had nowhere to go. AttachConsole hands back the parent's
    console and CONOUT$ is the handle that writes into it. Started by double-click there
    is no parent console, the call fails, and the log stays in the window's log frame.
    """
    import ctypes

    if ctypes.windll.kernel32.AttachConsole(-1):  # ATTACH_PARENT_PROCESS
        sys.stdout = open("CONOUT$", "w", encoding="utf-8", errors="replace", buffering=1)
        sys.stderr = open("CONOUT$", "w", encoding="utf-8", errors="replace", buffering=1)


if sys.stdout is None or sys.stderr is None:
    if os.name == "nt":
        attach_parent_console()
if sys.stdout is None:
    sys.stdout = open(os.devnull, "w", encoding="utf-8")
if sys.stderr is None:
    sys.stderr = open(os.devnull, "w", encoding="utf-8")

from daisy2telmi.cli import main

if __name__ == "__main__":
    raise SystemExit(main())
