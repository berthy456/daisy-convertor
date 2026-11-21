"""Turn a short phrase into an MP3 using whatever speech engine the machine has.

Every supported engine writes a lossless or near-lossless file into a temp
directory; ffmpeg then brings it to the Telmi audio spec, so the announcement
sits in the same `audios/` folder as the chapters and needs no special handling
in the player.
"""

from __future__ import annotations

import base64
import json
import logging
import os
import re
import shutil
import subprocess
import sys
import tempfile
import urllib.request
from dataclasses import dataclass
from pathlib import Path

from . import audio, settings
from .extractor import AudioSegment
from .i18n import Language, language

log = logging.getLogger(__name__)

AUTO = "auto"
OFF = "off"
ESPEAK = "espeak"
SAY = "say"
SAPI = "sapi"
COMMAND = "command"
CHATTERBOX = "chatterbox"

ENGINES = (AUTO, ESPEAK, SAY, SAPI, COMMAND, CHATTERBOX, OFF)

# What Chatterbox runs on. It picks the CPU on its own even where a GPU is
# there, so the user can force one; `auto` takes the fastest device found.
DEVICES = (AUTO, "cpu", "mps", "cuda")

SPEAKING_RATE = 150  # words per minute, espeak-ng's unit

# A card is heard over the narration, so it is levelled instead of left at whatever
# the engine made: espeak lands near -25 LUFS and `say` near -16 on the same words.
CARD_TARGET = -20.0

# Spoken narration keeps the level the engine made, as an audio pack's own keeps
# its own; the cards are levelled against it afterwards. `""` means no chain.
UNLEVELLED = ""

# Chatterbox drifts from the reference voice after a few hundred words and
# espeak-ng is slow to answer, so narration is spoken in sentence-sized pieces.
NARRATION_CHUNK = 400


def card_target(story: float | None) -> float:
    """The LUFS a card is levelled to: a little under the story it interrupts.

    Published packs sit anywhere between -18 and -25 LUFS, so one fixed target is
    too loud for a quiet pack and too soft for a loud one.
    """
    if story is None:
        return CARD_TARGET
    return min(max(story - 2.0, -26.0), -16.0)


def card_loudness(story: float | None) -> str:
    """The ffmpeg chain that brings a card to `card_target`."""
    return f"loudnorm=I={card_target(story):.1f}:TP=-2:LRA=11"


class TtsError(RuntimeError):
    """Raised when no engine can produce the audio."""


@dataclass(frozen=True)
class Engine:
    name: str
    label: str
    found: bool


def _which(name: str) -> str | None:
    bundled = audio.bundled_executable(name)
    return str(bundled) if bundled else shutil.which(name)


def _espeak_env() -> dict[str, str]:
    """Point a bundled espeak-ng at the language data sitting next to it.

    Without this the binary finds no voices and says nothing at all, which looks
    like a working engine that produces silence.
    """
    env = dict(os.environ)
    bundled = audio.bundled_executable("espeak-ng")
    if bundled is None:
        return env
    data = Path(sys._MEIPASS) / "espeak-ng-data" if hasattr(sys, "_MEIPASS") else bundled.parent / "espeak-ng-data"
    if data.is_dir():
        env["ESPEAK_DATA_PATH"] = str(data)
    if sys.platform != "darwin":
        # The copied binary still names its libraries by their build-machine
        # paths; the loader looks next to the binary instead. macOS gets the same
        # effect from @loader_path rewrites at build time.
        env["LD_LIBRARY_PATH"] = os.pathsep.join([str(bundled.parent), env.get("LD_LIBRARY_PATH", "")]).rstrip(
            os.pathsep
        )
    return env


def _python(python: str | None = None) -> str | None:
    """A Python the user can install models into; a frozen app has none of its own."""
    return python or _which("python3") or _which("python")


def _speak_script() -> Path:
    if hasattr(sys, "_MEIPASS"):
        return Path(sys._MEIPASS) / "chatterbox_speak.py"
    return Path(__file__).parent / "chatterbox_speak.py"


# Spawning a Python to look for a package is cheap but not free, and the options
# page asks on every relabel.
_chatterbox_cache: dict[str, bool] = {}


def _has_chatterbox(exe: str | None) -> bool:
    if not exe:
        return False
    if exe not in _chatterbox_cache:
        # find_spec only looks the package up: importing chatterbox would pull torch in.
        probe = "import importlib.util as u; print(u.find_spec('chatterbox') is not None)"
        result = subprocess.run([exe, "-I", "-c", probe], capture_output=True, text=True)
        _chatterbox_cache[exe] = result.stdout.strip() == "True"
    return _chatterbox_cache[exe]


def available(name: str, python: str | None = None) -> bool:
    if name == ESPEAK:
        return _which("espeak-ng") or _which("espeak")
    if name == SAY:
        return sys.platform == "darwin" and _which("say")
    if name == SAPI:
        # pwsh runs on Linux and macOS, but System.Speech is Windows only.
        return sys.platform == "win32" and (_which("powershell") or _which("pwsh"))
    if name == COMMAND:
        return True
    if name == CHATTERBOX:
        return _has_chatterbox(_python(python))
    return False


CHATTERBOX_PACKAGE = "chatterbox-tts"
GET_PIP = "https://bootstrap.pypa.io/get-pip.py"


def chatterbox_python(env: Path) -> str:
    """The interpreter of the environment `env` holds, whether it was built or not."""
    return str(env / ("Scripts/python.exe" if sys.platform == "win32" else "bin/python"))


def make_chatterbox_env() -> str:
    """Build the app's own Python environment for Chatterbox, and return its interpreter.

    The machine's Python is not a safe place to install: Debian 13 and Fedora mark
    it externally managed and ship no pip module at all, so `python3 -m pip install`
    there fails before it starts. A venv of our own needs no root and leaves the
    system alone. Debian's own venv cannot bootstrap pip either, so pip is fetched
    when `ensurepip` is missing; where it cannot be fetched, an environment that
    reads the system's packages runs the machine's own pip instead.
    """
    env = settings.chatterbox_env()
    exe = chatterbox_python(env)
    if os.path.exists(exe):
        return exe
    base = _python(None)
    if not base:
        raise TtsError(
            "no Python to build a Chatterbox environment with: install Python 3.11 or newer, "
            "or set the Chatterbox Python in the options"
        )
    env.parent.mkdir(parents=True, exist_ok=True)
    log.info("making a Chatterbox environment in %s", env)
    result = subprocess.run([base, "-m", "venv", str(env)], capture_output=True, text=True)
    if result.returncode == 0:
        return exe
    # Half an environment is worse than none: the retry has to start clean.
    shutil.rmtree(env, ignore_errors=True)
    log.info("this Python cannot make an environment with pip: %s", _reason(result))
    result = subprocess.run(
        [base, "-m", "venv", "--without-pip", str(env)], capture_output=True, text=True
    )
    if result.returncode:
        raise TtsError(f"could not make a Python environment: {_reason(result)}")
    try:
        _bootstrap_pip(exe)
    except TtsError as exc:
        # pip could not be fetched, so the only pip left is the machine's own. An
        # environment that reads the system's packages can run it, and pip still
        # installs inside the environment.
        log.info("%s: trying the machine's own pip", exc)
        if not _shared_pip(base, env):
            raise TtsError(
                "no pip for the Chatterbox environment: install Python's venv package "
                "(on Debian: apt install python3-venv), or set a Chatterbox Python in the options"
            ) from exc
    return exe


def _reason(result: subprocess.CompletedProcess) -> str:
    """What a failed command said, as one line: its own words, from the end."""
    text = " ".join((result.stderr or result.stdout or "").split())
    return text[-200:] if text else "no reason given"


def _shared_pip(base: str, env: Path) -> bool:
    """Rebuild `env` reading the system's packages, if that gives it a working pip."""
    shutil.rmtree(env, ignore_errors=True)
    result = subprocess.run(
        [base, "-m", "venv", "--without-pip", "--system-site-packages", str(env)],
        capture_output=True,
        text=True,
    )
    if result.returncode:
        return False
    probe = subprocess.run(
        [chatterbox_python(env), "-m", "pip", "--version"], capture_output=True, text=True
    )
    if probe.returncode:
        return False
    log.info("pip comes from the machine's Python: %s", _reason(probe))
    return True


def _bootstrap_pip(exe: str) -> None:
    """Give an environment made with `--without-pip` its pip."""
    log.info("this Python has no ensurepip: fetching pip")
    try:
        with urllib.request.urlopen(GET_PIP, timeout=60) as response:
            script = Path(tempfile.mkstemp(prefix="get-pip-", suffix=".py")[1])
            script.write_bytes(response.read())
    except OSError as exc:
        raise TtsError(f"could not reach {GET_PIP}: {exc}") from exc
    try:
        result = subprocess.run([exe, str(script)], capture_output=True, text=True)
    finally:
        script.unlink(missing_ok=True)
    if result.returncode:
        raise TtsError(f"could not install pip into the Chatterbox environment: {_reason(result)}")


def install_chatterbox(python: str | None = None) -> str:
    """Install Chatterbox, and answer which interpreter now has it.

    Asked for rather than done on the side: the wheel brings torch with it, which
    is a download of a gigabyte or more. pip's own output goes to the log, since
    a silent minute and a half looks like a hang.

    With no Python named, the app installs into one it made itself: the machine's
    own Python refuses the install on Debian 13 and Fedora.
    """
    exe = _python(python) if python else make_chatterbox_env()
    if not exe:
        raise TtsError(
            "no Python to install Chatterbox into: set the Chatterbox Python in the options, "
            "or pass --tts-python"
        )
    command = [exe, "-m", "pip", "install", "--upgrade", CHATTERBOX_PACKAGE]
    log.info("installing %s into %s", CHATTERBOX_PACKAGE, exe)
    process = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    assert process.stdout is not None
    for line in process.stdout:
        if line.strip():
            log.info("pip: %s", line.strip())
    if process.wait():
        raise TtsError(f"pip could not install {CHATTERBOX_PACKAGE} into {exe}")
    _chatterbox_cache.clear()
    log.info("%s is installed", CHATTERBOX_PACKAGE)
    return exe


def engines(python: str | None = None) -> list[Engine]:
    labels = {
        ESPEAK: "espeak-ng",
        SAY: "macOS say",
        SAPI: "Windows speech",
        COMMAND: "custom command",
        CHATTERBOX: "Chatterbox",
    }
    return [Engine(name, labels[name], bool(available(name, python))) for name in labels]


def resolve(engine: str, lang: Language, python: str | None = None) -> str:
    """Pick an engine. `auto` prefers espeak-ng: it covers all ten languages."""
    if engine in (AUTO, OFF):
        if engine == OFF:
            return OFF
        for name in (ESPEAK, SAY, SAPI):
            if available(name):
                return name
        raise TtsError(
            "no speech engine found. Install espeak-ng "
            "(macOS: brew install espeak-ng, Debian/Ubuntu: apt install espeak-ng) "
            "or pass --tts command."
        )
    if engine not in ENGINES:
        raise TtsError(f"unknown speech engine {engine!r}; choose from {', '.join(ENGINES)}")
    if engine == CHATTERBOX and not _python(python):
        raise TtsError("chatterbox runs in your own Python: pass --tts-python with that interpreter")
    if engine == CHATTERBOX and not _has_chatterbox(_python(python)):
        raise TtsError(
            "chatterbox is not installed in that Python: pip install chatterbox-tts there, "
            "or point --tts-python at an interpreter that has it"
        )
    if engine != COMMAND and not available(engine, python):
        raise TtsError(f"{engine} is not available on this machine")
    return engine


def voices(engine: str, lang: Language) -> list[str]:
    """Voices the engine offers for `lang`, for the options page to list."""
    if engine == ESPEAK and (exe := _which("espeak-ng") or _which("espeak")):
        return _lines(exe, ["--voices=" + lang.espeak], _espeak_env(), skip_header=True)
    if engine == SAY and (exe := _which("say")):
        result = subprocess.run([exe, "-v", "?"], capture_output=True, text=True)
        wanted = lang.locale.replace("-", "_")
        return [
            line.split()[0]
            for line in result.stdout.splitlines()
            if len(line.split()) > 1
            and (line.split()[1] == wanted or line.split()[1].split("_")[0] == lang.code)
        ]
    if engine == SAPI and (exe := _which("powershell") or _which("pwsh")):
        script = (
            "[System.Speech.Synthesis.SpeechSynthesizer]::new().GetInstalledVoices() |"
            " ForEach-Object { $_.VoiceInfo.Name + \"`t\" + $_.VoiceInfo.Culture.Name }"
        )
        result = _run_powershell(exe, script)
        return [line.split("\t")[0] for line in result.splitlines() if lang.code in line.lower()]
    return []


def _lines(exe: str, args: list[str], env: dict[str, str] | None = None, skip_header: bool = False) -> list[str]:
    result = subprocess.run([exe, *args], capture_output=True, text=True, env=env)
    rows = result.stdout.splitlines()[1:] if skip_header else result.stdout.splitlines()
    names = []
    for row in rows:
        columns = row.split()
        if len(columns) >= 4:
            names.append(columns[3])
    return names


def _run_powershell(exe: str, script: str) -> str:
    # -EncodedCommand keeps non-ASCII phrases (Arabic, Chinese) intact and avoids quoting.
    payload = base64.b64encode(script.encode("utf-16-le")).decode("ascii")
    result = subprocess.run(
        [exe, "-NoProfile", "-NonInteractive", "-EncodedCommand", payload],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        raise TtsError(f"speech command failed: {(result.stderr or result.stdout).strip()}")
    return result.stdout.strip()


def _render(engine: str, phrase: str, lang: Language, voice: str | None, command: str | None, out: Path) -> None:
    if engine == ESPEAK:
        exe = _which("espeak-ng") or _which("espeak")
        voice_name = voice or lang.espeak
        _check(
            [exe, "-v", voice_name, "-s", str(SPEAKING_RATE), "-w", str(out), "--", phrase],
            "espeak-ng",
            _espeak_env(),
        )
    elif engine == SAY:
        # Without a matching voice, say reads foreign text with the default one
        # and mangles it, so pick one for the language rather than trusting -v.
        voice = voice or (voices(SAY, lang) or [None])[0]
        args = ["-r", str(SPEAKING_RATE)]
        if voice:
            args += ["-v", voice]
        elif lang.code != "en":
            log.warning("no %s voice installed for `say`; the default voice will read it", lang.name)
        _check(["say", *args, "-o", str(out), "--", phrase], "say")
    elif engine == SAPI:
        exe = _which("powershell") or _which("pwsh")
        # A single-quoted PowerShell string only ends at a doubled quote, and book
        # text is full of apostrophes.
        spoken = phrase.replace("'", "''")
        select = f"$s.SelectVoice('{voice}');" if voice else ""
        script = (
            "Add-Type -AssemblyName System.Speech;"
            "$s=[System.Speech.Synthesis.SpeechSynthesizer]::new();"
            f"{select}"
            f"$s.Rate=0;$s.SetOutputToWaveFile('{out}');$s.Speak('{spoken}');$s.Dispose();"
        )
        _run_powershell(exe, script)
    elif engine == COMMAND:
        if not command:
            raise TtsError("--tts command needs --tts-command with {text}, {out} and {lang} placeholders")
        import shlex

        argv = [
            part.format(text=phrase, out=str(out), lang=lang.code, locale=lang.locale, voice=voice or "")
            for part in shlex.split(command)
        ]
        _check(argv, "custom speech command")
    else:
        raise TtsError(f"cannot speak with engine {engine!r}")


def _check(argv: list[str], label: str, env: dict[str, str] | None = None) -> subprocess.CompletedProcess:
    log.debug("running: %s", " ".join(argv))
    result = subprocess.run(argv, capture_output=True, text=True, env=env)
    if result.returncode != 0:
        detail = (result.stderr or result.stdout or "").strip().splitlines()
        raise TtsError(f"{label} failed: {detail[-1] if detail else 'exit ' + str(result.returncode)}")
    return result


def _to_spec(raw: Path, dest: Path, engine: str, phrase: str, loudness: str) -> None:
    if not raw.is_file() or raw.stat().st_size == 0:
        raise TtsError(f"{engine} produced no audio for {phrase!r}")
    audio.convert_to_spec(raw, dest, loudness)


def _speak_one(
    phrase: str,
    lang: Language,
    dest: Path,
    engine: str,
    voice: str | None,
    command: str | None,
    loudness: str,
) -> None:
    suffix = ".aiff" if engine == SAY else ".wav"
    with tempfile.TemporaryDirectory(prefix="daisy2telmi-tts-") as tmp:
        raw = Path(tmp) / f"speech{suffix}"
        _render(engine, phrase, lang, voice, command, raw)
        _to_spec(raw, dest, engine, phrase, loudness)
    log.debug("speech via %s: %r -> %s", engine, phrase, dest.name)


def _speak_batch(
    phrases: list[str],
    lang: Language,
    dests: list[Path],
    clip: str | None,
    python: str | None,
    device: str,
    loudness: str,
) -> None:
    """One Chatterbox process for the whole batch: loading the model costs more
    than speaking a chapter number, and it would otherwise happen once per card."""
    exe = _python(python)
    log.info("speaking %d card(s) with Chatterbox in %s; the first run downloads the model", len(phrases), exe)
    with tempfile.TemporaryDirectory(prefix="daisy2telmi-chatterbox-") as tmp:
        folder = Path(tmp)
        raw = [folder / f"speech{k}.wav" for k in range(len(phrases))]
        manifest = folder / "manifest.json"
        manifest.write_text(
            json.dumps(
                {
                    "phrases": phrases,
                    "outs": [str(path) for path in raw],
                    "lang": lang.code,
                    "clip": clip,
                    "device": device,
                }
            ),
            encoding="utf-8",
        )
        result = _check([exe, "-I", str(_speak_script()), str(manifest)], "chatterbox")
        # The script reports the device it settled on, so a run that silently
        # stayed on the CPU is visible in the log.
        for line in result.stdout.splitlines():
            if line.startswith("chatterbox "):
                log.info(line)
        for phrase, source, dest in zip(phrases, raw, dests):
            _to_spec(source, dest, CHATTERBOX, phrase, loudness)


def synthesize_all(
    phrases: list[str],
    lang_code: str,
    dests: list[Path],
    engine: str = AUTO,
    voice: str | None = None,
    command: str | None = None,
    python: str | None = None,
    clip: str | None = None,
    device: str = AUTO,
    loudness: str | None = None,
) -> None:
    """Speak every phrase into `dests`, one file each.

    `loudness` is the ffmpeg chain the phrase is levelled to; it defaults to
    `card_loudness(None)`, the level a card takes when the story was never measured.
    """
    if not phrases:
        return
    if len(phrases) != len(dests):
        raise TtsError("every phrase needs an output file")
    lang = language(lang_code)
    chosen = resolve(engine, lang, python)
    if chosen == OFF:
        raise TtsError("speech is switched off")
    chain = card_loudness(None) if loudness is None else loudness
    if chosen == CHATTERBOX:
        _speak_batch(list(phrases), lang, list(dests), clip, python, device, chain)
        return
    for phrase, dest in zip(phrases, dests):
        _speak_one(phrase, lang, dest, chosen, voice, command, chain)


def synthesize(
    phrase: str,
    lang_code: str,
    dest: Path,
    engine: str = AUTO,
    voice: str | None = None,
    command: str | None = None,
    python: str | None = None,
) -> None:
    """Speak `phrase` into `dest` as an MP3 in the Telmi audio spec."""
    synthesize_all([phrase], lang_code, [dest], engine, voice, command, python)


_SENTENCE_BREAK = re.compile(r"(?<=[.!?:;…])\s+")


def _chunks(text: str, limit: int = NARRATION_CHUNK) -> list[str]:
    """Split narration into sentence-sized phrases, none longer than `limit`."""
    out: list[str] = []
    current = ""
    for sentence in (s.strip() for s in _SENTENCE_BREAK.split(text)):
        while len(sentence) > limit:
            cut = sentence.rfind(" ", 0, limit)
            cut = cut if cut > 0 else limit
            if current:
                out.append(current)
                current = ""
            out.append(sentence[:cut].strip())
            sentence = sentence[cut:].strip()
        if not sentence:
            continue
        if current and len(current) + 1 + len(sentence) > limit:
            out.append(current)
            current = sentence
        else:
            current = f"{current} {sentence}".strip()
    if current:
        out.append(current)
    return out


def narrate(
    text: str,
    lang_code: str,
    dest: Path,
    engine: str = AUTO,
    voice: str | None = None,
    command: str | None = None,
    python: str | None = None,
    clip: str | None = None,
    device: str = AUTO,
) -> None:
    """Speak a whole part of a book into `dest`, chunked and joined back.

    A text-only pack carries no audio at all, so its scenes are made here. The
    pieces are already at the Telmi spec, so joining them costs no re-encode.
    """
    phrases = _chunks(text)
    if not phrases:
        raise TtsError("nothing to speak")
    with tempfile.TemporaryDirectory(prefix="daisy2telmi-narration-") as tmp:
        parts = [Path(tmp) / f"part{index}.mp3" for index in range(len(phrases))]
        synthesize_all(phrases, lang_code, parts, engine, voice, command, python, clip, device, UNLEVELLED)
        audio.render([AudioSegment(part) for part in parts], dest)
