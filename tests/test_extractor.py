"""Extractor tests against a synthetic pack that mirrors the gon-ruby-mp3 layout."""

from __future__ import annotations

from pathlib import Path

import pytest

from daisy2telmi.detector import DaisyPackError, detect
from daisy2telmi.extractor import _npt, extract

NCC = """<?xml version="1.0" encoding="utf-8"?>
<html xmlns="http://www.w3.org/1999/xhtml">
  <head>
    <meta name="dc:title" content="Sample"/>
    <meta name="dc:creator" content="Author"/>
    <meta name="dc:language" content="fr"/>
    <meta name="ncc:narrator" content="Reader"/>
  </head>
  <body>
    <h1 class="title" id="t"><a href="a.smil#t">Sample</a></h1>
    <h1 id="c1"><a href="b.smil#c1">Un</a></h1>
    <span class="page-normal" id="p"><a href="b.smil#pg">1</a></span>
    <h2 id="c11"><a href="c.smil#c11">1-1</a></h2>
    <h1 id="c2"><a href="d.smil#c2">Deux</a></h1>
    <h2 id="c21"><a href="e.smil#c21">2-1</a></h2>
  </body>
</html>
"""


def smil(*clips: str) -> str:
    return (
        '<?xml version="1.0" encoding="utf-8"?>\n<smil><body><seq>\n'
        + "\n".join(f"<par><audio {clip}/></par>" for clip in clips)
        + "\n</seq></body></smil>\n"
    )


@pytest.fixture()
def book(tmp_path: Path):
    root = tmp_path / "pack"
    root.mkdir()
    (root / "ncc.html").write_text(NCC, encoding="utf-8")
    # Two back-to-back clips of one file: they should collapse into a single range.
    (root / "a.smil").write_text(smil('src="front.mp3"'), encoding="utf-8")
    (root / "b.smil").write_text(
        smil('src="one.mp3" clip-begin="npt=0.000s" clip-end="npt=10.000s"',
             'src="one.mp3" clip-begin="npt=10.000s" clip-end="npt=20.000s"'),
        encoding="utf-8",
    )
    (root / "c.smil").write_text(smil('src="sub.mp3"'), encoding="utf-8")
    (root / "d.smil").write_text(smil('src="two.mp3"'), encoding="utf-8")
    (root / "e.smil").write_text(smil('src="gone.mp3"'), encoding="utf-8")
    for name in ("front.mp3", "one.mp3", "sub.mp3", "two.mp3"):
        (root / name).write_bytes(b"\xff\xfb")
    pack = detect(tmp_path)
    assert pack is not None
    return extract(pack)


def test_metadata_comes_from_the_ncc(book) -> None:
    assert (book.title, book.author, book.language, book.narrator) == ("Sample", "Author", "fr", "Reader")


def test_top_level_headings_open_chapters(book) -> None:
    assert [c.title for c in book.chapters] == ["Un", "Deux"]


def test_sub_headings_are_folded_into_their_chapter(book) -> None:
    assert [s.src.name for s in book.chapters[0].segments] == ["one.mp3", "sub.mp3"]


def test_back_to_back_clips_of_one_file_merge_into_one_range(book) -> None:
    segment = book.chapters[0].segments[0]
    assert (segment.begin, segment.end) == (0.0, 20.0)


def test_front_matter_is_kept_apart_from_the_chapters(book) -> None:
    assert [s.src.name for s in book.front_matter] == ["front.mp3"]


def test_page_anchors_are_not_treated_as_chapters(book) -> None:
    assert len(book.chapters) == 2


def test_missing_audio_is_dropped_without_failing_the_pack(book) -> None:
    assert [s.src.name for s in book.chapters[1].segments] == ["two.mp3"]


def test_a_pack_whose_top_level_is_h2_splits_on_h2(tmp_path: Path) -> None:
    """Some producers start their outline at h2; the shallowest level opens chapters."""
    root = tmp_path / "pack"
    root.mkdir()
    (root / "ncc.html").write_text(
        """<?xml version="1.0" encoding="utf-8"?>
<html xmlns="http://www.w3.org/1999/xhtml"><head><title>T</title></head><body>
  <h1 class="title" id="t"><a href="a.smil#t">Book</a></h1>
  <h2 id="c1"><a href="b.smil#c1">Un</a></h2>
  <h3 id="c11"><a href="c.smil#c11">1-1</a></h3>
  <h2 id="c2"><a href="d.smil#c2">Deux</a></h2>
</body></html>
""",
        encoding="utf-8",
    )
    for name in ("a", "b", "c", "d"):
        (root / f"{name}.smil").write_text(smil(f'src="{name}.mp3"'), encoding="utf-8")
        (root / f"{name}.mp3").write_bytes(b"\xff\xfb")
    pack = detect(tmp_path)
    assert pack is not None
    book = extract(pack)
    assert [c.title for c in book.chapters] == ["Un", "Deux"]
    assert [s.src.name for s in book.chapters[0].segments] == ["b.mp3", "c.mp3"]
    assert [s.src.name for s in book.chapters[1].segments] == ["d.mp3"]


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("npt=0.000s", 0.0),
        ("npt=12.5", 12.5),
        ("npt=0:01:30.5", 90.5),
        ("npt=1:00:00", 3600.0),
        ("npt=indefinite", None),
        (None, None),
        ("npt=", None),
    ],
)
def test_npt_parsing(raw, expected) -> None:
    assert _npt(raw) == expected


SYNCED_NCC = """<?xml version="1.0" encoding="utf-8"?>
<html xmlns="http://www.w3.org/1999/xhtml">
  <head><meta name="dc:title" content="Sample"/></head>
  <body>
    <h1 id="c1"><a href="b.smil#c1">Un</a></h1>
    <h2 id="c11"><a href="c.smil#c11">1-1</a></h2>
    <h1 id="c2"><a href="d.smil#c2">Deux</a></h1>
  </body>
</html>
"""

LYRICS = """<?xml version="1.0" encoding="utf-8"?>
<html xmlns="http://www.w3.org/1999/xhtml"><body>
  <p id="s1">Le loup sortit de la foret.</p>
  <p id="s2">Il cherchait de quoi manger.</p>
  <p id="s3">Un agneau buvait a la riviere.</p>
</body></html>
"""


def synced(*pairs: tuple[str, str, str, str]) -> str:
    """SMIL whose every par pairs one clip with the line read over it."""
    return (
        '<?xml version="1.0" encoding="utf-8"?>\n<smil><body><seq>\n'
        + "\n".join(
            f'<par><text src="text.html#{word}"/><audio src="{audio}"'
            f' clip-begin="npt={begin}s" clip-end="npt={end}s"/></par>'
            for word, audio, begin, end in pairs
        )
        + "\n</seq></body></smil>\n"
    )


@pytest.fixture()
def marked(tmp_path: Path):
    """A pack that synced its text: the source of all three cut levels."""
    root = tmp_path / "pack"
    root.mkdir()
    (root / "ncc.html").write_text(SYNCED_NCC, encoding="utf-8")
    (root / "text.html").write_text(LYRICS, encoding="utf-8")
    (root / "b.smil").write_text(
        synced(("s1", "one.mp3", "0", "10"), ("s2", "one.mp3", "10", "20")), encoding="utf-8"
    )
    (root / "c.smil").write_text(synced(("s3", "sub.mp3", "0", "5")), encoding="utf-8")
    (root / "d.smil").write_text(synced(("s1", "two.mp3", "0", "8")), encoding="utf-8")
    for name in ("one.mp3", "sub.mp3", "two.mp3"):
        (root / name).write_bytes(b"\xff\xfb")
    pack = detect(tmp_path)
    assert pack is not None
    return pack


def test_the_chapter_card_shows_the_first_line_read_over_it(marked) -> None:
    book = extract(marked, "chapter")
    assert [c.text for c in book.chapters] == [
        "Le loup sortit de la foret.",
        "Le loup sortit de la foret.",
    ]


def test_the_paragraph_level_cuts_on_every_heading_the_pack_marks(marked) -> None:
    book = extract(marked, "paragraph")
    assert [c.title for c in book.chapters] == ["Un", "1-1", "Deux"]
    assert [s.src.name for s in book.chapters[0].segments] == ["one.mp3"]
    assert book.chapters[1].text == "Un agneau buvait a la riviere."


def test_the_sentence_level_gives_one_part_per_synced_clip(marked) -> None:
    book = extract(marked, "sentence")
    assert [(c.title, c.text) for c in book.chapters] == [
        ("Part 1", "Le loup sortit de la foret."),
        ("Part 2", "Il cherchait de quoi manger."),
        ("Part 3", "Un agneau buvait a la riviere."),
        ("Part 4", "Le loup sortit de la foret."),
    ]
    assert [(s.begin, s.end) for s in book.chapters[1].segments] == [(10.0, 20.0)]


def test_an_unknown_cut_level_is_refused(marked) -> None:
    with pytest.raises(DaisyPackError, match="unknown cut level"):
        extract(marked, "verse")


# The other way a producer writes a synced pack: one `<par>` per chapter holding the
# chapter's own heading, with the clips it was read over in a `<seq>` under it.
HEADING_NCC = """<?xml version="1.0" encoding="utf-8"?>
<html xmlns="http://www.w3.org/1999/xhtml">
  <head><meta name="dc:title" content="Heading only"/></head>
  <body>
    <h1 id="heading_1"><a href="h.smil#bookid_1">Chap 1</a></h1>
    <h1 id="heading_2"><a href="i.smil#bookid_2">Chap 2</a></h1>
  </body>
</html>
"""

def heading_smil(anchor: str, last: str) -> str:
    return f"""<?xml version="1.0" encoding="utf-8"?>
<smil><body><seq dur="{last}s">
  <par endsync="last">
    <text src="ncc.html#{anchor}" id="bookid_{anchor.rsplit('_', 1)[1]}"/>
    <seq>
      <audio src="one.mp3" clip-begin="npt=0.000s" clip-end="npt=2.000s" id="phrase_1"/>
      <audio src="one.mp3" clip-begin="npt=2.000s" clip-end="npt=4.000s" id="phrase_2"/>
      <audio src="one.mp3" clip-begin="npt=4.000s" clip-end="npt={last}s" id="phrase_3"/>
    </seq>
  </par>
</seq></body></smil>
"""


@pytest.fixture()
def headings_only(tmp_path: Path):
    """A pack that timed every clip but wrote no words for them."""
    root = tmp_path / "pack"
    root.mkdir()
    (root / "ncc.html").write_text(HEADING_NCC, encoding="utf-8")
    (root / "h.smil").write_text(heading_smil("heading_1", "6.000"), encoding="utf-8")
    (root / "i.smil").write_text(heading_smil("heading_2", "3.000"), encoding="utf-8")
    (root / "one.mp3").write_bytes(b"\xff\xfb")
    pack = detect(tmp_path)
    assert pack is not None
    return pack


def test_a_chapter_heading_is_not_read_once_per_clip(headings_only) -> None:
    book = extract(headings_only, "chapter")
    assert [c.body for c in book.chapters] == ["Chap 1", "Chap 2"]


def test_the_clips_under_a_heading_keep_their_own_times(headings_only) -> None:
    book = extract(headings_only, "chapter")
    assert [(s.begin, s.end) for s in book.chapters[0].spans] == [(0.0, 2.0), (2.0, 4.0), (4.0, 6.0)]


# A DAISY 3 (ANSI/NISO Z39.86-2005) pack: no ncc.html, an outline in an NCX whose
# package document is named after the book, and SMIL that trims with `clipBegin`.
DAISY3_OPF = """<?xml version="1.0" encoding="UTF-8"?>
<package xmlns="http://www.idpf.org/2006/01/opf" unique-identifier="uid">
  <metadata>
    <dc-metadata xmlns:dc="http://purl.org/dc/elements/1.1/">
      <dc:Title>ARE YOU READY?</dc:Title>
      <dc:Creator>WikiHow</dc:Creator>
      <dc:Language>en</dc:Language>
    </dc-metadata>
  </metadata>
  <manifest><item id="ncx" href="areyouready.ncx" media-type="application/x-dtbncx+xml"/></manifest>
  <spine><itemref idref="c1"/></spine>
</package>
"""

DAISY3_NCX = """<?xml version="1.0" encoding="UTF-8"?>
<ncx xmlns="http://www.daisy.org/z3986/2005/ncx/" version="2005-1">
  <head><meta content="0:00:20" name="dtb:totalTime"/></head>
  <docTitle>
    <text>ARE YOU READY?</text>
    <audio clipBegin="0:00:00" clipEnd="0:00:02.029" src="front.mp3"/>
  </docTitle>
  <docAuthor>
    <text>WikiHow</text>
    <audio clipBegin="0:00:02.029" clipEnd="0:00:04.878" src="front.mp3"/>
  </docAuthor>
  <navMap>
    <navPoint class="h1" id="n1" playOrder="1">
      <navLabel><text>The whole book</text></navLabel>
      <content src="one.smil#n1"/>
      <navPoint class="h2" id="n2" playOrder="2">
        <navLabel><text>Chapter One</text><audio src="one.mp3"/></navLabel>
        <content src="two.smil#n2"/>
        <navPoint class="h3" id="n3" playOrder="3">
          <navLabel><text>Steps</text></navLabel>
          <content src="three.smil#n3"/>
        </navPoint>
      </navPoint>
      <navPoint class="h2" id="n4" playOrder="4">
        <navLabel><text>Chapter Two</text></navLabel>
        <content src="four.smil#n4"/>
      </navPoint>
    </navPoint>
  </navMap>
</ncx>
"""

DTBOOK = """<?xml version="1.0" encoding="UTF-8"?>
<dtbook xmlns="http://www.daisy.org/z3986/2005/struct/"><book><doc>
  <p id="d1">Make a plan for your family.</p>
  <p id="d2">Pack water first.</p>
</doc></book></dtbook>
"""


def smil3(*pairs: tuple[str, str, str, str]) -> str:
    """A DAISY 3 SMIL: camelCase clip points, text anchored in the DTBook body."""
    return (
        '<?xml version="1.0" encoding="UTF-8"?>\n<smil><body><seq>\n'
        + "\n".join(
            f'<par id="{par}"><text src="book.xml#{word}"/>'
            f'<audio clipBegin="0:00:{begin}" clipEnd="0:00:{end}" src="{audio}"/></par>'
            for par, word, audio, begin, end in pairs
        )
        + "\n</seq></body></smil>\n"
    )


@pytest.fixture()
def daisy3(tmp_path: Path):
    root = tmp_path / "Are_you_ready_z3986"
    root.mkdir()
    (root / "areyouready.opf").write_text(DAISY3_OPF, encoding="utf-8")
    (root / "areyouready.ncx").write_text(DAISY3_NCX, encoding="utf-8")
    (root / "book.xml").write_text(DTBOOK, encoding="utf-8")
    (root / "one.smil").write_text(smil3(("p1", "d1", "one.mp3", "00", "05")), encoding="utf-8")
    (root / "two.smil").write_text(smil3(("p2", "d1", "two.mp3", "00", "04")), encoding="utf-8")
    (root / "three.smil").write_text(smil3(("p3", "d2", "three.mp3", "04", "09")), encoding="utf-8")
    (root / "four.smil").write_text(smil3(("p4", "d2", "four.mp3", "00", "07")), encoding="utf-8")
    for name in ("front", "one", "two", "three", "four"):
        (root / f"{name}.mp3").write_bytes(b"\xff\xfb")
    pack = detect(tmp_path)
    assert pack is not None
    return pack


def test_a_daisy3_pack_is_detected_without_an_ncc(daisy3) -> None:
    assert daisy3.ncc is None
    assert daisy3.ncx is not None and daisy3.ncx.name == "areyouready.ncx"
    assert daisy3.opf is not None and daisy3.opf.name == "areyouready.opf"


def test_a_single_h1_wrapper_does_not_swallow_the_book(daisy3) -> None:
    """One `h1` over the whole book is a wrapper, not a chapter: the h2s below it are."""
    book = extract(daisy3)
    assert [c.title for c in book.chapters] == ["The whole book", "Chapter One", "Chapter Two"]


def test_daisy3_clips_are_trimmed_by_their_camel_case_clip_points(daisy3) -> None:
    book = extract(daisy3)
    assert [(s.src.name, s.begin, s.end) for s in book.chapters[1].segments] == [
        ("two.mp3", 0.0, 4.0),
        ("three.mp3", 4.0, 9.0),
    ]


def test_daisy3_front_matter_is_the_ncx_header(daisy3) -> None:
    """The spoken title and author, which a 2.02 pack keeps in its outline instead."""
    book = extract(daisy3)
    assert [(s.src.name, s.begin, s.end) for s in book.front_matter] == [("front.mp3", 0.0, 4.878)]


def test_daisy3_metadata_comes_from_the_package_document(daisy3) -> None:
    book = extract(daisy3)
    assert (book.title, book.author, book.language) == ("ARE YOU READY?", "WikiHow", "en")


def test_daisy3_cards_show_the_dtbook_body(daisy3) -> None:
    book = extract(daisy3)
    assert [c.text for c in book.chapters] == [
        "Make a plan for your family.",
        "Make a plan for your family.",
        "Pack water first.",
    ]


def test_daisy3_paragraph_level_keeps_every_nav_point(daisy3) -> None:
    book = extract(daisy3, "paragraph")
    assert [c.title for c in book.chapters] == [
        "The whole book",
        "Chapter One",
        "Steps",
        "Chapter Two",
    ]


TEXT_ONLY_NCC = """<?xml version="1.0" encoding="utf-8"?>
<html xmlns="http://www.w3.org/1999/xhtml">
  <head><meta name="dc:title" content="Treaty"/></head>
  <body>
    <h1 id="c1"><a href="a.smil#t1">Article 1</a></h1>
    <h1 id="c2"><a href="b.smil#t3">Article 2</a></h1>
  </body>
</html>
"""


def text_smil(*anchors: str) -> str:
    """A SMIL whose every par names words and no audio: a text-only pack."""
    return (
        '<?xml version="1.0" encoding="utf-8"?>\n<smil><body><seq>\n'
        + "\n".join(f'<par><text src="text.html#{anchor}"/></par>' for anchor in anchors)
        + "\n</seq></body></smil>\n"
    )


@pytest.fixture()
def text_only(tmp_path: Path):
    root = tmp_path / "pack"
    root.mkdir()
    (root / "ncc.html").write_text(TEXT_ONLY_NCC, encoding="utf-8")
    (root / "text.html").write_text(LYRICS, encoding="utf-8")
    (root / "a.smil").write_text(text_smil("s1", "s2"), encoding="utf-8")
    (root / "b.smil").write_text(text_smil("s3"), encoding="utf-8")
    pack = detect(tmp_path)
    assert pack is not None
    return pack


def test_a_pack_with_no_audio_still_gives_chapters(text_only) -> None:
    book = extract(text_only)
    assert [c.title for c in book.chapters] == ["Article 1", "Article 2"]
    assert [c.segments for c in book.chapters] == [(), ()]


def test_a_text_only_chapter_keeps_all_of_its_text(text_only) -> None:
    book = extract(text_only)
    assert book.chapters[0].body == "Le loup sortit de la foret. Il cherchait de quoi manger."
    assert book.chapters[1].body == "Un agneau buvait a la riviere."


def test_an_audio_chapter_keeps_its_text_too(marked) -> None:
    """The body is what a part says, whether or not the pack also recorded it."""
    book = extract(marked)
    assert book.chapters[0].body == (
        "Le loup sortit de la foret. Il cherchait de quoi manger. Un agneau buvait a la riviere."
    ), "a folded sub-heading folds its words in as well as its audio"


# An EPUB is the same OEB package and NCX outline as a DAISY 3 pack, minus the
# SMIL and the audio: its chapters are anchors inside one XHTML document.
EPUB_OPF = """<?xml version="1.0" encoding="UTF-8"?>
<package xmlns="http://www.idpf.org/2007/opf" unique-identifier="uid" version="2.0">
  <metadata xmlns:dc="http://purl.org/dc/elements/1.1/">
    <dc:title>The Wolf</dc:title>
    <dc:creator>Aesop</dc:creator>
    <dc:language>en</dc:language>
  </metadata>
  <manifest>
    <item id="ncx" href="navigation.ncx" media-type="application/x-dtbncx+xml"/>
    <item id="b" href="book.xhtml" media-type="application/xhtml+xml"/>
  </manifest>
  <spine><itemref idref="b"/></spine>
</package>
"""

EPUB_NCX = """<?xml version="1.0" encoding="UTF-8"?>
<ncx xmlns="http://www.daisy.org/z3986/2005/ncx/" version="2005-1">
  <docTitle><text>The Wolf</text></docTitle>
  <navMap>
    <navPoint class="h1" id="t1" playOrder="1">
      <navLabel><text>Chapter One</text></navLabel>
      <content src="book.xhtml#c1"/>
      <navPoint class="h2" id="t2" playOrder="2">
        <navLabel><text>A section</text></navLabel>
        <content src="book.xhtml#c2"/>
      </navPoint>
    </navPoint>
    <navPoint class="h1" id="t3" playOrder="3">
      <navLabel><text>Chapter Two</text></navLabel>
      <content src="book.xhtml#c3"/>
    </navPoint>
    <navPoint class="h1" id="t4" playOrder="4">
      <navLabel><text>Cover</text></navLabel>
      <content src="cover.jpg"/>
    </navPoint>
    <navPoint class="h1" id="t5" playOrder="5">
      <navLabel><text>Missing</text></navLabel>
      <content src="gone.xhtml#c9"/>
    </navPoint>
  </navMap>
</ncx>
"""

EPUB_XHTML = """<?xml version="1.0" encoding="UTF-8"?>
<html xmlns="http://www.w3.org/1999/xhtml">
  <body>
    <h1 id="c1">Chapter One</h1>
    <p>The wolf went out. He was looking for food.</p>
    <h2 id="c2">A section</h2>
    <p>An lamb drank at the river.</p>
    <h1 id="c3">Chapter Two</h1>
    <p>Next morning the wolf came back.</p>
  </body>
</html>
"""


@pytest.fixture()
def epub(tmp_path: Path):
    root = tmp_path / "the-wolf"
    (root / "OEBPS").mkdir(parents=True)
    (root / "mimetype").write_text("application/epub+zip", encoding="utf-8")
    (root / "OEBPS/package.opf").write_text(EPUB_OPF, encoding="utf-8")
    (root / "OEBPS/navigation.ncx").write_text(EPUB_NCX, encoding="utf-8")
    (root / "OEBPS/book.xhtml").write_text(EPUB_XHTML, encoding="utf-8")
    pack = detect(tmp_path)
    assert pack is not None
    return pack


def test_an_epub_book_is_read_from_its_anchors(epub) -> None:
    """An anchor names a heading, so a chapter runs to the next one the outline names."""
    book = extract(epub)
    assert [c.title for c in book.chapters] == ["Chapter One", "Chapter Two"]
    assert book.chapters[1].body == "Chapter Two Next morning the wolf came back."


def test_an_epub_chapter_with_no_audio_still_has_a_card_line(epub) -> None:
    book = extract(epub)
    assert book.chapters[0].text == "Chapter One The wolf went out."


def test_an_epub_sub_heading_folds_into_its_chapter(epub) -> None:
    book = extract(epub)
    assert book.chapters[0].body == (
        "Chapter One The wolf went out. He was looking for food. "
        "A section An lamb drank at the river."
    )


def test_an_epub_pointing_at_a_picture_or_a_missing_file_gives_no_part(
    epub, caplog
) -> None:
    """A contents entry for a picture is not a part; a missing document is reported."""
    with caplog.at_level("WARNING"):
        book = extract(epub)
    assert [c.title for c in book.chapters] == ["Chapter One", "Chapter Two"]
    assert "missing document: gone.xhtml" in caplog.text


def test_an_epub_cut_to_sentences_labels_every_card(epub) -> None:
    """The sentence level has no headings to name its parts, so it says their words."""
    book = extract(epub, "sentence")
    assert [c.text for c in book.chapters] == [
        "Chapter One The wolf went out.",
        "He was looking for food.",
        "A section An lamb drank at the river.",
        "Chapter Two Next morning the wolf came back.",
    ]
    assert [c.title for c in book.chapters] == [f"Part {i}" for i in range(1, 5)]
