"""Regenerate `web/voices/`: the chapter-card phrases in every language.

A browser tab can play speech but cannot capture it into a file, so the web
converter ships these clips instead of synthesising them.

    .venv/bin/python tools/make_web_voices.py
    .venv/bin/python tools/make_web_voices.py --engine piper --python ~/.venvs/piper/bin/python
    .venv/bin/python tools/make_web_voices.py --engine chatterbox --python /usr/bin/python3

espeak-ng is the default because it is GPLv3 like the project and needs no
download. Piper, Coqui and Chatterbox give a real voice instead of a robotic
one, but they run in a Python of your own (`pip install piper-tts`,
`coqui-tts` or `chatterbox-tts`) and Piper downloads a voice per language into
the app's voice folder. All of them are levelled to the same loudness, so
switching engine does not change how loud the cards sound next to the story.

Chatterbox is the one engine that cannot say a bare number: 0.1.7 loses its
place on a one-word phrase and answers "of" with 23 seconds of noise, so its
numbers and the word a language puts before them stay espeak-ng. Piper and
Coqui read a number as the language reads it, so their numbers are theirs. The
total is recorded as the whole phrase after the number — " sur 20", "，共 20 章"
— which Chatterbox does say.

Chatterbox rambles now and then and a Piper take sometimes cuts the word short,
so each phrase is measured against the time espeak-ng takes on it and anything
three times off that length is recorded again, the take nearer the espeak
length winning. A phrase that is off beat through every take is left to
espeak-ng rather than shipped as a stutter or a half-word.
"""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import tempfile
from pathlib import Path

from daisy2telmi import settings, tts
from daisy2telmi.i18n import LANGUAGES

ROOT = Path(__file__).resolve().parents[1]

# The engines that speak from a model, and so cost one process per language.
NEURAL = (tts.PIPER, tts.COQUI, tts.CHATTERBOX)

# A neural take this far off the espeak length of the same words — that much
# longer, or that much shorter — is the model repeating itself or cutting the
# word short rather than speaking.
RAMBLE = 3.0
RETRIES = 2


def run(argv: list[str]) -> None:
    subprocess.run(argv, check=True, capture_output=True, text=True)


def around(pattern: str) -> tuple[str, str, str]:
    """What a "{n} of {total}" pattern puts before the number, between the two
    numbers, and after the total."""
    head, _, tail = pattern.partition("{n}")
    middle, _, last = tail.partition("{total}")
    return head, middle, last


def total_phrase(lang, number: int) -> str:
    """What a card says after its chapter number to name `number` as the total.

    The total is one phrase rather than the word and the number apart, because
    that is the shortest thing Chatterbox will still say cleanly. A language
    that puts its word for chapter after the number — Chinese's "第 4 章" —
    said that word in the chapter clip already, so the overlap is dropped
    instead of stuttered.
    """
    _, middle, last = around(lang.number_of)
    said = lang.chapter.partition("{n}")[2]
    middle = middle[len(said):] if middle.startswith(said) else middle
    return f"{middle}{number}{last}"


def number_rows(lang, max_number: int) -> list[tuple[str, str]]:
    """The bare number, and the word a language puts before it, for a card that
    does not say the word chapter."""
    head = around(lang.number_of)[0]
    bare = [(str(n), f"n-{n:02d}.mp3") for n in range(1, max_number + 1)]
    words = [(head, "of-pre.mp3")] if any(c.isalnum() for c in head) else []
    return bare + words


def rows(lang, max_number: int, engine: str) -> list[tuple[str, str]]:
    """The phrases the chosen engine records, with the file name each one becomes."""
    every = [(lang.chapter.format(n=n), f"{n:02d}.mp3") for n in range(1, max_number + 1)]
    totals = [(total_phrase(lang, n), f"of-{n:02d}.mp3") for n in range(1, max_number + 1)]
    numbers = [] if engine == tts.CHATTERBOX else number_rows(lang, max_number)
    return every + totals + [(lang.next_prompt, "next.mp3")] + numbers


def espeak_rows(engine: str, lang, max_number: int) -> list[tuple[str, str]]:
    """What espeak-ng records instead of the engine: Chatterbox is the one engine
    that turns a bare number or a single word into noise.

    Piper and Coqui read a number as the language reads it — `piper-phonemize`
    answers "23" with twenty-three, three-and-twenty, ثلاثة وعشرون — so their own
    voice carries the card number too.
    """
    return number_rows(lang, max_number) if engine == tts.CHATTERBOX else []


def encode(raw: Path, dest: Path, bitrate: str) -> None:
    """One clip to the web page's size: mono, small, and levelled like a card."""
    run(
        [
            "ffmpeg",
            "-hide_banner",
            "-loglevel",
            "error",
            "-y",
            "-i",
            str(raw),
            "-ar",
            "44100",
            "-ac",
            "1",
            "-af",
            tts.card_loudness(None),
            "-c:a",
            "libmp3lame",
            "-b:a",
            bitrate,
            str(dest),
        ]
    )


def duration(clip: Path) -> float:
    """Seconds of audio in a finished clip. A take that rambles shows up here."""
    result = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(clip)],
        capture_output=True,
        text=True,
        check=True,
    )
    return float(result.stdout.strip())


def speak_espeak(phrases: list[str], voice: str, raws: list[Path]) -> None:
    for phrase, raw in zip(phrases, raws):
        run(["espeak-ng", "-v", voice, "-s", str(tts.SPEAKING_RATE), "-w", str(raw), phrase])


def espeak_seconds(phrases: list[str], voice: str, folder: Path) -> list[float]:
    """What each phrase costs to say, measured with espeak-ng at the app's own
    reading rate: the yardstick a Chatterbox take is held against."""
    out: list[float] = []
    for k, phrase in enumerate(phrases):
        raw = folder / f"yardstick{k}.wav"
        speak_espeak([phrase], voice, [raw])
        out.append(duration(raw))
    return out


def speaks(engine: str, lang, voice: str | None) -> bool:
    """Whether this engine has something to speak this language with.

    Arabic is the gap: only Coqui's multilingual model speaks it, and that one
    reads with a recording of the voice to clone, which this tool is not given.
    """
    if engine == tts.PIPER:
        return bool(tts.piper_voice(lang, voice))
    if engine == tts.COQUI:
        return bool(voice) or any(m != tts.COQUI_MULTILINGUAL for m in tts.COQUI_MODELS.get(lang.code, []))
    return True


def speak_batch(
    engine: str, phrases: list[str], lang, raws: list[Path], folder: Path, python, device: str, voice: str | None
) -> None:
    """One process for the whole language: loading the model costs more than
    speaking a chapter number, and it would otherwise happen once per clip."""
    manifest: dict = {"phrases": phrases, "outs": [str(raw) for raw in raws], "lang": lang.code, "device": device}
    if engine == tts.PIPER:
        manifest["model"] = str(tts.piper_voice(lang, voice))
    elif engine == tts.COQUI:
        manifest["model"] = voice or tts.COQUI_MODELS[lang.code][0]
    path = folder / "manifest.json"
    path.write_text(json.dumps(manifest), encoding="utf-8")
    result = subprocess.run(
        [python, "-I", str(tts._speak_script(engine)), str(path)],
        capture_output=True,
        text=True,
    )
    if result.returncode:
        raise SystemExit(f"{engine} failed: {(result.stderr or result.stdout).strip()}")
    print(f"  {result.stdout.strip().splitlines()[0]}")


def off_beat(raws: list[Path], yardstick: list[float]) -> list[int]:
    """Which takes came out far off the espeak length of their words."""
    return [k for k, raw in enumerate(raws) if not yardstick[k] / RAMBLE <= duration(raw) <= RAMBLE * yardstick[k]]


def speak_neural(
    engine: str,
    phrases: list[str],
    lang,
    raws: list[Path],
    folder: Path,
    python,
    device: str,
    voice: str | None,
    yardstick: list[float],
) -> tuple[int, int]:
    """Record every phrase, then take again the ones that came out off beat.

    A take three times the length of espeak-ng reading the same words is the
    model repeating itself, not speaking: the published cards carried a 10 s
    "Bölüm 30" before this existed. A take three times shorter has cut the word
    short — these models draw their own pacing, so the same "cinq" comes out at
    0.19 s on one run and 0.37 s on the next. The take nearer the espeak length
    is kept, and a phrase that is off beat through every take is given to
    espeak-ng rather than shipped as a stutter or a half-word.

    Returns how many takes were re-recorded and how many ended up espeak-ng.
    """
    speak_batch(engine, phrases, lang, raws, folder, python, device, voice)
    retaken = 0
    for round_ in range(RETRIES):
        off = off_beat(raws, yardstick)
        if not off:
            break
        again = [folder / f"retake{round_}-{k}.wav" for k in off]
        speak_batch(engine, [phrases[k] for k in off], lang, again, folder, python, device, voice)
        for k, new in zip(off, again):
            retaken += 1
            if abs(duration(new) - yardstick[k]) < abs(duration(raws[k]) - yardstick[k]):
                shutil.copyfile(new, raws[k])
    stubborn = off_beat(raws, yardstick)
    speak_espeak([phrases[k] for k in stubborn], lang.espeak, [raws[k] for k in stubborn])
    return retaken, len(stubborn)


def get_piper_voices(exe: str, voice: str | None) -> None:
    """Download the Piper voice each language will be recorded with.

    The weights are the one part of an engine this repo never ships, so a Piper
    recording is made with the voices downloaded into the app's own folder — the
    same folder the app looks in, which is why they are fetched there.
    """
    folder = settings.speech_voices()
    folder.mkdir(parents=True, exist_ok=True)
    for lang in LANGUAGES.values():
        if tts.piper_voice(lang, voice):
            continue
        name = voice or tts.default_voice(lang.code)
        if not name:
            raise SystemExit(f"Piper has no voice listed for {lang.name}: pass --voice")
        tts._download_voice(exe, name, folder)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, default=ROOT / "web" / "voices")
    parser.add_argument("--max", type=int, default=50, help="highest chapter number to record")
    parser.add_argument("--bitrate", default="32")
    parser.add_argument("--engine", choices=(tts.ESPEAK, tts.PIPER, tts.COQUI, tts.CHATTERBOX), default=tts.ESPEAK)
    parser.add_argument("--python", default=None, help="interpreter the package engines run in")
    parser.add_argument("--voice", default=None, help="the Piper voice or the Coqui model to record with")
    parser.add_argument("--device", choices=tts.DEVICES, default=tts.AUTO)
    args = parser.parse_args()

    python = tts._python(args.python) if args.engine in NEURAL else None
    if args.engine in NEURAL and not python:
        raise SystemExit(f"{tts.LABELS[args.engine]} runs in your own Python: pass --python")
    if args.engine == tts.PIPER:
        get_piper_voices(python, args.voice)

    total = 0
    written = 0
    for code, lang in LANGUAGES.items():
        directory = args.out / code
        directory.mkdir(parents=True, exist_ok=True)
        engine = args.engine if speaks(args.engine, lang, args.voice) else tts.ESPEAK
        wanted = rows(lang, args.max, engine)
        light = espeak_rows(engine, lang, args.max)
        with tempfile.TemporaryDirectory(prefix="daisy2telmi-voices-") as tmp:
            folder = Path(tmp)
            raws = [folder / f"speech{k}.wav" for k in range(len(wanted) + len(light))]
            phrases = [phrase for phrase, _ in wanted]
            if engine in NEURAL:
                yardstick = espeak_seconds(phrases, lang.espeak, folder)
                retaken, robotic = speak_neural(
                    engine, phrases, lang, raws[: len(wanted)], folder, python, args.device, args.voice, yardstick
                )
            else:
                speak_espeak(phrases, lang.espeak, raws[: len(wanted)])
                retaken = robotic = 0
            speak_espeak([phrase for phrase, _ in light], lang.espeak, raws[len(wanted):])
            longest = (0.0, "")
            for (phrase, name), raw in zip(wanted + light, raws):
                dest = directory / name
                encode(raw, dest, args.bitrate)
                seconds = duration(dest)
                total += dest.stat().st_size
                written += 1
                if seconds > longest[0]:
                    longest = (seconds, name)
        mixed = f", {len(light) + robotic} of them espeak-ng" if engine in NEURAL else ""
        again = f", {retaken} re-taken" if retaken else ""
        print(
            f"{code}: {len(wanted) + len(light)} clip(s) with {tts.LABELS[engine]}{mixed}{again} — "
            f"longest {longest[1]} at {longest[0]:.2f} s"
        )

    print(f"{written} file(s), {total / 1e6:.2f} MB in {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
