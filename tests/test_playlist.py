"""The mp3 playlist pack: the file names, the four playlists, the book's own text."""

from __future__ import annotations

import logging
import re
import zipfile
from pathlib import Path
from urllib.parse import quote
from xml.etree import ElementTree

import pytest

from daisy2telmi import audio, playlist, textfiles, tts
from daisy2telmi.cli import main
from daisy2telmi.extractor import AudioSegment, Book, Chapter, Span
from daisy2telmi.telmi_builder import Announce

_PACK_CANDIDATES = (
    Path(__file__).resolve().parents[1] / "gon-ruby-mp3.zip",
    Path("~/daisy/gon-ruby-mp3.zip").expanduser(),
)
PACK = next((p for p in _PACK_CANDIDATES if p.exists()), _PACK_CANDIDATES[-1])
requires_pack = pytest.mark.skipif(not PACK.exists(), reason=f"test pack missing: {PACK}")

XSPF = {"x": "http://xspf.org/ns/0/"}


def make_book(**overrides) -> Book:
    defaults = dict(
        title="Le loup et l'agneau",
        chapters=(
            Chapter("Le loup et l'agneau", (), body="Un Agneau se désaltérait"),
            Chapter("Le loup résigné", (), body="Sire, ne vous mettez en peine"),
        ),
        front_matter=(),
        author="Jean de La Fontaine",
        language="fr",
        publisher="ATDO",
        identifier="1234567890",
    )
    defaults.update(overrides)
    return Book(**defaults)


def test_safe_name_keeps_accents_and_drops_what_a_file_system_refuses() -> None:
    assert playlist.safe_name("Le loup : chapitre/1 * 2") == "Le loup chapitre 1 2"
    assert playlist.safe_name("  Gon ぎつね  ") == "Gon ぎつね"
    assert playlist.safe_name("///") == "story"


def test_a_chapter_file_leads_with_its_number_so_a_sorted_player_keeps_order() -> None:
    name = playlist.chapter_name(3, "Le loup résigné", "Le loup et l'agneau")
    assert name == "003 - Le loup résigné - Le loup et l'agneau"
    assert playlist.chapter_name(1, "", "Book") == "001 - Book"


def test_the_interlude_sorts_in_front_of_the_chapter_it_introduces() -> None:
    chapter = "001 - Le loup - Livre I"
    assert sorted([f"{chapter}.mp3", f"{chapter}-intro.mp3"])[0].endswith("-intro.mp3")


def test_a_book_past_a_thousand_parts_numbers_its_parts_wide_enough_to_sort() -> None:
    """`999` sorts after `1000`, and the playlist order is the name order."""
    names = [playlist.chapter_name(n, "Suite", "Book", 1001) for n in (1, 999, 1000, 1001)]
    assert [name.split(" - ")[0] for name in names] == ["0001", "0999", "1000", "1001"]
    assert sorted(f"{name}.mp3" for name in names) == [f"{name}.mp3" for name in names]


@pytest.mark.parametrize(
    "seconds,written",
    [(None, "?"), (0, "0:00"), (5.4, "0:05"), (75, "1:15"), (3723.5, "1:02:04")],
)
def test_clock(seconds: float | None, written: str) -> None:
    assert playlist.clock(seconds) == written


def test_m3u_holds_its_header_first_and_one_entry_per_track() -> None:
    tracks = [playlist.Track("001 - Le loup.mp3", "Le loup", 12.6), playlist.Track("002.mp3", "Suite", None)]
    lines = playlist.m3u(tracks, "La Fontaine").splitlines()
    assert lines[0] == "#EXTM3U"
    assert lines[1] == "#EXTINF:12,La Fontaine - Le loup"
    assert lines[2] == "001 - Le loup.mp3"
    assert lines[3] == "#EXTINF:-1,La Fontaine - Suite", "an unknown length is -1, not 0"


def test_pls_numbers_every_field_and_counts_the_entries() -> None:
    tracks = [playlist.Track("a.mp3", "Un", 3.2), playlist.Track("b.mp3", "Deux", None)]
    body = playlist.pls(tracks, "")
    assert body.splitlines() == [
        "[playlist]",
        "File1=a.mp3",
        "Title1=Un",
        "Length1=3",
        "File2=b.mp3",
        "Title2=Deux",
        "Length2=-1",
        "NumberOfEntries=2",
        "Version=2",
    ]


def test_xspf_is_well_formed_xml_with_millisecond_durations() -> None:
    tracks = [playlist.Track("001 - Le loup.mp3", "Le loup", 12.5)]
    root = ElementTree.fromstring(playlist.xspf(tracks, "Le loup et l'agneau", "La Fontaine"))
    assert root.get("version") == "1"
    assert root.find("x:title", XSPF).text == "Le loup et l'agneau"
    item = root.find("x:trackList/x:track", XSPF)
    assert item.find("x:title", XSPF).text == "Le loup"
    assert item.find("x:album", XSPF).text == "Le loup et l'agneau"
    assert item.find("x:trackNum", XSPF).text == "1"
    assert item.find("x:duration", XSPF).text == "12500", "XSPF counts milliseconds"


def test_xspf_percent_encodes_a_file_name_it_cannot_hold_raw() -> None:
    """XSPF locations are URIs: a space has to be escaped, unlike in an M3U."""
    body = playlist.xspf([playlist.Track("001 - Le loup.mp3", "Le loup", None)], "Book", None)
    root = ElementTree.fromstring(body)
    location = root.find("x:trackList/x:track/x:location", XSPF).text
    assert location == "001%20-%20Le%20loup.mp3"


def test_details_lists_what_the_pack_declared_and_every_part_of_it() -> None:
    book = make_book()
    tracks = [playlist.Track("001 - a.mp3", "Le loup et l'agneau", 10.0), playlist.Track("002 - b.mp3", "Le loup résigné", 5.0)]
    body = playlist.details(book, tracks)
    assert body.splitlines()[:1] == ["Le loup et l'agneau"]
    for line in ("Author: Jean de La Fontaine", "Publisher: ATDO", "Language: fr", "Identifier: 1234567890"):
        assert line in body
    assert "Chapters: 2" in body
    assert "Length: 0:15" in body
    assert "001  0:10  Le loup et l'agneau" in body


def test_book_text_writes_the_packs_own_words_chapter_by_chapter() -> None:
    text = textfiles.book_text(make_book())
    assert text.splitlines()[:4] == [
        "001  Le loup et l'agneau",
        "-" * 40,
        "Un Agneau se désaltérait",
        "",
    ]
    assert "Le loup résigné" in text
    assert textfiles.book_text(make_book(chapters=())) == ""


def spans(*words: str, length: float = 2.0) -> tuple[Span, ...]:
    return tuple(Span(word, Path("a.mp3"), i * length, (i + 1) * length) for i, word in enumerate(words))


def test_the_synced_lines_follow_the_clips_they_were_read_over() -> None:
    chapter = Chapter("Un", (), spans=spans("Un agneau", "buvait", "a la riviere"))
    lines = textfiles.timings(chapter, 6.0)
    assert [line.text for line in lines] == ["Un agneau", "buvait", "a la riviere"]
    assert [(line.start, line.end) for line in lines] == [(0.0, 2.0), (2.0, 4.0), (4.0, 6.0)]


def test_the_lines_are_stretched_to_the_chapter_that_was_written() -> None:
    # The pack rounds its times, so its own sum falls short of the mp3 it produced.
    chapter = Chapter("Un", (), spans=spans("Un agneau", "buvait"))
    lines = textfiles.timings(chapter, 20.0)
    assert lines[-1].end == 20.0
    assert lines[1].start == pytest.approx(10.0)


def test_a_heading_repeated_over_every_clip_of_its_level_becomes_one_line() -> None:
    chapter = Chapter("Chap 1", (), spans=spans("Chap 1", "Chap 1", "Chap 1"))
    lines = textfiles.timings(chapter, 6.0)
    assert [line.text for line in lines] == ["Chap 1"]
    assert (lines[0].start, lines[0].end) == (0.0, 6.0)


def test_a_chapter_with_no_synced_text_gets_no_lyric_file(tmp_path: Path) -> None:
    chapter = Chapter("Chap 1", ())
    assert textfiles.synced_text(chapter, tmp_path, "001 - Chap 1", 6.0, "Book", "") == []
    assert not list(tmp_path.iterdir())


def test_the_lyric_and_subtitle_files_share_the_chapters_name_and_words(tmp_path: Path) -> None:
    chapter = Chapter("Un", (), spans=spans("Un agneau", "buvait"))
    written = textfiles.synced_text(chapter, tmp_path, "001 - Un - Book", 4.0, "Le loup", "La Fontaine")
    assert [p.name for p in written] == ["001 - Un - Book.lrc", "001 - Un - Book.srt"]
    lyric = written[0].read_text(encoding="utf-8")
    assert lyric.splitlines()[:2] == ["[ti:Le loup]", "[au:La Fontaine]"]
    assert lyric.splitlines()[2:] == ["[00:00.00]Un agneau", "[00:02.00]buvait"]
    subtitle = written[1].read_text(encoding="utf-8")
    assert subtitle.splitlines()[:3] == ["1", "00:00:00,000 --> 00:00:02,000", "Un agneau"]


def test_a_text_only_pack_copies_the_xml_it_was_written_in(tmp_path: Path) -> None:
    root = tmp_path / "pack"
    (root / "text").mkdir(parents=True)
    (root / "text" / "book.xml").write_text("<book/>", encoding="utf-8")
    (root / "ncc.smil").write_text("<smil/>", encoding="utf-8")
    (root / "cover.jpg").write_bytes(b"\xff\xd8")
    folder = tmp_path / "out"
    folder.mkdir()
    copied = playlist._copy_source(make_book(), folder, None, root)
    assert [p.name for p in copied] == ["ncc.smil", "book.xml"]
    assert (folder / "source" / "text" / "book.xml").read_text(encoding="utf-8") == "<book/>"
    assert not (folder / "cover.jpg").exists(), "the pack's pictures are not part of an mp3 pack"


def test_an_epub_is_copied_whole(tmp_path: Path) -> None:
    source = tmp_path / "Livre.epub"
    source.write_bytes(b"PK\x03\x04")
    folder = tmp_path / "out"
    folder.mkdir()
    copied = playlist._copy_source(make_book(), folder, source, None)
    assert [p.name for p in copied] == ["Livre.epub"]
    assert (folder / "Livre.epub").read_bytes() == b"PK\x03\x04"
    assert [p.name for p in copied] == ["Livre.epub"]


def test_an_audio_pack_copies_nothing(tmp_path: Path) -> None:
    book = make_book(chapters=(Chapter("Le loup", (AudioSegment(tmp_path / "a.mp3"),)),))
    assert playlist._copy_source(book, tmp_path, None, tmp_path) == []


def test_interludes_are_named_after_the_chapter_and_levelled_against_it(tmp_path: Path, monkeypatch) -> None:
    book = make_book()
    folder = tmp_path / "out"
    folder.mkdir()
    (folder / "001 - Le loup et l'agneau - Le loup et l'agneau.mp3").write_bytes(b"\xff\xfb")
    chapters = [
        playlist.Track("001 - Le loup et l'agneau - Le loup et l'agneau.mp3", "Le loup et l'agneau", 4.0),
        playlist.Track("002 - Le loup résigné - Le loup et l'agneau.mp3", "Le loup résigné", 3.0),
    ]
    spoken: dict = {}

    def fake(phrases, lang, dests, engine, voice, command, python, clip, device, speed, expression, loudness):
        spoken["phrases"] = phrases
        spoken["expression"] = expression
        spoken["loudness"] = loudness
        for dest in dests:
            dest.write_bytes(b"\xff\xfb")

    monkeypatch.setattr(tts, "synthesize_all", fake)
    monkeypatch.setattr(playlist.audio, "loudness", lambda _p: -18.0)
    tracks = playlist._interludes(book, Announce(mode="prompt", language="fr", expression="expressive"), folder, chapters)
    assert [t.file for t in tracks] == [
        "001 - Le loup et l'agneau - Le loup et l'agneau-intro.mp3",
        "002 - Le loup résigné - Le loup et l'agneau-intro.mp3",
    ]
    assert spoken["phrases"] == ["1. Le loup et l'agneau", "2. Le loup résigné"], "the card's prompt to press OK is dropped"
    assert spoken["expression"] == "expressive"
    assert spoken["loudness"] == tts.card_loudness(-18.0)


def test_a_chapter_name_that_cannot_be_spoken_keeps_the_pack(tmp_path: Path, monkeypatch, caplog) -> None:
    book = make_book()
    folder = tmp_path / "out"
    folder.mkdir()
    chapters = [playlist.Track("001 - a.mp3", "Le loup et l'agneau", 4.0)]

    def fail(*_args, **_kwargs):
        raise tts.TtsError("no engine on this machine")

    monkeypatch.setattr(tts, "synthesize_all", fail)
    monkeypatch.setattr(playlist.audio, "loudness", lambda _p: None)
    with caplog.at_level(logging.ERROR):
        assert playlist._interludes(book, Announce(), folder, chapters) == []
    assert "could not be spoken" in caplog.text


def test_announcing_nothing_writes_no_interlude(tmp_path: Path) -> None:
    chapters = [playlist.Track("001 - a.mp3", "Le loup", 4.0)]
    assert playlist._interludes(make_book(), Announce(mode="off"), tmp_path, chapters) == []


@requires_pack
def test_the_mp3_pack_is_the_book_named_folder_the_player_opens(tmp_path: Path) -> None:
    assert main([str(PACK), str(tmp_path), "--format", "mp3", "--announce", "off"]) == 0
    folder = tmp_path / "ごんぎつね"
    assert folder.is_dir(), f"expected the pack named after the book, got {[p.name for p in tmp_path.iterdir()]}"
    mp3s = sorted(p.name for p in folder.glob("*.mp3"))
    assert len(mp3s) == 6
    assert mp3s[0].startswith("001 - "), "the number leads so a sorted player keeps the order"
    assert all("ごんぎつね" in name for name in mp3s)
    assert (folder / "ごんぎつね.m3u8").is_file()
    assert (folder / "ごんぎつね.m3u").is_file()
    assert (folder / "ごんぎつね.pls").is_file()
    assert (folder / "ごんぎつね.xspf").is_file()
    assert (folder / "ごんぎつね.txt").is_file()
    text = (folder / "ごんぎつね - text.txt").read_text(encoding="utf-8")
    assert text.startswith("001  一\n"), "the pack's own SMIL text travels with the audio"
    assert "そのあくる日もごんは" in text
    assert not list(folder.glob("*.png")), "an mp3 pack carries no cards"
    archive = tmp_path / "ごんぎつね-mp3.zip"
    assert archive.is_file()
    with zipfile.ZipFile(archive) as reader:
        assert all(name.startswith("ごんぎつね/") for name in reader.namelist())


@requires_pack
def test_every_playlist_lists_the_same_files_in_the_same_order(tmp_path: Path) -> None:
    assert main([str(PACK), str(tmp_path), "--format", "mp3", "--announce", "off"]) == 0
    folder = tmp_path / "ごんぎつね"
    mp3s = sorted(p.name for p in folder.glob("*.mp3"))
    m3u = [line for line in (folder / "ごんぎつね.m3u8").read_text(encoding="utf-8").splitlines() if not line.startswith("#")]
    assert m3u == mp3s
    pls = [line.split("=", 1)[1] for line in (folder / "ごんぎつね.pls").read_text(encoding="utf-8").splitlines() if line.startswith("File")]
    assert pls == mp3s
    root = ElementTree.fromstring((folder / "ごんぎつね.xspf").read_text(encoding="utf-8"))
    # XSPF locations are URIs, so the names arrive escaped.
    assert [item.find("x:location", XSPF).text for item in root.findall("x:trackList/x:track", XSPF)] == [
        quote(name) for name in mp3s
    ]


@requires_pack
def test_the_playlist_names_each_chapter_for_the_screen(tmp_path: Path) -> None:
    assert main([str(PACK), str(tmp_path), "--format", "mp3", "--announce", "off"]) == 0
    entries = [
        line
        for line in (tmp_path / "ごんぎつね" / "ごんぎつね.m3u8").read_text(encoding="utf-8").splitlines()
        if line.startswith("#EXTINF")
    ]
    assert all("新美南吉" in line for line in entries), "the writer is the artist a player shows"
    assert all(int(line.split(":", 2)[1].split(",")[0]) > 0 for line in entries), "every chapter has a length"


@requires_pack
def test_each_chapter_gets_its_text_as_a_lyric_and_a_subtitle_file(tmp_path: Path) -> None:
    assert main([str(PACK), str(tmp_path), "--format", "mp3", "--announce", "off"]) == 0
    folder = tmp_path / "ごんぎつね"
    chapter = next(p for p in sorted(folder.glob("*.mp3")) if p.name.startswith("001 - "))
    lyric = chapter.with_suffix(".lrc").read_text(encoding="utf-8").splitlines()
    subtitle = chapter.with_suffix(".srt").read_text(encoding="utf-8").splitlines()
    stamps = [line for line in lyric if re.fullmatch(r"\[\d\d:\d\d\.\d\d].+", line)]
    assert len(stamps) > 10, "the pack synced every phrase, so the lyric has one line each"
    assert stamps[0].startswith("[00:00.00]")
    minutes, rest = stamps[-1][1:9].split(":")
    assert int(minutes) * 60 + float(rest) < audio.duration(chapter), "the last line lands inside the chapter"
    blocks = [line for line in subtitle if "-->" in line]
    assert len(blocks) == len(stamps), "the subtitle covers the same lines as the lyric"
    assert blocks[0].startswith("00:00:00,000 -->")
