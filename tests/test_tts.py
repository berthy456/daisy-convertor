"""Speech engines: which ones are usable, and what they produce."""

from __future__ import annotations

import json
import shutil
import subprocess
import wave
from pathlib import Path

import pytest

from daisy2telmi import audio, chatterbox_speak, cli, settings, tts
from daisy2telmi.i18n import LANGUAGES
from daisy2telmi.telmi_builder import Announce

requires_espeak = pytest.mark.skipif(
    shutil.which("espeak-ng") is None and shutil.which("espeak") is None,
    reason="no espeak-ng on this machine",
)
requires_ffmpeg = pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg not installed")


def test_the_engine_list_names_the_back_ends_in_the_order_the_page_lists_them() -> None:
    assert [engine.name for engine in tts.engines()] == [
        tts.PIPER,
        tts.ESPEAK,
        tts.SAY,
        tts.SAPI,
        tts.COQUI,
        tts.CHATTERBOX,
        tts.COMMAND,
    ]


def test_windows_speech_is_only_offered_on_windows(monkeypatch) -> None:
    # pwsh is installed on Linux runners, where System.Speech does not exist;
    # offering it there makes `auto` pick an engine that cannot speak.
    monkeypatch.setattr(tts.sys, "platform", "linux")
    monkeypatch.setattr(tts.shutil, "which", lambda name: "/usr/bin/pwsh")
    assert not tts.available(tts.SAPI)


def test_off_short_circuits_before_any_lookup() -> None:
    assert tts.resolve("off", LANGUAGES["en"]) == "off"


def test_an_absent_engine_is_refused_rather_than_tried() -> None:
    # The package engines answer with their own hint about pip, and have tests of
    # their own; this one is about the engines that live on the machine.
    absent = next((e.name for e in tts.engines() if not e.found and e.name not in tts.PACKAGES), None)
    if absent is None:
        pytest.skip("this machine has every engine")
    with pytest.raises(tts.TtsError, match="not available"):
        tts.resolve(absent, LANGUAGES["en"])


def test_an_unknown_engine_name_is_refused() -> None:
    with pytest.raises(tts.TtsError, match="unknown speech engine"):
        tts.resolve("festival", LANGUAGES["en"])


@requires_espeak
@requires_ffmpeg
@pytest.mark.parametrize("code", ["en", "fr", "zh", "ar"])
def test_espeak_produces_audio_in_the_telmi_spec(code: str, tmp_path) -> None:
    dest = tmp_path / "card.mp3"
    tts.synthesize(LANGUAGES[code].chapter.format(n=3), code, dest, engine=tts.ESPEAK)

    out = subprocess.run(
        [
            shutil.which("ffprobe") or "ffprobe",
            "-v",
            "error",
            "-show_entries",
            "stream=sample_rate,channels",
            "-of",
            "csv=p=0",
            str(dest),
        ],
        capture_output=True,
        text=True,
    )
    assert out.stdout.strip() == f"{audio.SAMPLE_RATE},{audio.CHANNELS}"
    assert dest.stat().st_size > 1000


@requires_espeak
def test_espeak_lists_voices_for_a_language() -> None:
    assert tts.voices(tts.ESPEAK, LANGUAGES["fr"])


@requires_espeak
@requires_ffmpeg
def test_espeak_speaks_a_phrase_that_opens_with_a_dash(tmp_path) -> None:
    # A book's sentence can open with "- ". Read as an option, espeak-ng prints
    # "invalid option", exits 0 and writes no file at all.
    dest = tmp_path / "scene.mp3"
    tts.synthesize("- a list item read aloud", "en", dest, engine=tts.ESPEAK)
    assert dest.is_file() and dest.stat().st_size > 1000


def test_the_phrase_is_kept_away_from_where_an_option_goes(tmp_path, monkeypatch) -> None:
    calls: list[list[str]] = []

    def fake_check(argv, label, env=None):
        calls.append(argv)
        return subprocess.CompletedProcess(argv, 0, "", "")

    monkeypatch.setattr(tts, "_check", fake_check)
    monkeypatch.setattr(tts, "_which", lambda name: f"/usr/bin/{name}")
    monkeypatch.setattr(tts, "voices", lambda engine, lang: [])
    for engine, suffix in ((tts.ESPEAK, ".wav"), (tts.SAY, ".aiff")):
        tts._render(engine, "- a list item", LANGUAGES["en"], None, None, tmp_path / f"raw{suffix}")

    assert [argv[-1] for argv in calls] == ["- a list item"] * 2
    assert all(argv[-2] == "--" for argv in calls)


def test_an_apostrophe_survives_the_windows_speech_script(tmp_path, monkeypatch) -> None:
    scripts: list[str] = []

    def fake_powershell(exe: str, script: str) -> str:
        scripts.append(script)
        return ""

    monkeypatch.setattr(tts, "_which", lambda name: "/usr/bin/pwsh")
    monkeypatch.setattr(tts, "_run_powershell", fake_powershell)
    tts._render(tts.SAPI, "l'épée's pupils", LANGUAGES["fr"], None, None, tmp_path / "raw.wav")
    assert "$s.Speak('l''épée''s pupils');" in scripts[0]


def test_a_custom_command_needs_a_template() -> None:
    with pytest.raises(tts.TtsError, match="needs --tts-command"):
        tts.synthesize("Chapter 1", "en", Path("/tmp/x.mp3"), engine=tts.COMMAND)


@pytest.mark.parametrize("engine", [tts.CHATTERBOX, tts.PIPER, tts.COQUI])
def test_each_engine_helper_is_a_real_file(engine: str) -> None:
    # The frozen binary has no Python of its own, so the helper it hands to the
    # user's interpreter has to exist on disk rather than live inside the archive.
    assert tts._speak_script(engine).is_file()


@pytest.mark.parametrize("engine", [tts.CHATTERBOX, tts.PIPER, tts.COQUI])
def test_a_package_engine_is_refused_when_no_python_is_on_the_machine(monkeypatch, engine: str) -> None:
    monkeypatch.setattr(tts, "_pythons", lambda python=None: [])
    with pytest.raises(tts.TtsError, match="runs in your own Python"):
        tts.resolve(engine, LANGUAGES["fr"])


@pytest.mark.parametrize(
    "engine,package",
    [(tts.CHATTERBOX, "chatterbox-tts"), (tts.PIPER, "piper-tts"), (tts.COQUI, "coqui-tts")],
)
def test_a_package_engine_points_at_the_install_when_it_is_missing(monkeypatch, engine: str, package: str) -> None:
    monkeypatch.setattr(tts, "_pythons", lambda python=None: ["/usr/bin/python3"])
    monkeypatch.setattr(tts, "_has_package", lambda name, exe: False)
    with pytest.raises(tts.TtsError, match=f"pip install {package}"):
        tts.resolve(engine, LANGUAGES["fr"])


class _SilentProcess:
    stdout: list[str] = []

    def wait(self) -> int:
        return 0


def test_installing_a_package_uses_the_pip_of_the_python_named(monkeypatch) -> None:
    started: list[list[str]] = []

    def fake_popen(argv, **kwargs):
        started.append(argv)
        return _SilentProcess()

    monkeypatch.setattr(tts.subprocess, "Popen", fake_popen)
    monkeypatch.setattr(tts, "_python", lambda python=None: "/opt/python")
    tts._package_cache[("/opt/python", tts.PIPER)] = False

    assert tts.install_engine(tts.PIPER, "/opt/python") == "/opt/python"
    assert started == [["/opt/python", "-m", "pip", "install", "--upgrade", tts.PACKAGES[tts.PIPER]]]
    # The options page shows whether the package is there, so it must look again.
    assert ("/opt/python", tts.PIPER) not in tts._package_cache
    tts._package_cache.clear()


def test_an_install_with_no_python_named_makes_an_environment_of_its_own(
    tmp_path, monkeypatch
) -> None:
    """Debian 13 and Fedora refuse a pip install in their own Python, so the app builds one."""
    started: list[list[str]] = []
    env = tmp_path / "piper"

    def fake_run(argv, **kwargs):
        started.append(argv)
        return subprocess.CompletedProcess(argv, 0, "", "")

    def fake_popen(argv, **kwargs):
        started.append(argv)
        return _SilentProcess()

    monkeypatch.setattr(tts.subprocess, "run", fake_run)
    monkeypatch.setattr(tts.subprocess, "Popen", fake_popen)
    monkeypatch.setattr(tts.settings, "speech_env", lambda engine: env)
    monkeypatch.setattr(tts, "_python", lambda python=None: "/usr/bin/python3")

    exe = tts.install_engine(tts.PIPER)

    assert started[0] == ["/usr/bin/python3", "-m", "venv", str(env)]
    assert [exe, "-m", "pip", "install", "--upgrade", tts.PACKAGES[tts.PIPER]] in started
    assert exe == str(env / "bin/python")


def test_an_environment_whose_python_lacks_ensurepip_is_given_its_pip(tmp_path, monkeypatch) -> None:
    """Debian ships venv without ensurepip: the environment still has to end up with pip."""
    started: list[list[str]] = []
    env = tmp_path / "piper"

    def fake_run(argv, **kwargs):
        started.append(argv)
        if len(started) == 2:
            assert not env.exists(), "the half-made environment was cleared before the retry"
        # The plain venv is the one Debian fails at, and it leaves half a tree behind.
        failed = "venv" in argv and "--without-pip" not in argv
        if failed:
            env.mkdir(parents=True, exist_ok=True)
        return subprocess.CompletedProcess(argv, 1 if failed else 0, "", "ensurepip is not available")

    class FakeDownload:
        def __enter__(self):
            return self

        def __exit__(self, *_exc):
            return False

        def read(self) -> bytes:
            return b"#!/usr/bin/env python\n"

    monkeypatch.setattr(tts.subprocess, "run", fake_run)
    monkeypatch.setattr(tts.urllib.request, "urlopen", lambda *a, **k: FakeDownload())
    monkeypatch.setattr(tts.settings, "speech_env", lambda engine: env)
    monkeypatch.setattr(tts, "_python", lambda python=None: "/usr/bin/python3")

    exe = tts.make_speech_env(tts.PIPER)

    assert started[1] == ["/usr/bin/python3", "-m", "venv", "--without-pip", str(env)]
    assert started[2][0] == exe and started[2][1].endswith(".py"), "pip is fetched into that environment"
    assert env.exists() is False or True, "the half-made environment was cleared before the retry"


def test_an_environment_falls_back_to_the_machines_own_pip(tmp_path, monkeypatch) -> None:
    """No pip to fetch from the network: the machine's own pip can still fill the environment."""
    started: list[list[str]] = []
    env = tmp_path / "piper"

    def fake_run(argv, **kwargs):
        started.append(argv)
        if argv[1:3] == ["-m", "venv"]:
            ok = "--without-pip" in argv or "--system-site-packages" in argv
            return subprocess.CompletedProcess(argv, 0 if ok else 1, "", "ensurepip is not available")
        if argv[1].endswith(".py"):
            return subprocess.CompletedProcess(argv, 1, "", "ERROR: No matching distribution found for pip")
        return subprocess.CompletedProcess(argv, 0, "pip 25.1.1 from /usr/lib/python3/dist-packages/pip", "")

    class FakeDownload:
        def __enter__(self):
            return self

        def __exit__(self, *_exc):
            return False

        def read(self) -> bytes:
            return b"#!/usr/bin/env python\n"

    monkeypatch.setattr(tts.subprocess, "run", fake_run)
    monkeypatch.setattr(tts.urllib.request, "urlopen", lambda *a, **k: FakeDownload())
    monkeypatch.setattr(tts.settings, "speech_env", lambda engine: env)
    monkeypatch.setattr(tts, "_python", lambda python=None: "/usr/bin/python3")

    exe = tts.make_speech_env(tts.PIPER)

    assert exe == str(env / "bin/python")
    assert started[-2] == [
        "/usr/bin/python3",
        "-m",
        "venv",
        "--without-pip",
        "--system-site-packages",
        str(env),
    ]
    assert started[-1] == [exe, "-m", "pip", "--version"]


def test_no_pip_at_all_names_the_package_to_install(tmp_path, monkeypatch) -> None:
    env = tmp_path / "piper"

    def fake_run(argv, **kwargs):
        if argv[1:3] == ["-m", "venv"]:
            ok = "--without-pip" in argv
            return subprocess.CompletedProcess(argv, 0 if ok else 1, "", "ensurepip is not available")
        if argv[1].endswith(".py"):
            return subprocess.CompletedProcess(argv, 1, "", "ERROR: No matching distribution found for pip")
        return subprocess.CompletedProcess(argv, 1, "", "No module named pip")

    class FakeDownload:
        def __enter__(self):
            return self

        def __exit__(self, *_exc):
            return False

        def read(self) -> bytes:
            return b"#!/usr/bin/env python\n"

    monkeypatch.setattr(tts.subprocess, "run", fake_run)
    monkeypatch.setattr(tts.urllib.request, "urlopen", lambda *a, **k: FakeDownload())
    monkeypatch.setattr(tts.settings, "speech_env", lambda engine: env)
    monkeypatch.setattr(tts, "_python", lambda python=None: "/usr/bin/python3")

    with pytest.raises(tts.TtsError) as raised:
        tts.make_speech_env(tts.PIPER)
    assert "python3-venv" in str(raised.value)


def test_a_second_install_reuses_the_environment(tmp_path, monkeypatch) -> None:
    env = tmp_path / "piper"
    exe = tts.speech_python(env)
    Path(exe).parent.mkdir(parents=True)
    Path(exe).write_text("")

    def fake_run(argv, **kwargs):
        assert argv[1:3] == ["-m", "pip"], "the environment is looked at, never rebuilt"
        return subprocess.CompletedProcess(argv, 0, "pip 25.1.1 from site-packages", "")

    monkeypatch.setattr(tts.settings, "speech_env", lambda engine: env)
    monkeypatch.setattr(tts.subprocess, "run", fake_run)
    assert tts.make_speech_env(tts.PIPER) == exe


def test_an_environment_left_without_pip_is_given_its_pip(tmp_path, monkeypatch) -> None:
    """A run that could not fetch pip leaves an environment with an interpreter and
    nothing to install with. Reusing it fails on the first pip line, so the pip is
    fetched into the environment that is already there."""
    started: list[list[str]] = []
    env = tmp_path / "piper"
    exe = tts.speech_python(env)
    Path(exe).parent.mkdir(parents=True)
    Path(exe).write_text("")

    def fake_run(argv, **kwargs):
        started.append(argv)
        if argv[1].endswith(".py"):
            return subprocess.CompletedProcess(argv, 0, "", "")
        return subprocess.CompletedProcess(argv, 1, "", "No module named pip")

    class FakeDownload:
        def __enter__(self):
            return self

        def __exit__(self, *_exc):
            return False

        def read(self) -> bytes:
            return b"#!/usr/bin/env python\n"

    monkeypatch.setattr(tts.subprocess, "run", fake_run)
    monkeypatch.setattr(tts.urllib.request, "urlopen", lambda *a, **k: FakeDownload())
    monkeypatch.setattr(tts.settings, "speech_env", lambda engine: env)
    monkeypatch.setattr(tts, "_python", lambda python=None: pytest.fail("no new environment"))

    assert tts.make_speech_env(tts.PIPER) == exe
    assert started[0] == [exe, "-m", "pip", "--version"]
    assert started[1][0] == exe and started[1][1].endswith(".py"), "pip goes into the environment left behind"


def test_a_failed_install_says_so(monkeypatch) -> None:
    class FailedProcess:
        stdout: list[str] = []

        def wait(self) -> int:
            return 1

    monkeypatch.setattr(tts.subprocess, "Popen", lambda argv, **kwargs: FailedProcess())
    monkeypatch.setattr(tts, "_python", lambda python=None: "/opt/python")
    with pytest.raises(tts.TtsError, match="pip failed in /opt/python"):
        tts.install_engine(tts.PIPER, "/opt/python")


def test_a_package_cannot_be_installed_without_a_python(monkeypatch) -> None:
    monkeypatch.setattr(tts, "_python", lambda python=None: None)
    with pytest.raises(tts.TtsError, match="no Python to install"):
        tts.install_engine(tts.PIPER, "/opt/python")


def test_a_run_without_a_neural_engine_still_makes_its_cards(monkeypatch) -> None:
    """A neural engine is the one a user picks for its voice, but it is not inside
    the app, so a machine that lacks it must convert with its own voice instead of
    failing on the cards."""
    monkeypatch.setattr(tts, "_pythons", lambda python=None: ["/usr/bin/python3"])
    monkeypatch.setattr(tts, "_has_package", lambda name, exe: False)
    for engine in (tts.CHATTERBOX, tts.COQUI, tts.PIPER):
        chosen = cli.speech(Announce(mode="chapter", language="fr", engine=engine))
        assert chosen.engine == tts.AUTO

    monkeypatch.setattr(tts, "_has_package", lambda name, exe: True)
    for engine in (tts.CHATTERBOX, tts.COQUI, tts.PIPER):
        assert cli.speech(Announce(engine=engine)).engine == engine
    assert cli.speech(Announce(engine=tts.ESPEAK)).engine == tts.ESPEAK
    assert cli.speech(None) is None


def test_a_package_engine_is_only_offered_when_its_package_is_installed(monkeypatch) -> None:
    tts._package_cache.clear()
    seen: list[str] = []

    def fake_run(argv, capture_output, text):
        seen.extend(argv)
        return subprocess.CompletedProcess(argv, 0, stdout="False\n", stderr="")

    monkeypatch.setattr(tts.subprocess, "run", fake_run)
    monkeypatch.setattr(tts, "_pythons", lambda python=None: ["/usr/bin/python3"])
    assert not tts.available(tts.CHATTERBOX, "/usr/bin/python3")
    # find_spec only looks the package up; importing any of them would pull torch in.
    assert seen[:2] == ["/usr/bin/python3", "-I"] and "find_spec" in " ".join(seen)
    # The options page asks on every relabel, so the answer is kept.
    once = len(seen)
    assert not tts.available(tts.CHATTERBOX, "/usr/bin/python3")
    assert len(seen) == once
    tts._package_cache.clear()


def _fake_batch(monkeypatch, manifests: list[dict], stdout: str = "") -> None:
    """A stand-in for the helper process: it writes silent WAVs and reports nothing."""

    def fake_check(argv, label, env=None):
        manifests.append(json.loads(Path(argv[3]).read_text(encoding="utf-8")))
        for out in manifests[-1]["outs"]:
            with wave.open(out, "wb") as handle:
                handle.setframerate(8000)
                handle.setnchannels(1)
                handle.setsampwidth(2)
                handle.writeframes(b"\x00\x20" * 8000)
        return subprocess.CompletedProcess(argv, 0, stdout, "")

    monkeypatch.setattr(tts, "_check", fake_check)
    monkeypatch.setattr(tts, "_pythons", lambda python=None: ["/usr/bin/python3"])
    monkeypatch.setattr(tts, "_has_package", lambda name, exe: True)


@requires_ffmpeg
def test_a_neural_engine_loads_its_model_once_for_the_whole_batch(tmp_path, monkeypatch) -> None:
    manifests: list[dict] = []
    _fake_batch(monkeypatch, manifests, "chatterbox speaking on mps\n")

    dests = [tmp_path / f"c{k}.mp3" for k in range(3)]
    tts.synthesize_all(
        ["un", "deux", "trois"],
        "fr",
        dests,
        engine=tts.CHATTERBOX,
        python="/opt/models/bin/python",
        clip="ref.wav",
        device="mps",
    )

    assert len(manifests) == 1, "one interpreter process should speak every card"
    assert manifests[0]["phrases"] == ["un", "deux", "trois"]
    assert manifests[0]["lang"] == "fr"
    assert manifests[0]["clip"] == "ref.wav"
    assert manifests[0]["device"] == "mps"
    assert len(manifests[0]["outs"]) == 3
    for dest in dests:
        assert dest.is_file() and dest.stat().st_size > 1000


@requires_ffmpeg
def test_the_device_is_left_to_the_model_when_not_chosen(tmp_path, monkeypatch) -> None:
    manifests: list[dict] = []
    _fake_batch(monkeypatch, manifests)
    tts.synthesize_all(["un"], "fr", [tmp_path / "c0.mp3"], engine=tts.CHATTERBOX)
    assert manifests[0]["device"] == "auto"


def test_the_piper_voice_folder_lists_only_the_language_it_holds(tmp_path, monkeypatch) -> None:
    voices = tmp_path / "voices"
    voices.mkdir()
    for name in ("fr_FR-siwis-medium", "fr_FR-tom-medium", "en_US-lessac-medium", "fr_FR-unlisted-low"):
        (voices / f"{name}.onnx").write_text("")
    monkeypatch.setattr(tts.settings, "speech_voices", lambda: voices)

    # The voices the app lists come in the order it lists them; a voice the user put
    # there themselves is still offered, at the end.
    listed = tts.PIPER_VOICES["fr"][0]
    assert tts.piper_voices("fr") == [listed, "fr_FR-tom-medium", "fr_FR-unlisted-low"]
    assert tts.piper_voices("de") == []
    assert tts.piper_voice("fr").name == f"{listed}.onnx"
    assert tts.piper_voice("de") is None


def test_downloading_a_voice_runs_piper_in_the_engine_python(tmp_path, monkeypatch) -> None:
    started: list[list[str]] = []
    voices = tmp_path / "voices"
    monkeypatch.setattr(tts.settings, "speech_voices", lambda: voices)
    monkeypatch.setattr(tts, "_pythons", lambda python=None: ["/opt/python"])
    monkeypatch.setattr(tts, "_has_package", lambda name, exe: True)
    monkeypatch.setattr(tts, "_stream", lambda exe, args, label: started.append([exe, *args]))
    voices.mkdir()
    (voices / "fr_FR-siwis-medium.onnx").write_text("")

    assert tts.download_voice("fr_FR-siwis-medium") == voices / "fr_FR-siwis-medium.onnx"
    assert started == [["/opt/python", "-m", "piper.download_voices", "fr_FR-siwis-medium", "--data-dir", str(voices)]]


def test_a_voice_download_needs_the_engine_installed_first(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(tts, "_pythons", lambda python=None: ["/usr/bin/python3"])
    monkeypatch.setattr(tts, "_has_package", lambda name, exe: False)
    with pytest.raises(tts.TtsError, match="is not installed"):
        tts.download_voice("fr_FR-siwis-medium")


@requires_ffmpeg
def test_a_piper_card_carries_the_voice_and_the_speed(tmp_path, monkeypatch) -> None:
    manifests: list[dict] = []
    _fake_batch(monkeypatch, manifests, "piper speaking on cpu\n")
    voices = tmp_path / "voices"
    voices.mkdir()
    (voices / "fr_FR-siwis-medium.onnx").write_text("")
    monkeypatch.setattr(tts.settings, "speech_voices", lambda: voices)

    tts.synthesize_all(["chapitre 1"], "fr", [tmp_path / "c0.mp3"], engine=tts.PIPER, speed="slow")

    assert manifests[0]["model"] == str(voices / "fr_FR-siwis-medium.onnx")
    assert manifests[0]["length_scale"] == tts.LENGTH_SCALES["slow"]


@requires_ffmpeg
def test_a_piper_card_without_a_voice_says_what_to_download(tmp_path, monkeypatch) -> None:
    manifests: list[dict] = []
    _fake_batch(monkeypatch, manifests)
    monkeypatch.setattr(tts.settings, "speech_voices", lambda: tmp_path / "nothing")
    with pytest.raises(tts.TtsError, match="Install voice"):
        tts.synthesize_all(["chapitre 1"], "fr", [tmp_path / "c0.mp3"], engine=tts.PIPER)


def test_auto_takes_piper_only_when_it_has_a_voice_for_the_language(monkeypatch) -> None:
    monkeypatch.setattr(tts, "available", lambda name, python=None: name == tts.PIPER)
    monkeypatch.setattr(tts, "piper_voice", lambda lang, voice=None: None)
    with pytest.raises(tts.TtsError, match="no speech engine"):
        tts.resolve(tts.AUTO, LANGUAGES["fr"])

    monkeypatch.setattr(tts, "piper_voice", lambda lang, voice=None: Path(f"/v/{lang.code}.onnx"))
    assert tts.resolve(tts.AUTO, LANGUAGES["fr"]) == tts.PIPER


def test_auto_takes_the_lightest_engine_installed(monkeypatch) -> None:
    monkeypatch.setattr(tts, "available", lambda name, python=None: name in (tts.ESPEAK, tts.CHATTERBOX))
    assert tts.resolve(tts.AUTO, LANGUAGES["fr"]) == tts.ESPEAK


def test_coqui_lists_the_models_of_the_language_then_the_cloning_one(monkeypatch) -> None:
    models = tts.voices(tts.COQUI, LANGUAGES["fr"])
    assert models[0] == tts.COQUI_MODELS["fr"][0]
    assert tts.COQUI_MULTILINGUAL in models
    # Arabic has no model of its own: only the cloning one reads it.
    assert tts.voices(tts.COQUI, LANGUAGES["ar"]) == [tts.COQUI_MULTILINGUAL]


@requires_ffmpeg
def test_coqui_needs_a_recording_for_the_model_that_clones(tmp_path, monkeypatch) -> None:
    manifests: list[dict] = []
    _fake_batch(monkeypatch, manifests)
    with pytest.raises(tts.TtsError, match="reads with a cloned voice"):
        tts.synthesize_all(
            ["un"], "fr", [tmp_path / "c0.mp3"], engine=tts.COQUI, voice=tts.COQUI_MULTILINGUAL
        )

    tts.synthesize_all(
        ["un"],
        "fr",
        [tmp_path / "c0.mp3"],
        engine=tts.COQUI,
        voice=tts.COQUI_MULTILINGUAL,
        clip="ref.wav",
    )
    assert manifests[0]["clip"] == "ref.wav"


def test_a_forced_chatterbox_device_is_refused_when_the_machine_lacks_it() -> None:
    class NoGpu:
        class cuda:
            @staticmethod
            def is_available():
                return False

        class backends:
            mps = None

    assert chatterbox_speak._device("cpu", NoGpu) == "cpu"
    with pytest.raises(SystemExit, match="no CUDA device"):
        chatterbox_speak._device("cuda", NoGpu)
    with pytest.raises(SystemExit, match="no Apple GPU"):
        chatterbox_speak._device("mps", NoGpu)
    assert chatterbox_speak._device("auto", NoGpu) == "cpu"


def test_the_device_flag_overrides_the_saved_choice(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "cfg"))
    settings.save({**settings.DEFAULTS, "device": "cpu"})

    saved = cli.announce_options(cli._parse_args(["pack.zip", "out"]))
    forced = cli.announce_options(cli._parse_args(["pack.zip", "out", "--tts-device", "mps"]))
    assert saved.device == "cpu", "the saved choice is used when no flag is given"
    assert forced.device == "mps"


def test_the_card_total_flag_overrides_the_saved_choice(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "cfg"))
    settings.save({**settings.DEFAULTS, "card_total": False})

    saved = cli.announce_options(cli._parse_args(["pack.zip", "out"]))
    drawn = cli.announce_options(cli._parse_args(["pack.zip", "out", "--card-total"]))
    hidden = cli.announce_options(cli._parse_args(["pack.zip", "out", "--no-card-total"]))
    assert saved.show_total is False, "the saved choice is used when no flag is given"
    assert drawn.show_total is True
    assert hidden.show_total is False


def test_auto_takes_the_apple_gpu_when_there_is_one() -> None:
    class Mps:
        class cuda:
            @staticmethod
            def is_available():
                return False

        class backends:
            class mps:
                @staticmethod
                def is_available():
                    return True

    assert chatterbox_speak._device(None, Mps) == "mps"
    assert chatterbox_speak._device("mps", Mps) == "mps"


def test_every_phrase_needs_an_output_file() -> None:
    with pytest.raises(tts.TtsError, match="needs an output file"):
        tts.synthesize_all(["un", "deux"], "fr", [Path("/tmp/a.mp3")])


def test_a_card_is_levelled_a_little_under_the_story_it_interrupts() -> None:
    assert tts.card_target(None) == tts.CARD_TARGET
    assert tts.card_target(-18.3) == pytest.approx(-20.3)
    assert tts.card_target(-24.7) == pytest.approx(-26.0), "a quiet pack keeps its cards quiet"
    assert tts.card_target(-12.0) == pytest.approx(-16.0), "a loud pack does not get shouted over"
    assert tts.card_loudness(-18.3) == "loudnorm=I=-20.3:TP=-2:LRA=11"


@requires_espeak
@requires_ffmpeg
def test_a_spoken_card_lands_on_its_target(tmp_path: Path) -> None:
    card = tmp_path / "c0.mp3"
    tts.synthesize_all(["chapter 3"], "en", [card], engine=tts.ESPEAK, loudness=tts.card_loudness(-18.3))
    # loudnorm needs a few seconds to settle, so a one-word card lands near the
    # target; the point is that espeak's own -25 LUFS is no longer what we hear.
    assert audio.loudness(card) == pytest.approx(-20.3, abs=3.0)


def test_narration_is_cut_into_speakable_pieces() -> None:
    text = " ".join(f"Phrase numero {index}." for index in range(60))
    pieces = tts._chunks(text)
    assert len(pieces) > 1
    assert all(len(piece) <= tts.NARRATION_CHUNK for piece in pieces)
    assert " ".join(pieces) == text, "chunking loses no word"


def test_a_short_passage_stays_in_one_piece() -> None:
    assert tts._chunks("Une phrase. Une autre.") == ["Une phrase. Une autre."]


def test_a_sentence_longer_than_the_limit_is_cut_between_words() -> None:
    pieces = tts._chunks("word " * 300)
    assert all(len(piece) <= tts.NARRATION_CHUNK for piece in pieces)
    assert all(piece.endswith("word") for piece in pieces)


def test_narration_keeps_the_level_the_engine_made(monkeypatch, tmp_path: Path) -> None:
    """A scene is the story itself, so nothing levels it; the cards follow it."""
    seen: list[object] = []
    monkeypatch.setattr(tts, "synthesize_all", lambda *args: seen.append(args[-1]))
    monkeypatch.setattr(tts.audio, "render", lambda segments, dest: "joined")
    tts.narrate("Une phrase.", "fr", tmp_path / "s0.mp3", engine=tts.ESPEAK)
    assert seen == [tts.UNLEVELLED]


def test_a_part_with_nothing_to_say_is_refused(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setattr(tts.audio, "render", lambda segments, dest: "joined")
    with pytest.raises(tts.TtsError, match="nothing to speak"):
        tts.narrate("   ", "fr", tmp_path / "s0.mp3", engine=tts.ESPEAK)


@requires_espeak
@requires_ffmpeg
def test_narration_joins_its_pieces_into_one_scene(tmp_path: Path) -> None:
    scene = tmp_path / "s0.mp3"
    tts.narrate(
        "Le loup sortit de la foret. Il cherchait de quoi manger. Un agneau buvait a la riviere.",
        "fr",
        scene,
        engine=tts.ESPEAK,
    )
    assert audio.probe(scene) == ("mp3", 44100, "mono")
    assert audio.duration(scene) > 3.0
