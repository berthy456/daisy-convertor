"""Regenerate `web/voices/`: the chapter-card phrases in every language.

A browser tab can play speech but cannot capture it into a file, so the web
converter ships these clips instead of synthesising them.

    .venv/bin/python tools/make_web_voices.py
    .venv/bin/python tools/make_web_voices.py --engine chatterbox --python /usr/bin/python3

espeak-ng is the default because it is GPLv3 like the project and needs no
download. Chatterbox gives a real voice instead of a robotic one, but it runs in
a Python of your own (`pip install chatterbox-tts`) and takes a couple of minutes
per language on a CPU. Both are levelled to the same loudness, so switching
engine does not change how loud the cards sound next to the story.

The bare numbers and the word a language puts before them stay espeak-ng
whatever --engine says: Chatterbox 0.1.7 loses its place on a one-word phrase
and answers "of" with 23 seconds of noise. The total is recorded as the whole
phrase after the number — " sur 20", "，共 20 章" — which it does say.

Chatterbox also rambles now and then, so each phrase is measured against the
time espeak-ng takes on it and anything three times longer is recorded again,
the shorter take winning. A phrase that rambles through every take is left to
espeak-ng rather than shipped as a stutter.
"""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import tempfile
from pathlib import Path

from daisy2telmi import tts
from daisy2telmi.i18n import LANGUAGES

ROOT = Path(__file__).resolve().parents[1]

# A Chatterbox take this much longer than espeak-ng reading the same words is the
# model repeating itself rather than speaking.
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


def rows(lang, max_number: int) -> list[tuple[str, str]]:
    """The phrases the chosen engine records, with the file name each one becomes."""
    every = [(lang.chapter.format(n=n), f"{n:02d}.mp3") for n in range(1, max_number + 1)]
    totals = [(total_phrase(lang, n), f"of-{n:02d}.mp3") for n in range(1, max_number + 1)]
    return every + totals + [(lang.next_prompt, "next.mp3")]


def espeak_rows(lang, max_number: int) -> list[tuple[str, str]]:
    """The phrases espeak-ng records whatever the engine is: each one is a bare
    number or a single word, which is what Chatterbox turns into noise."""
    head = around(lang.number_of)[0]
    bare = [(str(n), f"n-{n:02d}.mp3") for n in range(1, max_number + 1)]
    words = [(head, "of-pre.mp3")] if any(c.isalnum() for c in head) else []
    return bare + words


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


def chatterbox_run(phrases: list[str], lang, raws: list[Path], folder: Path, python, device: str) -> None:
    """One process for the whole language: loading the model costs more than
    speaking a chapter number, and it would otherwise happen once per clip."""
    manifest = folder / "manifest.json"
    manifest.write_text(
        json.dumps({"phrases": phrases, "outs": [str(raw) for raw in raws], "lang": lang.code, "device": device}),
        encoding="utf-8",
    )
    result = subprocess.run(
        [python, "-I", str(tts._speak_script(tts.CHATTERBOX)), str(manifest)],
        capture_output=True,
        text=True,
    )
    if result.returncode:
        raise SystemExit(f"chatterbox failed: {(result.stderr or result.stdout).strip()}")
    print(f"  {result.stdout.strip().splitlines()[0]}")


def too_long(raws: list[Path], yardstick: list[float]) -> list[int]:
    """Which takes ran on past three times the espeak length of their words."""
    return [k for k, raw in enumerate(raws) if duration(raw) > RAMBLE * yardstick[k]]


def speak_chatterbox(
    phrases: list[str], lang, raws: list[Path], folder: Path, python, device: str, yardstick: list[float]
) -> tuple[int, int]:
    """Record every phrase, then take again the ones that ran on too long.

    A take three times the length of espeak-ng reading the same words is the
    model repeating itself, not speaking: the published cards carried a 10 s
    "Bölüm 30" before this existed. The shorter of the two takes is kept, and a
    phrase that rambles through every take is given to espeak-ng rather than
    shipped as a stutter — one robotic clip in a card is better than a card that
    says the same number four times.

    Returns how many takes were re-recorded and how many ended up espeak-ng.
    """
    chatterbox_run(phrases, lang, raws, folder, python, device)
    retaken = 0
    for round_ in range(RETRIES):
        long = too_long(raws, yardstick)
        if not long:
            break
        again = [folder / f"retake{round_}-{k}.wav" for k in long]
        chatterbox_run([phrases[k] for k in long], lang, again, folder, python, device)
        for k, new in zip(long, again):
            retaken += 1
            if duration(new) < duration(raws[k]):
                shutil.copyfile(new, raws[k])
    stubborn = too_long(raws, yardstick)
    speak_espeak([phrases[k] for k in stubborn], lang.espeak, [raws[k] for k in stubborn])
    return retaken, len(stubborn)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, default=ROOT / "web" / "voices")
    parser.add_argument("--max", type=int, default=50, help="highest chapter number to record")
    parser.add_argument("--bitrate", default="32")
    parser.add_argument("--engine", choices=(tts.ESPEAK, tts.CHATTERBOX), default=tts.ESPEAK)
    parser.add_argument("--python", default=None, help="interpreter --engine chatterbox runs in")
    parser.add_argument("--device", choices=tts.DEVICES, default=tts.AUTO)
    args = parser.parse_args()

    python = tts._python(args.python) if args.engine == tts.CHATTERBOX else None
    if args.engine == tts.CHATTERBOX and not python:
        raise SystemExit("chatterbox runs in your own Python: pass --python")

    total = 0
    written = 0
    for code, lang in LANGUAGES.items():
        directory = args.out / code
        directory.mkdir(parents=True, exist_ok=True)
        wanted = rows(lang, args.max)
        light = espeak_rows(lang, args.max)
        with tempfile.TemporaryDirectory(prefix="daisy2telmi-voices-") as tmp:
            folder = Path(tmp)
            raws = [folder / f"speech{k}.wav" for k in range(len(wanted) + len(light))]
            phrases = [phrase for phrase, _ in wanted]
            if args.engine == tts.CHATTERBOX:
                yardstick = espeak_seconds(phrases, lang.espeak, folder)
                retaken, robotic = speak_chatterbox(
                    phrases, lang, raws[: len(wanted)], folder, python, args.device, yardstick
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
        mixed = f", {len(light) + robotic} of them espeak-ng" if args.engine == tts.CHATTERBOX else ""
        again = f", {retaken} re-taken" if retaken else ""
        print(
            f"{code}: {len(wanted) + len(light)} clip(s){mixed}{again} — "
            f"longest {longest[1]} at {longest[0]:.2f} s"
        )

    print(f"{written} file(s), {total / 1e6:.2f} MB in {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
