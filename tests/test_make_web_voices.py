"""The clip list `tools/make_web_voices.py` records, without recording any of it."""

from __future__ import annotations

import importlib.util
from pathlib import Path

_spec = importlib.util.spec_from_file_location(
    "make_web_voices", Path(__file__).resolve().parents[1] / "tools" / "make_web_voices.py"
)
make_web_voices = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(make_web_voices)

from daisy2telmi.i18n import LANGUAGES

MAX = 50


def names(rows: list[tuple[str, str]]) -> list[str]:
    return [name for _, name in rows]


def phrase_of(rows: list[tuple[str, str]], name: str) -> str:
    return {clip_name: phrase for phrase, clip_name in rows}[name]


def test_the_total_is_recorded_as_the_words_the_language_says_after_its_number():
    rows = make_web_voices.rows(LANGUAGES["fr"], MAX)
    assert phrase_of(rows, "of-07.mp3") == " sur 7"
    assert phrase_of(rows, "of-20.mp3") == " sur 20"
    assert phrase_of(rows, "of-50.mp3") == " sur 50"


def test_a_language_that_names_the_part_after_the_total_records_it_with_the_total():
    assert phrase_of(make_web_voices.rows(LANGUAGES["zh"], MAX), "of-20.mp3") == "，共 20 章"
    assert phrase_of(make_web_voices.rows(LANGUAGES["tr"], MAX), "of-20.mp3") == ", 20 bölümden"


def test_the_word_the_chapter_clip_already_said_is_not_said_again_by_the_tail():
    """Chinese ends its chapter clip with 章 and starts its total with it too."""
    for code, lang in LANGUAGES.items():
        said = lang.chapter.partition("{n}")[2]
        if not said:
            continue
        for phrase, name in make_web_voices.rows(lang, MAX):
            if name.startswith("of-"):
                assert not phrase.startswith(said), f"{code}/{name}: {phrase!r}"


def test_the_tail_says_a_word_beside_the_number():
    """A bare digit is what Chatterbox turns into noise; the language's own word
    around the total is what it can say."""
    for code, lang in LANGUAGES.items():
        for phrase, name in make_web_voices.rows(lang, MAX):
            if name.startswith("of-"):
                assert any(c.isalpha() for c in phrase), f"{code}/{name}: {phrase!r}"


def test_every_number_in_the_recorded_range_has_its_tail():
    for code, lang in LANGUAGES.items():
        wanted = {f"of-{n:02d}.mp3" for n in range(1, MAX + 1)}
        assert wanted <= set(names(make_web_voices.rows(lang, MAX))), code


def test_the_pieces_left_to_espeak_are_a_bare_number_or_a_single_word():
    for code, lang in LANGUAGES.items():
        for phrase, name in make_web_voices.espeak_rows(lang, MAX):
            short = phrase.strip().isdigit() or len(phrase.split()) == 1
            assert short, f"{code}/{name}: {phrase!r}"
            assert (name == "of-pre.mp3") == (not phrase.strip().isdigit()), code


def test_the_two_sets_do_not_record_the_same_clip_twice():
    for code, lang in LANGUAGES.items():
        engine = set(names(make_web_voices.rows(lang, MAX)))
        light = set(names(make_web_voices.espeak_rows(lang, MAX)))
        assert engine & light == set(), code


def fake_engine(monkeypatch, rambling: str, always: bool = False) -> list[list[str]]:
    """A stand-in for the Chatterbox process: a take is long when it says the
    rambling phrase, and any take named `retake…` came out clean unless it
    `always` rambles."""
    passes: list[list[str]] = []

    def run(phrases, lang, raws, folder, python, device):
        passes.append(list(phrases))
        for phrase, raw in zip(phrases, raws):
            again = raw.name.startswith("retake")
            raw.write_text("ramble" if phrase == rambling and (always or not again) else "clean")

    monkeypatch.setattr(make_web_voices, "chatterbox_run", run)
    monkeypatch.setattr(make_web_voices, "speak_espeak", lambda phrases, voice, raws: [
        raw.write_text("espeak") for raw in raws
    ])
    monkeypatch.setattr(make_web_voices, "duration", lambda path: 6.0 if path.read_text() == "ramble" else 1.1)
    return passes


def speak(tmp_path, phrases: list[str]) -> tuple[int, int]:
    return make_web_voices.speak_chatterbox(
        phrases,
        LANGUAGES["fr"],
        [tmp_path / f"c{k}.wav" for k in range(len(phrases))],
        tmp_path,
        "python",
        "auto",
        [1.0] * len(phrases),
    )


def test_a_take_three_times_espeak_on_the_same_words_is_recorded_again(tmp_path, monkeypatch):
    passes = fake_engine(monkeypatch, "Chapitre 2")
    assert speak(tmp_path, ["Chapitre 1", "Chapitre 2", "Chapitre 3"]) == (1, 0)
    assert passes[0] == ["Chapitre 1", "Chapitre 2", "Chapitre 3"]
    assert passes[1] == ["Chapitre 2"]
    assert (tmp_path / "c1.wav").read_text() == "clean", "the shorter take is the one kept"


def test_a_take_that_stays_near_the_espeak_length_is_left_alone(tmp_path, monkeypatch):
    passes = fake_engine(monkeypatch, "nothing rambling here")
    assert speak(tmp_path, ["Chapitre 1", "Chapitre 2"]) == (0, 0)
    assert len(passes) == 1


def test_a_phrase_that_rambles_through_every_take_is_recorded_by_espeak(tmp_path, monkeypatch):
    passes = fake_engine(monkeypatch, "Chapitre 2", always=True)
    assert speak(tmp_path, ["Chapitre 1", "Chapitre 2"]) == (2, 1)
    assert (tmp_path / "c1.wav").read_text() == "espeak"
    assert (tmp_path / "c0.wav").read_text() == "clean", "the phrase that spoke is left alone"
