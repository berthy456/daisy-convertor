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
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import tkinter as tk
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
from .telmi_builder import ANNOUNCE_MODES, Announce, slugify

try:
    from tkinterdnd2 import DND_FILES, TkinterDnD
except ImportError:  # drag and drop is a bonus, browsing works without it
    DND_FILES = None
    TkinterDnD = None

log = logging.getLogger("daisy2telmi")

ANNOUNCE_KEYS = {mode: f"announce_{mode}" for mode in ANNOUNCE_MODES}
SPEED_KEYS = {speed: f"speed_{speed}" for speed in tts.SPEEDS}


def engine_keys() -> list[str]:
    """Every engine the app can be told to read with: automatic first, silence last."""
    return ["auto"] + [engine.name for engine in tts.engines()] + ["off"]

# The crop frame is drawn this wide, whatever the player's ratio: the same room to
# move the cover in on every machine, and the web page draws it at the same size.
# It is wider than any screen it previews, so the cover's own lettering can be read
# in it while it is being placed.
CROP_WIDTH = 720

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
            fieldbackground=[("readonly", FIELD)],
            foreground=[("disabled", MUTED)],
        )
    # A readonly combo selects its text in the accent colour, which reads as a
    # button rather than a field; keep it the field's own colours.
    style.map(
        "TCombobox",
        selectbackground=[("readonly", FIELD)],
        selectforeground=[("readonly", INK)],
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


# The window's own bar: the mark, what the window is for, and the three controls that
# stand in for a system bar's. Tk cannot round the corners of a window it does not
# draw, so the bar draws a rounded card of its own and sets its controls in it.
BAR_WIDTH = 900
BAR_INSET = 10
BAR_RADIUS = 14
BAR_MARK = 112
# The card has to be taller than the mark it holds, or the mark spills past its
# rounded edge: 140 - 2 * 10 = 120 against a 112 mark.
BAR_HEIGHT = 140
BAR_BUTTON = 30
BAR_BUTTON_RADIUS = 9
BAR_GAP = 8
BAR_HOVER = "#565274"
BAR_CLOSE_HOVER = "#8f3b34"

# The window opens at a size of its own: Tk asks each widget how big it wants to be,
# hands the total to the window manager and lets it decide, and a window the manager
# does not manage is only as big as it is told. The floor is the size the window needs
# to show its bar and the three columns of work under it.
MIN_WIDTH = 900
MIN_HEIGHT = 560
WINDOW_MARGIN = 40


class TitleBar(tk.Canvas):
    """The window's own bar: the mark, its name, and the controls it needs.

    The controls call Tk's own window commands, so they work whether or not the system
    bar is there. They are only drawn when the bar has to stand in for that bar: with
    the system bar in place the window already has its buttons, and this one is the
    mark and the name.
    """

    def __init__(self, app: App) -> None:
        super().__init__(
            app.root,
            width=BAR_WIDTH,
            height=BAR_HEIGHT,
            background=PAPER,
            highlightthickness=0,
            borderwidth=0,
        )
        self.app = app
        self.mark = app._mark_photo(BAR_MARK)
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
        left = BAR_INSET + BAR_RADIUS + BAR_MARK + 16
        self._title_y = height // 2 - 11
        self._words["title"] = self.create_text(
            left,
            self._title_y,
            anchor="w",
            text=self.app.t("app_title"),
            fill=INK,
            font=("TkDefaultFont", 15, "bold"),
        )
        title = self.bbox(self._words["title"])
        self._words["version"] = self.create_text(
            (title[2] if title else left) + 10,
            self._title_y,
            anchor="w",
            text=__version__,
            fill=MUTED,
            font=("TkDefaultFont", 11),
        )
        self._words["headline"] = self.create_text(
            left,
            height // 2 + 11,
            anchor="w",
            text=self.app.t("app_headline"),
            fill=MUTED,
            font=("TkDefaultFont", 10),
        )
        self._controls(width, height)
        if self.frameless:
            # With no system bar to pull on, the bar's own card and words move the window.
            for item in (card, *self._words.values()):
                self.tag_bind(item, "<ButtonPress-1>", self._press_bar)
                self.tag_bind(item, "<B1-Motion>", self._drag_bar)

    def relabel(self) -> None:
        self.itemconfigure(self._words["title"], text=self.app.t("app_title"))
        self.itemconfigure(self._words["headline"], text=self.app.t("app_headline"))
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


class _QueueHandler(logging.Handler):
    """Ship log records to the Tk thread; widgets are only touched from the main thread."""

    def __init__(self, sink: queue.Queue) -> None:
        super().__init__(logging.INFO)
        self.sink = sink

    def emit(self, record: logging.LogRecord) -> None:
        self.sink.put_nowait(self.format(record))


def _tag(line: str) -> tuple[str, ...]:
    """Which of the log's two emphases a line gets, however the report indents it."""
    head = line.strip().split(":", 1)[0]
    if head in ("ERROR", "CRITICAL"):
        return ("error",)
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
        self.frameless = bool(self.options.get("frameless"))
        self._maximised = False
        self._restore_geometry = ""
        self.pack_var = tk.StringVar()
        self.out_var = tk.StringVar()
        self._auto_out = ""
        # What the last pack's lookup filled in. A cover, an author and a title the
        # services gave belong to the pack they were found for; only what the user
        # typed or picked is a setting that carries to the next pack.
        self._auto_cover = ""
        self._auto_author = ""
        self._auto_title = ""
        theme(root)
        set_icon(root)
        self.pack_var.trace_add("write", self._follow_pack)
        self.cover_var = tk.StringVar()
        self.author_var = tk.StringVar()
        self.title_var = tk.StringVar()
        # A looked-up cover has to stay on disk while the cards are drawn, so the
        # session keeps one folder for them and each lookup replaces the last.
        self._cover_dir: Path | None = None
        self._cover_job = 0
        self._spin_job = None
        self._pack_title: str | None = None
        self.crop_var = tk.StringVar(value=self.options["cover_crop"])
        self.format_var = tk.StringVar(value=self.options["format"])
        # The cover drawn at the frame's ratio: the file it came from, the player it
        # was drawn for, the zoom it was drawn at, then the picture and its size.
        self._crop_cover: tuple[str, str, float, bool, tuple[bytes, tuple[int, int]]] | None = None
        self._crop_photo: tk.PhotoImage | None = None
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
        self._on_done = None
        self._labels: dict[str, tk.Widget] = {}
        self._build()
        self._size_window()
        if not self.options.get("input_dir") and not self.options.get("output_dir"):
            self._append(self.t("log_folders_missing"))
        if self.frameless:
            self._take_system_bar()
        root.after(100, self._drain)

    def t(self, key: str) -> str:
        return text(self.language_var.get(), key)

    def _label(self, parent: tk.Widget, key: str, **kwargs) -> ttk.Label:
        widget = ttk.Label(parent, text=self.t(key), **kwargs)
        self._labels[key] = widget
        return widget

    def _mark_photo(self, side: int) -> tk.PhotoImage | None:
        """The mark at the size a frame asks for. Tk cannot scale a photo image, so
        tools/make_icons.py writes one PNG for each size the window draws."""
        path = ICON.parent / f"icon-{side}.png"
        return tk.PhotoImage(master=self.root, file=str(path)) if path.is_file() else None

    def _button(self, parent: tk.Widget, key: str, command, **kwargs) -> ttk.Button:
        widget = ttk.Button(parent, text=self.t(key), command=command, **kwargs)
        self._labels[key] = widget
        return widget

    def _build(self) -> None:
        self.root.columnconfigure(0, weight=1)
        self.root.rowconfigure(1, weight=1)

        # The window's own bar along the top — the mark, its name and its controls —
        # and the name again along the bottom: the work itself sits between them.
        self.bar = TitleBar(self)
        self.bar.grid(row=0, column=0, sticky="ew")

        grid = ttk.Frame(self.root, padding=(12, 0))
        grid.grid(row=1, column=0, sticky="nsew")
        # Three columns: the picture on the left, the knobs that change it in the
        # middle, the log on the right. Both stretchy columns share the spare width
        # when the window is widened.
        grid.columnconfigure(1, weight=1)
        grid.columnconfigure(2, weight=1)
        grid.rowconfigure(8, weight=1)

        self._label(grid, "pack_label").grid(row=0, column=0, sticky="w")
        ttk.Entry(grid, textvariable=self.pack_var).grid(row=1, column=0, columnspan=2, sticky="ew", pady=(0, 8))
        buttons = ttk.Frame(grid)
        buttons.grid(row=1, column=2, sticky="e", padx=(6, 0))
        self._button(buttons, "browse_zip", self._pick_pack_file).pack(side="left")
        self._button(buttons, "browse_folder", self._pick_pack_dir).pack(side="left", padx=(4, 0))

        self._label(grid, "output_label").grid(row=2, column=0, sticky="w")
        ttk.Entry(grid, textvariable=self.out_var).grid(row=3, column=0, columnspan=2, sticky="ew", pady=(0, 8))
        self._button(grid, "browse_output", self._pick_out_dir).grid(row=3, column=2, sticky="e", padx=(6, 0))

        # The player comes first: its screen decides the size every picture is drawn at.
        # Five names do not fit on one line in every language, so they share a box.
        format_row = ttk.Frame(grid)
        format_row.grid(row=4, column=0, columnspan=3, sticky="w", pady=(0, 8))
        self._label(format_row, "format_label").pack(side="left", padx=(0, 10))
        self.format_box = ttk.Combobox(format_row, values=self.format_names(), state="readonly", width=20)
        self.format_box.pack(side="left")
        self.format_box.bind("<<ComboboxSelected>>", lambda _event: self._choose_format())
        self._show_formats()

        # The voice the cards get is picked here, next to the machine they are read
        # on; everything that goes with it - voices, installs, a sample to clone -
        # sits behind its own button, on a page of its own.
        speech_row = ttk.Frame(grid)
        speech_row.grid(row=5, column=0, columnspan=3, sticky="w", pady=(0, 8))
        self._label(speech_row, "speech_label").pack(side="left", padx=(0, 10))
        self.speech_box = ttk.Combobox(speech_row, values=self.engine_names(), state="readonly", width=20)
        self.speech_box.pack(side="left")
        self.speech_box.bind("<<ComboboxSelected>>", lambda _event: self._choose_engine())
        self._button(speech_row, "speech_configure", self._open_speech).pack(side="left", padx=(10, 0))
        self._show_engines()

        cover_row = ttk.Frame(grid)
        cover_row.grid(row=6, column=0, columnspan=3, sticky="w", pady=(0, 4))
        self._label(cover_row, "cover_label").pack(side="left")
        ttk.Entry(cover_row, textvariable=self.cover_var, width=40).pack(side="left", padx=(6, 8))
        self._button(cover_row, "browse_cover", self._pick_cover).pack(side="left")
        # A lookup takes a second or two on its own thread, and three services are
        # asked before the answer says there is nothing: the window has to show it
        # is waiting. An indeterminate bar is the closest Tk comes to a spinner.
        self.cover_spin = ttk.Progressbar(cover_row, mode="indeterminate", length=44)

        # When none of those services has the book, the two words the user types
        # are the search: an image search on the author and the title, on either
        # of the two engines that answer without an account.
        search = ttk.Frame(grid)
        search.grid(row=7, column=0, columnspan=3, sticky="w", pady=(6, 0))
        self._label(search, "cover_author").pack(side="left")
        ttk.Entry(search, textvariable=self.author_var, width=18).pack(side="left", padx=(6, 12))
        self._label(search, "cover_book_title").pack(side="left")
        ttk.Entry(search, textvariable=self.title_var, width=24).pack(side="left", padx=(6, 12))
        self._label(search, "cover_search_on").pack(side="left", padx=(0, 6))
        self._button(search, "cover_search_ddg", lambda: self._search_cover("ddg")).pack(side="left")
        self._button(search, "cover_search_google", lambda: self._search_cover("google")).pack(
            side="left", padx=(4, 0)
        )

        # The frame, its knobs and the log share the last row: a frame this wide
        # would push the log below the bottom of a laptop screen.
        #
        # The frame the chosen player shows, drawn from the cover: drag it to move,
        # the wheel or the arrows to enlarge. What is inside here is what the story
        # carries, so the choice is made on the picture instead of described to it.
        self.crop_frame = tk.Canvas(
            grid, highlightthickness=0, cursor="fleur", takefocus=1, background=SUNK
        )
        self.crop_frame.grid(row=8, column=0, sticky="n", pady=(6, 0))
        self._crop_item = self.crop_frame.create_image(0, 0, anchor="nw")
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

        # Everything that changes what the frame shows stands beside it, in the order
        # the eye goes over a picture: which band to keep, how far in, then the way
        # the player's own grey screen reads the ink.
        side = ttk.Frame(grid)
        side.grid(row=8, column=1, sticky="n", padx=(12, 0))
        self.crop_side = side
        self._label(side, "cover_crop_label").pack(anchor="w")
        self.crop_boxes: dict[str, ttk.Radiobutton] = {}
        for mode in images.CROP_MODES:
            box = ttk.Radiobutton(
                side,
                text=self.t(f"crop_{mode}"),
                variable=self.crop_var,
                value=mode,
                command=self._pick_crop,
            )
            self._labels[f"crop_{mode}"] = box
            box.pack(anchor="w", pady=(2, 0))
            self.crop_boxes[mode] = box
        zoom = ttk.Frame(side)
        zoom.pack(anchor="w", pady=(8, 0))
        self._label(zoom, "crop_zoom").pack(side="left", padx=(0, 6))
        self.crop_zoom = ttk.Scale(
            zoom, from_=images.MIN_ZOOM, to=images.MAX_ZOOM, length=110, command=self._slide_crop
        )
        self.crop_zoom.pack(side="left")
        self._button(side, "crop_reset", self._reset_crop).pack(anchor="w", pady=(8, 0))
        # Turning the picture inside out is a thing a grey screen does, so the box is
        # only live next to the format that has one.
        self.negative_var = tk.BooleanVar(value=bool(self.options["card_negative"]))
        self.negative_box = ttk.Checkbutton(
            side, text=self.t("card_negative"), variable=self.negative_var, command=self._pick_negative
        )
        self._labels["card_negative"] = self.negative_box
        self.negative_box.pack(anchor="w", pady=(10, 0))
        # A part's own words on screen is a thing a big screen does, so this box is
        # only live next to the two formats whose screen can hold a sentence.
        self.captions_var = tk.BooleanVar(value=bool(self.options["captions"]))
        self.captions_box = ttk.Checkbutton(
            side, text=self.t("captions"), variable=self.captions_var, command=self._pick_captions
        )
        self._labels["captions"] = self.captions_box
        self.captions_box.pack(anchor="w", pady=(6, 0))
        self._label(side, "crop_hint", wraplength=240).pack(anchor="w", pady=(10, 0))
        self.cover_var.trace_add("write", lambda *_: self._show_crop())

        controls = ttk.Frame(grid)
        controls.grid(row=9, column=0, columnspan=3, sticky="ew", pady=(8, 8))
        self.save_button = self._button(controls, "save", self._start, style="Work.TButton")
        self.save_button.pack(side="left")
        self._button(controls, "options", self._open_options).pack(side="left", padx=12)
        self._button(controls, "chapters", self._open_chapters).pack(side="left")
        self.test_button = self._button(controls, "test_story", self._test_story)
        self.test_button.pack(side="left", padx=12)
        self.force_box = ttk.Checkbutton(
            controls, text=self.t("overwrite"), variable=self.force_var
        )
        self._labels["overwrite"] = self.force_box
        self.force_box.pack(side="left", padx=12)
        self.progress = ttk.Progressbar(controls, mode="indeterminate", length=140)
        self.progress.pack(side="right")

        # Every control the format decides between now exists.
        self._show_cover()
        self._show_crop()
        self._show_negative()
        self._show_captions()

        log_frame = ttk.Frame(grid)
        log_frame.grid(row=8, column=2, sticky="nsew", padx=(12, 0))
        log_frame.columnconfigure(0, weight=1)
        log_frame.rowconfigure(0, weight=1)
        self.log_view = tk.Text(
            log_frame,
            # Twelve lines and thirty characters: the frame and its knobs take the
            # width of the row, and a log that asks for more would push the window
            # past the edge of a laptop screen. It stretches with the window.
            height=12,
            width=30,
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
        self._errors: list[str] = []

        bar = ttk.Frame(self.root)
        bar.grid(row=2, column=0, sticky="ew", padx=12, pady=(2, 8))
        self._stamp = self._mark_photo(16)
        if self._stamp:
            ttk.Label(bar, image=self._stamp).pack(side="left")
        self._label(bar, "brand", style="Brand.TLabel").pack(side="left", padx=(6, 0))

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
        self._show_formats()
        self._show_engines()
        if not DND_FILES:
            self._append(self.t("drop_unavailable"))

    def bar_controls(self) -> list[str]:
        """The buttons the window's own band has to draw.

        With the system bar there the window has its buttons already, so the band is
        the mark and the name. Without it the band is all the window has, and
        `overrideredirect` hands the window back to the app: the window manager that
        would have minimised it goes with it, so a frameless window on X11 has no hide
        button to draw.
        """
        if not self.frameless:
            return []
        if self.windowingsystem == "x11":
            return ["zoom", "close"]
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
        self.root.destroy()

    def set_frameless(self, on: bool) -> None:
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

    def _size_window(self) -> None:
        """Open the window at a size of its own, in the middle of the screen.

        Tk hands the window manager the size its widgets asked for and lets it decide
        what to do with it. A window the manager does not manage — the frameless one on
        Linux and Windows — is only as big as it is told, and left untold the AppImage's
        window came up a small square.
        """
        self.root.update_idletasks()
        screen = self.root.winfo_screenwidth(), self.root.winfo_screenheight()
        width = min(self.root.winfo_reqwidth(), screen[0] - 2 * WINDOW_MARGIN)
        height = min(self.root.winfo_reqheight(), screen[1] - 2 * WINDOW_MARGIN)
        x = max(0, (screen[0] - width) // 2)
        y = max(0, (screen[1] - height) // 2)
        self.root.geometry(f"{width}x{height}+{x}+{y}")
        self.root.minsize(min(MIN_WIDTH, width), min(MIN_HEIGHT, height))

    def _take_system_bar(self) -> None:
        """Take the system bar away.

        On macOS Tk can hide the title bar and leave the window managed. On Windows
        and X11 the only way Tk has is `overrideredirect`, which unmanages the window:
        no iconify, no resize borders, and on X11 no place in the taskbar either.
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
        )

    def _test_story(self) -> None:
        """Convert into a folder the app made, play it there, and keep it for Save.

        Nothing of the user's is touched: the output folder stays as it is until the
        Save button is pressed.
        """
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
        return True

    def announce(self) -> Announce:
        return Announce(
            mode=self.options["announce"],
            language=self.language_var.get(),
            autoplay=self.options["autoplay"],
            show_total=self.options["card_total"],
            engine=self.options["tts"],
            voice=self.options["voice"],
            command=self.options["command"],
            python=self.options["python"],
            clip=self.options["clip"],
            device=self.options["device"],
            speed=self.options["speed"],
        )

    def _follow_pack(self, *_: object) -> None:
        """The story lands in the folder named in the options, or next to its pack.

        Only the folder this put there last time is replaced: an output path the
        user typed or picked themselves is never overwritten.
        """
        pack = self._typed_path(self.pack_var)
        if pack != self._pack_path:
            # A different book: the part numbers the last one's dropped parts point at
            # mean nothing here, the same as a different cut level.
            self.dropped.clear()
            self._pack_path = pack
        folder = self.options.get("output_dir") or ("" if pack is None else str(pack.parent))
        if self.out_var.get() in ("", self._auto_out):
            self._auto_out = folder
            self.out_var.set(folder)

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

    def _pick_pack_dir(self) -> None:
        path = self._browse("browse_folder", self.pack_var.get(), "folder")
        if path:
            self.pack_var.set(path)
            self._find_cover()

    def _find_cover(self) -> None:
        """Offer the cover of the print book the pack was read from.

        A 2.02 audio pack carries no picture, but its metadata names the book's
        ISBN, and three services hold the cover for it between them. The answer
        arrives on its own thread: a lookup that takes a second should not stop
        the window from drawing. A cover the user already typed is never
        replaced, and the cover the last pack's lookup found is dropped before
        this one asks.
        """
        pack = self._typed_path(self.pack_var)
        self._cover_job += 1
        self._pack_title = None
        self._spin_off()
        self._drop_lookup()
        if pack is None or not pack.exists() or self.cover_var.get().strip():
            return
        if self._cover_dir is None:
            self._cover_dir = Path(tempfile.mkdtemp(prefix="daisy2telmi-cover-"))
        # One name per lookup, so two packs picked in a row cannot write over each other.
        dest = self._cover_dir / f"cover-{self._cover_job}.jpg"
        self._spin_on()
        threading.Thread(target=self._cover_lookup, args=(pack, self._cover_job, dest), daemon=True).start()

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
        """Show the bar and keep it turning: Tk only moves an indeterminate bar
        when something steps it from the event loop."""
        if self._spin_job is not None:
            return
        self.cover_spin.pack(side="left", padx=(6, 0))
        self._spin_step()

    def _spin_step(self) -> None:
        self.cover_spin.step(1)
        self._spin_job = self.root.after(90, self._spin_step)

    def _spin_off(self) -> None:
        if self._spin_job is not None:
            self.root.after_cancel(self._spin_job)
            self._spin_job = None
        self.cover_spin.pack_forget()

    def _cover_lookup(self, pack: Path, job: int, dest: Path) -> None:
        """Read the pack's own metadata off the worker thread, then post the picture back."""
        try:
            with open_pack(pack) as opened:
                book = extract(opened, "chapter")
            found = covers.find(book, dest)
            title, author = book.title, book.author or ""
        except Exception as exc:  # a suggestion is worth nothing here: it must never break the window
            log.warning("no cover lookup for %s: %s", pack.name, exc)
            found, title, author = None, "", ""
        self.events.put(("cover", job, "" if found is None else str(found), title, author))

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
        if engine == "ddg":
            webbrowser.open(f"https://duckduckgo.com/?q={query}&iax=images&ia=images")
        else:
            webbrowser.open(f"https://www.google.com/search?tbm=isch&q={query}")

    def _pick_out_dir(self) -> None:
        path = self._browse("browse_output", self.out_var.get(), "folder")
        if path:
            self.out_var.set(path)

    def _pick_cover(self) -> None:
        path = self._browse("browse_cover", self.cover_var.get(), "file")
        if path:
            self.cover_var.set(path)

    def _pick_crop(self) -> None:
        """A band chosen by name: the frame goes back to it."""
        if self.crop_var.get() not in images.CROP_MODES:
            return
        self.options["cover_crop"] = self.crop_var.get()
        settings.save(self.options)
        self._crop_focus = None
        self._draw_crop()

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
        self._show_negative()
        self._show_captions()
        self._pick_format()

    def _show_cover(self) -> None:
        """An mp3 pack carries no pictures, so the frame and its knobs have nothing
        to show for it, and there is no story graph for the emulator to play."""
        cards = self.format_var.get() in CARD_FORMATS
        for widget in (self.crop_frame, self.crop_side):
            if cards:
                widget.grid()
            else:
                widget.grid_remove()
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
        return next((e.label for e in tts.engines() if e.name == key), key)

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
        size = images.preview_size(self.format_var.get(), CROP_WIDTH)
        # A cover smaller than the frame leaves its room empty on the canvas, which
        # is the card's band colour and not the window's.
        style = "bw" if self.format_var.get() == "lunii" else "colour"
        # A colour card has nothing to invert, so the preview inverts with the card.
        negative = self.negative_var.get() and style == "bw"
        self.crop_frame.configure(width=size[0], height=size[1], background=images.band_colour(style, negative))
        source = self._typed_path(self.cover_var)
        if source is None or not source.is_file():
            self._clear_crop()
            return
        key = (str(source), self.format_var.get(), self._crop_zoom_value, negative)
        if self._crop_cover is None or self._crop_cover[:4] != key:
            try:
                self._crop_cover = (
                    *key,
                    images.cover_fit(source, size, self._crop_zoom_value, style, negative),
                )
            except images.ImageError as exc:
                self._append(f"ERROR: {exc}")
                self._clear_crop()
                return
        self._draw_crop()

    def _draw_crop(self) -> None:
        """Slide the drawn cover under the frame until the frame sits at its focus."""
        if self._crop_cover is None:
            return
        png, picture = self._crop_cover[4]
        frame = images.preview_size(self.format_var.get(), CROP_WIDTH)
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
        self.crop_zoom.set(1.0)
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
        self._move_crop(dx * CROP_WIDTH / 10, dy * CROP_WIDTH / 10)

    def _move_crop(
        self, dx: float, dy: float, start: tuple[float, float] | None = None
    ) -> None:
        if self._crop_cover is None:
            return
        frame = images.preview_size(self.format_var.get(), CROP_WIDTH)
        picture = self._crop_cover[4][1]
        focus = start or self._current_focus()
        room = (picture[0] - frame[0], picture[1] - frame[1])
        self._crop_focus = (
            min(1.0, max(0.0, focus[0] - dx / room[0])) if room[0] > 0 else focus[0],
            min(1.0, max(0.0, focus[1] - dy / room[1])) if room[1] > 0 else focus[1],
        )
        # A moved frame answers to no band, so the three radios let go of it.
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

    def _slide_crop(self, value: str) -> None:
        self._set_crop_zoom(float(value))

    def _set_crop_zoom(self, value: float) -> None:
        zoom = images.clamp_zoom(value)
        if abs(zoom - self._crop_zoom_value) < 0.01:
            return
        self._crop_zoom_value = zoom
        self.crop_zoom.set(zoom)
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
        self.crop_zoom.set(1.0)
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
        picked = self._sources()
        if picked is None:
            return
        source, cover = picked
        out_dir = self._typed_path(self.out_var)
        if out_dir is None:
            self._append("ERROR: choose an output folder first")
            return
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
                    tts.install_engine(announce.engine, announce.python)
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
            log.error("%s: %s", type(exc).__name__, exc)
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
            else:
                self._append(item)

        self.root.after(100, self._drain)

    def _took_cover(self, job: int, path: str, title: str, author: str) -> None:
        """Put a looked-up cover in the Image box.

        Only the lookup the newest pack asked for counts, and a cover the user
        typed or picked themselves is never replaced by a suggestion. The author
        and the title the pack's own metadata gave go into the search boxes, so
        the search is ready to make even when none of the services had the book.
        """
        if job != self._cover_job:
            return
        self._spin_off()
        self._pack_title = title
        if author and not self.author_var.get().strip():
            self._auto_author = author
            self.author_var.set(author)
        if title and not self.title_var.get().strip():
            self._auto_title = title
            self.title_var.set(title)
        if self.cover_var.get().strip():
            return
        if path:
            self._auto_cover = path
            self.cover_var.set(path)
            self._append(self.t("cover_found"))
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
        self.book = extract(self.pack, self.cut)
        self.info = chapters.describe(self.book, with_durations=False)
        self.cache = (
            Path(tempfile.gettempdir()) / "daisy2telmi-preview" / (slugify(self.book.title) or "pack")
        )
        self.keep: list[tk.BooleanVar] = []

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
            self._choose(available[min(CUT_LEVELS.index(self.cut), len(available) - 1)])

    def _choose(self, level: str) -> None:
        """A different level is a different book: the old part numbers mean nothing."""
        if level == self.cut:
            return
        self.cut = level
        self.cut_var.set(level)
        self.app.cut = level
        self.app.dropped = set()
        self._build()
        threading.Thread(target=self._measure, daemon=True).start()

    def _measure(self) -> None:
        cut = self.cut
        self.events.put(("sizes", (cut, chapters.describe(extract(self.pack, cut)))))
        if not self.reported:
            self.reported = True
            books = {level: extract(self.pack, level) for level in CUT_LEVELS}
            self.events.put(("counts", chapters.describe_levels(books)))

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
        self.packs.close()
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
        return next((e.label for e in tts.engines() if e.name == key), key)

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
        """What a finished job changes. Only the speech window has jobs of its own."""


class Options(Settings):
    """The app's own settings, saved when the window closes.

    The speech engine is not here: it has a page of its own, because everything
    that goes with it - voices, installs, a sample to clone - is a page's worth.
    """

    def __init__(self, app: App) -> None:
        super().__init__(app, "opt_title")

        self.language_keys = list(LANGUAGES)
        self.announce_keys = list(ANNOUNCE_MODES)

        self.language_box = self._combo(self.grid, self.language_keys, app.options["language"])
        self.announce_box = self._combo(
            self.grid, self.announce_keys, app.options["announce"], ANNOUNCE_KEYS
        )
        self.card_total_var = tk.BooleanVar(value=bool(app.options["card_total"]))
        self.autoplay_var = tk.BooleanVar(value=bool(app.options["autoplay"]))
        self.frameless_var = tk.BooleanVar(value=bool(app.options["frameless"]))

        self._row(self.grid, "opt_language", 0, self.language_box)
        self._row(self.grid, "opt_announce", 1, self.announce_box)
        self._check(self.grid, "opt_card_total", 2, self.card_total_var)
        self._check(self.grid, "opt_autoplay", 3, self.autoplay_var)
        self._check(self.grid, "opt_frameless", 4, self.frameless_var)
        # The bar is drawn either way; what the tick changes is whether the system's
        # own bar is left above it, and what that bar was doing for the window.
        ttk.Label(self.grid, text=app.t("opt_frameless_hint"), style="Brand.TLabel").grid(
            row=5, column=0, columnspan=2, sticky="w"
        )

        folders = ttk.Labelframe(self.grid, text=app.t("opt_section_folders"), padding=(10, 6))
        folders.grid(row=6, column=0, columnspan=2, sticky="ew", pady=(14, 0))
        folders.columnconfigure(1, weight=1)
        self.input_box = self._folder(folders, "opt_input_dir", 0, app.options["input_dir"])
        self.output_box = self._folder(folders, "opt_output_dir", 1, app.options["output_dir"])
        # Two empty boxes are a question the app is still asking, so the page asks it.
        asked = "opt_folders_hint" if app.options.get("input_dir") and app.options.get("output_dir") else "opt_folders_unset"
        self.folders_hint = ttk.Label(folders, text=app.t(asked), style="Brand.TLabel")
        self.folders_hint.grid(row=2, column=0, columnspan=2, sticky="w", pady=(6, 0))

        ttk.Button(self.grid, text=app.t("opt_close"), command=self._close).grid(
            row=7, column=0, columnspan=2, sticky="e", pady=(12, 0)
        )

    def _close(self) -> None:
        input_dir = self.input_box.get().strip() or None
        output_dir = self.output_box.get().strip() or None
        moved_input = input_dir != self.app.options.get("input_dir")
        moved_output = output_dir != self.app.options.get("output_dir")
        self._save(
            {
                "language": self._chosen(self.language_box, self.language_keys),
                "announce": self._chosen(self.announce_box, self.announce_keys, ANNOUNCE_KEYS),
                "autoplay": self.autoplay_var.get(),
                "card_total": self.card_total_var.get(),
                "input_dir": input_dir,
                "output_dir": output_dir,
                "frameless": self.frameless_var.get(),
            }
        )
        if moved_input:
            # A folder the user named beats the one a file list happened to end in.
            self.app.options["last_dir"] = None
            settings.save(self.app.options)
        if moved_output:
            # The main window's own field follows the folder named here, until the
            # user types or picks another one.
            self.app._auto_out = output_dir or ""
            self.app.out_var.set(output_dir or "")
        self.app.language_var.set(self.app.options["language"])
        self.app.set_frameless(self.app.options["frameless"])
        self.app._relabel()
        self.app._follow_pack()
        self.top.destroy()


class Speech(Settings):
    """The voice the chapter cards get, and everything that goes with it.

    The engines that are packages rather than parts of the app are installed from
    here, into a Python of their own; so are the Piper voices they read with. A box
    the chosen engine has nothing to do with is greyed out rather than taken away,
    so the page keeps its shape when the engine changes.
    """

    # The rows each engine can answer for. `auto` gets the voice downloads because
    # it reaches for Piper as soon as a voice for the language is on the machine.
    LIVE = {
        "auto": ("speed", "download"),
        tts.PIPER: ("voice", "speed", "python", "download"),
        tts.ESPEAK: ("voice", "speed"),
        tts.SAY: ("voice", "speed"),
        tts.SAPI: ("voice", "speed"),
        tts.COQUI: ("voice", "clip", "python", "device"),
        tts.CHATTERBOX: ("voice", "clip", "python", "device"),
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

        self.engine_box = self._combo(self.grid, self.engine_keys, app.options["tts"])
        self.voice_box = self._combo(self.grid, [None], app.options["voice"])
        self.speed_box = self._combo(self.grid, self.speed_keys, app.options["speed"], SPEED_KEYS)
        self.python_box = ttk.Entry(self.grid, width=28)
        self.python_box.insert(0, app.options["python"] or "")
        self.device_box = self._combo(self.grid, self.device_keys, app.options["device"])
        clip_row = ttk.Frame(self.grid)
        self.clip_box = ttk.Entry(clip_row, width=22)
        self.clip_box.insert(0, app.options["clip"] or "")
        self.clip_box.pack(side="left")
        ttk.Button(clip_row, text=app.t("browse_output"), command=self._pick_clip).pack(side="left", padx=(6, 0))
        try_row = ttk.Frame(self.grid)
        self.try_box = ttk.Entry(try_row, width=22)
        self.try_box.insert(0, self._sample_text())
        self.try_box.pack(side="left")
        self.try_button = ttk.Button(try_row, text=app.t("speech_try"), command=self._try_voice)
        self.try_button.pack(side="left", padx=(6, 0))

        self._row(self.grid, "opt_engine", 0, self.engine_box)
        self._row(self.grid, "opt_voice", 1, self.voice_box)
        self._row(self.grid, "opt_speed", 2, self.speed_box)
        self._row(self.grid, "opt_python", 3, self.python_box)
        self._row(self.grid, "opt_device", 4, self.device_box)
        self._row(self.grid, "opt_clip", 5, clip_row)
        self._row(self.grid, "speech_try_label", 6, try_row)

        install = ttk.Labelframe(self.grid, text=app.t("speech_section"), padding=(10, 6))
        install.grid(row=7, column=0, columnspan=2, sticky="ew", pady=(14, 0))
        install.columnconfigure(1, weight=1)
        ttk.Label(install, text=app.t("speech_hint"), style="Brand.TLabel").grid(
            row=0, column=0, columnspan=2, sticky="w", pady=(0, 6)
        )
        self.engine_state = ttk.Label(install, text=self._engine_text())
        self.engine_state.grid(row=1, column=0, sticky="w")
        self.install_button = ttk.Button(install, text=app.t("speech_install"), command=self._install_engine)
        self.install_button.grid(row=1, column=1, sticky="e", padx=(8, 0))
        download_row = ttk.Frame(install)
        self.download_box = ttk.Combobox(download_row, values=[], state="readonly", width=26)
        self.download_box.pack(side="left")
        self.download_button = ttk.Button(
            download_row, text=app.t("speech_download"), command=self._download_voice
        )
        self.download_button.pack(side="left", padx=(6, 0))
        self._row(install, "speech_download_label", 2, download_row)

        ttk.Button(self.grid, text=app.t("opt_close"), command=self._close).grid(
            row=8, column=0, columnspan=2, sticky="e", pady=(12, 0)
        )

        self.engine_box.bind("<<ComboboxSelected>>", lambda _e: self._show_engine())
        self.python_box.bind("<FocusOut>", lambda _e: self._recheck())
        self._show_engine()

    def language(self) -> Language:
        return LANGUAGES.get(self.app.options["language"], LANGUAGES["en"])

    def _sample_text(self) -> str:
        """What the try box holds before the user types anything: the card's own words."""
        return self.language().chapter.format(n=1)

    def _show_engine(self) -> None:
        """Refill the voice lists and grey out the rows this engine cannot answer for."""
        self._fill_voices()
        self._fill_downloads()
        self._show_live()
        self.engine_state.configure(text=self._engine_text())

    def _show_live(self) -> None:
        engine = self._chosen(self.engine_box, self.engine_keys)
        live = self.LIVE.get(engine, ())
        for key, box in (("voice", self.voice_box), ("speed", self.speed_box), ("device", self.device_box)):
            box.configure(state="readonly" if key in live else "disabled")
        for key, box in (("python", self.python_box), ("clip", self.clip_box)):
            box.configure(state="normal" if key in live else "disabled")
        self.download_box.configure(state="readonly" if "download" in live else "disabled")
        self.download_button.state(["!disabled"] if "download" in live and self.download_keys else ["disabled"])
        self.install_button.state(["!disabled"] if engine in tts.PACKAGES else ["disabled"])

    def _engine_text(self) -> str:
        engine = self._chosen(self.engine_box, self.engine_keys)
        if engine not in tts.PACKAGES:
            return self.app.t("speech_builtin")
        if tts.available(engine, self.python_box.get().strip() or None):
            return self.app.t("speech_found").format(engine=tts.LABELS[engine])
        return self.app.t("speech_missing").format(engine=tts.LABELS[engine])

    def _recheck(self) -> None:
        self.engine_state.configure(text=self._engine_text())

    def _fill_voices(self) -> None:
        engine = self._chosen(self.engine_box, self.engine_keys)
        names: list[str | None] = [None]
        if engine not in (None, "auto", "off"):
            names += tts.voices(engine, self.language())
        self.voice_keys = names
        self.voice_box.configure(values=[self._label_text(k) for k in names])
        saved = self.app.options["voice"]
        self.voice_box.set(self._label_text(saved if saved in names else None))

    def _fill_downloads(self) -> None:
        """The Piper voices this language has that are not on the machine yet."""
        engine = self._chosen(self.engine_box, self.engine_keys)
        code = self.language().code
        have = set(tts.piper_voices(code))
        wanted = tts.PIPER_VOICES.get(code, []) if engine in ("auto", tts.PIPER) else []
        self.download_keys = [name for name in wanted if name not in have]
        self.download_box.configure(values=self.download_keys)
        self.download_box.set(self.download_keys[0] if self.download_keys else "")

    def _pick_clip(self) -> None:
        path = self.app._browse("opt_clip", self.clip_box.get(), "file")
        if path:
            self.clip_box.delete(0, "end")
            self.clip_box.insert(0, path)

    def _settings(self) -> dict:
        """The engine settings as they stand in the boxes. Only this thread reads a widget."""
        return {
            "engine": self._chosen(self.engine_box, self.engine_keys),
            "voice": self._chosen(self.voice_box, self.voice_keys),
            "speed": self._chosen(self.speed_box, self.speed_keys, SPEED_KEYS),
            "python": self.python_box.get().strip() or None,
            "clip": self.clip_box.get().strip() or None,
            "device": self._chosen(self.device_box, self.device_keys),
        }

    def _install_engine(self) -> None:
        """pip runs in the background: a neural engine is a gigabyte or more."""
        engine = self._chosen(self.engine_box, self.engine_keys)
        self.install_button.state(["disabled"])
        self.install_button.configure(text=self.app.t("speech_installing"))
        threading.Thread(target=self._job, args=("install", engine, self._settings()), daemon=True).start()

    def _download_voice(self) -> None:
        name = self._chosen(self.download_box, self.download_keys)
        if not name:
            return
        self.download_button.state(["disabled"])
        self.download_button.configure(text=self.app.t("speech_installing"))
        threading.Thread(target=self._job, args=("voice", name, self._settings()), daemon=True).start()

    def _try_voice(self) -> None:
        """Speak the sample with the settings as they stand, then play it back."""
        self.try_button.state(["disabled"])
        threading.Thread(
            target=self._job, args=("try", self.try_box.get().strip() or self._sample_text(), self._settings()),
            daemon=True,
        ).start()

    def _job(self, kind: str, argument: str, options: dict) -> None:
        code = self.language().code
        try:
            if kind == "install":
                self.events.put(("install", tts.install_engine(argument, options["python"])))
                return
            if kind == "voice":
                self.events.put(("voice", tts.download_voice(argument, options["python"])))
                return
            if self._try_folder is None:
                self._try_folder = Path(tempfile.mkdtemp(prefix="daisy2telmi-try-"))
            clip = options["clip"]
            if clip and not Path(clip).is_file():
                raise tts.TtsError(f"no such reference clip: {clip}")
            tts.synthesize(
                argument, code, self._try_folder / "try.mp3",
                options["engine"], options["voice"], self.app.options["command"], options["python"],
                clip, options["device"], options["speed"],
            )
            self.events.put(("try", self._try_folder / "try.mp3"))
        except tts.TtsError as exc:
            log.error("%s", exc)
            self.events.put((kind, None))

    def finished(self, kind: str, result: object) -> None:
        if kind == "install":
            if result and not self.python_box.get().strip():
                # The app built its own Python: show the user which one now speaks.
                self.python_box.insert(0, str(result))
            self.install_button.state(["!disabled"])
            self.install_button.configure(text=self.app.t("speech_install"))
            self._show_engine()
        elif kind == "voice":
            self.download_button.state(["!disabled"])
            self.download_button.configure(text=self.app.t("speech_download"))
            self._show_engine()
        elif kind == "try":
            self.try_button.state(["!disabled"])
            self.try_button.configure(text=self.app.t("speech_try"))
            if result is not None:
                chapters.stop_preview(self._playing)
                self._playing = chapters.open_preview(Path(result))

    def _close(self) -> None:
        self._save(
            {
                "tts": self._chosen(self.engine_box, self.engine_keys),
                "voice": self._chosen(self.voice_box, self.voice_keys),
                "speed": self._chosen(self.speed_box, self.speed_keys, SPEED_KEYS),
                "python": self.python_box.get().strip() or None,
                "clip": self.clip_box.get().strip() or None,
                "device": self._chosen(self.device_box, self.device_keys),
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
    the teller's keys are round: the screen on its bezel, the wheel's ring beside it,
    and along the bottom the two side buttons, the play triangle, the pause bars and
    the card slot. A chapter keeps the card it was read from on the screen for a few
    seconds, then the screen goes dark until a key is pressed.
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

    # The Telmi's side buttons step through the clip; the Lunii's wheel moves between
    # the stages of a run.
    SEEK = 10
    # How long a chapter keeps its card on the screen before the screen goes dark.
    CARD_HOLD = 4000

    KEYS = ("up", "down", "ok", "pause", "home")

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
        self.face.create_oval(
            ring_x - self.RING // 2,
            ring_y - self.RING // 2,
            ring_x + self.RING // 2,
            ring_y + self.RING // 2,
            fill=MACHINE,
            outline=DIAL_EDGE,
        )

        first = (self.FACE_WIDTH - (len(self.KEYS) - 1) * self.SPACING) / 2
        y = self.PAD + self.SCREEN_HEIGHT + self.GAP + self.DIAL
        for column, button in enumerate(self.KEYS):
            label = None if button in ("up", "down") else self.t(f"emu_{button}")
            self._key(button, first + column * self.SPACING, y, label)
        # The two side keys are one pair with one word: the Lunii's wheel, or the
        # Telmi's seek, which is what the machine's own face says.
        self.pair_caption = self.face.create_text(
            first + self.SPACING / 2,
            y + self.DIAL + 12,
            text=self.t("emu_seek" if self.story.machine == "telmi" else "emu_wheel"),
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
            fill=SLOT if button == "home" else DIAL,
            outline=SLOT if button == "home" else DIAL_EDGE,
            tags=(group,),
        )
        icons = []
        if button == "home":
            # The card slot on the machine's face: dark, and rectangular.
            icons.append(
                self.face.create_rectangle(x - 16, y - 9, x + 16, y + 9, fill=SLOT_INK, outline=SLOT_INK, tags=(group,))
            )
        elif button == "pause":
            for bar in (-11, 3):
                icons.append(
                    self.face.create_rectangle(
                        x + bar, y - 13, x + bar + 8, y + 13, fill=DIAL_INK, outline=DIAL_INK, tags=(group,)
                    )
                )
        else:
            points = {
                "up": [(x, y - 14), (x - 15, y + 10), (x + 15, y + 10)],
                "down": [(x, y + 14), (x - 15, y - 10), (x + 15, y - 10)],
                "ok": [(x - 11, y - 14), (x - 11, y + 14), (x + 15, y)],
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
        wheel = (self.story.machine == "telmi" and clip) or stage.controls.wheel
        self._paint("up", wheel)
        self._paint("down", wheel)
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
        if button == "home":
            fill, ink = (SLOT, SLOT_INK) if on else (OFF, OFF_INK)
            edge = fill
        self.face.itemconfigure(key["dial"], fill=fill, outline=edge)
        for icon in key["icons"]:
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

    def _seek(self, step: float) -> None:
        if self.sound is None:
            return
        if self.player is not None:
            self.heard += time.monotonic() - self.started
            self._stop()
        self.heard = max(0.0, self.heard + step)
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
        if self.story.machine == "telmi" and button in ("up", "down"):
            self._seek(-self.SEEK if button == "up" else self.SEEK)
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


def attach_log_handler(app: App) -> logging.Handler:
    """Route the converter's log records into the app's queue."""
    handler = _QueueHandler(app.events)
    handler.setFormatter(logging.Formatter("%(levelname)s: %(message)s"))
    log.addHandler(handler)
    # Without this the logger keeps the root's WARNING level and the window stays blank.
    log.setLevel(logging.INFO)
    return handler


def make_root() -> tk.Misc:
    """tkinterdnd2 only adds the drop methods to its own root class."""
    return TkinterDnD.Tk() if TkinterDnD else tk.Tk()


def run_gui() -> int:
    root = make_root()
    app = App(root)
    root.title(f"{app.t('app_title')} {__version__}")
    handler = attach_log_handler(app)
    try:
        root.mainloop()
    finally:
        log.removeHandler(handler)
    return 0
