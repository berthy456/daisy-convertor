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
import subprocess
import sys
import tempfile
import threading
import tkinter as tk
import webbrowser
from dataclasses import replace
from pathlib import Path
from tkinter import font as tkfont, messagebox, ttk
from urllib.parse import quote_plus

from . import audio, chapters, covers, emulator, images, settings, tts
from .cli import FORMATS, convert
from .detector import DaisyPackError, open_pack
from .extractor import CUT_LEVELS, extract
from .i18n import LANGUAGES, text
from .telmi_builder import ANNOUNCE_MODES, Announce, slugify

try:
    from tkinterdnd2 import DND_FILES, TkinterDnD
except ImportError:  # drag and drop is a bonus, browsing works without it
    DND_FILES = None
    TkinterDnD = None

log = logging.getLogger("daisy2telmi")

ANNOUNCE_KEYS = {mode: f"announce_{mode}" for mode in ANNOUNCE_MODES}

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
    style.map("Treeview", background=[("selected", ACCENT)], foreground=[("selected", "#ffffff")])
    style.configure("Treeview.Heading", background=FIELD, foreground=MUTED, relief="flat")
    style.configure("TScrollbar", background=FIELD, troughcolor=PAPER, bordercolor=LINE, arrowsize=12, relief="flat")
    style.configure("TProgressbar", background=ACCENT, troughcolor=FIELD, bordercolor=LINE)
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
        self.pack_var = tk.StringVar()
        self.out_var = tk.StringVar()
        self._auto_out = ""
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
        self._crop_cover: tuple[str, str, float, tuple[bytes, tuple[int, int]]] | None = None
        self._crop_photo: tk.PhotoImage | None = None
        self._crop_zoom_value = 1.0
        self._crop_focus: tuple[float, float] | None = None
        self._crop_drag: tuple[int, int, tuple[float, float]] | None = None
        self._crop_redraw = None
        self.force_var = tk.BooleanVar(value=False)
        self.language_var = tk.StringVar(value=self.options["language"])
        self.cut = self.options["cut"]
        self.dropped: set[int] = set()
        self._labels: dict[str, tk.Widget] = {}
        self._build()
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

        # The mark and what the window is for, centred along the top, and the name
        # along the bottom: the work itself sits between them.
        brand = ttk.Frame(self.root)
        brand.grid(row=0, column=0, sticky="ew", pady=(4, 10))
        brand.columnconfigure(0, weight=1)
        self._logo = self._mark_photo(64)
        if self._logo:
            ttk.Label(brand, image=self._logo).grid(row=0, column=0)
        self._label(brand, "app_headline", style="Headline.TLabel").grid(row=1, column=0, pady=(6, 0))

        grid = ttk.Frame(self.root, padding=(12, 0))
        grid.grid(row=1, column=0, sticky="nsew")
        # Three columns: the picture on the left, the knobs that change it in the
        # middle, the log on the right. Both stretchy columns share the spare width
        # when the window is widened.
        grid.columnconfigure(1, weight=1)
        grid.columnconfigure(2, weight=1)
        grid.rowconfigure(7, weight=1)

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

        cover_row = ttk.Frame(grid)
        cover_row.grid(row=5, column=0, columnspan=3, sticky="w", pady=(0, 4))
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
        search.grid(row=6, column=0, columnspan=3, sticky="w", pady=(6, 0))
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
        self.crop_frame.grid(row=7, column=0, sticky="n", pady=(6, 0))
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
        side.grid(row=7, column=1, sticky="n", padx=(12, 0))
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
        self._label(side, "crop_hint", wraplength=240).pack(anchor="w", pady=(10, 0))
        self.cover_var.trace_add("write", lambda *_: self._show_crop())
        self._show_crop()
        self._show_negative()

        controls = ttk.Frame(grid)
        controls.grid(row=8, column=0, columnspan=3, sticky="ew", pady=(8, 8))
        self.save_button = self._button(controls, "save", self._start)
        self.save_button.pack(side="left")
        self._button(controls, "options", self._open_options).pack(side="left", padx=12)
        self._button(controls, "chapters", self._open_chapters).pack(side="left")
        self._button(controls, "emulator", self._open_emulator).pack(side="left", padx=12)
        self.force_box = ttk.Checkbutton(
            controls, text=self.t("overwrite"), variable=self.force_var
        )
        self._labels["overwrite"] = self.force_box
        self.force_box.pack(side="left", padx=12)
        self.progress = ttk.Progressbar(controls, mode="indeterminate", length=140)
        self.progress.pack(side="right")

        log_frame = ttk.Frame(grid)
        log_frame.grid(row=7, column=2, sticky="nsew", padx=(12, 0))
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
        self.root.title(self.t("app_title"))
        for key, widget in self._labels.items():
            widget.configure(text=self.t(key))
        self._show_formats()
        if not DND_FILES:
            self._append(self.t("drop_unavailable"))

    def _open_options(self) -> None:
        Options(self)

    def _open_chapters(self) -> None:
        source = self._typed_path(self.pack_var)
        if source is None or not source.exists():
            self._append("ERROR: choose a DAISY pack first")
            return
        ChapterList(self, source)

    def _open_emulator(self) -> None:
        """Play the story the last run wrote. When the output folder holds none,
        ask for a folder and read the story out of that."""
        out = Path(self.out_var.get()).expanduser() if self.out_var.get().strip() else None
        folder = emulator.find_story(out) if out is not None and out.is_dir() else None
        if folder is None:
            chosen = self._browse("emulator", self.out_var.get(), "folder")
            if not chosen:
                return
            folder = Path(chosen)
        self.emulator = Emulator(self, folder)

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
        )

    def _follow_pack(self, *_: object) -> None:
        """The story lands next to the pack it came from.

        Only the folder this put there last time is replaced: an output path the
        user typed or picked themselves is never overwritten.
        """
        pack = self._typed_path(self.pack_var)
        folder = "" if pack is None else str(pack.parent)
        if self.out_var.get() in ("", self._auto_out):
            self._auto_out = folder
            self.out_var.set(folder)

    def _start_dir(self, current: str = "") -> str:
        """Where a file list opens: the folder the field already names, then the
        folder the last one ended in, then home."""
        typed = current.strip()
        if typed:
            named = Path(typed).expanduser()
            folder = named if named.is_dir() else named.parent
            if folder.is_dir():
                return str(folder)
        remembered = self.options.get("last_dir")
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
        replaced.
        """
        pack = self._typed_path(self.pack_var)
        self._cover_job += 1
        self._pack_title = None
        self._spin_off()
        if pack is None or not pack.exists() or self.cover_var.get().strip():
            return
        if self._cover_dir is None:
            self._cover_dir = Path(tempfile.mkdtemp(prefix="daisy2telmi-cover-"))
        # One name per lookup, so two packs picked in a row cannot write over each other.
        dest = self._cover_dir / f"cover-{self._cover_job}.jpg"
        self._spin_on()
        threading.Thread(target=self._cover_lookup, args=(pack, self._cover_job, dest), daemon=True).start()

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
        """The five players, by the name this window's language gives them."""
        return [self.t(f"fmt_{player.replace('-', '_')}") for player in FORMATS]

    def _show_formats(self) -> None:
        """Refill the box in this language, and put it back on the chosen player."""
        names = self.format_names()
        self.format_box.configure(values=names)
        chosen = FORMATS.index(self.format_var.get()) if self.format_var.get() in FORMATS else 0
        self.format_box.set(names[chosen])

    def _choose_format(self) -> None:
        self.format_var.set(FORMATS[self.format_names().index(self.format_box.get())])
        self._show_negative()
        self._pick_format()

    def _show_negative(self) -> None:
        """Only a grey screen can be turned inside out; the colour ones ignore it."""
        self.negative_box.configure(state="normal" if self.format_var.get() == "lunii" else "disabled")

    def _pick_negative(self) -> None:
        self.options["card_negative"] = self.negative_var.get()
        settings.save(self.options)

    def _pick_format(self) -> None:
        self.options["format"] = self.format_var.get()
        settings.save(self.options)
        # The frame is the chosen player's screen, so it is drawn at its ratio again.
        self._crop_cover = None
        self._show_crop()

    def _show_crop(self) -> None:
        """Draw the crop frame over the cover: which part of a tall cover the frame
        keeps is not something a word can tell you.

        The frame is the chosen player's screen, so it is drawn with no cover too:
        the shape a cover has to be fitted into is worth seeing before one exists.

        The cover is scaled once per zoom, so dragging stays instant: a move only
        shifts the picture already on the canvas.
        """
        size = images.preview_size(self.format_var.get(), CROP_WIDTH)
        # A cover smaller than the frame leaves its room empty on the canvas, which
        # is the card's band colour and not the window's.
        style = "bw" if self.format_var.get() == "lunii" else "colour"
        self.crop_frame.configure(width=size[0], height=size[1], background=images.band_colour(style))
        source = self._typed_path(self.cover_var)
        if source is None or not source.is_file():
            self._clear_crop()
            return
        key = (str(source), self.format_var.get(), self._crop_zoom_value)
        if self._crop_cover is None or self._crop_cover[:3] != key:
            try:
                self._crop_cover = (*key, images.cover_fit(source, size, self._crop_zoom_value))
            except images.ImageError as exc:
                self._append(f"ERROR: {exc}")
                self._clear_crop()
                return
        self._draw_crop()

    def _draw_crop(self) -> None:
        """Slide the drawn cover under the frame until the frame sits at its focus."""
        if self._crop_cover is None:
            return
        png, picture = self._crop_cover[3]
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
        picture = self._crop_cover[3][1]
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

    def _start(self) -> None:
        source = self._typed_path(self.pack_var)
        out_dir = self._typed_path(self.out_var)
        if source is None:
            self._append("ERROR: choose a DAISY pack first")
            return
        if not source.exists():
            self._append(f"ERROR: no such pack: {source}")
            return
        if out_dir is None:
            self._append("ERROR: choose an output folder first")
            return
        cover = self._typed_path(self.cover_var)
        if cover is not None and not cover.exists():
            self._append(f"ERROR: no such image: {cover}")
            return
        out_dir.mkdir(parents=True, exist_ok=True)

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
                self.force_var.get(),
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
            ),
            daemon=True,
        ).start()

    def _speech(self) -> tuple[str, bool]:
        """The engine this story's cards get, and whether Chatterbox goes in first.

        Chatterbox is the default because a card read by a real voice is what the
        player is for, but it does not ship inside the app: it is a package the
        user's own Python installs. OK starts that install, Cancel takes the best
        voice the machine already has.
        """
        engine = self.options["tts"]
        if engine != tts.CHATTERBOX or tts.available(tts.CHATTERBOX, self.options["python"]):
            return engine, False
        if messagebox.askokcancel(self.t("opt_section_chatterbox"), self.t("chatterbox_needed")):
            return engine, True
        self._append("Chatterbox is not installed: the machine's own voice will read the cards")
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
    ) -> None:
        try:
            if install:
                try:
                    tts.install_chatterbox(announce.python)
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
                self.progress.stop()
                self.save_button.state(["!disabled"])
                self._report(item[1])
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
            self.author_var.set(author)
        if title and not self.title_var.get().strip():
            self.title_var.set(title)
        if self.cover_var.get().strip():
            return
        if path:
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
        self.app.dropped = {
            info.number for info, box in zip(self.info, self.keep) if not box.get()
        }
        self.app.options["cut"] = self.cut
        settings.save(self.app.options)
        chapters.stop_preview(self._playing)
        self.packs.close()
        self.top.destroy()


class Options:
    """Language and speech settings, saved when the window closes.

    The boxes show translated names, so each one keeps its own list of keys in
    the same order as the values it displays and reads the choice back by index.
    """

    def __init__(self, app: App) -> None:
        self.app = app
        self.top = tk.Toplevel(app.root)
        self.top.title(app.t("opt_title"))
        self.top.configure(background=PAPER)
        self.top.resizable(False, False)

        self.language_keys = list(LANGUAGES)
        self.announce_keys = list(ANNOUNCE_MODES)
        self.engine_keys = ["auto"] + [engine.name for engine in tts.engines()] + ["off"]
        self.device_keys = list(tts.DEVICES)

        grid = ttk.Frame(self.top, padding=12)
        grid.grid(sticky="nsew")
        grid.columnconfigure(1, weight=1)

        self.language_box = self._combo(grid, self.language_keys, app.options["language"])
        self.announce_box = self._combo(
            grid, self.announce_keys, app.options["announce"], ANNOUNCE_KEYS
        )
        self.engine_box = self._combo(grid, self.engine_keys, app.options["tts"])
        self.voice_box = self._combo(grid, [None], app.options["voice"])
        self.card_total_var = tk.BooleanVar(value=bool(app.options["card_total"]))
        self.autoplay_var = tk.BooleanVar(value=bool(app.options["autoplay"]))

        self._row(grid, "opt_language", 0, self.language_box)
        self._row(grid, "opt_announce", 1, self.announce_box)
        self._check(grid, "opt_card_total", 2, self.card_total_var)
        self._check(grid, "opt_autoplay", 3, self.autoplay_var)
        self._row(grid, "opt_engine", 4, self.engine_box)
        self._row(grid, "opt_voice", 5, self.voice_box)

        # Chatterbox is a package that goes into the Python named below, not part of
        # the app, so its rows sit behind their own border.
        chatterbox = ttk.Labelframe(grid, text=app.t("opt_section_chatterbox"), padding=(10, 6))
        chatterbox.grid(row=6, column=0, columnspan=2, sticky="ew", pady=(14, 0))
        chatterbox.columnconfigure(1, weight=1)
        self.device_box = self._combo(chatterbox, self.device_keys, app.options["device"])
        self.python_box = ttk.Entry(chatterbox, width=28)
        self.python_box.insert(0, app.options["python"] or "")
        clip_row = ttk.Frame(chatterbox)
        self.clip_box = ttk.Entry(clip_row, width=22)
        self.clip_box.insert(0, app.options["clip"] or "")
        self.clip_box.pack(side="left")
        ttk.Button(clip_row, text=app.t("browse_output"), command=self._pick_clip).pack(side="left", padx=(6, 0))

        ttk.Label(chatterbox, text=app.t("opt_chatterbox_hint")).grid(
            row=0, column=0, columnspan=2, sticky="w", pady=(0, 6)
        )
        # Whether the package is already in the Python named below, and the way to put it there.
        self.chatterbox_state = ttk.Label(chatterbox, text=self._chatterbox_text())
        self.chatterbox_state.grid(row=1, column=0, sticky="w")
        self.chatterbox_button = ttk.Button(
            chatterbox, text=app.t("chatterbox_install"), command=self._install_chatterbox
        )
        self.chatterbox_button.grid(row=1, column=1, sticky="e", padx=(8, 0))
        self._row(chatterbox, "opt_python", 2, self.python_box)
        self._row(chatterbox, "opt_device", 3, self.device_box)
        self._row(chatterbox, "opt_clip", 4, clip_row)

        ttk.Button(grid, text=app.t("opt_close"), command=self._close).grid(
            row=7, column=0, columnspan=2, sticky="e", pady=(12, 0)
        )

        self.engine_box.bind("<<ComboboxSelected>>", lambda _e: self._fill_voices())
        self.language_box.bind("<<ComboboxSelected>>", lambda _e: self._fill_voices())
        self.python_box.bind("<FocusOut>", lambda _e: self._recheck_chatterbox())
        self._fill_voices()

        self.events: queue.Queue[str | None] = queue.Queue()
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

    def _row(self, grid: ttk.Frame, key: str, row: int, widget: tk.Widget) -> None:
        ttk.Label(grid, text=self.app.t(key)).grid(row=row, column=0, sticky="w", pady=4)
        widget.grid(row=row, column=1, sticky="ew", padx=(8, 0), pady=4)

    def _check(self, grid: ttk.Frame, key: str, row: int, var: tk.BooleanVar) -> None:
        """The tick and its words in one widget: a label in the left column would
        leave the box a hand's width from the sentence it belongs to."""
        ttk.Checkbutton(grid, text=self.app.t(key), variable=var).grid(
            row=row, column=0, columnspan=2, sticky="w", pady=4
        )

    def _fill_voices(self) -> None:
        code = self._chosen(self.language_box, self.language_keys)
        engine = self._chosen(self.engine_box, self.engine_keys)
        names: list[str | None] = [None]
        if engine not in (None, "auto", "off"):
            names += tts.voices(engine, LANGUAGES.get(code, LANGUAGES["en"]))
        self.voice_keys = names
        self.voice_box.configure(values=[self._label_text(k) for k in names])
        self.voice_box.set(self._label_text(None))

    def _pick_clip(self) -> None:
        path = self.app._browse("opt_clip", self.clip_box.get(), "file")
        if path:
            self.clip_box.delete(0, "end")
            self.clip_box.insert(0, path)

    def _chatterbox_text(self) -> str:
        engine = tts.CHATTERBOX
        python = self.python_box.get().strip() or None
        if tts.available(engine, python):
            return self.app.t("chatterbox_found")
        return self.app.t("chatterbox_missing")

    def _recheck_chatterbox(self) -> None:
        self.chatterbox_state.configure(text=self._chatterbox_text())

    def _install_chatterbox(self) -> None:
        """pip runs in the background: the download is a gigabyte or more."""
        self.chatterbox_button.state(["disabled"])
        self.chatterbox_button.configure(text=self.app.t("chatterbox_installing"))
        # The entry is read here: only this thread may touch a widget.
        python = self.python_box.get().strip() or None
        threading.Thread(target=self._chatterbox_install, args=(python,), daemon=True).start()

    def _chatterbox_install(self, python: str | None = None) -> None:
        try:
            self.events.put(tts.install_chatterbox(python))
        except tts.TtsError as exc:
            log.error("%s", exc)
            self.events.put(None)

    def _drain(self) -> None:
        if not self.top.winfo_exists():
            return
        try:
            installed = self.events.get_nowait()
        except queue.Empty:
            self.top.after(200, self._drain)
            return
        if installed and not self.python_box.get().strip():
            # The app built its own Python: show the user which one now speaks.
            self.python_box.insert(0, installed)
        self.chatterbox_button.state(["!disabled"])
        self.chatterbox_button.configure(text=self.app.t("chatterbox_install"))
        self.chatterbox_state.configure(text=self._chatterbox_text())
        self.top.after(200, self._drain)

    def _close(self) -> None:
        self.app.options.update(
            {
                "language": self._chosen(self.language_box, self.language_keys),
                "announce": self._chosen(self.announce_box, self.announce_keys, ANNOUNCE_KEYS),
                "autoplay": self.autoplay_var.get(),
                "card_total": self.card_total_var.get(),
                "tts": self._chosen(self.engine_box, self.engine_keys),
                "voice": self._chosen(self.voice_box, self.voice_keys),
                "python": self.python_box.get().strip() or None,
                "clip": self.clip_box.get().strip() or None,
                "device": self._chosen(self.device_box, self.device_keys),
            }
        )
        self.app.language_var.set(self.app.options["language"])
        self.app._relabel()
        try:
            settings.save(self.app.options)
        except OSError as exc:
            self.app._append(f"ERROR: could not save options: {exc}")
        self.top.destroy()


class Emulator:
    """The story a run just wrote, played with the machine's own buttons.

    The story folder is already the machine's: `nodes.json` says where each button
    leads, `images/` and `audios/` hold what a stage shows and plays. A Lunii run
    writes a second copy of the story as a STUdio archive, and that one holds the
    greys its screen shows, so the window opens the archive when it finds one.
    Reading either back answers "what will the teller do" without a teller, at the
    sizes that machine's screen has.

    Two of the web emulator's buttons are missing here on purpose. The Telmi's side
    buttons seek inside the clip, and the pause button holds it: both need a player
    the app can steer, and the app hands the sound to `afplay` or `ffplay`. So the
    side buttons stay shut unless the stage has a wheel, and Stop silences the clip.
    """

    # The screen is drawn at the width every player's card can be blown up to by
    # whole pixels, which keeps the sixteen greys of a Lunii card grey.
    SCREEN_WIDTH = 640

    # The wheel's two buttons are arrows on every machine; the rest carry words.
    ARROWS = {"up": "▲", "down": "▼"}
    BUTTONS = {"ok": "emu_ok", "home": "emu_home", "stop": "chapters_stop"}

    def __init__(self, app: App, folder: Path) -> None:
        self.app = app
        try:
            self.story = emulator.read_story(folder)
        except emulator.EmulatorError as exc:
            app._append(f"ERROR: {self.t('emu_failed').format(error=exc)}")
            return

        self.top = tk.Toplevel(app.root)
        self.top.title(f"{app.t('emulator')}: {self.story.title}")
        self.top.configure(background=PAPER)
        self.top.resizable(False, False)
        self.photo: tk.PhotoImage | None = None
        self.player: subprocess.Popen | None = None
        self.current: str | None = None
        # The screen keeps the size of the cards once seen, so a stage with no
        # picture of its own does not shrink the window mid-story.
        self.size = (self.SCREEN_WIDTH, self.SCREEN_WIDTH * 3 // 4)

        grid = ttk.Frame(self.top, padding=12)
        grid.grid(sticky="nsew")
        self.canvas = tk.Canvas(
            grid,
            background=SUNK,
            relief="flat",
            highlightthickness=1,
            highlightbackground=LINE,
            highlightcolor=ACCENT,
        )
        self.canvas.grid(row=0, column=0, columnspan=5)

        self.status = ttk.Label(grid, text=self.t("emu_hint"), foreground=MUTED, wraplength=420)
        self.status.grid(row=1, column=0, columnspan=5, sticky="w", pady=(8, 8))

        self.keys: dict[str, ttk.Button] = {}
        for column, button in enumerate(("up", "down", "ok", "home", "stop")):
            box = ttk.Button(
                grid,
                text=self.ARROWS.get(button) or self.t(self.BUTTONS[button]),
                command=lambda which=button: self._press(which),
            )
            box.grid(row=2, column=column, padx=(0, 6))
            self.keys[button] = box

        self.top.bind("<Up>", lambda _event: self._press("up"))
        self.top.bind("<Down>", lambda _event: self._press("down"))
        self.top.bind("<Return>", lambda _event: self._press("ok"))
        self.top.bind("<space>", lambda _event: self._press("ok"))
        self.top.bind("<BackSpace>", lambda _event: self._press("home"))
        self.top.bind("<h>", lambda _event: self._press("home"))
        self.top.protocol("WM_DELETE_WINDOW", self._close)

        self._show(self.story.start)
        self.top.after(200, self._poll)

    def t(self, key: str) -> str:
        return self.app.t(key)

    def _show(self, stage_id: str) -> None:
        stage = self.story.stages.get(stage_id)
        if stage is None:
            return
        self.current = stage_id
        self._stop()
        self._draw(stage.image)
        if stage.audio is not None and stage.audio.is_file():
            try:
                self.player = chapters.open_preview(stage.audio)
            except chapters.ChapterError as exc:
                self.status.configure(text=str(exc))
        self._light(stage)

    def _draw(self, image: Path | None) -> None:
        """The stage's own picture, blown up by whole pixels. A stage with no image
        of its own leaves the screen empty, at the size the story's cards have."""
        self.canvas.delete("all")
        self.photo = None
        if image is not None and image.is_file():
            photo = tk.PhotoImage(data=image.read_bytes())
            step = self.SCREEN_WIDTH // photo.width()
            self.photo = photo.zoom(step, step) if step > 1 else photo
            self.size = (self.photo.width(), self.photo.height())
        self.canvas.configure(width=self.size[0], height=self.size[1])
        if self.photo is not None:
            self.canvas.create_image(self.size[0] // 2, self.size[1] // 2, image=self.photo)

    def _light(self, stage: emulator.Stage) -> None:
        """A button is lit when the stage left it on; the machine ignores the rest."""
        self.keys["ok"].state(["!disabled"] if stage.controls.ok else ["disabled"])
        self.keys["home"].state(["!disabled"] if stage.controls.home else ["disabled"])
        wheel = ["!disabled"] if stage.controls.wheel else ["disabled"]
        self.keys["up"].state(wheel)
        self.keys["down"].state(wheel)
        self.keys["stop"].state(["!disabled"] if self.player is not None else ["disabled"])

    def _press(self, button: str) -> None:
        if self.current is None:
            return
        if button == "stop":
            self._stop()
            return
        nxt = emulator.walk(self.story, self.current, button)
        if nxt and nxt != self.current:
            self._show(nxt)

    def _stop(self) -> None:
        chapters.stop_preview(self.player)
        self.player = None

    def _poll(self) -> None:
        """The clip ends on its own, and an autoplayed one forces the next stage."""
        if not self.top.winfo_exists():
            return
        if self.player is not None and self.player.poll() is not None:
            self.player = None
            stage = self.story.stages.get(self.current or "")
            if stage is not None:
                self._light(stage)
                if stage.controls.autoplay:
                    self._press("next")
        self.top.after(200, self._poll)

    def _close(self) -> None:
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
    root.title(app.t("app_title"))
    handler = attach_log_handler(app)
    try:
        root.mainloop()
    finally:
        log.removeHandler(handler)
    return 0
