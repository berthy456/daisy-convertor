"""Frozen entry point for the PyInstaller build."""

import os
import sys

# The Windows build is windowed, so it can start with no console at all. Started
# from one, these handles are real and the prints land in it.
if sys.stdout is None:
    sys.stdout = open(os.devnull, "w", encoding="utf-8")
if sys.stderr is None:
    sys.stderr = open(os.devnull, "w", encoding="utf-8")

from daisy2telmi.cli import main

if __name__ == "__main__":
    raise SystemExit(main())
