"""Write a book back out as a DAISY 2.02 pack.

`ncc.html` carries the book's text and its table of contents; every part gets one
`.smil` pairing the audio with the words it was read over, and one `.mp3`. It is the
layout a DAISY 2.02 producer writes, and `extractor.py` reads it back.

The audio is the pack's own MP3 wherever its clips join whole, and this tool's own
spec — 128 kbps mono at 44.1 kHz — wherever a clip has to be trimmed or a chapter has
to be spoken.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path
from xml.etree import ElementTree

from . import __version__, audio
from .extractor import Book, Chapter, Span
from .telmi_builder import Announce, narrate
from .textfiles import digits

log = logging.getLogger(__name__)

XHTML = "http://www.w3.org/1999/xhtml"
XML_LANG = "http://www.w3.org/XML/1998/namespace"
FORMAT = "Daisy 2.02"

# A chapter's length is measured twice: once off the file this tool wrote, once by
# adding up the trim points the pack gave its synced text. Joining whole MP3s keeps
# each file's own padding, so the chapter comes out a little longer than the clips it
# was made of — about 0.12 s per clip on this repo's reference pack. Further apart
# than that, the marks no longer say where the words are, and the chapter is written
# as one piece of audio.
_CLIP_SLACK = 0.25


@dataclass(frozen=True)
class _Part:
    """One part's files, and the ids its NCC heading and its SMIL clips share."""

    stem: str
    title: str
    heading: str
    audio: Path
    smil: Path
    duration: float
    elapsed: float
    paragraphs: tuple[str, ...]
    marks: tuple[tuple[str, float, float], ...]


def _prefix(title: str) -> str:
    """The short name a DAISY pack prefixes its part files with: `gonrub0001.mp3`."""
    letters = "".join(character for character in title if character.isascii() and character.isalnum())
    return (letters[:6] or "book").lower()


def _clock(seconds: float, millis: bool = False) -> str:
    """`00:22:56`, or `00:00:07.197` with `millis`: the shape the timing metadata takes."""
    total_ms = round(seconds * 1000)
    hours, rest = divmod(total_ms, 3_600_000)
    minutes, rest = divmod(rest, 60_000)
    secs, thousandths = divmod(rest, 1000)
    tail = f".{thousandths:03d}" if millis else ""
    return f"{hours:02d}:{minutes:02d}:{secs:02d}{tail}"


def _npt(seconds: float) -> str:
    """A SMIL clip's place on its own audio: `npt=83.500s`."""
    return f"npt={max(0.0, seconds):.3f}s"


def _dur(seconds: float) -> str:
    """A SMIL `<seq>`'s length: `83.500s`."""
    return f"{max(0.0, seconds):.3f}s"


def _length(span: Span) -> float | None:
    """Seconds one synced clip plays: the pack's trim points, or the whole file."""
    if span.begin is not None and span.end is not None:
        return span.end - span.begin
    total = audio.duration(span.src)
    if total is None:
        return None
    if span.begin is not None:
        return total - span.begin
    if span.end is not None:
        return span.end
    return total


def _synced(chapter: Chapter) -> list[tuple[str, float, float]]:
    """The pack's marks as (words, seconds from the chapter's start, seconds long).

    Empty when a mark has no words or no length to place it at: these trim points are
    the only clock this writer has, and a chapter it cannot time gets no sync.
    """
    marks: list[tuple[str, float, float]] = []
    for span in chapter.spans:
        words = span.text.strip()
        length = _length(span)
        if not words or length is None or length <= 0:
            return []
        if marks and marks[-1][0] == words:
            # A pack that wrote one mark for a whole level repeats its heading on
            # every clip of that level: the words cover all of them.
            previous = marks[-1]
            marks[-1] = (words, previous[1], previous[2] + length)
            continue
        begin = marks[-1][2] if marks else 0.0
        marks.append((words, begin, begin + length))
    return marks


def _paragraphs(chapter: Chapter) -> list[str]:
    """The chapter's words when they cannot be timed: one line per paragraph."""
    return [line for line in (part.strip() for part in chapter.body.splitlines()) if line]


def _meta(parent: ElementTree.Element, name: str, content: str) -> None:
    ElementTree.SubElement(parent, "meta", {"name": name, "content": content})


def _book_meta(head: ElementTree.Element, book: Book) -> None:
    _meta(head, "dc:format", FORMAT)
    _meta(head, "dc:title", book.title)
    for name, value in (
        ("dc:creator", book.author),
        ("dc:publisher", book.publisher),
        ("dc:language", book.language),
        ("dc:identifier", book.identifier),
        ("dc:source", book.source),
        ("ncc:narrator", book.narrator),
    ):
        if value:
            _meta(head, name, value)
    _meta(head, "ncc:generator", f"daisy-convertor {__version__}")


def ncc(book: Book, front: _Part | None, parts: list[_Part], files: int) -> str:
    """The NCC: the book's metadata, its outline, and the text every SMIL points at."""
    ElementTree.register_namespace("", XHTML)
    root = ElementTree.Element(f"{{{XHTML}}}html")
    if book.language:
        root.set(f"{{{XML_LANG}}}lang", book.language)
    head = ElementTree.SubElement(root, f"{{{XHTML}}}head")
    ElementTree.SubElement(head, f"{{{XHTML}}}title").text = book.title
    _book_meta(head, book)
    _meta(head, "ncc:charset", "utf-8")
    _meta(head, "ncc:totalTime", _clock(sum(part.duration for part in [front, *parts] if part)))
    _meta(head, "ncc:tocItems", str(len(parts)))
    _meta(head, "ncc:depth", "2" if front is not None else "1")
    _meta(head, "ncc:files", str(files))
    _meta(head, "ncc:setInfo", "1 of 1")
    for name in ("ncc:footnotes", "ncc:sidebars", "ncc:pageNormal"):
        _meta(head, name, "0")

    body = ElementTree.SubElement(root, f"{{{XHTML}}}body")
    title = ElementTree.SubElement(body, f"{{{XHTML}}}h1", {"class": "title"})
    if front is not None:
        title.set("id", front.heading)
        link = ElementTree.SubElement(title, f"{{{XHTML}}}a", {"href": f"{front.smil.name}#{front.stem}"})
        link.text = book.title
    else:
        title.text = book.title
    for part in parts:
        heading = ElementTree.SubElement(body, f"{{{XHTML}}}h2", {"id": part.heading})
        link = ElementTree.SubElement(heading, f"{{{XHTML}}}a", {"href": f"{part.smil.name}#{part.stem}"})
        link.text = part.title
        for index, words in enumerate(part.paragraphs, start=1):
            ElementTree.SubElement(body, f"{{{XHTML}}}p", {"id": f"{part.stem}p{index}"}).text = words
    ElementTree.indent(root)
    return f'<?xml version="1.0" encoding="UTF-8"?>\n{ElementTree.tostring(root, encoding="unicode")}\n'


def smil(part: _Part) -> str:
    """One part's SMIL: its audio cut into the pieces the NCC's paragraphs were read over."""
    root = ElementTree.Element("smil")
    head = ElementTree.SubElement(root, "head")
    _meta(head, "dc:format", FORMAT)
    _meta(head, "ncc:generator", f"daisy-convertor {__version__}")
    _meta(head, "ncc:timeInThisSmil", _clock(part.duration, millis=True))
    _meta(head, "ncc:totalElapsedTime", _clock(part.elapsed, millis=True))
    body = ElementTree.SubElement(root, "body")
    seq = ElementTree.SubElement(body, "seq", {"id": part.stem, "dur": _dur(part.duration)})
    last = len(part.marks)
    for index, (words, begin, end) in enumerate(part.marks, start=1):
        par = ElementTree.SubElement(seq, "par", {"id": f"{part.stem}p{index}"})
        ElementTree.SubElement(
            par,
            "audio",
            {
                "src": part.audio.name,
                "clip-begin": _npt(begin),
                # The last paragraph holds to the end of the audio: the file is a
                # little longer than the clips it was joined from, and a reader
                # should not sit through that tail with nothing highlighted.
                "clip-end": _npt(part.duration if index == last else min(end, part.duration)),
            },
        )
        ElementTree.SubElement(par, "text", {"src": f"ncc.html#{part.stem}p{index}"})
    if not part.marks:
        par = ElementTree.SubElement(seq, "par", {"id": f"{part.stem}p1"})
        ElementTree.SubElement(
            par, "audio", {"src": part.audio.name, "clip-begin": _npt(0.0), "clip-end": _npt(part.duration)}
        )
    ElementTree.indent(root)
    return f'<?xml version="1.0" encoding="UTF-8"?>\n{ElementTree.tostring(root, encoding="unicode")}\n'


def _write(text: str, dest: Path) -> Path:
    dest.write_text(text, encoding="utf-8")
    return dest


def _record(chapter: Chapter, announce: Announce | None, dest: Path) -> float:
    """Put one chapter's audio on disk and say how long it came out at."""
    if chapter.segments:
        log.info("writing %s (%d audio clip group(s)) <- %s", dest.name, len(chapter.segments), chapter.title)
        audio.render(chapter.segments, dest)
    else:
        log.info("speaking %s (%d characters) <- %s", dest.name, len(chapter.body), chapter.title)
        narrate(chapter.body, announce, dest)
    return audio.duration(dest) or 0.0


def export(book: Book, folder: Path, announce: Announce | None = None) -> list[Path]:
    """Write the book as a DAISY 2.02 pack in `folder`, and list the files written."""
    folder.mkdir(parents=True, exist_ok=True)
    prefix = _prefix(book.title)
    width = digits(len(book.chapters))
    written: list[Path] = []

    front = None
    elapsed = 0.0
    if book.front_matter:
        stem = f"{prefix}0000"
        audio_file = folder / f"{stem}.mp3"
        audio.render(book.front_matter, audio_file)
        duration = audio.duration(audio_file) or 0.0
        front = _Part(
            stem=stem,
            title=book.title,
            heading=f"{prefix}h0",
            audio=audio_file,
            smil=folder / f"{stem}.smil",
            duration=duration,
            elapsed=elapsed,
            paragraphs=(),
            marks=(),
        )
        written.append(audio_file)
        elapsed += duration

    parts: list[_Part] = []
    for index, chapter in enumerate(book.chapters, start=1):
        stem = f"{prefix}{index:0{width}}"
        audio_file = folder / f"{stem}.mp3"
        duration = _record(chapter, announce, audio_file)
        marks = _synced(chapter)
        slack = _CLIP_SLACK * max(1, len(chapter.segments))
        if not marks or abs(marks[-1][2] - duration) > slack:
            log.info("%s: its %d mark(s) do not cover the %s s of audio written for it", stem, len(marks), duration)
            marks = ()
        parts.append(
            _Part(
                stem=stem,
                title=chapter.title,
                heading=f"{prefix}h{index}",
                audio=audio_file,
                smil=folder / f"{stem}.smil",
                duration=duration,
                elapsed=elapsed,
                paragraphs=tuple(words for words, _, _ in marks) or tuple(_paragraphs(chapter)),
                marks=marks,
            )
        )
        written.append(audio_file)
        elapsed += duration

    for part in [front, *parts]:
        if part is not None:
            written.append(_write(smil(part), part.smil))
    written.append(_write(ncc(book, front, parts, len(written) + 1), folder / "ncc.html"))
    log.info("wrote %d chapter(s) as a %s pack", len(parts), FORMAT)
    return written
