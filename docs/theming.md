# Theming: Tk today, PyQt if we move

`tools/theming_demo.py` draws the app's real controls under each way of theming
Tk, switchable live. Run it to see the difference instead of reading about it:

    python tools/theming_demo.py            # the window
    python tools/theming_demo.py --facts    # what this Tk offers, no window

## What the app uses now

Measured from `src/daisy2telmi/gui.py` (974 lines):

| Tk widget | Used for |
| :--- | :--- |
| `ttk.Frame`, `ttk.Label`, `ttk.Button` | every window's layout |
| `ttk.Combobox`, `ttk.Entry`, `ttk.Checkbutton`, `ttk.Radiobutton` | format, cut level, crop, speech options |
| `ttk.Treeview`, `ttk.Scrollbar` | the chapter list |
| `ttk.Progressbar` | the convert bar |
| `tk.Toplevel` | the chapters and options windows |
| `tk.Text` | the log |
| `tk.PhotoImage` | the crop previews |
| `StringVar`, `BooleanVar` | every option |

`ttk.Style` appears nowhere: the app takes whatever theme Tk picks for the
platform and configures nothing. That is the whole of the current look.

The frozen macOS binary is 48.9 MB (`dist/daisy2telmi`), Tk included.

## What Tk theming can do

- `ttk.Style().theme_names()` lists the themes this Tk was built with. On macOS
  the running theme is the platform's own (`aqua`), on Windows `vista`.
- The platform theme draws from the system and **ignores `background` and
  `foreground` on most widgets**. To set colours you switch to `clam` or `alt`,
  which are Tk's own cross-platform themes and look a few years older.
- `style.configure` sets a colour, a font or a padding for a widget class or for
  a custom style name (`Accent.TButton`); `style.map` sets the same per state
  (`active`, `pressed`, `disabled`, `selected`, `focus`).
- `style.configure(".", ...)` reaches every ttk widget at once, including ones
  the code never mentions.
- Fonts scale globally through the named fonts (`TkDefaultFont`, …) that every
  widget already uses, so one call changes the whole window.
- Two things sit outside the style system and must be coloured by hand: the
  combobox's popup listbox (through `option_add`) and any plain-`tk` widget —
  `Text`, `Canvas`. The app uses both.
- Tk never reads the system's light/dark setting. A dark look is a palette you
  set yourself, and it stays dark when the OS goes light.
- Third-party theme packs exist (`sv-ttk` is a set of Tcl files, `ttkbootstrap`
  is a Python package). Both are layers over the same `ttk.Style` calls, and
  `ttkbootstrap` is a dependency the frozen binaries would have to carry.

## What PyQt would give

- Qt styles the whole widget set from one place: `setStyle("Fusion")` plus a
  `QPalette`, or a stylesheet that is a CSS subset. Every widget, including the
  text edit and the item views, follows the palette.
- `QStyleHints.colorScheme()` (Qt 6.5+) reports the OS light/dark setting, so the
  app can follow it. Tk has no equivalent.
- Native drag-and-drop, so the `tkinterdnd2` dependency goes away.
- Licensing is not a blocker: PyQt6 is GPL v3 (or commercial), PySide6 is LGPL v3,
  and this project is GPL.

## The comparison

| | Tk (now) | PyQt6 / PySide6 |
| :--- | :--- | :--- |
| Dependency | none, it is the stdlib | a Qt install and a PyInstaller hook |
| Frozen size | 48.9 MB measured | see below |
| Colour control | only on `clam`/`alt`, not on the platform theme | everywhere, from one palette |
| System dark mode | no | yes, Qt 6.5+ |
| Widgets outside the theme | `Text`, `Canvas`, combobox popup | none |
| Drag and drop | `tkinterdnd2` | built in |
| Licence | PSF | GPL v3 / LGPL v3 |
| Code to move | — | 974 lines of `gui.py` |

## Size

Measured on this machine (macOS, Python 3.15), downloaded not installed:

| | Wheels |
| :--- | :--- |
| `PySide6-Essentials` 6.12.0 (universal2: arm64 + x86_64) | 116 MB |
| `shiboken6` 6.12.0 | 4.8 MB |
| `PySide6` itself | 0.6 MB (it only pulls the two above) |

111 MB of Qt before PyInstaller has trimmed anything, against 48.9 MB for the
whole app today. PyInstaller does cut Qt down, and a single-file build compresses
what it keeps, so the shipped figure would be lower than 111 MB added — but it
has not been measured here, and the wheels are universal2 while the CI builds one
architecture per binary, which roughly halves them.

## Reading

Tk can be made to look deliberate — `clam` with a palette, spacing and named
fonts, which is the `Branded` entry in the demo — and it costs no dependency and
no size. It cannot look native, and it cannot follow the OS dark setting.

The app's widget set is small and ordinary: frames, labels, buttons, combos,
entries, radios, checks, one tree, one progress bar, one text log. Nothing in it
needs Qt. So the choice is between paying a dependency and a size increase for a
system-following look, or staying in the stdlib and doing the palette by hand.
