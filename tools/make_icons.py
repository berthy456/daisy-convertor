"""Redraw the icon files from the SVGs in assets/.

    python tools/make_icons.py

Writes the PNGs the window draws — `icon.png` for the window itself, `icon-112.png`
for its bar, `icon-64.png` for the AppImage and `icon-16.png` for its status line —
into
`src/daisy2telmi/assets`, where they travel inside the binaries, plus
`assets/icon.icns` and `assets/icon.ico` for PyInstaller. They are committed,
because the build machines — and CI in particular — have no SVG rasteriser; run
this once after changing the SVG. Tk draws a photo image at its own size and has
no way to shrink one, so each size the window asks for is its own file.

The four `pick-*.png` are the marks on the buttons that pick the book, a folder of
packs, the export folder and the story-teller's story folder.

Needs `rsvg-convert` on PATH, and `iconutil`, which only macOS ships. Without
iconutil the .icns is skipped and the other two are still written.
"""

from __future__ import annotations

import shutil
import struct
import subprocess
import sys
import tempfile
import zlib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SVG = ROOT / "assets" / "icon.svg"
# The PNGs live inside the package, so a pip install carries them and a frozen
# binary unpacks them next to the module that reads them; the two container formats
# are only ever read by PyInstaller, so they stay at the top.
ASSETS = ROOT / "src" / "daisy2telmi" / "assets"
PNGS = (("icon.png", 128), ("icon-112.png", 112), ("icon-64.png", 64), ("icon-16.png", 16))
# The mark each pick button carries: the book, a folder of packs, the export folder,
# the story-teller.
PICKS = ("pick-book", "pick-folder", "pick-export", "pick-player")
PICK_MARK = 18
OUT = ROOT / "assets"

# The .ico keeps small sizes as raw bitmaps, which every Windows shell reads, and
# the large one as a PNG, which Vista and later do and which stays small.
ICO_BITMAP_SIZES = (16, 32, 48)
ICO_PNG_SIZES = (256,)
# iconutil fills a .iconset, and only accepts its own names.
ICNS_PNGS = (
    ("icon_16x16.png", 16),
    ("icon_16x16@2x.png", 32),
    ("icon_32x32.png", 32),
    ("icon_32x32@2x.png", 64),
    ("icon_128x128.png", 128),
    ("icon_128x128@2x.png", 256),
    ("icon_256x256.png", 256),
    ("icon_256x256@2x.png", 512),
    ("icon_512x512.png", 512),
    ("icon_512x512@2x.png", 1024),
)


def rasterise(size: int, svg: Path = SVG) -> bytes:
    result = subprocess.run(
        ["rsvg-convert", "-w", str(size), "-h", str(size), str(svg), "-f", "png"],
        capture_output=True,
    )
    if result.returncode:
        raise SystemExit(f"rsvg-convert failed: {result.stderr.decode(errors='replace')}")
    return result.stdout


def decode_png(png: bytes) -> tuple[int, int, bytearray]:
    """The RGBA pixels of a non-interlaced 8-bit PNG, which is what rsvg writes."""
    if png[:8] != b"\x89PNG\r\n\x1a\n":
        raise SystemExit("rsvg-convert did not write a PNG")
    width = height = 0
    parts = []
    at = 8
    while at < len(png):
        length = struct.unpack_from(">I", png, at)[0]
        kind = png[at + 4 : at + 8]
        body = png[at + 8 : at + 8 + length]
        if kind == b"IHDR":
            width, height, depth, colour = struct.unpack(">IIBB", body[:10])
            if (depth, colour, png[at + 12]) != (8, 6, 0):
                raise SystemExit("the PNG is not 8-bit RGBA non-interlaced")
        elif kind == b"IDAT":
            parts.append(body)
        elif kind == b"IEND":
            break
        at += 12 + length

    raw = zlib.decompress(b"".join(parts))
    pixels = bytearray(width * height * 4)
    stride = width * 4
    for y in range(height):
        line = raw[y * (stride + 1) : (y + 1) * (stride + 1)]
        filter_type, data = line[0], bytearray(line[1:])
        above = pixels[y * stride : (y + 1) * stride]
        for x in range(stride):
            left = data[x - 4] if x >= 4 else 0
            up = above[x]
            upleft = above[x - 4] if x >= 4 else 0
            if filter_type == 1:
                data[x] = (data[x] + left) & 0xFF
            elif filter_type == 2:
                data[x] = (data[x] + up) & 0xFF
            elif filter_type == 3:
                data[x] = (data[x] + ((left + up) >> 1)) & 0xFF
            elif filter_type == 4:
                p = left + up - upleft
                pa, pb, pc = abs(p - left), abs(p - up), abs(p - upleft)
                predictor = left if (pa <= pb and pa <= pc) else (up if pb <= pc else upleft)
                data[x] = (data[x] + predictor) & 0xFF
        pixels[y * stride : (y + 1) * stride] = data
    return width, height, pixels


def ico_entry_bitmap(size: int) -> bytes:
    """A 32-bit BGRA entry: the header says twice the height, and an empty AND
    mask follows, which is how an .ico carries transparency."""
    _, _, pixels = decode_png(rasterise(size))
    rows = []
    for y in range(size - 1, -1, -1):
        row = bytearray(size * 4)
        for x in range(size):
            at = (y * size + x) * 4
            row[at : at + 4] = bytes((pixels[at + 2], pixels[at + 1], pixels[at], pixels[at + 3]))
        rows.append(bytes(row))
    mask_row = ((size + 31) // 32) * 4
    mask = b"\x00" * (mask_row * size)
    header = struct.pack("<IiiHHIIiiII", 40, size, size * 2, 1, 32, 0, 0, 0, 0, 0, 0)
    return header + b"".join(rows) + mask


def write_ico(path: Path) -> None:
    entries = [(size, ico_entry_bitmap(size)) for size in ICO_BITMAP_SIZES]
    entries += [(size, rasterise(size)) for size in ICO_PNG_SIZES]
    # The directory names 256 as zero, and every image starts on a four-byte edge.
    directory, images = b"", b""
    offset = 6 + 16 * len(entries)
    for size, image in entries:
        edge = (-offset) % 4
        offset += edge
        side = 0 if size >= 256 else size
        directory += struct.pack("<BBBBHHII", side, side, 0, 0, 1, 32, len(image), offset)
        images += b"\x00" * edge + image
        offset += len(image)
    path.write_bytes(struct.pack("<HHH", 0, 1, len(entries)) + directory + images)
    print(f"wrote {path} ({path.stat().st_size} bytes, {', '.join(str(s) for s, _ in entries)} px)")


def write_icns(path: Path) -> None:
    if shutil.which("iconutil") is None:
        print("no iconutil on this machine; the .icns was not written")
        return
    with tempfile.TemporaryDirectory() as scratch:
        iconset = Path(scratch) / "icon.iconset"
        iconset.mkdir()
        for name, size in ICNS_PNGS:
            (iconset / name).write_bytes(rasterise(size))
        result = subprocess.run(["iconutil", "-c", "icns", str(iconset), "-o", str(path)], capture_output=True, text=True)
        if result.returncode:
            raise SystemExit(f"iconutil failed: {result.stderr.strip()}")
    print(f"wrote {path} ({path.stat().st_size} bytes)")


def main() -> int:
    if not SVG.is_file():
        print(f"error: no SVG at {SVG}", file=sys.stderr)
        return 1
    if shutil.which("rsvg-convert") is None:
        print("error: rsvg-convert is not on PATH", file=sys.stderr)
        return 1

    ASSETS.mkdir(parents=True, exist_ok=True)
    for name, size in PNGS:
        (ASSETS / name).write_bytes(rasterise(size))
        print(f"wrote {ASSETS / name} ({size} px)")
    for name in PICKS:
        png = ASSETS / f"{name}-{PICK_MARK}.png"
        png.write_bytes(rasterise(PICK_MARK, OUT / f"{name}.svg"))
        print(f"wrote {png} ({PICK_MARK} px)")
    write_ico(OUT / "icon.ico")
    write_icns(OUT / "icon.icns")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
