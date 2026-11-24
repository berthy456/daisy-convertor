"""Saved options: the window writes them, the command line reads them."""

from __future__ import annotations

import json

import pytest

from daisy2telmi import cli, settings


def _machine_language(monkeypatch, value: str) -> None:
    """Ask the settings module what it would see on a machine set to `value`."""
    for name in ("LC_ALL", "LC_MESSAGES", "LANGUAGE"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("LANG", value)
    monkeypatch.setattr(settings, "_apple_languages", lambda: "")


def test_missing_file_gives_the_defaults(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "absent"))
    _machine_language(monkeypatch, "fr_FR.UTF-8")
    assert settings.load() == dict(settings.DEFAULTS, language="fr")


def test_the_machine_language_is_the_one_the_window_starts_in(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "absent"))
    _machine_language(monkeypatch, "de_DE.UTF-8")
    assert settings.load()["language"] == "de"


def test_a_language_the_app_has_no_words_for_leaves_english(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "absent"))
    _machine_language(monkeypatch, "cy_GB.UTF-8")
    assert settings.load()["language"] == "en"


def test_a_saved_language_beats_the_machine(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    _machine_language(monkeypatch, "de_DE.UTF-8")
    settings.save(dict(settings.DEFAULTS, language="tr"))
    assert settings.load()["language"] == "tr"


def test_round_trip(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    options = dict(settings.DEFAULTS, language="tr", announce="chapter", autoplay=True, voice="Cem")
    path = settings.save(options)
    assert settings.load() == options
    assert json.loads(path.read_text(encoding="utf-8"))["language"] == "tr"


def test_the_folder_the_last_file_list_ended_in_is_kept(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    settings.save(dict(settings.DEFAULTS, last_dir=str(tmp_path / "books")))
    assert settings.load()["last_dir"] == str(tmp_path / "books")


def test_unknown_keys_are_ignored(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    settings.save(dict(settings.DEFAULTS, language="de", cover="nope"))
    assert settings.load()["language"] == "de"
    assert "cover" not in settings.load()


def test_a_file_written_before_the_own_bar_default_loses_that_default(tmp_path, monkeypatch) -> None:
    """1.3 wrote `frameless: false` for everyone, and no one was asked."""
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    settings.save(dict(settings.DEFAULTS, language="fr"))
    stored = json.loads(settings.config_path().read_text(encoding="utf-8"))
    stored["frameless"] = False
    stored["written_by"] = "1.3"
    settings.config_path().write_text(json.dumps(stored), encoding="utf-8")
    assert settings.load()["frameless"] is True


def test_a_system_bar_put_back_after_that_version_stays_back(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    settings.save(dict(settings.DEFAULTS, frameless=False))
    assert settings.load()["frameless"] is False


def test_a_broken_file_does_not_stop_a_conversion(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    _machine_language(monkeypatch, "en_US.UTF-8")
    settings.config_path().parent.mkdir(parents=True, exist_ok=True)
    settings.config_path().write_text("{not json", encoding="utf-8")
    assert settings.load() == settings.DEFAULTS
