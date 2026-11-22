"""Build a self-contained daisy-convertor executable with PyInstaller.

ffmpeg found on the build machine is bundled into the binary, so the end user
does not have to install it. PyInstaller cannot cross-compile, so this produces
a binary for the machine it runs on. On Windows:

    py -3.11 -m venv .venv
    .venv\\Scripts\\activate
    pip install -e .[dev]
    python build_binary.py

The result is dist/daisy-convertor-<version>-<os>-<arch>.exe on Windows, and
dist/daisy-convertor on macOS and Linux, where the packaging steps name the file
the user downloads.
"""

from __future__ import annotations

import importlib.util
import os
import platform
import shutil
import subprocess
import sys
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parent

_OS_NAMES = {"darwin": "macos", "linux": "linux", "win32": "windows"}
_ARCH_NAMES = {"AMD64": "x86_64", "x86_64": "x86_64", "ARM64": "aarch64", "aarch64": "aarch64"}


def _locate_ffmpeg() -> str | None:
    """DAISY2TELMI_FFMPEG wins over PATH, because installers do not always refresh PATH."""
    explicit = os.environ.get("DAISY2TELMI_FFMPEG")
    if not explicit:
        return shutil.which("ffmpeg")
    if not Path(explicit).is_file():
        print(f"error: DAISY2TELMI_FFMPEG points at a missing file: {explicit}", file=sys.stderr)
        return None
    return explicit


def _locate_espeak() -> tuple[str, str] | None:
    """The espeak-ng binary and its data folder.

    The data folder matters more than the binary: without it espeak-ng knows no
    languages, and the chapter cards would be silent.
    """
    explicit = os.environ.get("DAISY2TELMI_ESPEAK")
    binary = explicit or shutil.which("espeak-ng") or shutil.which("espeak")
    if not binary or not Path(binary).is_file():
        return None

    # Debian keeps the language data in /usr/share, Ubuntu in the multiarch
    # library directory, and Homebrew next to the binary's prefix.
    parent = Path(binary).parent
    candidates = [
        parent.parent / "share" / "espeak-ng-data",
        Path("/usr/share/espeak-ng-data"),
        Path("/usr/local/share/espeak-ng-data"),
        *sorted(Path("/usr/lib").glob("*/espeak-ng-data")),
        parent.parent / "lib" / "espeak-ng-data",
        parent / "espeak-ng-data",
    ]
    for candidate in candidates:
        if candidate.is_dir():
            return binary, str(candidate)
    looked = ", ".join(str(c) for c in candidates)
    print(f"warning: found {binary} but no espeak-ng-data folder in {looked}; speech will not be bundled", file=sys.stderr)
    return None


# On macOS everything outside /usr/lib and /System was installed by the user, so
# it all has to travel. On Linux the distribution puts real dependencies in
# /usr/lib too, so only the C and C++ runtime stays behind.
_LINUX_RUNTIME = (
    "ld-linux",
    "libc.so",
    "libdl.so",
    "libgcc_s.so",
    "libm.so",
    "libpthread.so",
    "libresolv.so",
    "librt.so",
    "libstdc++.so",
    "libutil.so",
)


def _linked_libs(binary: str) -> list[str]:
    """Absolute paths of the shared libraries `binary` loads, system ones excluded."""
    if sys.platform == "darwin":
        out = subprocess.run(["otool", "-L", binary], capture_output=True, text=True).stdout
        paths = [line.split()[0] for line in out.splitlines()[1:]]
        return [p for p in paths if p.startswith("/") and not p.startswith(("/usr/lib", "/System"))]

    out = subprocess.run(["ldd", binary], capture_output=True, text=True).stdout
    paths = [parts[2] for parts in (line.split() for line in out.splitlines()) if len(parts) >= 3]
    return [p for p in paths if p.startswith("/") and not p.split("/")[-1].startswith(_LINUX_RUNTIME)]


def _copy_closure(binary: str, stage: Path) -> list[Path]:
    """Copy every library `binary` loads into `stage`, following them to the end.

    apt and Homebrew name the same library through different paths, so a copy's
    file name is what identifies it.
    """
    staged_libs: list[Path] = []
    pending = [binary]
    seen: set[str] = set()
    while pending:
        for dep in _linked_libs(pending.pop()):
            if Path(dep).name in seen or not Path(dep).is_file():
                continue
            seen.add(Path(dep).name)
            target = stage / Path(dep).name
            shutil.copy2(dep, target)
            target.chmod(0o755)
            staged_libs.append(target)
            pending.append(dep)
    return staged_libs


def _stage_espeak(binary: str, data: str) -> list[str]:
    """Copy espeak-ng, its libraries and its language data into dist/speech.

    The libraries matter: the installed binary names them by absolute path, so a
    bare copy would look for Homebrew or apt files on the user's machine and fail.
    On macOS the load commands are rewritten to `@loader_path`; on Linux the
    runtime sets LD_LIBRARY_PATH instead, which needs no rewriting.
    """
    stage = ROOT / "dist" / "speech"
    if stage.exists():
        shutil.rmtree(stage)
    stage.mkdir(parents=True)

    staged_exe = stage / Path(binary).name
    shutil.copy2(binary, staged_exe)
    staged_exe.chmod(0o755)

    staged_libs = _copy_closure(binary, stage)

    if sys.platform == "darwin":
        for target in [staged_exe, *staged_libs]:
            if target.suffix == ".dylib":
                subprocess.run(
                    ["install_name_tool", "-id", f"@loader_path/{target.name}", str(target)],
                    check=True,
                    capture_output=True,
                )
            for dep in _linked_libs(str(target)):
                subprocess.run(
                    ["install_name_tool", "-change", dep, f"@loader_path/{Path(dep).name}", str(target)],
                    check=True,
                    capture_output=True,
                )

    print(f"bundling espeak-ng from {binary} with data {data} and {len(staged_libs)} librar{'y' if len(staged_libs) == 1 else 'ies'}")
    return [
        "--add-binary",
        f"{staged_exe}{os.pathsep}.",
        *(arg for lib in staged_libs for arg in ("--add-binary", f"{lib}{os.pathsep}.")),
        "--add-data",
        f"{data}{os.pathsep}espeak-ng-data",
    ]


def _speech_options() -> list[str]:
    found = _locate_espeak()
    if found is None:
        return []
    return _stage_espeak(*found)


def _ffplay(ffmpeg: str) -> Path | None:
    """The player next to the ffmpeg being bundled, wherever it came from."""
    companion = Path(ffmpeg).with_name("ffplay")
    if companion.is_file():
        return companion
    found = shutil.which("ffplay")
    return Path(found) if found else None


def _ffmpeg_options(ffmpeg: str) -> list[str]:
    """ffmpeg and ffplay, and on Linux every library they load.

    PyInstaller follows ffmpeg's own dependencies but leaves the ones it takes for
    part of the distribution behind — libdrm and libwayland among them — and a bare
    Debian 13 has neither, so the ffmpeg inside the AppImage died before it could
    crop a cover. Copying the closure ourselves keeps the picture working anywhere.
    macOS needs none of this: Homebrew names its libraries by absolute path and
    PyInstaller rewrites them.

    ffplay travels with ffmpeg because the chapters window can only offer Stop for
    a player it started itself, and the desktop's opener is not one of those.
    """
    players = [Path(ffmpeg)]
    companion = _ffplay(ffmpeg)
    if companion is None:
        print("warning: no ffplay next to ffmpeg; a chapter will not be stoppable", file=sys.stderr)
    else:
        players.append(companion)

    if sys.platform != "linux":
        return [arg for one in players for arg in ("--add-binary", f"{one}{os.pathsep}.")]

    stage = ROOT / "dist" / "ffmpeg"
    if stage.exists():
        shutil.rmtree(stage)
    stage.mkdir(parents=True)
    staged: list[Path] = []
    libraries = 0
    for source in players:
        target = stage / source.name
        shutil.copy2(source, target)
        target.chmod(0o755)
        staged.append(target)
        staged_libs = _copy_closure(str(source), stage)
        libraries += len(staged_libs)
        staged += staged_libs
    names = " and ".join(one.name for one in players)
    print(f"bundling {names} with {libraries} librar{'y' if libraries == 1 else 'ies'}")
    return [arg for one in staged for arg in ("--add-binary", f"{one}{os.pathsep}.")]


def _gui_options() -> list[str]:
    """tkinterdnd2 carries Tcl/Tk scripts and a tkdnd library PyInstaller cannot see."""
    if importlib.util.find_spec("tkinterdnd2") is None:
        return []
    return ["--collect-all", "tkinterdnd2"]


def _icon_options() -> list[str]:
    """The binary's own icon, and the PNG the window draws.

    PyInstaller takes .icns on macOS and .ico on Windows only; on Linux the icon
    belongs to the AppImage's desktop file instead. The PNG is not Python data, so
    PyInstaller leaves it behind unless it is named: the window looks for it next
    to its own module, which is where this unpacks it.
    """
    icon = {"darwin": "icon.icns", "win32": "icon.ico"}.get(sys.platform)
    return [
        *(["--icon", str(ROOT / "assets" / icon)] if icon else []),
        "--add-data",
        f"{ROOT / 'src' / 'daisy2telmi' / 'assets' / 'icon.png'}{os.pathsep}daisy2telmi/assets",
    ]


def _artifact_name() -> str:
    """The name a download should carry: the version the binary itself reports, the OS and the machine."""
    version = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))["project"]["version"]
    return f"daisy-convertor-{version}-{_OS_NAMES[sys.platform]}-{_ARCH_NAMES.get(platform.machine(), platform.machine())}"


def _name_windows_exe(dist: Path, name: str) -> Path:
    """Move the built exe to `dist/<name>.exe`, dropping the ones an earlier build left."""
    for stale in dist.glob("daisy-convertor-*.exe"):
        stale.unlink()
    built = dist / "daisy-convertor.exe"
    named = built.with_name(f"{name}.exe")
    built.replace(named)
    return named


def main() -> int:
    ffmpeg = _locate_ffmpeg()
    if ffmpeg is None:
        print(
            "error: ffmpeg was not found on this machine, so it cannot be bundled.\n"
            "Install it, or point DAISY2TELMI_FFMPEG at an ffmpeg executable.",
            file=sys.stderr,
        )
        return 1
    print(f"bundling ffmpeg from {ffmpeg} for {sys.platform}")

    command = [
        sys.executable,
        "-m",
        "PyInstaller",
        "--clean",
        "--onefile",
        "--console",
        "--name",
        "daisy-convertor",
        # Without this, a machine where the package was never pip-installed builds
        # a binary that starts and then says there is no daisy2telmi module.
        "--paths",
        str(ROOT / "src"),
        "--hidden-import",
        "lxml.etree",
        "--hidden-import",
        "lxml._elementpath",
        *_ffmpeg_options(ffmpeg),
        # Chatterbox runs in the user's own Python, so its helper has to be a real
        # file on disk; PyInstaller keeps this one at the top of the unpacked folder.
        "--add-data",
        f"{ROOT / 'src' / 'daisy2telmi' / 'chatterbox_speak.py'}{os.pathsep}.",
        *_speech_options(),
        *_gui_options(),
        *_icon_options(),
        str(ROOT / "launcher.py"),
    ]
    print(" ".join(command))
    code = subprocess.run(command, cwd=ROOT).returncode
    if code != 0:
        return code

    # On Windows the exe is the file a user downloads, so it names itself. On Linux
    # and macOS it is an input to the packaging steps, which name the download.
    if sys.platform == "win32":
        print(f"built {_name_windows_exe(ROOT / 'dist', _artifact_name())}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
