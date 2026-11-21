"""The version the tool prints and the version the package declares are one version."""

from pathlib import Path

from daisy2telmi import __version__

PYPROJECT = Path(__file__).resolve().parents[1] / "pyproject.toml"


def test_the_package_and_its_metadata_agree_on_the_version() -> None:
    declared = [
        line.split("=", 1)[1].strip().strip('"')
        for line in PYPROJECT.read_text().splitlines()
        if line.startswith("version =")
    ]
    assert declared == [__version__], "a release bumps both"
