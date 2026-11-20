"""The cover a DAISY audio pack does not carry.

A 2.02 pack has no image, but its metadata names the print book it was read
from: `dc:source` holds that book's ISBN. No one service has every book, so the
app asks three of them in order and keeps the first real picture. Each answers
with no key and with `Access-Control-Allow-Origin: *`, which is what lets the
web page ask the same questions.

isbndb.com carries covers for French titles the others miss, but its API needs
a paid key and its pages refuse anything but a browser. Google Books has them
too; its keyless API is one shared quota, spent most days before anyone asks.
"""

from __future__ import annotations

import json
import logging
import re
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import TYPE_CHECKING, Callable

if TYPE_CHECKING:
    from .extractor import Book

log = logging.getLogger("daisy2telmi")

# The app and the web page ask the same services; saying who asks is what keeps
# the request welcome.
USER_AGENT = "daisy2telmi (+https://github.com/berthy456/daisy2telmi)"
TIMEOUT = 12.0
# Open Library answers a cover it does not have with a 1x1 GIF of 43 bytes.
PLACEHOLDER_BYTES = 200
# Apple serves its covers at any of a few sizes; 600 is the largest it gives
# without a key, and the Telmi screen is 480 across.
APPLE_ART_SIZE = "600x600bb"


class CoverError(Exception):
    """The lookup could not be made, as opposed to made and answered with nothing."""


def isbn_of(book: Book) -> str | None:
    """The ISBN of the print book the pack was read from.

    DAISY puts it in `dc:source`. `dc:identifier` is the DAISY edition's own
    number (`FR-ADV-1310715633`), which no cover service knows, so it is only
    taken when it happens to be an ISBN.
    """
    for value in (book.source, book.identifier):
        # Digits, spaces and hyphens, and an X only as the last character: a DAISY
        # id such as `FR-ADV-1310715633` has letters in the middle and is not one.
        text = (value or "").strip().upper().removeprefix("URN:ISBN:")
        if not re.fullmatch(r"[0-9][0-9 -]*[0-9X]", text):
            continue
        digits = text.replace(" ", "").replace("-", "")
        if len(digits) == 13 and digits.isdigit():
            return digits
        if len(digits) == 10 and digits[:9].isdigit():
            return digits
    return None


def _openlibrary_isbn(book: Book) -> str | None:
    """The exact answer: the print book's own ISBN, when the pack carries one."""
    isbn = isbn_of(book)
    return f"https://covers.openlibrary.org/b/isbn/{isbn}-L.jpg" if isbn else None


def _openlibrary_title(book: Book) -> str | None:
    """No ISBN, or an ISBN Open Library has never heard of: ask by title and author."""
    if not book.title:
        return None
    params = {"title": book.title, "limit": "5", "fields": "cover_i"}
    if book.author:
        params["author"] = book.author
    body = _get(f"https://openlibrary.org/search.json?{urllib.parse.urlencode(params)}")
    try:
        docs = json.loads(body)["docs"]
    except (ValueError, KeyError, TypeError) as exc:
        raise CoverError(f"Open Library answered an unreadable search: {exc}") from exc
    for doc in docs:
        cover = doc.get("cover_i")
        if cover:
            return f"https://covers.openlibrary.org/b/id/{int(cover)}-L.jpg"
    return None


def _apple_books(book: Book) -> str | None:
    """Apple's book store, which lists titles Open Library has no record of at all.

    It cannot be asked by ISBN, only by words, so the title carries the weight
    and the author narrows it.
    """
    if not book.title:
        return None
    params = {
        "term": " ".join(value for value in (book.title, book.author) if value),
        "media": "ebook",
        "limit": "5",
        "output": "json",
    }
    body = _get(f"https://itunes.apple.com/search?{urllib.parse.urlencode(params)}")
    try:
        results = json.loads(body)["results"]
    except (ValueError, KeyError, TypeError) as exc:
        raise CoverError(f"Apple answered an unreadable search: {exc}") from exc
    best = _best_match(book.title, results)
    artwork = best.get("artworkUrl100") if best else None
    if not artwork:
        return None
    return artwork.replace("100x100bb", APPLE_ART_SIZE)


def _normal(text: str) -> str:
    """A title without the punctuation and case that differ between catalogues:
    the pack says `Wesh  la fée`, Apple says `Wesh, la fée`."""
    return re.sub(r"\W+", " ", text.casefold()).strip()


def _best_match(title: str, results: list) -> dict | None:
    """Apple ranks by what sells, not by what matches, so an exact title wins."""
    wanted = _normal(title)
    for item in results:
        if _normal(str(item.get("trackName") or "")) == wanted and wanted:
            return item
    return results[0] if results else None


# Best guess first: an ISBN is the book, a title is only a strong hint.
SOURCES: tuple[tuple[str, Callable[[Book], str | None]], ...] = (
    ("Open Library", _openlibrary_isbn),
    ("Apple Books", _apple_books),
    ("Open Library search", _openlibrary_title),
)


def fetch(url: str) -> bytes:
    """The picture at `url`, or None when the service has none for it."""
    body = _get(url)
    return body if len(body) > PLACEHOLDER_BYTES else None


def find(book: Book, dest: Path) -> Path | None:
    """Ask each source in turn and keep the first real cover, or leave nothing behind.

    The answer is written rather than kept in memory because the rest of the
    converter takes a cover as a file, like the one a user picks.
    """
    for name, locate in SOURCES:
        try:
            url = locate(book)
            if url is None:
                continue
            body = fetch(url)
        except CoverError as exc:
            log.warning("%s: %s", name, exc)
            continue
        if body is None:
            continue
        log.info("cover from %s: %s", name, url)
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(body)
        return dest
    log.info("no cover for %s", book.title or "this pack")
    return None


def _get(url: str) -> bytes:
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    try:
        with urllib.request.urlopen(request, timeout=TIMEOUT) as response:
            return response.read()
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        raise CoverError(f"could not reach {url}: {exc}") from exc
