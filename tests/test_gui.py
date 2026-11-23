"""The Tkinter front end. Skipped where no display or no Tk is available."""

from __future__ import annotations

import contextlib
import json
import logging
import os
import shutil
import time
import zipfile
from pathlib import Path

import pytest

tk = pytest.importorskip("tkinter")

from daisy2telmi import gui, images
from daisy2telmi.detector import DaisyPackError
from daisy2telmi.extractor import AudioSegment, Book, Chapter


@pytest.fixture()
def app(tmp_path, monkeypatch):
    # A page that saves on close would otherwise leave its options in the config
    # folder the rest of the suite reads.
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
    try:
        root = gui.make_root()
    except tk.TclError as exc:  # headless CI
        pytest.skip(f"no display: {exc}")
    instance = gui.App(root)
    instance.options["tts"] = "off"  # a window test must not stop on a Chatterbox install
    try:
        yield instance
    finally:
        root.destroy()


def test_the_window_builds(app: gui.App) -> None:
    assert app.save_button.instate(["!disabled"])


def test_the_window_is_given_the_app_icon(app: gui.App) -> None:
    """The PNG is committed as package data and unpacked next to the module in a
    frozen binary, so the window can always draw it."""
    assert gui.ICON.is_file()
    assert gui.ICON.read_bytes()[:8] == b"\x89PNG\r\n\x1a\n"
    assert app.root._icon is not None


def test_each_mark_is_its_own_file(app: gui.App) -> None:
    """Tk cannot scale a photo image, so a size the window asks for has to be
    shipped as its own PNG; a missing one draws nothing at all."""
    for side in (64, 16):
        photo = app._mark_photo(side)
        assert photo is not None, f"no {side}-pixel mark"
        assert (photo.width(), photo.height()) == (side, side)


def test_the_output_folder_follows_the_pack(app: gui.App, tmp_path: Path) -> None:
    app.pack_var.set(str(tmp_path / "Wesh.zip"))
    assert app.out_var.get() == str(tmp_path)

    app.pack_var.set(str(tmp_path / "Rachel" / "pack.zip"))
    assert app.out_var.get() == str(tmp_path / "Rachel")


def test_an_output_folder_the_user_chose_is_left_alone(app: gui.App, tmp_path: Path) -> None:
    app.out_var.set(str(tmp_path / "keep"))
    app.pack_var.set(str(tmp_path / "Wesh.zip"))
    assert app.out_var.get() == str(tmp_path / "keep")


def test_a_dialog_opens_where_the_pack_is(app: gui.App, tmp_path: Path, monkeypatch) -> None:
    opened: list[str] = []
    monkeypatch.setattr(gui.Browser, "run", lambda self: opened.append(str(self.current)) or "")

    app._pick_pack_file()
    assert opened[0] == str(Path.home())

    app.pack_var.set(str(tmp_path / "Wesh.zip"))
    app._pick_pack_file()
    assert opened[1] == str(tmp_path)


def test_the_next_dialog_opens_where_the_last_one_ended(app: gui.App, tmp_path: Path, monkeypatch) -> None:
    """The folder a file list was left in is kept in the config file, so a window
    reopened tomorrow starts where the user was looking."""
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    seen: list[str] = []

    def stub(self: gui.Browser) -> str:
        seen.append(str(self.current))
        self.current = elsewhere  # the list was left showing that folder
        return str(elsewhere / "pack.zip")

    monkeypatch.setattr(gui.Browser, "run", stub)

    app._pick_pack_file()
    assert seen == [str(Path.home())]

    app.pack_var.set("")
    app._pick_pack_file()
    assert seen[1] == str(elsewhere)

    reopened = gui.App(gui.make_root())
    try:
        reopened._pick_pack_file()
    finally:
        reopened.root.destroy()
    assert seen[2] == str(elsewhere), "the folder outlived the window that opened it"


def test_a_folder_that_went_away_is_not_reopened(app: gui.App, tmp_path: Path, monkeypatch) -> None:
    gone = tmp_path / "moved-away"
    gone.mkdir()
    app.options["last_dir"] = str(gone)
    gone.rmdir()
    seen: list[str] = []
    monkeypatch.setattr(gui.Browser, "run", lambda self: seen.append(str(self.current)) or "")

    app._pick_pack_file()
    assert seen == [str(Path.home())]


@pytest.fixture()
def browser(app: gui.App, tmp_path: Path) -> gui.Browser:
    (tmp_path / "chapters").mkdir()
    (tmp_path / "pack.zip").write_bytes(b"PK")
    (tmp_path / ".hidden").write_bytes(b"x")
    window = gui.Browser(app, title="test", folder=tmp_path, pick="file")
    try:
        yield window
    finally:
        if window.top.winfo_exists():
            window.top.destroy()


def _rows(window: gui.Browser) -> list[tuple[str, str]]:
    return [tuple(window.tree.item(row, "values")[:2]) for row in window.tree.get_children()]


def test_the_browser_lists_folders_before_files(browser: gui.Browser) -> None:
    assert _rows(browser) == [
        ("chapters", browser.app.t("kind_folder")),
        ("pack.zip", browser.app.t("kind_file")),
    ]


def test_a_filter_brings_the_hidden_ones_back(browser: gui.Browser) -> None:
    browser.filter_var.set("hidden")
    browser._list()
    assert [name for name, _kind in _rows(browser)] == [".hidden"]


def test_the_list_is_a_window_of_its_own_not_three_rows(browser: gui.Browser) -> None:
    """Tk's own dialog drew its list 90 pixels tall; the app asks for a window and
    gives the whole of it to the list."""
    assert browser.WIDTH >= 1000 and browser.HEIGHT >= 600
    assert browser.top.grid_rowconfigure(1)["weight"] == 1
    assert set(browser.tree.grid_info()["sticky"]) == set("nsew")


def test_a_folder_is_walked_into_and_a_file_ends_the_window(browser: gui.Browser, tmp_path: Path) -> None:
    (tmp_path / "chapters" / "part.mp3").write_bytes(b"x")

    browser.tree.selection_set(browser.tree.get_children()[0])
    browser._pick_selected()
    assert browser.current == tmp_path / "chapters"
    assert browser.chosen is None, "walking into a folder is not a choice"

    browser.tree.selection_set(browser.tree.get_children()[0])
    browser._pick_selected()
    assert browser.chosen == str(tmp_path / "chapters" / "part.mp3")
    assert not browser.top.winfo_exists()


def test_the_folder_button_hands_back_the_folder_being_shown(app: gui.App, tmp_path: Path) -> None:
    window = gui.Browser(app, title="test", folder=tmp_path, pick="folder")
    try:
        window._accept()
        assert window.chosen == str(tmp_path)
    finally:
        if window.top.winfo_exists():
            window.top.destroy()


def test_the_player_is_chosen_before_the_cover(app: gui.App) -> None:
    """The player decides the size of every picture, so it is asked before the cover."""
    player_row = int(app.format_box.master.grid_info()["row"])
    cover_row = int(app._labels["cover_label"].grid_info()["row"])
    assert player_row < cover_row


def test_conversion_log_lines_reach_the_window(app: gui.App) -> None:
    handler = gui.attach_log_handler(app)
    try:
        logging.getLogger("daisy2telmi").info("rendering s0")
        app._drain()
    finally:
        logging.getLogger("daisy2telmi").removeHandler(handler)
    assert "INFO: rendering s0" in app.log_view.get("1.0", "end")


def test_a_round_ends_with_its_errors_written_again(app: gui.App, tmp_path, monkeypatch) -> None:
    """The frame closes a round with the errors it made, and starts the next one empty."""

    def convert_with_trouble(*_args, **_kwargs):
        gui.log.error("could not draw m0.png")
        return 1

    handler = gui.attach_log_handler(app)
    monkeypatch.setattr(gui, "convert", convert_with_trouble)
    (tmp_path / "pack.zip").write_bytes(b"PK")
    app.pack_var.set(str(tmp_path / "pack.zip"))
    app.out_var.set(str(tmp_path / "out"))
    app._append("ERROR: an older round's trouble")
    try:
        app._start()
        for _ in range(100):
            app._drain()
            if "REPORT:" in app.log_view.get("1.0", "end"):
                break
            time.sleep(0.05)
    finally:
        logging.getLogger("daisy2telmi").removeHandler(handler)

    shown = app.log_view.get("1.0", "end")
    assert "an older round's trouble" not in shown
    assert shown.count("ERROR: could not draw m0.png") == 2
    assert "REPORT: failed with exit code 1, 1 error(s)" in shown
    assert len(app.log_view.tag_ranges("error")) == 4, "both copies are drawn as errors"


def test_a_converter_that_crashes_still_closes_its_round(app: gui.App, tmp_path, monkeypatch) -> None:
    """A failure the converter does not name used to die in its thread: the bar kept
    turning and the frame stayed silent. It is written in red like any other error."""

    def crash(*_args, **_kwargs):
        raise TypeError("'int' object is not iterable")

    handler = gui.attach_log_handler(app)
    monkeypatch.setattr(gui, "convert", crash)
    (tmp_path / "pack.zip").write_bytes(b"PK")
    app.pack_var.set(str(tmp_path / "pack.zip"))
    app.out_var.set(str(tmp_path / "out"))
    try:
        app._start()
        for _ in range(100):
            app._drain()
            if "REPORT:" in app.log_view.get("1.0", "end"):
                break
            time.sleep(0.05)
    finally:
        logging.getLogger("daisy2telmi").removeHandler(handler)

    shown = app.log_view.get("1.0", "end")
    assert "ERROR: TypeError: 'int' object is not iterable" in shown
    assert "REPORT: failed with exit code 1, 1 error(s)" in shown
    assert app.save_button.instate(["!disabled"]), "the bar stopped turning"


def test_a_clean_round_says_there_was_nothing_to_report(app: gui.App) -> None:
    app._append("INFO: rendering s0")
    app._report(0)
    assert "REPORT: done" in app.log_view.get("1.0", "end")
    assert not app.log_view.tag_ranges("error")


def test_a_missing_pack_is_refused_before_any_work(app: gui.App) -> None:
    app.pack_var.set("/nonexistent/pack.zip")
    app.out_var.set("/tmp/daisy2telmi-never")
    app._start()
    app._drain()
    assert "no such pack" in app.log_view.get("1.0", "end")
    assert not Path("/tmp/daisy2telmi-never").exists()


def test_an_empty_box_is_not_read_as_the_working_directory(app: gui.App, tmp_path) -> None:
    (tmp_path / "pack.zip").write_bytes(b"PK")
    app.pack_var.set(str(tmp_path / "pack.zip"))
    app.out_var.set("")  # the pack would otherwise have filled it in
    app._start()
    app._drain()
    assert "choose an output folder first" in app.log_view.get("1.0", "end")


def test_a_missing_cover_is_refused_before_any_work(app: gui.App, tmp_path) -> None:
    (tmp_path / "pack.zip").write_bytes(b"PK")
    app.pack_var.set(str(tmp_path / "pack.zip"))
    app.out_var.set(str(tmp_path / "out"))
    app.cover_var.set(str(tmp_path / "nope.png"))
    app._start()
    app._drain()
    assert "no such image" in app.log_view.get("1.0", "end")
    assert not (tmp_path / "out").exists()


def test_the_cover_reaches_the_converter(app: gui.App, tmp_path, monkeypatch) -> None:
    seen: list = []
    monkeypatch.setattr(gui, "convert", lambda *args, **kwargs: seen.append(args) or 0)

    cover = tmp_path / "cover.png"
    cover.write_bytes(b"not really a png")
    (tmp_path / "pack.zip").write_bytes(b"PK")
    app.pack_var.set(str(tmp_path / "pack.zip"))
    app.out_var.set(str(tmp_path / "out"))
    app.cover_var.set(str(cover))
    app._start()
    app._drain()

    assert seen[0][4] == cover
    app.cover_var.set("")
    seen.clear()
    app._start()
    app._drain()
    assert seen[0][4] is None


def test_a_dropped_path_becomes_the_pack(app: gui.App) -> None:
    if gui.DND_FILES is None:
        pytest.skip("tkinterdnd2 not installed")

    class Event:
        data = "{/tmp/some pack.zip}"  # tkdnd braces a path containing spaces

    app._on_drop(Event())
    assert app.pack_var.get() == "/tmp/some pack.zip"


def test_the_window_labels_follow_the_language(app: gui.App) -> None:
    app.language_var.set("fr")
    app._relabel()
    assert app._labels["save"].cget("text") == "Enregistrer le zip"
    assert app._labels["overwrite"].cget("text").startswith("Écraser")


def test_the_options_page_saves_what_it_shows(app: gui.App, tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    page = gui.Options(app)
    page.language_box.set("fr - Français")
    page.autoplay_var.set(True)
    page.card_total_var.set(False)
    page._close()

    assert app.options["language"] == "fr"
    assert app.options["autoplay"] is True
    assert app.options["card_total"] is False
    saved = (tmp_path / "daisy2telmi" / "config.json").read_text(encoding="utf-8")
    assert '"language": "fr"' in saved
    assert app._labels["save"].cget("text") == "Enregistrer le zip"


def test_every_option_sits_inside_the_page_frame(app: gui.App) -> None:
    """A box parented to the window instead of its frame is drawn outside the window."""
    page = gui.Options(app)
    frames = page.top.winfo_children()
    assert len(frames) == 1, "the window holds one frame"
    inside, pending = set(), list(frames)
    while pending:
        widget = pending.pop()
        inside.add(widget)
        pending.extend(widget.winfo_children())
    for box in (page.language_box, page.announce_box, page.engine_box, page.voice_box, page.device_box, page.python_box):
        assert box.master in inside, box


def test_the_chatterbox_device_choice_is_saved(app: gui.App, tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    page = gui.Options(app)
    page.device_box.set("mps")
    page._close()

    assert app.options["device"] == "mps"
    saved = (tmp_path / "daisy2telmi" / "config.json").read_text(encoding="utf-8")
    assert '"device": "mps"' in saved


def _settled(app: gui.App, page: gui.Options) -> None:
    """The install runs on its own thread; the page's queue brings the button back."""
    for _ in range(300):
        app.root.update()
        if page.chatterbox_button.instate(["!disabled"]):
            return
        time.sleep(0.01)
    pytest.fail("the install never reported back")


def test_the_options_page_can_install_chatterbox(app: gui.App, monkeypatch) -> None:
    installed: list[str | None] = []
    monkeypatch.setattr(gui.tts, "available", lambda name, python=None: False)
    monkeypatch.setattr(gui.tts, "install_chatterbox", lambda python: installed.append(python))
    page = gui.Options(app)
    page.python_box.insert(0, "/opt/python")
    assert page.chatterbox_state.cget("text") == app.t("chatterbox_missing")

    page._install_chatterbox()
    assert page.chatterbox_button.instate(["disabled"])
    _settled(app, page)
    assert installed == ["/opt/python"]


def test_a_failing_install_is_reported_and_the_button_comes_back(app: gui.App, monkeypatch, caplog) -> None:
    def refuse(_python):
        raise gui.tts.TtsError("no network")

    monkeypatch.setattr(gui.tts, "available", lambda name, python=None: False)
    monkeypatch.setattr(gui.tts, "install_chatterbox", refuse)
    page = gui.Options(app)
    page._install_chatterbox()
    _settled(app, page)
    assert "no network" in caplog.text


def test_a_missing_chatterbox_is_offered_before_the_cards_are_made(app: gui.App, monkeypatch) -> None:
    """Chatterbox is the default voice, but it is not inside the app: the user says
    whether it may be downloaded, and Cancel takes the machine's own voice."""
    app.options["tts"] = "chatterbox"
    monkeypatch.setattr(gui.tts, "available", lambda name, python=None: False)

    monkeypatch.setattr(gui.messagebox, "askokcancel", lambda title, body: True)
    assert app._speech() == ("chatterbox", True)

    monkeypatch.setattr(gui.messagebox, "askokcancel", lambda title, body: False)
    assert app._speech() == (gui.tts.AUTO, False)


def test_an_installed_chatterbox_is_used_without_asking(app: gui.App, monkeypatch) -> None:
    app.options["tts"] = "chatterbox"
    monkeypatch.setattr(gui.tts, "available", lambda name, python=None: True)
    monkeypatch.setattr(
        gui.messagebox, "askokcancel", lambda *args: pytest.fail("asked about an installed engine")
    )
    assert app._speech() == ("chatterbox", False)


def test_the_voice_list_follows_the_engine_and_language(app: gui.App) -> None:
    page = gui.Options(app)
    page.language_box.set("fr - Français")
    espeak = next(e for e in gui.tts.engines() if e.name == "espeak")
    if not espeak.found:
        pytest.skip("no espeak-ng on this machine")
    page.engine_box.set(espeak.label)
    page._fill_voices()
    assert any("French" in name or "fr" in name for name in page.voice_box.cget("values"))


def test_every_card_pattern_is_offered_in_the_language_of_the_window(
    app: gui.App, tmp_path, monkeypatch
) -> None:
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    page = gui.Options(app)
    shown = list(page.announce_box.cget("values"))
    assert shown == [app.t(gui.ANNOUNCE_KEYS[mode]) for mode in gui.ANNOUNCE_MODES]

    page.announce_box.set(shown[gui.ANNOUNCE_MODES.index("number_title")])
    page._close()
    assert app.options["announce"] == "number_title"
    assert app.announce().phrase(1, 3, "Le lapin") == "1. Le lapin"


def test_the_announcement_setting_reaches_the_converter(app: gui.App) -> None:
    app.options.update({"announce": "chapter", "tts": "off"})
    app.language_var.set("de")
    announce = app.announce()
    assert announce.mode == "chapter"
    assert announce.engine == "off"
    assert announce.phrase(2) == "Kapitel 2"


@pytest.fixture()
def three_chapters(app: gui.App, monkeypatch, tmp_path) -> Book:
    """A pack whose chapters are already known, so the window needs no real pack."""
    book = Book(
        "Wesh la fee",
        tuple(
            Chapter(name, (AudioSegment(tmp_path / f"{name}.mp3"),))
            for name in ("Un", "Deux", "Trois")
        ),
    )
    monkeypatch.setattr(gui, "open_pack", lambda path: contextlib.nullcontext(object()))
    monkeypatch.setattr(gui, "extract", lambda pack, level="chapter": book)
    monkeypatch.setattr(gui.audio, "duration", lambda src: 12.5)
    return book


def test_the_cut_levels_wait_until_the_pack_has_been_read(app: gui.App, three_chapters) -> None:
    page = gui.ChapterList(app, Path("/tmp/pack.zip"))
    assert not any(box.instate(["!disabled"]) for box in page.cut_boxes.values())


def test_a_pack_with_one_outline_level_greys_out_the_finer_cuts(
    app: gui.App, three_chapters, monkeypatch
) -> None:
    monkeypatch.setattr(gui, "extract", lambda pack, level="chapter": three_chapters)
    page = gui.ChapterList(app, Path("/tmp/pack.zip"))
    page._measure()
    page._drain()

    assert [box.instate(["!disabled"]) for box in page.cut_boxes.values()] == [True, False, False]


def test_a_deeper_outline_opens_every_level_it_adds_parts_to(
    app: gui.App, three_chapters, monkeypatch, tmp_path
) -> None:
    def by_level(pack, level: str = "chapter"):
        if level == "chapter":
            return three_chapters
        count = 6 if level == "paragraph" else 20
        return Book(
            "Wesh la fee",
            tuple(Chapter(f"p{n}", (AudioSegment(tmp_path / f"s{n}.mp3"),)) for n in range(count)),
        )

    monkeypatch.setattr(gui, "extract", by_level)
    page = gui.ChapterList(app, Path("/tmp/pack.zip"))
    page._measure()
    page._drain()

    assert [box.instate(["!disabled"]) for box in page.cut_boxes.values()] == [True, True, True]


def test_a_saved_level_the_pack_cannot_cut_falls_back(
    app: gui.App, three_chapters, monkeypatch
) -> None:
    app.cut = "sentence"
    monkeypatch.setattr(gui, "extract", lambda pack, level="chapter": three_chapters)
    page = gui.ChapterList(app, Path("/tmp/pack.zip"))
    page._measure()
    page._drain()

    assert page.cut == "chapter"
    assert page.cut_var.get() == "chapter"


def test_the_chapter_window_lists_the_chapters(app: gui.App, three_chapters) -> None:
    page = gui.ChapterList(app, Path("/tmp/pack.zip"))
    page._measure()
    page._drain()

    assert [page.rows.set(iid, "length") for iid in page.rows.get_children()] == ["0:12"] * 3
    assert [box.get() for box in page.keep] == [True, True, True]
    assert page.info[1].title == "Deux"


def test_the_parts_are_drawn_rather_than_built(app: gui.App, three_chapters, monkeypatch) -> None:
    """A book cut to the sentence level has hundreds of parts. One widget per row
    runs X out of pixmaps and kills the app, so the list must stay one widget."""
    many = Book(
        "Wesh la fee",
        tuple(Chapter(f"Phrase {n}", ()) for n in range(1000)),
    )
    monkeypatch.setattr(gui, "extract", lambda pack, level="chapter": many)

    page = gui.ChapterList(app, Path("/tmp/pack.zip"))

    assert len(page.rows.get_children()) == 1000
    assert page.rows.winfo_children() == []


def test_clicking_a_row_s_tick_leaves_it_out(app: gui.App, three_chapters) -> None:
    page = gui.ChapterList(app, Path("/tmp/pack.zip"))
    page._toggle(1)

    assert page.keep[1].get() is False
    assert page.rows.set("1", "keep") == "☐"
    page._close()
    assert app.dropped == {2}


def _playback(app: gui.App, monkeypatch, tmp_path) -> list:
    """Let the chapters window play without ffmpeg or a speaker, and keep what it
    started: the window can only offer Stop for a player it owns."""
    started: list[_Player] = []

    def render(info, chapter, out):
        return tmp_path / f"{info.number}.mp3"

    def open_preview(path):
        player = _Player(path)
        started.append(player)
        return player

    monkeypatch.setattr(gui.chapters, "render_preview", render)
    monkeypatch.setattr(gui.chapters, "open_preview", open_preview)
    return started


def test_listening_to_a_part_says_so_in_its_own_window(
    app: gui.App, three_chapters, monkeypatch, tmp_path
) -> None:
    started = _playback(app, monkeypatch, tmp_path)

    page = gui.ChapterList(app, Path("/tmp/pack.zip"))
    page._play(1)
    assert page.play_button.instate(["disabled"]), "the part is still being rendered"
    page._drain()

    assert [p.path for p in started] == [tmp_path / "2.mp3"]
    assert page.status.cget("text") == app.t("chapters_playing").format(n=2)
    assert page.play_button.cget("text") == app.t("chapters_stop")
    page._close()


def test_the_button_stops_the_part_that_is_playing(
    app: gui.App, three_chapters, monkeypatch, tmp_path
) -> None:
    started = _playback(app, monkeypatch, tmp_path)

    page = gui.ChapterList(app, Path("/tmp/pack.zip"))
    page._play(0)
    page._drain()
    page._play_or_stop()

    assert started[0].stopped is True
    assert page.play_button.cget("text") == app.t("chapters_play")
    assert page.status.cget("text") == ""
    page._close()


def test_a_second_part_stops_the_one_before_it(
    app: gui.App, three_chapters, monkeypatch, tmp_path
) -> None:
    started = _playback(app, monkeypatch, tmp_path)

    page = gui.ChapterList(app, Path("/tmp/pack.zip"))
    page._play(0)
    page._drain()
    page._play(1)
    page._drain()

    assert [p.path for p in started] == [tmp_path / "1.mp3", tmp_path / "2.mp3"]
    assert started[0].stopped is True, "two parts over each other is not what a click means"
    assert started[1].stopped is False
    page._close()


def test_a_part_that_ran_to_its_end_gives_the_button_back(
    app: gui.App, three_chapters, monkeypatch, tmp_path
) -> None:
    started = _playback(app, monkeypatch, tmp_path)

    page = gui.ChapterList(app, Path("/tmp/pack.zip"))
    page._play(0)
    page._drain()
    assert page.play_button.cget("text") == app.t("chapters_stop")

    started[0].ended = True
    page._drain()
    assert page.play_button.cget("text") == app.t("chapters_play")
    page._close()


def test_closing_the_window_stops_the_part_that_was_playing(
    app: gui.App, three_chapters, monkeypatch, tmp_path
) -> None:
    started = _playback(app, monkeypatch, tmp_path)

    page = gui.ChapterList(app, Path("/tmp/pack.zip"))
    page._play(0)
    page._drain()
    page._close()

    assert started[0].stopped is True


class _Player:
    """The part of a player the chapters window uses: is it still playing, and stop it."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self.stopped = False
        self.ended = False

    def poll(self) -> int | None:
        return 0 if self.ended else None

    def terminate(self) -> None:
        self.stopped = True
        self.ended = True

    def wait(self, timeout: float) -> None:
        return None

    def kill(self) -> None:
        raise AssertionError("terminate already worked")


def test_a_part_that_will_not_play_says_why_under_the_cursor(
    app: gui.App, three_chapters, monkeypatch, tmp_path
) -> None:
    def refuse(info, chapter, out):
        raise gui.chapters.ChapterError("no audio for that part")

    monkeypatch.setattr(gui.chapters, "render_preview", refuse)

    page = gui.ChapterList(app, Path("/tmp/pack.zip"))
    page._render(page.info[0], page.book.chapters[0])
    page._drain()

    assert "no audio for that part" in page.status.cget("text")
    assert page.play_button.instate(["!disabled"])


def test_unchecking_a_chapter_leaves_it_out_of_the_story(
    app: gui.App, three_chapters, monkeypatch, tmp_path
) -> None:
    seen: list = []
    monkeypatch.setattr(gui, "convert", lambda *args, **kwargs: seen.append(args) or 0)

    page = gui.ChapterList(app, Path("/tmp/pack.zip"))
    page.keep[1].set(False)
    page._close()
    assert app.dropped == {2}

    (tmp_path / "pack.zip").write_bytes(b"PK")
    app.pack_var.set(str(tmp_path / "pack.zip"))
    app.out_var.set(str(tmp_path / "out"))
    app._start()
    app._drain()
    assert seen[0][5] == "2"


def test_keeping_every_chapter_sends_no_selection(app: gui.App, three_chapters, monkeypatch, tmp_path) -> None:
    seen: list = []
    monkeypatch.setattr(gui, "convert", lambda *args, **kwargs: seen.append(args) or 0)

    gui.ChapterList(app, Path("/tmp/pack.zip"))._close()

    (tmp_path / "pack.zip").write_bytes(b"PK")
    app.pack_var.set(str(tmp_path / "pack.zip"))
    app.out_var.set(str(tmp_path / "out"))
    app._start()
    app._drain()
    assert seen[0][5] is None


def test_choosing_a_cut_level_redraws_the_parts(
    app: gui.App, three_chapters, monkeypatch, tmp_path
) -> None:
    """Part numbers from one level mean nothing at another, so the list is rebuilt."""
    sentences = Book(
        "Wesh la fee",
        tuple(Chapter(f"Phrase {n}", (AudioSegment(tmp_path / f"s{n}.mp3"),)) for n in (1, 2)),
    )
    monkeypatch.setattr(
        gui, "extract", lambda pack, level="chapter": three_chapters if level == "chapter" else sentences
    )

    page = gui.ChapterList(app, Path("/tmp/pack.zip"))
    assert [box.get() for box in page.keep] == [True, True, True]
    page.keep[0].set(False)

    page._choose("sentence")
    assert [box.get() for box in page.keep] == [True, True]
    assert page.info[1].title == "Phrase 2"
    assert app.dropped == set()
    assert app.cut == "sentence"


def test_the_chosen_cut_level_reaches_the_conversion(
    app: gui.App, three_chapters, monkeypatch, tmp_path
) -> None:
    seen: list = []
    monkeypatch.setattr(gui, "convert", lambda *args, **kwargs: seen.append(args) or 0)
    monkeypatch.setattr(gui.settings, "save", lambda options: None)

    page = gui.ChapterList(app, Path("/tmp/pack.zip"))
    page._choose("paragraph")
    page._close()
    assert app.options["cut"] == "paragraph"

    (tmp_path / "pack.zip").write_bytes(b"PK")
    app.pack_var.set(str(tmp_path / "pack.zip"))
    app.out_var.set(str(tmp_path / "out"))
    app._start()
    app._drain()
    assert seen[0][6] == "paragraph"


def test_the_chosen_crop_reaches_the_conversion(app: gui.App, monkeypatch, tmp_path) -> None:
    seen: list = []
    monkeypatch.setattr(gui, "convert", lambda *args, **kwargs: seen.append(args) or 0)
    monkeypatch.setattr(gui.settings, "save", lambda options: None)

    app.crop_var.set("top")
    (tmp_path / "pack.zip").write_bytes(b"PK")
    app.pack_var.set(str(tmp_path / "pack.zip"))
    app.out_var.set(str(tmp_path / "out"))
    app._start()
    app._drain()
    assert seen[0][8] == "top"


def test_the_chosen_format_reaches_the_conversion(app: gui.App, monkeypatch, tmp_path) -> None:
    seen: list = []
    monkeypatch.setattr(gui, "convert", lambda *args, **kwargs: seen.append(args) or 0)
    monkeypatch.setattr(gui.settings, "save", lambda options: None)

    app.format_var.set("lunii")
    (tmp_path / "pack.zip").write_bytes(b"PK")
    app.pack_var.set(str(tmp_path / "pack.zip"))
    app.out_var.set(str(tmp_path / "out"))
    app._start()
    app._drain()
    assert seen[0][7] == "lunii"


def test_the_window_offers_every_player(app: gui.App) -> None:
    assert app.format_box["values"] == tuple(
        [
            "Telmi",
            "Lunii (B/W)",
            "Commercial PK",
            "Flam",
            "Merlin (beta)",
        ]
    )


def test_the_negative_is_offered_for_the_grey_screen_only(app: gui.App) -> None:
    assert app.negative_box.instate(["disabled"]), "a colour player has no negative"
    app.format_var.set("lunii")
    app._show_negative()
    assert app.negative_box.instate(["!disabled"]), "the B/W Lunii can be read the other way round"
    app.format_var.set("lunii-pk")
    app._show_negative()
    assert app.negative_box.instate(["disabled"]), "a Commercial PK keeps its colours"


def test_the_negative_reaches_the_conversion(app: gui.App, tmp_path, monkeypatch) -> None:
    seen: list = []
    monkeypatch.setattr(gui, "convert", lambda *args, **kwargs: seen.append((args, kwargs)) or 0)
    monkeypatch.setattr(gui.settings, "save", lambda options: None)

    app.format_var.set("lunii")
    app.negative_var.set(True)
    (tmp_path / "pack.zip").write_bytes(b"PK")
    app.pack_var.set(str(tmp_path / "pack.zip"))
    app.out_var.set(str(tmp_path / "out"))
    app._start()
    app._drain()
    assert seen[0][1]["negative"] is True


@pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg not installed")
def test_a_cover_is_drawn_under_the_crop_frame(app: gui.App, tmp_path) -> None:
    cover = tmp_path / "cover.png"
    cover.write_bytes(images.placeholder_png(300, 900))
    app.cover_var.set(str(cover))
    assert app.crop_frame.itemcget(app._crop_item, "image"), "the frame carries the cover"
    # A 300x900 cover under a 720x540 frame is drawn 720x2160 to fill it, so the
    # frame shows 540 of its 2160 lines; centred, it starts 810 lines in.
    assert tuple(app.crop_frame.coords(app._crop_item)) == (0, -810), "the frame sits in the middle of the cover"


@pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg not installed")
def test_dragging_the_cover_moves_the_frame_it_crops(app: gui.App, tmp_path) -> None:
    cover = tmp_path / "cover.png"
    cover.write_bytes(images.placeholder_png(300, 900))
    app.cover_var.set(str(cover))
    before = app.crop_frame.coords(app._crop_item)

    app._move_crop(0, 100)

    after = app.crop_frame.coords(app._crop_item)
    assert after[1] > before[1], "the cover slides the way the pointer went"
    assert app.crop_var.get() == "", "a moved frame answers to no band"


@pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg not installed")
def test_a_moved_frame_goes_back_to_the_band_it_is_clicked_onto(app: gui.App, tmp_path) -> None:
    cover = tmp_path / "cover.png"
    cover.write_bytes(images.placeholder_png(300, 900))
    app.cover_var.set(str(cover))
    app._move_crop(0, 100)

    app.crop_var.set("bottom")
    app._pick_crop()

    assert app.crop_frame.coords(app._crop_item)[1] == -1620, "the frame sits on the band's edge"


def test_the_frame_is_drawn_before_a_cover_exists(app: gui.App) -> None:
    """The frame is the chosen player's screen: its shape is what a cover gets
    fitted to, so it is on screen from the start and not only once a cover is picked."""
    width = int(app.crop_frame.cget("width"))
    height = int(app.crop_frame.cget("height"))
    assert (width, height) == images.preview_size(app.format_var.get(), gui.CROP_WIDTH)
    assert app.crop_frame.cget("background") == images.band_colour("colour")
    assert not app.crop_frame.itemcget(app._crop_item, "image"), "no cover, so no picture"

    app.format_var.set("lunii")
    app._pick_format()
    assert (int(app.crop_frame.cget("width")), int(app.crop_frame.cget("height"))) == images.preview_size(
        "lunii", gui.CROP_WIDTH
    )
    assert app.crop_frame.cget("background") == images.band_colour("bw")


def test_the_zoom_and_a_moved_frame_reach_the_conversion(app: gui.App, monkeypatch, tmp_path) -> None:
    seen: list = []
    monkeypatch.setattr(gui, "convert", lambda *args, **kwargs: seen.append((args, kwargs)) or 0)
    monkeypatch.setattr(gui.settings, "save", lambda options: None)

    app._crop_zoom_value = 2.0
    app._crop_focus = (0.2, 0.8)
    (tmp_path / "pack.zip").write_bytes(b"PK")
    app.pack_var.set(str(tmp_path / "pack.zip"))
    app.out_var.set(str(tmp_path / "out"))
    app._start()
    app._drain()
    assert seen[0][1]["zoom"] == 2.0
    assert seen[0][1]["focus"] == (0.2, 0.8)


def test_a_pack_the_window_cannot_open_is_reported(app: gui.App, monkeypatch) -> None:
    def refuse(path: Path):
        raise DaisyPackError("no DAISY pack in that folder")

    monkeypatch.setattr(gui, "open_pack", refuse)
    gui.ChapterList(app, Path("/tmp/pack.zip"))
    assert "no DAISY pack" in app.log_view.get("1.0", "end")


def test_the_chapters_button_needs_a_pack_first(app: gui.App) -> None:
    app._open_chapters()
    assert "choose a DAISY pack first" in app.log_view.get("1.0", "end")


def _story_folder(root: Path) -> Path:
    """A story folder as a run leaves one, with a picture Tk can actually decode."""
    (root / "images").mkdir(parents=True)
    (root / "audios").mkdir()
    (root / "images" / "m0.png").write_bytes(images.placeholder_png(320, 240, style="colour"))
    (root / "audios" / "c0.mp3").write_bytes(b"ID3")
    (root / "nodes.json").write_text(
        json.dumps(
            {
                "startAction": {"action": "m", "index": 0},
                "stages": {
                    "m0": {
                        "image": "m0.png",
                        "audio": None,
                        "ok": {"action": "s0", "index": 0},
                        "home": None,
                        "control": {"ok": True, "home": True, "autoplay": False},
                    },
                    "s0": {
                        "image": None,
                        "audio": "c0.mp3",
                        "ok": None,
                        "home": {"action": "m", "index": 0},
                        "control": {"ok": False, "home": True, "autoplay": True},
                    },
                },
                "actions": {"m": [{"stage": "m0"}], "s0": [{"stage": "s0"}]},
            }
        ),
        encoding="utf-8",
    )
    (root / "metadata.json").write_text(json.dumps({"title": "The book"}), encoding="utf-8")
    return root


def test_the_emulator_window_walks_the_story_it_is_shown(
    app: gui.App, tmp_path: Path, monkeypatch
) -> None:
    """The window reads the folder the last run left and answers with the machine's
    own buttons: OK moves on, Home goes back, and a stage with no picture of its own
    shows nothing."""
    started: list = []
    monkeypatch.setattr(gui.chapters, "open_preview", lambda path: started.append(path))
    window = gui.Emulator(app, _story_folder(tmp_path / "the book"))
    try:
        assert window.current == "m0"
        assert window.canvas.find_all(), "the chapter card is drawn"
        assert window.keys["ok"].instate(["!disabled"])

        window._press("ok")
        assert window.current == "s0"
        assert not window.canvas.find_all(), "the chapter itself has no picture"
        assert [clip.name for clip in started] == ["c0.mp3"]

        window._press("home")
        assert window.current == "m0"
    finally:
        window._close()


def test_the_emulator_buttons_are_words_not_keys(app: gui.App, tmp_path: Path) -> None:
    """The buttons are the machine's, so they carry the machine's words: a window
    that reads `emu_ok` is a label that never went through the translation."""
    window = gui.Emulator(app, _story_folder(tmp_path / "the book"))
    try:
        assert [window.keys[key].cget("text") for key in ("up", "ok", "home", "stop")] == [
            "▲",
            "OK",
            "Home",
            "Stop",
        ]
    finally:
        window._close()


def test_the_emulator_button_opens_the_newest_story(app: gui.App, tmp_path: Path) -> None:
    """The output folder keeps every story written to it; the one just written is
    the one the button plays, without asking."""
    out = tmp_path / "out"
    older, newer = _story_folder(out / "older"), _story_folder(out / "newer")
    stamp = time.time()
    os.utime(older, (stamp - 60, stamp - 60))
    os.utime(newer, (stamp, stamp))

    app.out_var.set(str(out))
    app._open_emulator()
    assert app.emulator.story.folder == newer
    app.emulator._close()


def test_the_emulator_asks_for_a_folder_when_none_was_written(
    app: gui.App, tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.setattr(gui.Browser, "run", lambda self: str(_story_folder(tmp_path / "picked")))
    app.out_var.set(str(tmp_path / "nothing written here"))
    app._open_emulator()
    assert app.emulator.story.title == "The book"
    app.emulator._close()


def test_a_folder_that_holds_no_story_is_reported(app: gui.App, tmp_path: Path) -> None:
    """A wrong folder has to be named in the log: a window that opens empty looks
    like a story with nothing in it."""
    (tmp_path / "not a story").mkdir()
    gui.Emulator(app, tmp_path / "not a story")
    assert "no story graph" in app.log_view.get("1.0", "end")


def test_the_emulator_opens_the_lunii_archive_not_the_colour_folder(
    app: gui.App, tmp_path: Path
) -> None:
    """A Lunii run leaves two stories in the output folder: the colour one in a
    folder, and the archive holding the sixteen greys that machine's screen shows.
    The archive is written last, so it is the one the button plays."""
    out = tmp_path / "out"
    _story_folder(out / "the book")
    archive = out / "the book-lunii.zip"
    with zipfile.ZipFile(archive, "w") as packed:
        packed.writestr(
            "story.json",
            json.dumps(
                {
                    "title": "The book",
                    "stageNodes": [
                        {
                            "uuid": "cover",
                            "image": "a1.png",
                            "okTransition": None,
                            "homeTransition": None,
                            "controlSettings": {"ok": True, "home": False, "wheel": True},
                            "squareOne": True,
                        }
                    ],
                    "actionNodes": [],
                }
            ),
        )
        packed.writestr("assets/a1.png", images.placeholder_png(320, 240, style="bw"))

    app.out_var.set(str(out))
    app._open_emulator()
    try:
        assert app.emulator.story.machine == "lunii"
        card = app.emulator.story.stages[app.emulator.story.start]
        assert card.image.parts[-2:] == ("assets", "a1.png")
        assert card.image.is_file(), "the archive was let out so its cards can be drawn"
    finally:
        app.emulator._close()
