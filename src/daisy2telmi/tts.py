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
PIPER = "piper"
ESPEAK = "espeak"
SAY = "say"
SAPI = "sapi"
COQUI = "coqui"
COMMAND = "command"
CHATTERBOX = "chatterbox"

# The engines `auto` may reach for, in the order it prefers them: the light ones
# first, because a card has to be spoken on a machine that may have nothing
# installed at all. Chatterbox and Coqui come after it, as the better voices a
# user chooses on purpose.
ENGINES = (AUTO, PIPER, ESPEAK, SAY, SAPI, COQUI, CHATTERBOX, COMMAND, OFF)
PREFERRED = (PIPER, ESPEAK, SAY, SAPI)

# What Chatterbox runs on. It picks the CPU on its own even where a GPU is
# there, so the user can force one; `auto` takes the fastest device found.
DEVICES = (AUTO, "cpu", "mps", "cuda")

# The engines that are a package in a Python rather than a program on the machine,
# and the name each one is imported by.
PACKAGES = {PIPER: "piper-tts", COQUI: "coqui-tts", CHATTERBOX: "chatterbox-tts"}
MODULES = {PIPER: "piper", COQUI: "TTS", CHATTERBOX: "chatterbox"}

SPEAKING_RATE = 150  # words per minute, espeak-ng's unit

SPEEDS = ("slow", "normal", "fast")
# espeak-ng and `say` count words a minute; Piper holds each phoneme longer or
# shorter. Only these three can be spoken at another speed.
RATES = {"slow": 120, "normal": SPEAKING_RATE, "fast": 190}
LENGTH_SCALES = {"slow": 1.25, "normal": 1.0, "fast": 0.8}
# Windows speech counts steps either side of zero, and a step is about 5%.
SAPI_RATES = {"slow": -3, "normal": 0, "fast": 3}

# A card is heard over the narration, so it is levelled instead of left at whatever
# the engine made: espeak lands near -25 LUFS and `say` near -16 on the same words.
CARD_TARGET = -20.0

# Spoken narration keeps the level the engine made, as an audio pack's own keeps
# its own; the cards are levelled against it afterwards. `""` means no chain.
UNLEVELLED = ""

# Chatterbox drifts from the reference voice after a few hundred words and
# espeak-ng is slow to answer, so narration is spoken in sentence-sized pieces.
NARRATION_CHUNK = 400

# The Piper voices to pick from, one entry per language the app has words for,
# named as rhasspy/piper-voices names its files. Each voice carries its own
# licence on its model card; the defaults here are CC0 or CC-BY but one.
PIPER_VOICES: dict[str, list[str]] = {
    "ar": ["ar_JO-kareem-medium", "ar_JO-kareem-low"],
    "de": ["de_DE-thorsten-medium", "de_DE-thorsten-high", "de_DE-mls-medium", "de_DE-kerstin-low"],
    "en": [
        "en_US-lessac-medium",
        "en_US-lessac-high",
        "en_US-lessac-low",
        "en_US-ryan-medium",
        "en_US-amy-medium",
        "en_GB-jenny_dioco-medium",
        "en_GB-vctk-medium",
    ],
    "es": ["es_ES-davefx-medium", "es_ES-sharvard-medium", "es_MX-ald-medium", "es_MX-claude-high"],
    "fr": ["fr_FR-siwis-medium", "fr_FR-upmc-medium", "fr_FR-tom-medium", "fr_FR-mls-medium", "fr_FR-siwis-low"],
    "it": ["it_IT-paola-medium", "it_IT-riccardo-x_low"],
    "nl": ["nl_NL-pim-medium", "nl_NL-ronnie-medium", "nl_NL-mls-medium", "nl_BE-nathalie-medium"],
    "pt": ["pt_BR-faber-medium", "pt_BR-cadu-medium", "pt_BR-jeff-medium", "pt_PT-tugão-medium"],
    "tr": ["tr_TR-fahrettin-medium", "tr_TR-dfki-medium", "tr_TR-fettah-medium"],
    "zh": ["zh_CN-huayan-medium", "zh_CN-huayan-x_low"],
}

# The one Coqui model that covers every language, at the price of a recording to
# clone and of the non-commercial terms its weights are released under.
COQUI_MULTILINGUAL = "tts_models/multilingual/multi-dataset/xtts_v2"

# The Coqui models for each language, in the order the options page lists them.
# Arabic has none of its own: only the multilingual model speaks it, and that one
# asks for a recording of the voice to read with.
COQUI_MODELS: dict[str, list[str]] = {
    "en": ["tts_models/en/ljspeech/vits", "tts_models/en/vctk/vits"],
    "fr": ["tts_models/fr/css10/vits", "tts_models/fr/mai/tacotron2-DDC"],
    "de": ["tts_models/de/thorsten/vits", "tts_models/de/css10/vits-neon"],
    "es": ["tts_models/es/css10/vits", "tts_models/es/mai/tacotron2-DDC"],
    "it": ["tts_models/it/mai_female/vits", "tts_models/it/mai_male/vits"],
    "nl": ["tts_models/nl/css10/vits", "tts_models/nl/rdh/tacotron2-DDC"],
    "pt": ["tts_models/pt/cv/vits"],
    "tr": ["tts_models/tr/common-voice/glow-tts"],
    "zh": ["tts_models/zh-CN/baker/tacotron2-DDC-GST"],
    "ar": [COQUI_MULTILINGUAL],
}

# The languages that one model can take a recording of.
COQUI_CLONE_LANGUAGES = {
    "en", "es", "fr", "de", "it", "pt", "nl", "tr", "ar", "zh", "pl", "ru", "cs", "ja", "hu", "ko", "hi",
}


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


def _pythons(python: str | None = None) -> list[str]:
    """Every interpreter the app could speak with: the one named, then the ones it built.

    The engines live in interpreters the app made for them, which are not on `PATH`,
    so asking only the machine's Python would report an installed engine as missing.
    """
    found: list[str] = []
    for exe in (_python(python), *(speech_python(settings.speech_env(name)) for name in PACKAGES)):
        if exe and os.path.exists(exe) and exe not in found:
            found.append(exe)
    return found


def _speak_script(engine: str) -> Path:
    """The helper a Python outside the app runs to speak a whole batch.

    Each engine gets its own file because each one loads its model differently,
    and none of them can be imported by the app itself: the binaries carry no
    torch and no onnxruntime.
    """
    name = f"{engine}_speak.py"
    if hasattr(sys, "_MEIPASS"):
        return Path(sys._MEIPASS) / name
    return Path(__file__).parent / name


# Spawning a Python to look for a package is cheap but not free, and the options
# page asks on every relabel.
_package_cache: dict[tuple[str, str], bool] = {}


def _has_package(engine: str, exe: str | None) -> bool:
    if not exe:
        return False
    key = (exe, engine)
    if key not in _package_cache:
        # find_spec only looks the package up: importing any of them pulls torch
        # or onnxruntime in, which is what asking costs.
        probe = f"import importlib.util as u; print(u.find_spec({MODULES[engine]!r}) is not None)"
        result = subprocess.run([exe, "-I", "-c", probe], capture_output=True, text=True)
        _package_cache[key] = result.stdout.strip() == "True"
    return _package_cache[key]


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
    if name in PACKAGES:
        return any(_has_package(name, exe) for exe in _pythons(python))
    return False


GET_PIP = "https://bootstrap.pypa.io/get-pip.py"

# What each engine is called to a person. The order is the order the options page
# lists them in, and the text the install errors use.
LABELS = {
    PIPER: "Piper",
    ESPEAK: "espeak-ng",
    SAY: "macOS say",
    SAPI: "Windows speech",
    COQUI: "Coqui TTS",
    CHATTERBOX: "Chatterbox",
    COMMAND: "custom command",
}


def speech_python(env: Path) -> str:
    """The interpreter of the environment `env` holds, whether it was built or not."""
    return str(env / ("Scripts/python.exe" if sys.platform == "win32" else "bin/python"))


def make_speech_env(engine: str) -> str:
    """Build the app's own Python environment for `engine`, and return its interpreter.

    The machine's Python is not a safe place to install: Debian 13 and Fedora mark
    it externally managed and ship no pip module at all, so `python3 -m pip install`
    there fails before it starts. A venv of our own needs no root and leaves the
    system alone. Debian's own venv cannot bootstrap pip either, so pip is fetched
    when `ensurepip` is missing; where it cannot be fetched, an environment that
    reads the system's packages runs the machine's own pip instead.
    """
    env = settings.speech_env(engine)
    exe = speech_python(env)
    if os.path.exists(exe):
        if _has_pip(exe):
            return exe
        # An environment left behind by a run that could not fetch pip has an
        # interpreter and nothing to install with: it has to be fixed, not reused.
        log.info("%s has no pip", env)
        try:
            _bootstrap_pip(exe)
            return exe
        except TtsError as exc:
            log.info("%s: making a new environment", exc)
            shutil.rmtree(env, ignore_errors=True)
    base = _python(None)
    if not base:
        raise TtsError(
            f"no Python to build a {LABELS[engine]} environment with: install Python 3.10 or newer, "
            f"or set the {LABELS[engine]} Python in the options"
        )
    env.parent.mkdir(parents=True, exist_ok=True)
    log.info("making a %s environment in %s", LABELS[engine], env)
    result = subprocess.run([base, "-m", "venv", str(env)], capture_output=True, text=True)
    if result.returncode == 0 and _has_pip(exe):
        return exe
    # Half an environment is worse than none: the retry has to start clean. A
    # venv that came back without pip is half an environment too.
    shutil.rmtree(env, ignore_errors=True)
    log.info(
        "this Python cannot make an environment with pip: %s",
        _reason(result) if result.returncode else "the environment it made has no pip",
    )
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
                f"no pip for the {LABELS[engine]} environment: install Python's venv package "
                f"(on Debian: apt install python3-venv), or set a {LABELS[engine]} Python in the options"
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
    exe = speech_python(env)
    if not _has_pip(exe):
        return False
    log.info("pip comes from the machine's Python")
    return True


def _has_pip(exe: str) -> bool:
    """Whether that interpreter can run pip as a module."""
    return subprocess.run([exe, "-m", "pip", "--version"], capture_output=True).returncode == 0


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
        raise TtsError(f"could not install pip into the environment: {_reason(result)}")


def _stream(exe: str, args: list[str], label: str) -> None:
    """Run a long download in `exe`, logging its output line by line.

    A silent minute and a half looks like a hang, and a torch wheel is a silent
    minute and a half.
    """
    log.info("%s: %s", label, " ".join(args[:4]))
    process = subprocess.Popen([exe, *args], stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    assert process.stdout is not None
    for line in process.stdout:
        if line.strip():
            log.info("%s: %s", label, line.strip())
    if process.wait():
        raise TtsError(f"{label} failed in {exe}: {' '.join(args)}")


def install_engine(engine: str, python: str | None = None) -> str:
    """Install `engine`'s package, and answer which interpreter now has it.

    Asked for rather than done on the side: Chatterbox and Coqui bring torch with
    them, which is a download of a gigabyte or more.

    With no Python named, the app installs into one it made itself: the machine's
    own Python refuses the install on Debian 13 and Fedora.
    """
    package = PACKAGES.get(engine)
    if package is None:
        raise TtsError(f"{LABELS.get(engine, engine)} is not something the app can install")
    exe = _python(python) if python else make_speech_env(engine)
    if not exe:
        raise TtsError(
            f"no Python to install {package} into: set the {LABELS[engine]} Python in the options, "
            "or pass --tts-python"
        )
    _stream(exe, ["-m", "pip", "install", "--upgrade", package], "pip")
    _package_cache.clear()
    log.info("%s is installed in %s", package, exe)
    return exe


def engine_python(engine: str, python: str | None = None) -> str:
    """The interpreter that has `engine`: the one named, else the one the app built."""
    for exe in _pythons(python):
        if _has_package(engine, exe):
            return exe
    raise TtsError(f"{LABELS[engine]} is not installed: install it from the speech page first")


def download_voice(voice: str, python: str | None = None) -> Path:
    """Download one Piper voice into the app's voice folder, and answer where it landed.

    The weights are the one part of a speech engine the app never ships: a voice is
    tens of megabytes, and the machine that speaks the cards should fetch its own.
    """
    exe = engine_python(PIPER, python)
    folder = settings.speech_voices()
    folder.mkdir(parents=True, exist_ok=True)
    _stream(exe, ["-m", "piper.download_voices", voice, "--data-dir", str(folder)], "piper")
    path = folder / f"{voice}.onnx"
    if not path.is_file():
        raise TtsError(f"the {voice} voice did not arrive in {folder}")
    log.info("%s is in %s", voice, folder)
    return path


def engines(python: str | None = None) -> list[Engine]:
    return [
        Engine(name, LABELS[name], bool(available(name, python)))
        for name in ENGINES
        if name not in (AUTO, OFF)
    ]


def resolve(engine: str, lang: Language, python: str | None = None) -> str:
    """Pick an engine. `auto` takes the lightest one that can speak `lang`.

    Piper wins when it is installed and has a voice for the language on disk: it is
    neural, so it is the only one of the light engines that sounds like a person, and
    it runs on a CPU. espeak-ng follows, because it covers all ten languages with
    nothing installed at all.
    """
    if engine == OFF:
        return OFF
    if engine == AUTO:
        for name in PREFERRED:
            if not available(name, python):
                continue
            if name == PIPER and piper_voice(lang) is None:
                continue
            return name
        raise TtsError(
            "no speech engine found. Install espeak-ng "
            "(macOS: brew install espeak-ng, Debian/Ubuntu: apt install espeak-ng), "
            "or install Piper from the speech page."
        )
    if engine not in ENGINES:
        raise TtsError(f"unknown speech engine {engine!r}; choose from {', '.join(ENGINES)}")
    if engine in PACKAGES and not _pythons(python):
        raise TtsError(
            f"{LABELS[engine]} runs in your own Python: pass --tts-python with that interpreter"
        )
    if engine in PACKAGES and not available(engine, python):
        raise TtsError(
            f"{LABELS[engine]} is not installed: pip install {PACKAGES[engine]} into a Python of your "
            "own and point --tts-python at it, or use the Install button on the speech page"
        )
    if engine != COMMAND and not available(engine, python):
        raise TtsError(f"{engine} is not available on this machine")
    return engine


def piper_voices(lang: Language | str) -> list[str]:
    """The Piper voices already downloaded for `lang`.

    The ones this app lists come first, in the order it lists them; a voice the user
    put in the folder themselves is still offered, at the end.
    """
    code = lang.code if isinstance(lang, Language) else str(lang)
    found = {path.name[: -len(".onnx")] for path in settings.speech_voices().glob(f"{code}_*-*.onnx")}
    listed = [name for name in PIPER_VOICES.get(code, []) if name in found]
    return listed + sorted(found - set(PIPER_VOICES.get(code, [])))


def piper_voice(lang: Language | str, voice: str | None = None) -> Path | None:
    """The model file for one Piper voice, or None when it was never downloaded."""
    name = voice or (piper_voices(lang) or [None])[0]
    if not name:
        return None
    path = Path(name)
    if path.is_file():
        return path
    path = settings.speech_voices() / f"{name}.onnx"
    return path if path.is_file() else None


def voices(engine: str, lang: Language) -> list[str]:
    """Voices the engine offers for `lang`, for the options page to list."""
    if engine == PIPER:
        return piper_voices(lang)
    if engine == COQUI:
        models = list(COQUI_MODELS.get(lang.code, []))
        if lang.code in COQUI_CLONE_LANGUAGES and COQUI_MULTILINGUAL not in models:
            models.append(COQUI_MULTILINGUAL)
        return models
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


def _render(
    engine: str,
    phrase: str,
    lang: Language,
    voice: str | None,
    command: str | None,
    out: Path,
    speed: str = "normal",
) -> None:
    if engine == ESPEAK:
        exe = _which("espeak-ng") or _which("espeak")
        voice_name = voice or lang.espeak
        _check(
            [exe, "-v", voice_name, "-s", str(RATES.get(speed, SPEAKING_RATE)), "-w", str(out), "--", phrase],
            "espeak-ng",
            _espeak_env(),
        )
    elif engine == SAY:
        # Without a matching voice, say reads foreign text with the default one
        # and mangles it, so pick one for the language rather than trusting -v.
        voice = voice or (voices(SAY, lang) or [None])[0]
        args = ["-r", str(RATES.get(speed, SPEAKING_RATE))]
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
            f"$s.Rate={SAPI_RATES.get(speed, 0)};"
            f"$s.SetOutputToWaveFile('{out}');$s.Speak('{spoken}');$s.Dispose();"
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
    speed: str = "normal",
) -> None:
    suffix = ".aiff" if engine == SAY else ".wav"
    with tempfile.TemporaryDirectory(prefix="daisy2telmi-tts-") as tmp:
        raw = Path(tmp) / f"speech{suffix}"
        _render(engine, phrase, lang, voice, command, raw, speed)
        _to_spec(raw, dest, engine, phrase, loudness)
    log.debug("speech via %s: %r -> %s", engine, phrase, dest.name)


def _speak_batch(
    engine: str,
    phrases: list[str],
    lang: Language,
    dests: list[Path],
    voice: str | None,
    clip: str | None,
    python: str | None,
    device: str,
    speed: str,
    loudness: str,
) -> None:
    """One process for the whole batch: loading a neural model costs far more
    than speaking a chapter number, and it would otherwise happen once per card."""
    exe = engine_python(engine, python)
    manifest: dict = {
        "phrases": phrases,
        "outs": [],
        "lang": lang.code,
        "device": device,
    }
    if engine == PIPER:
        model = piper_voice(lang, voice)
        if model is None:
            raise TtsError(
                f"no Piper voice for {lang.name} is downloaded: pick one on the speech page and "
                "use Install voice"
            )
        manifest["model"] = str(model)
        manifest["length_scale"] = LENGTH_SCALES.get(speed, 1.0)
    elif engine == COQUI:
        model = voice or (voices(COQUI, lang) or [None])[0]
        if not model:
            raise TtsError(f"Coqui has no model listed for {lang.name}: name one with --tts-voice")
        manifest["model"] = model
        manifest["clip"] = clip
        if model == COQUI_MULTILINGUAL and not clip:
            raise TtsError(
                f"{COQUI_MULTILINGUAL} reads with a cloned voice: give it a recording with --tts-clip "
                "or pick another model"
            )
    else:
        manifest["clip"] = clip
    label = LABELS[engine]
    log.info("speaking %d phrase(s) with %s in %s", len(phrases), label, exe)
    with tempfile.TemporaryDirectory(prefix=f"daisy2telmi-{engine}-") as tmp:
        folder = Path(tmp)
        raw = [folder / f"speech{k}.wav" for k in range(len(phrases))]
        manifest["outs"] = [str(path) for path in raw]
        manifest_path = folder / "manifest.json"
        manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
        result = _check([exe, "-I", str(_speak_script(engine)), str(manifest_path)], engine)
        # The script reports the device it settled on, so a run that silently
        # stayed on the CPU is visible in the log.
        for line in result.stdout.splitlines():
            if line.startswith(f"{engine} "):
                log.info(line)
        for phrase, source, dest in zip(phrases, raw, dests):
            _to_spec(source, dest, engine, phrase, loudness)


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
    speed: str = "normal",
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
    if chosen in PACKAGES:
        _speak_batch(chosen, list(phrases), lang, list(dests), voice, clip, python, device, speed, chain)
        return
    for phrase, dest in zip(phrases, dests):
        _speak_one(phrase, lang, dest, chosen, voice, command, chain, speed)


def synthesize(
    phrase: str,
    lang_code: str,
    dest: Path,
    engine: str = AUTO,
    voice: str | None = None,
    command: str | None = None,
    python: str | None = None,
    clip: str | None = None,
    device: str = AUTO,
    speed: str = "normal",
) -> None:
    """Speak `phrase` into `dest` as an MP3 in the Telmi audio spec."""
    synthesize_all([phrase], lang_code, [dest], engine, voice, command, python, clip, device, speed)


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
    speed: str = "normal",
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
        synthesize_all(
            phrases, lang_code, parts, engine, voice, command, python, clip, device, speed, UNLEVELLED
        )
        audio.render([AudioSegment(part) for part in parts], dest)
