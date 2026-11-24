"""The version every front end shows is the version the package declares."""

from pathlib import Path

from daisy2telmi import __version__

ROOT = Path(__file__).resolve().parents[1]
PYPROJECT = ROOT / "pyproject.toml"
INDEX = ROOT / "web" / "index.html"


def test_the_package_and_its_metadata_agree_on_the_version() -> None:
    declared = [
        line.split("=", 1)[1].strip().strip('"')
        for line in PYPROJECT.read_text().splitlines()
        if line.startswith("version =")
    ]
    assert declared == [__version__], "a release bumps both"


def test_the_web_page_shows_that_version_beside_its_name() -> None:
    assert f'<span class="version">{__version__}</span>' in INDEX.read_text()
