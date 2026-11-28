"""The DAISY 2.02 pack: its outline, the audio its paragraphs are read over, reading it back."""

from __future__ import annotations

import logging
import zipfile
from pathlib import Path
from xml.etree import ElementTree

import pytest

from daisy2telmi import audio, daisy_writer
from daisy2telmi.cli import main
from daisy2telmi.detector import open_pack
from daisy2telmi.extractor import AudioSegment, Book, Chapter, Span, extract

_PACK_CANDIDATES = (
    Path(__file__).resolve().parents[1] / "gon-ruby-mp3.zip",
    Path("~/daisy/gon-ruby-mp3.zip").expanduser(),
)
PACK = next((p for p in _PACK_CANDIDATES if p.exists()), _PACK_CANDIDATES[-1])
requires_pack = pytest.mark.skipif(not PACK.exists(), reason=f"test pack missing: {PACK}")
requires_ffmpeg = pytest.mark.skipif(audio.ffmpeg_executable() is None, reason="ffmpeg not installed")

XHTML = "{http://www.w3.org/1999/xhtml}"
XML_LANG = "{http://www.w3.org/XML/1998/namespace}lang"


def make_book(**overrides) -> Book:
    defaults = dict(
        title="Le loup et l'agneau",
        chapters=(),
        front_matter=(),
        author="Jean de La Fontaine",
        language="fr",
        publisher="ATDO",
        identifier="1234567890",
    )
    defaults.update(overrides)
    return Book(**defaults)


def make_chapter(clip: Path) -> Chapter:
    return Chapter(
        title="Un loup",
        segments=(AudioSegment(clip),),
        text="Un loup",
        body="Un loup dormait.\nIl rêvait.",
        spans=(Span("Un loup dormait.", clip, 0.0, 2.0), Span("Il rêvait.", clip, 2.0, 5.0)),
    )


def make_part(**overrides) -> daisy_writer._Part:
    defaults = dict(
        stem="loup001",
        title="Un loup",
        heading="louph1",
        audio=Path("loup001.mp3"),
        smil=Path("loup001.smil"),
        duration=5.0,
        elapsed=0.0,
        paragraphs=("Un loup dormait.", "Il rêvait."),
        marks=(("Un loup dormait.", 0.0, 2.0), ("Il rêvait.", 2.0, 5.0)),
    )
    defaults.update(overrides)
    return daisy_writer._Part(**defaults)


def metas(root: ElementTree.Element, prefix: str = XHTML) -> dict[str, str]:
    return {m.get("name"): m.get("content") for m in root.findall(f"{prefix}head/{prefix}meta")}


def test_the_part_files_are_named_after_the_titles_letters() -> None:
    assert daisy_writer._prefix("Gon Ruby no Kitsune") == "gonrub"
    assert daisy_writer._prefix("Le loup : chapitre 1") == "leloup"
    assert daisy_writer._prefix("ごんぎつね") == "book"


def test_the_times_are_written_the_way_a_daisy_pack_writes_them() -> None:
    assert daisy_writer._clock(1376.0) == "00:22:56"
    assert daisy_writer._clock(7.197, millis=True) == "00:00:07.197"
    assert daisy_writer._npt(83.5) == "npt=83.500s"
    assert daisy_writer._dur(7.197) == "7.197s"


def test_the_marks_are_laid_end_to_end_on_the_chapters_own_audio() -> None:
    chapter = Chapter(
        "Un loup",
        (),
        spans=(Span("Un loup dormait.", Path("c.mp3"), 0.0, 2.0), Span("Il rêvait.", Path("c.mp3"), 2.0, 5.0)),
    )
    assert daisy_writer._synced(chapter) == [("Un loup dormait.", 0.0, 2.0), ("Il rêvait.", 2.0, 5.0)]


def test_a_heading_repeated_on_every_clip_of_its_level_covers_them_all() -> None:
    chapter = Chapter(
        "Un loup",
        (),
        spans=(Span("Chapitre 1", Path("c.mp3"), 0.0, 1.0), Span("Chapitre 1", Path("c.mp3"), 1.0, 3.0)),
    )
    assert daisy_writer._synced(chapter) == [("Chapitre 1", 0.0, 3.0)]


def test_a_mark_without_words_or_length_leaves_the_chapter_unsynced() -> None:
    chapter = Chapter("Un loup", (), spans=(Span("Un loup dormait.", Path("c.mp3"), 0.0, 2.0), Span("", Path("c.mp3"), 2.0, 5.0)))
    assert daisy_writer._synced(chapter) == []


def test_the_ncc_carries_the_book_and_its_outline() -> None:
    root = ElementTree.fromstring(daisy_writer.ncc(make_book(), None, [make_part()], files=3))
    assert root.tag == f"{XHTML}html"
    assert root.get(XML_LANG) == "fr"
    assert metas(root) == {
        "dc:format": "Daisy 2.02",
        "dc:title": "Le loup et l'agneau",
        "dc:creator": "Jean de La Fontaine",
        "dc:publisher": "ATDO",
        "dc:language": "fr",
        "dc:identifier": "1234567890",
        "ncc:generator": f"daisy-convertor {daisy_writer.__version__}",
        "ncc:charset": "utf-8",
        "ncc:totalTime": "00:00:05",
        "ncc:tocItems": "1",
        "ncc:depth": "1",
        "ncc:files": "3",
        "ncc:setInfo": "1 of 1",
        "ncc:footnotes": "0",
        "ncc:sidebars": "0",
        "ncc:pageNormal": "0",
    }


def test_the_ncc_is_one_xhtml_document_the_book_text_lives_in() -> None:
    text = daisy_writer.ncc(make_book(), None, [make_part()], files=3)
    assert text.startswith('<?xml version="1.0" encoding="UTF-8"?>\n<html xmlns="http://www.w3.org/1999/xhtml"')
    root = ElementTree.fromstring(text)
    heading = root.find(f"{XHTML}body/{XHTML}h2")
    assert heading.get("id") == "louph1"
    assert heading.find(f"{XHTML}a").get("href") == "loup001.smil#loup001"
    assert heading.find(f"{XHTML}a").text == "Un loup"
    paragraphs = root.findall(f"{XHTML}body/{XHTML}p")
    assert [p.get("id") for p in paragraphs] == ["loup001p1", "loup001p2"]
    assert [p.text for p in paragraphs] == ["Un loup dormait.", "Il rêvait."]


def test_the_book_title_leads_to_its_own_audio_only_when_there_is_one() -> None:
    plain = ElementTree.fromstring(daisy_writer.ncc(make_book(), None, [], files=1))
    title = plain.find(f"{XHTML}body/{XHTML}h1")
    assert title.get("class") == "title"
    assert title.find(f"{XHTML}a") is None
    assert title.text == "Le loup et l'agneau"

    front = make_part(stem="loup0000", heading="louph0", audio=Path("loup0000.mp3"), smil=Path("loup0000.smil"))
    spoken = ElementTree.fromstring(daisy_writer.ncc(make_book(), front, [], files=3))
    link = spoken.find(f"{XHTML}body/{XHTML}h1/{XHTML}a")
    assert link.get("href") == "loup0000.smil#loup0000"
    assert spoken.find(f"{XHTML}body/{XHTML}h1").get("id") == "louph0"


def test_the_smil_pairs_each_paragraph_with_the_audio_it_was_read_over() -> None:
    root = ElementTree.fromstring(daisy_writer.smil(make_part()))
    assert root.find("head/meta").get("content") == "Daisy 2.02"
    assert metas(root, "")["ncc:timeInThisSmil"] == "00:00:05.000"
    assert metas(root, "")["ncc:totalElapsedTime"] == "00:00:00.000"
    seq = root.find("body/seq")
    assert seq.get("dur") == "5.000s"
    clips = seq.findall("par")
    assert [c.find("audio").get("src") for c in clips] == ["loup001.mp3", "loup001.mp3"]
    assert [c.find("audio").get("clip-begin") for c in clips] == ["npt=0.000s", "npt=2.000s"]
    assert [c.find("audio").get("clip-end") for c in clips] == ["npt=2.000s", "npt=5.000s"]
    assert [c.find("text").get("src") for c in clips] == ["ncc.html#loup001p1", "ncc.html#loup001p2"]


def test_the_second_part_says_where_it_starts_in_the_book() -> None:
    root = ElementTree.fromstring(daisy_writer.smil(make_part(elapsed=5.0)))
    assert metas(root, "")["ncc:totalElapsedTime"] == "00:00:05.000"


def test_the_last_paragraph_holds_to_the_end_of_the_audio() -> None:
    part = make_part(duration=5.4)
    clips = ElementTree.fromstring(daisy_writer.smil(part)).findall("body/seq/par")
    assert [c.find("audio").get("clip-end") for c in clips] == ["npt=2.000s", "npt=5.400s"]


def test_a_chapter_that_cannot_be_timed_is_written_as_one_piece_of_audio() -> None:
    par = ElementTree.fromstring(daisy_writer.smil(make_part(marks=(), paragraphs=()))).find("body/seq/par")
    assert par.find("text") is None
    assert par.find("audio").get("clip-begin") == "npt=0.000s"
    assert par.find("audio").get("clip-end") == "npt=5.000s"


@requires_ffmpeg
def test_the_pack_is_the_parts_the_book_has_one_mp3_and_one_smil_each(tmp_path: Path) -> None:
    clip = tmp_path / "clip.mp3"
    audio.render_silence(clip, 5.0)
    book = make_book(chapters=(make_chapter(clip),))
    folder = tmp_path / "pack"
    written = daisy_writer.export(book, folder)
    assert sorted(p.name for p in written) == ["leloup001.mp3", "leloup001.smil", "ncc.html"]
    assert all(p.parent == folder for p in written)
    assert (folder / "leloup001.mp3").stat().st_size > 0
    assert (folder / "ncc.html").read_text(encoding="utf-8").count("<p id=") == 2


@requires_ffmpeg
def test_the_parts_are_numbered_wide_enough_for_the_book_they_belong_to(tmp_path: Path) -> None:
    clip = tmp_path / "clip.mp3"
    audio.render_silence(clip, 1.0)
    chapter = Chapter("Un loup", (AudioSegment(clip),), body="Un loup dormait.")
    book = make_book(chapters=(chapter,) * 4)
    written = daisy_writer.export(book, tmp_path / "pack")
    assert sorted(p.stem for p in written if p.suffix == ".mp3") == ["leloup001", "leloup002", "leloup003", "leloup004"]


def test_a_chapter_whose_marks_stop_short_of_its_audio_is_written_without_sync(
    tmp_path: Path, monkeypatch, caplog
) -> None:
    def record(_chapter, _announce, dest: Path) -> float:
        dest.write_bytes(b"\xff\xfb")
        return 20.0

    monkeypatch.setattr(daisy_writer, "_record", record)
    chapter = Chapter(
        "Un loup",
        (),
        body="Un loup dormait.\nIl rêvait.",
        spans=(Span("Un loup dormait.", Path("c.mp3"), 0.0, 2.0), Span("Il rêvait.", Path("c.mp3"), 2.0, 3.0)),
    )
    with caplog.at_level(logging.INFO):
        daisy_writer.export(make_book(chapters=(chapter,)), tmp_path / "pack")
    assert "do not cover" in caplog.text
    root = ElementTree.fromstring((tmp_path / "pack" / "leloup001.smil").read_text(encoding="utf-8"))
    assert root.find("body/seq/par/text") is None
    assert root.find("body/seq/par/audio").get("clip-end") == "npt=20.000s"


def test_a_long_chapter_may_run_past_the_slack_of_a_short_one(tmp_path: Path, monkeypatch) -> None:
    def record(_chapter, _announce, dest: Path) -> float:
        dest.write_bytes(b"\xff\xfb")
        return 410.38

    monkeypatch.setattr(daisy_writer, "_record", record)
    spans = tuple(Span(f"s{index}", Path("c.mp3"), index * 1.0, index * 1.0 + 1.0) for index in range(409))
    chapter = Chapter("Un loup", (AudioSegment(Path("c.mp3")),) * 9, body="Un loup dormait.", spans=spans)
    daisy_writer.export(make_book(chapters=(chapter,)), tmp_path / "pack")
    root = ElementTree.fromstring((tmp_path / "pack" / "leloup001.smil").read_text(encoding="utf-8"))
    assert root.find("body/seq/par/text") is not None


@requires_pack
def test_the_cli_writes_the_pack_and_zips_it_under_the_books_name(tmp_path: Path) -> None:
    assert main([str(PACK), str(tmp_path), "--format", "daisy2", "--announce", "off"]) == 0
    folder = tmp_path / "ごんぎつね"
    assert (folder / "ncc.html").exists()
    assert sorted(p.stem for p in folder.glob("*.mp3")) == ["book0000", "book001", "book002", "book003", "book004", "book005", "book006"]
    with zipfile.ZipFile(tmp_path / "ごんぎつね-daisy2.zip") as archive:
        names = archive.namelist()
    assert "ごんぎつね/ncc.html" in names
    assert sum(1 for name in names if name.endswith(".mp3")) == 7


@requires_pack
def test_the_pack_written_here_reads_back_as_the_book_it_came_from(tmp_path: Path) -> None:
    assert main([str(PACK), str(tmp_path), "--format", "daisy2", "--announce", "off"]) == 0
    with open_pack(PACK) as pack:
        original = extract(pack, "chapter")
    with open_pack(tmp_path / "ごんぎつね") as pack:
        written = extract(pack, "chapter")
    assert written.title == original.title
    assert written.author == original.author
    assert written.language == original.language
    assert written.publisher == original.publisher
    assert written.identifier == original.identifier
    assert written.narrator == original.narrator
    assert [c.title for c in written.chapters] == [c.title for c in original.chapters]
    assert [c.body for c in written.chapters] == [c.body for c in original.chapters]
    assert [[s.text for s in c.spans] for c in written.chapters] == [[s.text for s in c.spans] for c in original.chapters]
    assert [len(c.spans) for c in written.chapters] == [len(c.spans) for c in original.chapters]


@requires_pack
def test_the_pack_reads_back_at_a_cut_below_its_chapters(tmp_path: Path) -> None:
    assert main([str(PACK), str(tmp_path), "--format", "daisy2", "--announce", "off"]) == 0
    with open_pack(PACK) as pack:
        original = extract(pack, "sentence")
    with open_pack(tmp_path / "ごんぎつね") as pack:
        written = extract(pack, "sentence")
    assert len(written.chapters) == len(original.chapters)
    assert [c.body for c in written.chapters] == [c.body for c in original.chapters]


@requires_pack
@requires_ffmpeg
def test_the_pack_reexports_without_touching_its_audio(tmp_path: Path) -> None:
    first = tmp_path / "first"
    second = tmp_path / "second"
    assert main([str(PACK), str(first), "--format", "daisy2", "--announce", "off"]) == 0
    assert main([str(first / "ごんぎつね"), str(second), "--format", "daisy2", "--announce", "off"]) == 0
    for clip in (first / "ごんぎつね").glob("*.mp3"):
        assert (second / "ごんぎつね" / clip.name).read_bytes() == clip.read_bytes()
