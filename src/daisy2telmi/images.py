"""Scene images: placeholders, and a user's cover cropped into Telmi's sizes.

ffmpeg scales, crops and composites. A card's number is drawn here with a 5x7 grid
font, so a card needs no image library; a sentence is drawn with a real typeface
bundled in assets, because a small screen forces a size at which a grid font is no
longer readable. Languages whose script neither font can write (Chinese, Arabic)
get the number on its own, which needs no letters to be legible.
"""

from __future__ import annotations

import io
import shutil
import struct
import subprocess
import tempfile
import zlib
from pathlib import Path
from typing import Sequence

from PIL import Image, ImageDraw, ImageFont

from .audio import ffmpeg_failure, require_ffmpeg
from .i18n import language
from .proc import NO_WINDOW, UTF8

TITLE_IMAGE = "title.png"
COVER_IMAGE = "cover.png"
IMAGE_SIZE = (640, 480)
COVER_SIZE = (512, 512)

# What each player's screen shows, so the picture is drawn where it will be seen:
# a cover cropped for a 320x240 Lunii keeps the band that Lunii displays. The
# Merlin has no screen: its story is drawn at Telmi's sizes and merlin.py cuts the
# 128x128 card pictures from them, so the story folder it leaves behind is one the
# Telmi player can still read.
SCREENS = {
    "telmi": (IMAGE_SIZE, COVER_SIZE),
    "lunii": ((320, 240), (320, 240)),
    "lunii-pk": ((320, 240), (320, 240)),
    "flam": ((320, 212), (104, 136)),
    "merlin": (IMAGE_SIZE, COVER_SIZE),
}


def screen(player: str) -> tuple[tuple[int, int], tuple[int, int]]:
    """The scene and cover sizes of one player."""
    return SCREENS[player]


BACKGROUND = (26, 30, 42)
BAND = (78, 92, 122)
FOREGROUND = (214, 222, 238)

# A Lunii that is not a Flam has no colour panel: sixteen greys, and the light end
# of them, so its cards are drawn the other way round — white ground, black ink.
# The published packs keep the same sixteen levels in each picture's own palette.
LUNII_STYLES = ("colour", "bw")

GREY_LEVELS = 16
_GREY_STEP = 255 // (GREY_LEVELS - 1)
_GREY_LEVELS = tuple(_GREY_STEP * level for level in range(GREY_LEVELS))

_PALETTE = {
    "colour": {"background": BACKGROUND, "band": BAND, "ink": (255, 255, 255), "border": (0, 0, 0)},
    "bw": {"background": (255, 255, 255), "band": _GREY_LEVELS[11], "ink": (0, 0, 0), "border": (255, 255, 255)},
}


def _palette(style: str, negative: bool = False) -> dict:
    """The card's own colours; a negative turns the black-and-white one inside out."""
    palette = _PALETTE[style]
    if not negative:
        return palette
    # A grey of the black-and-white palette is one number, and 255 is the top of
    # its sixteen levels, so flipping it lands on a level too.
    return {key: (255 - value if isinstance(value, int) else tuple(255 - channel for channel in value))
            for key, value in palette.items()}

# 5 columns, 7 rows, "1" is ink. Uppercase only: a card label is a title, and it
# keeps the table to what eight Latin-script languages need.
_LETTERS = {
    "A": ("01110", "10001", "10001", "11111", "10001", "10001", "10001"),
    "B": ("11110", "10001", "10001", "11110", "10001", "10001", "11110"),
    "C": ("01110", "10001", "10000", "10000", "10000", "10001", "01110"),
    "D": ("11110", "10001", "10001", "10001", "10001", "10001", "11110"),
    "E": ("11111", "10000", "10000", "11110", "10000", "10000", "11111"),
    "F": ("11111", "10000", "10000", "11110", "10000", "10000", "10000"),
    "G": ("01110", "10001", "10000", "10111", "10001", "10001", "01111"),
    "H": ("10001", "10001", "10001", "11111", "10001", "10001", "10001"),
    "I": ("01110", "00100", "00100", "00100", "00100", "00100", "01110"),
    "J": ("00111", "00010", "00010", "00010", "00010", "10010", "01100"),
    "K": ("10001", "10010", "10100", "11000", "10100", "10010", "10001"),
    "L": ("10000", "10000", "10000", "10000", "10000", "10000", "11111"),
    "M": ("10001", "10001", "11011", "10101", "10001", "10001", "10001"),
    "N": ("10001", "10001", "11001", "10101", "10011", "10001", "10001"),
    "O": ("01110", "10001", "10001", "10001", "10001", "10001", "01110"),
    "P": ("11110", "10001", "10001", "11110", "10000", "10000", "10000"),
    "Q": ("01110", "10001", "10001", "10001", "10101", "10010", "01101"),
    "R": ("11110", "10001", "10001", "11110", "10100", "10010", "10001"),
    "S": ("01111", "10000", "10000", "01110", "00001", "00001", "11110"),
    "T": ("11111", "00100", "00100", "00100", "00100", "00100", "00100"),
    "U": ("10001", "10001", "10001", "10001", "10001", "10001", "01110"),
    "V": ("10001", "10001", "10001", "10001", "10001", "01010", "00100"),
    "W": ("10001", "10001", "10001", "10101", "11011", "10001", "10001"),
    "X": ("10001", "10001", "01010", "00100", "01010", "10001", "10001"),
    "Y": ("10001", "10001", "01010", "00100", "00100", "00100", "00100"),
    "Z": ("11111", "00001", "00010", "00100", "01000", "10000", "11111"),
    "Á": ("00100", "00010", "01110", "10001", "11111", "10001", "10001"),
    "É": ("00100", "00010", "11111", "10000", "11110", "10000", "11111"),
    "Í": ("00100", "00010", "01110", "00100", "00100", "00100", "01110"),
    "Ó": ("00100", "00010", "01110", "10001", "10001", "10001", "01110"),
    "Ö": ("00101", "00000", "01110", "10001", "10001", "10001", "01110"),
    "Ü": ("00101", "00000", "10001", "10001", "10001", "10001", "01110"),
    "Ç": ("01110", "10001", "10000", "10000", "10000", "10001", "01110"),
    # The rest of what a French or German sentence needs: the accents the words
    # carry, and the marks that hold a sentence together.
    "À": ("00100", "01000", "01110", "10001", "11111", "10001", "10001"),
    "Â": ("00100", "01010", "01110", "10001", "11111", "10001", "10001"),
    "È": ("00100", "01000", "11111", "10000", "11110", "10000", "11111"),
    "Ê": ("00100", "01010", "11111", "10000", "11110", "10000", "11111"),
    "Ë": ("00101", "00000", "11111", "10000", "11110", "10000", "11111"),
    "Î": ("00100", "01010", "01110", "00100", "00100", "00100", "01110"),
    "Ï": ("00101", "00000", "01110", "00100", "00100", "00100", "01110"),
    "Ô": ("00100", "01010", "01110", "10001", "10001", "10001", "01110"),
    "Ù": ("00100", "01000", "10001", "10001", "10001", "10001", "01110"),
    "Û": ("00100", "01010", "10001", "10001", "10001", "10001", "01110"),
    "Ñ": ("01010", "10001", "11001", "10101", "10011", "10001", "10001"),
    ".": ("00000", "00000", "00000", "00000", "00000", "01110", "01110"),
    ",": ("00000", "00000", "00000", "00000", "01110", "01110", "00100"),
    ":": ("00000", "01110", "01110", "00000", "01110", "01110", "00000"),
    ";": ("00000", "01110", "01110", "00000", "01110", "01110", "00100"),
    "!": ("00100", "00100", "00100", "00100", "00100", "00000", "00100"),
    "?": ("01110", "10001", "00001", "00010", "00100", "00000", "00100"),
    "'": ("00100", "00100", "00000", "00000", "00000", "00000", "00000"),
    "’": ("00100", "00100", "00000", "00000", "00000", "00000", "00000"),
    "“": ("01010", "01010", "00000", "00000", "00000", "00000", "00000"),
    "”": ("01010", "01010", "00000", "00000", "00000", "00000", "00000"),
    "«": ("00000", "00101", "01010", "10000", "01010", "00101", "00000"),
    "»": ("00000", "10100", "01010", "00010", "01010", "10100", "00000"),
    "-": ("00000", "00000", "00000", "11111", "00000", "00000", "00000"),
    "(": ("00010", "00100", "01000", "01000", "01000", "00100", "00010"),
    ")": ("01000", "00100", "00010", "00010", "00010", "00100", "01000"),
    "/": ("00001", "00010", "00010", "00100", "01000", "01000", "10000"),
    " ": ("00000", "00000", "00000", "00000", "00000", "00000", "00000"),
}

_DIGITS = {
    "0": ("01110", "10001", "10011", "10101", "11001", "10001", "01110"),
    "1": ("00100", "01100", "00100", "00100", "00100", "00100", "01110"),
    "2": ("01110", "10001", "00001", "00010", "00100", "01000", "11111"),
    "3": ("11111", "00010", "00100", "00010", "00001", "10001", "01110"),
    "4": ("00010", "00110", "01010", "10010", "11111", "00010", "00010"),
    "5": ("11111", "10000", "11110", "00001", "00001", "10001", "01110"),
    "6": ("01110", "10001", "10000", "11110", "10001", "10001", "01110"),
    "7": ("11111", "00001", "00010", "00100", "01000", "01000", "01000"),
    "8": ("01110", "10001", "10001", "01110", "10001", "10001", "01110"),
    "9": ("01110", "10001", "10001", "01111", "00001", "10001", "01110"),
}

GLYPHS = {**_DIGITS, **_LETTERS}


class ImageError(RuntimeError):
    """Raised when ffmpeg cannot produce a scene image."""


def _png(width: int, height: int, rows: list[bytes], colour_type: int = 2) -> bytes:
    def chunk(tag: bytes, payload: bytes) -> bytes:
        body = tag + payload
        return struct.pack(">I", len(payload)) + body + struct.pack(">I", zlib.crc32(body) & 0xFFFFFFFF)

    raw = b"".join(b"\x00" + row for row in rows)
    header = struct.pack(">IIBBBBB", width, height, 8, colour_type, 0, 0, 0)
    return (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", header)
        + chunk(b"IDAT", zlib.compress(raw, 9))
        + chunk(b"IEND", b"")
    )


def placeholder_png(
    width: int = IMAGE_SIZE[0], height: int = IMAGE_SIZE[1], style: str = "colour", negative: bool = False
) -> bytes:
    """A flat placeholder, written by hand so the converter needs no image library."""
    palette = _palette(style, negative)
    band_top, band_bottom = height // 2 - 48, height // 2 + 48
    rows = []
    for y in range(height):
        rgb = palette["band"] if band_top <= y < band_bottom else palette["background"]
        rows.append(bytes(_rgb(rgb)) * width)
    return _png(width, height, rows)


def glyph_rows(text: str) -> list[str]:
    """The 7 rows of a 5x7 rendering of `text`, one character per column."""
    return [" ".join(GLYPHS[character][y] for character in text) for y in range(7)]


MARGIN = 24
CARD_LINES = 3
CARD_WORDS = 20
CARD_SCALE = 4
NUMBER_SCALE = 6


def _layout(width: int) -> tuple[int, int, int, int]:
    """The margin, the height the cover keeps to itself, and the two lettering sizes.

    The built-in font is a fixed 5x7 grid, so a smaller screen shrinks everything
    around it by the same step and the card still holds the same words.
    """
    step = width / IMAGE_SIZE[0]
    return (
        max(6, round(MARGIN * step)),
        width // 4 * 3 // 4,
        max(1, round(CARD_SCALE * step)),
        max(2, round(NUMBER_SCALE * step)),
    )


def _drawable(text: str) -> str:
    """`text` upper cased with every character the built-in font lacks turned into a space."""
    return "".join(character if character in GLYPHS else " " for character in text.upper())


def card_words(text: str, width: int = IMAGE_SIZE[0], scale: int = CARD_SCALE) -> list[str]:
    """The opening words of a part, as up to three lines the card can show.

    The built-in font is 5x7 Latin, so a part whose words it cannot write gets no
    lines at all: the number and the spoken snippet still place the listener.
    """
    words = _drawable(text).split()[:CARD_WORDS]
    if not words:
        return []
    columns = (width - 2 * _layout(width)[0]) // (scale * 6)
    lines: list[str] = []
    for word in words:
        if lines and len(lines[-1]) + 1 + len(word) <= columns:
            lines[-1] = f"{lines[-1]} {word}"
        elif len(lines) < CARD_LINES:
            lines.append(word)
        else:
            break
    return [line[:columns] for line in lines]


def _stamp(ink: list[bytearray], left: int, top: int, glyph: list[str], scale: int, border: int) -> None:
    """Mark a 5x7 `glyph` into `ink`: 1 for the outline, 2 for the letter itself."""
    for pass_number, value in ((1, 1), (2, 2)):
        for y, row in enumerate(glyph):
            for x, cell in enumerate(row):
                if cell != "1":
                    continue
                grow = border if pass_number == 1 else 0
                for py in range(top + y * scale - grow, top + (y + 1) * scale + grow):
                    if 0 <= py < len(ink):
                        line = ink[py]
                        for px in range(left + x * scale - grow, left + (x + 1) * scale + grow):
                            if 0 <= px < len(line):
                                line[px] = value


def _strip(ink: list[bytearray], top: int, bottom: int) -> None:
    """Mark a full-width strip of the card where the lettering gets its own ground."""
    for line in ink[max(0, top) : min(len(ink), bottom)]:
        line[:] = bytes([3]) * len(line)


def _number_grid(label: str, width: int, height: int) -> list[bytearray]:
    """A four-grey card: the part's number alone, as large as the screen allows.

    The Lunii screen sits behind a plastic window, so nothing drawn small is
    readable there: no word, no total and no opening words, only the number. It is
    sized as a three-digit one, so every card of a story carries a number of the
    same height and a story of a hundred parts fills the width of the screen.
    """
    margin, _, _, _ = _layout(width)
    ink = [bytearray(width) for _ in range(height)]
    rows = glyph_rows(label)
    columns = len(rows[0])
    size = min(
        (width - 2 * margin) // max(columns, 3 * 6 - 1),
        (height - 2 * margin) // len(rows),
    )
    top = (height - size * len(rows)) // 2
    _strip(ink, top - margin, top + size * len(rows) + margin)
    _stamp(ink, (width - columns * size) // 2, top, rows, size, max(2, size // 4))
    return ink


def _card_grid(label: str, text: str, width: int, height: int, band: bool = False) -> list[bytearray]:
    """One card's lettering: its number, and the words it opens with.

    A colour card numbers itself top right, and its words start under the height
    the cover keeps to itself. A four-grey card is the Lunii's, and draws the
    number alone.
    """
    if band:
        return _number_grid(label, width, height)
    margin, cover_room, scale, number_scale = _layout(width)
    ink = [bytearray(width) for _ in range(height)]
    number = glyph_rows(label)
    number_width = len(number[0]) * number_scale
    left = width - margin - number_width
    _stamp(ink, left, margin, number, number_scale, 1)

    lines = card_words(text, width, scale)
    top = margin + cover_room + margin
    for offset, line in enumerate(lines):
        _stamp(ink, margin, top + offset * scale * 9, glyph_rows(line), scale, 1)
    return ink


def _grid_png(
    ink: list[bytearray], base: tuple[int, int, int] | None, style: str = "colour", negative: bool = False
) -> bytes:
    """Colour a card's lettering grid: over `base` for a whole card, transparent for an overlay."""
    palette = _palette(style, negative)
    letter, border, ground = bytes(palette["ink"]), bytes(palette["border"]), bytes(palette["background"])
    width, height = len(ink[0]), len(ink)
    rows = []
    for line in ink:
        row = bytearray()
        for cell in line:
            if cell == 2:
                row += letter + (b"" if base is not None else b"\xff")
            elif cell == 1:
                row += border + (b"" if base is not None else b"\xff")
            elif cell == 3:
                row += ground + (b"" if base is not None else b"\xff")
            elif base is not None:
                row += bytes(base)
            else:
                row += b"\x00\x00\x00\x00"
        rows.append(bytes(row))
    return _png(width, height, rows, colour_type=6 if base is None else 2)


def card_png(
    label: str,
    text: str = "",
    width: int = IMAGE_SIZE[0],
    height: int = IMAGE_SIZE[1],
    style: str = "colour",
    negative: bool = False,
) -> bytes:
    """A card on its own: the part number, and the words it opens with when we can draw them."""
    return _grid_png(
        _card_grid(label, text, width, height, band=style == "bw"),
        _palette(style, negative)["background"],
        style,
        negative,
    )


def card_layer_png(
    label: str,
    text: str = "",
    width: int = IMAGE_SIZE[0],
    height: int = IMAGE_SIZE[1],
    style: str = "colour",
    negative: bool = False,
) -> bytes:
    """The same lettering on a transparent canvas, to lay over a cropped cover."""
    return _grid_png(_card_grid(label, text, width, height, band=style == "bw"), None, style, negative)


# A sentence's own picture: the whole of it, at the largest lettering the screen
# still holds. The screen is small, so the size is found by trying the largest and
# stepping down until every line of the sentence fits on it. The ceiling is the web
# page's, so the same sentence is the same size on both.
CAPTION_MAX_SIZE = 96
CAPTION_MIN_SIZE = 10
# The air between two lines of a caption grows with the lettering, but never below
# a few pixels: at the small lettering a tall sentence needs, one pixel of air reads
# as one solid block of words.
CAPTION_LINE_GAP = 4

# A sentence needs letterforms of its own width, and lower case as well as upper,
# to be read at the size a small screen forces on it. Arimo is metric compatible
# with Arial and carries the SIL Open Font Licence, whose text sits beside it in
# assets; that licence lets the font travel inside the binaries.
FONT_FILE = Path(__file__).resolve().parent / "assets" / "Arimo.ttf"
# A font answers a character it has no glyph for with its own .notdef box, which is
# the same picture for every character it cannot write.
_NOTDEF = "￿"

_SIZES: dict[int, ImageFont.FreeTypeFont] = {}
_GLYPHS: dict[str, bool] = {}


def _typeface(size: int) -> ImageFont.FreeTypeFont:
    """The caption font at one pixel size, loaded once."""
    found = _SIZES.get(size)
    if found is None:
        found = ImageFont.truetype(str(FONT_FILE), size)
        _SIZES[size] = found
    return found


def _letterable(font: ImageFont.FreeTypeFont, text: str) -> str:
    """`text` in single-spaced words, with every character the font has no glyph for turned into a space.

    A sentence keeps the line breaks the page it was written on had, and the font
    renderer refuses to lay one out at all: here they are word separators.
    """
    blank = bytes(font.getmask(_NOTDEF))
    kept = []
    for character in " ".join(text.split()):
        known = _GLYPHS.get(character)
        if known is None:
            known = bytes(font.getmask(character)) != blank
            _GLYPHS[character] = known
        kept.append(character if known else " ")
    return "".join(kept)


def line_pitch(font: ImageFont.FreeTypeFont) -> int:
    """How many pixel rows one line of a caption takes: its lettering and the air under it."""
    ascent, descent = font.getmetrics()
    return ascent + descent + max(CAPTION_LINE_GAP, round(font.size / 8))


def _block(lines: list[str], font: ImageFont.FreeTypeFont) -> int:
    """The height a caption's lines take on the screen."""
    return (len(lines) - 1) * line_pitch(font) + sum(font.getmetrics())


def _wrap(text: str, font: ImageFont.FreeTypeFont, room: int) -> list[str]:
    """`text` in lines no wider than `room`; a word wider than that is cut."""
    lines: list[str] = []
    line = ""
    for word in text.split():
        if line and font.getlength(f"{line} {word}") <= room:
            line = f"{line} {word}"
            continue
        if line:
            lines.append(line)
            line = ""
        while font.getlength(word) > room:
            taken = 1
            while taken < len(word) and font.getlength(word[: taken + 1]) <= room:
                taken += 1
            lines.append(word[:taken])
            word = word[taken:]
        line = word
    if line:
        lines.append(line)
    return lines


def _caption_fit(text: str, width: int, height: int) -> tuple[int, list[str]]:
    """The largest lettering size the whole sentence fits at, and its lines.

    A bigger size needs more lines and taller ones, so the sizes are searched by
    halving: the largest one that holds is found in a handful of tries, which
    matters when a book has a thousand sentences to letter.
    """
    margin = _layout(width)[0]
    room_w, room_h = width - 2 * margin, height - 2 * margin
    best: tuple[int, list[str]] | None = None
    low, high = CAPTION_MIN_SIZE, CAPTION_MAX_SIZE
    while low <= high:
        size = (low + high) // 2
        font = _typeface(size)
        lines = _wrap(_letterable(font, text), font, room_w)
        if lines and _block(lines, font) <= room_h:
            best = size, lines
            low = size + 1
        else:
            high = size - 1
    if best is not None:
        return best
    # Nothing above the smallest size held: the smallest size, and only the lines
    # the screen has room for.
    font = _typeface(CAPTION_MIN_SIZE)
    lines = _wrap(_letterable(font, text), font, room_w)
    room = max(1, 1 + (room_h - sum(font.getmetrics())) // line_pitch(font))
    return CAPTION_MIN_SIZE, lines[:room]


def caption_lines(text: str, width: int, height: int) -> list[str]:
    """The sentence as one screen will show it, at the largest size that holds it.

    A sentence the caption font has no letters for gets no lines at all: the audio
    is still there, only the words are not something we can draw.
    """
    return _caption_fit(text, width, height)[1]


def _photo_png(canvas: Image.Image) -> bytes:
    """A canvas Pillow drew, written as the PNG the machine will read."""
    buffer = io.BytesIO()
    canvas.save(buffer, "PNG", optimize=True)
    return buffer.getvalue()


# A caption is filled from a mask rather than drawn in colour: the words keep one
# colour on one ground, as a card's do, and a book carries a thousand of these
# pictures. A screen reads a hard letter edge as well as a soft one.
_HARD_EDGE = [0 if level < 128 else 255 for level in range(256)]


def caption_png(
    text: str,
    width: int = IMAGE_SIZE[0],
    height: int = IMAGE_SIZE[1],
    style: str = "colour",
    negative: bool = False,
) -> bytes:
    """One sentence on its own ground: no cover behind it, nothing but the words."""
    palette = _palette(style, negative)
    canvas = Image.new("RGB", (width, height), _rgb(palette["background"]))
    size, lines = _caption_fit(text, width, height)
    if not lines:
        return _photo_png(canvas)
    font = _typeface(size)
    pitch = line_pitch(font)
    top = max(_layout(width)[0], (height - _block(lines, font)) // 2)
    ground = Image.new("L", (width, height), 0)
    drawn = ImageDraw.Draw(ground)
    for offset, line in enumerate(lines):
        drawn.text(
            ((width - round(font.getlength(line))) // 2, top + offset * pitch),
            line,
            font=font,
            fill=255,
        )
    canvas.paste(_rgb(palette["ink"]), (0, 0), ground.point(_HARD_EDGE))
    return _photo_png(canvas)


def card_label(
    language_code: str, number: int, total: int, word: bool = True, show_total: bool = True
) -> str:
    """The words drawn on a card, in the story's language.

    The font is Latin, so Chinese and Arabic cards carry the number alone: it is
    the part that has to be read, and the spoken card already says the word.
    A part cut below chapter level gets the number alone as well, because none
    of the words we have would be the right one for it.
    """
    shown = f"{number}/{total}" if show_total else str(number)
    label = f"{language(language_code).card.upper()} {shown}" if word else shown
    if all(character in GLYPHS for character in label):
        return label
    return shown


def _run(args: list[str], description: str) -> None:
    result = subprocess.run(args, capture_output=True, creationflags=NO_WINDOW, **UTF8)
    if result.returncode != 0:
        raise ImageError(
            f"{description}: {ffmpeg_failure(args, result.returncode, result.stderr or result.stdout)}"
        )


# A tall cover loses its title to a centre-crop, so which band survives is the
# user's call.
CROP_MODES = ("top", "center", "bottom")

# Where the frame sits over the cover, as fractions of the room it has to move
# in: 0 is the cover's left/top edge, 1 its right/bottom edge. The web page lets
# the frame be dragged and enlarged anywhere inside these bounds, so the desktop
# reads the same two numbers rather than only the three bands.
CROP_FOCUS = {"top": (0.5, 0.0), "center": (0.5, 0.5), "bottom": (0.5, 1.0)}
# The slider's ends are the two things a listener can see: the whole cover with the
# frame's extra room filled by bands, and the cover filling the frame.
MIN_ZOOM = 0.0
MAX_ZOOM = 4.0


def clamp_zoom(value: float) -> float:
    return min(max(MIN_ZOOM, float(value)), MAX_ZOOM)


def _rgb(value: tuple[int, int, int] | int) -> tuple[int, int, int]:
    """The black-and-white palette holds its greys as one number; ffmpeg and Tk want three."""
    return value if isinstance(value, tuple) else (value, value, value)


def _hex(rgb: tuple[int, int, int] | int) -> str:
    return "0x%02x%02x%02x" % _rgb(rgb)


def _grey_level(value: int, negative: bool = False) -> int:
    """The grey one brightness lands on once `_grey_chain` has done with it.

    The same three steps in Python as in the ffmpeg chain: the contrast first, then
    the swap, then the levels. A preview fills its empty room with one solid colour
    and cannot run it through ffmpeg, so this says where that colour ends up.
    """
    light = max(0, min(255, round((value - 128) * 1.6 + 128)))
    return round((255 - light if negative else light) * (GREY_LEVELS - 1) / 255) * _GREY_STEP


def band_colour(style: str, negative: bool = False) -> str:
    """The colour a card fills the frame's empty room with, spelled as Tk wants it.

    A grey card flattens the room with the rest of the picture, so the preview asks
    `_grey_level` where that colour ends up rather than using the palette's own.
    """
    band = _palette(style)["band"]
    return "#%02x%02x%02x" % _rgb(_grey_level(band, negative) if style == "bw" else band)


def _grey_chain(negative: bool = False) -> str:
    """Flatten a picture to the sixteen greys the Lunii keeps.

    The contrast is pushed before the levels, which is how the published packs
    read: mostly light greys, black where the drawing is, and the blur keeps large
    areas flat instead of dithered. A negative swaps light and dark first, so the
    levels still land on the same sixteen.
    """
    steps = f"trunc((val*{GREY_LEVELS - 1}+255/2)/255)*{_GREY_STEP}"
    return (
        f"format=gray,eq=contrast=1.6,gblur=sigma=1{',negate' if negative else ''}"
        f",lutyuv=y='{steps}',format=rgb24"
    )


def crop_view(mode: str = "center", zoom: float = 1.0, focus: Sequence[float] | None = None) -> dict:
    """The one picture of the crop: a band, a place, an enlargement.

    `focus` overrides the band as soon as the frame is moved, which is what the
    web page's drag does; `zoom` enlarges the cover under a frame that stays put,
    and goes below 1 to show the whole cover with bands.
    """
    return {
        "crop": mode,
        "zoom": clamp_zoom(zoom),
        "focus": list(focus) if focus else None,
    }


def _scale_chain(width: int, height: int, zoom: float = 1.0) -> str:
    """Scale the cover for `size`.

    Zoom 0 fits the whole cover inside the frame, zoom 1 fills the frame, and past
    1 the cover grows further and the frame crops it. Between 0 and 1 the scale
    moves geometrically from the fit to the fill, so the slider's ends land on the
    two things a listener can see.

    `ceil` to an even number so the scaled cover always reaches the frame, which
    `crop` cannot place outside itself. The quotes matter: the filter graph would
    read max()'s comma as the end of the scale filter.
    """
    fit = f"min({width}/iw,{height}/ih)"
    fill = f"max({width}/iw,{height}/ih)"
    zoom = clamp_zoom(zoom)
    # The geometric step from the fit to the fill is only taken up to zoom 1; past it
    # the cover simply grows by the zoom again.
    factor = f"({fit})*(pow(({fill})/({fit}),min({zoom},1))*max(1,{zoom}))"
    return f"scale=w='ceil(iw*{factor}/2)*2':h='ceil(ih*{factor}/2)*2'"


def _crop(
    ffmpeg: str,
    source: Path,
    size: tuple[int, int],
    dest: Path,
    mode: str = "center",
    view: dict | None = None,
    band: tuple[int, int, int] = BAND,
) -> None:
    width, height = size
    zoom = clamp_zoom((view or {}).get("zoom", 1.0))
    focus = (view or {}).get("focus") or CROP_FOCUS[mode]
    # A cover smaller than the frame leaves room around it, which takes the card's
    # own band colour rather than black: it belongs to the card, not to the photo.
    # The frame sits at `focus` of the room the enlarged cover leaves around it.
    chain = (
        f"{_scale_chain(width, height, zoom)},"
        f"pad='max(iw,{width})':'max(ih,{height})':(ow-iw)/2:(oh-ih)/2:color={_hex(band)},"
        f"crop={width}:{height}:(iw-{width})*{focus[0]}:(ih-{height})*{focus[1]}"
    )
    _run(
        [ffmpeg, "-y", "-i", str(source), "-vf", chain, "-frames:v", "1", str(dest)],
        f"could not crop {source.name} to {width}x{height}",
    )


def _flatten(ffmpeg: str, png: Path, negative: bool = False) -> None:
    """Reduce one finished picture in place, so it holds no grey the screen lacks."""
    flat = png.with_suffix(".flat.png")
    _run(
        [ffmpeg, "-y", "-i", str(png), "-vf", _grey_chain(negative), "-frames:v", "1", str(flat)],
        f"could not flatten {png.name} to {GREY_LEVELS} greys",
    )
    flat.replace(png)


PREVIEW_SIZE = (160, 120)


def preview_size(player: str, width: int = PREVIEW_SIZE[0]) -> tuple[int, int]:
    """The crop preview, at the ratio of the player chosen."""
    scene, _ = screen(player)
    return width, round(width * scene[1] / scene[0])


def cover_fit(
    source: Path,
    size: tuple[int, int],
    zoom: float = 1.0,
    style: str = "colour",
    negative: bool = False,
) -> tuple[bytes, tuple[int, int]]:
    """The cover scaled to fill `size` and enlarged by `zoom`, and the picture's size.

    The crop frame sits on this picture and shows only what falls inside it, which
    is what the web page's canvas does; a desktop Canvas does the same with one
    image item. The picture is drawn once per zoom, so dragging stays instant.

    `style` is the screen the preview is being read against: a grey one gets the
    cover already flattened, so what the frame shows is what that card will carry.
    """
    ffmpeg = require_ffmpeg()
    chain = _scale_chain(*size, clamp_zoom(zoom))
    if style == "bw":
        chain += f",{_grey_chain(negative)}"
    with tempfile.TemporaryDirectory(prefix="daisy2telmi-") as tmp:
        out = Path(tmp) / "fit.png"
        _run(
            [
                ffmpeg,
                "-y",
                "-i",
                str(source),
                "-vf",
                chain,
                "-frames:v",
                "1",
                str(out),
            ],
            f"could not read {source.name}",
        )
        png = out.read_bytes()
    return png, _png_size(png)


def _png_size(png: bytes) -> tuple[int, int]:
    """The width and height a PNG holds: its header sits at a fixed offset."""
    return struct.unpack(">II", png[16:24])


def _overlay(ffmpeg: str, base: Path, overlay: Path, dest: Path, x: int, y: int) -> None:
    _run(
        [
            ffmpeg,
            "-y",
            "-i",
            str(base),
            "-i",
            str(overlay),
            "-filter_complex",
            f"overlay={x}:{y}",
            "-frames:v",
            "1",
            str(dest),
        ],
        f"could not draw on {dest.name}",
    )


def write_cover(
    source: Path,
    out_dir: Path,
    labels: Sequence[str],
    texts: Sequence[str],
    crop: str = "center",
    style: str = "colour",
    scene: tuple[int, int] = IMAGE_SIZE,
    cover_size: tuple[int, int] = COVER_SIZE,
    view: dict | None = None,
    negative: bool = False,
) -> None:
    """`title.png`, `cover.png` and one card per part, all from `source`.

    `style` is the screen the pack is drawn for: `bw` flattens the picture to the
    sixteen Lunii greys and turns the card inside out, white ground and black ink.
    `negative` swaps light and dark once the card is whole, which reads as the
    negative of an old film. `scene` and `cover_size` are that player's own sizes.

    Every card shows the cover across the whole screen, with the number and the
    opening words laid over it. A colour card of a photograph weighs far more than
    a grey one of the same cover: the greys flatten into a few palette entries and
    a photograph does not, and a book cut to the sentence level is where that shows.
    """
    ffmpeg = require_ffmpeg()
    images = out_dir / "images"
    images.mkdir(parents=True, exist_ok=True)
    # The band is painted before the flattening, so a negative card inverts it with
    # the rest of the picture: asking for the inverted colour here would flip it twice.
    band = _palette(style)["band"]

    with tempfile.TemporaryDirectory(prefix="daisy2telmi-") as tmp:
        scratch = Path(tmp)
        base = scratch / "base.png"
        square = scratch / "square.png"
        card_base = scratch / "card.png"
        _crop(ffmpeg, source, scene, base, crop, view, band)
        _crop(ffmpeg, source, cover_size, square, crop, view, band)
        # The whole cover behind the lettering. A grey card is flattened with the
        # lettering, once: flattening twice would push every grey up a level.
        shutil.copyfile(base, card_base)

        for index, (label, text) in enumerate(zip(labels, texts)):
            layer = scratch / f"layer{index}.png"
            layer.write_bytes(card_layer_png(label, text, *scene, style=style))
            _overlay(ffmpeg, card_base, layer, images / f"m{index}.png", 0, 0)
            if style == "bw":
                # Flattened last, once the compositing is done, so a card cannot
                # pick up a grey the screen lacks where two layers meet.
                _flatten(ffmpeg, images / f"m{index}.png", negative)
        if style == "bw":
            _flatten(ffmpeg, base, negative)
            _flatten(ffmpeg, square, negative)

        shutil.copyfile(base, out_dir / TITLE_IMAGE)
        shutil.copyfile(square, out_dir / COVER_IMAGE)
