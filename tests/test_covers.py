"""The cover a pack does not carry: which number to ask with, and what answers."""

from __future__ import annotations

import json
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import pytest

from daisy2telmi import covers
from daisy2telmi.extractor import AudioSegment, Book, Chapter

# Values from the Rachel pack: dc:identifier is the DAISY edition's own number,
# dc:source is the print book's ISBN.
DAISY_ID = "FR-ADV-1310715633"
PRINT_ISBN = "979-10-363-6487-7"
# What Open Library answers for a book it has no picture of: a 1x1 GIF.
NOTHING = b"GIF89a" + b"\0" * 37
# A cover, as far as these tests care: anything over the placeholder's size.
COVER = b"\xff\xd8\xff" + b"x" * 4000
APPLE_ART = "https://is1-ssl.mzstatic.com/image/thumb/x/9791036383786-001-x.jpeg/100x100bb.jpg"


def book(**kwargs) -> Book:
    defaults = {
        "title": "Wesh la fée",
        "chapters": (Chapter("Un", (AudioSegment(Path("a.mp3")),)),),
        "identifier": DAISY_ID,
        "source": PRINT_ISBN,
    }
    return Book(**{**defaults, **kwargs})


class Answers:
    """Stand in for the network: record what was asked, reply with what a test wants.

    A service not mentioned by the test answers the way it does for a book it
    does not have, so a test only spells out the part it is about.
    """

    defaults = {
        "-L.jpg": NOTHING,
        "itunes.apple.com": json.dumps({"resultCount": 0, "results": []}).encode(),
        "search.json": json.dumps({"numFound": 0, "docs": []}).encode(),
    }

    def __init__(self, replies: dict[str, bytes] | None = None, fail: bool = False) -> None:
        self.replies = replies or {}
        self.fail = fail
        self.asked: list[str] = []

    def __call__(self, url: str) -> bytes:
        self.asked.append(url)
        if self.fail:
            raise covers.CoverError(f"could not reach {url}")
        # A test's own reply wins over the standing one, however specific either is.
        for part, body in list(self.replies.items()) + list(self.defaults.items()):
            if part in url:
                return body
        raise AssertionError(f"unexpected request: {url}")

    def query(self, part: str) -> dict:
        """The query of the one request that mentions `part`."""
        urls = [url for url in self.asked if part in url]
        assert len(urls) == 1, f"{part} was asked {len(urls)} times"
        return parse_qs(urlparse(urls[0]).query)


@pytest.fixture()
def offline(monkeypatch) -> Answers:
    answers = Answers()
    monkeypatch.setattr(covers, "_get", answers)
    return answers


def test_the_print_books_isbn_is_the_one_in_dc_source() -> None:
    assert covers.isbn_of(book()) == "9791036364877"


def test_a_daisy_identifier_is_not_taken_for_an_isbn() -> None:
    assert covers.isbn_of(book(source=None)) is None


def test_an_isbn_10_keeps_its_check_letter() -> None:
    assert covers.isbn_of(book(source="0-140-44918-0", identifier=None)) == "0140449180"
    assert covers.isbn_of(book(source="081269018x", identifier=None)) == "081269018X"


def test_an_isbn_points_straight_at_the_cover(offline: Answers, tmp_path: Path) -> None:
    """A cover found by ISBN stops the asking: no search follows it."""
    offline.replies = {"b/isbn/9791036364877-L.jpg": COVER}
    dest = tmp_path / "cover.jpg"
    assert covers.find(book(), dest) == dest
    assert offline.asked == ["https://covers.openlibrary.org/b/isbn/9791036364877-L.jpg"]


def test_a_book_no_service_has_leaves_nothing_behind(offline: Answers, tmp_path: Path) -> None:
    dest = tmp_path / "cover.jpg"
    assert covers.find(book(), dest) is None
    assert not dest.exists()
    # Every service is asked before the answer says there is nothing.
    asked = " ".join(offline.asked)
    for part in ("covers.openlibrary.org/b/isbn", "itunes.apple.com", "openlibrary.org/search.json"):
        assert part in asked


def test_a_book_openlibrary_has_never_heard_of_is_asked_of_apple(offline: Answers, tmp_path: Path) -> None:
    """The Rachel pack: Open Library holds no record of it at all, Apple has its cover."""
    offline.replies = {
        "itunes.apple.com": json.dumps(
            {"resultCount": 1, "results": [{"trackName": "Wesh, la fée", "artworkUrl100": APPLE_ART}]}
        ).encode(),
        "600x600bb": COVER,
    }
    dest = tmp_path / "cover.jpg"
    assert covers.find(book(), dest) == dest
    assert dest.read_bytes().startswith(b"\xff\xd8\xff")
    query = offline.query("itunes.apple.com")
    assert query["term"] == ["Wesh la fée"]
    assert query["media"] == ["ebook"]


def test_apple_is_asked_at_the_largest_cover_it_gives(offline: Answers, tmp_path: Path) -> None:
    offline.replies = {
        "itunes.apple.com": json.dumps(
            {"resultCount": 1, "results": [{"trackName": "Wesh la fée", "artworkUrl100": APPLE_ART}]}
        ).encode(),
        "600x600bb": COVER,
    }
    covers.find(book(), tmp_path / "cover.jpg")
    assert "600x600bb" in offline.asked[-1]


def test_an_exact_title_beats_apples_first_hit(offline: Answers, tmp_path: Path) -> None:
    """Apple ranks by what sells; the pack's own title is the one to keep."""
    offline.replies = {
        "itunes.apple.com": json.dumps(
            {
                "resultCount": 2,
                "results": [
                    {"trackName": "Wesh la fée : le coffret", "artworkUrl100": "https://x/wrong/100x100bb.jpg"},
                    {"trackName": "Wesh,  la fée !", "artworkUrl100": APPLE_ART},
                ],
            }
        ).encode(),
        "600x600bb": COVER,
    }
    covers.find(book(), tmp_path / "cover.jpg")
    assert "9791036383786" in offline.asked[-1], "the exact title's artwork, not the coffret's"


def test_the_author_narrows_the_search(offline: Answers, tmp_path: Path) -> None:
    offline.replies = {"itunes.apple.com": json.dumps({"results": []}).encode()}
    covers.find(book(source=None, author="Rachel Corenblit"), tmp_path / "cover.jpg")
    assert offline.query("itunes.apple.com")["term"] == ["Wesh la fée Rachel Corenblit"]


def test_without_an_isbn_the_title_and_author_are_asked(offline: Answers, tmp_path: Path) -> None:
    offline.replies = {
        "itunes.apple.com": json.dumps({"results": []}).encode(),
        "search.json": json.dumps({"docs": [{"cover_i": 12123878}]}).encode(),
        "b/id/12123878-L.jpg": COVER,
    }
    dest = tmp_path / "cover.jpg"
    assert covers.find(book(source=None, author="Rachel Corenblit"), dest) == dest
    query = offline.query("search.json")
    assert query["title"] == ["Wesh la fée"]
    assert query["author"] == ["Rachel Corenblit"]
    assert offline.asked[-1] == "https://covers.openlibrary.org/b/id/12123878-L.jpg"


def test_a_book_with_neither_isbn_nor_title_asks_nothing(offline: Answers, tmp_path: Path) -> None:
    assert covers.find(book(source=None, identifier=None, title=""), tmp_path / "cover.jpg") is None
    assert offline.asked == []


def test_the_one_pixel_answer_is_no_cover(offline: Answers) -> None:
    # Open Library's "we have nothing" reply: a 1x1 GIF.
    offline.replies = {"-L.jpg": NOTHING}
    assert covers.fetch("https://covers.openlibrary.org/b/isbn/1-L.jpg") is None


def test_one_service_being_down_does_not_stop_the_others(offline: Answers, tmp_path: Path) -> None:
    """Apple answers for some books and refuses for others; the next source still runs."""
    offline.replies = {
        "itunes.apple.com": b"not json",
        "search.json": json.dumps({"docs": [{"cover_i": 7}]}).encode(),
        "b/id/7-L.jpg": COVER,
    }
    dest = tmp_path / "cover.jpg"
    assert covers.find(book(source=None), dest) == dest


def test_an_unreachable_service_leaves_nothing_behind(offline: Answers, tmp_path: Path) -> None:
    offline.fail = True
    dest = tmp_path / "cover.jpg"
    assert covers.find(book(), dest) is None
    assert not dest.exists()
