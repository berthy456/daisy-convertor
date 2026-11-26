"""The Windows build: the folder PyInstaller makes, packaged by `windows.iss`."""

from __future__ import annotations

import importlib.util
import shutil
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest
from daisy2telmi import __version__

_spec = importlib.util.spec_from_file_location(
    "build_binary", Path(__file__).resolve().parents[1] / "build_binary.py"
)
build_binary = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(build_binary)

ISS = (Path(__file__).resolve().parents[1] / "windows.iss").read_text(encoding="utf-8")


def test_the_setup_is_named_for_the_version_the_app_reports() -> None:
    assert build_binary._version() == __version__


@pytest.mark.parametrize(
    ("os_name", "expected"),
    [
        # A one-file exe unpacks its whole payload on every start, and a console
        # build opens a terminal the GUI does not use.
        ("win32", ["--onedir", "--windowed"]),
        ("darwin", ["--onefile", "--console"]),
        ("linux", ["--onefile", "--console"]),
    ],
)
def test_windows_gets_a_folder_and_no_console(monkeypatch: pytest.MonkeyPatch, os_name: str, expected: list[str]) -> None:
    monkeypatch.setattr(sys, "platform", os_name)
    assert build_binary._mode_options() == expected


def test_ffplay_is_found_by_its_windows_name(tmp_path: Path) -> None:
    (tmp_path / "ffmpeg.exe").write_bytes(b"MZ")
    (tmp_path / "ffplay.exe").write_bytes(b"MZ")
    assert build_binary._ffplay(str(tmp_path / "ffmpeg.exe")).name == "ffplay.exe"


def test_the_setup_unpacks_the_folder_into_the_users_own_folder() -> None:
    assert r'DefaultDirName={localappdata}\daisy2telmi\app' in ISS
    assert r'Source: "dist\daisy-convertor\*"' in ISS
    assert r'Name: "{autoprograms}\Daisy Convertor"' in ISS


def test_the_setup_offers_python_where_the_app_looks_for_it() -> None:
    assert 'Name: "Python"' in ISS
    # Per-user: no admin rights, and it lands in the folder tts._installs() searches.
    assert "InstallAllUsers=0" in ISS
    # The engine install runs pip in the environment it builds.
    assert "Include_pip=1" in ISS
    assert r"{localappdata}\Programs\Python" in ISS


def test_the_uninstall_leaves_the_users_own_speech_folder_alone() -> None:
    # The engines live in data_dir(), beside {app} rather than inside it.
    assert "[UninstallDelete]" not in ISS


def test_the_windows_build_finishes_by_packaging_the_folder(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    class Runner:
        def run(self, *args, **kwargs):
            return SimpleNamespace(returncode=0)

    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setattr(build_binary, "ROOT", tmp_path)
    monkeypatch.setattr(build_binary, "subprocess", Runner())
    monkeypatch.setattr(build_binary, "_locate_ffmpeg", lambda: "ffmpeg")
    for name in ("_ffmpeg_options", "_speech_options", "_gui_options", "_asset_options"):
        monkeypatch.setattr(build_binary, name, lambda *args: [])

    built = []
    monkeypatch.setattr(
        build_binary,
        "_build_installer",
        lambda dist: built.append(dist) or dist / f"daisy-convertor-{__version__}-setup.exe",
    )
    assert build_binary.main() == 0
    assert built == [tmp_path / "dist"]


def test_no_setup_without_inno_setup(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr(shutil, "which", lambda name: None)
    monkeypatch.delenv("ProgramFiles(x86)", raising=False)
    monkeypatch.delenv("ProgramFiles", raising=False)
    assert build_binary._build_installer(tmp_path) is None
