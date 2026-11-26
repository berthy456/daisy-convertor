"""The language tables have to stay complete: a missing key would show a raw key
or an English label in a window the user asked to see in Turkish.

The other half of this file reads a language off a pack, which is what lets a book's
cards use the book's own words without anyone being asked for them."""

from __future__ import annotations

import pytest

from daisy2telmi.i18n import LANGUAGES, UI, code_of, guess, language, story_language, text

EXPECTED = {"en", "fr", "zh", "ar", "tr", "de", "it", "es", "pt", "nl"}

# A pack names its language under "ISO 639", and which ISO 639 it means, and whether it
# names the language in English or in its own words, is up to whoever made the pack.
DECLARED = [
    ("FR", "fr"),
    ("fr", "fr"),
    ("fre", "fr"),
    ("fra", "fr"),
    ("fr-FR", "fr"),
    ("Français", "fr"),
    ("eng", "en"),
    ("en-GB", "en"),
    ("English", "en"),
    ("deu", "de"),
    ("ger", "de"),
    ("Deutsch", "de"),
    ("dut", "nl"),
    ("nld", "nl"),
    ("spa", "es"),
    ("por", "pt"),
    ("ita", "it"),
    ("ara", "ar"),
    ("tur", "tr"),
    ("chi", "zh"),
    ("zho", "zh"),
]

# One page per language the app has words for, each a few sentences of ordinary story.
PAGES = {
    "ar": "نظرت الثعوبة إلى البنت ونظرت إليه، ومشيا في الغابة.",
    "de": "Der Fuchs sah das Mädchen an und es sah zurück, und sie gingen durch den Wald.",
    "en": "The fox looked at the girl and she looked back at him, and they walked on through the wood.",
    "es": "El zorro miró a la niña y ella lo miró también, y caminaron por el bosque.",
    "fr": "La fée lui répondit avec douceur, et le bateau reprit sa route vers l'île.",
    "it": "La volpe guardò la bambina e lei guardò lui, e camminarono nel bosco.",
    "nl": "De vos keek naar het meisje en zij keek terug, en ze liepen door het bos.",
    "pt": "A raposa olhou para a menina e ela olhou de volta, e caminharam pela floresta.",
    "tr": "Tilki kıza baktı ve kız ona baktı ve ormanda babasının evine kadar yürüdüler.",
    "zh": "狐狸看着那个女孩，她也看着他，他们走过了树林。",
}


def test_the_ten_languages_are_present() -> None:
    assert EXPECTED == set(LANGUAGES)


@pytest.mark.parametrize("code", sorted(EXPECTED))
def test_every_language_can_speak_a_chapter_number(code: str) -> None:
    lang = LANGUAGES[code]
    assert "{n}" in lang.chapter
    assert lang.chapter.format(n=7).strip()
    assert lang.next_prompt.strip()


@pytest.mark.parametrize("code", sorted(EXPECTED))
def test_every_language_can_name_a_part(code: str) -> None:
    """A card that reads the part's title needs the words around the number too."""
    lang = LANGUAGES[code]
    assert "{n}" in lang.number_of and "{total}" in lang.number_of
    assert "{n}" in lang.number_title and "{title}" in lang.number_title
    assert "{n}" in lang.title_number and "{title}" in lang.title_number
    assert lang.number_of.format(n=3, total=9).strip()
    assert lang.number_title.format(n=3, title="X").strip()


@pytest.mark.parametrize("code", sorted(EXPECTED))
def test_every_window_language_is_translated(code: str) -> None:
    missing = set(UI["en"]) - set(UI[code])
    assert not missing, f"{code} is missing {sorted(missing)}"


def test_an_unknown_language_falls_back_to_english() -> None:
    assert language("xx").code == "en"
    assert language(None).code == "en"
    assert text("xx", "save") == UI["en"]["save"]


def test_speech_tags_are_set_for_every_language() -> None:
    for code, lang in LANGUAGES.items():
        assert lang.espeak, f"{code} has no espeak tag"
        assert lang.locale, f"{code} has no locale"


@pytest.mark.parametrize("tag, code", DECLARED)
def test_a_pack_names_its_language_in_whichever_way_it_does(tag: str, code: str) -> None:
    assert code_of(tag) == code


@pytest.mark.parametrize("tag", ["ja", "jpn", "pol", "", None])
def test_a_language_the_app_has_no_words_for_is_not_invented(tag: str | None) -> None:
    assert code_of(tag) is None


@pytest.mark.parametrize("code, page", sorted(PAGES.items()))
def test_a_page_reads_as_the_language_it_is_written_in(code: str, page: str) -> None:
    assert guess(page) == code


def test_japanese_is_not_read_as_chinese() -> None:
    """Kana sit among the same Han characters Chinese is written in, and neither is here."""
    assert guess("狐は少女を見て、少女も狐を見た。") is None


def test_a_page_with_no_words_in_it_gives_nothing_to_read() -> None:
    assert guess("") is None
    assert guess("1234 5678") is None


def test_a_page_too_short_to_tell_is_not_guessed() -> None:
    """A title alone is a handful of words no language wins, and guessing wrong is worse
    than asking."""
    assert guess("Chapter One. The house on the hill.") is None


def test_a_capitalised_turkish_letter_still_counts() -> None:
    """Lowercasing Turkish İ leaves a second dot sitting on the i that no Turkish word
    is spelled with."""
    assert guess("İle başladı ve bir şey kaldı, çünkü o geldi.") == "tr"


def test_the_pack_s_own_text_outranks_the_language_it_declares() -> None:
    """`dc:language` is a producer's note: a French book has been found declaring English."""
    assert story_language("EN", PAGES["fr"], "de") == ("fr", "the pack's own text")


def test_the_language_a_pack_declares_is_used_when_its_text_gives_nothing() -> None:
    assert story_language("FR", "", "en") == ("fr", "the pack's metadata")


def test_the_pack_s_own_text_is_read_when_it_declares_nothing() -> None:
    assert story_language(None, PAGES["de"], "en") == ("de", "the pack's own text")


def test_a_declared_language_the_app_has_no_words_for_says_so() -> None:
    assert story_language("ja", "", "fr") == (
        "fr",
        "your machine: the pack says ja, which has no words here",
    )


def test_a_pack_that_is_audio_only_falls_back_to_the_machine() -> None:
    assert story_language(None, "", "de") == ("de", "your machine")
