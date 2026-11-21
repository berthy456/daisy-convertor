"""Checks a STUdio archive the way a Lunii's tools read it.

Port of `StudioStory` in Lunii.QT's `pkg/api/stories.py`, including the `ni`,
`ri`, `si` and `li` index it builds from `story.json` and what the device then
expects to find. Used by the tests, never shipped.
"""

from __future__ import annotations

import json
import re
import zipfile

NODE_SIZE = 0x2C
NI_HEADER_SIZE = 0x200
ASSET_NAME = re.compile(r"^[0-9A-F]{8}\.(png|mp3|jpg|jpeg|bmp|gif|webp|ogg)$")

# What a `.plain.pk` must hold: the three plain tables, which is how Lunii.QT
# recognises one, and the uuid, without which it cannot be put on a card.
PK_ENTRIES = ("ni", "li.plain", "ri.plain", "si.plain", "uuid.bin")
RECORD_SIZE = 12


def index_story(story: dict) -> dict:
    """The tables Lunii.QT builds while loading `story.json`."""
    snodes = story.get("stageNodes") or []
    anodes = story.get("actionNodes") or []

    ri: dict[str, int] = {}
    si: dict[str, int] = {}
    for node in snodes:
        for table, key in ((ri, "image"), (si, "audio")):
            name = node.get(key)
            if name and name not in table:
                table[name] = len(table)

    li: list[int] = []
    absolute = 0
    for anode in anodes:
        anode["global_index"] = absolute
        absolute += len(anode.get("options") or [])
        for option in anode.get("options") or []:
            li.append(next((i for i, n in enumerate(snodes) if n.get("uuid") == option), -1))

    return {"snodes": snodes, "anodes": anodes, "ri": ri, "si": si, "li": li}


def ni_bytes(story: dict) -> bytes:
    """The `ni` file the device reads: a 0x200 header then one 0x2C node each."""
    tables = index_story(story)
    snodes, ri, si = tables["snodes"], tables["ri"], tables["si"]

    header = b""
    header += int(str(story.get("format", "v1"))[1:]).to_bytes(2, "little")
    header += int(story.get("version") or 0).to_bytes(2, "little")
    header += NI_HEADER_SIZE.to_bytes(4, "little")
    header += NODE_SIZE.to_bytes(4, "little")
    header += len(snodes).to_bytes(4, "little")
    header += len(ri).to_bytes(4, "little")
    header += len(si).to_bytes(4, "little")
    header += b"\x01"
    header += b"\x00" * (NI_HEADER_SIZE - len(header))

    def signed(value: int) -> bytes:
        return int(value).to_bytes(4, "little", signed=True)

    def move(node: dict, field: str) -> bytes:
        transition = node.get(field)
        if not transition:
            return b"\xff\xff\xff\xff" * 3
        anode = next((a for a in tables["anodes"] if a.get("id") == transition["actionNode"]), None)
        return signed(anode["global_index"]) + signed(len(anode["options"])) + signed(transition["optionIndex"])

    body = b""
    for node in snodes:
        controls = node["controlSettings"]
        current = signed(ri.get(node.get("image"), -1))
        current += signed(si.get(node.get("audio"), -1))
        current += move(node, "okTransition")
        current += move(node, "homeTransition")
        for key in ("wheel", "ok", "home", "pause", "autoplay"):
            current += int(bool(controls.get(key))).to_bytes(2, "little")
        current += b"\x00\x00"
        if len(current) > NODE_SIZE:
            raise ValueError(f"a node is {len(current)} bytes, over the {NODE_SIZE} the device allows")
        body += current + b"\xaa" * (NODE_SIZE - len(current))
    return header + body


def validate_studio_zip(path) -> list[str]:
    """Every way a STUdio archive can fail to be a story a Lunii will take."""
    problems: list[str] = []
    try:
        archive = zipfile.ZipFile(path)
    except (OSError, zipfile.BadZipFile) as exc:
        return [f"not a zip archive: {exc}"]

    with archive:
        names = archive.namelist()
        if "story.json" not in names:
            return ["story.json missing from the archive"]
        try:
            story = json.loads(archive.read("story.json").decode("utf-8"))
        except (ValueError, UnicodeDecodeError) as exc:
            return [f"story.json does not parse: {exc}"]

        inside = set(names)
        tables = index_story(story)
        snodes = tables["snodes"]
        if not snodes:
            problems.append("story.json has no stageNodes")
        elif snodes[0].get("type") != "cover":
            problems.append("the first stageNode is not the cover")

        for table, folder, key in ((tables["ri"], "assets", "image"), (tables["si"], "assets", "audio")):
            for name in table:
                asset = f"{folder}/{name}"
                if asset not in inside:
                    problems.append(f"{key} is missing from the archive: {asset}")
                elif not ASSET_NAME.match(name):
                    problems.append(f"{key} is not named like a STUdio asset: {name}")

        known_actions = {a.get("id") for a in tables["anodes"]}
        for node in snodes:
            label = node.get("name") or node.get("uuid")
            for field in ("okTransition", "homeTransition"):
                transition = node.get(field)
                if transition and transition.get("actionNode") not in known_actions:
                    problems.append(f"{label}.{field} points at an unknown action {transition.get('actionNode')}")
                if transition and not isinstance(transition.get("optionIndex"), int):
                    problems.append(f"{label}.{field} has no optionIndex")
            controls = node.get("controlSettings") or {}
            missing = {"wheel", "ok", "home", "pause", "autoplay"} - set(controls)
            if missing:
                problems.append(f"{label} has no controlSettings for {sorted(missing)}")
            # A lit OK with nothing behind it stops the device with an SD card error.
            if controls.get("ok") and not node.get("okTransition"):
                problems.append(f"{label} lights OK with no transition behind it")

        for anode in tables["anodes"]:
            if not anode.get("options"):
                problems.append(f"action {anode.get('id')} has no option")
            if any(index < 0 for index in _option_indexes(anode, snodes)):
                problems.append(f"action {anode.get('id')} points at a stage that is not in the story")

        start = snodes[0].get("okTransition") if snodes else None
        if not start:
            problems.append("the cover has no okTransition, so the story cannot be started")
        elif start.get("actionNode") not in known_actions:
            problems.append(f"the cover starts an unknown action {start.get('actionNode')}")

        ni = ni_bytes(story)
        if len(ni) != NI_HEADER_SIZE + NODE_SIZE * len(snodes):
            problems.append(f"ni is {len(ni)} bytes, not header plus {len(snodes)} node(s)")
        if len(tables["li"]) < 8 // 4:
            problems.append("li is shorter than the device pads it to")

    return problems


def _option_indexes(anode: dict, snodes: list[dict]) -> list[int]:
    return [next((i for i, n in enumerate(snodes) if n.get("uuid") == option), -1) for option in anode.get("options") or []]


def _u16(data: bytes, at: int) -> int:
    return int.from_bytes(data[at : at + 2], "little")


def _u32(data: bytes, at: int) -> int:
    return int.from_bytes(data[at : at + 4], "little")


def _i32(data: bytes, at: int) -> int:
    return int.from_bytes(data[at : at + 4], "little", signed=True)


def records(block: bytes) -> list[str]:
    """`ri`/`si` as their twelve-character `000\\XXXXXXXX` names."""
    return [block[at : at + RECORD_SIZE].decode("ascii", "replace") for at in range(0, len(block), RECORD_SIZE)]


def rle4(bmp: bytes) -> tuple[int, int, list[int]]:
    """`(width, height, levels)` of a 4 bpp RLE4 bitmap: what its runs actually draw.

    `levels` is the palette index of every pixel, top line first, which is what the
    device turns back into a picture.
    """
    width, height, levels, at = _u32(bmp, 18), _u32(bmp, 22), [], _u32(bmp, 10)
    while at + 1 < len(bmp):
        count, pair = bmp[at], bmp[at + 1]
        at += 2
        if count:
            levels.extend([pair >> 4] * count)
        elif pair == 2:
            at += 2
        elif pair == 1:
            break
        elif pair >= 3:
            span = (pair + 1) // 2
            for byte in bmp[at : at + span]:
                levels.extend((byte >> 4, byte & 0xF))
            at += span + (span % 2)
    return width, height, levels


def palette(bmp: bytes) -> list[tuple[int, int, int]]:
    """The bitmap's own colours as red green blue, in the order its indices pick.

    Every picture carries its own set, so an index is only a colour together with
    the entry it points at.
    """
    used = _u32(bmp, 46) or 1 << _u16(bmp, 28)
    return [(bmp[at + 2], bmp[at + 1], bmp[at]) for at in range(54, 54 + 4 * used, 4)]


def validate_plain_pk(path, night_mode: bool | None = None) -> list[str]:
    """Every way a `.plain.pk` can fail to be a story the device will take.

    These are the checks Lunii.QT's `import_lunii_plain` and open-story-teller's
    `ni_parser` make: the plain tables and the uuid are there, and every index the
    nodes hold stays inside the table it points at.
    """
    try:
        archive = zipfile.ZipFile(path)
    except (OSError, zipfile.BadZipFile) as exc:
        return [f"not a zip archive: {exc}"]

    problems: list[str] = []
    with archive:
        names = archive.namelist()
        missing = [entry for entry in PK_ENTRIES if entry not in names]
        if missing:
            return [f"missing from the archive: {', '.join(missing)}"]
        if len(archive.read("uuid.bin")) != 16:
            problems.append("uuid.bin is not the 16 bytes a uuid is")
        if night_mode is not None and ("nm" in names) != night_mode:
            problems.append(f"night mode was {'offered' if night_mode else 'refused'}, and the archive says otherwise")

        ni = archive.read("ni")
        if len(ni) < NI_HEADER_SIZE:
            return problems + [f"ni is {len(ni)} bytes, shorter than its {NI_HEADER_SIZE}-byte header"]
        _, _, header_size, node_size, nodes, ri_count, si_count = (
            _u16(ni, 0),
            _u16(ni, 2),
            _u32(ni, 4),
            _u32(ni, 8),
            _u32(ni, 12),
            _u32(ni, 16),
            _u32(ni, 20),
        )
        if header_size != NI_HEADER_SIZE:
            problems.append(f"ni says its header is {header_size} bytes, not {NI_HEADER_SIZE}")
        if node_size != NODE_SIZE:
            problems.append(f"ni says a node is {node_size} bytes, not {NODE_SIZE}")
        if len(ni) != header_size + node_size * nodes:
            problems.append(f"ni is {len(ni)} bytes, not {header_size} plus {nodes} node(s) of {node_size}")

        ri, si = records(archive.read("ri.plain")), records(archive.read("si.plain"))
        for table, label, count in ((ri, "ri", ri_count), (si, "si", si_count)):
            if len(table) != count:
                problems.append(f"{label} holds {len(table)} record(s), but ni counts {count}")
        for table, label, folder, extension in ((ri, "ri", "rf", "bmp"), (si, "si", "sf", "mp3")):
            for record in table:
                if not record.startswith("000\\") or len(record) != RECORD_SIZE:
                    problems.append(f"{label} record {record!r} is not a 12-character 000\\ name")
                    continue
                media = f"{folder}/000/{record[4:]}.{extension}"
                if media not in names:
                    problems.append(f"{label} names {record}, which is not in the archive as {media}")

        block = archive.read("li.plain")
        li = [_i32(block, at) for at in range(0, len(block) - len(block) % 4, 4)]

        for index in range(nodes):
            at = header_size + index * node_size
            for label, value, count in (("ri", _i32(ni, at), ri_count), ("si", _i32(ni, at + 4), si_count)):
                if value != -1 and not 0 <= value < count:
                    problems.append(f"node {index} points at {label} {value}, which is not one of {count}")
            for field, base in (("ok", at + 8), ("home", at + 20)):
                start, length, pick = _i32(ni, base), _i32(ni, base + 4), _i32(ni, base + 8)
                if (start, length, pick) == (-1, -1, -1):
                    continue
                if start < 0 or length <= 0 or start + length > len(li):
                    problems.append(f"node {index}.{field} reads li {start}..{start + length}, past its {len(li)}")
                    continue
                if not 0 <= pick < length:
                    problems.append(f"node {index}.{field} picks option {pick} of {length}")
                for entry in li[start : start + length]:
                    if not 0 <= entry < nodes:
                        problems.append(f"node {index}.{field} leads to stage {entry}, which is not one of {nodes}")
            if _u16(ni, at + 34) and (_i32(ni, at + 8), _i32(ni, at + 12), _i32(ni, at + 16)) == (-1, -1, -1):
                problems.append(f"node {index} lights OK with no transition behind it")

        for record in ri:
            media = f"rf/000/{record[4:]}.bmp"
            if media not in names:
                continue
            bmp = archive.read(media)
            if bmp[:2] != b"BM" or _u16(bmp, 28) != 4 or _u32(bmp, 30) != 2:
                problems.append(f"{media} is not a 4 bpp RLE4 bitmap")
                continue
            width, height, levels = rle4(bmp)
            if len(levels) != width * height:
                problems.append(f"{media} draws {len(levels)} pixels, not the {width}x{height} its header says")

        for record in si:
            media = f"sf/000/{record[4:]}.mp3"
            if media in names and not archive.read(media)[:3].startswith((b"\xff", b"ID3")):
                problems.append(f"{media} does not start with an MP3 frame")

    return problems
