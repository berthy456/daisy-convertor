"""The clip list `tools/make_web_voices.py` records, without recording any of it."""

from __future__ import annotations

import importlib.util
from pathlib import Path

_spec = importlib.util.spec_from_file_location(
    "make_web_voices", Path(__file__).resolve().parents[1] / "tools" / "make_web_voices.py"
)
make_web_voices = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(make_web_voices)

from daisy2telmi import settings, tts
from daisy2telmi.i18n import LANGUAGES

MAX = 50
ENGINES = (tts.ESPEAK, tts.PIPER, tts.COQUI, tts.CHATTERBOX)


def names(rows: list[tuple[str, str]]) -> list[str]:
    return [name for _, name in rows]


def phrase_of(rows: list[tuple[str, str]], name: str) -> str:
    return {clip_name: phrase for phrase, clip_name in rows}[name]


def test_the_total_is_recorded_as_the_words_the_language_says_after_its_number():
    rows = make_web_voices.rows(LANGUAGES["fr"], MAX, tts.CHATTERBOX)
    assert phrase_of(rows, "of-07.mp3") == " sur 7"
    assert phrase_of(rows, "of-20.mp3") == " sur 20"
    assert phrase_of(rows, "of-50.mp3") == " sur 50"


def test_a_language_that_names_the_part_after_the_total_records_it_with_the_total():
    assert phrase_of(make_web_voices.rows(LANGUAGES["zh"], MAX, tts.CHATTERBOX), "of-20.mp3") == "，共 20 章"
    assert phrase_of(make_web_voices.rows(LANGUAGES["tr"], MAX, tts.CHATTERBOX), "of-20.mp3") == ", 20 bölümden"


def test_the_word_the_chapter_clip_already_said_is_not_said_again_by_the_tail():
    """Chinese ends its chapter clip with 章 and starts its total with it too."""
    for code, lang in LANGUAGES.items():
        said = lang.chapter.partition("{n}")[2]
        if not said:
            continue
        for phrase, name in make_web_voices.rows(lang, MAX, tts.CHATTERBOX):
            if name.startswith("of-"):
                assert not phrase.startswith(said), f"{code}/{name}: {phrase!r}"


def test_the_tail_says_a_word_beside_the_number():
    """A bare digit is what Chatterbox turns into noise; the language's own word
    around the total is what it can say."""
    for code, lang in LANGUAGES.items():
        for phrase, name in make_web_voices.rows(lang, MAX, tts.CHATTERBOX):
            if name.startswith("of-"):
                assert any(c.isalpha() for c in phrase), f"{code}/{name}: {phrase!r}"


def test_every_number_in_the_recorded_range_has_its_tail():
    for code, lang in LANGUAGES.items():
        wanted = {f"of-{n:02d}.mp3" for n in range(1, MAX + 1)}
        assert wanted <= set(names(make_web_voices.rows(lang, MAX, tts.CHATTERBOX))), code


def test_the_page_finds_the_same_clips_whichever_engine_recorded_them():
    for code, lang in LANGUAGES.items():
        recorded = [
            set(names(make_web_voices.rows(lang, MAX, engine)))
            | set(names(make_web_voices.espeak_rows(engine, lang, MAX)))
            for engine in ENGINES
        ]
        assert all(recorded[0] == every for every in recorded[1:]), code


def test_an_engine_that_reads_a_number_says_its_own_numbers():
    """`piper-phonemize` answers "23" with twenty-three, so the card's number is
    in the engine's own voice instead of a robotic one."""
    for code, lang in LANGUAGES.items():
        for engine in (tts.ESPEAK, tts.PIPER, tts.COQUI):
            names_of = set(names(make_web_voices.rows(lang, MAX, engine)))
            assert {"n-01.mp3", "n-50.mp3"} <= names_of, f"{code}/{engine}"
            assert make_web_voices.espeak_rows(engine, lang, MAX) == [], f"{code}/{engine}"


def test_chatterbox_leaves_the_bare_numbers_to_espeak():
    for code, lang in LANGUAGES.items():
        assert not ({"n-01.mp3", "n-50.mp3"} & set(names(make_web_voices.rows(lang, MAX, tts.CHATTERBOX)))), code
        assert make_web_voices.espeak_rows(tts.CHATTERBOX, lang, MAX), code


def test_the_pieces_left_to_espeak_are_a_bare_number_or_a_single_word():
    for code, lang in LANGUAGES.items():
        for phrase, name in make_web_voices.espeak_rows(tts.CHATTERBOX, lang, MAX):
            short = phrase.strip().isdigit() or len(phrase.split()) == 1
            assert short, f"{code}/{name}: {phrase!r}"
            assert (name == "of-pre.mp3") == (not phrase.strip().isdigit()), code


def test_the_two_sets_do_not_record_the_same_clip_twice():
    for code, lang in LANGUAGES.items():
        for engine in ENGINES:
            spoken = set(names(make_web_voices.rows(lang, MAX, engine)))
            light = set(names(make_web_voices.espeak_rows(engine, lang, MAX)))
            assert spoken & light == set(), f"{code}/{engine}"


def test_only_an_engine_with_a_voice_for_the_language_is_used(tmp_path, monkeypatch):
    """Piper speaks a language once its voice file is in the app's voice folder,
    and the Coqui model that covers Arabic only speaks with a recording to clone."""
    monkeypatch.setattr(settings, "speech_voices", lambda: tmp_path)
    assert not make_web_voices.speaks(tts.PIPER, LANGUAGES["fr"], None)
    (tmp_path / "fr_FR-upmc-medium.onnx").write_bytes(b"")
    assert make_web_voices.speaks(tts.PIPER, LANGUAGES["fr"], None)
    assert not make_web_voices.speaks(tts.COQUI, LANGUAGES["ar"], None)
    assert make_web_voices.speaks(tts.COQUI, LANGUAGES["ar"], "someone.onnx")


SECONDS = {"ramble": 6.0, "short": 0.2, "clean": 1.1, "espeak": 1.1}


def fake_engine(monkeypatch, rambling: str, always: bool = False, short: str | None = None) -> list[list[str]]:
    """A stand-in for the engine's process: a take is long when it says the
    rambling phrase and clipped when it says the `short` one, and any take named
    `retake…` came out clean unless it `always` rambles."""
    passes: list[list[str]] = []

    def run(engine, phrases, lang, raws, folder, python, device, voice):
        passes.append(list(phrases))
        for phrase, raw in zip(phrases, raws):
            again = raw.name.startswith("retake")
            if phrase == rambling and (always or not again):
                raw.write_text("ramble")
            elif phrase == short and not again:
                raw.write_text("short")
            else:
                raw.write_text("clean")

    monkeypatch.setattr(make_web_voices, "speak_batch", run)
    monkeypatch.setattr(make_web_voices, "speak_espeak", lambda phrases, voice, raws: [
        raw.write_text("espeak") for raw in raws
    ])
    monkeypatch.setattr(make_web_voices, "duration", lambda path: SECONDS[path.read_text()])
    return passes


def speak(tmp_path, phrases: list[str]) -> tuple[int, int]:
    return make_web_voices.speak_neural(
        tts.CHATTERBOX,
        phrases,
        LANGUAGES["fr"],
        [tmp_path / f"c{k}.wav" for k in range(len(phrases))],
        tmp_path,
        "python",
        "auto",
        None,
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


def test_a_take_far_shorter_than_espeak_is_recorded_again(tmp_path, monkeypatch):
    passes = fake_engine(monkeypatch, "nothing rambling here", short="Chapitre 2")
    assert speak(tmp_path, ["Chapitre 1", "Chapitre 2"]) == (1, 0)
    assert passes[1] == ["Chapitre 2"]
    assert (tmp_path / "c1.wav").read_text() == "clean", "the take nearer the espeak length is the one kept"


def test_a_phrase_that_rambles_through_every_take_is_recorded_by_espeak(tmp_path, monkeypatch):
    passes = fake_engine(monkeypatch, "Chapitre 2", always=True)
    assert speak(tmp_path, ["Chapitre 1", "Chapitre 2"]) == (2, 1)
    assert (tmp_path / "c1.wav").read_text() == "espeak"
    assert (tmp_path / "c0.wav").read_text() == "clean", "the phrase that spoke is left alone"
