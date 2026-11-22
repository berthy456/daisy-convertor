"""Assemble an AppImage from the built daisy-convertor binary.

    python tools/make_appimage.py dist/daisy-convertor dist/daisy-convertor.AppImage [--appimagetool PATH]

The layout is the usual one: the binary under `usr/bin`, an `AppRun` entry point
that execs it, and a `.desktop` file with its icon — the pair at the AppDir root
for appimagetool, and copies under `usr/share/applications` and
`usr/share/icons/hicolor` for the desktop. `appimagetool` turns that folder into
the squashfs image; when it is not on the machine the AppDir is left behind and
the command to finish the job is printed, so the layout can still be checked.
"""

from __future__ import annotations

import argparse
import os
import shutil
import stat
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

# The name comes from the binary: appimagetool stops on a desktop file whose
# Exec and Icon do not match a file the AppDir carries.
DESKTOP = """[Desktop Entry]
Type=Application
Name={name}
Comment=Convert a DAISY, EPUB or text-only book into a Telmi, Lunii, Flam or Merlin story
Exec={name}
Icon={name}
Terminal=true
Categories=Utility;
"""

APPRUN = """#!/usr/bin/env bash
here="$(dirname "$(readlink -f "$0")")"
export APPDIR="$here"
exec "$here/usr/bin/{binary}" "$@"
"""


# The icon files, and the hicolor folder each one is installed at.
ICONS = (("128x128", "icon.png"), ("64x64", "icon-64.png"), ("16x16", "icon-16.png"))


def build_appdir(binary: Path, appdir: Path, icons: Path | None = None) -> Path:
    appdir = appdir.resolve()
    if appdir.exists():
        shutil.rmtree(appdir)
    (appdir / "usr" / "bin").mkdir(parents=True)

    name = binary.name
    target = appdir / "usr" / "bin" / name
    shutil.copy2(binary, target)
    target.chmod(target.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)

    apprun = appdir / "AppRun"
    apprun.write_text(APPRUN.format(binary=name), encoding="utf-8")
    apprun.chmod(apprun.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)

    (appdir / f"{name}.desktop").write_text(DESKTOP.format(name=name), encoding="utf-8")
    if icons is None:
        return appdir

    root_icon = icons / ICONS[0][1]
    if root_icon.is_file():
        # appimagetool stops with an error when the desktop file names an icon
        # the AppDir does not carry, and makes the .DirIcon out of it.
        shutil.copy2(root_icon, appdir / f"{name}.png")

    # The AppDir's own desktop file and icon are only what appimagetool reads. The
    # copies under usr/share are what a launcher installs, and an AppImage that
    # carries neither lands in the menu with no picture.
    applications = appdir / "usr" / "share" / "applications"
    applications.mkdir(parents=True)
    shutil.copy2(appdir / f"{name}.desktop", applications / f"{name}.desktop")
    for size, file_name in ICONS:
        source = icons / file_name
        if not source.is_file():
            continue
        folder = appdir / "usr" / "share" / "icons" / "hicolor" / size / "apps"
        folder.mkdir(parents=True)
        shutil.copy2(source, folder / f"{name}.png")
    return appdir


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("binary", type=Path)
    parser.add_argument("output", type=Path)
    parser.add_argument("--appimagetool", type=Path, help="path to appimagetool; searched on PATH otherwise")
    parser.add_argument("--arch", default="x86_64")
    parser.add_argument("--icons", type=Path, default=ROOT / "src" / "daisy2telmi" / "assets")
    args = parser.parse_args(argv)

    if not args.binary.is_file():
        print(f"error: no binary at {args.binary}", file=sys.stderr)
        return 1

    appdir = build_appdir(
        args.binary,
        args.output.with_suffix("").parent / f"{args.output.stem}-AppDir",
        args.icons if args.icons.is_dir() else None,
    )
    tool = str(args.appimagetool) if args.appimagetool else shutil.which("appimagetool")
    if tool is None:
        print(f"AppDir ready at {appdir}")
        print(f"finish with: ARCH={args.arch} appimagetool {appdir} {args.output}")
        return 0

    env = dict(os.environ, ARCH=args.arch)
    result = subprocess.run([tool, str(appdir), str(args.output)], env=env)
    return result.returncode


if __name__ == "__main__":
    raise SystemExit(main())
