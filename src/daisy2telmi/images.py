"""Scene images: placeholders, and a user's cover cropped into Telmi's sizes.

ffmpeg scales, crops and composites. Every word — a card's number, a card's opening
words, a whole sentence — is drawn with the typeface bundled in assets. Languages
whose script that typeface cannot write (Chinese, Arabic) get the number on its
own, which needs no letters to be legible.
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


MARGIN = 24
CARD_LINES = 3
CARD_WORDS = 20
# The card's two lettering sizes, as fractions of its screen's height, and the air
# between two lines of its words. The web page sizes its cards the same way, so the
# same part is the same card on both.
CARD_NUMBER_SIZE = 11
CARD_WORD_SIZE = 17
CARD_WORD_PITCH = 1.3
# A digit's ink is about three quarters of the font size, in any of them.
DIGIT_INK = 0.72


def _layout(width: int) -> tuple[int, int]:
    """The margin and the height the cover keeps to itself.

    A smaller screen shrinks both by the same step, so a card keeps the same
    furniture whatever it is drawn for.
    """
    step = width / IMAGE_SIZE[0]
    return max(6, round(MARGIN * step)), width // 4 * 3 // 4


def _card_typeface(height: int) -> ImageFont.FreeTypeFont:
    """The typeface a card's opening words are written in."""
    return _typeface(max(1, round(height / CARD_WORD_SIZE)))


def _outline(size: int) -> int:
    """The weight of a letter's border: the same at every screen size."""
    return max(1, round(size / 7))


def card_words(text: str, width: int = IMAGE_SIZE[0], height: int = IMAGE_SIZE[1]) -> list[str]:
    """The opening words of a part, as up to three lines the card can show.

    The card's typeface has no letters for some scripts, so a part whose words it
    cannot write gets no lines at all: the number and the spoken snippet still
    place the listener.
    """
    font = _card_typeface(height)
    words = _letterable(font, text).split()[:CARD_WORDS]
    if not words:
        return []
    return _wrap(" ".join(words), font, width - 2 * _layout(width)[0])[:CARD_LINES]


def _number_card(drawn: ImageDraw.ImageDraw, label: str, width: int, height: int, palette: dict) -> None:
    """Letter a four-grey card: its number alone, as large as the screen allows.

    The Lunii screen sits behind a plastic window, so nothing drawn small is
    readable there: no word, no total and no opening words, only the number. It is
    sized as a three-digit one, so every card of a story carries a number of the
    same height and a story of a hundred parts fills the width of the screen.
    """
    margin = _layout(width)[0]
    base = max(1, round(height / CARD_NUMBER_SIZE))
    widest = max(_typeface(base).getlength(label), _typeface(base).getlength("888"))
    size = int(min(base * (width - 2 * margin) / widest, (height - 2 * margin) / DIGIT_INK))
    ink = round(size * DIGIT_INK)
    middle = height // 2
    drawn.rectangle(
        (0, middle - ink // 2 - margin, width - 1, middle + ink // 2 + margin),
        fill=_rgb(palette["background"]),
    )
    font = _typeface(size)
    # A typeface leaves room above and below the letters it draws, and the room is
    # not the same for every digit: the number is centred on what it actually inks.
    box = drawn.textbbox((width // 2, middle), label, font=font, anchor="mm")
    drawn.text(
        (width // 2, middle + middle - (box[1] + box[3]) // 2),
        label,
        font=font,
        fill=_rgb(palette["ink"]),
        stroke_width=max(4, round(size / 12)),
        stroke_fill=_rgb(palette["border"]),
        anchor="mm",
    )


def _lettering(label: str, text: str, width: int, height: int, style: str, negative: bool) -> Image.Image:
    """One card's lettering on a transparent canvas: its number, and the words it opens with.

    A colour card numbers itself top right, and its words start under the height the
    cover keeps to itself. A four-grey card is the Lunii's, and draws the number alone.
    """
    palette = _palette(style, negative)
    canvas = Image.new("RGBA", (width, height), (0, 0, 0, 0))
    drawn = ImageDraw.Draw(canvas)
    margin = _layout(width)[0]
    if style == "bw":
        _number_card(drawn, label, width, height, palette)
        return canvas

    number_size = max(1, round(height / CARD_NUMBER_SIZE))
    room = width - 2 * margin
    wide = _typeface(number_size).getlength(label)
    if wide > room:
        # A titled line is drawn smaller than a bare number, never smaller than the
        # card's own opening words: below that the number stops being the thing a
        # listener reads on a card.
        number_size = max(max(1, round(height / CARD_WORD_SIZE)), int(number_size * room / wide))
    drawn.text(
        (width - margin, margin),
        label,
        font=_typeface(number_size),
        fill=_rgb(palette["ink"]),
        stroke_width=_outline(number_size),
        stroke_fill=_rgb(palette["border"]),
        anchor="ra",
    )
    font = _card_typeface(height)
    pitch = round(font.size * CARD_WORD_PITCH)
    top = margin + _layout(width)[1] + margin
    for offset, line in enumerate(card_words(text, width, height)):
        drawn.text(
            (margin, top + offset * pitch),
            line,
            font=font,
            fill=_rgb(palette["ink"]),
            stroke_width=_outline(font.size),
            stroke_fill=_rgb(palette["border"]),
            anchor="la",
        )
    return canvas


# A letter edge is cut from a mask rather than kept as drawn: a grey card has sixteen
# levels, and the half-greys a soft edge is made of are not among them.
_HARD_EDGE = [0 if level < 128 else 255 for level in range(256)]


def _hard_lettering(lettering: Image.Image) -> Image.Image:
    """The lettering cut back to the card's own colours, in its ink and its ground and
    its edges alike: a grey card has sixteen levels, and the half-greys a soft letter
    edge is made of are not among them.

    Drawing a letter blends its colour into whatever it lands on, so the blend has to
    be cut on the colour as much as on the coverage — a hard edge over soft ink is
    still soft ink.
    """
    return Image.merge("RGBA", [lettering.getchannel(band).point(_HARD_EDGE) for band in "RGBA"])


def card_png(
    label: str,
    text: str = "",
    width: int = IMAGE_SIZE[0],
    height: int = IMAGE_SIZE[1],
    style: str = "colour",
    negative: bool = False,
) -> bytes:
    """A card on its own: the part number, and the words it opens with when we can draw them."""
    lettering = _lettering(label, text, width, height, style, negative)
    if style == "bw":
        lettering = _hard_lettering(lettering)
    canvas = Image.new("RGB", (width, height), _rgb(_palette(style, negative)["background"]))
    canvas.paste(lettering, (0, 0), lettering)
    return _photo_png(canvas)


def card_layer_png(
    label: str,
    text: str = "",
    width: int = IMAGE_SIZE[0],
    height: int = IMAGE_SIZE[1],
    style: str = "colour",
    negative: bool = False,
) -> bytes:
    """The same lettering on a transparent canvas, to lay over a cropped cover."""
    lettering = _lettering(label, text, width, height, style, negative)
    return _photo_png(_hard_lettering(lettering) if style == "bw" else lettering)


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
    language_code: str,
    number: int,
    total: int,
    word: bool = True,
    show_total: bool = True,
    title: str = "",
    screen: tuple[int, int] = IMAGE_SIZE,
) -> str:
    """The words drawn on a card, in the story's language.

    The typeface is Latin, so Chinese and Arabic cards carry the number alone: it
    is the part that has to be read, and the spoken card already says the word.
    A part cut below chapter level gets the number alone as well, because none
    of the words we have would be the right one for it. A title is added after the
    number, and a card keeps its number alone when its typeface has no letters for
    the title, or no room for it at the size of the card's own opening words.
    """
    width, height = screen
    shown = f"{number}/{total}" if show_total else str(number)
    label = f"{language(language_code).card.upper()} {shown}" if word else shown
    font = _card_typeface(height)
    if title:
        named = f"{label}, {title.upper()}"
        room = width - 2 * _layout(width)[0]
        small = _typeface(max(1, round(height / CARD_WORD_SIZE)))
        if _letterable(font, named) == named and small.getlength(named) <= room:
            return named
    return label if _letterable(font, label) == label else shown


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
