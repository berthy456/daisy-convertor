"""The drawn scene images: the font table, the card labels, and the cover pass."""

from __future__ import annotations

import re
import shutil
import struct
import subprocess
from pathlib import Path

import pytest

from daisy2telmi import audio, images
from daisy2telmi.cli import FORMATS
from daisy2telmi.i18n import LANGUAGES

NO_FFMPEG = pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg not installed")


def png_header(data: bytes) -> tuple[int, int, int]:
    width, height, depth, colour = struct.unpack(">IIBB", data[16:26])
    assert depth == 8
    return width, height, colour


def test_every_glyph_is_five_by_seven() -> None:
    for character, glyph in images.GLYPHS.items():
        assert len(glyph) == 7, character
        assert all(len(row) == 5 and set(row) <= {"0", "1"} for row in glyph), character


def test_the_font_covers_every_card_word_it_claims() -> None:
    for code, language in LANGUAGES.items():
        drawn = images.card_label(code, 3, 12)
        assert all(character in images.GLYPHS for character in drawn), (code, drawn)


def test_the_card_word_is_written_in_the_story_language() -> None:
    assert images.card_label("en", 2, 23) == "CHAPTER 2/23"
    assert images.card_label("fr", 2, 23) == "CHAPITRE 2/23"
    assert images.card_label("tr", 2, 23) == "BÖLÜM 2/23"
    assert images.card_label("nl", 9, 9) == "HOOFDSTUK 9/9"


def test_a_card_can_leave_the_total_out() -> None:
    assert images.card_label("en", 2, 23, show_total=False) == "CHAPTER 2"
    assert images.card_label("en", 2, 23, word=False, show_total=False) == "2"
    assert images.card_label("zh", 4, 23, show_total=False) == "4"


def test_scripts_the_font_lacks_fall_back_to_the_number() -> None:
    for code in ("zh", "ar"):
        assert images.card_label(code, 4, 23) == "4/23"


def test_glyph_rows_put_the_characters_side_by_side() -> None:
    rows = images.glyph_rows("10")
    assert len(rows) == 7
    assert all(len(row) == 11 for row in rows)  # two 5-wide glyphs and a gap
    assert rows[0] == "00100 01110"


def test_the_card_layer_is_white_ink_on_a_transparent_canvas() -> None:
    width, height, colour = png_header(images.card_layer_png("2/6", "the second of six parts"))
    assert colour == 6, "RGBA, so ffmpeg can overlay it"
    assert (width, height) == images.IMAGE_SIZE


def test_a_card_shows_the_opening_words_in_three_lines() -> None:
    lines = images.card_words("the second of six parts, and the words keep going past the line")
    assert len(lines) == images.CARD_LINES
    assert lines[0].startswith("THE SECOND")
    assert all(len(line) * images.CARD_SCALE * 6 <= images.IMAGE_SIZE[0] for line in lines)
    assert images.card_words("one two") == ["ONE TWO"]


def test_words_the_font_cannot_write_leave_the_card_without_lines() -> None:
    assert images.card_words("これは、私（わたし）が小さいときに") == []
    assert images.card_words("") == []


def test_each_player_draws_at_its_own_screen() -> None:
    assert images.screen("telmi") == ((640, 480), (512, 512))
    assert images.screen("lunii") == ((320, 240), (320, 240))
    assert images.screen("flam") == ((320, 212), (104, 136))
    assert set(images.SCREENS) == set(FORMATS)


def test_a_smaller_card_shrinks_everything_around_it() -> None:
    """The font is a fixed 5x7 grid, so the card's furniture scales with its screen."""
    assert images._layout(640) == (24, (160, 120), 4, 6)
    assert images._layout(320) == (12, (80, 60), 2, 3)


def test_a_smaller_card_holds_the_same_words() -> None:
    sentence = "the second of six parts, and the words keep going past the line"
    assert images.card_words(sentence, 320, 2) == images.card_words(sentence)


def test_the_lettering_of_every_card_stays_inside_its_screen() -> None:
    for player, (scene, _) in images.SCREENS.items():
        margin, thumb, scale, number_scale = images._layout(scene[0])
        lines = images.card_words("the second of six parts, and the words keep going", scene[0], scale)
        bottom = margin + thumb[1] + margin + (len(lines) - 1) * scale * 9 + 7 * scale
        assert bottom <= scene[1], player
        number = images.glyph_rows("213/213")
        assert margin + len(number[0]) * number_scale <= scene[0], player


def test_the_crop_preview_keeps_the_players_ratio() -> None:
    assert images.preview_size("telmi") == (160, 120)
    assert images.preview_size("lunii") == (160, 120)
    assert images.preview_size("flam") == (160, 106)


def test_a_part_below_chapter_level_is_numbered_without_a_word() -> None:
    assert images.card_label("fr", 7, 213, word=False) == "7/213"


def test_the_placeholder_and_the_card_are_the_sizes_telmi_wants() -> None:
    assert png_header(images.placeholder_png())[:2] == (640, 480)
    assert png_header(images.placeholder_png(*images.COVER_SIZE))[:2] == (512, 512)
    assert png_header(images.card_png("7/9"))[:2] == (640, 480)
    assert png_header(images.flat_png())[:2] == (640, 480)


@NO_FFMPEG
def test_write_cover_crops_the_picture_and_numbers_every_card(tmp_path: Path) -> None:
    source = tmp_path / "source.png"
    # A picture with detail in it, so a card's real size shows up.
    subprocess.run(
        [
            "ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
            "-f", "lavfi", "-i", "testsrc2=size=800x600,format=rgb24", "-frames:v", "1", str(source),
        ],
        check=True,
    )
    out = tmp_path / "story"

    images.write_cover(
        source,
        out,
        [images.card_label("en", k + 1, 3) for k in range(3)],
        ["", "", ""],
    )

    assert png_header((out / images.TITLE_IMAGE).read_bytes())[:2] == (640, 480)
    assert png_header((out / images.COVER_IMAGE).read_bytes())[:2] == (512, 512)
    cards = sorted(p.name for p in (out / "images").glob("*.png"))
    assert cards == ["m0.png", "m1.png", "m2.png"]
    for name in cards:
        assert png_header((out / "images" / name).read_bytes())[:2] == (640, 480)
    # The label is what separates the cards; identical cards mean the overlay failed.
    first = (out / "images/m0.png").read_bytes()
    second = (out / "images/m1.png").read_bytes()
    assert first != second
    # A book cut to the sentence level has hundreds of cards, so one must stay small.
    assert len(first) < 20_000


@NO_FFMPEG
def test_write_cover_reports_an_image_ffmpeg_cannot_read(tmp_path: Path) -> None:
    broken = tmp_path / "broken.png"
    broken.write_bytes(b"not a png")
    with pytest.raises(images.ImageError) as failure:
        images.write_cover(broken, tmp_path / "story", [images.card_label("en", 1, 1)], [""])
    message = str(failure.value)
    assert "could not crop" in message
    assert re.search(r"ffmpeg exited \d+", message), "which binary failed, and with what code"
    assert "Conversion failed" in message, "ffmpeg's own words about the picture"


def test_a_failed_run_names_the_binary_its_exit_code_and_its_own_words() -> None:
    message = audio.ffmpeg_failure(
        [r"C:\app\ffmpeg.exe", "-y", "-i", "cover.jpg", "out.png"],
        1,
        "bogus filter expression\nError opening output file out.png.\n",
    )
    assert r"C:\app\ffmpeg.exe" in message, "the copy inside the app and the one on PATH are different problems"
    assert "exited 1" in message
    assert "Error opening output file out.png." in message


def test_a_run_that_wrote_nothing_says_that_instead_of_guessing() -> None:
    silent = audio.ffmpeg_failure(["ffmpeg"], 1, "")
    assert "without writing a word" in silent

    # Windows stops a binary whose libraries are missing, before it can write anything.
    loader = audio.ffmpeg_failure(["ffmpeg"], -1073741515, "")
    assert "0xc0000135" in loader
    assert "library" in loader


def test_a_picture_ffmpeg_refuses_is_reported_as_that(tmp_path: Path) -> None:
    silent = shutil.which("false")
    if silent is None:
        pytest.skip("no `false` on this machine")
    with pytest.raises(images.ImageError, match="without writing a word"):
        images._run([silent], "could not read cover-1.jpg")


BANDS = {"top": "red", "center": "green", "bottom": "blue"}


def _tall_cover(tmp_path: Path) -> Path:
    """Red over green over blue, 300x900: a crop's band is then its colour."""
    source = tmp_path / "bands.png"
    subprocess.run(
        [
            "ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
            "-f", "lavfi", "-i", "color=red:s=300x300",
            "-f", "lavfi", "-i", "color=lime:s=300x300",
            "-f", "lavfi", "-i", "color=blue:s=300x300",
            "-filter_complex", "vstack=inputs=3", "-frames:v", "1", str(source),
        ],
        check=True,
    )
    return source


def _sided_cover(tmp_path: Path) -> Path:
    """Red beside green, 600x300: which side a crop keeps is then its colour."""
    source = tmp_path / "sides.png"
    subprocess.run(
        [
            "ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
            "-f", "lavfi", "-i", "color=red:s=300x300",
            "-f", "lavfi", "-i", "color=lime:s=300x300",
            "-filter_complex", "hstack=inputs=2", "-frames:v", "1", str(source),
        ],
        check=True,
    )
    return source


def _colour(png: Path) -> tuple[int, int, int]:
    result = subprocess.run(
        ["ffmpeg", "-hide_banner", "-loglevel", "error", "-i", str(png),
         "-vf", "scale=1:1", "-f", "rawvideo", "-pix_fmt", "rgb24", "-"],
        capture_output=True,
    )
    assert result.returncode == 0, result.stderr.decode()
    return struct.unpack("BBB", result.stdout[:3])


def _pixel(png: Path, x: int, y: int) -> tuple[int, int, int]:
    """One pixel of a picture, where a band and a cover meet."""
    result = subprocess.run(
        ["ffmpeg", "-hide_banner", "-loglevel", "error", "-i", str(png),
         "-vf", f"crop=1:1:{x}:{y}", "-f", "rawvideo", "-pix_fmt", "rgb24", "-"],
        capture_output=True,
    )
    assert result.returncode == 0, result.stderr.decode()
    return struct.unpack("BBB", result.stdout[:3])


def _band(png: Path) -> str:
    """The channel that dominates a picture: ffmpeg moves a flat colour by a
    couple of units, so the loudest channel is what a crop can be checked on."""
    return max(zip(_colour(png), ("red", "green", "blue")))[1]


@NO_FFMPEG
def test_each_crop_keeps_the_band_it_names(tmp_path: Path) -> None:
    source = _tall_cover(tmp_path)
    for mode, band in BANDS.items():
        out = tmp_path / f"{mode}.png"
        images._crop("ffmpeg", source, images.IMAGE_SIZE, out, mode)
        assert _band(out) == band, mode


@NO_FFMPEG
def test_the_crop_choice_reaches_every_image_write_cover_draws(tmp_path: Path) -> None:
    source = _tall_cover(tmp_path)
    high = tmp_path / "high"
    low = tmp_path / "low"
    images.write_cover(source, high, ["1/1"], [""], "top")
    images.write_cover(source, low, ["1/1"], [""], "bottom")
    for name in (images.TITLE_IMAGE, images.COVER_IMAGE):
        assert _band(high / name) == BANDS["top"], name
        assert _band(low / name) == BANDS["bottom"], name
    # The card's thumbnail follows the same band, so it is the choice that changes it.
    assert (high / "images/m0.png").read_bytes() != (low / "images/m0.png").read_bytes()


@NO_FFMPEG
def test_cover_fit_draws_a_picture_the_frame_can_move_over(tmp_path: Path) -> None:
    """The frame shows a part of this picture, so it has to reach past the frame
    in the direction the cover has more of."""
    png, size = images.cover_fit(_tall_cover(tmp_path), images.PREVIEW_SIZE)
    assert png_header(png)[:2] == size, "the size said is the size drawn"
    assert size[0] == images.PREVIEW_SIZE[0], "a wide frame is filled across"
    assert size[1] > images.PREVIEW_SIZE[1], "a tall cover runs past it the other way"


@NO_FFMPEG
def test_the_zoom_enlarges_the_picture_the_frame_moves_over(tmp_path: Path) -> None:
    source = _tall_cover(tmp_path)
    _, one = images.cover_fit(source, images.PREVIEW_SIZE)
    _, whole = images.cover_fit(source, images.PREVIEW_SIZE, 0.0)
    assert whole[1] == images.PREVIEW_SIZE[1] and whole[0] < images.PREVIEW_SIZE[0], (
        "zoom 0 keeps the whole cover inside the frame, which leaves room sideways"
    )
    _, twice = images.cover_fit(source, images.PREVIEW_SIZE, 2.0)
    assert twice == (one[0] * 2, one[1] * 2)
    _, clamped = images.cover_fit(source, images.PREVIEW_SIZE, 99)
    assert clamped == (one[0] * images.MAX_ZOOM, one[1] * images.MAX_ZOOM), "no zoom past the ceiling"


@NO_FFMPEG
def test_the_preview_is_drawn_in_the_card_s_own_greys(tmp_path: Path) -> None:
    """The frame is the card's own screen, so a Lunii preview cannot keep a colour
    that screen has no grey for, and its negative is the same picture turned over."""
    source = _sided_cover(tmp_path)
    for name, style, negative in (("colour", "colour", False), ("grey", "bw", False), ("flat", "bw", True)):
        png, _ = images.cover_fit(source, images.PREVIEW_SIZE, 1.0, style, negative)
        (tmp_path / f"{name}.png").write_bytes(png)

    colour, grey, flat = (tmp_path / f"{name}.png" for name in ("colour", "grey", "flat"))
    assert _pixel(colour, 10, 60)[0] > 200 and _pixel(colour, 150, 60)[1] > 200, (
        "a colour preview keeps the cover's own colours"
    )
    for png in (grey, flat):
        for x in (10, 150):
            red, green, blue = _pixel(png, x, 60)
            assert red == green == blue, "a Lunii preview has no colour left"
            assert red in images._GREY_LEVELS, "and every grey is one the screen keeps"
    assert _pixel(grey, 10, 60)[0] < _pixel(grey, 150, 60)[0], "the cover's red side reads darker"
    for x in (10, 150):
        assert _pixel(flat, x, 60)[0] == 255 - _pixel(grey, x, 60)[0], "the negative is turned over"


def test_the_frame_fills_its_room_where_the_flattening_puts_the_band() -> None:
    """The frame's empty room is one solid colour that never goes through ffmpeg, so
    it has to be asked where the flattening ends up putting the card's band."""
    plain = int(images.band_colour("bw")[1:3], 16)
    flipped = int(images.band_colour("bw", True)[1:3], 16)
    assert plain in images._GREY_LEVELS and flipped in images._GREY_LEVELS
    assert plain + flipped == 255, "the negative frame's room is the plain one turned over"
    assert images.band_colour("colour") == "#%02x%02x%02x" % images.BAND, "a colour card is left alone"


@NO_FFMPEG
def test_the_card_paints_the_band_the_preview_shows(tmp_path: Path) -> None:
    """A cover shown whole leaves the card's band on both sides of it, and the frame
    paints the same room: the two colours have to be the one colour."""
    source = _tall_cover(tmp_path)
    view = images.crop_view("center", 0.0)
    for negative in (False, True):
        out = tmp_path / f"card{int(negative)}"
        images.write_cover(source, out, ["1"], [""], "center", "bw", (320, 240), (256, 256), view, negative)
        band = _pixel(out / "images/m0.png", 20, 10)
        shown = tuple(int(images.band_colour("bw", negative)[at : at + 2], 16) for at in (1, 3, 5))
        assert band == shown, "the preview shows the band the card ends up with"


def test_crop_view_keeps_the_band_and_clamps_the_zoom() -> None:
    assert images.crop_view()["zoom"] == 1.0
    assert images.crop_view()["focus"] is None
    assert images.crop_view("top", 0)["zoom"] == 0.0, "zoom 0 shows the whole cover"
    assert images.crop_view("top", -1)["zoom"] == images.MIN_ZOOM, "no zoom below the whole cover"
    assert images.crop_view("top", 99)["zoom"] == images.MAX_ZOOM
    assert images.crop_view("top", 2, [0, 1])["focus"] == [0, 1]


@NO_FFMPEG
def test_a_whole_cover_leaves_the_bands_of_the_card(tmp_path: Path) -> None:
    """600x300 in a 640x480 frame fits by 1.07: 640 across and 320 down, so the card
    fills the 80 px above and below the cover with its own band colour."""
    source = _sided_cover(tmp_path)
    out = tmp_path / "whole.png"
    images._crop("ffmpeg", source, images.IMAGE_SIZE, out, "center", {"zoom": 0}, images.BAND)
    for y in (0, images.IMAGE_SIZE[1] - 1):
        assert _pixel(out, 0, y) == pytest.approx(images.BAND, abs=6), f"y={y}"
    assert _pixel(out, 0, 240)[0] > 200, "the middle of the frame is still the cover's red half"


@NO_FFMPEG
def test_a_whole_cover_still_lands_on_the_band_it_was_shown_from(tmp_path: Path) -> None:
    """A tall cover shown whole fills the frame top to bottom and leaves its room on
    the sides instead, where the card's own band colour goes."""
    source = _tall_cover(tmp_path)
    out = tmp_path / "whole-tall.png"
    images._crop("ffmpeg", source, images.IMAGE_SIZE, out, "center", {"zoom": 0}, images.BAND)
    assert _pixel(out, 0, 240) == pytest.approx(images.BAND, abs=6), "the room is on the cover's side"
    assert _pixel(out, 320, 240) == pytest.approx((0, 255, 0), abs=6), "and the cover's middle band is still there"


@NO_FFMPEG
def test_a_moved_frame_keeps_the_band_it_was_dragged_onto(tmp_path: Path) -> None:
    source = _tall_cover(tmp_path)
    for focus, band in (((0.5, 0.0), "red"), ((0.5, 1.0), "blue")):
        out = tmp_path / f"moved-{focus[1]}.png"
        images._crop("ffmpeg", source, images.IMAGE_SIZE, out, "center", {"focus": list(focus)})
        assert _band(out) == band, focus


@NO_FFMPEG
def test_a_frame_with_no_room_to_move_stays_put(tmp_path: Path) -> None:
    """A tall cover already fills a 4:3 frame sideways, so a sideways move does nothing."""
    source = _tall_cover(tmp_path)
    left = tmp_path / "left.png"
    right = tmp_path / "right.png"
    images._crop("ffmpeg", source, images.IMAGE_SIZE, left, "center", {"focus": [0.0, 0.5]})
    images._crop("ffmpeg", source, images.IMAGE_SIZE, right, "center", {"focus": [1.0, 0.5]})
    assert left.read_bytes() == right.read_bytes()


@NO_FFMPEG
def test_zooming_opens_room_to_move_sideways(tmp_path: Path) -> None:
    source = _sided_cover(tmp_path)
    for focus, band in (((0.0, 0.5), "red"), ((1.0, 0.5), "green")):
        out = tmp_path / f"zoom-{focus[0]}.png"
        images._crop("ffmpeg", source, images.IMAGE_SIZE, out, "center", {"focus": list(focus), "zoom": 2})
        assert _band(out) == band, focus


@NO_FFMPEG
def test_zooming_keeps_the_band_the_frame_was_on(tmp_path: Path) -> None:
    source = _tall_cover(tmp_path)
    out = tmp_path / "zoomed.png"
    images._crop("ffmpeg", source, images.IMAGE_SIZE, out, "bottom", {"zoom": 2})
    assert _band(out) == BANDS["bottom"]


@NO_FFMPEG
def test_a_moved_frame_reaches_every_image_write_cover_draws(tmp_path: Path) -> None:
    source = _tall_cover(tmp_path)
    up = tmp_path / "up"
    down = tmp_path / "down"
    images.write_cover(source, up, ["1/1"], [""], "center", view={"focus": [0.5, 0.0]})
    images.write_cover(source, down, ["1/1"], [""], "center", view={"focus": [0.5, 1.0]})
    for name in (images.TITLE_IMAGE, images.COVER_IMAGE):
        assert _band(up / name) == BANDS["top"], name
        assert _band(down / name) == BANDS["bottom"], name
    assert (up / "images/m0.png").read_bytes() != (down / "images/m0.png").read_bytes()


def _pixel(png: Path, x: int, y: int) -> tuple[int, int, int]:
    result = subprocess.run(
        [
            "ffmpeg", "-hide_banner", "-loglevel", "error", "-i", str(png),
            "-vf", f"crop=1:1:{x}:{y}", "-f", "rawvideo", "-pix_fmt", "rgb24", "-",
        ],
        capture_output=True,
    )
    assert result.returncode == 0, result.stderr.decode()
    return struct.unpack("BBB", result.stdout[:3])


@NO_FFMPEG
def test_a_four_grey_card_fills_its_screen_with_the_cover(tmp_path: Path) -> None:
    """A small thumbnail on a white field reads as an empty card, not a cover."""
    out = tmp_path / "story"
    images.write_cover(
        _tall_cover(tmp_path), out, ["1/1"], [""], "center", "bw", (320, 240), (320, 240)
    )
    card = out / "images" / "m0.png"
    assert _pixel(card, 160, 10) == _pixel(out / images.TITLE_IMAGE, 160, 10)


@NO_FFMPEG
def test_a_colour_card_keeps_its_thumbnail_and_its_flat_ground(tmp_path: Path) -> None:
    out = tmp_path / "story"
    images.write_cover(
        _tall_cover(tmp_path), out, ["1/1"], [""], "center", "colour", (320, 240), (320, 240)
    )
    # ffmpeg moves a flat colour on the way through, and by how much depends on the
    # build: two units on Homebrew's, four on Debian 13's.
    assert _pixel(out / "images" / "m0.png", 160, 120) == pytest.approx(images.BACKGROUND, abs=6)


def _inked(ink: list[bytearray], top: int, bottom: int) -> tuple[int, int]:
    """The leftmost and rightmost inked column across a band of rows."""
    columns = [x for line in ink[top:bottom] for x, cell in enumerate(line) if cell in (1, 2)]
    return min(columns), max(columns)


def _inked_rows(ink: list[bytearray]) -> list[int]:
    """The rows that carry any lettering."""
    return [y for y, line in enumerate(ink) if any(cell in (1, 2) for cell in line)]


def test_a_four_grey_card_carries_its_number_alone() -> None:
    ink = images._card_grid("23", "the second of six parts", 320, 240, band=True)
    rows = _inked_rows(ink)
    left, right = _inked(ink, rows[0], rows[-1] + 1)
    assert abs(left + right - 319) <= 1, "the number sits in the middle of its screen"
    assert abs(rows[0] + rows[-1] - 239) <= 1, "and in the middle of it the other way"
    strip = [y for y, line in enumerate(ink) if line[0] == 3]
    assert ink[strip[0]][319] == 3, "the strip behind it reaches the edges"
    assert 3 not in ink[0] and 3 not in ink[239], "the cover keeps the top and the bottom"
    assert len(strip) < 240, "and the strip leaves the cover room"
    bare = images._card_grid("23", "", 320, 240, band=True)
    assert _inked_rows(bare) == rows, "the card draws no opening words"


def test_a_three_digit_number_fills_a_lunii_screen() -> None:
    """The Lunii screen sits behind a plastic window, so its number is as big as it gets."""
    one = images._card_grid("7", "", 320, 240, band=True)
    three = images._card_grid("888", "", 320, 240, band=True)
    left, right = _inked(three, 0, 240)
    assert left <= 12 and right >= 304, "three digits reach the edges of the screen"
    assert _inked_rows(one) == _inked_rows(three), "every card of a story is the same height"


def test_a_colour_card_still_numbers_itself_in_the_corner() -> None:
    ink = images._card_grid("3/12", "the second of six parts", 640, 480)
    margin, _, _, number_scale = images._layout(640)
    left, right = _inked(ink, margin, margin + 7 * number_scale)
    assert abs(right - (640 - margin)) <= 1
    assert left > 640 // 2, "the number stays in the corner"
    assert 3 not in ink[0], "a colour card draws no strip behind its lettering"


def test_the_negative_palette_lands_on_the_greys_the_lunii_keeps() -> None:
    """The grey palette holds one number per colour, not three: flipping it has to
    stay a grey of the same sixteen levels the plain card used."""
    for style in images.LUNII_STYLES:
        base, flipped = dict(images._palette(style)), images._palette(style, negative=True)
        for key, value in base.items():
            assert images._rgb(flipped[key]) == tuple(255 - channel for channel in images._rgb(value)), (style, key)
    assert images._palette("bw", negative=True)["band"] in images._GREY_LEVELS


@NO_FFMPEG
def test_a_negative_placeholder_is_a_picture_a_player_can_read(tmp_path: Path) -> None:
    """These pictures are written byte by byte: a wrong row length only shows up
    when something outside the converter decodes them."""
    for style in images.LUNII_STYLES:
        for negative in (False, True):
            card = tmp_path / f"{style}-{int(negative)}.png"
            card.write_bytes(images.placeholder_png(320, 240, style=style, negative=negative))
            ground, band = _pixel(card, 0, 0), _pixel(card, 0, 240 // 2)
            assert ground != band, card.name
            if style == "bw":
                assert set(ground + band) <= set(images._GREY_LEVELS), card.name
