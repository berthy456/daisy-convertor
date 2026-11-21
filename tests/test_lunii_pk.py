"""The Lunii `.plain.pk`: the same story in the layout the story card keeps."""

from __future__ import annotations

import json
import os
import zipfile
from pathlib import Path
from uuid import UUID

import pytest

from daisy2telmi import audio, images
from daisy2telmi.extractor import AudioSegment, Book, Chapter
from daisy2telmi.lunii import build_story_json
from daisy2telmi.lunii_pk import bitmap_rle4, pack_name, write_pack
from daisy2telmi.telmi_builder import build_metadata, build_nodes
from lunii_validator import NODE_SIZE, ni_bytes, palette, records, rle4, validate_plain_pk

SAMPLE = Path(
    os.environ.get(
        "LUNII_SAMPLE", "~/chatterbox/Les Aventures Intergalacbriques.72275E37.plain.pk"
    )
).expanduser()
requires_sample = pytest.mark.skipif(not SAMPLE.is_file(), reason=f"published Lunii pack missing: {SAMPLE}")

MP3 = b"\xff\xfb\x50\xc0" + b"\x00" * 200


@pytest.fixture()
def story(tmp_path: Path, monkeypatch) -> Path:
    """A converted Telmi folder: three parts, their media, and the two JSON files.

    The pictures are real, because the pack re-draws every one of them; the audio
    is a stub whose header is declared rather than read, so no encoder is needed.
    """
    monkeypatch.setattr(audio, "probe", lambda src: ("mp3", 44100, "mono"))
    card = images.placeholder_png(320, 240, "bw")
    book = Book(
        title="Wesh la fée",
        chapters=tuple(Chapter(f"Partie {k}", (AudioSegment(tmp_path / f"a{k}.mp3"),)) for k in range(3)),
    )
    directory = tmp_path / "story"
    (directory / "audios").mkdir(parents=True)
    (directory / "images").mkdir(parents=True)
    (directory / "title.mp3").write_bytes(MP3)
    (directory / "title.png").write_bytes(card)
    (directory / "cover.png").write_bytes(card)
    for k in range(3):
        (directory / "audios" / f"s{k}.mp3").write_bytes(MP3)
        (directory / "audios" / f"c{k}.mp3").write_bytes(MP3)
        (directory / "images" / f"m{k}.png").write_bytes(card)

    (directory / "metadata.json").write_text(json.dumps(build_metadata(book)), encoding="utf-8")
    nodes = build_nodes([f"s{k}" for k in range(3)], [f"c{k}.mp3" for k in range(3)])
    (directory / "nodes.json").write_text(json.dumps(nodes), encoding="utf-8")
    return directory


def read(directory: Path) -> tuple[dict, dict]:
    return (
        json.loads((directory / "metadata.json").read_text(encoding="utf-8")),
        json.loads((directory / "nodes.json").read_text(encoding="utf-8")),
    )


def test_the_pack_holds_the_layout_the_device_reads(tmp_path, story) -> None:
    pack = write_pack(story)
    with zipfile.ZipFile(pack) as archive:
        names = archive.namelist()
        pictures = [f"rf/000/{record[4:]}.bmp" for record in records(archive.read("ri.plain"))]
        sounds = [f"sf/000/{record[4:]}.mp3" for record in records(archive.read("si.plain"))]

    assert names[:5] == ["nm", "li.plain", "ni", "ri.plain", "si.plain"]
    assert "uuid.bin" in names
    assert [name for name in names if name.startswith("rf/")] == pictures
    assert [name for name in names if name.startswith("sf/")] == sounds


def test_the_pack_is_one_the_device_will_take(tmp_path, story) -> None:
    assert validate_plain_pk(write_pack(story), night_mode=True) == []


def test_our_ni_is_the_one_lunii_qt_writes(tmp_path, story) -> None:
    """Byte for byte what Lunii.QT builds from the STUdio archive of the same story.

    Only the byte after the counts differs: the published packs leave it at zero,
    Lunii.QT writes one, and nothing reads it back.
    """
    ours = zipfile.ZipFile(write_pack(story)).read("ni")
    theirs = ni_bytes(build_story_json(*read(story), story)[0])
    assert ours[:24] == theirs[:24]
    assert ours[25:] == theirs[25:]
    assert (ours[24], theirs[24]) == (0, 1)
    assert len(ours) == 0x200 + NODE_SIZE * 7


def test_the_pack_is_named_as_the_published_ones_are(tmp_path, story) -> None:
    pack = write_pack(story)
    uuid = UUID(str(read(story)[0]["uuid"]))
    assert pack.name == f"Wesh la fée.{uuid.hex[24:].upper()}.plain.pk"
    assert pack.parent == story.parent


def test_the_card_is_listed_under_its_own_title(tmp_path, story) -> None:
    """Lunii.QT reads these two to name the story, and keeps them off the card."""
    metadata = read(story)[0]
    with zipfile.ZipFile(write_pack(story)) as archive:
        listed = json.loads(archive.read("_metadata.json").decode("utf-8"))
        assert archive.read("_thumbnail.png") == (story / "cover.png").read_bytes()

    assert listed["uuid"] == str(metadata["uuid"]).upper()
    assert listed["title"] == "Wesh la fée"


def test_audio_the_device_cannot_play_is_re_encoded(tmp_path, story, monkeypatch) -> None:
    """A pack whose MP3s are stereo at 22050 Hz still gives the card 44100 Hz mono."""

    def not_the_spec(src):
        return ("mp3", 22050, "stereo")

    def re_encode(src, dest, filters=None):
        Path(dest).write_bytes(b"re-encoded")

    monkeypatch.setattr(audio, "probe", not_the_spec)
    monkeypatch.setattr(audio, "convert_to_spec", re_encode)
    with zipfile.ZipFile(write_pack(story)) as archive:
        sounds = [name for name in archive.namelist() if name.startswith("sf/")]
        assert all(archive.read(name) == b"re-encoded" for name in sounds)


def test_audio_the_device_can_play_is_left_alone(tmp_path, story, monkeypatch) -> None:
    monkeypatch.setattr(audio, "probe", lambda src: ("mp3", 44100, "mono"))
    with zipfile.ZipFile(write_pack(story)) as archive:
        sounds = [name for name in archive.namelist() if name.startswith("sf/")]
        assert all(archive.read(name) == MP3 for name in sounds)


def test_a_card_is_stored_the_way_the_device_draws_it(tmp_path) -> None:
    png = tmp_path / "card.png"
    png.write_bytes(images.placeholder_png(320, 240, "bw"))
    bmp = bitmap_rle4(png)

    assert bmp[:2] == b"BM"
    assert int.from_bytes(bmp[28:30], "little") == 4
    assert int.from_bytes(bmp[30:34], "little") == 2
    width, height, levels = rle4(bmp)
    assert (width, height) == (320, 240)
    assert len(levels) == 320 * 240


def test_a_picture_at_another_size_is_drawn_on_the_lunii_screen(tmp_path) -> None:
    png = tmp_path / "card.png"
    png.write_bytes(images.placeholder_png(640, 480, "bw"))
    width, height, levels = rle4(bitmap_rle4(png))
    assert (width, height, len(levels)) == (320, 240, 320 * 240)


@requires_sample
def test_a_published_pack_passes_the_same_checks() -> None:
    assert validate_plain_pk(SAMPLE, night_mode=True) == []


@requires_sample
def test_our_bitmaps_draw_what_a_published_pack_draws(tmp_path) -> None:
    """Every bitmap of a real story, re-encoded: the same colours, line by line."""
    with zipfile.ZipFile(SAMPLE) as archive:
        for name in [n for n in archive.namelist() if n.startswith("rf/")]:
            original = archive.read(name)
            width, height, levels = rle4(original)
            # Each picture carries its own sixteen colours, so what the device shows
            # is the entry an index points at, never the index itself.
            shown = [palette(original)[level] for level in levels]
            pixels = b"".join(bytes(rgb) for rgb in shown)
            png = tmp_path / f"{Path(name).stem}.png"
            png.write_bytes(
                images._png(width, height, [pixels[row * width * 3 : (row + 1) * width * 3] for row in range(height)])
            )
            ours = bitmap_rle4(png)
            entries = palette(ours)
            drawn = [entries[level] for level in rle4(ours)[2]]
            worst = max(max(abs(a - b) for a, b in zip(want, got)) for want, got in zip(shown, drawn))
            assert worst <= 8, f"{name}: {worst} off"
