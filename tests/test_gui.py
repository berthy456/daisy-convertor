"""The Tkinter front end. Skipped where no display or no Tk is available."""

from __future__ import annotations

import contextlib
import logging
import shutil
import time
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
    opened: list[dict] = []
    monkeypatch.setattr(gui.filedialog, "askopenfilename", lambda **kw: opened.append(kw) or "")

    app._pick_pack_file()
    assert opened[0]["initialdir"] == str(Path.home())

    app.pack_var.set(str(tmp_path / "Wesh.zip"))
    app._pick_pack_file()
    assert opened[1]["initialdir"] == str(tmp_path)


def test_the_file_dialog_list_is_given_room(app: gui.App) -> None:
    """Tk draws that list 90 pixels tall: three rows, which reads as unscrollable."""
    icons = tk.Frame(app.root, name="icons")
    listing = tk.Canvas(icons, name="canvas")
    assert listing.winfo_reqheight() > 200


def test_the_player_is_chosen_before_the_cover(app: gui.App) -> None:
    """The player decides the size of every picture, so it is asked before the cover."""
    player_row = int(app._labels["fmt_telmi"].master.grid_info()["row"])
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
    for box in (page.language_box, page.announce_box, page.engine_box, page.voice_box, page.device_box, page.python_box):
        assert box.master is frames[0], box


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
    assert [app.t(f"fmt_{player}") for player in gui.FORMATS] == [
        "Telmi",
        "Lunii",
        "Flam",
        "Merlin (beta)",
    ]


@pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg not installed")
def test_a_cover_is_drawn_under_the_crop_frame(app: gui.App, tmp_path) -> None:
    cover = tmp_path / "cover.png"
    cover.write_bytes(images.placeholder_png(300, 900))
    app.cover_var.set(str(cover))
    assert app.crop_frame.itemcget(app._crop_item, "image"), "the frame carries the cover"
    # A 300x900 cover under a 360x270 frame is drawn 360x1080 to fill it, so the
    # frame shows 270 of its 1080 lines; centred, it starts 405 lines in.
    assert tuple(app.crop_frame.coords(app._crop_item)) == (0, -405), "the frame sits in the middle of the cover"


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

    assert app.crop_frame.coords(app._crop_item)[1] == -810, "the frame sits on the band's edge"


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
