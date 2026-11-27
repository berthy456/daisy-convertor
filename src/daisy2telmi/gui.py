"""Tkinter front end: pick a DAISY pack and an output folder, watch the conversion log.

Drop a pack on the window when tkinterdnd2 is installed; otherwise use the browse
buttons. The conversion runs on a worker thread so the window stays responsive.

The Options window holds the language and the speech settings; both are read from
and written to `settings.py`, so the command line sees the same choices.
"""

from __future__ import annotations

import contextlib
import logging
import os
import queue
import random
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import tkinter as tk
import weakref
import webbrowser
from dataclasses import replace
from pathlib import Path
from tkinter import font as tkfont, messagebox, ttk
from urllib.parse import quote_plus

from . import __version__, audio, chapters, covers, emulator, images, settings, tts
from .cli import CAPTION_FORMATS, CARD_FORMATS, FORMATS, convert
from .detector import DaisyPackError, open_pack
from .extractor import CUT_LEVELS, extract
from .i18n import LANGUAGES, Language, text
from .proc import NO_WINDOW
from .telmi_builder import ANNOUNCE_MODES, PAUSE_LEVELS, Announce, card_language, part_title, slugify

try:
    from tkinterdnd2 import DND_FILES, TkinterDnD
except ImportError:  # drag and drop is a bonus, browsing works without it
    DND_FILES = None
    TkinterDnD = None

log = logging.getLogger("daisy2telmi")

ANNOUNCE_KEYS = {mode: f"announce_{mode}" for mode in ANNOUNCE_MODES}

# How short and how long a card playing its own part may play it for.
CARD_SECONDS_LIMITS = (0.5, 30.0)
# The four card letterings the combo offers, as the two settings they stand for.
SPEED_KEYS = {speed: f"speed_{speed}" for speed in tts.SPEEDS}
EXPRESSION_KEYS = {name: f"expression_{name}" for name in tts.EXPRESSIONS}
# Nothing chosen at the top of the window means the book's own language is read off the pack.
# Nothing chosen on the reading page means the book is read with the cards' setting.
READ_SPEED_KEYS = {**SPEED_KEYS, None: "speech_same_as_cards"}
READ_EXPRESSION_KEYS = {**EXPRESSION_KEYS, None: "speech_same_as_cards"}
READ_DEVICE_KEYS = {None: "speech_same_as_cards"}
READ_VOICE_KEYS = {None: "speech_same_as_cards"}


def engine_keys() -> list[str]:
    """Every engine the app can be told to read with: automatic first, silence last.

    The names, not `tts.engines()`: asking which of them is installed starts a
    Python per candidate interpreter, and the window's own engine list has no use
    for the answer.
    """
    return ["auto", *(n for n in tts.ENGINES if n not in (tts.AUTO, tts.OFF)), "off"]

# The crop frame is the chosen player's own screen, drawn at its own size: what sits
# inside it is what the card carries. A frame wider than any screen it previews made
# the window 1200 wide and pushed the log and the buttons off a small one, so the
# width is what the screen leaves over the rest of the window, and the web page draws
# its canvas at the same size.
CROP_WIDTH = 320
CROP_MIN = 200
CROP_ROOM = 420

# Tk has no theme of its own that looks the same on the three platforms, so the
# window brings its own: one flat set of colours, drawn by `clam` everywhere. The
# ground is a dark lavender, the same hue as the icon's magenta so the mark sits on
# it without changing colour; the two sunken frames go one step darker.
PAPER = "#3b3852"
SUNK = "#322f46"
INK = "#eeeaf6"
FIELD = "#484563"
LINE = "#5c5878"
ACCENT = "#d24fc9"
MUTED = "#b3aecb"
ALERT = "#ff8978"
# The colour of the work the app does on the user's behalf: the progress bar, the
# button that starts it, and whatever the window marks as picked. The icon's magenta
# stays on the mark.
NAVY = "#2a5ec7"

# The emulator's machine, drawn by hand: Tk's buttons are rectangles and the
# story-teller's keys are not. A key is one colour while the stage has it switched
# on, another while it is off, and the icon's magenta while it is doing its thing.
MACHINE = "#4a4160"
BEZEL = "#10131a"
DIAL = "#5b4a6e"
DIAL_EDGE = "#8a4f86"
DIAL_INK = "#f0b6e8"
OFF = "#3a3750"
OFF_EDGE = "#4c4866"
OFF_INK = "#6f6a86"
SLOT = "#2a2f3a"
SLOT_INK = "#f2f4f8"


def theme(root: tk.Misc) -> None:
    """Recolour Tk's widgets once, so the window looks the same on Mac and Linux.

    `clam` is the only built-in theme whose widgets can all be recoloured; the
    plain `Text` and `Canvas` widgets sit outside the style system and are painted
    by hand. Tk draws the window's own frame, so its corners are the desktop's.
    """
    style = ttk.Style(root)
    if "clam" not in style.theme_names():
        return
    style.theme_use("clam")
    root.configure(background=PAPER)

    style.configure(".", background=PAPER, foreground=INK)
    for name in ("TFrame", "TLabel", "TCheckbutton", "TRadiobutton", "TLabelframe", "TNotebook"):
        style.configure(name, background=PAPER)
    style.configure("TLabelframe", bordercolor=LINE)
    style.configure("TLabelframe.Label", background=PAPER, foreground=MUTED)
    style.configure("TButton", padding=(10, 5), relief="flat", background=FIELD, bordercolor=LINE)
    style.map(
        "TButton",
        background=[("active", "#565274"), ("disabled", PAPER)],
        foreground=[("disabled", MUTED)],
        bordercolor=[("disabled", PAPER)],
    )
    for name in ("TEntry", "TCombobox", "TSpinbox"):
        style.configure(name, fieldbackground=FIELD, background=FIELD, bordercolor=LINE, padding=4, arrowcolor=INK)
        style.map(
            name,
            bordercolor=[("focus", ACCENT)],
            fieldbackground=[("readonly", FIELD), ("disabled", SUNK)],
            foreground=[("disabled", MUTED)],
        )
    # A readonly combo selects its text in the accent colour, which reads as a
    # button rather than a field; keep it the field's own colours.
    style.map(
        "TCombobox",
        selectbackground=[("readonly", FIELD)],
        selectforeground=[("readonly", INK)],
    )
    # The page marks are the only way to see which half of the speech page is open,
    # so the open one carries the same blue as the work button.
    style.configure("TNotebook", borderwidth=0)
    style.configure("TNotebook.Tab", background=FIELD, foreground=MUTED, padding=(12, 6), borderwidth=0)
    style.map(
        "TNotebook.Tab",
        background=[("selected", NAVY)],
        foreground=[("selected", "#ffffff")],
    )
    style.configure("Treeview", background=SUNK, fieldbackground=SUNK, bordercolor=LINE, rowheight=22)
    style.map("Treeview", background=[("selected", NAVY)], foreground=[("selected", "#ffffff")])
    style.configure("Treeview.Heading", background=FIELD, foreground=MUTED, relief="flat")
    style.configure("TScrollbar", background=FIELD, troughcolor=PAPER, bordercolor=LINE, arrowsize=12, relief="flat")
    style.configure("TProgressbar", background=NAVY, troughcolor=FIELD, bordercolor=LINE)
    # The button that starts the work is the one to find by eye, so it is the widest
    # thing in its row and the only one carrying the work's own blue.
    style.configure(
        "Work.TButton",
        padding=(16, 9),
        background=NAVY,
        bordercolor=NAVY,
        foreground="#ffffff",
        font=("TkDefaultFont", 11, "bold"),
    )
    style.map(
        "Work.TButton",
        background=[("active", "#3a70dd"), ("disabled", FIELD)],
        foreground=[("disabled", MUTED)],
        bordercolor=[("disabled", FIELD)],
    )
    style.configure("TSeparator", background=LINE)
    style.configure("TSizegrip", background=PAPER)
    style.configure("TLabelframe", background=PAPER, bordercolor=LINE)
    style.configure("TLabelframe.Label", background=PAPER, foreground=MUTED, font=("TkDefaultFont", 10, "bold"))
    # The window's own two lines of words: what it is for, under the mark, and the
    # name along the bottom.
    style.configure("Headline.TLabel", foreground=INK, font=("TkDefaultFont", 13, "bold"))
    style.configure("Brand.TLabel", foreground=MUTED)
    style.configure("Warn.TLabel", foreground=ALERT)


# The window's icon, drawn from assets/icon.svg by tools/make_icons.py. A frozen
# binary unpacks the PNG next to this module, which is the same place `__file__`
# points from, so one path covers the checkout, the wheel and the binary.
ICON = Path(__file__).resolve().parent / "assets" / "icon.png"


def set_icon(root: tk.Misc) -> None:
    """Give the window and its dialogs the app icon.

    macOS ignores `iconphoto` and takes the icon from the binary itself, which is
    what `--icon` sets there. The image has to stay referenced: Tkinter deletes the
    Tcl image when the Python object goes away.
    """
    if not ICON.is_file():
        return
    # The image belongs to the interpreter that draws it: a second window made from
    # a second root cannot use one made for the first.
    root._icon = tk.PhotoImage(master=root, file=str(ICON))
    root.iconphoto(True, root._icon)


def system_bar_kept() -> bool:
    """Whether the window has to keep the system's own bar.

    Tk's only way to lose the title bar is `overrideredirect`, and that hands the window
    back to the app: the taskbar entry, the resize edges and the minimise go with it,
    and so does the window manager that places the window, sizes it and repaints what
    it uncovers. macOS is the one system where Tk can hide the bar and leave the window
    managed, so it is the one system that gets a frameless window.
    """
    return sys.platform != "darwin"


def open_url(url: str) -> bool:
    """Hand a link to the machine's own browser, and answer whether anything took it.

    The desktop's opener is asked first: `webbrowser` searches a fixed list of program
    names and answers only True or False, so a button that opens nothing says nothing.
    """
    opener = {"linux": "xdg-open", "darwin": "open"}.get(sys.platform)
    if opener is not None:
        try:
            subprocess.Popen([opener, url], creationflags=NO_WINDOW)
            return True
        except OSError:
            pass
    return webbrowser.open(url)


def is_link(text: str) -> bool:
    """Whether the Image box was given a web address rather than a file.

    One box takes both, so every reader of it has to tell them apart before treating
    the text as a path: Path("https://…") is a perfectly good path to nothing.
    """
    return text.startswith(("http://", "https://"))


# The window's own title block: the mark, the app's name with its version, and what
# the window is for, on the line under the name. Tk cannot round the corners of a
# window it does not draw, so the block draws a rounded card of its own, and sets the
# three controls that stand in for a system bar's at its right end - only when there
# is no system bar to stand in for.
BAR_WIDTH = 500
BAR_INSET = 10
BAR_RADIUS = 12
BAR_MARK = 64
# The card has to hold both the mark it carries and the two lines of words beside it:
# 96 - 2 * 10 = 76 against a 64 mark, a 25-pixel name and a description that wraps to
# two 19-pixel lines in the longest of the ten languages.
BAR_HEIGHT = 96
BAR_NAME_FONT = 15
BAR_VERSION_FONT = 12
BAR_HEADLINE_FONT = 11
BAR_BUTTON = 26
BAR_BUTTON_RADIUS = 8
BAR_GAP = 6
BAR_HOVER = "#565274"
BAR_CLOSE_HOVER = "#8f3b34"

# The mark the pick button carries, and the wheel beside the button
# that starts the work: bigger than the one that says a cover is being looked up,
# because it is the one that says the book is being converted.
PICK_MARK = 18
PICK_MARKS = ("book",)
WORK_WHEEL = 34

# The window opens at a size of its own: Tk asks each widget how big it wants to be,
# hands the total to the window manager and lets it decide. The manager's answer is
# asked for again while the window settles, and not after. The floor is the size the
# window needs to show its bar and the two columns of work under it.
MIN_WIDTH = 900
MIN_HEIGHT = 560
WINDOW_MARGIN = 40
REFIT_WAIT = 50
REFIT_SETTLE = 600

# Every box the window asks the user to fill in is the same width, so the fields read
# as one column instead of a pile of rows of their own.
FIELD_WIDTH = 34

# How many characters the line under the buttons shows: each picked path gets half of
# it, so the line never asks the window for more room than it was given. It is the one
# line with the whole width of the window to itself, so it is read wide.
PATH_READ = 76


class TitleBar(tk.Canvas):
    """The window's own title block: the mark, its name, what it is for, and the
    controls it needs.

    The controls call Tk's own window commands, so they work whether or not the system
    bar is there. They are only drawn when the block has to stand in for that bar: with
    the system bar in place the window already has its buttons, and this one is the
    mark, the name and the description.
    """

    def __init__(self, app: App, parent: tk.Widget) -> None:
        super().__init__(
            parent,
            width=BAR_WIDTH,
            height=BAR_HEIGHT,
            background=PAPER,
            highlightthickness=0,
            borderwidth=0,
        )
        self.app = app
        self.mark = app._mark_photo(BAR_MARK)
        self.headline = app._label(
            self,
            "app_headline",
            background=SUNK,
            foreground=MUTED,
            font=("TkDefaultFont", BAR_HEADLINE_FONT),
        )
        self.headline.place_forget()
        self.frameless = app.frameless
        self._boxes: dict[str, int] = {}
        self._words: dict[str, int] = {}
        self._title_y = 0
        self._press: tuple[int, int] | None = None
        self.bind("<Configure>", lambda _event: self.redraw())
        self.redraw()

    def redraw(self) -> None:
        width = self.winfo_width()
        if width <= 1:
            width = int(self.cget("width"))
        height = self.winfo_height()
        if width <= 2 * (BAR_INSET + BAR_RADIUS):
            return
        self.delete("all")
        self._boxes.clear()
        self._words.clear()
        card = self._rounded(
            BAR_INSET, BAR_INSET, width - BAR_INSET, height - BAR_INSET, BAR_RADIUS, SUNK
        )
        if self.mark:
            self.create_image(BAR_INSET + BAR_RADIUS, height // 2, image=self.mark, anchor="w")
        left = BAR_INSET + BAR_RADIUS + BAR_MARK + 14
        # The words stop where the controls start, so a description long in its own
        # language wraps inside the card instead of running under its buttons.
        room = width - left - BAR_INSET - BAR_RADIUS - len(self.app.bar_controls()) * (
            BAR_BUTTON + BAR_GAP
        )
        self._title_y = BAR_INSET + 6
        self._words["title"] = self.create_text(
            left,
            self._title_y,
            anchor="nw",
            text=self.app.t("app_title"),
            fill=INK,
            font=("TkDefaultFont", BAR_NAME_FONT, "bold"),
        )
        title = self.bbox(self._words["title"])
        self._words["version"] = self.create_text(
            title[2] + 10,
            self._title_y,
            anchor="nw",
            text=__version__,
            fill=MUTED,
            font=("TkDefaultFont", BAR_VERSION_FONT),
        )
        # The description is the block's second line, and the only part of it that can
        # need one: it wraps at the room the words have, so a language with long words
        # takes a second line rather than running under the controls.
        self.headline.configure(wraplength=room)
        self.headline.place(x=left, y=title[3] + 2, anchor="nw")
        self._controls(width, height)
        if self.frameless:
            # With no system bar to pull on, the block's own card and words move the window.
            for item in (card, *self._words.values()):
                self.tag_bind(item, "<ButtonPress-1>", self._press_bar)
                self.tag_bind(item, "<B1-Motion>", self._drag_bar)

    def relabel(self) -> None:
        self.itemconfigure(self._words["title"], text=self.app.t("app_title"))
        title = self.bbox(self._words["title"])
        if title:
            self.coords(self._words["version"], title[2] + 10, self._title_y)

    def _controls(self, width: int, height: int) -> None:
        keys = self.app.bar_controls()
        right = width - BAR_INSET - BAR_RADIUS
        middle = height // 2
        for index, key in enumerate(reversed(keys)):
            x = right - BAR_BUTTON - index * (BAR_BUTTON + BAR_GAP)
            tag = f"bar-{key}"
            self._boxes[key] = self._rounded(
                x,
                middle - BAR_BUTTON // 2,
                x + BAR_BUTTON,
                middle + BAR_BUTTON // 2,
                BAR_BUTTON_RADIUS,
                FIELD,
                tag,
            )
            self._glyph(key, x, middle, tag)
            self.tag_bind(tag, "<Button-1>", lambda _event, name=key: self.app.bar_action(name))
            self.tag_bind(tag, "<Enter>", lambda _event, name=key: self._hover(name, True))
            self.tag_bind(tag, "<Leave>", lambda _event, name=key: self._hover(name, False))

    def _glyph(self, key: str, x: int, middle: int, tag: str) -> None:
        left, right = x + 9, x + BAR_BUTTON - 9
        top, bottom = middle - 7, middle + 7
        if key == "hide":
            self.create_line(left, bottom, right, bottom, fill=INK, width=2, tags=tag)
        elif key == "zoom":
            self.create_rectangle(left, top, right, bottom, outline=INK, width=2, tags=tag)
        else:
            self.create_line(left, top, right, bottom, fill=INK, width=2, tags=tag)
            self.create_line(right, top, left, bottom, fill=INK, width=2, tags=tag)

    def _hover(self, key: str, on: bool) -> None:
        if not self.winfo_exists():
            return
        colour = (BAR_CLOSE_HOVER if key == "close" else BAR_HOVER) if on else FIELD
        self.itemconfigure(self._boxes[key], fill=colour)

    def _press_bar(self, event: tk.Event) -> None:
        root = self.app.root
        self._press = (event.x_root - root.winfo_rootx(), event.y_root - root.winfo_rooty())

    def _drag_bar(self, event: tk.Event) -> None:
        if self._press is None:
            return
        x = max(0, event.x_root - self._press[0])
        y = max(0, event.y_root - self._press[1])
        self.app.root.geometry(f"+{x}+{y}")

    def _rounded(
        self, x0: int, y0: int, x1: int, y1: int, radius: int, fill: str, tag: str = ""
    ) -> int:
        return self.create_polygon(
            x0 + radius,
            y0,
            x1 - radius,
            y0,
            x1,
            y0 + radius,
            x1,
            y1 - radius,
            x1 - radius,
            y1,
            x0 + radius,
            y1,
            x0,
            y1 - radius,
            x0,
            y0 + radius,
            smooth=True,
            fill=fill,
            outline="",
            tags=tag,
        )


class Wheel(tk.Canvas):
    """A wheel that turns while the app works, and stops when it is done.

    A bar says nothing here: a conversion has no measurable end to measure against,
    and an indeterminate bar filling up over and over reads like progress that goes
    nowhere. A turning wheel only says what is actually known - that something is
    happening.
    """

    SIZE = 22
    SPOKES = 8
    STEP = 45

    def __init__(self, parent: tk.Widget, size: int = SIZE) -> None:
        super().__init__(
            parent,
            width=size,
            height=size,
            background=PAPER,
            highlightthickness=0,
            borderwidth=0,
        )
        self.size = size
        self._turn = 0
        self._job: str | None = None
        self.redraw()

    def redraw(self) -> None:
        self.delete("all")
        middle = self.size / 2
        reach = middle - 2
        self.create_arc(
            middle - reach,
            middle - reach,
            middle + reach,
            middle + reach,
            start=self._turn * self.STEP,
            extent=270,
            style="arc",
            outline=NAVY,
            width=max(3, self.size // 10),
        )

    def start(self, interval: int = 90) -> None:
        if self._job is not None:
            return
        self._spin(interval)

    def _spin(self, interval: int) -> None:
        self._turn = (self._turn + 1) % self.SPOKES
        self.redraw()
        self._job = self.after(interval, self._spin, interval)

    def stop(self) -> None:
        if self._job is not None:
            self.after_cancel(self._job)
            self._job = None


class _QueueHandler(logging.Handler):
    """Ship log records to the Tk thread; widgets are only touched from the main thread."""

    def __init__(self, sink: queue.Queue, level: int = logging.INFO) -> None:
        super().__init__(level)
        self.sink = sink

    def emit(self, record: logging.LogRecord) -> None:
        self.sink.put_nowait(self.format(record))


def _tag(line: str) -> tuple[str, ...]:
    """Which of the log's emphases a line gets, however the report indents it."""
    head = line.strip().split(":", 1)[0]
    if head in ("ERROR", "CRITICAL"):
        return ("error",)
    if head == "DEBUG":
        return ("detail",)
    return ("report",) if head == "REPORT" else ()


def _file_size(size: int) -> str:
    """A size the eye can read, the same way a file manager writes one."""
    for unit, scale in (("GB", 1024**3), ("MB", 1024**2), ("KB", 1024)):
        if size >= scale:
            return f"{size / scale:.1f} {unit}"
    return f"{size} B"


class Browser:
    """The file list the app draws itself.

    Tk's dialog is the system's own: its height, its single column of rows and the
    folder it opens in are the system's business and none of them can be changed.
    The app wants a window twice as tall, three columns, and the folder the last
    one ended in.
    """

    WIDTH = 1100
    HEIGHT = 680

    def __init__(self, app: App, *, title: str, folder: Path, pick: str) -> None:
        """`pick` says which of the two the window hands back: a file or a folder."""
        self.app = app
        self.pick = pick
        self.current = folder
        self.chosen: str | None = None
        self.sort_column = "name"
        self.descending = False

        self.top = tk.Toplevel(app.root)
        self.top.title(title)
        self.top.configure(background=PAPER)
        self.top.geometry(f"{self.WIDTH}x{self.HEIGHT}")
        self.top.minsize(640, 360)
        self.top.transient(app.root)
        self.top.columnconfigure(0, weight=1)
        self.top.rowconfigure(1, weight=1)
        if hasattr(app.root, "_icon"):
            self.top.iconphoto(False, app.root._icon)

        nav = ttk.Frame(self.top, padding=(12, 10, 12, 0))
        nav.grid(row=0, column=0, sticky="ew")
        nav.columnconfigure(1, weight=1)
        ttk.Button(nav, text=app.t("browser_up"), command=self._up).grid(row=0, column=0)
        self.path = ttk.Entry(nav)
        self.path.grid(row=0, column=1, sticky="ew", padx=8)
        self.path.bind("<Return>", lambda _event: self._enter(self.path.get().strip()))
        self.filter_var = tk.StringVar()
        ttk.Label(nav, text=app.t("browser_filter")).grid(row=0, column=2)
        box = ttk.Entry(nav, textvariable=self.filter_var, width=16)
        box.grid(row=0, column=3, padx=(6, 0))
        box.bind("<KeyRelease>", lambda _event: self._list())

        style = ttk.Style(self.top)
        style.configure("Browser.Treeview", rowheight=26, font=("TkDefaultFont", 11))
        style.configure("Browser.Treeview.Heading", font=("TkDefaultFont", 11, "bold"))
        listing = ttk.Frame(self.top)
        listing.grid(row=1, column=0, sticky="nsew", padx=12, pady=8)
        listing.columnconfigure(0, weight=1)
        listing.rowconfigure(0, weight=1)
        self.tree = ttk.Treeview(
            listing,
            columns=("name", "kind", "size"),
            show="headings",
            style="Browser.Treeview",
            selectmode="browse",
        )
        for column, key, width in (("name", "browser_name", 620), ("kind", "browser_kind", 130), ("size", "browser_size", 110)):
            self.tree.heading(column, text=app.t(key), command=lambda chosen=column: self._sort(chosen))
            self.tree.column(column, width=width, minwidth=70, stretch=column == "name", anchor="w" if column == "name" else "e")
        bar = ttk.Scrollbar(listing, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=bar.set)
        self.tree.grid(row=0, column=0, sticky="nsew")
        bar.grid(row=0, column=1, sticky="ns")
        self.tree.bind("<Double-1>", lambda _event: self._pick_selected())
        self.tree.bind("<Return>", lambda _event: self._pick_selected())
        self.tree.bind("<<TreeviewSelect>>", lambda _event: self.choose.state(["!disabled"]))

        foot = ttk.Frame(self.top, padding=(12, 0, 12, 12))
        foot.grid(row=2, column=0, sticky="ew")
        foot.columnconfigure(0, weight=1)
        self.status = ttk.Label(foot, foreground=ALERT)
        self.status.grid(row=0, column=0, sticky="w")
        self.choose = ttk.Button(
            foot,
            text=app.t("browser_open" if pick == "file" else "browser_pick_folder"),
            command=self._accept,
        )
        self.choose.grid(row=0, column=1, padx=(8, 0))
        if pick == "folder":
            self.choose.state(["!disabled"])
        else:
            self.choose.state(["disabled"])
        ttk.Button(foot, text=app.t("browser_cancel"), command=self.top.destroy).grid(
            row=0, column=2, padx=(6, 0)
        )

        self.top.bind("<Escape>", lambda _event: self.top.destroy())
        self._show(folder)

    def run(self) -> str:
        """Wait for the window the way Tk's own dialogs do, and give the choice back."""
        self.top.wait_window(self.top)
        return self.chosen or ""

    def _show(self, folder: Path) -> None:
        self.current = folder
        self.path.delete(0, "end")
        self.path.insert(0, str(folder))
        self.status.configure(text="")
        self._list()

    def _enter(self, typed: str) -> None:
        folder = Path(typed).expanduser()
        if not folder.is_dir():
            self.status.configure(text=str(folder))
            return
        self._show(folder)

    def _up(self) -> None:
        self._show(self.current.parent)

    def _sort(self, column: str) -> None:
        if self.sort_column == column:
            self.descending = not self.descending
        else:
            # The first click on the sizes asks for the biggest first; the other
            # two columns read top to bottom the other way.
            self.sort_column = column
            self.descending = column == "size"
        self._list()

    def _list(self) -> None:
        self.tree.delete(*self.tree.get_children())
        needle = self.filter_var.get().strip().lower()
        try:
            scanned = list(os.scandir(self.current))
        except OSError as exc:
            self.status.configure(text=str(exc))
            return
        rows: list[tuple[str, bool, int]] = []
        for entry in scanned:
            if entry.name.startswith(".") and not needle:
                continue
            if needle and needle not in entry.name.lower():
                continue
            is_dir = entry.is_dir()
            try:
                size = 0 if is_dir else entry.stat().st_size
            except OSError:
                size = 0
            rows.append((entry.name, is_dir, size))
        keys = {
            "name": lambda row: row[0].lower(),
            "kind": lambda row: (not row[1], row[0].lower()),
            "size": lambda row: (not row[1], -row[2], row[0].lower()),
        }
        rows.sort(key=keys[self.sort_column], reverse=self.descending)
        for name, is_dir, size in rows:
            self.tree.insert(
                "",
                "end",
                values=(name, self.app.t("kind_folder" if is_dir else "kind_file"), "" if is_dir else _file_size(size)),
            )

    def _selected(self) -> Path | None:
        chosen = self.tree.selection()
        if not chosen:
            return None
        return self.current / self.tree.item(chosen[0], "values")[0]

    def _pick_selected(self) -> None:
        """A folder is walked into; a file ends the window when the app asked for one."""
        path = self._selected()
        if path is None:
            return
        if path.is_dir():
            self._show(path)
        elif self.pick == "file":
            self.chosen = str(path)
            self.top.destroy()

    def _accept(self) -> None:
        if self.pick == "folder":
            self.chosen = str(self.current)
            self.top.destroy()
            return
        path = self._selected()
        if path is not None:
            self.chosen = str(path)
            self.top.destroy()


class App:
    def __init__(self, root: tk.Misc, options: dict | None = None) -> None:
        self.root = root
        self.events: queue.Queue = queue.Queue()
        self.options = dict(settings.DEFAULTS, **(options or settings.load()))
        self.windowingsystem = root.tk.call("tk", "windowingsystem")
        self.frameless = bool(self.options.get("frameless")) and not system_bar_kept()
        self._maximised = False
        self._restore_geometry = ""
        self.pack_var = tk.StringVar()
        self.out_var = tk.StringVar(value=self.options.get("output_dir") or "")
        # What the last pack's lookup filled in. A cover, an author and a title the
        # services gave belong to the pack they were found for; only what the user
        # typed or picked is a setting that carries to the next pack.
        self._auto_cover = ""
        self._auto_author = ""
        self._auto_title = ""
        theme(root)
        set_icon(root)
        self.pack_var.trace_add("write", self._follow_pack)
        self.out_var.trace_add("write", lambda *_: self._show_paths())
        self.cover_var = tk.StringVar()
        self.author_var = tk.StringVar()
        self.title_var = tk.StringVar()
        # A looked-up cover has to stay on disk while the cards are drawn, so the
        # session keeps one folder for them and each lookup replaces the last.
        self._cover_dir: Path | None = None
        self._cover_job = 0
        # Which ask the newest cover answer answers: the pack's own metadata, the
        # words the user typed, or a link they pasted.
        self._cover_asked = "pack"
        # The run asked for while the Image box still held a link, kept until the
        # picture lands: one press of the button does the fetch and the conversion.
        self._cover_then = None
        self._spinning = False
        self._pack_title: str | None = None
        # What the pack's own reading gave the preview: how many parts it has at
        # chapter level, the first one's name, and the language its cards are lettered in.
        self._part_count: int | None = None
        self._part_title = ""
        self._part_code = ""
        self.story_language_keys: list[str | None] = [None, *LANGUAGES]
        self.crop_var = tk.StringVar(value=self.options["cover_crop"])
        self.format_var = tk.StringVar(value=self.options["format"])
        # The cover drawn at the frame's ratio: the file it came from, the player it
        # was drawn for, the zoom it was drawn at, then the picture and its size.
        self._crop_cover: tuple[str, str, float, bool, float, tuple[bytes, tuple[int, int]]] | None = None
        self._crop_photo: tk.PhotoImage | None = None
        # The card's lettering over that picture: the words it was drawn for, then the
        # picture itself.
        self._crop_osd: tuple[str, tuple[int, int], str, bool] | None = None
        self._osd_photo: tk.PhotoImage | None = None
        self._crop_zoom_value = 1.0
        self._crop_focus: tuple[float, float] | None = None
        self._crop_drag: tuple[int, int, tuple[float, float]] | None = None
        self._crop_redraw = None
        self.force_var = tk.BooleanVar(value=False)
        self.language_var = tk.StringVar(value=self.options["language"])
        self.cut = self.options["cut"]
        self.dropped: set[int] = set()
        self._pack_path: Path | None = None
        # The chapters window lists the parts at its own cut, so it has to follow a
        # cut the main window changes under it.
        self.chapter_list: ChapterList | None = None
        # A test run writes the story into a folder the app made and keeps it, so the
        # Save button can write that same pack without running the converter again.
        self._test_folder: Path | None = None
        self._test_key: tuple | None = None
        # The player a card test started, so the next test can silence it.
        self._card_player: subprocess.Popen | None = None
        self._on_done = None
        self._labels: dict[str, tk.Widget] = {}
        self._pick_marks = {name: self._mark_photo(PICK_MARK, f"pick-{name}") for name in PICK_MARKS}
        self._crop_width = self._crop_room()
        self._build()
        self._show_paths()
        self._size_window()
        if self.frameless:
            self._take_system_bar()
        root.after(100, self._drain)

    def t(self, key: str) -> str:
        return text(self.language_var.get(), key)

    def _label(self, parent: tk.Widget, key: str, **kwargs) -> ttk.Label:
        widget = ttk.Label(parent, text=self.t(key), **kwargs)
        self._labels[key] = widget
        return widget

    def _mark_photo(self, side: int, name: str = "icon") -> tk.PhotoImage | None:
        """The mark at the size a frame asks for. Tk cannot scale a photo image, so
        tools/make_icons.py writes one PNG for each mark and size the window draws."""
        path = ICON.parent / f"{name}-{side}.png"
        return tk.PhotoImage(master=self.root, file=str(path)) if path.is_file() else None

    def _button(self, parent: tk.Widget, key: str, command, **kwargs) -> ttk.Button:
        widget = ttk.Button(parent, text=self.t(key), command=command, **kwargs)
        self._labels[key] = widget
        return widget

    def _pick_button(self, parent: tk.Widget, key: str, command, glyph: str) -> ttk.Button:
        """A button that opens something, drawn with the mark of the thing it picks:
        the book, a folder of them, the folder a story is written into, the machine
        that plays it."""
        widget = self._button(parent, key, command, compound="left")
        mark = self._pick_marks.get(glyph)
        if mark:
            widget.configure(image=mark)
        return widget

    def _build(self) -> None:
        self.root.columnconfigure(0, weight=1)
        self.root.rowconfigure(1, weight=1)

        # The window's own title block stands at the top left - the mark, the name with
        # its version, and what the window is for on the line under it. At the top right
        # stand the button that picks the book and the language the book is read in. The
        # two folders - where the packs are and where a story is written - are app
        # settings, so they are set with the others. The line under them has the whole
        # width of the window to be read in - a path is the one thing here that is never
        # short. Nothing is typed: a pack is dropped on the window or picked from a list.
        head = ttk.Frame(self.root, padding=(12, 12, 12, 0))
        head.grid(row=0, column=0, sticky="ew")
        head.columnconfigure(1, weight=1)
        self.bar = TitleBar(self, head)
        self.bar.grid(row=0, column=0, sticky="w")
        picks = ttk.Frame(head)
        picks.grid(row=0, column=1, sticky="e")
        self._pick_button(picks, "browse_zip", self._pick_pack_file, "book").pack(side="left", padx=(6, 0))
        self._label(picks, "opt_story_language").pack(side="left", padx=(12, 6))
        self.story_language_box = ttk.Combobox(
            picks, values=self._story_language_labels(), state="readonly", width=FIELD_WIDTH
        )
        self.story_language_box.pack(side="left")
        self.story_language_box.bind("<<ComboboxSelected>>", lambda _event: self._choose_story_language())
        self._show_story_language()
        self._paths_line = ttk.Label(head, text="", style="Brand.TLabel")
        self._paths_line.grid(row=1, column=0, columnspan=2, sticky="w", pady=(6, 0))

        # Two pages: one for what the cards say and how long they play, one for
        # everything the screen shows. The log is in neither - it runs along the bottom
        # of the window, as wide as the window is, because a log read one column at a
        # time is not read.
        grid = ttk.Frame(self.root, padding=(12, 8))
        grid.grid(row=1, column=0, sticky="nsew")
        grid.rowconfigure(0, weight=1)

        pages = ttk.Notebook(grid)
        pages.grid(row=0, column=0, sticky="nsew")
        self.pages = pages
        audio = ttk.Frame(pages, padding=(10, 8))
        audio.columnconfigure(1, weight=1)
        display = ttk.Frame(pages, padding=(10, 8))
        self.display_page = display
        pages.add(audio, text=self.t("page_audio"))
        pages.add(display, text=self.t("page_display"))

        # Every field on one grid: the label above its box, every box the same width,
        # every button in the same column, so the whole page reads down one edge.
        fields = ttk.Frame(audio)
        fields.grid(row=0, column=0, sticky="nw")

        # The voice the cards get is picked here, next to the machine they are read
        # on; everything that goes with it - voices, installs, a sample to clone -
        # sits behind its own button, on a page of its own.
        self._label(fields, "speech_label").grid(row=0, column=0, sticky="w")
        self.speech_box = ttk.Combobox(fields, values=self.engine_names(), state="readonly", width=FIELD_WIDTH)
        self.speech_box.grid(row=1, column=0, sticky="w")
        self.speech_box.bind("<<ComboboxSelected>>", lambda _event: self._choose_engine())
        self._button(fields, "speech_configure", self._open_speech).grid(row=1, column=1, sticky="w", padx=(6, 0))
        self._show_engines()

        # What a card says out loud, and how long it plays when it has nothing of its
        # own to say: the two answers that decide the audio of every chapter.
        self._label(fields, "opt_announce").grid(row=2, column=0, sticky="w", pady=(6, 0))
        self.announce_keys = list(ANNOUNCE_MODES)
        self.announce_box = ttk.Combobox(fields, values=self.announce_names(), state="readonly", width=FIELD_WIDTH)
        self.announce_box.grid(row=3, column=0, sticky="w")
        self.announce_box.bind("<<ComboboxSelected>>", lambda _event: self._choose_announce())
        self.announce_try_button = self._button(fields, "announce_try", self._try_announce)
        self.announce_try_button.grid(row=3, column=1, sticky="w", padx=(8, 0))
        self._label(fields, "opt_card_seconds").grid(row=4, column=0, sticky="w", pady=(6, 0))
        self.seconds_box = ttk.Entry(fields, width=6)
        self.seconds_box.insert(0, f"{self.options['card_seconds']:g}")
        self.seconds_box.grid(row=5, column=0, sticky="w")
        for event in ("<Return>", "<FocusOut>"):
            self.seconds_box.bind(event, lambda _e: self._type_seconds())
        self._show_announce()

        # Where the story waits for OK. Without autoplay there is nothing to choose:
        # such a story waits at its chapters, however it was cut.
        self.autoplay_var = tk.BooleanVar(value=bool(self.options["autoplay"]))
        self.autoplay_box = ttk.Checkbutton(
            fields, text=self.t("opt_autoplay"), variable=self.autoplay_var, command=self._choose_autoplay
        )
        self.autoplay_box.grid(row=6, column=0, sticky="w", pady=(8, 0))
        self._labels["opt_autoplay"] = self.autoplay_box
        self.pause_keys = list(PAUSE_LEVELS)
        self._label(fields, "opt_pause").grid(row=5, column=1, sticky="w", padx=(8, 0))
        self.pause_box = ttk.Combobox(fields, values=self.pause_names(), state="readonly", width=12)
        self.pause_box.grid(row=6, column=1, sticky="w", padx=(8, 0))
        self.pause_box.bind("<<ComboboxSelected>>", lambda _event: self._choose_pause())
        self._show_pause()

        # The cover and the card's lettering stand in the column beside the picture:
        # stacked under it they make the page taller than any screen they open on.
        right = ttk.Frame(display)
        right.grid(row=0, column=1, sticky="n", padx=(12, 0))
        cover_card = ttk.Labelframe(right, text=self.t("cover_section"), padding=(10, 8))
        cover_card.grid(row=0, column=0, sticky="w")
        cover_card.columnconfigure(1, weight=1)
        self.cover_card = cover_card
        self._labels["cover_section"] = cover_card
        self._label(cover_card, "cover_label").grid(row=0, column=0, sticky="w")
        cover_entry = ttk.Entry(cover_card, textvariable=self.cover_var, width=FIELD_WIDTH)
        cover_entry.grid(row=1, column=0, sticky="w")
        cover_entry.bind("<Return>", self._cover_typed)
        cover_entry.bind("<FocusOut>", self._cover_typed)
        self._button(cover_card, "browse_cover", self._pick_cover).grid(row=1, column=1, sticky="w", padx=(6, 0))
        # A lookup takes a second or two on its own thread, and four services are
        # asked before the answer says there is nothing: the window has to say it is
        # waiting, and let the user call it off. It gets the line under the box -
        # the wheel, its sentence and the Stop button are wider than the box is.
        cover_wait = ttk.Frame(cover_card)
        cover_wait.grid(row=2, column=0, columnspan=2, sticky="w", pady=(4, 0))
        self.cover_spin = Wheel(cover_wait)
        self.cover_note = self._label(cover_wait, "cover_searching")
        self.cover_stop = self._button(cover_wait, "cover_stop", self._stop_cover)

        # When none of those services has the book, the two words the user types
        # are the search: the app's own catalogues first, then an image search on
        # either of the two engines that answer without an account - those two
        # only open a browser, so their answer is a picture the user saves. The
        # button comes after the two boxes it reads, on their line.
        search = ttk.Frame(cover_card)
        search.grid(row=3, column=0, columnspan=2, sticky="w", pady=(8, 0))
        words = ttk.Frame(search)
        words.pack(anchor="w")
        self._label(words, "cover_author").pack(side="left")
        ttk.Entry(words, textvariable=self.author_var, width=12).pack(side="left", padx=(6, 12))
        self._label(words, "cover_book_title").pack(side="left")
        ttk.Entry(words, textvariable=self.title_var, width=16).pack(side="left", padx=(6, 12))
        self._button(words, "cover_search_app", self._search_words).pack(side="left")
        # The two engines that only open a browser are a second line rather than a
        # wider one: a row this wide would push the log off the window.
        engines = ttk.Frame(search)
        engines.pack(anchor="w", pady=(4, 0))
        self._label(engines, "cover_search_on").pack(side="left")
        self._button(engines, "cover_search_ddg", lambda: self._search_cover("ddg")).pack(side="left", padx=(6, 0))
        self._button(engines, "cover_search_google", lambda: self._search_cover("google")).pack(
            side="left", padx=(4, 0)
        )
        # Turning the picture inside out is a thing a grey screen does, so the box is
        # only live next to the format that has one.
        self.negative_var = tk.BooleanVar(value=bool(self.options["card_negative"]))
        self.negative_box = ttk.Checkbutton(
            cover_card, text=self.t("card_negative"), variable=self.negative_var, command=self._pick_negative
        )
        self._labels["card_negative"] = self.negative_box
        self.negative_box.grid(row=4, column=0, columnspan=2, sticky="w", pady=(10, 0))
        self._label(cover_card, "card_negative_hint", wraplength=440).grid(
            row=5, column=0, columnspan=2, sticky="w", pady=(2, 0)
        )

        # The frame and its knobs are one card: what changes the picture stands next to
        # it, not in the next column over. It takes the column the log used to have,
        # and the words that explain the knobs go under the picture, where they are
        # read after the thing they describe rather than beside it.
        preview = ttk.Frame(display)
        preview.grid(row=0, column=0, sticky="nw")
        self.crop_preview = preview
        crop_top = ttk.Frame(preview)
        crop_top.pack(anchor="n")
        # The picture and the words under it are one column: the advice is read against
        # the frame it describes, so it follows the frame's own bottom rather than the
        # tallest thing in the row.
        crop_left = ttk.Frame(crop_top)
        crop_left.pack(side="left", anchor="n")

        # The frame the chosen player shows, drawn from the cover: drag it to move,
        # the wheel or the arrows to enlarge. What is inside here is what the story
        # carries, so the choice is made on the picture instead of described to it.
        self.crop_frame = tk.Canvas(
            crop_left, highlightthickness=0, cursor="fleur", takefocus=1, background=SUNK
        )
        self.crop_frame.pack(anchor="n")
        self._crop_item = self.crop_frame.create_image(0, 0, anchor="nw")
        # The card's own lettering, laid over the picture it will be drawn on.
        self._osd_item = self.crop_frame.create_image(0, 0, anchor="nw")
        self.crop_frame.bind("<ButtonPress-1>", self._grab_crop)
        self.crop_frame.bind("<B1-Motion>", self._drag_crop)
        self.crop_frame.bind("<ButtonRelease-1>", lambda _e: setattr(self, "_crop_drag", None))
        self.crop_frame.bind("<MouseWheel>", self._wheel_crop)
        self.crop_frame.bind("<Button-4>", lambda _e: self._zoom_crop(1.25))
        self.crop_frame.bind("<Button-5>", lambda _e: self._zoom_crop(0.8))
        for key, step in (
            ("<KeyPress-Left>", (1, 0)),
            ("<KeyPress-Right>", (-1, 0)),
            ("<KeyPress-Up>", (0, 1)),
            ("<KeyPress-Down>", (0, -1)),
        ):
            self.crop_frame.bind(key, lambda event, move=step: self._step_crop(*move))
        self.crop_frame.bind("<KeyPress-plus>", lambda _e: self._zoom_crop(1.2))
        self.crop_frame.bind("<KeyPress-equal>", lambda _e: self._zoom_crop(1.2))
        self.crop_frame.bind("<KeyPress-minus>", lambda _e: self._zoom_crop(1 / 1.2))

        # Everything that changes what the frame shows stands beside it. Which band of
        # a tall cover survives is not asked by name any more - it is the middle, and
        # the frame is moved off it - so what is offered is the move itself: a cross of
        # arrows, one per edge, doing what the keyboard arrows already do.
        side = ttk.Frame(crop_top)
        side.pack(side="left", anchor="n", padx=(12, 0))
        self.crop_side = side
        self._label(side, "cover_crop_label").pack(anchor="w")
        pad = ttk.Frame(side)
        pad.pack(anchor="w", pady=(4, 0))
        self.crop_pad = pad
        self.crop_arrows: dict[str, ttk.Button] = {}
        for key, glyph, row, column, step in (
            ("up", "↑", 0, 1, (0, 1)),
            ("left", "←", 1, 0, (1, 0)),
            ("right", "→", 1, 2, (-1, 0)),
            ("down", "↓", 2, 1, (0, -1)),
        ):
            button = ttk.Button(pad, text=glyph, width=3, command=lambda move=step: self._step_crop(*move))
            button.grid(row=row, column=column, padx=1, pady=1)
            self.crop_arrows[key] = button

        # How far in is a number worth typing: a cover that has to land on one band is
        # asked for as a percentage, and the two buttons step it the way the wheel does.
        zoom = ttk.Frame(side)
        zoom.pack(anchor="w", pady=(8, 0))
        self._label(zoom, "crop_zoom").pack(side="left", padx=(0, 6))
        self.crop_zoom_out = ttk.Button(zoom, text="−", width=3, command=lambda: self._zoom_crop(1 / 1.2))
        self.crop_zoom_out.pack(side="left")
        self.crop_zoom_var = tk.StringVar()
        self.crop_zoom_box = ttk.Entry(zoom, textvariable=self.crop_zoom_var, width=5, justify="center")
        self.crop_zoom_box.pack(side="left", padx=2)
        self.crop_zoom_in = ttk.Button(zoom, text="+", width=3, command=lambda: self._zoom_crop(1.2))
        self.crop_zoom_in.pack(side="left")
        for event in ("<Return>", "<FocusOut>"):
            self.crop_zoom_box.bind(event, lambda _e: self._type_crop_zoom())
        self._show_crop_zoom()
        self._button(side, "crop_reset", self._reset_crop).pack(anchor="w", pady=(8, 0))

        # How far apart the picture's lights and shadows are pulled. The player's
        # screen is small and its greys are not a monitor's, so a cover that reads on
        # this one can be mud on that one.
        self._label(side, "contrast").pack(anchor="w", pady=(10, 0))
        self.contrast_var = tk.IntVar(value=int(round(100 * float(self.options["card_contrast"]))))
        self.contrast_box = ttk.Scale(
            side,
            from_=50,
            to=150,
            orient="horizontal",
            length=150,
            variable=self.contrast_var,
            command=lambda _value: self._pick_contrast(),
        )
        self.contrast_box.pack(anchor="w", pady=(2, 0))
        self.cover_var.trace_add("write", lambda *_: self._show_crop())

        # The words that explain the knobs read after the picture they describe, so they
        # sit under it, as wide as it is, rather than beside it.
        self._label(crop_left, "crop_hint", wraplength=self._crop_width - 24, justify="center").pack(
            anchor="center", pady=(8, 0)
        )

        # What the card is lettered with, read as the card will read: the line first,
        # then the line under it.
        osd = ttk.Labelframe(right, text=self.t("card_text_section"), padding=(10, 8))
        osd.grid(row=1, column=0, sticky="w", pady=(12, 0))
        self.osd_card = osd
        self._labels["card_text_section"] = osd
        self._label(osd, "card_text_label").grid(row=0, column=0, sticky="w")
        self.card_text_box = ttk.Combobox(osd, state="readonly", width=22)
        self.card_text_box.grid(row=1, column=0, sticky="w")
        self.card_text_box.bind("<<ComboboxSelected>>", lambda _e: self._choose_card_text())
        self._label(osd, "card_text_next").grid(row=2, column=0, sticky="w", pady=(6, 0))
        self.card_title_box = ttk.Combobox(osd, state="readonly", width=22)
        self.card_title_box.grid(row=3, column=0, sticky="w")
        self.card_title_box.bind("<<ComboboxSelected>>", lambda _e: self._choose_card_title())
        self._show_card_text()
        # A part's own words on screen is a thing a big screen does, so this box is
        # only live next to the two formats whose screen can hold a sentence.
        self.captions_var = tk.BooleanVar(value=bool(self.options["captions"]))
        self.captions_box = ttk.Checkbutton(
            osd, text=self.t("captions"), variable=self.captions_var, command=self._pick_captions
        )
        self._labels["captions"] = self.captions_box
        self.captions_box.grid(row=4, column=0, sticky="w", pady=(10, 0))
        self._label(osd, "captions_warning", style="Warn.TLabel", wraplength=440).grid(
            row=5, column=0, sticky="w", pady=(2, 0)
        )

        # Which parts, and in which player's shape: the two choices that decide what
        # gets written, standing together on the line above the button that writes it.
        choose = ttk.Frame(self.root)
        choose.grid(row=2, column=0, sticky="ew", padx=12, pady=(6, 0))
        self.chapters_button = self._button(choose, "chapters", self._open_chapters)
        self.chapters_button.pack(side="left")
        self._label(choose, "format_label").pack(side="left", padx=(12, 6))
        self.format_box = ttk.Combobox(choose, values=self.format_names(), state="readonly", width=FIELD_WIDTH)
        self.format_box.pack(side="left")
        self.format_box.bind("<<ComboboxSelected>>", lambda _event: self._choose_format())
        self._show_formats()

        controls = ttk.Frame(self.root)
        controls.grid(row=3, column=0, sticky="ew", padx=12, pady=(6, 0))
        self.save_button = self._button(controls, "save", self._start, style="Work.TButton")
        self.save_button.pack(side="left")
        # The wheel that says the work is running stands on the button's right, and
        # bigger than the one that says a search is running: it is the answer to the
        # button the user just pressed.
        self.progress = Wheel(controls, WORK_WHEEL)
        self.progress.pack(side="left", padx=(12, 0))
        self._button(controls, "options", self._open_options).pack(side="left", padx=12)
        self.test_button = self._button(controls, "test_story", self._test_story)
        self.test_button.pack(side="left", padx=12)
        self.force_box = ttk.Checkbutton(
            controls, text=self.t("overwrite"), variable=self.force_var
        )
        self._labels["overwrite"] = self.force_box
        self.force_box.pack(side="left", padx=12)

        # Every control the format decides between now exists.
        self._show_cover()
        self._show_crop()
        self._show_negative()
        self._show_captions()

        # The log runs the whole width of the window, and gives up height for it: a
        # conversion says one line per part, and a line broken into three reads as
        # three things that went wrong.
        log_frame = ttk.Frame(self.root)
        log_frame.grid(row=4, column=0, sticky="nsew", padx=12, pady=(6, 8))
        log_frame.columnconfigure(0, weight=1)
        log_frame.rowconfigure(0, weight=1)
        self.log_view = tk.Text(
            log_frame,
            height=5,
            width=96,
            state="disabled",
            wrap="word",
            background=SUNK,
            foreground=INK,
            selectbackground=NAVY,
            selectforeground="#ffffff",
            relief="flat",
            highlightthickness=1,
            highlightbackground=LINE,
            highlightcolor=ACCENT,
            padx=8,
            pady=6,
        )
        self.log_view.grid(row=0, column=0, sticky="nsew")
        scroll = ttk.Scrollbar(log_frame, command=self.log_view.yview)
        scroll.grid(row=0, column=1, sticky="ns")
        self.log_view.configure(yscrollcommand=scroll.set)
        # An error has to be picked out of a page of progress lines, in the frame
        # itself and again in the report that closes each round.
        bold = tkfont.Font(font=self.log_view.cget("font") or "TkFixedFont")
        bold.configure(weight="bold")
        self.log_view.tag_configure("error", foreground=ALERT, font=bold)
        self.log_view.tag_configure("report", foreground=INK, font=bold)
        # The detailed log is fine print: ffmpeg's command lines are long, and they
        # should not read like the progress lines they sit under.
        self.log_view.tag_configure("detail", foreground=MUTED)
        self._errors: list[str] = []

        # The name of the app along the bottom edge, where it is read last instead of
        # crowding the work above it; the window's own band sits at its right end.
        hint = self.t("drop_hint") if DND_FILES else self.t("drop_unavailable")
        self._append(hint)

        if DND_FILES:
            self.root.drop_target_register(DND_FILES)
            self.root.dnd_bind("<<Drop>>", self._on_drop)

    def _relabel(self) -> None:
        self.root.title(f"{self.t('app_title')} {__version__}")
        for key, widget in self._labels.items():
            widget.configure(text=self.t(key))
        self.bar.relabel()
        self._show_story_language()
        for index, key in enumerate(("page_audio", "page_display")):
            self.pages.tab(index, text=self.t(key))
        self._show_formats()
        self._show_engines()
        self._show_announce()
        self._show_pause()
        self._show_card_text()
        self._show_crop()
        if not DND_FILES:
            self._append(self.t("drop_unavailable"))

    def bar_controls(self) -> list[str]:
        """The buttons the window's own band has to draw.

        With the system bar there the window has its buttons already, so the band is
        the mark, the name and what the window is for. Only a frameless window needs
        buttons of its own, and only macOS gets a frameless window: the bar can be
        hidden there without handing the window back to the app.
        """
        if not self.frameless:
            return []
        return ["hide", "zoom", "close"]

    def bar_action(self, key: str) -> None:
        {"hide": self._hide_window, "zoom": self._zoom_window, "close": self._close_window}[key]()

    def _hide_window(self) -> None:
        try:
            self.root.state("iconic")
        except tk.TclError:
            self.root.withdraw()

    def _zoom_window(self) -> None:
        """Maximise, or put back the size the window had before it was maximised.

        Tk has no one way to do it. Windows and macOS have a `zoomed` state; X11 takes
        a `-zoomed` attribute but no window manager is bound to honour it, so there the
        window is sized to the screen by hand.
        """
        if self._maximised:
            self._maximised = False
            for undo in (
                lambda: self.root.state("normal"),
                lambda: self.root.attributes("-zoomed", False),
            ):
                with contextlib.suppress(tk.TclError):
                    undo()
            self.root.geometry(self._restore_geometry)
            return
        self._restore_geometry = self.root.geometry()
        self._maximised = True
        if self.windowingsystem == "x11":
            self.root.geometry(
                f"{self.root.winfo_screenwidth()}x{self.root.winfo_screenheight()}+0+0"
            )
            return
        try:
            self.root.state("zoomed")
        except tk.TclError:
            with contextlib.suppress(tk.TclError):
                self.root.attributes("-zoomed", True)

    def _close_window(self) -> None:
        chapters.stop_preview(self._card_player)
        self.root.destroy()

    def set_frameless(self, on: bool) -> None:
        if system_bar_kept():
            on = False
        if on == self.frameless:
            return
        self.frameless = on
        if on:
            self._take_system_bar()
        else:
            self._give_system_bar()
        # A window manager decides what decorations a window gets when it maps it, so
        # the change only shows once the window has been mapped again.
        self.root.withdraw()
        self.root.update()
        self.root.deiconify()
        self.bar.frameless = on
        self.bar.redraw()

    def _crop_room(self) -> int:
        """How wide the crop frame may be on this screen.

        The frame is the one part of the window that grows on its own, and a window
        taller than the screen is not scrolled or shrunk — Tk simply cuts what hangs
        past the bottom, which is where the buttons and the log were.
        """
        return max(CROP_MIN, min(CROP_WIDTH, self.root.winfo_screenheight() - CROP_ROOM))

    def _size_window(self) -> None:
        """Open the window at a size of its own, in the middle of the screen.

        Tk hands the window manager the size its widgets asked for and lets it decide
        what to do with it. Left untold the AppImage's window came up a small square;
        told once, before the window is on screen, the manager still answers with a size
        of its own — its bar, a panel along the edge of the screen — and Tk lays the
        widgets out in the room the window was given, which is where the column on the
        right gets cut. So the size is asked for again after every resize the window is
        given while it settles, and never after: from then on the user's own size stands.
        """
        self.root.update_idletasks()
        self._fit_window()
        self._settling = True
        self.root.bind("<Configure>", self._refit, add="+")
        self.root.after(REFIT_SETTLE, lambda: setattr(self, "_settling", False))

    def _refit(self, _event: tk.Event) -> None:
        """Ask again for the room the widgets want, while the window is still settling."""
        if not self._settling or self._maximised:
            return
        self.root.after(REFIT_WAIT, self._fit_window)

    def _fit_window(self) -> None:
        self.root.update_idletasks()
        screen = self.root.winfo_screenwidth(), self.root.winfo_screenheight()
        room = (screen[0] - 2 * WINDOW_MARGIN, screen[1] - 2 * WINDOW_MARGIN)
        width = min(max(self.root.winfo_reqwidth(), min(MIN_WIDTH, room[0])), room[0])
        height = min(max(self.root.winfo_reqheight(), min(MIN_HEIGHT, room[1])), room[1])
        if (width, height) == (self.root.winfo_width(), self.root.winfo_height()):
            return
        x = max(0, (screen[0] - width) // 2)
        y = max(0, (screen[1] - height) // 2)
        self.root.geometry(f"{width}x{height}+{x}+{y}")
        self.root.minsize(min(MIN_WIDTH, width), min(MIN_HEIGHT, height))

    def _take_system_bar(self) -> None:
        """Take the system bar away, where Tk can do it and leave the window managed.

        `system_bar_kept` keeps the bar everywhere else: Tk's other way is
        `overrideredirect`, which unmanages the window — no iconify, no resize borders,
        and on X11 no place in the taskbar either.
        """
        if sys.platform == "darwin":
            try:
                self.root.attributes("-titlebar", False)
                return
            except tk.TclError:
                pass
        self.root.overrideredirect(True)

    def _give_system_bar(self) -> None:
        if sys.platform == "darwin":
            with contextlib.suppress(tk.TclError):
                self.root.attributes("-titlebar", True)
        self.root.overrideredirect(False)

    def _open_options(self) -> None:
        Options(self)

    def _open_chapters(self) -> None:
        source = self._typed_path(self.pack_var)
        if source is None or not source.exists():
            self._append("ERROR: choose a DAISY pack first")
            return
        ChapterList(self, source)

    def _settings_key(self) -> tuple:
        """Everything a run reads from the window. Two runs with the same key write
        the same story, so the second one is not worth waiting for."""
        announce = self.announce()
        return (
            str(self._typed_path(self.pack_var) or ""),
            str(self._typed_path(self.cover_var) or ""),
            self.cut,
            self.format_var.get(),
            self.crop_var.get() or "center",
            self._crop_zoom_value,
            self._crop_focus,
            self.negative_var.get(),
            self.captions_wanted(),
            ",".join(str(number) for number in sorted(self.dropped)),
            announce.mode,
            announce.seconds,
            announce.language,
            announce.autoplay,
            announce.show_total,
            announce.engine,
            announce.voice,
            announce.command,
            announce.python,
            announce.clip,
            announce.device,
            announce.speed,
            announce.show_title,
            float(self.options["card_contrast"]),
        )

    def _test_story(self) -> None:
        """Convert into a folder the app made, play it there, and keep it for Save.

        Nothing of the user's is touched: the output folder stays as it is until the
        Save button is pressed.
        """
        if self._cover_pending(self._test_story):
            return
        picked = self._sources()
        if picked is None:
            return
        source, cover = picked

        if self._test_folder is not None:
            shutil.rmtree(self._test_folder, ignore_errors=True)
        self._test_folder = Path(tempfile.mkdtemp(prefix="daisy2telmi-test-"))
        self._test_key = None
        self._on_done = lambda code, folder=self._test_folder: self._tested(code, folder)
        self._convert(source, self._test_folder, cover, True)

    def _tested(self, code: int, folder: Path | None) -> None:
        """Open the player on the story a test just wrote, and remember its settings."""
        if code != 0 or folder is None or not folder.is_dir():
            return
        story = emulator.find_story(folder)
        if story is None:
            self._append("ERROR: the test wrote no story to play")
            return
        self._test_key = self._settings_key()
        self.emulator = Emulator(self, story)

    def _copy_tested(self, out_dir: Path) -> bool:
        """Write the pack the player just showed into the output folder as it stands."""
        try:
            for entry in sorted(self._test_folder.iterdir()):
                target = out_dir / entry.name
                if entry.is_dir():
                    if target.is_dir() and any(target.iterdir()) and not self.force_var.get():
                        self._append(
                            f"ERROR: a file of the same name already exists: {target} holds a story. "
                            "Tick the overwrite box to replace it"
                        )
                        return False
                    shutil.copytree(entry, target, dirs_exist_ok=True)
                else:
                    shutil.copy2(entry, target)
        except OSError as exc:
            self._append(f"ERROR: {exc.filename or out_dir} could not be overwritten: {exc.strerror or exc}")
            return False
        return True

    def speech_language(self) -> str:
        """What the cards fall back to: the language picked on the speech page, else the window's own."""
        return self.options["speech_language"] or self.options["language"]

    def _story_language_label(self, code: str | None) -> str:
        """One line of the story language box. Auto names the language the opened
        pack was read as, so the box says what it is about to do rather than leaving
        it to be guessed."""
        if code is not None:
            return f"{code} - {LANGUAGES[code].name}"
        if self._part_code in LANGUAGES:
            return f"{self.t('speech_auto')} — {LANGUAGES[self._part_code].name}"
        return self.t("speech_auto")

    def _story_language_labels(self) -> list[str]:
        return [self._story_language_label(code) for code in self.story_language_keys]

    def _show_story_language(self) -> None:
        self.story_language_box.configure(values=self._story_language_labels())
        self.story_language_box.set(self._story_language_label(self.options["speech_language"]))

    def _choose_story_language(self) -> None:
        """Pin the book's language, or leave it to the pack.

        The cards, the words drawn on them and the voice that reads them all follow
        this one answer: a French book on an English machine needs a French card."""
        labels = self._story_language_labels()
        try:
            chosen = self.story_language_keys[labels.index(self.story_language_box.get())]
        except ValueError:
            chosen = None
        self.options["speech_language"] = chosen
        settings.save(self.options)
        self._show_story_language()
        self._show_crop()

    def announce(self) -> Announce:
        return Announce(
            mode=self.options["announce"],
            seconds=self.options["card_seconds"],
            language=self.speech_language(),
            auto_language=self.options["speech_language"] is None,
            autoplay=self.options["autoplay"],
            pause_after=self.options["pause_after"],
            show_total=self.options["card_total"],
            show_word=self.options["card_word"],
            show_title=self.options["card_title"],
            contrast=float(self.options["card_contrast"]),
            engine=self.options["tts"],
            voice=self.options["voice"],
            command=self.options["command"],
            python=self.options["python"],
            clip=self.options["clip"],
            device=self.options["device"],
            speed=self.options["speed"],
            expression=self.options["expression"],
            read_speed=self.options["read_speed"],
        )

    def _follow_pack(self, *_: object) -> None:
        """A new book is a new book: the part numbers the last one's dropped parts
        point at mean nothing here, the same as a different cut level."""
        pack = self._typed_path(self.pack_var)
        if pack != self._pack_path:
            self.dropped.clear()
            self._pack_path = pack
        self._show_paths()

    def _show_paths(self) -> None:
        """What the three buttons picked, read under them.

        The paths are not typed anywhere, so this line is the only place the window
        says which book it holds and where its story will be written.
        """
        # A path is read from its end, so a long one loses its front rather than the
        # part that says where things are.
        room = (PATH_READ - 3) // 2
        picked = [str(self._typed_path(self.pack_var) or ""), str(self._out_dir())]
        parts = [part if len(part) <= room else "…" + part[-room:] for part in picked if part]
        self._paths_line.configure(text=" → ".join(parts))

    def _start_dir(self, current: str = "") -> str:
        """Where a file list opens: the folder the field already names, then the
        folder the last one ended in, then the one named in the options, then home."""
        typed = current.strip()
        if typed:
            named = Path(typed).expanduser()
            folder = named if named.is_dir() else named.parent
            if folder.is_dir():
                return str(folder)
        for remembered in (self.options.get("last_dir"), self.options.get("input_dir")):
            if remembered and Path(remembered).is_dir():
                return str(remembered)
        return str(Path.home())

    def _browse(self, key: str, current: str, pick: str) -> str:
        """Open the app's own file list and wait for it, as Tk's dialogs do.

        The folder it was last showing is kept, whether or not something was
        chosen: the next one opens where the user was looking.
        """
        window = Browser(self, title=self.t(key), folder=Path(self._start_dir(current)), pick=pick)
        chosen = window.run()
        self._remember_dir(window.current)
        return chosen

    def _remember_dir(self, folder: Path) -> None:
        if folder.is_dir():
            self.options["last_dir"] = str(folder)
            settings.save(self.options)

    def _pick_pack_file(self) -> None:
        path = self._browse("browse_zip", self.pack_var.get(), "file")
        if path:
            self.pack_var.set(path)
            self._find_cover()

    def _find_cover(self) -> None:
        """Read the pack, and offer the cover of the print book it was read from.

        A 2.02 audio pack carries no picture, but its metadata names the book's
        ISBN, and four services hold the cover for it between them. The answer
        arrives on its own thread: a lookup that takes a second should not stop
        the window from drawing. A cover the user already typed is never
        replaced, and the cover the last pack's lookup found is dropped before
        this one asks. The pack is read on that thread whatever the Image box
        holds, because the card the preview draws is drawn from the pack.
        """
        pack = self._typed_path(self.pack_var)
        self._cover_job += 1
        self._pack_title = None
        self._part_count, self._part_title, self._part_code = None, "", ""
        self._show_story_language()
        self._spin_off()
        self._drop_lookup()
        if pack is None or not pack.exists():
            return
        typed = bool(self.cover_var.get().strip())
        self._cover_asked = "pack"
        if not typed:
            self._spin_on()
        threading.Thread(
            target=self._cover_lookup,
            args=(pack, self._cover_job, self._cover_dest(self._cover_job), typed),
            daemon=True,
        ).start()
        self._show_crop()

    def _drop_lookup(self) -> None:
        """Clear what the previous pack's lookup filled in.

        A cover, an author and a title the services gave were found for one book.
        The next pack gets its own. A box the user typed into or pointed at holds
        their own choice, and is left where they put it.
        """
        for box, name in ((self.cover_var, "_auto_cover"), (self.author_var, "_auto_author"), (self.title_var, "_auto_title")):
            if box.get() == getattr(self, name):
                setattr(self, name, "")
                box.set("")

    def _spin_on(self) -> None:
        """Show the wheel, the word about it and the way to call it off."""
        if self._spinning:
            return
        self._spinning = True
        self.cover_spin.pack(side="left", padx=(6, 0))
        self.cover_note.pack(side="left", padx=(6, 0))
        self.cover_stop.pack(side="left", padx=(6, 0))
        self.cover_spin.start()

    def _spin_off(self) -> None:
        if not self._spinning:
            return
        self._spinning = False
        self.cover_spin.stop()
        self.cover_spin.pack_forget()
        self.cover_note.pack_forget()
        self.cover_stop.pack_forget()

    def _stop_cover(self) -> None:
        """Call the lookup off.

        The thread is already asked and cannot be recalled, so the lookup is
        stopped by number: the answer it posts carries the number it started with,
        and raising the current number makes that answer an old one.
        """
        self._cover_job += 1
        self._spin_off()
        self._append(self.t("cover_stopped"))

    def _cover_lookup(self, pack: Path, job: int, dest: Path, typed: bool) -> None:
        """Read the pack's own metadata off the worker thread, then post the picture back.

        The pack is read once and answers two questions: what the book is called,
        which the cover services search for, and how many parts it has and what the
        first one is named, which is what the preview's card is lettered with.
        """
        try:
            with open_pack(pack) as opened:
                book = extract(opened, "chapter")
            code = card_language(self.announce(), book)[0]
            self.events.put(
                (
                    "pack",
                    job,
                    len(book.chapters),
                    part_title(book.chapters[0].title, code) if book.chapters else "",
                    code,
                )
            )
            found = None if typed else covers.find(book, dest)
            title, author = book.title, book.author or ""
        except Exception as exc:  # a suggestion is worth nothing here: it must never break the window
            log.warning("no cover lookup for %s: %s", pack.name, exc)
            found, title, author = None, "", ""
        self.events.put(("cover", job, "" if found is None else str(found), title, author))

    def _search_words(self) -> None:
        """Ask the catalogues for the two words the boxes hold, and take their answer
        back into the Image box.

        The services answer for the book the words name, not for the pack, so a
        title the pack got wrong - a folder name, a producer's truncation - is
        corrected by typing the real one and asking again here.
        """
        title = self.title_var.get().strip()
        if not title:
            return
        self._cover_job += 1
        job = self._cover_job
        self._cover_asked = "words"
        dest = self._cover_dest(job)
        self._spin_on()
        words = covers.Words(title, self.author_var.get().strip())
        threading.Thread(target=self._words_lookup, args=(words, job, dest), daemon=True).start()

    def _cover_dest(self, job: int) -> Path:
        """One name per lookup, so two lookups run in a row cannot write over each other."""
        if self._cover_dir is None:
            self._cover_dir = Path(tempfile.mkdtemp(prefix="daisy2telmi-cover-"))
        return self._cover_dir / f"cover-{job}.jpg"

    def _words_lookup(self, words: covers.Words, job: int, dest: Path) -> None:
        try:
            found = covers.find(words, dest)
        except Exception as exc:
            log.warning("no cover for %r: %s", words.title, exc)
            found = None
        self.events.put(("cover", job, "" if found is None else str(found), words.title, words.author))

    def _cover_typed(self, _event: object = None) -> None:
        """Fetch the link the Image box was just given.

        The box takes a file or a web address, so a pasted link has to become a
        picture before the frame, the cards or the story can use it.
        """
        if is_link(self.cover_var.get().strip()):
            self._use_cover_link()

    def _cover_pending(self, then) -> bool:
        """True when the Image box holds a link rather than a file.

        A link is fetched like any other cover - on a worker thread, with the wheel
        turning - so the run asked for here waits for the answer instead of running
        without a picture. One press of the button then does both.
        """
        if not is_link(self.cover_var.get().strip()):
            return False
        self._cover_then = then
        self._use_cover_link()
        return True

    def _use_cover_link(self) -> None:
        """Fetch the picture a pasted link points at, on the same thread a lookup runs on."""
        url = self.cover_var.get().strip()
        if not is_link(url):
            return
        self._cover_job += 1
        job = self._cover_job
        self._cover_asked = "link"
        dest = self._cover_dest(job)
        self._spin_on()
        threading.Thread(target=self._link_lookup, args=(url, job, dest), daemon=True).start()

    def _link_lookup(self, url: str, job: int, dest: Path) -> None:
        try:
            found = covers.link(url, dest)
        except Exception as exc:
            log.warning("no picture at %s: %s", url, exc)
            found = None
        self.events.put(("cover", job, "" if found is None else str(found), "", ""))

    def _search_cover(self, engine: str) -> None:
        """Open an image search on the two words the boxes hold: the book's author
        and its title. Neither engine gives a picture back to the app, so the user
        saves one and points the Image box at it."""
        words = " ".join(part for part in (self.author_var.get(), self.title_var.get()) if part.strip())
        if not words:
            pack = self._typed_path(self.pack_var)
            words = self._pack_title or (pack.stem if pack is not None else "")
        if not words.strip():
            return
        query = quote_plus(words.strip())
        url = (
            f"https://duckduckgo.com/?q={query}&iax=images&ia=images"
            if engine == "ddg"
            else f"https://www.google.com/search?tbm=isch&q={query}"
        )
        # The link is written whatever the machine did with it: a search that opens
        # nothing still leaves a link to open by hand.
        log.info("image search: %s", url)
        if not open_url(url):
            log.warning("no browser opened: copy the link above into one")

    def _out_dir(self) -> Path:
        """Where a story is written: the folder the Output button picked, or the home folder
        when nothing was picked. Nothing picked is not an error worth stopping a run for."""
        return self._typed_path(self.out_var) or Path.home()

    def _pick_cover(self) -> None:
        path = self._browse("browse_cover", self.cover_var.get(), "file")
        if path:
            self.cover_var.set(path)

    def card_text_names(self) -> list[str]:
        """The card's first line, written the way a card writes it: the word for the
        part or no word, the total or no total."""
        card = self.card_word().upper()
        return [f"{card} 1", f"{card} 1/20", "1", "1/20"]

    def card_word(self) -> str:
        """The story's own word for a part of it: the language the pack was read as,
        or the one the user pinned over it."""
        return LANGUAGES.get(self._part_code or self.speech_language(), LANGUAGES["en"]).card

    def card_title_names(self) -> list[str]:
        """What the line under the number holds."""
        return [self.t("card_text_none"), self.t("card_text_title")]

    def _show_card_text(self) -> None:
        names = self.card_text_names()
        self.card_text_box.configure(values=names)
        self.card_text_box.set(names[self._card_text_index()])
        titles = self.card_title_names()
        self.card_title_box.configure(values=titles)
        self.card_title_box.set(titles[1 if self.options["card_title"] else 0])

    def _card_text_index(self) -> int:
        """Which of the four shapes the saved word and total make."""
        word, total = bool(self.options["card_word"]), bool(self.options["card_total"])
        return (0 if word else 2) + (1 if total else 0)

    def _choose_card_text(self) -> None:
        index = self.card_text_names().index(self.card_text_box.get())
        self.options["card_word"] = index < 2
        self.options["card_total"] = index in (1, 3)
        settings.save(self.options)
        self._show_crop()

    def _choose_card_title(self) -> None:
        self.options["card_title"] = self.card_title_names().index(self.card_title_box.get()) == 1
        settings.save(self.options)
        self._show_crop()

    def announce_names(self) -> list[str]:
        """Every thing a card can say, by the name this window's language gives it."""
        return [self.t(ANNOUNCE_KEYS[mode]) for mode in self.announce_keys]

    def _show_announce(self) -> None:
        """Refill the box in this language, and grey the extract length out for every
        pattern that plays no extract."""
        names = self.announce_names()
        self.announce_box.configure(values=names)
        chosen = self.options["announce"]
        self.announce_box.set(names[self.announce_keys.index(chosen) if chosen in self.announce_keys else 0])
        # A playlist's interlude is always spoken: it has no card to play an opening on.
        plays = chosen == "snippet" and self.format_var.get() in CARD_FORMATS
        self.seconds_box.configure(state="normal" if plays else "disabled")
        self._labels["opt_card_seconds"].configure(foreground=INK if plays else MUTED)
        # A silent card has nothing to hear.
        self.announce_try_button.state(["!disabled"] if chosen != "off" else ["disabled"])

    def _choose_announce(self) -> None:
        self.options["announce"] = self.announce_keys[self.announce_names().index(self.announce_box.get())]
        settings.save(self.options)
        self._show_announce()

    def pause_names(self) -> list[str]:
        """Where a story can stop for OK, by the name this window's language gives it."""
        return [self.t(f"pause_{mode}") for mode in self.pause_keys]

    def _show_pause(self) -> None:
        """Refill the box in this language, and grey it out where it asks nothing."""
        names = self.pause_names()
        self.pause_box.configure(values=names)
        chosen = self.options["pause_after"]
        self.pause_box.set(names[self.pause_keys.index(chosen) if chosen in self.pause_keys else 0])
        asked = bool(self.options["autoplay"])
        self.pause_box.configure(state="readonly" if asked else "disabled")
        self._labels["opt_pause"].configure(foreground=INK if asked else MUTED)

    def _choose_autoplay(self) -> None:
        self.options["autoplay"] = bool(self.autoplay_var.get())
        settings.save(self.options)
        self._show_pause()

    def _choose_pause(self) -> None:
        self.options["pause_after"] = self.pause_keys[self.pause_names().index(self.pause_box.get())]
        settings.save(self.options)

    def _card_seconds(self) -> float:
        try:
            value = float(self.seconds_box.get().strip().replace(",", "."))
        except ValueError:
            return float(settings.DEFAULTS["card_seconds"])
        low, high = CARD_SECONDS_LIMITS
        return min(max(value, low), high)

    def _type_seconds(self) -> None:
        self.options["card_seconds"] = self._card_seconds()
        self.seconds_box.delete(0, "end")
        self.seconds_box.insert(0, f"{self.options['card_seconds']:g}")
        settings.save(self.options)

    def _try_announce(self) -> None:
        """Hear one card of this book, made the way the story will make it.

        The part is picked at random: a book's first part is usually front matter,
        and a test that always played it would never be heard.
        """
        source = self._typed_path(self.pack_var)
        if source is None or not source.exists():
            self._append("ERROR: choose a DAISY pack first")
            return
        self.announce_try_button.state(["disabled"])
        threading.Thread(target=self._card_try, args=(source,), daemon=True).start()

    def _card_try(self, source: Path) -> None:
        announce = self.announce()
        try:
            with open_pack(source) as pack:
                book = extract(pack, self.cut)
                kept = [
                    (number, chapter)
                    for number, chapter in enumerate(book.chapters, start=1)
                    if number not in self.dropped
                ]
                if not kept:
                    raise chapters.ChapterError("this pack has no chapter left to listen to")
                number, chapter = random.choice(kept)
                announce = replace(announce, language=card_language(announce, book)[0])
                dest = Path(tempfile.mkdtemp(prefix="daisy2telmi-card-")) / "card.mp3"
                if announce.mode == "snippet":
                    audio.render(chapter.segments, dest, limit=announce.seconds)
                else:
                    phrase = announce.phrase(number, len(book.chapters), chapter.title)
                    if phrase is None:
                        raise chapters.ChapterError("this card says nothing to hear")
                    tts.synthesize(
                        phrase,
                        announce.language,
                        dest,
                        engine=announce.engine,
                        voice=announce.voice,
                        command=announce.command,
                        python=announce.python,
                        clip=announce.clip,
                        device=announce.device,
                        speed=announce.speed,
                        expression=announce.expression,
                    )
            self.events.put(("card", dest))
        except Exception as exc:  # a pack that will not open, or a voice that will not speak
            log.error("%s", exc)
            self.events.put(("card", None))

    def _announced(self, path: Path | None) -> None:
        self.announce_try_button.state(["!disabled"])
        if path is None:
            return
        chapters.stop_preview(self._card_player)
        self._card_player = chapters.open_preview(path)

    def _pick_contrast(self) -> None:
        self.options["card_contrast"] = self.contrast_var.get() / 100
        settings.save(self.options)
        self._crop_cover = None
        self._show_crop()

    def format_names(self) -> list[str]:
        """Every output, by the name this window's language gives it."""
        return [self.t(f"fmt_{player.replace('-', '_')}") for player in FORMATS]

    def _show_formats(self) -> None:
        """Refill the box in this language, and put it back on the chosen player."""
        names = self.format_names()
        self.format_box.configure(values=names)
        chosen = FORMATS.index(self.format_var.get()) if self.format_var.get() in FORMATS else 0
        self.format_box.set(names[chosen])

    def _choose_format(self) -> None:
        self.format_var.set(FORMATS[self.format_names().index(self.format_box.get())])
        self._show_cover()
        self._show_announce()
        self._show_negative()
        self._show_captions()
        self._pick_format()

    def _show_cover(self) -> None:
        """An mp3 pack carries no pictures, so the frame and its knobs have nothing
        to show for it, there is no cover to place, no card to letter, and no story
        graph for the emulator to play."""
        cards = self.format_var.get() in CARD_FORMATS
        for frame in (self.crop_preview, self.cover_card, self.osd_card):
            if cards:
                frame.grid()
            else:
                frame.grid_remove()
        if cards:
            self.pages.add(self.display_page, text=self.t("page_display"))
        else:
            self.pages.hide(self.display_page)
        self.test_button.configure(state="normal" if cards else "disabled")

    def _show_negative(self) -> None:
        """Only a grey screen can be turned inside out; the colour ones ignore it."""
        self.negative_box.configure(state="normal" if self.format_var.get() == "lunii" else "disabled")

    def _pick_negative(self) -> None:
        self.options["card_negative"] = self.negative_var.get()
        settings.save(self.options)
        # The preview is the card's own picture, so turning the card inside out
        # changes it too.
        self._crop_cover = None
        self._show_crop()

    def _show_captions(self) -> None:
        """Only a Telmi or a Flam screen is big enough to read a sentence on."""
        self.captions_box.configure(state="normal" if self.format_var.get() in CAPTION_FORMATS else "disabled")

    def captions_wanted(self) -> bool:
        """The box's tick, but only where a screen exists to show the words on: a
        tick left over from a Telmi run must not make a Lunii run refuse its own
        conversion."""
        return bool(self.captions_var.get()) and self.format_var.get() in CAPTION_FORMATS

    def _pick_captions(self) -> None:
        self.options["captions"] = self.captions_var.get()
        if self.captions_var.get() and self.cut != "sentence":
            # One picture per sentence is only worth drawing when each part is one.
            self.cut = self.options["cut"] = "sentence"
            if self.chapter_list is not None:
                self.chapter_list._choose("sentence")
        settings.save(self.options)

    def _pick_format(self) -> None:
        self.options["format"] = self.format_var.get()
        settings.save(self.options)
        # The frame is the chosen player's screen, so it is drawn at its ratio again.
        self._crop_cover = None
        self._show_crop()

    def engine_names(self) -> list[str]:
        """The engines this machine can speak with, plus automatic and silence."""
        return [self.engine_name(name) for name in engine_keys()]

    def engine_name(self, key: str) -> str:
        if key == "auto":
            return self.t("engine_auto")
        if key == "off":
            return self.t("engine_off")
        return tts.LABELS.get(key, key)

    def _show_engines(self) -> None:
        """Refill the box in this language, and put it back on the chosen engine."""
        names = self.engine_names()
        self.speech_box.configure(values=names)
        keys = engine_keys()
        chosen = self.options["tts"]
        self.speech_box.set(names[keys.index(chosen) if chosen in keys else 0])

    def _choose_engine(self) -> None:
        keys = engine_keys()
        self.options["tts"] = keys[self.engine_names().index(self.speech_box.get())]
        settings.save(self.options)
        if self.options["tts"] in tts.PACKAGES:
            threading.Thread(target=self._engine_probe, args=(self.options["tts"],), daemon=True).start()

    def _engine_probe(self, engine: str) -> None:
        """Whether a Python on this machine already has the engine just picked.

        That costs a Python per candidate interpreter, so it is asked off the window;
        an engine the machine has needs nothing more said about it.
        """
        if not tts.available(engine, self.options.get("python") or None):
            self.events.put(("install_engine", engine))

    def _offer_install(self, engine: str) -> None:
        """The install is asked where the engine was chosen, and nowhere else."""
        if self.options["tts"] != engine:
            return  # the box moved on while the machine was being asked
        label = tts.LABELS[engine]
        if messagebox.askokcancel(self.t("speech_title"), self.t("engine_needed").format(engine=label)):
            self._install_now(engine)
        else:
            self._append(f"{label} is not installed: it will be asked for again when the story is saved")

    def _open_speech(self) -> None:
        Speech(self)

    def _show_crop(self) -> None:
        """Draw the crop frame over the cover: which part of a tall cover the frame
        keeps is not something a word can tell you.

        The frame is the chosen player's screen, so it is drawn with no cover too:
        the shape a cover has to be fitted into is worth seeing before one exists.

        The cover is scaled once per zoom, so dragging stays instant: a move only
        shifts the picture already on the canvas.
        """
        if self.format_var.get() not in CARD_FORMATS:
            return
        size = images.preview_size(self.format_var.get(), self._crop_width)
        # A cover smaller than the frame leaves its room empty on the canvas, which
        # is the card's band colour and not the window's.
        style = "bw" if self.format_var.get() == "lunii" else "colour"
        # A colour card has nothing to invert, so the preview inverts with the card.
        negative = self.negative_var.get() and style == "bw"
        contrast = float(self.options["card_contrast"])
        self.crop_frame.configure(width=size[0], height=size[1], background=images.band_colour(style, negative))
        self._draw_osd(size, style, negative)
        source = self._typed_path(self.cover_var)
        if source is None or not source.is_file():
            self._clear_crop()
            return
        key = (str(source), self.format_var.get(), self._crop_zoom_value, negative, contrast)
        if self._crop_cover is None or self._crop_cover[:5] != key:
            try:
                self._crop_cover = (
                    *key,
                    images.cover_fit(source, size, self._crop_zoom_value, style, negative, contrast),
                )
            except images.ImageError as exc:
                self._append(f"ERROR: {exc}")
                self._clear_crop()
                return
        self._draw_crop()

    def _card_words(self, size: tuple[int, int]) -> str:
        """The words the book's first card will carry, at the preview's size.

        Only a chapter-level cut has chapter names and a chapter count to letter a
        card with, and only the pack's own reading says what they are: until it has
        been read, the card carries its number alone.
        """
        chapters = self.cut == "chapter" and self._part_count is not None
        return images.card_label(
            self._part_code or self.speech_language(),
            1,
            self._part_count or 1,
            self.cut == "chapter" and bool(self.options["card_word"]),
            chapters and bool(self.options["card_total"]),
            self._part_title if chapters and self.options["card_title"] else "",
            size,
        )

    def _draw_osd(self, size: tuple[int, int], style: str, negative: bool) -> None:
        """Lay the card's own lettering over the preview, so the words it will carry
        are seen on the picture they will be carried on.

        A grey card carries its number alone, as every card of that player does.
        It is drawn once per card, not once per move: the cover slides under it.
        """
        label = "1" if style == "bw" else self._card_words(size)
        key = (label, size, style, negative)
        if self._crop_osd == key:
            return
        self._crop_osd = key
        self._osd_photo = tk.PhotoImage(
            master=self.root, data=images.card_layer_png(label, "", *size, style=style, negative=negative)
        )
        self.crop_frame.itemconfigure(self._osd_item, image=self._osd_photo)

    def _draw_crop(self) -> None:
        """Slide the drawn cover under the frame until the frame sits at its focus."""
        if self._crop_cover is None:
            return
        png, picture = self._crop_cover[5]
        frame = images.preview_size(self.format_var.get(), self._crop_width)
        focus = self._crop_focus or images.CROP_FOCUS.get(self.crop_var.get(), images.CROP_FOCUS["center"])
        self._crop_photo = tk.PhotoImage(master=self.root, data=png)
        self.crop_frame.itemconfigure(self._crop_item, image=self._crop_photo)
        self.crop_frame.coords(
            self._crop_item,
            -round((picture[0] - frame[0]) * focus[0]),
            -round((picture[1] - frame[1]) * focus[1]),
        )

    def _clear_crop(self) -> None:
        """The picture belongs to the cover that went away; the frame and the chosen band do not."""
        self._crop_cover = None
        self._crop_photo = None
        self._crop_focus = None
        self._crop_zoom_value = 1.0
        self._show_crop_zoom()
        self.crop_frame.itemconfigure(self._crop_item, image="")

    def _grab_crop(self, event: tk.Event) -> None:
        if self._crop_cover is None:
            return
        self.crop_frame.focus_set()
        self._crop_drag = (event.x, event.y, self._current_focus())

    def _drag_crop(self, event: tk.Event) -> None:
        """A drag moves the cover, so the frame's place moves the other way. It is
        measured from where the pointer went down, not from the move before."""
        if self._crop_drag is None:
            return
        x, y, start = self._crop_drag
        self._move_crop(event.x - x, event.y - y, start)

    def _step_crop(self, dx: int, dy: int) -> None:
        """The keyboard reaches the same frame: the arrows move it by a tenth."""
        self._move_crop(dx * self._crop_width / 10, dy * self._crop_width / 10)

    def _move_crop(
        self, dx: float, dy: float, start: tuple[float, float] | None = None
    ) -> None:
        if self._crop_cover is None:
            return
        frame = images.preview_size(self.format_var.get(), self._crop_width)
        picture = self._crop_cover[5][1]
        focus = start or self._current_focus()
        room = (picture[0] - frame[0], picture[1] - frame[1])
        self._crop_focus = (
            min(1.0, max(0.0, focus[0] - dx / room[0])) if room[0] > 0 else focus[0],
            min(1.0, max(0.0, focus[1] - dy / room[1])) if room[1] > 0 else focus[1],
        )
        # A frame that has been dragged answers to no band: it sits where the pointer
        # left it, and no band name describes that any more.
        self.crop_var.set("")
        self._draw_crop()

    def _current_focus(self) -> tuple[float, float]:
        """The frame's place over the cover: the chosen band until it is moved."""
        return self._crop_focus or images.CROP_FOCUS.get(
            self.crop_var.get(), images.CROP_FOCUS["center"]
        )

    def _wheel_crop(self, event: tk.Event) -> None:
        """The wheel enlarges the cover. macOS reports a notch as 1 unit and Windows
        as 120; X11 sends buttons 4 and 5, which are bound on their own."""
        notches = event.delta / (1 if sys.platform == "darwin" else 120)
        self._zoom_crop(1 - notches * 0.25)

    def _zoom_crop(self, factor: float) -> None:
        # A wheel notch scales the zoom, and zero scales to zero: from the whole cover
        # the only way out is a step in, and there is nothing below it to step down to.
        if self._crop_zoom_value:
            self._set_crop_zoom(self._crop_zoom_value * factor)
        elif factor > 1:
            self._set_crop_zoom(0.25)

    def _show_crop_zoom(self) -> None:
        """The zoom as the box writes it: a percentage of the cover's own size."""
        self.crop_zoom_var.set(f"{round(self._crop_zoom_value * 100)}%")

    def _type_crop_zoom(self) -> None:
        typed = self.crop_zoom_var.get().replace(",", ".").replace("%", "").strip()
        try:
            percent = float(typed)
        except ValueError:
            self._show_crop_zoom()
            return
        self._set_crop_zoom(percent / 100)

    def _set_crop_zoom(self, value: float) -> None:
        zoom = images.clamp_zoom(value)
        if abs(zoom - self._crop_zoom_value) < 0.005:
            self._show_crop_zoom()
            return
        self._crop_zoom_value = zoom
        self._show_crop_zoom()
        # A new zoom means a new picture, which takes a moment: a wheel gesture
        # asks for one, at the end of it.
        if self._crop_redraw is not None:
            self.root.after_cancel(self._crop_redraw)
        self._crop_redraw = self.root.after(120, self._show_crop)

    def _reset_crop(self) -> None:
        self.crop_var.set("center")
        self._crop_focus = None
        self.options["cover_crop"] = "center"
        settings.save(self.options)
        self._crop_zoom_value = 1.0
        self._show_crop_zoom()
        self._show_crop()

    def _on_drop(self, event: tk.Event) -> None:
        dropped = self.root.tk.splitlist(event.data)
        if dropped:
            self.pack_var.set(dropped[0])
            self._find_cover()

    def _append(self, line: str, counted: bool = True) -> None:
        tags = _tag(line)
        if counted and tags == ("error",):
            self._errors.append(line)
        self.log_view.configure(state="normal")
        self.log_view.insert("end", line + "\n", *tags)
        self.log_view.see("end")
        self.log_view.configure(state="disabled")

    def _clear_log(self) -> None:
        """Start a round on an empty frame, so its report closes something readable."""
        self.log_view.configure(state="normal")
        self.log_view.delete("1.0", "end")
        self.log_view.configure(state="disabled")
        self._errors = []

    def _report(self, code: int) -> None:
        """Close a round with the errors it produced, repeated where they can be read."""
        errors = self._errors
        head = "done" if code == 0 else f"failed with exit code {code}"
        self._append("")
        self._append(f"REPORT: {head}, {len(errors)} error(s)" if errors else f"REPORT: {head}")
        for line in errors:
            self._append(line, counted=False)

    def _typed_path(self, var: tk.StringVar) -> Path | None:
        """The folder or file the box holds, or None when it is empty.

        An empty box must not become Path('.'), which exists and would be read as
        the working directory.
        """
        typed = var.get().strip()
        return Path(typed).expanduser() if typed else None

    def _sources(self) -> tuple[Path, Path | None] | None:
        """The pack and the cover the window points at, refusing either when it is
        not there. Both buttons start a run, so both read the same boxes."""
        source = self._typed_path(self.pack_var)
        if source is None:
            self._append("ERROR: choose a DAISY pack first")
            return None
        if not source.exists():
            self._append(f"ERROR: no such pack: {source}")
            return None
        cover = self._typed_path(self.cover_var)
        if cover is not None and not cover.exists():
            self._append(f"ERROR: no such image: {cover}")
            return None
        return source, cover

    def _convert(self, source: Path, out_dir: Path, cover: Path | None, force: bool) -> None:
        """Hand the conversion to a worker thread and let the window keep drawing."""
        self._clear_log()
        self.save_button.state(["disabled"])
        self.progress.start(12)
        engine, install = self._speech()
        announce = replace(self.announce(), engine=engine)
        dropped = sorted(self.dropped)
        threading.Thread(
            target=self._run,
            args=(
                source,
                out_dir,
                force,
                announce,
                install,
                cover,
                ",".join(str(number) for number in dropped) if dropped else None,
                self.cut,
                self.format_var.get(),
                self.crop_var.get() or "center",
                self._crop_zoom_value,
                self._crop_focus,
                self.negative_var.get(),
                self.captions_wanted(),
            ),
            daemon=True,
        ).start()

    def _start(self) -> None:
        if self._cover_pending(self._start):
            return
        picked = self._sources()
        if picked is None:
            return
        source, cover = picked
        out_dir = self._out_dir()
        out_dir.mkdir(parents=True, exist_ok=True)

        # A test that has not been changed since it was played needs no converter:
        # the pack the player just showed is written as it stands.
        if (
            self._test_key is not None
            and self._test_key == self._settings_key()
            and self._test_folder is not None
            and self._test_folder.is_dir()
        ):
            self._clear_log()
            if self._copy_tested(out_dir):
                self._append(self.t("test_reuse"))
            return

        self._test_key = None
        self._convert(source, out_dir, cover, self.force_var.get())

    def _speech(self) -> tuple[str, bool]:
        """The engine this story's cards get, and whether one has to be installed first.

        The neural engines do not ship inside the app: they are packages a Python of
        the user's installs. OK starts that install, Cancel takes the best voice the
        machine already has.
        """
        engine = self.options["tts"]
        if engine not in tts.PACKAGES or tts.available(engine, self.options["python"]):
            return engine, False
        label = tts.LABELS[engine]
        if messagebox.askokcancel(self.t("speech_title"), self.t("engine_needed").format(engine=label)):
            return engine, True
        self._append(f"{label} is not installed: the machine's own voice will read the cards")
        return tts.AUTO, False

    def _install_engine(self) -> None:
        """The engine the app ships with is installed without being asked for.

        A first run that answers "no Python to build a Piper environment" is a first
        run whose cards are never spoken, and that install is the only thing between
        the two. Whether it is needed costs a Python per candidate interpreter, so it
        is asked off the window's thread, and the answer goes to the log.
        """
        engine = self.options["tts"]
        if engine in tts.PACKAGES:
            self._install_now(engine)

    def _install_now(self, engine: str) -> None:
        threading.Thread(target=self._install_answers, args=self._install_job(engine), daemon=True).start()

    def _install_job(self, engine: str) -> tuple[str, str, str]:
        """The worker's arguments, every one of them read on the window's thread.

        A thread that asks Tk for a word dies with "main thread is not in main loop",
        so nothing the install has to say may be read inside it.
        """
        return engine, self.speech_language(), self.t("engine_installing").format(engine=tts.LABELS[engine])

    def _install_answers(self, engine: str, language: str, notice: str) -> None:
        python = self.options.get("python") or None
        if tts.available(engine, python):
            return
        log.info(notice)
        try:
            built = tts.install_engine(engine, python, language)
        except tts.TtsError as exc:
            log.error("%s", exc)
            return
        if built and not python:
            # The app built its own Python: the next run has to be told which one.
            self.options["python"] = str(built)
            settings.save(self.options)

    def _book_language(self, source: Path, announce: Announce) -> str:
        """The language the voices being installed now have to cover.

        Read here rather than from the window's setting: a French book on an English
        machine needs a French voice, and the pack is the only thing that says so.
        Asked on the worker thread, because reading a pack is work the window must
        not sit still for; a pack that will not open leaves the language the window
        was already using, and the install goes ahead either way.
        """
        try:
            with open_pack(source) as pack:
                return card_language(announce, extract(pack, self.cut))[0]
        except Exception as exc:  # the conversion reports the pack itself, in full
            log.warning("no language read from %s: %s", source.name, exc)
            return announce.language

    def _run(
        self,
        source: Path,
        out_dir: Path,
        force: bool,
        announce: Announce,
        install: bool,
        cover: Path | None,
        drop: str | None,
        cut: str,
        fmt: str,
        crop: str,
        zoom: float = 1.0,
        focus: tuple[float, float] | None = None,
        negative: bool = False,
        captions: bool = False,
    ) -> None:
        try:
            if install:
                try:
                    tts.install_engine(announce.engine, announce.python, self._book_language(source, announce))
                except tts.TtsError as exc:
                    log.error("%s: the machine's own voice will read the cards instead", exc)
                    announce = replace(announce, engine=tts.AUTO)
            self.events.put(
                (
                    "code",
                    convert(
                        source,
                        out_dir,
                        force,
                        announce,
                        cover,
                        drop,
                        cut,
                        fmt,
                        crop,
                        zoom=zoom,
                        focus=focus,
                        negative=negative,
                        captions=captions,
                    ),
                )
            )
        except Exception as exc:
            # A round has to close whatever this thread hits. Without the code the
            # bar keeps turning and the log frame says nothing about the failure.
            # The frames under it go too: a failure on a machine nobody has is
            # only fixable from what the machine itself says.
            log.error("%s: %s", type(exc).__name__, exc, exc_info=True)
            self.events.put(("code", 1))

    def _drain(self) -> None:
        while True:
            try:
                item = self.events.get_nowait()
            except queue.Empty:
                break
            if item[0] == "code":
                done, self._on_done = self._on_done, None
                self.progress.stop()
                self.save_button.state(["!disabled"])
                self._report(item[1])
                if done is not None:
                    done(item[1])
            elif item[0] == "cover":
                self._took_cover(*item[1:])
            elif item[0] == "pack":
                self._took_pack(*item[1:])
            elif item[0] == "install_engine":
                self._offer_install(item[1])
            elif item[0] == "card":
                self._announced(item[1])
            else:
                self._append(item)

        self.root.after(100, self._drain)

    def _took_pack(self, job: int, parts: int, named: str, code: str) -> None:
        """Letter the preview's card with what the pack itself said."""
        if job != self._cover_job:
            return
        self._part_count, self._part_title, self._part_code = parts, named, code
        self._show_story_language()
        self._show_crop()

    def _took_cover(self, job: int, path: str, title: str, author: str) -> None:
        """Put a looked-up cover in the Image box.

        Only the newest lookup counts, and a cover the user typed or picked
        themselves is never replaced by a suggestion - but it is replaced by a
        search they asked for, which is why the answer carries what was asked.
        The author and the title the pack's own metadata gave go into the search
        boxes, so the search is ready to make even when none of the services had
        the book.
        """
        if job != self._cover_job:
            return
        self._spin_off()
        asked = self._cover_asked
        if asked == "pack":
            self._pack_title = title
            if author and not self.author_var.get().strip():
                self._auto_author = author
                self.author_var.set(author)
            if title and not self.title_var.get().strip():
                self._auto_title = title
                self.title_var.set(title)
        if self.cover_var.get().strip() and asked == "pack":
            return
        then, self._cover_then = self._cover_then, None
        if path:
            self._auto_cover = path
            self.cover_var.set(path)
            self._append(self.t("cover_found"))
            if asked == "link" and then is not None:
                # The run that waited for this picture goes on now that it is a file.
                self.root.after(0, then)
        elif asked == "link":
            # A link that gave no picture is not a cover: the box is left empty rather
            # than holding something every later reader of it would have to reject.
            self.cover_var.set("")
            self._append(self.t("cover_link_failed"))
        else:
            self._append(self.t("cover_none"))


class ChapterList:
    """The pack's chapters: listen to one, uncheck the ones to leave out.

    The pack stays open while the window is up, so listening costs no extra work.
    Durations and previews run on worker threads and post their results to a queue
    the Tk thread drains: widgets may only be touched from the thread that made
    them.

    The parts are drawn by one Treeview rather than a row of widgets each. A book
    cut to the sentence level has hundreds of parts, and X runs out of pixmaps
    long before that: the tree draws only the rows on screen, so the list stays
    scrollable at any level.
    """

    PART_COLUMNS = ("keep", "number", "title", "length")
    TICK_COLUMN = "#1"
    SHOWN_ROWS = 16

    def __init__(self, app: App, source: Path) -> None:
        self.app = app
        self.top = tk.Toplevel(app.root)
        self.top.title(app.t("chapters"))
        self.top.configure(background=PAPER)
        self.events: queue.Queue = queue.Queue()
        self.packs = contextlib.ExitStack()
        # The pack is given up when the last thread reading it is done, not when
        # the window closes: a parse that outlived the window was reading a folder
        # that had already been deleted, and reported the pack's own files missing.
        weakref.finalize(self, self.packs.close)
        try:
            self.pack = self.packs.enter_context(open_pack(source))
        except (DaisyPackError, OSError) as exc:
            self.top.destroy()
            app._append(f"ERROR: {exc}")
            return

        self.cut = app.cut
        self.reported = False
        app.chapter_list = self
        # The chapter now playing, when the app started the player itself: only
        # then can the window stop it, and only one chapter plays at a time.
        self._playing: subprocess.Popen | None = None
        grid = ttk.Frame(self.top, padding=12)
        grid.grid(sticky="nsew")
        grid.columnconfigure(0, weight=1)
        grid.rowconfigure(3, weight=1)

        # The levels stay shut until the pack has been read: which of them the
        # pack can actually cut at is only known once its own outline is in hand.
        chooser = ttk.Frame(grid)
        chooser.grid(row=0, column=0, sticky="w")
        ttk.Label(chooser, text=app.t("cut_level")).pack(side="left")
        self.cut_var = tk.StringVar(value=self.cut)
        self.cut_boxes: dict[str, ttk.Radiobutton] = {}
        for level in CUT_LEVELS:
            box = ttk.Radiobutton(
                chooser,
                text=app.t(f"cut_{level}"),
                variable=self.cut_var,
                value=level,
                command=lambda chosen=level: self._choose(chosen),
            )
            box.state(["disabled"])
            box.pack(side="left", padx=(8, 0))
            self.cut_boxes[level] = box
        self.counts = ttk.Label(grid, text=app.t("cut_hint"))
        self.counts.grid(row=1, column=0, sticky="w", pady=(2, 6))
        ttk.Label(grid, text=app.t("chapters_hint")).grid(row=2, column=0, sticky="w", pady=(0, 6))

        listing = ttk.Frame(grid)
        listing.grid(row=3, column=0, sticky="nsew")
        listing.columnconfigure(0, weight=1)
        listing.rowconfigure(0, weight=1)
        self.rows = ttk.Treeview(
            listing,
            columns=self.PART_COLUMNS,
            show="headings",
            height=self.SHOWN_ROWS,
            selectmode="browse",
        )
        self.rows.heading("keep", text="")
        self.rows.heading("number", text="#")
        self.rows.heading("title", text=app.t("chapters"))
        self.rows.heading("length", text=app.t("chapters_length"))
        self.rows.column("keep", width=34, stretch=False, anchor="center")
        self.rows.column("number", width=52, stretch=False, anchor="e")
        self.rows.column("title", width=320)
        self.rows.column("length", width=64, stretch=False, anchor="e")
        self.rows.grid(row=0, column=0, sticky="nsew")
        scroller = ttk.Scrollbar(listing, orient="vertical", command=self.rows.yview)
        scroller.grid(row=0, column=1, sticky="ns")
        self.rows.configure(yscrollcommand=scroller.set)
        self.rows.bind("<ButtonRelease-1>", self._click)
        self.rows.bind("<space>", lambda _event: self._toggle_selected())
        self.rows.bind("<Double-1>", lambda _event: self._play_selected())
        self.rows.bind("<Return>", lambda _event: self._play_selected())

        actions = ttk.Frame(grid)
        actions.grid(row=4, column=0, sticky="ew", pady=(8, 0))
        actions.columnconfigure(1, weight=1)
        self.play_button = ttk.Button(
            actions, text=app.t("chapters_play"), command=self._play_or_stop
        )
        self.play_button.grid(row=0, column=0)
        self.status = ttk.Label(actions, text="")
        self.status.grid(row=0, column=1, sticky="w", padx=(10, 0))
        ttk.Button(actions, text=app.t("opt_close"), command=self._close).grid(row=0, column=2)

        self.top.geometry("720x520")
        self._build()
        self.top.protocol("WM_DELETE_WINDOW", self._close)

        threading.Thread(target=self._measure, daemon=True).start()
        self.top.after(100, self._drain)

    def _build(self) -> None:
        """(Re)draw the part list for the level now chosen."""
        self.rows.delete(*self.rows.get_children())
        self.info, self.keep = [], []
        try:
            self.book = extract(self.pack, self.cut)
        except Exception as exc:  # a pack that will not be read has to say so here
            self._unread(exc)
            return
        self.info = chapters.describe(self.book, with_durations=False)
        self.cache = (
            Path(tempfile.gettempdir()) / "daisy2telmi-preview" / (slugify(self.book.title) or "pack")
        )

        for offset, info in enumerate(self.info):
            box = tk.BooleanVar(value=info.number not in self.app.dropped)
            self.keep.append(box)
            self.rows.insert(
                "",
                "end",
                iid=str(offset),
                values=(self._mark(box), info.number, info.title, ""),
            )
        self.status.configure(text="")

    def _unread(self, error: object) -> None:
        """Say what the pack could not be read as. The main window's log is behind
        this one, and cut levels that never opened look like a book with no chapters."""
        message = self.app.t("chapters_unread").format(error=error)
        self.status.configure(text=message)
        self.app._append(f"ERROR: {message}")

    @staticmethod
    def _mark(box: tk.BooleanVar) -> str:
        """The tick box drawn as text: a row of the tree is not a widget to put one in."""
        return "☒" if box.get() else "☐"

    def _toggle(self, index: int) -> None:
        box = self.keep[index]
        box.set(not box.get())
        self.rows.set(str(index), "keep", self._mark(box))

    def _click(self, event) -> str:
        """A click on the tick changes that part; anywhere else it just selects the row."""
        row = self.rows.identify_row(event.y)
        if row and self.rows.identify_column(event.x) == self.TICK_COLUMN:
            self._toggle(int(row))
        return ""

    def _selected(self) -> int | None:
        chosen = self.rows.selection()
        return int(chosen[0]) if chosen else None

    def _toggle_selected(self) -> None:
        index = self._selected()
        if index is not None:
            self._toggle(index)

    def _play_or_stop(self) -> None:
        """One button plays and stops. Two chapters over each other is not a choice
        anyone means to make, and there was no way back once a row was clicked."""
        if self._playing is not None:
            self._stop()
        else:
            self._play_selected()

    def _play_selected(self) -> None:
        index = self._selected()
        if index is not None:
            self._play(index)

    def _offer_levels(self, rows: list[chapters.LevelInfo]) -> None:
        """Open the levels the pack's own outline can cut at, and grey out the rest.

        A level that gives no more parts than the one above it only repeats that
        level, so the pack has nothing to cut at there. The audio is never asked.
        """
        available: list[str] = []
        parts = 0
        for row in rows:
            if row.parts > parts:
                available.append(row.level)
                parts = row.parts
        for level, box in self.cut_boxes.items():
            box.state(["!disabled"] if level in available else ["disabled"])
        if self.cut not in available:
            # A level the pack cannot cut at falls back to the coarser one beside
            # it. A book whose outline has no paragraph layer should open at
            # chapters, not at the sentence level nobody asked for.
            coarser = [
                level
                for level in available
                if CUT_LEVELS.index(level) < CUT_LEVELS.index(self.cut)
            ]
            self._choose(coarser[-1] if coarser else available[0])

    def _choose(self, level: str) -> None:
        """A different level is a different book: the old part numbers mean nothing."""
        if level == self.cut:
            return
        self.cut = level
        self.cut_var.set(level)
        self.app.cut = level
        self.app.dropped = set()
        if level != "chapter":
            # A book cut below its chapters is cut that finely to be read through: its
            # parts run on their own and the story stops at the end of a chapter.
            self.app.options["autoplay"] = True
            self.app.autoplay_var.set(True)
            self.app._show_pause()
        self.app._show_crop()
        self._build()
        threading.Thread(target=self._measure, daemon=True).start()

    def _measure(self) -> None:
        """Read the parts and their counts.

        The cut levels stay shut until the counts come back, so a worker that dies
        quietly leaves the window looking like the pack has no chapters at all.
        """
        cut = self.cut
        try:
            rows = chapters.describe(extract(self.pack, cut))
            self.events.put(("sizes", (cut, rows)))
            if not self.reported:
                self.reported = True
                books = {level: extract(self.pack, level) for level in CUT_LEVELS}
                self.events.put(("counts", chapters.describe_levels(books)))
        except Exception as exc:  # the pack's own problems belong in this window
            self.events.put(("unread", str(exc)))

    def _play(self, index: int) -> None:
        chapters.stop_preview(self._playing)
        self._playing = None
        self.play_button.state(["disabled"])
        self.status.configure(text=self.app.t("chapters_loading"))
        threading.Thread(
            target=self._render, args=(self.info[index], self.book.chapters[index]), daemon=True
        ).start()

    def _render(self, info: chapters.ChapterInfo, chapter) -> None:
        try:
            self.events.put(("play", (info.number, chapters.render_preview(info, chapter, self.cache))))
        except (chapters.ChapterError, audio.AudioError) as exc:
            self.events.put(("error", (info.number, str(exc))))

    def _stop(self) -> None:
        chapters.stop_preview(self._playing)
        self._playing = None
        self._enable()
        self.status.configure(text="")

    def _enable(self) -> None:
        """The button offers the one thing that makes sense now: Stop while a part
        plays, Listen otherwise."""
        self.play_button.state(["!disabled"])
        self.play_button.configure(
            text=self.app.t("chapters_stop" if self._playing is not None else "chapters_play")
        )

    def _drain(self) -> None:
        if not self.top.winfo_exists():
            return
        while True:
            try:
                item = self.events.get_nowait()
            except queue.Empty:
                break
            kind, payload = item[0], item[1]
            if kind == "sizes":
                cut, rows = payload
                if cut != self.cut:
                    continue  # a rebuild already replaced the rows these were measured for
                for offset, info in enumerate(rows):
                    self.rows.set(str(offset), "length", chapters.format_seconds(info.seconds))
            elif kind == "counts":
                self.counts.configure(
                    text="   ".join(
                        f"{self.app.t(f'cut_{row.level}')} {row.parts}"
                        f" ({chapters.format_seconds(row.each)})"
                        for row in payload
                    )
                )
                self._offer_levels(payload)
            elif kind == "unread":
                self._unread(payload)
            elif kind == "play":
                number, path = payload
                self.status.configure(
                    text=self.app.t("chapters_playing").format(n=number)
                )
                try:
                    self._playing = chapters.open_preview(path)
                except chapters.ChapterError as exc:
                    self._report("chapters_failed", number, exc)
                self._enable()
            else:
                number, reason = payload
                self._report("chapters_failed", number, reason)
                self._enable()
        if self._playing is not None and self._playing.poll() is not None:
            # The part ran to its end on its own: the button goes back to Listen.
            self._playing = None
            self._enable()
        self.top.after(100, self._drain)

    def _report(self, key: str, number: int, reason: object) -> None:
        """Say what went wrong under the user's cursor: the main window's log is
        behind this one, which is why a failed preview used to look like nothing."""
        message = self.app.t(key).format(n=number, error=reason)
        self.status.configure(text=message)
        self.app._append(f"ERROR: {message}")

    def _close(self) -> None:
        # "Chapters" can be pressed twice, so an older window must not clear the
        # tracking of the one still open.
        if self.app.chapter_list is self:
            self.app.chapter_list = None
        self.app.dropped = {
            info.number for info, box in zip(self.info, self.keep) if not box.get()
        }
        self.app.options["cut"] = self.cut
        settings.save(self.app.options)
        chapters.stop_preview(self._playing)
        self.top.destroy()


class Settings:
    """The rows a settings window is made of: labels, boxes, ticks and folder fields.

    Both windows below are built the same way, so the helpers live here: the boxes
    show translated names, and each one keeps its own list of keys in the same order
    as the values it displays, reading the choice back by index.
    """

    def __init__(self, app: App, title_key: str) -> None:
        self.app = app
        self.top = tk.Toplevel(app.root)
        self.top.title(app.t(title_key))
        self.top.configure(background=PAPER)
        self.top.resizable(False, False)
        self.grid = ttk.Frame(self.top, padding=12)
        self.grid.grid(sticky="nsew")
        self.grid.columnconfigure(1, weight=1)
        self.events: queue.Queue[tuple[str, object]] = queue.Queue()
        self.top.after(200, self._drain)

    def _label_text(self, key: str | None, labels: dict[str, str] | None = None) -> str:
        # "off" means two different things here: a silent card on one row, no
        # speech engine on another, so the row's own table decides which.
        if labels and key in labels:
            return self.app.t(labels[key])
        if key is None:
            return self.app.t("voice_default")
        if key == "auto":
            return self.app.t("engine_auto")
        if key == "off":
            return self.app.t("engine_off")
        if key in LANGUAGES:
            return f"{key} - {LANGUAGES[key].name}"
        return tts.LABELS.get(key, key)

    def _combo(
        self, parent: tk.Widget, keys: list[str | None], current: str | None,
        names: dict[str, str] | None = None,
    ) -> ttk.Combobox:
        box = ttk.Combobox(parent, values=[self._label_text(k, names) for k in keys], state="readonly", width=26)
        box.set(self._label_text(current if current in keys else keys[0], names))
        return box

    def _chosen(
        self, box: ttk.Combobox, keys: list[str | None], names: dict[str, str] | None = None
    ) -> str | None:
        labels = [self._label_text(k, names) for k in keys]
        try:
            return keys[labels.index(box.get())]
        except ValueError:
            return keys[0]

    def _row(self, parent: ttk.Frame, key: str, row: int, widget: tk.Widget) -> None:
        ttk.Label(parent, text=self.app.t(key)).grid(row=row, column=0, sticky="w", pady=4)
        widget.grid(row=row, column=1, sticky="ew", padx=(8, 0), pady=4)

    def _check(self, parent: ttk.Frame, key: str, row: int, var: tk.BooleanVar) -> None:
        """The tick and its words in one widget: a label in the left column would
        leave the box a hand's width from the sentence it belongs to."""
        ttk.Checkbutton(parent, text=self.app.t(key), variable=var).grid(
            row=row, column=0, columnspan=2, sticky="w", pady=4
        )

    def _folder(self, parent: ttk.Frame, key: str, row: int, value: str | None) -> ttk.Entry:
        """A folder that can be typed as well as picked, like the main window's two."""
        line = ttk.Frame(parent)
        box = ttk.Entry(line, width=28)
        box.insert(0, value or "")
        box.pack(side="left")
        ttk.Button(line, text=self.app.t("browse_output"), command=lambda: self._pick_folder(box)).pack(
            side="left", padx=(6, 0)
        )
        self._row(parent, key, row, line)
        return box

    def _pick_folder(self, box: ttk.Entry) -> None:
        path = self.app._browse("browse_output", box.get(), "folder")
        if path:
            box.delete(0, "end")
            box.insert(0, path)

    def _save(self, options: dict) -> None:
        self.app.options.update(options)
        try:
            settings.save(self.app.options)
        except OSError as exc:
            self.app._append(f"ERROR: could not save options: {exc}")

    def _drain(self) -> None:
        """Whatever a background job finished, in the order the jobs were started."""
        if not self.top.winfo_exists():
            return
        try:
            while True:
                self.finished(*self.events.get_nowait())
        except queue.Empty:
            pass
        self.top.after(200, self._drain)

    def finished(self, kind: str, result: object) -> None:
        """What a finished job changes: each page answers for the jobs it started."""


# Where a machine without a Python is sent to get one.
PYTHON_URL = "https://www.python.org/downloads/"


class Options(Settings):
    """The app's own settings, saved when the window closes.

    The voices are not here: they have a page of their own, because everything that
    goes with them - a voice per page, a reference clip, a sample to clone - is a
    page's worth, and what a card says is asked there beside the voice that says it.
    The engine and the Python it runs on are here, because they are
    one choice for the whole app, and finding a Python is the one thing on that page
    a user has to go and do themselves.
    """

    def __init__(self, app: App) -> None:
        super().__init__(app, "opt_title")

        self.language_keys = list(LANGUAGES)
        self._probe = 0
        self._answered = 0

        self.language_box = self._combo(self.grid, self.language_keys, app.options["language"])
        self._row(self.grid, "opt_language", 0, self.language_box)
        # The bar is not asked for here: the system's own bar is kept everywhere Tk can
        # only lose it by handing the window back to the app, and macOS is the one
        # system where it can be hidden and leave the window managed. Autoplay is not
        # asked here either: it belongs with the chapters it advances through.

        engine = ttk.Labelframe(self.grid, text=app.t("speech_section"), padding=(10, 6))
        engine.grid(row=1, column=0, columnspan=2, sticky="ew", pady=(14, 0))
        engine.columnconfigure(1, weight=1)
        self.python_box = ttk.Entry(engine, width=28)
        self.python_box.insert(0, app.options["python"] or "")
        self._row(engine, "opt_python", 0, self.python_box)
        self.engine_state = ttk.Label(engine, text="")
        self.engine_state.grid(row=1, column=0, columnspan=2, sticky="w", pady=(6, 0))
        # An engine cannot be installed on a machine with no Python to build it with,
        # so the page says where to get one rather than leave the install to fail.
        # Installing is not asked here: it is asked where an engine is chosen.
        self.python_button = ttk.Button(engine, text=app.t("speech_get_python"), command=self._get_python)
        self.python_button.grid(row=2, column=1, sticky="e", pady=(6, 0))
        self.python_button.grid_remove()
        ttk.Label(engine, text=app.t("speech_hint"), style="Brand.TLabel", wraplength=380).grid(
            row=3, column=0, columnspan=2, sticky="w", pady=(6, 0)
        )
        self.python_box.bind("<FocusOut>", lambda _e: self._show_engine())
        self._show_engine()

        folders = ttk.Labelframe(self.grid, text=app.t("opt_section_folders"), padding=(10, 6))
        folders.grid(row=2, column=0, columnspan=2, sticky="ew", pady=(14, 0))
        folders.columnconfigure(1, weight=1)
        self.input_box = self._folder(folders, "opt_input_dir", 0, app.options["input_dir"])
        self.output_box = self._folder(folders, "output_label", 1, app.options["output_dir"])
        # Telmi-Sync lists its own stories folder every time it starts, so a story
        # written there is on its shelf without an import. The folder is Telmi-Sync's
        # to make: a machine it has never run on has none, and nothing here makes one.
        self.telmi_button = ttk.Button(folders, text=app.t("browse_stories"), command=self._telmi_stories)
        self.telmi_button.grid(row=2, column=1, sticky="e", pady=(6, 0))
        asked = "opt_input_hint" if app.options.get("input_dir") else "opt_input_unset"
        self.folders_hint = ttk.Label(folders, text=app.t(asked), style="Brand.TLabel")
        self.folders_hint.grid(row=3, column=0, columnspan=2, sticky="w", pady=(6, 0))

        ttk.Button(self.grid, text=app.t("opt_close"), command=self._close).grid(
            row=3, column=0, columnspan=2, sticky="e", pady=(12, 0)
        )

    def _telmi_stories(self) -> None:
        stories = Path.home() / ".telmi" / "stories"
        if not stories.is_dir():
            self.app._append(f"no Telmi-Sync stories folder at {stories}: run Telmi-Sync once, or pick a folder yourself")
            return
        self.output_box.delete(0, "end")
        self.output_box.insert(0, str(stories))

    def engine(self) -> str:
        """The engine the speech page left saved: this window installs that one."""
        return self.app.options["tts"]

    def _show_engine(self) -> None:
        """Ask the engine whether it is there, off the window's own thread.

        Answering means starting a Python per candidate interpreter, which is slow
        enough to leave the window frozen if it is asked here.
        """
        self._probe += 1
        threading.Thread(
            target=self._engine_answers,
            args=(self._probe, self.engine(), self.python_box.get().strip() or None),
            daemon=True,
        ).start()

    def _engine_answers(self, token: int, engine: str, python: str | None) -> None:
        if engine not in tts.PACKAGES:
            self.events.put(("engine", (token, True, True)))
            return
        found = tts.available(engine, python)
        self.events.put(("engine", (token, found, found or tts.has_python(python))))

    def _engine_text(self, found: bool, has_python: bool = True) -> str:
        engine = self.engine()
        if engine not in tts.PACKAGES:
            return self.app.t("speech_builtin")
        if found:
            return self.app.t("speech_found").format(engine=tts.LABELS[engine])
        if not has_python:
            return self.app.t("speech_no_python")
        return self.app.t("speech_missing").format(engine=tts.LABELS[engine])

    def _get_python(self) -> None:
        open_url(PYTHON_URL)

    def finished(self, kind: str, result: object) -> None:
        if kind != "engine":
            return
        token, found, has_python = result
        self._answered = token
        if token != self._probe:
            return  # an answer about a Python the user has already typed over
        self.engine_state.configure(text=self._engine_text(found, has_python))
        if has_python:
            self.python_button.grid_remove()
        else:
            self.python_button.grid()

    def _close(self) -> None:
        input_dir = self.input_box.get().strip() or None
        output_dir = self.output_box.get().strip() or None
        moved_input = input_dir != self.app.options.get("input_dir")
        self._save(
            {
                "language": self._chosen(self.language_box, self.language_keys),
                "python": self.python_box.get().strip() or None,
                "input_dir": input_dir,
                "output_dir": output_dir,
            }
        )
        if moved_input:
            # A folder the user named beats the one a file list happened to end in.
            self.app.options["last_dir"] = None
            settings.save(self.app.options)
        self.app.language_var.set(self.app.options["language"])
        self.app.out_var.set(output_dir or "")
        self.app._show_paths()
        self.app._relabel()
        self.top.destroy()


class Speech(Settings):
    """Everything about the audio that is not said on the window's own Audio page.

    Two pages, because a card and a chapter are read differently: one says a few
    words, the other is a page of prose, and each gets its own voice, reference
    clip, device, speed and expressiveness. Every reading box starts at "Same as
    the cards", so a book is read with the cards' settings until one is changed.
    The engine and its installer are one choice for both, so they sit outside the
    pages, above and below them.

    The voices these pages read with are downloaded from here - a Piper voice, a
    Coqui model, Chatterbox's model; the engine they belong to is installed from the
    options, where the Python it runs on is written.
    A box the chosen engine has nothing to do with is greyed out rather than taken
    away, so the page keeps its shape when the engine changes.
    """

    # The rows each engine can answer for. `auto` gets the voice downloads because
    # it reaches for Piper as soon as a voice for the language is on the machine.
    # The Python an engine runs on is not one of them: it lives in the options.
    LIVE = {
        "auto": ("speed", "download"),
        tts.PIPER: ("voice", "speed", "download"),
        tts.ESPEAK: ("voice", "speed"),
        tts.SAY: ("voice", "speed"),
        tts.SAPI: ("voice", "speed"),
        tts.COQUI: ("voice", "clip", "device", "download"),
        tts.CHATTERBOX: ("voice", "clip", "device", "expression", "download"),
        tts.COMMAND: (),
        "off": (),
    }

    def __init__(self, app: App) -> None:
        super().__init__(app, "speech_title")

        self.engine_keys = engine_keys()
        self.device_keys = list(tts.DEVICES)
        self.speed_keys = list(tts.SPEEDS)
        self._try_folder: Path | None = None
        self._playing: subprocess.Popen | None = None
        self._probe = 0
        self._answered = 0
        # The engine the page opened with: an install is offered for a new choice, not
        # for the one the page was already showing.
        self._asked = app.options["tts"]

        self.engine_box = self._combo(self.grid, self.engine_keys, app.options["tts"])
        self._row(self.grid, "opt_engine", 0, self.engine_box)

        self.tabs = ttk.Notebook(self.grid)
        self.tabs.grid(row=1, column=0, columnspan=2, sticky="nsew", pady=(10, 0))
        cards = ttk.Frame(self.tabs, padding=(10, 8))
        cards.columnconfigure(1, weight=1)
        reading = ttk.Frame(self.tabs, padding=(10, 8))
        reading.columnconfigure(1, weight=1)
        self.tabs.add(cards, text=app.t("speech_tab_cards"))
        self.tabs.add(reading, text=app.t("speech_tab_reading"))

        self.voice_keys: list[str | None] = [None]
        self.voice_box = self._combo(cards, self.voice_keys, app.options["voice"])
        self.speed_box = self._combo(cards, self.speed_keys, app.options["speed"], SPEED_KEYS)
        self.expression_keys = list(tts.EXPRESSIONS)
        self.expression_box = self._combo(cards, self.expression_keys, app.options["expression"], EXPRESSION_KEYS)
        self.device_box = self._combo(cards, self.device_keys, app.options["device"])
        clip_row = ttk.Frame(cards)
        self.clip_box = ttk.Entry(clip_row, width=22)
        self.clip_box.insert(0, app.options["clip"] or "")
        self.clip_box.pack(side="left")
        ttk.Button(clip_row, text=app.t("browse_output"), command=self._pick_clip).pack(side="left", padx=(6, 0))
        try_row = ttk.Frame(cards)
        self.try_box = ttk.Entry(try_row, width=22)
        self.try_box.insert(0, self._sample_text())
        self.try_box.pack(side="left")
        self.try_button = ttk.Button(try_row, text=app.t("speech_try"), command=self._try_voice)
        self.try_button.pack(side="left", padx=(6, 0))

        # The language the book is read in is asked once, at the top of the window,
        # so both tabs start from it; the rows are numbered as the reading tab's so
        # the two answer the same questions in the same places.
        self._row(cards, "opt_voice", 1, self.voice_box)
        self._row(cards, "opt_speed", 2, self.speed_box)
        self._row(cards, "opt_expression", 3, self.expression_box)
        self._row(cards, "opt_device", 4, self.device_box)
        self._row(cards, "opt_clip", 5, clip_row)
        self._row(cards, "speech_try_label", 6, try_row)

        install = ttk.Labelframe(self.grid, text=app.t("speech_section"), padding=(10, 6))
        install.grid(row=2, column=0, columnspan=2, sticky="ew", pady=(14, 0))
        install.columnconfigure(1, weight=1)
        download_row = ttk.Frame(install)
        self.download_box = ttk.Combobox(download_row, values=[], state="readonly", width=26)
        self.download_box.pack(side="left")
        self.download_button = ttk.Button(
            download_row, text=app.t("speech_download"), command=self._download_voice
        )
        self.download_button.pack(side="left", padx=(6, 0))
        self._row(install, "speech_download_label", 0, download_row)

        ttk.Label(reading, text=app.t("speech_read_hint"), style="Brand.TLabel", wraplength=420).grid(
            row=0, column=0, columnspan=2, sticky="w", pady=(0, 8)
        )
        self.read_voice_keys: list[str | None] = [None]
        self.read_voice_box = self._combo(reading, self.read_voice_keys, app.options["read_voice"], READ_VOICE_KEYS)
        self.read_speed_keys: list[str | None] = [None, *self.speed_keys]
        self.read_speed_box = self._combo(reading, self.read_speed_keys, app.options["read_speed"], READ_SPEED_KEYS)
        self.read_expression_keys: list[str | None] = [None, *self.expression_keys]
        self.read_expression_box = self._combo(
            reading, self.read_expression_keys, app.options["read_expression"], READ_EXPRESSION_KEYS
        )
        self.read_device_keys: list[str | None] = [None, *self.device_keys]
        self.read_device_box = self._combo(reading, self.read_device_keys, app.options["read_device"], READ_DEVICE_KEYS)
        read_clip_row = ttk.Frame(reading)
        self.read_clip_box = ttk.Entry(read_clip_row, width=22)
        self.read_clip_box.insert(0, app.options["read_clip"] or "")
        self.read_clip_box.pack(side="left")
        ttk.Button(read_clip_row, text=app.t("browse_output"), command=lambda: self._pick_folder(self.read_clip_box)).pack(
            side="left", padx=(6, 0)
        )
        read_try_row = ttk.Frame(reading)
        self.read_try_box = ttk.Entry(read_try_row, width=22)
        self.read_try_box.insert(0, self._sample_text())
        self.read_try_box.pack(side="left")
        self.read_try_button = ttk.Button(read_try_row, text=app.t("speech_try"), command=self._try_reading)
        self.read_try_button.pack(side="left", padx=(6, 0))

        self._row(reading, "opt_voice", 1, self.read_voice_box)
        self._row(reading, "opt_speed", 2, self.read_speed_box)
        self._row(reading, "opt_expression", 3, self.read_expression_box)
        self._row(reading, "opt_device", 4, self.read_device_box)
        self._row(reading, "opt_clip", 5, read_clip_row)
        self._row(reading, "speech_try_label", 6, read_try_row)

        ttk.Button(self.grid, text=app.t("opt_close"), command=self._close).grid(
            row=3, column=0, columnspan=2, sticky="e", pady=(12, 0)
        )

        self.engine_box.bind("<<ComboboxSelected>>", lambda _e: self._show_engine())
        self._show_engine()

    def language(self) -> Language:
        return LANGUAGES.get(self.app.speech_language(), LANGUAGES["en"])

    def _sample_text(self) -> str:
        """What the try box holds before the user types anything: the card's own words."""
        return self.language().chapter.format(n=1)

    def _show_engine(self) -> None:
        """Grey out the rows this engine cannot answer for, then ask it what it has.

        The asking is the slow half of the page: a voice list can mean PowerShell,
        and whether an engine is installed means starting a Python per candidate
        interpreter. Asked on the window's own thread, the page sits frozen until
        every one of them has answered.
        """
        self._fill_downloads()
        self._show_live()
        engine = self._chosen(self.engine_box, self.engine_keys)
        if engine in (tts.PIPER, tts.COQUI):
            # Both keep their voices where the app can read them without asking: a
            # folder of models on disk, or a table the app already holds.
            self._fill_voices(tts.voices(engine, self.language(), any_language=True))
        elif engine in (None, "auto", "off"):
            self._fill_voices([])
        self._ask_engine()

    def _ask_engine(self) -> None:
        self._probe += 1
        threading.Thread(
            target=self._engine_answers,
            args=(self._probe, self._chosen(self.engine_box, self.engine_keys), self.language()),
            daemon=True,
        ).start()

    def _engine_answers(self, token: int, engine: str, language: Language) -> None:
        # None means the window already answered that half on its own thread.
        listed = engine not in (None, "auto", "off", tts.PIPER, tts.COQUI)
        names = tts.voices(engine, language, any_language=True) if listed else None
        installed = engine not in tts.PACKAGES or tts.available(engine, self.app.options.get("python") or None)
        self.events.put(("engine", (token, names, installed, engine)))

    def _offer_install(self, engine: str) -> None:
        """An engine picked from this box that the machine does not have: the install
        is asked here, where the choice was just made, and not on another page."""
        if messagebox.askokcancel(self.app.t("speech_title"), self.app.t("engine_needed").format(engine=tts.LABELS[engine])):
            threading.Thread(target=self._install_now, args=self.app._install_job(engine), daemon=True).start()

    def _install_now(self, engine: str, language: str, notice: str) -> None:
        """pip runs in the background: a neural engine is a gigabyte or more."""
        self.app._install_answers(engine, language, notice)
        self.events.put(("installed", engine))

    def _show_live(self) -> None:
        engine = self._chosen(self.engine_box, self.engine_keys)
        live = self.LIVE.get(engine, ())
        combos = (
            ("voice", self.voice_box), ("speed", self.speed_box), ("device", self.device_box),
            ("expression", self.expression_box),
            ("voice", self.read_voice_box), ("speed", self.read_speed_box),
            ("device", self.read_device_box), ("expression", self.read_expression_box),
        )
        for key, box in combos:
            box.configure(state="readonly" if key in live else "disabled")
        self.clip_box.configure(state="normal" if "clip" in live else "disabled")
        self.read_clip_box.configure(state="normal" if "clip" in live else "disabled")
        self.download_box.configure(state="readonly" if "download" in live else "disabled")
        self.download_button.state(["!disabled"] if "download" in live and self.download_keys else ["disabled"])

    def _fill_voices(self, names: list[str]) -> None:
        """Both voice boxes, filled with the voices the engine answered with."""
        keys: list[str | None] = [None, *names]
        self.voice_keys = keys
        self.voice_box.configure(values=[self._label_text(k) for k in keys])
        saved = self.app.options["voice"]
        self.voice_box.set(self._label_text(saved if saved in keys else None))
        self.read_voice_keys = keys
        self.read_voice_box.configure(values=[self._label_text(k, READ_VOICE_KEYS) for k in keys])
        read = self.app.options["read_voice"]
        self.read_voice_box.set(self._label_text(read if read in keys else None, READ_VOICE_KEYS))

    def _fill_downloads(self) -> None:
        """What this engine can fetch for this language and is not set up yet."""
        engine = self._chosen(self.engine_box, self.engine_keys)
        self.download_keys = tts.downloadable(engine, self.language().code)
        self.download_box.configure(values=self.download_keys)
        self.download_box.set(self.download_keys[0] if self.download_keys else "")

    def _pick_clip(self) -> None:
        path = self.app._browse("opt_clip", self.clip_box.get(), "file")
        if path:
            self.clip_box.delete(0, "end")
            self.clip_box.insert(0, path)

    def _settings(self, reading: bool = False) -> dict:
        """The engine settings as they stand in the boxes. Only this thread reads a widget.

        `reading` takes the reading page's boxes instead, keeping the cards' answer for
        each one left at "Same as the cards".
        """
        settings = {
            "engine": self._chosen(self.engine_box, self.engine_keys),
            "voice": self._chosen(self.voice_box, self.voice_keys),
            "speed": self._chosen(self.speed_box, self.speed_keys, SPEED_KEYS),
            "expression": self._chosen(self.expression_box, self.expression_keys, EXPRESSION_KEYS),
            "python": self.app.options.get("python") or None,
            "clip": self.clip_box.get().strip() or None,
            "device": self._chosen(self.device_box, self.device_keys),
        }
        if reading:
            settings.update(
                voice=self._chosen(self.read_voice_box, self.read_voice_keys, READ_VOICE_KEYS) or settings["voice"],
                speed=self._chosen(self.read_speed_box, self.read_speed_keys, READ_SPEED_KEYS) or settings["speed"],
                expression=self._chosen(self.read_expression_box, self.read_expression_keys, READ_EXPRESSION_KEYS)
                or settings["expression"],
                device=self._chosen(self.read_device_box, self.read_device_keys, READ_DEVICE_KEYS) or settings["device"],
                clip=self.read_clip_box.get().strip() or settings["clip"],
            )
        return settings

    def _download_voice(self) -> None:
        name = self._chosen(self.download_box, self.download_keys)
        if not name:
            return
        self.download_button.state(["disabled"])
        self.download_button.configure(text=self.app.t("speech_installing"))
        threading.Thread(target=self._job, args=("voice", name, self._settings()), daemon=True).start()

    def _try_voice(self) -> None:
        """Speak the sample with the settings as they stand, then play it back."""
        self._try_with("try", self.try_button, self.try_box, self._settings())

    def _try_reading(self) -> None:
        """Speak the sample the way the book would be read, then play it back."""
        self._try_with("read", self.read_try_button, self.read_try_box, self._settings(reading=True))

    def _try_with(self, kind: str, button: ttk.Button, box: ttk.Entry, options: dict) -> None:
        button.state(["disabled"])
        threading.Thread(
            target=self._job, args=(kind, box.get().strip() or self._sample_text(), options), daemon=True
        ).start()

    def _job(self, kind: str, argument: str, options: dict) -> None:
        code = self.language().code
        try:
            if kind == "voice":
                self.events.put(("voice", tts.download_voice(argument, options["python"], options["engine"])))
                return
            if self._try_folder is None:
                self._try_folder = Path(tempfile.mkdtemp(prefix="daisy2telmi-try-"))
            clip = options["clip"]
            if clip and not Path(clip).is_file():
                raise tts.TtsError(f"no such reference clip: {clip}")
            tts.synthesize(
                argument, code, self._try_folder / "try.mp3",
                options["engine"], options["voice"], self.app.options["command"], options["python"],
                clip, options["device"], options["speed"], options["expression"],
            )
            self.events.put((kind, self._try_folder / "try.mp3"))
        except tts.TtsError as exc:
            log.error("%s", exc)
            self.events.put((kind, None))

    def finished(self, kind: str, result: object) -> None:
        if kind == "engine":
            token, names, installed, engine = result
            self._answered = token
            if token != self._probe:
                return  # an answer about an engine the user has already moved on from
            if names is not None:
                self._fill_voices(names)
            if not installed and engine != self._asked:
                self._asked = engine
                self._offer_install(engine)
        elif kind == "installed":
            self._show_engine()
        elif kind == "voice":
            self.download_button.state(["!disabled"])
            self.download_button.configure(text=self.app.t("speech_download"))
            self._show_engine()
        elif kind in ("try", "read"):
            button = self.try_button if kind == "try" else self.read_try_button
            button.state(["!disabled"])
            button.configure(text=self.app.t("speech_try"))
            if result is not None:
                chapters.stop_preview(self._playing)
                self._playing = chapters.open_preview(Path(result))

    def _close(self) -> None:
        self._save(
            {
                "tts": self._chosen(self.engine_box, self.engine_keys),
                "voice": self._chosen(self.voice_box, self.voice_keys),
                "speed": self._chosen(self.speed_box, self.speed_keys, SPEED_KEYS),
                "expression": self._chosen(self.expression_box, self.expression_keys, EXPRESSION_KEYS),
                "clip": self.clip_box.get().strip() or None,
                "device": self._chosen(self.device_box, self.device_keys),
                "read_voice": self._chosen(self.read_voice_box, self.read_voice_keys, READ_VOICE_KEYS),
                "read_clip": self.read_clip_box.get().strip() or None,
                "read_device": self._chosen(self.read_device_box, self.read_device_keys, READ_DEVICE_KEYS),
                "read_speed": self._chosen(self.read_speed_box, self.read_speed_keys, READ_SPEED_KEYS),
                "read_expression": self._chosen(
                    self.read_expression_box, self.read_expression_keys, READ_EXPRESSION_KEYS
                ),
            }
        )
        self.app._show_engines()
        chapters.stop_preview(self._playing)
        if self._try_folder is not None:
            shutil.rmtree(self._try_folder, ignore_errors=True)
        self.top.destroy()


class Emulator:
    """The story a run just wrote, played on a machine drawn like the icon's.

    The story folder is already the machine's: `nodes.json` says where each button
    leads, `images/` and `audios/` hold what a stage shows and plays. A Lunii run
    writes a second copy of the story as a STUdio archive, and that one holds the
    greys its screen shows, so the window opens the archive when it finds one.
    Reading either back answers "what will the teller do" without a teller, at the
    sizes that machine's screen has.

    The face is drawn rather than widgeted, because Tk's buttons are rectangles and
    the teller's keys are round: the screen on its bezel, the speaker's grill beside
    it, and along the bottom the two chapter keys, Back, Play/pause and OK. A chapter
    keeps the card it was read from on the screen for a few seconds, then the screen
    goes dark until a key is pressed.
    """

    # The screen is drawn at the width every player's card can be blown up to by
    # whole pixels, which keeps the sixteen greys of a Lunii card grey.
    SCREEN_WIDTH = 640
    SCREEN_HEIGHT = 480

    PAD = 14
    GAP = 22
    RING = 54
    DIAL = 32
    SPACING = 96
    CAPTION = 24

    # How long a chapter keeps its card on the screen before the screen goes dark.
    CARD_HOLD = 4000

    KEYS = ("up", "down", "ok", "pause", "home")
    # The words on the face are the player's, not the graph's: the key the graph calls
    # `home` is the way back, and the one that holds a clip is Play/pause.
    CAPTIONS = {"ok": "emu_ok", "pause": "emu_play", "home": "emu_back"}

    FACE_WIDTH = PAD * 2 + SCREEN_WIDTH + GAP + RING * 2
    FACE_HEIGHT = PAD * 2 + SCREEN_HEIGHT + GAP + DIAL * 2 + CAPTION

    def __init__(self, app: App, folder: Path) -> None:
        self.app = app
        try:
            self.story = emulator.read_story(folder)
        except emulator.EmulatorError as exc:
            app._append(f"ERROR: {self.t('emu_failed').format(error=exc)}")
            return

        self.top = tk.Toplevel(app.root)
        self.top.title(f"{app.t('test_story')}: {self.story.title}")
        self.top.configure(background=PAPER)
        self.top.resizable(False, False)

        self.photo: tk.PhotoImage | None = None
        self.player: subprocess.Popen | None = None
        self.sound: Path | None = None
        self.current: str | None = None
        self.paused = False
        # How much of the clip has already been heard, which is where a resumed one
        # starts and what a side button steps through.
        self.heard = 0.0
        self.started = 0.0
        self._fade: str | None = None
        self.keys: dict[str, dict[str, int]] = {}

        body = ttk.Frame(self.top, padding=12)
        body.grid(sticky="nsew")
        self.name = ttk.Label(body, text=self.story.title, foreground=INK)
        self.name.grid(row=0, column=0, pady=(0, 6))

        self.face = tk.Canvas(
            body,
            width=self.FACE_WIDTH,
            height=self.FACE_HEIGHT,
            background=PAPER,
            highlightthickness=0,
        )
        self.face.grid(row=1, column=0)
        self.status = ttk.Label(
            body,
            text=f"{self.t('emu_hint')} {self.t('emu_dimmed')}",
            foreground=MUTED,
            wraplength=self.SCREEN_WIDTH,
        )
        self.status.grid(row=2, column=0, sticky="w", pady=(8, 0))

        self._build()

        self.top.bind("<Up>", lambda _event: self._press("up"))
        self.top.bind("<Down>", lambda _event: self._press("down"))
        self.top.bind("<Return>", lambda _event: self._press("ok"))
        self.top.bind("<space>", lambda _event: self._press("ok"))
        self.top.bind("<BackSpace>", lambda _event: self._press("home"))
        self.top.bind("<h>", lambda _event: self._press("home"))
        self.top.bind("<p>", lambda _event: self._press("pause"))
        self.top.protocol("WM_DELETE_WINDOW", self._close)

        self._show(self.story.start)
        self.top.after(200, self._poll)

    def t(self, key: str) -> str:
        return self.app.t(key)

    def _build(self) -> None:
        """The machine's face: the shell, the screen, the ring, and the keys."""
        left, top = 2, 2
        right, bottom = self.FACE_WIDTH - 2, self.FACE_HEIGHT - 2
        corner = 18
        self.face.create_polygon(
            (
                left + corner, top,
                right - corner, top,
                right, top + corner,
                right, bottom - corner,
                right - corner, bottom,
                left + corner, bottom,
                left, bottom - corner,
                left, top + corner,
            ),
            smooth=True,
            fill=MACHINE,
            outline=LINE,
        )
        self.face.create_rectangle(
            self.PAD,
            self.PAD,
            self.PAD + self.SCREEN_WIDTH,
            self.PAD + self.SCREEN_HEIGHT,
            fill=BEZEL,
            outline=LINE,
        )

        ring_x = self.PAD + self.SCREEN_WIDTH + self.GAP + self.RING
        ring_y = self.PAD + self.SCREEN_HEIGHT // 2
        self.face.create_oval(
            ring_x - self.RING,
            ring_y - self.RING,
            ring_x + self.RING,
            ring_y + self.RING,
            fill=DIAL,
            outline=DIAL_EDGE,
            width=6,
        )
        # The speaker's grill: holes on a grid, kept inside the circle they are drilled in.
        reach = self.RING - 12
        for dy in range(-reach, reach + 1, 12):
            for dx in range(-reach, reach + 1, 12):
                if dx * dx + dy * dy <= reach * reach:
                    self.face.create_oval(
                        ring_x + dx - 3, ring_y + dy - 3, ring_x + dx + 3, ring_y + dy + 3,
                        fill=SLOT, outline=SLOT,
                    )

        first = (self.FACE_WIDTH - (len(self.KEYS) - 1) * self.SPACING) / 2
        y = self.PAD + self.SCREEN_HEIGHT + self.GAP + self.DIAL
        for column, button in enumerate(self.KEYS):
            label = None if button in ("up", "down") else self.t(self.CAPTIONS[button])
            self._key(button, first + column * self.SPACING, y, label)
        # The two side keys are one pair with one word: they step from one chapter to
        # the next, which is what both machines do with them.
        self.pair_caption = self.face.create_text(
            first + self.SPACING / 2,
            y + self.DIAL + 12,
            text=self.t("emu_chapters"),
            fill=MUTED,
            font=("TkDefaultFont", 9),
        )

    def _key(self, button: str, x: float, y: float, label: str | None) -> None:
        group = f"key-{button}"
        dial = self.face.create_oval(
            x - self.DIAL,
            y - self.DIAL,
            x + self.DIAL,
            y + self.DIAL,
            fill=DIAL,
            outline=DIAL_EDGE,
            tags=(group,),
        )
        icons = []
        if button == "home":
            # Back, as the road's own u-turn: up on one side, over, and down the other.
            icons.append(
                self.face.create_line(x + 17, y - 2, x + 17, y + 14, width=5, fill=DIAL_INK, tags=(group,))
            )
            icons.append(
                self.face.create_arc(
                    x - 11, y - 14, x + 17, y + 10, start=0, extent=180, style="arc",
                    width=5, outline=DIAL_INK, tags=(group,),
                )
            )
            icons.append(
                self.face.create_polygon(
                    (x - 17, y - 2), (x - 5, y - 2), (x - 11, y + 11),
                    fill=DIAL_INK, outline=DIAL_INK, tags=(group,),
                )
            )
        elif button == "pause":
            # Play and pause on one key: the triangle, and the two bars beside it.
            icons.append(
                self.face.create_polygon(
                    (x - 16, y - 12), (x - 16, y + 12), (x - 3, y),
                    fill=DIAL_INK, outline=DIAL_INK, tags=(group,),
                )
            )
            for bar in (5, 13):
                icons.append(
                    self.face.create_rectangle(
                        x + bar, y - 12, x + bar + 6, y + 12, fill=DIAL_INK, outline=DIAL_INK, tags=(group,)
                    )
                )
        elif button == "ok":
            # The tick of a checkbox: one line, bent.
            icons.append(
                self.face.create_line(
                    x - 13, y + 1, x - 4, y + 12, x + 14, y - 11,
                    width=6, fill=DIAL_INK, capstyle="projecting", joinstyle="round", tags=(group,),
                )
            )
        else:
            points = {
                "up": [(x, y - 14), (x - 15, y + 10), (x + 15, y + 10)],
                "down": [(x, y + 14), (x - 15, y - 10), (x + 15, y - 10)],
            }[button]
            icons.append(self.face.create_polygon(*points, fill=DIAL_INK, outline=DIAL_INK, tags=(group,)))
        caption = 0
        if label:
            caption = self.face.create_text(
                x, y + self.DIAL + 12, text=label, fill=MUTED, font=("TkDefaultFont", 9), tags=(group,)
            )
        self.keys[button] = {"dial": dial, "icons": icons, "caption": caption, "state": "on"}
        self.face.tag_bind(group, "<Button-1>", lambda _event, which=button: self._press(which))

    def caption(self, button: str) -> str:
        item = self.pair_caption if button in ("up", "down") else self.keys[button]["caption"]
        return self.face.itemcget(item, "text")

    def lit(self, button: str) -> bool:
        return self.keys[button]["state"] != "off"

    def _show(self, stage_id: str) -> None:
        stage = self.story.stages.get(stage_id)
        if stage is None:
            return
        self.current = stage_id
        self._silence()
        self._screen(stage)
        if stage.audio is not None and stage.audio.is_file():
            self.sound = stage.audio
            self._play()
        self._light(stage)

    def _screen(self, stage: emulator.Stage) -> None:
        """The stage's own picture; a chapter keeps the card it was read from, then
        leaves the screen dark until the next key."""
        self._cancel()
        if stage.image is not None and stage.image.is_file():
            self._draw(stage.image)
        elif self.photo is not None:
            self._fade = self.top.after(self.CARD_HOLD, self._dark)
        else:
            self._draw(None)

    def _dark(self) -> None:
        self._fade = None
        self._draw(None)

    def _cancel(self) -> None:
        if self._fade is not None:
            self.top.after_cancel(self._fade)
            self._fade = None

    def _draw(self, image: Path | None) -> None:
        """The picture blown up by whole pixels and centred on the screen, which keeps
        the card's own pixels square the way the machine's screen shows them."""
        self.face.delete("picture")
        self.photo = None
        if image is None or not image.is_file():
            return
        photo = tk.PhotoImage(data=image.read_bytes())
        step = max(1, min(self.SCREEN_WIDTH // photo.width(), self.SCREEN_HEIGHT // photo.height()))
        self.photo = photo.zoom(step, step) if step > 1 else photo
        self.face.create_image(
            self.PAD + self.SCREEN_WIDTH // 2, self.PAD + self.SCREEN_HEIGHT // 2, image=self.photo, tags=("picture",)
        )

    def _light(self, stage: emulator.Stage | None) -> None:
        """A key is lit when the stage left it on; the machine ignores the rest."""
        if stage is None:
            return
        clip = self.sound is not None
        # The side pair are the chapter keys: each is lit where the book has a chapter
        # on that side of this one, and on a Lunii where its card left the wheel on.
        self._paint("up", stage.controls.wheel or bool(emulator.chapter_step(self.story, stage.id, -1)))
        self._paint("down", stage.controls.wheel or bool(emulator.chapter_step(self.story, stage.id, 1)))
        self._paint("ok", stage.controls.ok)
        self._paint("home", stage.controls.home)
        self._paint("pause", clip, playing=self.player is not None)

    def _paint(self, button: str, on: bool, playing: bool = False) -> None:
        key = self.keys[button]
        key["state"] = "playing" if on and playing else "on" if on else "off"
        fill, edge, ink = {
            "off": (OFF, OFF_EDGE, OFF_INK),
            "on": (DIAL, DIAL_EDGE, DIAL_INK),
            "playing": (ACCENT, ACCENT, "#ffffff"),
        }[key["state"]]
        self.face.itemconfigure(key["dial"], fill=fill, outline=edge)
        for icon in key["icons"]:
            # A line is coloured by `fill` and an arc by `outline`; a shape takes both.
            kind = self.face.type(icon)
            if kind == "arc":
                self.face.itemconfigure(icon, outline=ink)
            elif kind == "line":
                self.face.itemconfigure(icon, fill=ink)
            else:
                self.face.itemconfigure(icon, fill=ink, outline=ink)

    def _play(self) -> None:
        if self.sound is None:
            return
        try:
            self.player = chapters.open_preview(self.sound, self.heard)
        except chapters.ChapterError as exc:
            self.status.configure(text=str(exc))
            self.player = None
        self.started = time.monotonic()
        self.paused = False

    def _pause(self) -> None:
        if self.player is not None:
            self.heard += time.monotonic() - self.started
            self._stop()
            self.paused = True
        elif self.sound is not None:
            self._play()
        self._light(self.story.stages.get(self.current or ""))

    def _press(self, button: str) -> None:
        if self.current is None:
            return
        stage = self.story.stages.get(self.current)
        if stage is None or not self.lit(button):
            return
        self._cancel()
        if button == "pause":
            self._pause()
            return
        nxt = emulator.walk(self.story, self.current, button)
        if nxt and nxt != self.current:
            self._show(nxt)
        else:
            self._screen(stage)

    def _stop(self) -> None:
        chapters.stop_preview(self.player)
        self.player = None

    def _silence(self) -> None:
        self._stop()
        self.sound = None
        self.heard = 0.0
        self.paused = False

    def _poll(self) -> None:
        """The clip ends on its own, and an autoplayed one forces the next stage."""
        if not self.top.winfo_exists():
            return
        if self.player is not None and self.player.poll() is not None:
            self.player = None
            self.heard = 0.0
            stage = self.story.stages.get(self.current or "")
            self._light(stage)
            if stage is not None and stage.controls.autoplay:
                nxt = emulator.walk(self.story, self.current or "", "next")
                if nxt and nxt != self.current:
                    self._show(nxt)
        self.top.after(200, self._poll)

    def _close(self) -> None:
        self._cancel()
        chapters.stop_preview(self.player)
        self.player = None
        self.top.destroy()


def attach_log_handler(app: App, verbose: bool = False) -> logging.Handler:
    """Route the converter's log records into the app's queue.

    Verbose is the whole log, ffmpeg's own command lines included: on Windows the
    window is the only place the app can write, because its build carries no terminal.
    """
    level = logging.DEBUG if verbose else logging.INFO
    handler = _QueueHandler(app.events, level)
    handler.setFormatter(logging.Formatter("%(levelname)s: %(message)s"))
    log.addHandler(handler)
    # Without this the logger keeps the root's WARNING level and the window stays blank.
    log.setLevel(level)
    return handler


def make_root() -> tk.Misc:
    """tkinterdnd2 only adds the drop methods to its own root class."""
    return TkinterDnD.Tk() if TkinterDnD else tk.Tk()


def run_gui(verbose: bool = False) -> int:
    root = make_root()
    app = App(root)
    root.title(f"{app.t('app_title')} {__version__}")
    handler = attach_log_handler(app, verbose)
    app._install_engine()
    try:
        root.mainloop()
    finally:
        log.removeHandler(handler)
    return 0
