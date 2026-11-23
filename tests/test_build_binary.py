"""The name a build ships under: the version, the OS and the machine, always."""

from __future__ import annotations

import importlib.util
import platform
import sys
import zipfile
from pathlib import Path

import pytest
from daisy2telmi import __version__

_spec = importlib.util.spec_from_file_location(
    "build_binary", Path(__file__).resolve().parents[1] / "build_binary.py"
)
build_binary = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(build_binary)


@pytest.mark.parametrize(
    ("os_name", "machine", "expected"),
    [
        ("win32", "AMD64", "windows-x86_64"),
        ("win32", "ARM64", "windows-aarch64"),
        ("darwin", "arm64", "macos-arm64"),
        ("linux", "x86_64", "linux-x86_64"),
    ],
)
def test_the_artifact_name_names_the_os_and_the_machine(
    monkeypatch: pytest.MonkeyPatch, os_name: str, machine: str, expected: str
) -> None:
    monkeypatch.setattr(sys, "platform", os_name)
    monkeypatch.setattr(platform, "machine", lambda: machine)
    assert build_binary._artifact_name() == f"daisy-convertor-{__version__}-{expected}"


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


def test_the_windows_build_ships_its_folder_as_a_zip(tmp_path: Path) -> None:
    dist = tmp_path / "dist"
    folder = dist / "daisy-convertor"
    (folder / "_internal").mkdir(parents=True)
    (folder / "daisy-convertor.exe").write_bytes(b"MZ")
    (folder / "_internal" / "ffmpeg.exe").write_bytes(b"MZ")

    named = build_binary._zip_windows_folder(dist, "daisy-convertor-1.3-windows-x86_64")

    assert named.name == "daisy-convertor-1.3-windows-x86_64.zip"
    assert not folder.exists(), "the folder travels inside the zip, not beside it"
    with zipfile.ZipFile(named) as archive:
        assert "daisy-convertor/daisy-convertor.exe" in archive.namelist()
        assert "daisy-convertor/_internal/ffmpeg.exe" in archive.namelist()


def test_an_earlier_build_does_not_stay_in_dist(tmp_path: Path) -> None:
    dist = tmp_path / "dist"
    folder = dist / "daisy-convertor"
    folder.mkdir(parents=True)
    (dist / "daisy-convertor-1.2-windows-x86_64.zip").write_bytes(b"PK")
    (folder / "daisy-convertor.exe").write_bytes(b"MZ")

    build_binary._zip_windows_folder(dist, "daisy-convertor-1.3-windows-x86_64")

    assert [p.name for p in dist.iterdir()] == ["daisy-convertor-1.3-windows-x86_64.zip"]
