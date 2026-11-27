"""Write the mp3 playlist pack: one mp3 per chapter, a spoken chapter name before each, the book's own text, and the playlists.

This pack is not a story graph. It is a folder an ordinary player can open: the
chapters keep their recorded audio, each is named by a short spoken interlude,
and four playlists name the same files in the same order so a player reads
whichever one it knows.

Where the pack synced its text to the audio, every chapter also gets a `.lrc` and
a `.srt` of the same name, which is what a lyric screen or a subtitle reader looks
for beside the file it plays.
"""

from __future__ import annotations

import logging
import re
import shutil
from dataclasses import dataclass, replace
from pathlib import Path
from urllib.parse import quote
from xml.etree import ElementTree

from . import audio, tts
from .extractor import Book
from .telmi_builder import Announce, narrate
from .textfiles import book_text, digits, synced_text

log = logging.getLogger(__name__)

# Every player reads an M3U; `.m3u8` is the same file in UTF-8, which is what a
# title with accents needs. `.pls` and `.xspf` cover the players that want a
# playlist with real metadata rather than comments.
PLAYLISTS = ("m3u8", "m3u", "pls", "xspf")

_XSPF_NS = "http://xspf.org/ns/0/"
_UNSAFE = re.compile(r'[<>:"/\\|?*\x00-\x1f]')
_TEXT_SUFFIXES = {".xml", ".smil", ".opf", ".ncx", ".html", ".htm", ".xhtml"}


@dataclass(frozen=True)
class Track:
    """One mp3 in the pack, as a playlist names it."""

    file: str
    title: str
    duration: float | None = None

    @property
    def stem(self) -> str:
        return Path(self.file).stem


def safe_name(value: str) -> str:
    """A file name every player's file system accepts, accents and all."""
    cleaned = re.sub(r"\s{2,}", " ", _UNSAFE.sub(" ", value)).strip(" .")
    return cleaned or "story"


def chapter_name(number: int, title: str, book_title: str, total: int = 0) -> str:
    """`001 - Chapter title - Book title`: the number leads so a player that sorts
    by name still plays the book in order, and the `-intro` interlude sorts in
    front of the chapter it introduces."""
    parts = [f"{number:0{digits(total)}}"] + ([title] if title else []) + [book_title]
    return safe_name(" - ".join(parts))


def clock(seconds: float | None) -> str:
    """`1:23:45`, or `?` when the pack gave no length."""
    if seconds is None:
        return "?"
    whole = int(round(seconds))
    hours, rest = divmod(whole, 3600)
    minutes, seconds = divmod(rest, 60)
    return f"{hours}:{minutes:02d}:{seconds:02d}" if hours else f"{minutes}:{seconds:02d}"


def _display(track: Track, byline: str) -> str:
    return f"{byline} - {track.title}" if byline else track.title


def m3u(tracks: list[Track], byline: str) -> str:
    """Extended M3U: `#EXTINF:seconds,Artist - Title` over the file name."""
    lines = ["#EXTM3U"]
    for track in tracks:
        lines.append(f"#EXTINF:{int(track.duration) if track.duration else -1},{_display(track, byline)}")
        lines.append(track.file)
    return "\n".join(lines) + "\n"


def pls(tracks: list[Track], byline: str) -> str:
    """PLS v2: an .ini-shaped list, lengths in seconds."""
    lines = ["[playlist]"]
    for index, track in enumerate(tracks, start=1):
        lines.append(f"File{index}={track.file}")
        lines.append(f"Title{index}={_display(track, byline)}")
        lines.append(f"Length{index}={int(track.duration) if track.duration else -1}")
    lines.append(f"NumberOfEntries={len(tracks)}")
    lines.append("Version=2")
    return "\n".join(lines) + "\n"


def xspf(tracks: list[Track], title: str, author: str | None) -> str:
    """XSPF: the XML playlist, whose duration is in milliseconds."""
    root = ElementTree.Element("playlist", {"version": "1", "xmlns": _XSPF_NS})
    ElementTree.SubElement(root, "title").text = title
    if author:
        ElementTree.SubElement(root, "creator").text = author
    listing = ElementTree.SubElement(root, "trackList")
    for index, track in enumerate(tracks, start=1):
        item = ElementTree.SubElement(listing, "track")
        ElementTree.SubElement(item, "location").text = quote(track.file)
        ElementTree.SubElement(item, "title").text = track.title
        ElementTree.SubElement(item, "album").text = title
        ElementTree.SubElement(item, "trackNum").text = str(index)
        if author:
            ElementTree.SubElement(item, "creator").text = author
        if track.duration:
            ElementTree.SubElement(item, "duration").text = str(int(track.duration * 1000))
    ElementTree.indent(root)
    return f'<?xml version="1.0" encoding="UTF-8"?>\n{ElementTree.tostring(root, encoding="unicode")}\n'


def details(book: Book, chapters: list[Track]) -> str:
    """What the pack says about itself, and what it turned into."""
    lines = [book.title]
    for label, value in (
        ("Author", book.author),
        ("Narrator", book.narrator),
        ("Publisher", book.publisher),
        ("Language", book.language),
        ("Identifier", book.identifier),
        ("Source", book.source),
    ):
        if value:
            lines.append(f"{label}: {value}")
    known = [track.duration for track in chapters]
    lines.append(f"Chapters: {len(chapters)}")
    lines.append(f"Length: {clock(sum(known)) if all(known) else clock(None)}")
    lines.append("")
    for index, track in enumerate(chapters, start=1):
        lines.append(f"{index:0{digits(len(chapters))}}  {clock(track.duration)}  {track.title}")
    return "\n".join(lines) + "\n"


def _interludes(book: Book, announce: Announce | None, folder: Path, chapters: list[Track]) -> list[Track]:
    """A spoken chapter name before every chapter.

    The card wording is left behind here: a plain player has no OK button, so the
    interlude says the number and the name and nothing else.
    """
    if announce is None or announce.mode == "off":
        return []
    voice = replace(announce, mode="number_title")
    phrases = [
        voice.phrase(index, len(chapters), book.chapters[index - 1].title) or ""
        for index in range(1, len(chapters) + 1)
    ]
    dests = [folder / f"{track.stem}-intro.mp3" for track in chapters]
    # The interlude is heard between two chapters, so it is levelled against them.
    story = audio.loudness(folder / chapters[0].file)
    try:
        tts.synthesize_all(
            phrases,
            announce.language,
            dests,
            announce.engine,
            announce.voice,
            announce.command,
            announce.python,
            announce.clip,
            announce.device,
            announce.speed,
            announce.expression,
            tts.card_loudness(story),
        )
    except tts.TtsError as exc:
        # The chapters are already written: an interlude that cannot be spoken is
        # reported, not a reason to throw the pack away.
        log.error("the chapter names could not be spoken: %s", exc)
        return []
    return [Track(dest.name, phrase, audio.duration(dest)) for dest, phrase in zip(dests, phrases)]


def _copy_source(book: Book, folder: Path, source: Path | None, pack_root: Path | None) -> list[Path]:
    """The pack's own document, when the pack is text rather than audio.

    An EPUB is copied whole; a text-only DAISY pack is copied as the XML and SMIL
    it was written in, which keeps the structure the plain text loses.
    """
    if source is not None and source.suffix.lower() == ".epub":
        dest = folder / safe_name(source.name)
        shutil.copyfile(source, dest)
        return [dest]
    if pack_root is None or any(chapter.segments for chapter in book.chapters):
        return []
    copied = []
    for path in sorted(pack_root.rglob("*")):
        if path.is_file() and path.suffix.lower() in _TEXT_SUFFIXES:
            dest = folder / "source" / path.relative_to(pack_root)
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(path, dest)
            copied.append(dest)
    return copied


def export(
    book: Book,
    announce: Announce | None,
    folder: Path,
    source: Path | None = None,
    pack_root: Path | None = None,
) -> list[Path]:
    """Write the pack: the chapters, their interludes, the text files and the playlists."""
    title = safe_name(book.title)
    folder.mkdir(parents=True, exist_ok=True)
    written: list[Path] = []

    # A player shows one name per track: the writer first, the reader when the
    # pack only knows who read it.
    byline = book.author or book.narrator or ""
    chapters: list[Track] = []
    for index, chapter in enumerate(book.chapters, start=1):
        name = chapter_name(index, chapter.title, title, len(book.chapters))
        dest = folder / f"{name}.mp3"
        if chapter.segments:
            log.info("rendering %s (%d audio clip group(s)) <- %s", name, len(chapter.segments), chapter.title)
            audio.render(chapter.segments, dest)
        else:
            log.info("speaking %s (%d characters) <- %s", name, len(chapter.body), chapter.title)
            narrate(chapter.body, announce, dest)
        length = audio.duration(dest)
        chapters.append(Track(dest.name, chapter.title or str(index), length))
        written.append(dest)
        written += synced_text(chapter, folder, name, length, book.title, byline)

    interludes = _interludes(book, announce, folder, chapters)
    written += [folder / track.file for track in interludes]

    order = sorted(chapters + interludes, key=lambda track: track.file)
    for extension in PLAYLISTS:
        playlist = folder / f"{title}.{extension}"
        body = xspf(order, book.title, byline or None) if extension == "xspf" else _plain(extension, order, byline)
        playlist.write_text(body, encoding="utf-8")
        written.append(playlist)

    notes = folder / f"{title}.txt"
    notes.write_text(details(book, chapters), encoding="utf-8")
    written.append(notes)
    text = book_text(book)
    if text:
        body_file = folder / f"{title} - text.txt"
        body_file.write_text(text, encoding="utf-8")
        written.append(body_file)
    written += _copy_source(book, folder, source, pack_root)
    return written


def _plain(extension: str, tracks: list[Track], byline: str) -> str:
    return pls(tracks, byline) if extension == "pls" else m3u(tracks, byline)
