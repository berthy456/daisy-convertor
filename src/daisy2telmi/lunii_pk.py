"""Write the pack a Lunii keeps on its own card: a `.plain.pk`.

A STUdio archive is the friendly form of the same story. Here the graph is
flattened into fixed-size records and the media is renamed to the eight hex
characters the device uses. `.plain.pk` is that layout zipped up *before* the
device ciphers it, so it needs no key: `ni`, `li`, `ri` and `si` stay plain, and
Lunii.QT does the ciphering when it writes the story onto a card.
"""

from __future__ import annotations

import json
import re
import subprocess
import struct
import tempfile
import zipfile
from pathlib import Path
from uuid import UUID

from . import audio
from .lunii import IMAGE_EXTS, LuniiError, _media, build_story_json

FORMAT_VERSION = 1
HEADER_SIZE = 0x200
NODE_SIZE = 0x2C
SCREEN = (320, 240)
# Four bits per pixel: every picture carries its own sixteen colours. The
# published packs fit sixteen greys to each one; a colour card keeps its colour.
COLORS = 16

# The device's own way of saying "there is nowhere to go from here".
NO_MOVE = -1

# The three entries a `.plain.pk` carries that never reach the card: the story's
# identity, and the title and picture Lunii.QT lists it under.
FILE_UUID = "uuid.bin"
FILE_META = "_metadata.json"
FILE_THUMB = "_thumbnail.png"


def _records(stems: list[str]) -> bytes:
    """`ri`/`si`: twelve characters per media file, `000\\` then eight hex."""
    return "".join(f"000\\{stem}" for stem in stems).encode("ascii")


def _ffmpeg(args: list[str], what: str) -> bytes:
    """Run ffmpeg, and say which picture failed when it does not."""
    result = subprocess.run(args, capture_output=True)
    if result.returncode != 0:
        tail = result.stderr.decode("utf-8", "replace").strip().splitlines()
        raise LuniiError(f"{what}: {tail[-1] if tail else 'ffmpeg failed'}")
    return result.stdout


def _pal8(block: bytes, size: tuple[int, int]) -> tuple[bytes, list[tuple[int, int, int]]]:
    """The pixels and the colours of ffmpeg's `pal8` output.

    The rawvideo muxer writes the frame's palette after its pixels, as blue green
    red; the index each pixel holds points into it.
    """
    width, height = size
    if len(block) < width * height + 4:
        raise LuniiError(f"the Lunii picture came back {len(block)} bytes, not {width * height}")
    return block[: width * height], [
        (rgb[2], rgb[1], rgb[0]) for rgb in struct.iter_unpack("4B", block[width * height :])
    ]


def bitmap_rle4(source: Path, size: tuple[int, int] = SCREEN) -> bytes:
    """The 320x240, four-bits-per-pixel, RLE4 bitmap the Lunii draws.

    Each picture brings its own sixteen colours, which is what the published packs
    do: ffmpeg fits the palette to this one picture and maps the picture onto it,
    so a colour card stays in colour and a grey one stays grey. The runs are how
    the device stores the result, one line at a time, top line first.
    """
    width, height = size
    ffmpeg = audio.require_ffmpeg()
    with tempfile.TemporaryDirectory(prefix="daisy2telmi-pk-") as work:
        palette = Path(work) / "palette.png"
        _ffmpeg(
            [
                ffmpeg,
                "-hide_banner",
                "-loglevel",
                "error",
                "-y",
                "-i",
                str(source),
                "-vf",
                f"scale={width}:{height},palettegen=max_colors={COLORS}",
                str(palette),
            ],
            f"could not find the colours of {Path(source).name}",
        )
        pixels = _ffmpeg(
            [
                ffmpeg,
                "-hide_banner",
                "-loglevel",
                "error",
                "-i",
                str(source),
                "-i",
                str(palette),
                "-lavfi",
                f"scale={width}:{height}[x];[x][1:v]paletteuse=dither=none",
                "-f",
                "rawvideo",
                "-pix_fmt",
                "pal8",
                "-",
            ],
            f"could not draw {Path(source).name} on the Lunii screen",
        )
        pixels, entries = _pal8(pixels, size)

    # `palettegen` keeps a colour for transparency next to the ones the picture
    # uses, so its indices are not simply the first sixteen: number the colours
    # this picture actually reaches for from zero, and store those.
    order = sorted(set(pixels))
    if len(order) > COLORS:
        raise LuniiError(f"{source.name} needs {len(order)} colours, more than a card's {COLORS}")
    slots = {index: number for number, index in enumerate(order)}
    pixels = bytes(slots[index] for index in pixels)
    colours = [entries[index] for index in order]
    # A flat picture needs one colour; the header still lists sixteen.
    colours += [colours[-1]] * (COLORS - len(colours))

    data = bytearray()
    for top in range(0, height * width, width):
        row = pixels[top : top + width]
        count, level = 1, row[0]
        for index in row[1:]:
            if index == level and count < 255:
                count += 1
                continue
            data += bytes((count, (level << 4) | level))
            count, level = 1, index
        data += bytes((count, (level << 4) | level))
        if top + width < len(pixels):
            data += b"\x00\x00"
    data += b"\x00\x01"

    offset = 54 + COLORS * 4
    head = struct.pack(
        "<2sI4xIIiiHHIIiiII",
        b"BM",
        offset + len(data),
        offset,
        40,
        width,
        height,
        1,
        4,
        2,
        len(data),
        0,
        0,
        # The published packs leave the count of colours implied by four bits per
        # pixel, and say all sixteen of them matter.
        0,
        COLORS,
    )
    # A BMP palette is blue, green, red; ffmpeg's is the other way round.
    palette_bytes = b"".join(struct.pack("4B", blue, green, red, 0) for red, green, blue in colours[:COLORS])
    return head + palette_bytes + bytes(data)


def _mp3(source: Path) -> bytes:
    """The audio as the device wants it: MP3, at least 44100 Hz, mono."""
    spec = audio.probe(source)
    if spec is not None and spec[0] == "mp3" and spec[1] >= audio.SAMPLE_RATE and spec[2] == "mono":
        return source.read_bytes()
    with tempfile.TemporaryDirectory() as work:
        dest = Path(work) / (source.stem + ".mp3")
        audio.convert_to_spec(source, dest)
        return dest.read_bytes()


def _graph(story: dict) -> tuple[list[int], dict[str, tuple[int, int]], dict[str, int]]:
    """`li`, where each action node starts in it, and where each stage sits in `ni`.

    An action node owns one unbroken run of stage indices; a stage's OK or home
    press is then `(start of that run, its length, which of its entries to take)`.
    """
    position = {node["uuid"]: index for index, node in enumerate(story["stageNodes"])}
    order: dict[str, tuple[int, int]] = {}
    li: list[int] = []
    for action in story["actionNodes"]:
        targets = [position[option] for option in action["options"]]
        if not targets:
            continue
        order[action["id"]] = (len(li), len(targets))
        li.extend(targets)
    return li, order, position


def _ni(story: dict, ri: dict[str, int], si: dict[str, int], order: dict[str, tuple[int, int]]) -> bytes:
    stages = story["stageNodes"]
    head = struct.pack(
        "<HHIIIII",
        FORMAT_VERSION,
        int(story.get("version") or 1),
        HEADER_SIZE,
        NODE_SIZE,
        len(stages),
        len(ri),
        len(si),
    )
    head += b"\x00" * (HEADER_SIZE - len(head))

    nodes = bytearray()
    for stage in stages:
        image = ri.get(stage["image"], NO_MOVE) if stage.get("image") else NO_MOVE
        sound = si.get(stage["audio"], NO_MOVE) if stage.get("audio") else NO_MOVE
        moves = []
        for field in ("okTransition", "homeTransition"):
            move = stage.get(field)
            if move and move["actionNode"] in order:
                start, count = order[move["actionNode"]]
                moves.extend((start, count, int(move.get("optionIndex") or 0)))
            else:
                moves.extend((NO_MOVE, NO_MOVE, NO_MOVE))
        controls = stage.get("controlSettings") or {}
        flags = [int(bool(controls.get(name))) for name in ("wheel", "ok", "home", "pause", "autoplay")]
        nodes += struct.pack("<8i6H", image, sound, *moves, *flags, 0)
    return head + bytes(nodes)


def pack_name(title: str, story_uuid: UUID) -> str:
    """`<Title>.<8 hex>.plain.pk`, the name the published packs use."""
    clean = re.sub(r"[^\w\- ]+", "", title, flags=re.UNICODE).strip()
    return f"{clean or 'story'}.{story_uuid.hex[24:].upper()}.plain.pk"


def write_pack(
    story_dir: Path,
    pk_path: Path | None = None,
    image_dir: Path | None = None,
) -> Path:
    """Write the `.plain.pk` of the Telmi story folder at `story_dir`.

    `image_dir` reads the pictures from a folder drawn for the Lunii's screen
    instead of the colour ones in the story folder.
    """
    story_dir = Path(story_dir)
    try:
        metadata = json.loads((story_dir / "metadata.json").read_text(encoding="utf-8"))
        nodes = json.loads((story_dir / "nodes.json").read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise LuniiError(f"{story_dir} is not a converted story: {exc}") from exc

    story, assets = build_story_json(metadata, nodes, story_dir, image_dir)
    story_uuid = UUID(str(metadata["uuid"]))

    images: list[tuple[str, Path]] = []
    sounds: list[tuple[str, Path]] = []
    ri: dict[str, int] = {}
    si: dict[str, int] = {}
    for asset, source in assets.items():
        stem = Path(asset).stem.upper()
        if Path(asset).suffix.lower() in IMAGE_EXTS:
            ri[asset] = len(images)
            images.append((stem, source))
        else:
            si[asset] = len(sounds)
            sounds.append((stem, source))

    li, order, _ = _graph(story)
    li_bytes = b"".join(index.to_bytes(4, "little", signed=True) for index in li)
    while len(li_bytes) < 8:
        li_bytes += b"\x00"

    pk_path = Path(pk_path) if pk_path else story_dir.parent / pack_name(
        story.get("title") or story_dir.name, story_uuid
    )
    pk_path.parent.mkdir(parents=True, exist_ok=True)

    thumbnail = _media(image_dir or story_dir, ".", metadata.get("image") or "cover.png", IMAGE_EXTS)
    # Stored, not deflated: the media is already MP3 and RLE4, and Lunii.QT reads
    # the archive straight onto the card.
    with zipfile.ZipFile(pk_path, "w", zipfile.ZIP_STORED) as archive:
        if story.get("nightModeAvailable"):
            archive.writestr("nm", b"")
        archive.writestr("li.plain", li_bytes)
        archive.writestr("ni", _ni(story, ri, si, order))
        archive.writestr("ri.plain", _records([stem for stem, _ in images]))
        archive.writestr("si.plain", _records([stem for stem, _ in sounds]))
        for stem, source in images:
            archive.writestr(f"rf/000/{stem}.bmp", bitmap_rle4(source))
        for stem, source in sounds:
            archive.writestr(f"sf/000/{stem}.mp3", _mp3(source))
        archive.writestr(FILE_UUID, story_uuid.bytes)
        archive.writestr(
            FILE_META,
            json.dumps(
                {
                    "uuid": str(story_uuid).upper(),
                    "title": story.get("title") or story_dir.name,
                    "description": story.get("description") or "",
                },
                ensure_ascii=False,
            ),
        )
        if thumbnail is not None:
            archive.writestr(FILE_THUMB, thumbnail.read_bytes())
    return pk_path
