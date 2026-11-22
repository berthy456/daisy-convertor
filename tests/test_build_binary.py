"""The name a build ships under: the version, the OS and the machine, always."""

from __future__ import annotations

import importlib.util
import platform
import sys
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


def test_the_windows_exe_takes_the_artifact_name(tmp_path: Path) -> None:
    dist = tmp_path / "dist"
    dist.mkdir()
    (dist / "daisy-convertor.exe").write_bytes(b"MZ")

    named = build_binary._name_windows_exe(dist, "daisy-convertor-1.3-windows-x86_64")

    assert named.name == "daisy-convertor-1.3-windows-x86_64.exe"
    assert [p.name for p in dist.iterdir()] == [named.name]


def test_an_earlier_build_does_not_stay_in_dist(tmp_path: Path) -> None:
    dist = tmp_path / "dist"
    dist.mkdir()
    (dist / "daisy-convertor-1.2-windows-x86_64.exe").write_bytes(b"MZ")
    (dist / "daisy-convertor.exe").write_bytes(b"MZ")

    build_binary._name_windows_exe(dist, "daisy-convertor-1.3-windows-x86_64")

    assert [p.name for p in dist.iterdir()] == ["daisy-convertor-1.3-windows-x86_64.exe"]
