"""Show daisy2telmi's controls under every way of theming Tk, switchable live.

    python tools/theming_demo.py            # the window
    python tools/theming_demo.py --facts    # print what this Tk offers, and stop

The form is the one the app really draws: format, cut level, cover crop, the
speech options, the chapter list, the log. Only the colours, the spacing and the
fonts change, so the two are easy to compare side by side.
"""

from __future__ import annotations

import argparse
import sys
import tkinter as tk
from tkinter import font as tkfont
from tkinter import ttk

# ---------------------------------------------------------------- the palettes

# `clam` is the only built-in Tk theme whose colours you can actually set: the
# platform theme (aqua on macOS, vista on Windows) draws its widgets from the
# system and ignores most of what you ask for.
LIGHT = {
    "bg": "#f4f5f7",
    "panel": "#ffffff",
    "fg": "#1b1f27",
    "muted": "#5c6470",
    "accent": "#2f6f4f",
    "accent_fg": "#ffffff",
    "edge": "#c8ccd4",
    "sel": "#dce9e2",
    "field": "#ffffff",
    "disabled": "#9aa1ac",
}

DARK = {
    "bg": "#1b1f27",
    "panel": "#232833",
    "fg": "#e8eaee",
    "muted": "#9aa1ac",
    "accent": "#4f9b74",
    "accent_fg": "#10141a",
    "edge": "#3a414f",
    "sel": "#2c3a44",
    "field": "#161a21",
    "disabled": "#5c6470",
}

# Sizes are set once, on the named fonts every ttk widget already uses, rather
# than on each widget: that is what makes the whole window change at once.
SIZES = {"compact": (9, 4), "roomy": (11, 9)}


# ------------------------------------------------------------- the theming ways

def plain(style: ttk.Style, root: tk.Tk, size: str) -> None:
    """What the app does today: the platform's own theme, nothing configured."""
    style.theme_use(PLATFORM_THEME)
    _fonts(root, size, default=True)


def built_in(name: str):
    """One of Tk's own themes, untouched."""

    def apply(style: ttk.Style, root: tk.Tk, size: str) -> None:
        style.theme_use(name)
        _fonts(root, size, default=True)

    return apply


def branded(style: ttk.Style, root: tk.Tk, size: str) -> None:
    """`clam` plus a palette and spacing: what the app could ship."""
    _brand(style, root, size, LIGHT)


def branded_dark(style: ttk.Style, root: tk.Tk, size: str) -> None:
    """The same, in dark. Tk never follows the system's light/dark setting."""
    _brand(style, root, size, DARK)


def _brand(style: ttk.Style, root: tk.Tk, size: str, c: dict) -> None:
    style.theme_use("clam")
    _fonts(root, size)

    # "." is the base every ttk style inherits, so the background and the padding
    # land on widgets this file never mentions (Notebook, Panedwindow, ...).
    style.configure(".", background=c["bg"], foreground=c["fg"], padding=SIZES[size][1])
    style.configure("TFrame", background=c["bg"])
    style.configure("TLabel", background=c["bg"])
    style.configure("Heading.TLabel", font="HeadingFont", foreground=c["fg"])
    style.configure("Muted.TLabel", foreground=c["muted"])

    style.configure(
        "TButton",
        background=c["panel"],
        foreground=c["fg"],
        bordercolor=c["edge"],
        focusthickness=0,
        padding=(12, SIZES[size][1]),
    )
    style.map(
        "TButton",
        background=[("disabled", c["bg"]), ("active", c["sel"]), ("pressed", c["accent"])],
        foreground=[("disabled", c["disabled"]), ("pressed", c["accent_fg"])],
        bordercolor=[("active", c["accent"])],
    )
    style.configure(
        "Accent.TButton", background=c["accent"], foreground=c["accent_fg"], bordercolor=c["accent"]
    )
    style.map(
        "Accent.TButton",
        background=[("disabled", c["bg"]), ("active", c["accent"]), ("pressed", c["sel"])],
        foreground=[("disabled", c["disabled"]), ("pressed", c["fg"])],
    )

    for widget in ("TEntry", "TCombobox", "TSpinbox"):
        style.configure(
            widget, fieldbackground=c["field"], background=c["field"], bordercolor=c["edge"],
            arrowcolor=c["muted"], insertcolor=c["fg"],
        )
        style.map(
            widget,
            fieldbackground=[("disabled", c["bg"]), ("readonly", c["bg"])],
            foreground=[("disabled", c["disabled"])],
            bordercolor=[("focus", c["accent"]), ("disabled", c["edge"])],
            arrowcolor=[("disabled", c["disabled"])],
        )
    # The combobox's popup is a plain Tk listbox, outside the ttk style tree.
    root.option_add("*TCombobox*Listbox.background", c["field"])
    root.option_add("*TCombobox*Listbox.foreground", c["fg"])
    root.option_add("*TCombobox*Listbox.selectBackground", c["sel"])
    root.option_add("*TCombobox*Listbox.selectForeground", c["fg"])

    for widget in ("TCheckbutton", "TRadiobutton"):
        style.configure(widget, background=c["bg"], indicatorcolor=c["field"], bordercolor=c["edge"])
        style.map(
            widget,
            background=[("active", c["bg"])],
            indicatorcolor=[("selected", c["accent"]), ("active", c["sel"])],
            foreground=[("disabled", c["disabled"])],
        )

    style.configure("Treeview", background=c["panel"], fieldbackground=c["panel"], rowheight=_rowheight(size))
    style.configure("Treeview.Heading", background=c["bg"], foreground=c["muted"], relief="flat")
    style.map(
        "Treeview",
        background=[("selected", c["sel"])],
        foreground=[("selected", c["fg"])],
    )
    style.map("Treeview.Heading", background=[("active", c["bg"])])

    style.configure("TNotebook", background=c["bg"], borderwidth=0)
    style.configure("TNotebook.Tab", background=c["bg"], padding=(12, 6))
    style.map("TNotebook.Tab", background=[("selected", c["panel"])], foreground=[("selected", c["fg"])])

    style.configure("TProgressbar", background=c["accent"], troughcolor=c["edge"], bordercolor=c["edge"])

    # clam's scrollbar has no arrows by default; the arrowspace layout is what
    # gives it the ends people expect.
    style.configure("TScrollbar", background=c["panel"], troughcolor=c["bg"], bordercolor=c["edge"], arrowsize=12)
    style.map("TScrollbar", background=[("active", c["sel"])])

    # `Text` is a Tk widget, not a ttk one, so no style reaches it: it has to be
    # coloured by hand. This is the main cost of theming Tk.
    for widget in root.winfo_children():
        _recolour(widget, c)


def _recolour(widget: tk.Misc, c: dict) -> None:
    """Colour the plain-Tk widgets a ttk style cannot reach (Text, Canvas)."""
    for child in widget.winfo_children():
        _recolour(child, c)
    if isinstance(widget, tk.Text):
        widget.configure(
            background=c["field"], foreground=c["fg"], insertbackground=c["fg"],
            highlightbackground=c["edge"], highlightcolor=c["accent"],
        )


def _fonts(root: tk.Tk, size: str, default: bool = False) -> None:
    """Scale the named fonts every widget already uses."""
    if default:
        return
    points, _pad = SIZES[size]
    base = tkfont.nametofont("TkDefaultFont")
    for name in ("TkDefaultFont", "TkTextFont", "TkMenuFont", "TkHeadingFont"):
        try:
            tkfont.nametofont(name).configure(size=points)
        except tk.TclError:
            pass
    # Re-applying a theme must not create the font a second time.
    try:
        heading = tkfont.nametofont("HeadingFont")
    except tk.TclError:
        heading = tkfont.Font(name="HeadingFont")
    heading.configure(family=base.actual("family"), size=points + 1, weight="bold")
    root.option_add("*Font", base)


# The theme Tk picked for this platform, before anything here changes it.
PLATFORM_THEME = "default"
_rowheight_cache: dict[str, int] = {}


def _rowheight(size: str) -> int:
    """A Treeview row has to be told its height; it does not follow the font."""
    if size not in _rowheight_cache:
        _rowheight_cache[size] = SIZES[size][0] * 2 + 10
    return _rowheight_cache[size]


THEMES = {
    "Platform": (plain, "Tk's own theme for this OS. Nothing is configurable."),
    "clam": (built_in("clam"), "Tk's cross-platform theme, at its own colours."),
    "alt": (built_in("alt"), "Tk's other cross-platform theme."),
    "Branded": (branded, "clam + a palette, spacing and fonts. What the app could ship."),
    "Branded dark": (branded_dark, "The same in dark. Tk never follows the system setting."),
}


# ------------------------------------------------------------- the form itself

FORMATS = ("Telmi", "Lunii", "Flam", "Merlin")
CUTS = ("Chapters", "Paragraphs", "Sentences")
CROPS = ("Top", "Centre", "Bottom")


class Demo:
    def __init__(self, root: tk.Tk) -> None:
        global PLATFORM_THEME
        self.root = root
        self.style = ttk.Style(root)
        PLATFORM_THEME = self.style.theme_use()
        self.theme_name = tk.StringVar(value="Branded")
        self.size = tk.StringVar(value="roomy")

        root.title("Tk theming demo")
        root.columnconfigure(0, weight=1)
        root.rowconfigure(2, weight=1)

        self._picker()
        self._form()
        self._log()
        self._facts()
        self.apply()

    def _picker(self) -> None:
        bar = ttk.Frame(self.root, padding=8)
        bar.grid(row=0, column=0, sticky="ew")
        ttk.Label(bar, text="Theme").grid(row=0, column=0, sticky="w")
        for column, name in enumerate(THEMES, start=1):
            ttk.Radiobutton(bar, text=name, value=name, variable=self.theme_name, command=self.apply).grid(
                row=0, column=column, padx=(8, 0)
            )
        ttk.Radiobutton(bar, text="Compact", value="compact", variable=self.size, command=self.apply).grid(
            row=1, column=1, sticky="w", pady=(6, 0)
        )
        ttk.Radiobutton(bar, text="Roomy", value="roomy", variable=self.size, command=self.apply).grid(
            row=1, column=2, sticky="w", pady=(6, 0)
        )
        self.note = ttk.Label(bar, style="Muted.TLabel")
        self.note.grid(row=2, column=0, columnspan=6, sticky="w", pady=(6, 0))

    def _form(self) -> None:
        box = ttk.Frame(self.root, padding=8)
        box.grid(row=1, column=0, sticky="nsew")
        box.columnconfigure(0, weight=1)
        box.columnconfigure(1, weight=1)

        left = ttk.Frame(box)
        left.grid(row=0, column=0, sticky="nsew")
        left.columnconfigure(1, weight=1)
        ttk.Label(left, text="Output format", style="Heading.TLabel").grid(columnspan=2, sticky="w")
        ttk.Label(left, text="Player").grid(row=1, column=0, sticky="w")
        ttk.Combobox(left, values=FORMATS, state="readonly", width=14).grid(row=1, column=1, sticky="w", padx=8)
        ttk.Label(left, text="Cut at").grid(row=2, column=0, sticky="w")
        cuts = ttk.Frame(left)
        cuts.grid(row=2, column=1, sticky="w", padx=8)
        for name in CUTS:
            ttk.Radiobutton(cuts, text=name, value=name).pack(side="left", padx=(0, 8))
        ttk.Checkbutton(left, text="Night mode").grid(row=3, column=1, sticky="w", padx=8, pady=(6, 0))
        ttk.Checkbutton(left, text="Colour images", state="disabled").grid(
            row=4, column=1, sticky="w", padx=8, pady=(4, 0)
        )

        right = ttk.Frame(box)
        right.grid(row=0, column=1, sticky="nsew", padx=(12, 0))
        right.columnconfigure(1, weight=1)
        ttk.Label(right, text="Speech", style="Heading.TLabel").grid(columnspan=2, sticky="w")
        ttk.Label(right, text="Engine").grid(row=1, column=0, sticky="w")
        ttk.Combobox(right, values=("Chatterbox", "espeak-ng", "macOS say"), state="readonly", width=14).grid(
            row=1, column=1, sticky="w", padx=8
        )
        ttk.Label(right, text="Python").grid(row=2, column=0, sticky="w")
        ttk.Entry(right).grid(row=2, column=1, sticky="ew", padx=8)
        ttk.Label(right, text="Crop").grid(row=3, column=0, sticky="w")
        crops = ttk.Frame(right)
        crops.grid(row=3, column=1, sticky="w", padx=8)
        for name in CROPS:
            ttk.Radiobutton(crops, text=name, value=name).pack(side="left", padx=(0, 8))
        ttk.Button(right, text="Install Chatterbox").grid(row=4, column=1, sticky="e", padx=8, pady=(8, 0))

        listing = ttk.Frame(box)
        listing.grid(row=1, column=0, columnspan=2, sticky="nsew", pady=(12, 0))
        box.rowconfigure(1, weight=1)
        listing.columnconfigure(0, weight=1)
        listing.rowconfigure(1, weight=1)
        ttk.Label(listing, text="Chapters", style="Heading.TLabel").grid(columnspan=2, sticky="w")
        tree = ttk.Treeview(listing, columns=("number", "length"), height=5)
        tree.heading("#0", text="Title")
        tree.heading("number", text="#")
        tree.heading("length", text="Length")
        tree.column("#0", width=280)
        for index, title in enumerate(
            ("Chapter One", "A section", "Chapter Two", "Chapter Three", "Notes"), start=1
        ):
            tree.insert("", "end", text=title, values=(index, f"{index * 7}:{index * 11:02d}"))
        scroller = ttk.Scrollbar(listing, orient="vertical")
        tree.grid(row=1, column=0, sticky="nsew")
        scroller.grid(row=1, column=1, sticky="ns")
        tree.configure(yscrollcommand=scroller.set)
        scroller.configure(command=tree.yview)

        actions = ttk.Frame(box)
        actions.grid(row=2, column=0, columnspan=2, sticky="ew", pady=(10, 0))
        actions.columnconfigure(2, weight=1)
        ttk.Button(actions, text="Options").grid(row=0, column=0, padx=(0, 6))
        ttk.Button(actions, text="Save", style="Accent.TButton").grid(row=0, column=1)
        ttk.Progressbar(actions, mode="indeterminate").grid(row=0, column=2, sticky="ew", padx=(12, 0))

    def _log(self) -> None:
        self.log = tk.Text(self.root, height=5, state="normal")
        self.log.grid(row=2, column=0, sticky="nsew", padx=8, pady=(4, 8))

    def _facts(self) -> None:
        names = ", ".join(self.style.theme_names())
        self.log.insert(
            "end",
            f"Python {sys.version.split()[0]}  Tk {tk.TkVersion}  ttk {self.style.theme_use()}\n"
            f"themes this Tk offers: {names}\n"
            "Tk has no system light/dark setting: a dark look is a palette you set yourself.\n"
            "The platform theme ignores background/foreground on most widgets; clam does not.\n",
        )

    def apply(self) -> None:
        THEMES[self.theme_name.get()][0](self.style, self.root, self.size.get())
        self.note.configure(text=THEMES[self.theme_name.get()][1])
        self.root.update_idletasks()


def facts() -> int:
    """Print what this Tk offers, without leaving a window open."""
    root = tk.Tk()
    root.withdraw()
    style = ttk.Style(root)
    print(f"Python {sys.version.split()[0]}")
    print(f"Tk {tk.TkVersion}")
    print(f"current theme: {style.theme_use()}")
    print(f"themes: {', '.join(style.theme_names())}")
    print(f"default font: {tkfont.nametofont('TkDefaultFont').actual()}")
    root.destroy()
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--facts", action="store_true", help="print this Tk's themes and stop")
    args = parser.parse_args(argv)
    if args.facts:
        return facts()
    Demo(tk.Tk()).root.mainloop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
