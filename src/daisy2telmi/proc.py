"""Child processes that must not open a window of their own.

A windowed build has no console, so Windows hands every child that does have one
- ffmpeg, python, a media player - a console window of its own. The flag says
that window is not wanted; the pipes still carry what the child writes.
"""

from __future__ import annotations

import os
import subprocess

NO_WINDOW = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0

# ffmpeg, pip and the voice lists all write UTF-8. `text=True` alone reads them
# back through the console's own code page on Windows, which turns an accented
# file or voice name into gibberish - and raises before the message is ever
# shown when a byte means nothing in that code page.
UTF8 = dict(encoding="utf-8", errors="replace")
