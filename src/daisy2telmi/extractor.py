"""Parse a DAISY 2.02, DAISY 3 or EPUB pack into chapters and the audio each covers."""

from __future__ import annotations

import logging
import re
from collections.abc import Iterable
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import unquote

from lxml import etree

from .detector import DaisyPack, DaisyPackError

log = logging.getLogger(__name__)

_HEADING_LEVELS = {"h1": 1, "h2": 2, "h3": 3, "h4": 4, "h5": 5, "h6": 6}
_MERGE_EPSILON = 0.005


@dataclass(frozen=True)
class AudioSegment:
    """One audio clip: `src`, optionally trimmed to [begin, end) seconds."""

    src: Path
    begin: float | None = None
    end: float | None = None


@dataclass(frozen=True)
class Span:
    """One piece of the pack's synced text and the clip it was read over.

    `begin` and `end` are the SMIL's own trim points, in seconds inside `src`, so
    the words can be put back on that clip's timeline: this is what a `.lrc` or a
    `.srt` beside a chapter's mp3 needs.
    """

    text: str
    src: Path
    begin: float | None = None
    end: float | None = None


@dataclass(frozen=True)
class Chapter:
    """One part of the book at the chosen cut level.

    `text` is what the pack's own synced text says at the start of the part, which
    is what a card can show when the part has no title of its own. `body` is all of
    that text: a text-only pack has no `segments`, so its audio has to be made from
    the body instead. `spans` is that same text one piece per synced clip, in the
    order the clips play.
    """

    title: str
    segments: tuple[AudioSegment, ...]
    text: str = ""
    body: str = ""
    spans: tuple[Span, ...] = ()
    # Which chapter of the pack this part came from, counted through the table of
    # contents: at a chapter cut a part is its own chapter, and its number is its
    # own.
    group: int = 0


@dataclass(frozen=True)
class Book:
    title: str
    chapters: tuple[Chapter, ...]
    front_matter: tuple[AudioSegment, ...] = ()
    author: str | None = None
    language: str | None = None
    publisher: str | None = None
    narrator: str | None = None
    identifier: str | None = None
    source: str | None = None


def language_sample(book: Book, limit: int = 4000) -> str:
    """A page of the words the pack carries beside its audio: the title, the part titles, the parts.

    Enough of a book to read a language off, and no more: a whole novel is a
    megabyte of text a handful of common words does not need.
    """
    parts = [book.title]
    for chapter in book.chapters:
        parts.extend((chapter.title, chapter.body or chapter.text))
        if sum(len(part) for part in parts) >= limit:
            break
    return " ".join(part for part in parts if part)[:limit]


@dataclass
class _TocEntry:
    level: int
    title: str
    target: str
    is_front_matter: bool = False


@dataclass
class _Draft:
    title: str
    segments: list[AudioSegment] = field(default_factory=list)
    text: str = ""
    body: str = ""
    spans: list[Span] = field(default_factory=list)
    level: int = 1
    group: int = 0


CUT_LEVELS = ("chapter", "paragraph", "sentence")


_PARSER = etree.XMLParser(
    load_dtd=False,
    dtd_validation=False,
    no_network=True,
    resolve_entities=False,
    recover=True,
)


def _localname(element: etree._Element) -> str:
    """Element tag without its namespace; empty for comments and processing instructions."""
    if not isinstance(element.tag, str):
        return ""
    return element.tag.rpartition("}")[2]


def _parse_xml(path: Path) -> etree._Element | None:
    """Parse XML/XHTML without fetching external DTDs, tolerating minor damage."""
    try:
        return etree.parse(str(path), parser=_PARSER).getroot()
    except (etree.XMLSyntaxError, OSError) as exc:
        log.warning("could not parse %s: %s", path, exc)
        return None


# What a producer writes into a metadata field instead of an answer. Read as a
# name it becomes a cover search for a book by "Unknown".
PLACEHOLDERS = frozenset({"", "-", "n/a", "na", "none", "inconnu", "unknown", "sans objet"})


def _credited(value: str | None) -> str | None:
    """The pack's author, unless all the pack says is that it does not know one."""
    words = (value or "").strip()
    return None if words.casefold() in PLACEHOLDERS else words


def _ncc_meta(root: etree._Element) -> dict[str, str]:
    """Collect `<meta name= content=>` values from an NCC, lowercased by name."""
    out: dict[str, str] = {}
    for meta in root.iter():
        if _localname(meta) != "meta":
            continue
        name = meta.get("name")
        if name and name.lower() not in out:
            out[name.lower()] = (meta.get("content") or "").strip()
    return out


def _opf_meta(root: etree._Element) -> dict[str, str]:
    """Collect Dublin Core fields from a package document's metadata block.

    A 2.02 pack writes them lower case (`dc:title`), a 3.x one capitalises them
    (`dc:Title`), so the name is matched either way.
    """
    wanted = {"title", "language", "creator", "publisher", "identifier", "description", "source"}
    out: dict[str, str] = {}
    for element in root.iter():
        name = _localname(element).lower()
        if name in wanted:
            text = "".join(element.itertext()).strip()
            if text and name not in out:
                out[name] = text
    return out


def _npt(value: str | None) -> float | None:
    """Parse a SMIL NPT time such as `npt=12.5`, `npt=0.000s` or `npt=0:01:30.5`."""
    if not value:
        return None
    text = value.strip()
    if text[:3].lower() == "npt":
        text = text[3:].lstrip("=").strip()
    if not text or text.lower() == "indefinite":
        return None
    parts = text.split(":")
    try:
        if len(parts) == 3:
            return int(parts[0]) * 3600 + int(parts[1]) * 60 + float(parts[2].rstrip("s"))
        if len(parts) == 2:
            return int(parts[0]) * 60 + float(parts[1].rstrip("s"))
        return float(text.rstrip("s"))
    except ValueError:
        return None


def _clip(element: etree._Element) -> tuple[float | None, float | None]:
    """A SMIL `<audio>`'s trim points.

    DAISY 2.02 writes them `clip-begin`, DAISY 3 writes `clipBegin`; reading only
    one spelling leaves a 3.x clip untrimmed, which repeats its whole file.
    """
    return (
        _npt(element.get("clip-begin") or element.get("clipBegin")),
        _npt(element.get("clip-end") or element.get("clipEnd")),
    )


def _merge(segments: list[AudioSegment]) -> list[AudioSegment]:
    """Join neighbouring clips of the same file that play back to back."""
    merged: list[AudioSegment] = []
    for segment in segments:
        previous = merged[-1] if merged else None
        contiguous = (
            previous is not None
            and previous.src == segment.src
            and previous.end is not None
            and segment.begin is not None
            and abs(previous.end - segment.begin) < _MERGE_EPSILON
        )
        if contiguous:
            merged[-1] = AudioSegment(previous.src, previous.begin, segment.end)
        else:
            merged.append(segment)
    return merged


@dataclass
class _Marks:
    """Parsed SMIL and XHTML, kept once per pack so the three cut levels share one read."""

    segments: dict[Path, tuple[AudioSegment, ...]] = field(default_factory=dict)
    parts: dict[Path, tuple[Chapter, ...]] = field(default_factory=dict)
    text: dict[Path, etree._Element | None] = field(default_factory=dict)
    anchors: dict[Path, dict[str, str]] = field(default_factory=dict)
    # The document a SMIL's bare `#anchor` points at. A DAISY 2.02 producer that
    # synced only its headings writes `<text src="#h51"/>` with no file named: the
    # words live in the pack's own text document, which is the NCC.
    document: Path | None = None


def _smil_segments(smil: Path, marks: _Marks) -> tuple[AudioSegment, ...]:
    """Resolve every `<audio>` referenced by a SMIL file, in playback order."""
    if smil in marks.segments:
        return marks.segments[smil]
    root = _parse_xml(smil)
    if root is None:
        marks.segments[smil] = ()
        return ()
    found: list[AudioSegment] = []
    for element in root.iter():
        if _localname(element) != "audio":
            continue
        src = element.get("src")
        if not src:
            continue
        target = (smil.parent / unquote(src)).resolve()
        if not target.is_file():
            log.warning("%s references a missing audio file: %s", smil.name, src)
            continue
        found.append(AudioSegment(target, *_clip(element)))
    result = tuple(_merge(found))
    marks.segments[smil] = result
    return result


def _text_document(path: Path, marks: _Marks) -> etree._Element | None:
    if path not in marks.text:
        marks.text[path] = _parse_xml(path)
    return marks.text[path]


def _anchor_words(path: Path, marks: _Marks) -> dict[str, str]:
    """Every id in a text document and the words inside it, read in one walk.

    A synced pack marks every sentence of the book, so a search per mark walks the
    whole document once per sentence: 6 000 marks on a 800 KB document took as many
    seconds. The index is built the first time the document is read and every mark
    after that is a lookup.
    """
    if path not in marks.anchors:
        root = _text_document(path, marks)
        words: dict[str, str] = {}
        if root is not None:
            for element in root.iter():
                anchor = element.get("id")
                if anchor and anchor not in words:
                    words[anchor] = " ".join("".join(element.itertext()).split())
        marks.anchors[path] = words
    return marks.anchors[path]


def _text_at(src: str, base: Path, marks: _Marks) -> str:
    """The pack's own words at a `gonxhtml.html#id` anchor, for a card to show."""
    file_part, _, anchor = unquote(src).partition("#")
    if not anchor:
        return ""
    if not file_part:
        if marks.document is None:
            return ""
        path = marks.document
    else:
        path = (base / file_part).resolve()
    return _anchor_words(path, marks).get(anchor, "")


def _smil_parts(smil: Path, marks: _Marks) -> tuple[Chapter, ...]:
    """The SMIL's audio clips, each paired with the synced text read over it.

    A DAISY 2.02 audio book marks every `<par>` it synced, so this is where the
    sentence-level cuts come from; a pack that synced nothing gives one part per
    clip with no text.
    """
    if smil in marks.parts:
        return marks.parts[smil]
    root = _parse_xml(smil)
    found: list[Chapter] = []
    if root is not None:
        for element in root.iter():
            if _localname(element) != "audio":
                continue
            src = element.get("src")
            if not src:
                continue
            target = (smil.parent / unquote(src)).resolve()
            if not target.is_file():
                continue
            par = element.getparent()
            while par is not None and _localname(par) != "par":
                par = par.getparent()
            words = ""
            if par is not None:
                text_el = next((c for c in par.iter() if _localname(c) == "text"), None)
                if text_el is not None:
                    words = _text_at(text_el.get("src") or "", smil.parent, marks)
            begin, end = _clip(element)
            found.append(
                Chapter(
                    title="",
                    segments=(AudioSegment(target, begin, end),),
                    text=words,
                    spans=(Span(words, target, begin, end),) if words else (),
                )
            )
    if not found and root is not None:
        # A text-only pack pairs every par with words and no audio at all; the
        # words are kept so the part's audio can be spoken from them later.
        for element in root.iter():
            if _localname(element) != "text":
                continue
            words = _text_at(element.get("src") or "", smil.parent, marks)
            if words:
                found.append(Chapter(title="", segments=(), text=words))
    if not found:
        found = [Chapter(title="", segments=(segment,)) for segment in _smil_segments(smil, marks)]
    marks.parts[smil] = tuple(found)
    return marks.parts[smil]


def _split_target(href: str) -> Path | None:
    """Turn an NCC `href` such as `hotl0002.smil#id` into a SMIL path."""
    file_part = unquote(href.split("#", 1)[0]).strip()
    if not file_part or not file_part.lower().endswith(".smil"):
        return None
    return Path(file_part)


_DOCUMENT_SUFFIXES = {".htm", ".html", ".xhtml"}
_SENTENCES = re.compile(r"(?<=[.!?…])\s+")


def _sentences(text: str) -> list[str]:
    return [sentence for sentence in (s.strip() for s in _SENTENCES.split(text)) if sentence]


def _split_document(href: str) -> tuple[Path, str] | None:
    """Turn an NCX `content src` such as `book.xhtml#ch1` into a document and an anchor.

    An EPUB has no SMIL at all: its whole book is one or a few XHTML files, and its
    table of contents points into them.
    """
    file_part, _, anchor = unquote(href).partition("#")
    file_part = file_part.strip()
    if not file_part or Path(file_part).suffix.lower() not in _DOCUMENT_SUFFIXES:
        return None
    return Path(file_part), anchor


def _anchors_by_document(entries: list[_TocEntry]) -> dict[Path, set[str]]:
    """Every anchor the outline names, grouped by the file it sits in."""
    found: dict[Path, set[str]] = {}
    for entry in entries:
        document = _split_document(entry.target)
        if document is not None and document[1]:
            found.setdefault(document[0], set()).add(document[1])
    return found


def _document_texts(path: Path, anchors: set[str], marks: _Marks) -> dict[str, str]:
    """The words of a document that belong to each anchor: its own, up to the next.

    The outline of an EPUB names a heading inside the file rather than a range, so
    a chapter is everything the reader reaches before the next named heading. Text
    is walked in document order and handed to the last anchor passed, which keeps a
    section's nested markup with the section that opened it.
    """
    root = _text_document(path, marks)
    if root is None:
        return {}
    words: dict[str, list[str]] = {}
    current: str | None = None
    for element in root.iter():
        if not isinstance(element.tag, str):
            continue
        anchor = element.get("id")
        if anchor in anchors:
            current = anchor
            words.setdefault(anchor, [])
        if current is None:
            continue
        if element.text:
            words[current].append(element.text)
        if element.tail:
            words[current].append(element.tail)
    return {anchor: " ".join(" ".join(part).split()) for anchor, part in words.items()}


def _first_words(body: str) -> str:
    """The line a card shows for a part that has no synced text of its own."""
    return next((s for s in _sentences(body) if len(s) >= 5), "")


def _ncc_entries(root: etree._Element) -> list[_TocEntry]:
    """Read the NCC outline: h1..h6 are TOC items, `class="title"` is front matter."""
    body = next((el for el in root.iter() if _localname(el) == "body"), None)
    if body is None:
        return []
    entries: list[_TocEntry] = []
    for element in body.iter():
        level = _HEADING_LEVELS.get(_localname(element).lower())
        if level is None:
            continue
        classes = (element.get("class") or "").lower().split()
        anchor = next((child for child in element.iter() if _localname(child) == "a"), None)
        href = (anchor.get("href") if anchor is not None else None) or element.get("href") or ""
        label_source = anchor if anchor is not None else element
        title = "".join(label_source.itertext()).strip()
        entries.append(
            _TocEntry(
                level=level,
                title=title,
                target=href,
                is_front_matter="title" in classes,
            )
        )
    return entries


def _child(element: etree._Element, name: str) -> etree._Element | None:
    return next((c for c in element if _localname(c).lower() == name), None)


def _ncx_level(nav_point: etree._Element, level: int) -> int:
    """How deep a navPoint sits: a DAISY 3 `class="h2"`, else an EPUB playLevel, else nesting."""
    heading = next(
        (c for c in (nav_point.get("class") or "").lower().split() if c[1:].isdigit() and c[0] == "h"), None
    )
    if heading is not None:
        return int(heading[1:])
    declared = nav_point.get("playLevel") or nav_point.get("level")
    return int(declared) if declared and declared.isdigit() else level


def _ncx_entries(root: etree._Element) -> list[_TocEntry]:
    """Read an NCX outline: the table of contents of a DAISY 3 pack or an EPUB.

    Both spell the element `navPoint` and put the label inside a `navLabel`, so
    names are matched without regard to case. A DAISY 3 label carries an `<audio>`
    of its own, which is the chapter's name already recorded by its producer.
    """
    nav_map = next((c for c in root if _localname(c).lower() == "navmap"), None)
    return _ncx_points(nav_map) if nav_map is not None else []


def _ncx_points(nav_map: etree._Element, level: int = 1) -> list[_TocEntry]:
    entries: list[_TocEntry] = []
    for nav_point in nav_map:
        if _localname(nav_point).lower() != "navpoint":
            continue
        current_level = _ncx_level(nav_point, level)
        label = _child(nav_point, "navlabel")
        if label is None:
            label = nav_point
        text = _child(label, "text")
        title = (text.get("value") or "".join(text.itertext())).strip() if text is not None else ""
        content = _child(nav_point, "content")
        target = content.get("src", "") if content is not None else ""
        if target:
            entries.append(_TocEntry(level=current_level, title=title, target=target))
        entries.extend(_ncx_points(nav_point, current_level + 1))
    return entries


def _ncx_front_matter(root: etree._Element, base: Path) -> list[AudioSegment]:
    """The spoken title and author a DAISY 3 NCX carries, as the book's front matter.

    A 2.02 pack puts its front matter in the NCC outline; a 3.x pack has no NCC and
    says it once, in the NCX header, as clips of its first audio file.
    """
    segments: list[AudioSegment] = []
    for element in root:
        if _localname(element).lower() not in {"doctitle", "docauthor"}:
            continue
        audio = _child(element, "audio")
        src = audio.get("src") if audio is not None else None
        if not src:
            continue
        target = (base / unquote(src)).resolve()
        if not target.is_file():
            log.warning("%s names a missing front matter clip: %s", root.tag, src)
            continue
        segments.append(AudioSegment(target, *_clip(audio)))
    return segments


def _opening_text(parts: tuple[Chapter, ...]) -> str:
    """The first words spoken in a part, skipping a read-out section number."""
    texts = [part.text for part in parts if part.text]
    return next((t for t in texts if len(t) >= 5), texts[0] if texts else "")


def _join_texts(texts: Iterable[str]) -> str:
    """Join the pack's synced texts, dropping a text repeated by the clips after it.

    A pack that synced only its headings writes the heading once in the `<par>` that
    holds the whole level, and every clip of that level inherits it: joining them as
    they come would read the heading once per sentence.
    """
    joined: list[str] = []
    for text in texts:
        if not joined or text != joined[-1]:
            joined.append(text)
    return " ".join(joined)


def _synced_words(parts: tuple[Chapter, ...], title: str) -> bool:
    """Whether the pack wrote words of its own over these clips.

    A pack that synced only the level's heading puts it in the `<par>` above every clip
    of that level, so every clip inherits it; those parts have no words of their own.
    """
    heading = title.strip()
    return any(text and text != heading for text in (part.text.strip() for part in parts))


def _chapter_level(entries: list[_TocEntry]) -> int:
    """The outline level that holds the book's chapters.

    The shallowest level is usually it, but a DAISY 3 outline often has one `h1`
    wrapping everything, which would give a single part as long as the book; the
    level below it holds the real chapters.
    """
    levels = sorted({e.level for e in entries if not e.is_front_matter})
    for level in levels:
        if sum(1 for e in entries if not e.is_front_matter and e.level == level) > 1:
            return level
    return levels[0] if levels else 1


def _group(
    entries: list[_TocEntry], pack_root: Path, marks: _Marks, level: str = "chapter"
) -> tuple[list[_Draft], list[AudioSegment]]:
    """Cut the book at `level`: one part per chapter heading, per TOC entry, or per synced mark."""
    chapters: list[_Draft] = []
    front_matter: list[AudioSegment] = []
    chapter_level = _chapter_level(entries)
    documents = _anchors_by_document(entries)

    def place(draft: _Draft) -> None:
        """Open a part, or add it to the one before at a level below the chapters."""
        if level == "chapter" and draft.level > chapter_level and chapters:
            chapters[-1].segments.extend(draft.segments)
            chapters[-1].spans.extend(draft.spans)
            chapters[-1].body = _join_texts(t for t in (chapters[-1].body, draft.body) if t)
        else:
            chapters.append(draft)

    group = 0
    for entry in entries:
        # A heading at the chapter level opens a chapter; whatever hangs under it
        # belongs to it, however finely the book is cut below that.
        if not entry.is_front_matter and entry.level <= chapter_level:
            group += 1
        smil = _split_target(entry.target)
        if smil is not None:
            resolved = (pack_root / smil).resolve()
            if not resolved.is_file():
                log.warning("TOC entry %r points at a missing SMIL: %s", entry.title, smil)
                continue
            if entry.is_front_matter:
                front_matter.extend(_smil_segments(resolved, marks))
                continue
            parts = _smil_parts(resolved, marks)
            if level == "sentence" and _synced_words(parts, entry.title):
                chapters.extend(
                    _Draft(
                        title="",
                        segments=list(p.segments),
                        text=p.text,
                        body=p.text,
                        spans=list(p.spans),
                        group=group,
                    )
                    for p in parts
                )
                continue
            place(
                _Draft(
                    title=entry.title or f"Part {len(chapters) + 1}",
                    segments=list(_smil_segments(resolved, marks)),
                    text=_opening_text(parts),
                    body=_join_texts(p.text for p in parts if p.text),
                    spans=[s for p in parts for s in p.spans],
                    level=entry.level,
                    group=group,
                )
            )
            continue

        document = _split_document(entry.target)
        if document is None:
            continue
        path, anchor = document
        resolved = (pack_root / path).resolve()
        if not resolved.is_file():
            log.warning("TOC entry %r points at a missing document: %s", entry.title, path)
            continue
        body = _document_texts(resolved, documents[path], marks).get(anchor, "")
        if level == "sentence":
            chapters.extend(
                _Draft(title="", text=sentence, body=sentence, group=group) for sentence in _sentences(body)
            )
            continue
        place(
            _Draft(
                title=entry.title or f"Part {len(chapters) + 1}",
                text=_first_words(body),
                body=body,
                level=entry.level,
                group=group,
            )
        )
    return chapters, front_matter


def _toc_entries(pack: DaisyPack) -> tuple[list[_TocEntry], etree._Element]:
    """The pack's table of contents, from the NCC when it has one."""
    toc = pack.toc_file
    if toc is None:
        raise DaisyPackError("pack has no table of contents")
    root = _parse_xml(toc)
    if root is None:
        raise DaisyPackError(f"could not parse the table of contents: {toc.name}")
    return (_ncc_entries(root) if toc is pack.ncc else _ncx_entries(root)), root


def extract(pack: DaisyPack, level: str = "chapter") -> Book:
    """Build the part list for a DAISY 2.02 audio pack, cut at `level`.

    `chapter` follows the top TOC headings, `paragraph` every TOC heading, and
    `sentence` every mark the pack itself synced; a pack with no synced text
    gives the same parts at `sentence` as at `paragraph`, only silent cards.
    """
    if level not in CUT_LEVELS:
        raise DaisyPackError(f"unknown cut level: {level}")

    entries, root = _toc_entries(pack)
    metadata = _ncc_meta(root)

    if pack.opf is not None:
        opf_root = _parse_xml(pack.opf)
        if opf_root is not None:
            for key, value in _opf_meta(opf_root).items():
                metadata.setdefault(f"dc:{key}", value)

    marks = _Marks(document=pack.ncc)
    drafts, front_matter = _group(entries, pack.root, marks, level)
    if not front_matter and pack.ncc is None:
        front_matter = _ncx_front_matter(root, pack.root)
    front_matter = _merge(front_matter)
    chapters = tuple(
        Chapter(
            title=d.title or f"Part {index + 1}",
            segments=tuple(_merge(d.segments)),
            text=d.text,
            body=d.body,
            spans=tuple(d.spans),
            group=d.group,
        )
        for index, d in enumerate(drafts)
        if d.segments or d.body
    )

    return Book(
        title=metadata.get("dc:title") or pack.root.name,
        chapters=chapters,
        front_matter=tuple(front_matter),
        author=_credited(metadata.get("dc:creator")),
        language=metadata.get("dc:language") or None,
        publisher=metadata.get("dc:publisher") or None,
        narrator=metadata.get("ncc:narrator") or None,
        identifier=metadata.get("dc:identifier") or None,
        source=metadata.get("dc:source") or None,
    )
