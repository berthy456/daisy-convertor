"""The book's own words, kept beside the audio they were read over.

A DAISY pack syncs its text to its audio. Every pack this tool writes keeps that
text: the whole book as plain text, and one `.lrc` and `.srt` per part, timed
against the file that part was rendered into. A story teller plays none of it —
it is the archive of what the pack said.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from pathlib import Path

from . import audio
from .extractor import Book, Chapter


@dataclass(frozen=True)
class Line:
    """One synced line of the part's text, on that part's own timeline."""

    start: float
    end: float
    text: str


def digits(total: int) -> int:
    """How many digits the part numbers need: three, or as many as the book has parts.

    A book cut at sentences runs past a thousand parts, and `999` sorts after `1000`:
    the padding has to cover the largest number the pack will write.
    """
    return max(3, len(str(total)))


def book_text(book: Book) -> str:
    """The book as plain text, part by part, from the pack's own words."""
    blocks = []
    for index, chapter in enumerate(book.chapters, start=1):
        body = (chapter.body or chapter.text).strip()
        if not body:
            continue
        heading = chapter.title or f"{index}"
        blocks.append(f"{index:0{digits(len(book.chapters))}}  {heading}\n{'-' * 40}\n{body}")
    return "\n\n".join(blocks) + "\n" if blocks else ""


def timings(chapter: Chapter, length: float | None) -> list[Line]:
    """Put the pack's synced spans on the timeline of the mp3 they were rendered into.

    The SMIL times are trims inside each source clip, and the part is those clips
    end to end, so the offsets are their durations added up. The pack rounds its
    times, so the sum lands a little short of the file that was written: scaling by
    the measured length keeps the last line inside the part instead of after it.
    """
    spans = [span for span in chapter.spans if span.text]
    if not spans:
        return []
    probed: dict[Path, float] = {}
    starts: list[float] = []
    offset = 0.0
    for span in spans:
        starts.append(offset)
        if span.begin is not None and span.end is not None:
            offset += max(span.end - span.begin, 0.0)
        else:
            # A clip the pack never trimmed plays whole, so only its own length is known.
            offset += probed.setdefault(span.src, audio.duration(span.src) or 0.0)
    scale = length / offset if length and offset > 0 else 1.0
    starts = [start * scale for start in starts]
    ends = starts[1:] + [max(length or offset, starts[-1])]

    # A pack that synced only its headings repeats the heading over every clip of the
    # level; those clips are one line of text, so they become one line covering them.
    lines: list[Line] = []
    for start, end, span in zip(starts, ends, spans):
        text = " ".join(span.text.split())
        if lines and lines[-1].text == text:
            lines[-1] = replace(lines[-1], end=end)
        else:
            lines.append(Line(start, end, text))
    return lines


def _lrc_clock(seconds: float) -> str:
    hundredths = int(round(seconds * 100))
    minutes, rest = divmod(hundredths, 6000)
    return f"[{minutes:02d}:{rest // 100:02d}.{rest % 100:02d}]"


def _srt_clock(seconds: float) -> str:
    millis = int(round(seconds * 1000))
    hours, rest = divmod(millis, 3_600_000)
    minutes, rest = divmod(rest, 60_000)
    return f"{hours:02d}:{minutes:02d}:{rest // 1000:02d},{rest % 1000:03d}"


def lrc(lines: list[Line], title: str, byline: str) -> str:
    """LRC: one `[mm:ss.xx]` stamp per line, which is what a lyric screen reads."""
    head = [f"[ti:{title}]"]
    if byline:
        head.append(f"[au:{byline}]")
    body = [f"{_lrc_clock(line.start)}{line.text}" for line in lines]
    return "\n".join(head + body) + "\n"


def srt(lines: list[Line]) -> str:
    """SubRip: the numbered block most video players already know how to show."""
    blocks = [
        f"{index}\n{_srt_clock(line.start)} --> {_srt_clock(line.end)}\n{line.text}"
        for index, line in enumerate(lines, start=1)
    ]
    return "\n\n".join(blocks) + "\n"


def synced_text(
    chapter: Chapter, folder: Path, stem: str, length: float | None, title: str, byline: str
) -> list[Path]:
    """The part's text as a lyric file and as a subtitle file, named after its mp3.

    Players look for these beside the audio: VLC and mpv pick up the `.srt`, most
    lyric screens pick up the `.lrc`. A pack that synced nothing gets neither.
    """
    lines = timings(chapter, length)
    if not lines:
        return []
    folder.mkdir(parents=True, exist_ok=True)
    lyric = folder / f"{stem}.lrc"
    lyric.write_text(lrc(lines, title, byline), encoding="utf-8")
    subtitle = folder / f"{stem}.srt"
    subtitle.write_text(srt(lines), encoding="utf-8")
    return [lyric, subtitle]
