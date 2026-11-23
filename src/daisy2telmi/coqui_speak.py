"""Speak a batch of phrases with Coqui TTS, run by a Python the app built for it.

daisy2telmi ships no torch, so it runs this file with an interpreter that has
`coqui-tts`. The model loads once for the whole batch; the weights themselves are
downloaded by Coqui into ~/.TTS the first time a model is used.

Manifest: {"phrases": [...], "outs": [...], "lang": "fr",
           "model": "tts_models/fr/css10/vits", "clip": "ref.wav", "device": "auto"}
"""

from __future__ import annotations

import json
import sys
from pathlib import Path


def _device(requested: str | None, torch) -> str:
    """Which device to run on.

    The CPU is the default here: a model that is not installed yet is downloaded
    and loaded on first use, and a GPU run of the cloning model fails on some
    machines for reasons the app cannot fix. A forced device that is not there
    stops the run rather than falling back quietly.
    """
    if requested and requested != "auto":
        if requested == "cuda" and not torch.cuda.is_available():
            raise SystemExit("no CUDA device in that Python: choose the CPU, or auto")
        if requested == "mps" and not (
            getattr(torch.backends, "mps", None) and torch.backends.mps.is_available()
        ):
            raise SystemExit("no Apple GPU in that Python: choose the CPU, or auto")
        return requested
    return "cpu"


def speak(manifest: dict) -> None:
    import torch
    from TTS.api import TTS

    model = manifest["model"]
    clip = manifest.get("clip")
    device = _device(manifest.get("device"), torch)
    print(f"coqui speaking on {device}")
    tts = TTS(model).to(device)
    # A model of one language already knows which language it reads, and Coqui
    # refuses a `language` for it. Only the multilingual one is told, and it reads
    # with a recording of the voice to clone.
    clone = model.endswith("xtts_v2") or bool(clip)
    for phrase, out in zip(manifest["phrases"], manifest["outs"]):
        if clone:
            tts.tts_to_file(phrase, file_path=out, language=manifest["lang"], speaker_wav=clip)
        else:
            tts.tts_to_file(phrase, file_path=out)


def main(argv: list[str]) -> int:
    manifest = json.loads(Path(argv[1]).read_text(encoding="utf-8"))
    speak(manifest)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
