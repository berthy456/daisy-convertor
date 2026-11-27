"""The Tkinter front end. Skipped where no display or no Tk is available."""

from __future__ import annotations

import contextlib
import json
import logging
import os
import shutil
import threading
import time
import zipfile
from pathlib import Path

import pytest

tk = pytest.importorskip("tkinter")

from tkinter import ttk

from daisy2telmi import covers, gui, images, settings
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
    instance.options["tts"] = "off"  # a window test must not stop on an engine install
    try:
        yield instance
    finally:
        # A test that closes the window leaves nothing to destroy.
        with contextlib.suppress(tk.TclError):
            root.destroy()


def test_the_window_builds(app: gui.App) -> None:
    assert app.save_button.instate(["!disabled"])


def test_the_window_is_given_the_app_icon(app: gui.App) -> None:
    """The PNG is committed as package data and unpacked next to the module in a
    frozen binary, so the window can always draw it."""
    assert gui.ICON.is_file()
    assert gui.ICON.read_bytes()[:8] == b"\x89PNG\r\n\x1a\n"
    assert app.root._icon is not None


def test_the_work_and_the_picked_row_share_one_blue(app: gui.App) -> None:
    """The progress bar, the button that starts the work and what the window marks as
    picked share one colour, so the eye finds them by the same blue."""
    style = ttk.Style(app.root)
    assert style.lookup("TProgressbar", "background") == gui.NAVY
    assert app.save_button.cget("style") == "Work.TButton"
    assert style.lookup("Work.TButton", "background") == gui.NAVY
    assert style.map("Treeview", "background") == [("selected", gui.NAVY)], "a picked row"
    assert app.log_view.cget("selectbackground") == gui.NAVY


def test_each_mark_is_its_own_file(app: gui.App) -> None:
    """Tk cannot scale a photo image, so a size the window asks for has to be
    shipped as its own PNG; a missing one draws nothing at all."""
    for side in (gui.BAR_MARK, 16):
        photo = app._mark_photo(side)
        assert photo is not None, f"no {side}-pixel mark"
        assert (photo.width(), photo.height()) == (side, side)


def _bar_items(app: gui.App, kind: str) -> list[int]:
    return [item for item in app.bar.find_all() if app.bar.type(item) == kind]


def _walk(widget: tk.Widget) -> list[tk.Widget]:
    """Every widget under `widget`, the window itself included."""
    seen, pending = [], [widget]
    while pending:
        item = pending.pop()
        seen.append(item)
        pending += item.winfo_children()
    return seen


def _bar_boxes(app: gui.App) -> dict[str, float]:
    """The left edge of each control the bar drew, by its name."""
    boxes = {}
    for item in _bar_items(app, "polygon"):
        for tag in app.bar.gettags(item):
            if tag.startswith("bar-"):
                boxes[tag[4:]] = app.bar.coords(item)[0]
    return boxes


def _frameless(app: gui.App, monkeypatch) -> None:
    """Ask for the one window that has no system bar, the way macOS gets it."""
    monkeypatch.setattr(gui, "system_bar_kept", lambda: False)
    app.set_frameless(True)


def _controls(app: gui.App) -> list[str]:
    """The controls the bar draws here, left to right. Nothing where the window has its
    own system bar."""
    return app.bar_controls()


def test_the_window_draws_its_own_bar(app: gui.App) -> None:
    """A rounded card, the mark, the name with the version beside it, and the controls
    only where the window has no system bar of its own."""
    app.root.update()
    bar = app.bar
    kinds = [bar.type(item) for item in bar.find_all()]
    assert kinds.count("polygon") == 1 + len(_controls(app)), "the card and its control boxes"
    assert kinds.count("image") == 1, "the mark"
    card = next(item for item in _bar_items(app, "polygon") if not bar.gettags(item))
    assert bar.itemcget(card, "smooth") == "true", "the card is rounded, not squared"
    assert {bar.itemcget(item, "text") for item in _bar_items(app, "text")} == {
        app.t("app_title"),
        gui.__version__,
    }


def test_the_title_block_stands_at_the_top_left(app: gui.App) -> None:
    """The window's title is one block at the corner a title belongs to, and the pick
    buttons are at the other end of the same line - not a band across the bottom."""
    app.root.geometry("1300x700")
    app.root.update()
    assert app.bar.winfo_width() <= gui.BAR_WIDTH
    assert app.bar.winfo_height() <= gui.BAR_HEIGHT
    assert app.bar.winfo_rootx() - app.root.winfo_rootx() < 40, "the block stands at the window's left edge"
    picks = app._labels["browse_zip"].master
    assert picks.winfo_rootx() >= app.bar.winfo_rootx() + app.bar.winfo_width(), "the buttons to its right"
    assert picks.winfo_rootx() + picks.winfo_width() > app.root.winfo_width() - 40, "at the window's right edge"


def test_the_headline_stands_under_the_name(app: gui.App) -> None:
    """The window says what it is for once, on the line under its name."""
    app.root.update()
    bar = app.bar
    assert app._labels["app_headline"] is bar.headline
    title = bar.bbox(bar._words["title"])
    assert bar.headline.winfo_y() > title[3], "the description is the line under the name"
    card = next(item for item in _bar_items(app, "polygon") if not bar.gettags(item))
    _, _, _, bottom = bar.bbox(card)
    assert bar.headline.winfo_y() + bar.headline.winfo_height() <= bottom, "and inside the card"
    said = [w for w in _walk(app.root) if isinstance(w, ttk.Label) and w.cget("text") == app.t("app_headline")]
    assert len(said) == 1, "said once, not again at the bottom of the window"


def test_the_bar_card_holds_its_mark(app: gui.App) -> None:
    """The mark is the biggest thing in the bar, so the card has to be taller than it:
    a mark spilling past the card's rounded edge reads as a drawing error."""
    app.root.update()
    bar = app.bar
    card = next(item for item in _bar_items(app, "polygon") if not bar.gettags(item))
    mark = next(item for item in _bar_items(app, "image"))
    _, top, _, bottom = bar.bbox(mark)
    _, card_top, _, card_bottom = bar.bbox(card)
    assert card_top <= top and bottom <= card_bottom, "the mark stands inside the card"


def test_the_version_sits_beside_the_name_it_versions(app: gui.App) -> None:
    app.root.update()
    bar = app.bar
    items = {bar.itemcget(item, "text"): item for item in _bar_items(app, "text")}
    name = bar.coords(items[app.t("app_title")])
    version = bar.coords(items[gui.__version__])
    assert version[0] > name[0], "the version follows the name"
    assert version[1] == name[1], "on the name's own line"


def test_the_bar_controls_stand_at_its_right_edge(app: gui.App, monkeypatch) -> None:
    _frameless(app, monkeypatch)
    try:
        app.root.update()
        boxes = _bar_boxes(app)
        assert sorted(boxes, key=lambda key: boxes[key]) == _controls(app), "the controls, left to right"
        assert boxes["close"] + gui.BAR_BUTTON < app.bar.winfo_width()
    finally:
        app.set_frameless(False)


def test_the_title_block_stays_the_same_card_when_the_window_widens(app: gui.App, monkeypatch) -> None:
    """The block is a card of its own, not a band stretched over the window: its controls
    stay where the card ends and the pick buttons are what moves."""
    _frameless(app, monkeypatch)
    try:
        app.root.geometry("1200x600")
        app.root.update()
        close = _bar_boxes(app)["close"]
        width = app.bar.winfo_width()
        app.root.geometry("1300x600")
        app.root.update()
        assert _bar_boxes(app)["close"] == close, "the controls stay at the card's right edge"
        assert app.bar.winfo_width() == width, "and the card keeps its own width"
        picks = app._labels["browse_zip"].master
        assert picks.winfo_rootx() > app.bar.winfo_rootx() + app.bar.winfo_width(), "the buttons move with the edge"
    finally:
        app.set_frameless(False)


def test_the_bar_enlarges_the_window_and_puts_it_back(app: gui.App) -> None:
    app.root.geometry("700x600")
    app.root.update()
    before = app.root.geometry()
    app.bar_action("zoom")
    app.root.update()
    assert app.root.geometry() != before
    app.bar_action("zoom")
    app.root.update()
    assert app.root.geometry() == before


def test_the_bar_hides_the_window(app: gui.App) -> None:
    app.root.update()
    app.bar_action("hide")
    assert app.root.state() in ("iconic", "withdrawn")


def test_the_bar_closes_the_window(app: gui.App) -> None:
    app.bar_action("close")
    try:
        exists = bool(app.root.winfo_exists())
    except tk.TclError:  # the window is gone, which is the point
        exists = False
    assert not exists


def test_the_bar_follows_the_language(app: gui.App) -> None:
    english = app.t("app_headline")
    app.language_var.set("fr")
    app._relabel()
    texts = {app.bar.itemcget(item, "text") for item in _bar_items(app, "text")}
    assert app.t("app_title") in texts
    assert app._labels["app_headline"].cget("text") == app.t("app_headline")
    assert app._labels["app_headline"].cget("text") != english


def test_the_window_keeps_the_system_bar_it_is_given(app: gui.App) -> None:
    """Tk's only way to lose the title bar is `overrideredirect`, which hands the window
    back to the app: the taskbar entry, the resize edges and the minimise go with it,
    and so does the window manager that places, sizes and repaints the window. The bar
    the app draws is not worth that, so the window keeps its own."""
    app.root.update()
    assert app.frameless is False
    assert not app.root.overrideredirect()


def test_the_band_draws_no_buttons_over_the_system_bar(app: gui.App) -> None:
    """The system bar has the window's buttons. Drawing them again in the band gives the
    window two sets of them, so the band keeps its mark, its name and its description."""
    app.root.update()
    assert app.bar_controls() == []
    assert _bar_boxes(app) == {}
    assert len(_bar_items(app, "polygon")) == 1, "the card, and nothing else"


def test_a_frameless_window_draws_the_controls_it_replaced(app: gui.App, monkeypatch) -> None:
    """macOS is the one system where Tk hides the bar and leaves the window managed, so
    it is the one system with a frameless window - and a frameless window needs the
    buttons the bar it replaced was drawing."""
    monkeypatch.setattr(gui, "system_bar_kept", lambda: False)
    app.set_frameless(True)
    try:
        assert app.frameless is True
        boxes = set(_bar_boxes(app))
        assert {"hide", "zoom", "close"} <= boxes
    finally:
        app.set_frameless(False)


def test_the_options_page_never_asks_about_the_system_bar(app: gui.App, monkeypatch) -> None:
    """The bar is what the system allows rather than a preference: Tk loses it only by
    handing the window back to the app, so there is nothing to ask either way."""
    for kept in (True, False):
        monkeypatch.setattr(gui, "system_bar_kept", lambda value=kept: value)
        page = gui.Options(app)
        try:
            boxes = {item.cget("text") for item in page.grid.grid_slaves() if isinstance(item, ttk.Checkbutton)}
            assert app.t("opt_autoplay") in boxes
            assert "frameless" not in " ".join(boxes).lower(), "the page does not ask about the bar"
        finally:
            page.top.destroy()


def test_the_window_opens_at_a_size_of_its_own(app: gui.App) -> None:
    """Tk leaves the size to the window manager, and a window left untold came up a
    small square."""
    app.root.update()
    assert app.root.winfo_width() >= min(gui.MIN_WIDTH, app.root.winfo_screenwidth())
    assert app.root.winfo_height() >= min(gui.MIN_HEIGHT, app.root.winfo_screenheight())


def test_the_window_is_as_big_as_its_widgets_ask_for(app: gui.App) -> None:
    """The manager answers a size request with a window of its own - its bar, a panel
    along the edge - and Tk lays the widgets out in the room the window was given, which
    cuts the column on the right. The window asks again until it has the room."""
    app.root.update()
    room = (
        app.root.winfo_screenwidth() - 2 * gui.WINDOW_MARGIN,
        app.root.winfo_screenheight() - 2 * gui.WINDOW_MARGIN,
    )
    assert app.root.winfo_width() >= min(app.root.winfo_reqwidth(), room[0])
    assert app.root.winfo_height() >= min(app.root.winfo_reqheight(), room[1])


def test_a_pack_does_not_choose_where_its_story_goes(app: gui.App, monkeypatch) -> None:
    """Nothing is written next to a book on its own: with nothing picked the line says
    the story would go to the home folder."""
    monkeypatch.setattr(gui.Path, "home", lambda: Path("/home"))
    app.pack_var.set("/books/Rachel/pack.zip")
    assert app.out_var.get() == ""
    assert app._paths_line.cget("text") == "/books/Rachel/pack.zip → /home"

    app.out_var.set("/stories")
    assert app._paths_line.cget("text") == "/books/Rachel/pack.zip → /stories"

    app.pack_var.set("/" + "deep" * 20 + "/pack.zip")
    line = app._paths_line.cget("text")
    assert line.endswith("pack.zip → /stories"), "a long path loses its front, not its end"
    assert len(line) <= gui.PATH_READ + 1 + 3 + len("/stories"), "and the line does not widen the window"


def test_the_book_is_picked_with_buttons(app: gui.App) -> None:
    """Two buttons, no boxes: a pack is dropped on the window or picked from a list."""
    picked = [app._labels[key] for key in ("browse_zip", "browse_folder")]
    assert all(isinstance(button, ttk.Button) for button in picked)
    row = picked[0].master
    assert all(button.master is row for button in picked)
    assert len({button.winfo_y() for button in picked}) == 1, "one line, not a column"
    assert row.winfo_rootx() > app.bar.winfo_rootx(), "the buttons stand right of the title block"
    assert row.winfo_rooty() < app.bar.winfo_rooty() + app.bar.winfo_height()

    assert not any(isinstance(widget, ttk.Entry) for widget in _walk(app.bar.master)), "no path is typed"


def test_each_pick_button_carries_the_mark_of_what_it_picks(app: gui.App) -> None:
    """The book and a folder of them: two marks, so the two buttons are read without their words."""
    asked = zip(("browse_zip", "browse_folder"), gui.PICK_MARKS)
    for key, glyph in asked:
        mark = app._pick_marks[glyph]
        assert (mark.width(), mark.height()) == (gui.PICK_MARK, gui.PICK_MARK)
        assert app._labels[key].cget("image") == (str(mark),), f"{key} carries the {glyph} mark"


def test_the_output_folder_is_set_with_the_other_options(app: gui.App, tmp_path: Path, monkeypatch) -> None:
    stories = tmp_path / "stories"
    stories.mkdir()
    monkeypatch.setattr(app, "_browse", lambda *_: str(stories))
    page = gui.Options(app)
    _probed(page)
    page._pick_folder(page.output_box)
    page._close()

    assert app.out_var.get() == str(stories)
    reopened = gui.App(gui.make_root(), options=settings.load())
    try:
        assert reopened.out_var.get() == str(stories)
    finally:
        reopened.root.destroy()


def test_an_output_folder_that_was_never_picked_is_home(app: gui.App, tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(gui.Path, "home", lambda: tmp_path)
    app.out_var.set("")
    assert app._out_dir() == tmp_path


def test_the_options_page_can_point_the_output_at_telmi_sync(app: gui.App, tmp_path, monkeypatch) -> None:
    """Telmi-Sync reads ~/.telmi/stories each time it starts, so a story written there
    is on its shelf without an import."""
    stories = tmp_path / ".telmi" / "stories"
    stories.mkdir(parents=True)
    monkeypatch.setattr(gui.Path, "home", lambda: tmp_path)
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))

    page = gui.Options(app)
    _probed(page)
    page._telmi_stories()
    page._close()

    assert app.out_var.get() == str(stories)
    assert app.options["output_dir"] == str(stories)
    written = json.loads((tmp_path / "config" / "daisy2telmi" / "config.json").read_text(encoding="utf-8"))
    assert written["output_dir"] == str(stories)


def test_the_options_page_says_when_telmi_sync_never_ran_here(app: gui.App, tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(gui.Path, "home", lambda: tmp_path)
    page = gui.Options(app)
    _probed(page)
    page._telmi_stories()

    assert "no Telmi-Sync stories folder" in app.log_view.get("1.0", "end")
    assert page.output_box.get() == ""
    assert not (tmp_path / ".telmi").exists(), "the folder is Telmi-Sync's to make"
    page.top.destroy()


def test_a_second_pack_loses_the_cover_the_app_found(app: gui.App, tmp_path: Path, monkeypatch) -> None:
    """A cover, an author and a title the services found were found for one book, so
    the next pack gets its own. Everything else the window holds carries over."""
    monkeypatch.setattr(app, "_cover_lookup", lambda *args: None)
    app.options["cut"] = "paragraph"
    app.format_var.set("flam")

    first = tmp_path / "wesh.zip"
    first.write_bytes(b"PK")
    app.pack_var.set(str(first))
    app._find_cover()
    app._took_cover(app._cover_job, str(tmp_path / "cover-1.jpg"), "Wesh", "Rachel Corenblit")
    assert app.cover_var.get() == str(tmp_path / "cover-1.jpg")
    assert (app.title_var.get(), app.author_var.get()) == ("Wesh", "Rachel Corenblit")

    second = tmp_path / "aiguille.zip"
    second.write_bytes(b"PK")
    app.pack_var.set(str(second))
    app._find_cover()
    assert app.cover_var.get() == "", "the cover found for the first pack does not follow it"
    assert (app.title_var.get(), app.author_var.get()) == ("", ""), "nor the words it filled the boxes with"
    assert app.options["cut"] == "paragraph" and app.format_var.get() == "flam", "the chosen settings stayed"

    mine = tmp_path / "mine.png"
    mine.write_bytes(images.placeholder_png(300, 900))
    app.cover_var.set(str(mine))
    app.author_var.set("Corenblit")
    app._find_cover()
    assert app.cover_var.get() == str(mine), "a cover the user picked is kept"
    assert app.author_var.get() == "Corenblit", "words the user typed are kept"


def test_a_cover_lookup_says_it_is_searching_and_can_be_called_off(app: gui.App, tmp_path: Path, monkeypatch) -> None:
    """Four services are asked before the answer says there is nothing, so the window
    says it is working - and gives the way to call it off."""
    monkeypatch.setattr(app, "_cover_lookup", lambda *args: None)
    (tmp_path / "aiguille.zip").write_bytes(b"PK")
    app.pack_var.set(str(tmp_path / "aiguille.zip"))
    app._find_cover()
    assert app.cover_note.winfo_manager() == "pack", "the window says it is searching"
    assert app.cover_stop.winfo_manager() == "pack", "and offers the way to stop"

    job = app._cover_job
    app._stop_cover()
    assert app.cover_note.winfo_manager() == "", "the waiting goes away with the stop"
    assert app.cover_stop.winfo_manager() == ""
    assert app.t("cover_stopped") in app.log_view.get("1.0", "end")

    app._took_cover(job, str(tmp_path / "late.jpg"), "L'Aiguille creuse", "")
    assert app.cover_var.get() == "", "the answer a stopped lookup posts is an old one"


def test_the_words_typed_are_asked_of_the_catalogues(app: gui.App, tmp_path: Path, monkeypatch) -> None:
    """The pack is named after its folder. Typing the book's real title and author and
    asking here asks the same services the pack's own metadata is asked of, and takes
    their answer into the Image box."""
    asked: list[tuple[str, str]] = []

    def find(words, dest: Path) -> Path:
        asked.append((words.title, words.author))
        dest.write_bytes(b"\xff\xd8\xff")
        return dest

    monkeypatch.setattr(covers, "find", find)
    app.title_var.set("Une vie")
    app.author_var.set("Guy de Maupassant")
    app._search_words()
    for _ in range(100):
        app._drain()
        if app.cover_var.get():
            break
        time.sleep(0.05)

    assert asked == [("Une vie", "Guy de Maupassant")]
    assert Path(app.cover_var.get()).read_bytes().startswith(b"\xff\xd8\xff")
    assert app.cover_note.winfo_manager() == "", "the waiting goes away with the answer"


def test_a_search_the_user_asked_replaces_the_cover_the_app_had_found(app: gui.App, tmp_path: Path, monkeypatch) -> None:
    """The Image box holds a cover the app suggested from the pack's own words. A
    search the user asked for is not a suggestion, so it takes the box over."""
    monkeypatch.setattr(app, "_cover_lookup", lambda *args: None)
    (tmp_path / "pack.zip").write_bytes(b"PK")
    app.pack_var.set(str(tmp_path / "pack.zip"))
    app._find_cover()
    app._took_cover(app._cover_job, str(tmp_path / "wrong.jpg"), "MaupassantUneVie", "Unknown")
    assert app.cover_var.get() == str(tmp_path / "wrong.jpg")

    def find(_words, dest: Path) -> Path:
        dest.write_bytes(b"\xff\xd8\xff")
        return dest

    monkeypatch.setattr(covers, "find", find)
    app.title_var.set("Une vie")
    app._search_words()
    for _ in range(100):
        app._drain()
        if app.cover_var.get() != str(tmp_path / "wrong.jpg"):
            break
        time.sleep(0.05)

    assert Path(app.cover_var.get()).read_bytes().startswith(b"\xff\xd8\xff")


def test_a_pasted_link_becomes_the_cover(app: gui.App, tmp_path: Path, monkeypatch) -> None:
    """The Image box takes a file or a link: a link is fetched and the box is left
    holding the picture it gave."""

    def link(_url: str, dest: Path) -> Path:
        dest.write_bytes(b"\xff\xd8\xff")
        return dest

    monkeypatch.setattr(covers, "link", link)
    app.cover_var.set("https://example.org/cover.jpg")
    app._cover_typed()
    for _ in range(100):
        app._drain()
        if app.cover_var.get() and not gui.is_link(app.cover_var.get()):
            break
        time.sleep(0.05)

    assert Path(app.cover_var.get()).read_bytes().startswith(b"\xff\xd8\xff")


def test_a_link_in_the_image_box_is_fetched_before_the_book_is_converted(
    app: gui.App, tmp_path: Path, monkeypatch
) -> None:
    """One press of the Save button does both jobs: the picture lands, then the run
    that was asked for goes on over it."""

    def link(_url: str, dest: Path) -> Path:
        dest.write_bytes(b"\xff\xd8\xff")
        return dest

    monkeypatch.setattr(covers, "link", link)
    runs: list[tuple] = []
    monkeypatch.setattr(app, "_convert", lambda *args: runs.append(args))
    pack = tmp_path / "pack.zip"
    pack.write_bytes(b"pk")
    app.pack_var.set(str(pack))
    app.out_var.set(str(tmp_path))
    app.cover_var.set("https://example.org/cover.jpg")

    app._start()
    assert runs == [], "the run waits for the picture"

    for _ in range(100):
        app.root.update()
        app._drain()
        if runs:
            break
        time.sleep(0.05)

    assert runs, "the run went on once the link was a file"
    _source, _out_dir, cover, _force = runs[0]
    assert Path(cover).read_bytes().startswith(b"\xff\xd8\xff")


def test_a_link_to_no_picture_says_so(app: gui.App, monkeypatch) -> None:
    monkeypatch.setattr(covers, "link", lambda _url, _dest: None)
    app.cover_var.set("https://example.org/blank.gif")
    app._cover_typed()
    for _ in range(100):
        app._drain()
        if app.t("cover_link_failed") in app.log_view.get("1.0", "end"):
            break
        time.sleep(0.05)

    assert app.t("cover_link_failed") in app.log_view.get("1.0", "end")
    assert app.cover_var.get() == ""


def test_a_search_link_is_handed_to_the_desktop_s_own_opener(monkeypatch) -> None:
    """`webbrowser` searches a fixed list of program names and answers only True or
    False, so a machine whose opener answers to another name opens nothing and says
    nothing."""
    started: list[list[str]] = []
    monkeypatch.setattr(gui.sys, "platform", "linux")
    monkeypatch.setattr(gui.subprocess, "Popen", lambda args, **flags: started.append(list(args)))

    assert gui.open_url("https://duckduckgo.com/?q=x") is True
    assert started == [["xdg-open", "https://duckduckgo.com/?q=x"]]


def test_a_machine_with_no_opener_falls_back_to_webbrowser(monkeypatch) -> None:
    monkeypatch.setattr(gui.sys, "platform", "linux")

    def refuse(_args, **_flags):
        raise OSError("no xdg-open")

    opened: list[str] = []
    monkeypatch.setattr(gui.subprocess, "Popen", refuse)
    monkeypatch.setattr(gui.webbrowser, "open", lambda url: opened.append(url) or True)

    assert gui.open_url("https://example.org") is True
    assert opened == ["https://example.org"]


def test_the_search_writes_the_link_it_tried(app: gui.App, monkeypatch) -> None:
    """Where the machine opens nothing, the log still holds the link to open by hand."""
    monkeypatch.setattr(gui, "open_url", lambda _url: False)
    app.author_var.set("Rachel Corenblit")
    app.title_var.set("Wesh la fée")
    handler = gui.attach_log_handler(app)
    try:
        app._search_cover("ddg")
        app._drain()
    finally:
        logging.getLogger("daisy2telmi").removeHandler(handler)

    shown = app.log_view.get("1.0", "end")
    assert "duckduckgo.com" in shown and "Wesh+la" in shown


def test_a_verbose_window_writes_the_command_lines_it_would_otherwise_hide(app: gui.App) -> None:
    """On Windows the window is the only place the app can write: no terminal beside it."""
    handler = gui.attach_log_handler(app, verbose=True)
    try:
        logging.getLogger("daisy2telmi").debug("running: ffmpeg -i s01.mp3 out.mp3")
        logging.getLogger("daisy2telmi").info("wrote 6 file(s)")
        app._drain()
    finally:
        logging.getLogger("daisy2telmi").removeHandler(handler)

    shown = app.log_view.get("1.0", "end")
    assert "ffmpeg -i s01.mp3" in shown, "the detailed log arrives in the frame"
    assert "wrote 6 file(s)" in shown
    assert app.log_view.tag_ranges("detail"), "the detailed lines read as fine print"


def test_a_launch_with_only_a_verbosity_flag_opens_the_window(monkeypatch) -> None:
    """`daisy-convertor --verbose` is the window, told to write everything it does."""
    from daisy2telmi import cli

    opened: list[bool] = []
    monkeypatch.setattr(gui, "run_gui", lambda verbose=False: opened.append(verbose) or 0)
    assert cli.main(["--verbose"]) == 0
    assert opened == [True]


def test_a_second_pack_loses_the_parts_dropped_from_the_first(app: gui.App, tmp_path: Path) -> None:
    """A dropped part is a number in one book's part list. The next pack has its own
    list, and the same numbers would cut its opening away in silence."""
    for name in ("wesh.zip", "aiguille.zip"):
        (tmp_path / name).write_bytes(b"PK")
    app.pack_var.set(str(tmp_path / "wesh.zip"))
    app.dropped = {1, 2, 3}
    app.pack_var.set(str(tmp_path / "aiguille.zip"))
    assert app.dropped == set(), "a different book, different part numbers"

    app.dropped = {4}
    app.format_var.set("flam")
    assert app.dropped == {4}, "the same pack keeps what was dropped from it"


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


def test_the_file_list_marks_a_picked_row_in_the_same_blue(browser: gui.Browser) -> None:
    """The list carries a style of its own for its taller rows, so the blue has to
    reach the style the list actually uses, not only the plain Treeview."""
    style = ttk.Style(browser.app.root)
    name = browser.tree.cget("style")
    assert style.lookup(name, "background", ("selected",)) == gui.NAVY
    assert style.lookup(name, "foreground", ("selected",)) == "#ffffff"


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
    """The player decides the size of every picture, so it is asked on the line above
    the button that writes the story, beside the chapters it is chosen for."""
    chosen = app.chapters_button.master
    assert app.format_box.master is chosen, "the format sits beside the chapter choice"
    assert chosen.grid_info()["row"] < app.save_button.master.grid_info()["row"]


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


def test_a_run_with_no_output_folder_picked_writes_into_home(app: gui.App, tmp_path, monkeypatch) -> None:
    """Nothing picked is not an error worth stopping a run for: the home folder takes it."""
    home = tmp_path / "home"
    monkeypatch.setattr(gui.Path, "home", lambda: home)
    (tmp_path / "pack.zip").write_bytes(b"PK")
    app.pack_var.set(str(tmp_path / "pack.zip"))
    app.out_var.set("")

    app._start()
    app._drain()

    assert "choose an output folder" not in app.log_view.get("1.0", "end")
    assert app._paths_line.cget("text").endswith("home")


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
    page.python_box.insert(0, "/opt/python")
    page._close()

    assert app.options["language"] == "fr"
    assert app.options["autoplay"] is True
    assert app.options["python"] == "/opt/python"
    saved = (tmp_path / "daisy2telmi" / "config.json").read_text(encoding="utf-8")
    assert '"language": "fr"' in saved
    assert app._labels["save"].cget("text") == "Enregistrer le zip"


def test_every_option_sits_inside_the_page_frame(app: gui.App) -> None:
    """A box parented to the window instead of its frame is drawn outside the window."""
    pages = [(gui.Options(app), "language_box input_box output_box python_box".split())]
    pages.append(
        (
            gui.Speech(app),
            "engine_box language_box voice_box speed_box expression_box "
            "device_box clip_box read_speed_box".split(),
        )
    )
    for page, names in pages:
        frames = page.top.winfo_children()
        assert len(frames) == 1, "the window holds one frame"
        inside, pending = set(), list(frames)
        while pending:
            widget = pending.pop()
            inside.add(widget)
            pending.extend(widget.winfo_children())
        for name in names:
            box = getattr(page, name)
            assert box.master in inside, f"{page.__class__.__name__}.{name}"
        page.top.destroy()


def test_the_main_window_asks_for_the_engine_and_a_page_to_configure_it(app: gui.App) -> None:
    app.options["tts"] = "auto"
    app._show_engines()
    assert app.speech_box.get() == app.t("engine_auto")
    assert app._labels["speech_configure"].cget("text") == app.t("speech_configure")

    app.speech_box.set(app.engine_name("espeak"))
    app._choose_engine()
    assert app.options["tts"] == "espeak"


def test_the_speech_page_saves_the_engine_the_device_and_the_speed(
    app: gui.App, tmp_path, monkeypatch
) -> None:
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    app.options["tts"] = "chatterbox"
    page = gui.Speech(app)
    page.device_box.set("mps")
    page.speed_box.set(app.t("speed_fast"))
    page.clip_box.insert(0, "/tmp/voice.wav")
    page._close()

    assert app.options["device"] == "mps"
    assert app.options["speed"] == "fast"
    assert app.options["clip"] == "/tmp/voice.wav"
    saved = (tmp_path / "daisy2telmi" / "config.json").read_text(encoding="utf-8")
    assert '"device": "mps"' in saved
    assert '"speed": "fast"' in saved


def test_the_speech_page_starts_on_the_book_s_own_language(app: gui.App) -> None:
    page = gui.Speech(app)
    assert page.language_box.get() == app.t("speech_auto")
    page._close()
    assert app.options["speech_language"] is None
    assert app.announce().auto_language is True


def test_the_speech_page_pins_a_language_over_the_book(app: gui.App, tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    page = gui.Speech(app)
    page.language_box.set("tr - Türkçe")
    page._close()

    assert app.options["speech_language"] == "tr"
    assert app.announce().language == "tr"
    assert app.announce().auto_language is False
    saved = (tmp_path / "daisy2telmi" / "config.json").read_text(encoding="utf-8")
    assert '"speech_language": "tr"' in saved


def test_the_options_page_holds_the_folder_the_file_lists_open_in(app: gui.App, tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    packs = tmp_path / "packs"
    packs.mkdir()
    page = gui.Options(app)
    page.input_box.insert(0, str(packs))
    page._close()

    assert app.options["input_dir"] == str(packs)
    assert app._start_dir() == str(packs)


def test_a_file_list_opens_in_the_folder_named_in_the_options(app: gui.App, tmp_path) -> None:
    named = tmp_path / "packs"
    named.mkdir()
    app.options["input_dir"] = str(named)
    assert app._start_dir() == str(named)
    # The folder a file list was left in still wins, because the user was just there.
    app.options["last_dir"] = str(tmp_path)
    assert app._start_dir() == str(tmp_path)


def test_the_options_page_asks_for_the_folder_it_does_not_have(app: gui.App, tmp_path) -> None:
    """An empty box is a question, so the page asks it instead of describing what it
    does with an empty one."""
    quiet = gui.Options(app)
    try:
        assert quiet.folders_hint.cget("text") == app.t("opt_input_unset")
    finally:
        quiet.top.destroy()

    app.options["input_dir"] = str(tmp_path)
    named = gui.Options(app)
    try:
        assert named.folders_hint.cget("text") == app.t("opt_input_hint")
    finally:
        named.top.destroy()


def test_the_window_names_the_folder_it_would_write_into(app: gui.App, tmp_path) -> None:
    """Nothing was picked, so the line under the buttons says where the story would go."""
    app.options["output_dir"] = str(tmp_path / "stories")
    reopened = gui.App(gui.make_root(), options=app.options)
    try:
        assert reopened._paths_line.cget("text").endswith("stories")
    finally:
        reopened.root.destroy()


def test_a_story_folder_that_will_not_be_given_up_says_so(app: gui.App, tmp_path, monkeypatch) -> None:
    """The reuse path copies the tested story straight into the output folder: a folder
    the machine holds is an overwrite that failed, and the window has to say it."""
    story = tmp_path / "story"
    story.mkdir()
    (story / "title.mp3").write_bytes(b"\xff\xfb")
    app._test_folder = story

    def refuse(*args, **kwargs):
        raise PermissionError(13, "Permission denied", str(tmp_path / "story" / "title.mp3"))

    monkeypatch.setattr(gui.shutil, "copy2", refuse)
    assert app._copy_tested(tmp_path / "out") is False
    line = app.log_view.get("1.0", "end")
    assert "Permission denied" in line
    assert "could not be overwritten" in line, "the failure is named as an overwrite"


def _settled(app: gui.App, button: ttk.Button) -> None:
    """A background job hands its button back through the page's queue."""
    for _ in range(300):
        app.root.update()
        if button.instate(["!disabled"]):
            return
        time.sleep(0.01)
    pytest.fail("the job never reported back")


def _probed(page: gui.Settings) -> None:
    """The page asks its engine off its own thread: wait for the newest answer."""
    for _ in range(300):
        page.top.update()
        if page._answered == page._probe:
            return
        time.sleep(0.01)
    pytest.fail("the engine never answered")


def test_the_window_lists_the_engines_without_asking_which_are_installed(app: gui.App, monkeypatch) -> None:
    """The engine box shows names; asking costs a Python per candidate interpreter."""
    asked: list[str] = []
    monkeypatch.setattr(gui.tts, "available", lambda name, python=None: asked.append(name) or False)

    keys = gui.engine_keys()
    assert keys[0] == "auto" and keys[-1] == "off"
    assert gui.tts.CHATTERBOX in keys
    assert asked == []


def test_the_speech_page_asks_its_engine_off_the_window_thread(app: gui.App, monkeypatch) -> None:
    """Asked on the window's own thread, the page sits frozen until every answer is in."""
    threads: list[str] = []

    def slow(engine, language, any_language=False) -> list[str]:
        threads.append(threading.current_thread().name)
        return []

    monkeypatch.setattr(gui.tts, "voices", slow)
    app.options["tts"] = "chatterbox"
    page = gui.Speech(app)
    _probed(page)

    assert threads, "the page did ask its engine"
    assert threading.main_thread().name not in threads, "the window kept moving while it asked"
    page.top.destroy()


def _settled_window(app: gui.App, wanted, what: str) -> None:
    """The window drains its queue on its own timer: wait for the answer it is for."""
    for _ in range(300):
        app.root.update()
        if wanted():
            return
        time.sleep(0.01)
    pytest.fail(what)


def test_the_options_page_says_what_the_python_field_is_for(app: gui.App, monkeypatch) -> None:
    """The box is not a bare "Python": it is the one the speech engines are built in,
    and leaving it alone is the normal way."""
    monkeypatch.setattr(gui.tts, "available", lambda name, python=None: True)
    page = gui.Options(app)
    _probed(page)
    words = [w.cget("text") for w in _walk(page.top) if isinstance(w, ttk.Label)]
    assert app.t("opt_python") in words, "the field says what it holds"
    assert app.t("speech_hint") in words, "and the page says what leaving it empty does"
    page.top.destroy()


def test_the_options_page_has_no_install_button(app: gui.App, monkeypatch) -> None:
    """An engine is installed where it is chosen, not from a settings page."""
    monkeypatch.setattr(gui.tts, "available", lambda name, python=None: False)
    app.options["tts"] = "piper"
    page = gui.Options(app)
    _probed(page)
    assert page.engine_state.cget("text") == app.t("speech_missing").format(engine="Piper")
    assert not any(w.cget("text") == app.t("speech_install") for w in _walk(page.top) if isinstance(w, ttk.Button))
    page.top.destroy()


def test_the_options_page_has_nothing_to_install_for_a_system_engine(app: gui.App, monkeypatch) -> None:
    monkeypatch.setattr(gui.tts, "available", lambda name, python=None: False)
    app.options["tts"] = "espeak"
    page = gui.Options(app)
    _probed(page)
    assert page.engine_state.cget("text") == app.t("speech_builtin")
    page.top.destroy()


def test_an_engine_with_no_python_on_the_machine_says_where_to_get_one(app: gui.App, monkeypatch) -> None:
    monkeypatch.setattr(gui.tts, "available", lambda name, python=None: False)
    monkeypatch.setattr(gui.tts, "has_python", lambda python=None: False)
    app.options["tts"] = "piper"
    page = gui.Options(app)
    _probed(page)
    assert page.engine_state.cget("text") == app.t("speech_no_python")
    assert page.python_button.winfo_viewable(), "the page offers the way to get one"
    page.top.destroy()


def test_a_machine_with_a_python_keeps_the_get_python_button_out(app: gui.App, monkeypatch) -> None:
    monkeypatch.setattr(gui.tts, "available", lambda name, python=None: False)
    monkeypatch.setattr(gui.tts, "has_python", lambda python=None: True)
    app.options["tts"] = "piper"
    page = gui.Options(app)
    _probed(page)
    assert page.engine_state.cget("text") == app.t("speech_missing").format(engine="Piper")
    assert not page.python_button.winfo_viewable()
    page.top.destroy()


def test_picking_an_engine_the_machine_lacks_asks_to_install_it(app: gui.App, monkeypatch) -> None:
    installed: list[tuple[str, str | None, str]] = []
    monkeypatch.setattr(gui.tts, "available", lambda name, python=None: False)
    monkeypatch.setattr(
        gui.tts,
        "install_engine",
        lambda engine, python=None, lang=None: installed.append((engine, python, lang)) or "/opt/venv/bin/python",
    )
    asked: list[str] = []
    monkeypatch.setattr(gui.messagebox, "askokcancel", lambda title, text: asked.append(text) or True)
    app.options["tts"] = "auto"
    app.options["python"] = "/opt/python"
    app._show_engines()
    app.speech_box.set(app.engine_name(gui.tts.PIPER))
    app._choose_engine()

    _settled_window(app, lambda: asked, "the window never asked about the engine")
    assert gui.tts.LABELS[gui.tts.PIPER] in asked[0]
    _settled_window(app, lambda: installed, "the install never ran")
    assert installed[0][:2] == ("piper", "/opt/python")
    assert installed[0][2], "the language in use is named, so its voice downloads first"


def test_an_install_with_no_python_named_records_the_one_the_app_built(app: gui.App, monkeypatch) -> None:
    monkeypatch.setattr(gui.tts, "available", lambda name, python=None: False)
    monkeypatch.setattr(gui.tts, "install_engine", lambda engine, python=None, lang=None: "/opt/venv/bin/python")
    monkeypatch.setattr(gui.messagebox, "askokcancel", lambda title, text: True)
    app.options["tts"] = "auto"
    app.options["python"] = None
    app._show_engines()
    app.speech_box.set(app.engine_name(gui.tts.PIPER))
    app._choose_engine()

    _settled_window(app, lambda: app.options.get("python"), "the install never reported its Python")
    assert app.options["python"] == "/opt/venv/bin/python"


def test_declining_the_install_leaves_the_engine_chosen(app: gui.App, monkeypatch) -> None:
    installed: list = []
    monkeypatch.setattr(gui.tts, "available", lambda name, python=None: False)
    monkeypatch.setattr(gui.tts, "install_engine", lambda *args, **kwargs: installed.append(args))
    monkeypatch.setattr(gui.messagebox, "askokcancel", lambda title, text: False)
    app.options["tts"] = "auto"
    app._show_engines()
    app.speech_box.set(app.engine_name(gui.tts.CHATTERBOX))
    app._choose_engine()

    _settled_window(app, lambda: "not installed" in app.log_view.get("1.0", "end"), "the window said nothing")
    assert app.options["tts"] == gui.tts.CHATTERBOX
    assert installed == []


def test_a_failing_install_is_reported(app: gui.App, monkeypatch, caplog) -> None:
    def refuse(engine, python=None, lang=None):
        raise gui.tts.TtsError("no network")

    monkeypatch.setattr(gui.tts, "available", lambda name, python=None: False)
    monkeypatch.setattr(gui.tts, "install_engine", refuse)
    monkeypatch.setattr(gui.messagebox, "askokcancel", lambda title, text: True)
    app.options["tts"] = "auto"
    app._show_engines()
    app.speech_box.set(app.engine_name(gui.tts.COQUI))
    app._choose_engine()

    for _ in range(300):
        app.root.update()
        if "no network" in caplog.text:
            return
        time.sleep(0.01)
    pytest.fail("the failed install was never reported")


def test_the_speech_page_offers_the_install_for_an_engine_just_chosen(app: gui.App, monkeypatch) -> None:
    monkeypatch.setattr(gui.tts, "available", lambda name, python=None: False)
    monkeypatch.setattr(gui.tts, "install_engine", lambda engine, python=None, lang=None: None)
    asked: list[str] = []
    monkeypatch.setattr(gui.messagebox, "askokcancel", lambda title, text: asked.append(text) or True)
    app.options["tts"] = "auto"
    page = gui.Speech(app)
    _probed(page)
    page.engine_box.set(page._label_text(gui.tts.CHATTERBOX))
    page._show_engine()

    for _ in range(300):
        page.top.update()
        if asked:
            break
        time.sleep(0.01)
    assert asked, "the page asked about the engine it was just given"
    assert gui.tts.LABELS[gui.tts.CHATTERBOX] in asked[0]
    page.top.destroy()


def test_the_speech_page_does_not_ask_about_the_engine_it_opened_with(app: gui.App, monkeypatch) -> None:
    """The engine was chosen earlier; only a new choice gets an offer."""
    monkeypatch.setattr(gui.tts, "available", lambda name, python=None: False)
    asked: list[str] = []
    monkeypatch.setattr(gui.messagebox, "askokcancel", lambda title, text: asked.append(text) or True)
    app.options["tts"] = gui.tts.CHATTERBOX
    page = gui.Speech(app)
    _probed(page)
    page.top.update()
    assert asked == []
    page.top.destroy()


def test_the_get_python_button_opens_the_downloads_page(app: gui.App, monkeypatch) -> None:
    opened: list[str] = []
    monkeypatch.setattr(gui, "open_url", opened.append)
    app.options["tts"] = "piper"
    page = gui.Options(app)
    page._get_python()
    assert opened == [gui.PYTHON_URL]
    page.top.destroy()


def test_an_engine_that_is_not_installed_is_offered_before_the_cards_are_made(
    app: gui.App, monkeypatch
) -> None:
    """The neural engines are not inside the app: the user says whether one may be
    downloaded, and Cancel takes the machine's own voice."""
    app.options["tts"] = "chatterbox"
    monkeypatch.setattr(gui.tts, "available", lambda name, python=None: False)

    monkeypatch.setattr(gui.messagebox, "askokcancel", lambda title, body: True)
    assert app._speech() == ("chatterbox", True)

    monkeypatch.setattr(gui.messagebox, "askokcancel", lambda title, body: False)
    assert app._speech() == (gui.tts.AUTO, False)


def test_an_installed_engine_is_used_without_asking(app: gui.App, monkeypatch) -> None:
    app.options["tts"] = "piper"
    monkeypatch.setattr(gui.tts, "available", lambda name, python=None: True)
    monkeypatch.setattr(
        gui.messagebox, "askokcancel", lambda *args: pytest.fail("asked about an installed engine")
    )
    assert app._speech() == ("piper", False)


def test_the_speech_page_greys_out_the_rows_the_engine_cannot_answer_for(app: gui.App) -> None:
    app.options["tts"] = "piper"
    page = gui.Speech(app)
    assert str(page.speed_box.cget("state")) == "readonly"
    assert str(page.clip_box.cget("state")) == "disabled"

    page.engine_box.set(app.engine_name("espeak"))
    page._show_engine()
    assert str(page.download_box.cget("state")) == "disabled"

    page.engine_box.set(app.engine_name("chatterbox"))
    page._show_engine()
    assert str(page.clip_box.cget("state")) == "normal"
    assert str(page.device_box.cget("state")) == "readonly"


def test_the_speech_page_has_one_page_for_the_cards_and_one_for_the_book(app: gui.App) -> None:
    page = gui.Speech(app)
    assert [page.tabs.tab(tab, "text") for tab in page.tabs.tabs()] == [
        app.t("speech_tab_cards"),
        app.t("speech_tab_reading"),
    ]
    page.top.destroy()


def test_the_book_is_read_with_the_cards_voice(app: gui.App) -> None:
    """One voice reads the cards and the book: the reading page asks for a pace, nothing else."""
    app.options["tts"] = "piper"
    page = gui.Speech(app)
    assert not hasattr(page, "read_voice_box"), "the book has no voice of its own"
    assert not hasattr(page, "read_clip_box"), "the book has no recording of its own"
    assert page.read_speed_box.get() == app.t("speech_same_as_cards")

    page._close()
    assert app.options["read_speed"] is None


def test_the_reading_page_saves_its_own_speed(app: gui.App, tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    app.options["tts"] = "piper"

    page = gui.Speech(app)
    page.read_speed_box.set(app.t("speed_fast"))
    page._close()

    assert app.options["read_speed"] == "fast"
    saved = (tmp_path / "daisy2telmi" / "config.json").read_text(encoding="utf-8")
    assert '"read_speed": "fast"' in saved
    assert "read_voice" not in saved and "read_clip" not in saved


def test_the_voice_lists_hold_the_voices_installed_for_another_language(app: gui.App, tmp_path, monkeypatch) -> None:
    voices = tmp_path / "voices"
    voices.mkdir()
    (voices / "en_US-lessac-medium.onnx").write_text("")
    monkeypatch.setattr(gui.tts.settings, "speech_voices", lambda: voices)
    app.options["language"] = "fr"
    app.options["tts"] = "piper"

    page = gui.Speech(app)
    assert "en_US-lessac-medium" in page.voice_keys


def test_the_reading_speed_row_follows_the_engine(app: gui.App) -> None:
    app.options["tts"] = "piper"
    page = gui.Speech(app)
    assert str(page.read_speed_box.cget("state")) == "readonly"

    page.engine_box.set(app.engine_name("command"))
    page._show_engine()
    assert str(page.read_speed_box.cget("state")) == "disabled"


def test_the_expressiveness_row_follows_the_engine(app: gui.App) -> None:
    app.options["tts"] = "piper"
    page = gui.Speech(app)
    assert str(page.expression_box.cget("state")) == "disabled"

    page.engine_box.set(app.engine_name("chatterbox"))
    page._show_engine()
    assert str(page.expression_box.cget("state")) == "readonly"
    assert list(page.expression_box.cget("values")) == [app.t("expression_natural"), app.t("expression_expressive")]
    assert page.expression_box.get() == app.t("expression_natural")


def test_the_cards_page_saves_the_expressiveness(app: gui.App, tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    app.options["tts"] = "chatterbox"
    page = gui.Speech(app)
    page.expression_box.set(app.t("expression_expressive"))
    page._close()

    assert app.options["expression"] == "expressive"
    saved = (tmp_path / "daisy2telmi" / "config.json").read_text(encoding="utf-8")
    assert '"expression": "expressive"' in saved


def test_the_voice_list_follows_the_engine_and_language(app: gui.App) -> None:
    app.options["language"] = "fr"
    app.options["tts"] = "espeak"
    espeak = next(e for e in gui.tts.engines() if e.name == "espeak")
    if not espeak.found:
        pytest.skip("no espeak-ng on this machine")
    page = gui.Speech(app)
    _probed(page)
    assert any("French" in name or "fr" in name for name in page.voice_box.cget("values"))


def test_the_voice_box_lists_the_voices_the_language_still_lacks(app: gui.App, tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(gui.tts.settings, "speech_voices", lambda: tmp_path)
    (tmp_path / "en_US-lessac-medium.onnx").write_bytes(b"")
    app.options["tts"] = "piper"
    page = gui.Speech(app)
    assert "en_US-lessac-medium" not in list(page.download_box.cget("values"))
    assert any(name.startswith("en_US-") for name in page.download_box.cget("values"))
    assert "en_US-lessac-medium" in [page._label_text(k) for k in page.voice_keys]


def test_installing_a_voice_adds_it_to_the_voice_list(app: gui.App, tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(gui.tts.settings, "speech_voices", lambda: tmp_path)

    def fake(voice: str, python: str | None = None, engine: str = gui.tts.PIPER) -> Path:
        path = tmp_path / f"{voice}.onnx"
        path.write_bytes(b"")
        return path

    monkeypatch.setattr(gui.tts, "download_voice", fake)
    app.options["tts"] = "piper"
    page = gui.Speech(app)
    page.download_box.set("en_US-amy-medium")
    page._download_voice()
    _settled(app, page.download_button)
    assert "en_US-amy-medium" in [page._label_text(k) for k in page.voice_keys]


def test_the_neural_engines_have_a_model_to_install(app: gui.App) -> None:
    """Coqui and Chatterbox carry their weights outside the app: the page has to name
    the one to fetch, or the download only happens in the middle of a conversion."""
    for engine in (gui.tts.COQUI, gui.tts.CHATTERBOX):
        app.options["tts"] = engine
        page = gui.Speech(app)
        assert str(page.download_box.cget("state")) == "readonly", engine
        assert list(page.download_box.cget("values")), f"{engine} offers nothing to install"
        page.top.destroy()


def test_installing_a_model_names_the_engine_it_is_for(app: gui.App, monkeypatch) -> None:
    asked: list[tuple[str, str]] = []

    def fake(voice: str, python: str | None = None, engine: str = gui.tts.PIPER) -> None:
        asked.append((voice, engine))

    monkeypatch.setattr(gui.tts, "download_voice", fake)
    app.options["tts"] = gui.tts.CHATTERBOX
    page = gui.Speech(app)
    page._download_voice()
    _settled(app, page.download_button)
    assert asked == [(gui.tts.CHATTERBOX_MODEL, gui.tts.CHATTERBOX)]


def test_the_try_box_speaks_its_text_and_plays_it_back(app: gui.App, monkeypatch) -> None:
    spoken: list[tuple[str, str]] = []

    def fake(phrase: str, lang_code: str, dest: Path, *rest) -> None:
        dest.write_bytes(b"")
        spoken.append((phrase, lang_code))

    played: list[Path] = []
    monkeypatch.setattr(gui.tts, "synthesize", fake)
    monkeypatch.setattr(gui.chapters, "open_preview", lambda path, resume=0.0: played.append(path))
    app.options["tts"] = "espeak"
    app.language_var.set("fr")
    app.options["language"] = "fr"
    page = gui.Speech(app)
    assert page.try_box.get() == "Chapitre 1", "the box starts with the card's own words"

    page.try_box.delete(0, "end")
    page.try_box.insert(0, "Le lapin")
    page._try_voice()
    _settled(app, page.try_button)
    assert spoken == [("Le lapin", "fr")]
    assert played and played[0].name == "try.mp3"
    page._close()
    assert not page._try_folder.exists(), "the sample clip goes with the window"


def test_every_card_pattern_is_offered_in_the_language_of_the_window(
    app: gui.App, tmp_path, monkeypatch
) -> None:
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    shown = list(app.announce_box.cget("values"))
    assert shown == [app.t(gui.ANNOUNCE_KEYS[mode]) for mode in gui.ANNOUNCE_MODES]

    app.announce_box.set(shown[gui.ANNOUNCE_MODES.index("number_title")])
    app._choose_announce()
    assert app.options["announce"] == "number_title"
    assert app.announce().phrase(1, 3, "Le lapin") == "1. Le lapin"


def test_the_extract_length_is_greyed_when_no_card_plays_an_extract(app: gui.App, tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    assert str(app.seconds_box.cget("state")) == "disabled"
    assert str(app._labels["opt_card_seconds"].cget("foreground")) == gui.MUTED

    shown = list(app.announce_box.cget("values"))
    app.announce_box.set(shown[gui.ANNOUNCE_MODES.index("snippet")])
    app._choose_announce()
    assert str(app.seconds_box.cget("state")) == "normal"
    assert str(app._labels["opt_card_seconds"].cget("foreground")) == gui.INK
    assert app.options["announce"] == "snippet"

    # A playlist has no card to play an opening on: its interlude is always spoken.
    app.format_var.set("mp3")
    app._show_announce()
    assert str(app.seconds_box.cget("state")) == "disabled"
    assert str(app._labels["opt_card_seconds"].cget("foreground")) == gui.MUTED

    app.format_var.set("telmi")
    app._show_announce()
    assert str(app.seconds_box.cget("state")) == "normal"
    assert str(app._labels["opt_card_seconds"].cget("foreground")) == gui.INK


def test_the_card_audio_length_is_typed_in_seconds(app: gui.App, tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    shown = list(app.announce_box.cget("values"))
    app.announce_box.set(shown[gui.ANNOUNCE_MODES.index("snippet")])
    app._choose_announce()
    app.seconds_box.delete(0, "end")
    app.seconds_box.insert(0, "2,5")
    app._type_seconds()
    assert app.options["card_seconds"] == 2.5
    assert app.announce().seconds == 2.5


def test_a_card_audio_length_that_cannot_be_read_keeps_the_default(
    app: gui.App, tmp_path, monkeypatch
) -> None:
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    shown = list(app.announce_box.cget("values"))
    app.announce_box.set(shown[gui.ANNOUNCE_MODES.index("snippet")])
    app._choose_announce()
    app.seconds_box.delete(0, "end")
    app.seconds_box.insert(0, "as fast as you can")
    app._type_seconds()
    assert app.options["card_seconds"] == gui.settings.DEFAULTS["card_seconds"]

    app.seconds_box.delete(0, "end")
    app.seconds_box.insert(0, "900")
    app._type_seconds()
    assert app.options["card_seconds"] == gui.CARD_SECONDS_LIMITS[1]


def test_the_announcement_setting_reaches_the_converter(app: gui.App) -> None:
    app.options.update({"announce": "chapter", "tts": "off", "language": "de"})
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


def test_a_level_the_pack_skips_falls_back_to_the_coarser_side(
    app: gui.App, three_chapters, monkeypatch, tmp_path
) -> None:
    """A book with no paragraph layer, saved at paragraphs: opening it at the
    sentence level is a cut nobody asked for and a thousand rows nobody wants."""
    app.cut = "paragraph"

    def by_level(pack, level: str = "chapter"):
        if level != "sentence":
            return three_chapters
        return Book(
            "Wesh la fee",
            tuple(Chapter(f"s{n}", (AudioSegment(tmp_path / f"s{n}.mp3"),)) for n in range(20)),
        )

    monkeypatch.setattr(gui, "extract", by_level)
    page = gui.ChapterList(app, Path("/tmp/pack.zip"))
    page._measure()
    page._drain()

    assert page.cut == "chapter"
    assert page.cut_var.get() == "chapter"


def test_a_pack_that_will_not_be_read_says_so(app: gui.App, three_chapters, monkeypatch) -> None:
    """The cut levels stay shut until the pack has been read, so a read that failed
    with nothing to say looked like a book with no chapters in it."""
    def unreadable(pack, level: str = "chapter") -> Book:
        raise DaisyPackError("no outline")

    monkeypatch.setattr(gui, "extract", unreadable)
    page = gui.ChapterList(app, Path("/tmp/pack.zip"))
    page._drain()

    assert "no outline" in page.status.cget("text")
    assert not any(box.instate(["!disabled"]) for box in page.cut_boxes.values())


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

    (tmp_path / "pack.zip").write_bytes(b"PK")
    app.pack_var.set(str(tmp_path / "pack.zip"))
    app.out_var.set(str(tmp_path / "out"))

    page = gui.ChapterList(app, Path("/tmp/pack.zip"))
    page.keep[1].set(False)
    page._close()
    assert app.dropped == {2}, "the pack being chosen does not undo what was dropped from it"

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
            "Commercial PK (.pk)",
            "Flam",
            "Merlin (beta)",
            "MP3 playlist",
        ]
    )


def test_an_mp3_pack_hides_the_picture_and_the_emulator(app: gui.App) -> None:
    """A plain player is given no cards, so the window has nothing to draw, nothing to
    preview, no cover to place and no card to letter."""
    app.pages.select(app.display_page)
    app.root.update_idletasks()
    app.format_var.set("mp3")
    app._show_cover()
    assert not app.crop_preview.grid_info(), "the picture frame and its knobs have nothing to show"
    assert not app.cover_card.grid_info(), "the pack carries no picture, so there is no cover to place"
    assert not app.osd_card.grid_info(), "and no card to letter"
    assert app.pages.select() != app.display_page, "the window leaves the page it has nothing to fill"
    assert app.test_button.instate(["disabled"]), "there is no story to test"
    app.pages.select(app.display_page)
    app.root.update_idletasks()
    app.format_var.set("telmi")
    app._show_cover()
    app.pages.select(app.display_page)
    app.root.update_idletasks()
    assert app.crop_preview.grid_info(), "and the frame comes back for a story-teller"
    assert app.cover_card.grid_info(), "with the cover and the card text beside it"
    assert app.osd_card.grid_info()
    assert app.display_page.winfo_ismapped(), "and the page it stands on is offered again"
    assert app.test_button.instate(["!disabled"])


def test_the_negative_is_offered_for_the_grey_screen_only(app: gui.App) -> None:
    assert app.negative_box.instate(["disabled"]), "a colour player has no negative"
    app.format_var.set("lunii")
    app._show_negative()
    assert app.negative_box.instate(["!disabled"]), "the B/W Lunii can be read the other way round"
    app.format_var.set("lunii-pk")
    app._show_negative()
    assert app.negative_box.instate(["disabled"]), "a Commercial PK keeps its colours"


def test_the_crop_knobs_stand_beside_the_frame(app: gui.App) -> None:
    """The choice is made on the picture, so what changes it sits next to it."""
    app.root.update()
    frame, side = app.crop_frame, app.crop_side
    gap = side.winfo_x() - (frame.winfo_x() + frame.winfo_width())
    assert 0 <= gap <= 24, "the knobs stand next to the frame, not in the next column over"
    assert side.winfo_y() == frame.winfo_y(), "the top of the frame is where they start"
    assert all(box.master is app.crop_pad for box in app.crop_arrows.values()), "the four arrows too"
    assert app.crop_zoom_in.master.master is app.crop_pad.master is side, "the pad and the zoom stand on it"
    assert app.contrast_box.master is side, "and the contrast too"
    assert app.negative_box.master is not side, "the negative belongs to the cover, not to the knobs"


def test_the_window_asks_about_the_audio_on_one_page_and_the_screen_on_the_other(app: gui.App) -> None:
    """Two pages, because the two questions are different: what a card says, and what
    the teller's screen shows."""
    assert [app.pages.tab(index, "text") for index in (0, 1)] == [app.t("page_audio"), app.t("page_display")]
    audio, display = (app.root.nametowidget(name) for name in app.pages.tabs())

    def page_of(widget) -> str:
        while widget is not app.root:
            if widget is audio:
                return "audio"
            if widget is display:
                return "display"
            widget = widget.master
        return "outside"

    for name in ("speech_box", "announce_box", "seconds_box"):
        assert page_of(getattr(app, name)) == "audio", f"{name} is an audio choice"
    for name in ("crop_frame", "contrast_box", "card_text_box", "card_title_box", "negative_box", "captions_box"):
        assert page_of(getattr(app, name)) == "display", f"{name} is a display choice"


def test_the_card_is_lettered_on_two_lines(app: gui.App) -> None:
    """The line the card shows first, then the line under it: the number out of the
    total, and the part's own name when it has one."""
    assert app.card_text_box.get() == app.card_text_names()[1], "the first line counts the parts"
    assert app.card_title_box.get() == app.card_title_names()[1], "the second line is the title"

    app.card_text_box.set(app.card_text_names()[0])
    app._choose_card_text()
    app.card_title_box.set(app.card_title_names()[0])
    app._choose_card_title()
    assert app.options["card_total"] is False
    assert app.options["card_title"] is False


def test_the_contrast_reaches_the_conversion(app: gui.App) -> None:
    """A cover picked out on a monitor is not the same picture on a teller's screen."""
    app.contrast_box.set(130)
    app._pick_contrast()
    assert app.options["card_contrast"] == 1.3
    assert app.announce().contrast == 1.3


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


def test_the_words_on_screen_are_offered_for_the_two_screens_that_hold_a_sentence(app: gui.App) -> None:
    app.format_var.set("telmi")
    app._show_captions()
    assert app.captions_box.instate(["!disabled"]), "a Telmi screen holds a sentence"
    app.format_var.set("flam")
    app._show_captions()
    assert app.captions_box.instate(["!disabled"]), "so does the Flam's"
    app.format_var.set("lunii")
    app._show_captions()
    assert app.captions_box.instate(["disabled"]), "a Lunii screen is too small to read a sentence on"
    app.format_var.set("merlin")
    app._show_captions()
    assert app.captions_box.instate(["disabled"]), "and a Merlin card has no screen at all"


def test_the_words_on_screen_ask_for_one_sentence_per_part(app: gui.App, monkeypatch) -> None:
    monkeypatch.setattr(gui.settings, "save", lambda options: None)
    app.cut = "chapter"
    app.captions_var.set(True)
    app._pick_captions()
    assert app.cut == "sentence", "one picture per part is only worth drawing when each part is one"
    assert app.options["cut"] == "sentence", "and the choice is saved as the cut it needs"


def test_the_words_on_screen_recut_the_chapters_window_open_under_them(
    app: gui.App, three_chapters, monkeypatch
) -> None:
    monkeypatch.setattr(gui.settings, "save", lambda options: None)
    page = gui.ChapterList(app, Path("/tmp/pack.zip"))
    page._measure = lambda: None
    assert app.chapter_list is page, "the window knows the list it has open"

    app.format_var.set("telmi")
    app.captions_var.set(True)
    app._pick_captions()

    assert page.cut == "sentence", "the window lists the parts the run will cut"
    assert page.cut_var.get() == "sentence", "and its own radio shows that cut"

    second = gui.ChapterList(app, Path("/tmp/other.zip"))
    second._measure = lambda: None
    page._close()
    assert app.chapter_list is second, "an older window does not clear the one still open"

    second._close()
    assert app.chapter_list is None, "a closed window is not one to follow"


def test_the_words_on_screen_reach_the_conversion(app: gui.App, tmp_path, monkeypatch) -> None:
    seen: list = []
    monkeypatch.setattr(gui, "convert", lambda *args, **kwargs: seen.append((args, kwargs)) or 0)
    monkeypatch.setattr(gui.settings, "save", lambda options: None)

    app.format_var.set("telmi")
    app.captions_var.set(True)
    app._pick_captions()
    (tmp_path / "pack.zip").write_bytes(b"PK")
    app.pack_var.set(str(tmp_path / "pack.zip"))
    app.out_var.set(str(tmp_path / "out"))
    app._start()
    app._drain()
    assert seen[0][1]["captions"] is True
    assert seen[0][0][7] == "telmi", "the format travels with it"


def test_the_words_on_screen_stay_off_for_a_machine_that_has_no_screen_for_them(
    app: gui.App, tmp_path, monkeypatch
) -> None:
    seen: list = []
    monkeypatch.setattr(gui, "convert", lambda *args, **kwargs: seen.append((args, kwargs)) or 0)
    monkeypatch.setattr(gui.settings, "save", lambda options: None)

    app.format_var.set("telmi")
    app.captions_var.set(True)
    app._pick_captions()
    app.format_var.set("lunii")
    app._show_captions()
    app._pick_format()

    (tmp_path / "pack.zip").write_bytes(b"PK")
    app.pack_var.set(str(tmp_path / "pack.zip"))
    app.out_var.set(str(tmp_path / "out"))
    app._start()
    app._drain()

    assert seen[0][1]["captions"] is False, "the Lunii run is not refused for a tick it cannot honour"
    assert app.captions_var.get(), "the tick is kept for the next Telmi run"


@pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg not installed")
def test_a_cover_is_drawn_under_the_crop_frame(app: gui.App, tmp_path) -> None:
    cover = tmp_path / "cover.png"
    cover.write_bytes(images.placeholder_png(300, 900))
    app.cover_var.set(str(cover))
    assert app.crop_frame.itemcget(app._crop_item, "image"), "the frame carries the cover"
    # A 300x900 cover under a 320x240 frame is drawn 320x960 to fill it, so the frame
    # shows 240 of its 960 lines; centred, it starts 360 lines in.
    frame = (int(app.crop_frame.cget("width")), int(app.crop_frame.cget("height")))
    drawn = (app._crop_photo.width(), app._crop_photo.height())
    assert drawn == (frame[0], round(900 * frame[0] / 300)), "the cover fills the frame's width"
    assert tuple(app.crop_frame.coords(app._crop_item)) == (
        0,
        -round((drawn[1] - frame[1]) / 2),
    ), "the frame sits in the middle of the cover"


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
def test_the_reset_button_puts_a_moved_frame_back_on_the_middle(app: gui.App, tmp_path) -> None:
    cover = tmp_path / "cover.png"
    cover.write_bytes(images.placeholder_png(300, 900))
    app.cover_var.set(str(cover))
    app._move_crop(0, 100)

    app._reset_crop()

    frame = (int(app.crop_frame.cget("width")), int(app.crop_frame.cget("height")))
    drawn = (app._crop_photo.width(), app._crop_photo.height())
    middle = -round((drawn[1] - frame[1]) * images.CROP_FOCUS["center"][1])
    assert app.crop_frame.coords(app._crop_item)[1] == middle, "the frame sits back on the middle band"
    assert app.crop_zoom_var.get() == "100%", "and the cover is its own size again"


def test_the_frame_is_drawn_before_a_cover_exists(app: gui.App) -> None:
    """The frame is the chosen player's screen: its shape is what a cover gets
    fitted to, so it is on screen from the start and not only once a cover is picked."""
    width = int(app.crop_frame.cget("width"))
    height = int(app.crop_frame.cget("height"))
    assert (width, height) == images.preview_size(app.format_var.get(), app._crop_width)
    assert app.crop_frame.cget("background") == images.band_colour("colour")
    assert not app.crop_frame.itemcget(app._crop_item, "image"), "no cover, so no picture"

    app.format_var.set("lunii")
    app._pick_format()
    assert (int(app.crop_frame.cget("width")), int(app.crop_frame.cget("height"))) == images.preview_size(
        "lunii", app._crop_width
    )
    assert app.crop_frame.cget("background") == images.band_colour("bw")


def test_the_crop_frame_keeps_the_players_own_shape(app: gui.App) -> None:
    """The frame is the chosen machine's screen, at a width the window can spare: a
    frame wider than any screen it previews made the window wider and taller than a
    laptop, and Tk cuts what hangs past the bottom instead of shrinking it."""
    scene = images.SCREENS[app.format_var.get()][0]
    width = int(app.crop_frame.cget("width"))
    height = int(app.crop_frame.cget("height"))
    assert width <= gui.CROP_WIDTH
    assert round(height / width) == round(scene[1] / scene[0]), "the frame is the screen's own shape"


def test_the_crop_frame_shrinks_on_a_short_screen(app: gui.App, monkeypatch) -> None:
    """Tk cuts what hangs past the bottom of the screen instead of shrinking the
    window, and the buttons and the log are what hangs there."""
    monkeypatch.setattr(app.root, "winfo_screenheight", lambda: 700)
    assert app._crop_room() == 700 - gui.CROP_ROOM
    monkeypatch.setattr(app.root, "winfo_screenheight", lambda: 300)
    assert app._crop_room() == gui.CROP_MIN, "the frame never shrinks past a usable preview"


def test_the_negative_turns_the_preview_inside_out(app: gui.App) -> None:
    """The preview is the card's own picture, so the room around it turns with it —
    and a player that shows colours has nothing to turn."""
    app.format_var.set("lunii")
    app._pick_format()
    plain = app.crop_frame.cget("background")

    app.negative_var.set(True)
    app._pick_negative()
    assert app.crop_frame.cget("background") == images.band_colour("bw", True)
    assert app.crop_frame.cget("background") != plain

    app.format_var.set("merlin")
    app._pick_format()
    assert app.crop_frame.cget("background") == images.band_colour("colour"), "a colour card has nothing to invert"


def test_the_crop_advice_sits_under_the_preview(app: gui.App) -> None:
    """The advice describes the frame it is read against, so it follows that frame's own
    bottom rather than the tallest thing in the row beside it."""
    frame, hint = app.crop_frame, app._labels["crop_hint"]
    app.root.update_idletasks()
    assert hint.master is frame.master, "the words are in the picture's own column"
    assert hint.winfo_y() >= frame.winfo_y() + int(frame.cget("height"))

    app.format_var.set("lunii")
    app._pick_format()
    app.root.update_idletasks()
    assert hint.winfo_y() >= frame.winfo_y() + int(frame.cget("height"))


def test_the_preview_carries_the_cards_own_words(app: gui.App, tmp_path: Path, monkeypatch) -> None:
    """A card is seen on the picture it will be drawn on: the preview shows the very
    lettering the exported cards will carry."""
    monkeypatch.setattr(
        app,
        "_cover_lookup",
        lambda *args: app.events.put(("pack", args[1], 23, "L'Aiguille creuse", "fr")),
    )
    (tmp_path / "aiguille.zip").write_bytes(b"PK")
    app.pack_var.set(str(tmp_path / "aiguille.zip"))
    app._find_cover()
    app._drain()

    size = (int(app.crop_frame.cget("width")), int(app.crop_frame.cget("height")))
    assert app.crop_frame.itemcget(app._osd_item, "image"), "the preview carries the card's words"
    assert app._crop_osd[0] == images.card_label(
        "fr", 1, 23, True, True, "L'Aiguille creuse", size
    ), "the default card: number of total, and the part's own name"

    app.card_text_box.set(app.card_text_names()[0])
    app._choose_card_text()
    assert app._crop_osd[0] == images.card_label("fr", 1, 23, True, False, "L'Aiguille creuse", size)


def test_the_preview_letters_no_total_for_a_cut_that_has_none(app: gui.App, tmp_path: Path, monkeypatch) -> None:
    """The part count and the names come from a chapter-level reading. A paragraph or
    sentence cut has neither, so its card carries its number rather than a wrong total."""
    monkeypatch.setattr(
        app,
        "_cover_lookup",
        lambda *args: app.events.put(("pack", args[1], 23, "L'Aiguille creuse", "fr")),
    )
    (tmp_path / "aiguille.zip").write_bytes(b"PK")
    app.pack_var.set(str(tmp_path / "aiguille.zip"))
    app.cut = app.options["cut"] = "sentence"
    app._find_cover()
    app._drain()

    size = (int(app.crop_frame.cget("width")), int(app.crop_frame.cget("height")))
    assert app._crop_osd[0] == images.card_label("fr", 1, 1, False, False, "", size)


def test_the_grey_preview_carries_a_number_alone(app: gui.App) -> None:
    """Every card of the grey player carries its number alone, so the preview does too."""
    app.format_var.set("lunii")
    app._pick_format()
    assert app._crop_osd[0] == "1"


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
    (root / "images" / "m1.png").write_bytes(images.placeholder_png(320, 240, style="colour"))
    (root / "audios" / "c0.mp3").write_bytes(b"ID3")
    (root / "audios" / "c1.mp3").write_bytes(b"ID3")
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
                        "ok": {"action": "m", "index": 1},
                        "home": {"action": "m", "index": 0},
                        "control": {"ok": False, "home": True, "autoplay": True},
                    },
                    "m1": {
                        "image": "m1.png",
                        "audio": None,
                        "ok": {"action": "s1", "index": 0},
                        "home": None,
                        "control": {"ok": True, "home": True, "autoplay": False},
                    },
                    "s1": {
                        "image": None,
                        "audio": "c1.mp3",
                        "ok": None,
                        "home": {"action": "m", "index": 1},
                        "control": {"ok": False, "home": True, "autoplay": True},
                    },
                },
                "actions": {
                    "m": [{"stage": "m0"}, {"stage": "m1"}],
                    "s0": [{"stage": "s0"}],
                    "s1": [{"stage": "s1"}],
                },
            }
        ),
        encoding="utf-8",
    )
    (root / "metadata.json").write_text(json.dumps({"title": "The book"}), encoding="utf-8")
    return root


def _clips(monkeypatch) -> list[tuple[str, float]]:
    """Record every clip the emulator asked for, and where in it it asked to start."""
    started: list[tuple[str, float]] = []

    def open_clip(path: Path, resume: float = 0.0):
        started.append((path.name, resume))
        return _Player(path)

    monkeypatch.setattr(gui.chapters, "open_preview", open_clip)
    return started


def test_the_emulator_window_walks_the_story_it_is_shown(
    app: gui.App, tmp_path: Path, monkeypatch
) -> None:
    """The window reads the folder the last run left and answers with the machine's
    own buttons: OK moves on, Back goes back."""
    started = _clips(monkeypatch)
    window = gui.Emulator(app, _story_folder(tmp_path / "the book"))
    try:
        assert window.current == "m0"
        assert window.photo is not None, "the chapter card is drawn"
        assert window.lit("ok")

        window._press("ok")
        assert window.current == "s0"
        assert [clip for clip, _ in started] == ["c0.mp3"]

        window._press("home")
        assert window.current == "m0"
    finally:
        window._close()


def test_the_emulator_keys_carry_the_machine_s_words(app: gui.App, tmp_path: Path) -> None:
    """The keys carry the player's words, not the graph's: the key the graph calls
    `home` is Back, the one that holds a clip is Play / pause, and the two side keys
    are the chapter keys. A caption that still reads `emu_ok` is a label that never
    went through the translation."""
    window = gui.Emulator(app, _story_folder(tmp_path / "the book"))
    try:
        assert [window.caption(key) for key in ("up", "down", "ok", "pause", "home")] == [
            "Chapters",
            "Chapters",
            "OK",
            "Play / pause",
            "Back",
        ]
    finally:
        window._close()


def test_a_key_the_stage_switched_off_does_nothing(app: gui.App, tmp_path: Path, monkeypatch) -> None:
    """A dimmed key is a key the machine ignores, and the window says so on its face."""
    _clips(monkeypatch)
    window = gui.Emulator(app, _story_folder(tmp_path / "the book"))
    try:
        window._press("ok")
        assert window.current == "s0"
        assert not window.lit("ok"), "the chapter switched its own OK off"
        window._press("ok")
        assert window.current == "s0"
    finally:
        window._close()


def test_a_chapter_keeps_its_card_then_leaves_the_screen_dark(app: gui.App, tmp_path: Path, monkeypatch) -> None:
    """A chapter has no picture of its own, so it is read from the card it came from:
    that card stays up while the clip runs, then the screen goes dark until a key."""
    _clips(monkeypatch)
    window = gui.Emulator(app, _story_folder(tmp_path / "the book"))
    try:
        window._press("ok")
        assert window.photo is not None, "the card it is being read from is still up"
        assert window._fade is not None, "and it is timed"

        window._dark()
        assert window.photo is None
        assert window.face.find_withtag("picture") == (), "the screen is its own dark"
    finally:
        window._close()


def test_pause_holds_the_clip_and_the_next_press_picks_it_up(app: gui.App, tmp_path: Path, monkeypatch) -> None:
    """Pause stops the sound where it is, and the same key starts it again from there."""
    started = _clips(monkeypatch)
    window = gui.Emulator(app, _story_folder(tmp_path / "the book"))
    try:
        window._press("ok")
        assert window.lit("pause")

        window.started = time.monotonic() - 4
        window._press("pause")
        assert window.player is None
        assert window.heard == pytest.approx(4, abs=0.5)
        assert window.lit("pause"), "the key stays lit so the clip can be picked up"

        window._press("pause")
        assert window.player is not None
        assert started[-1] == ("c0.mp3", pytest.approx(4, abs=0.5))
    finally:
        window._close()


def test_the_side_keys_step_from_one_chapter_to_the_next(app: gui.App, tmp_path: Path, monkeypatch) -> None:
    """The two side keys are the chapter keys: on a card they step to the neighbouring
    card, on a chapter's clip to the neighbouring clip, and the key with no chapter on
    that side is left dark."""
    started = _clips(monkeypatch)
    window = gui.Emulator(app, _story_folder(tmp_path / "the book"))
    try:
        assert window.lit("down") and not window.lit("up"), "the first chapter has nothing behind it"
        window._press("up")
        assert window.current == "m0"

        window._press("down")
        assert window.current == "m1", "the card steps to the card after it"
        assert window.lit("up") and not window.lit("down"), "and the last chapter has nothing ahead"

        window._press("ok")
        assert window.current == "s1"
        window._press("up")
        assert window.current == "s0", "a chapter's clip steps back to the chapter before it"
        assert [clip for clip, _ in started] == ["c1.mp3", "c0.mp3"]
    finally:
        window._close()


def _played_app(app: gui.App, tmp_path: Path, monkeypatch) -> list[Path]:
    """Give the app a converter that only writes a story where it is told to, press
    Test the story, and return the folders it was asked to write into."""
    written: list[Path] = []

    def fake(_source, out_dir, *_rest, **_kwargs):
        written.append(Path(out_dir))
        _story_folder(Path(out_dir) / "the book")
        return 0

    monkeypatch.setattr(gui, "convert", fake)
    (tmp_path / "pack.zip").write_bytes(b"PK")
    app.pack_var.set(str(tmp_path / "pack.zip"))
    app.out_var.set(str(tmp_path / "out"))
    app._test_story()
    for _ in range(100):
        app._drain()
        if getattr(app, "emulator", None) is not None:
            break
        time.sleep(0.05)
    return written


def test_the_test_button_writes_where_it_can_wreck_nothing(app: gui.App, tmp_path, monkeypatch) -> None:
    """Testing a story is looking at it, not shipping it: the pack lands in a folder
    the app made, the player opens on it, and the output folder is left as it was."""
    written = _played_app(app, tmp_path, monkeypatch)
    try:
        assert len(written) == 1
        assert written[0] != tmp_path / "out"
        assert not written[0].is_relative_to(tmp_path), "the test wrote outside the run's folders"
        assert app.emulator.story.title == "The book"
        assert not (tmp_path / "out").exists(), "nothing was written to the output folder"
    finally:
        app.emulator._close()


def test_save_writes_the_tested_pack_without_computing_it_again(app: gui.App, tmp_path, monkeypatch) -> None:
    """Nothing changed since the player showed the pack, so Save writes that pack."""
    written = _played_app(app, tmp_path, monkeypatch)
    app.emulator._close()
    app._start()
    app._drain()
    assert len(written) == 1, "the converter ran once, for the test"
    assert (tmp_path / "out" / "the book" / "nodes.json").is_file()
    assert "saved the story the player just showed" in app.log_view.get("1.0", "end")


def test_a_changed_setting_makes_save_compute_the_story_again(app: gui.App, tmp_path, monkeypatch) -> None:
    """The pack the player showed was drawn for another player, so it is not what
    the window now asks for."""
    written = _played_app(app, tmp_path, monkeypatch)
    app.emulator._close()
    app.format_var.set("lunii")
    app._start()
    for _ in range(100):
        app._drain()
        if len(written) > 1:
            break
        time.sleep(0.05)
    assert len(written) == 2
    assert "saved the story the player just showed" not in app.log_view.get("1.0", "end")


def test_a_folder_that_holds_no_story_is_reported(app: gui.App, tmp_path: Path) -> None:
    """A wrong folder has to be named in the log: a window that opens empty looks
    like a story with nothing in it."""
    (tmp_path / "not a story").mkdir()
    gui.Emulator(app, tmp_path / "not a story")
    assert "no story graph" in app.log_view.get("1.0", "end")


def test_the_emulator_opens_the_lunii_archive_not_the_colour_folder(
    app: gui.App, tmp_path: Path
) -> None:
    """A Lunii run leaves two stories behind: the colour one in a folder, and the
    archive holding the sixteen greys that machine's screen shows. The archive is
    written last, so it is the one the player opens."""
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

    window = gui.Emulator(app, out)
    try:
        assert window.story.machine == "lunii"
        card = window.story.stages[window.story.start]
        assert card.image.parts[-2:] == ("assets", "a1.png")
        assert card.image.is_file(), "the archive was let out so its cards can be drawn"
        assert window.caption("up") == "Chapters", "the side keys carry the same word on both machines"
        assert window.lit("up") and window.lit("down"), "the card left its wheel on"
    finally:
        window._close()
