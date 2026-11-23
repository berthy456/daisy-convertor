"""Speak a batch of phrases with Piper, run by a Python the app built for it.

daisy2telmi ships no onnxruntime and no voice weights, so it runs this file with an
interpreter that has `piper-tts` and downloads each voice into the app's own folder.
The voice loads once for the whole batch, which matters because a card says one word.

Manifest: {"phrases": [...], "outs": [...], "model": ".../fr_FR-siwis-medium.onnx",
           "length_scale": 1.0, "device": "auto"}
"""

from __future__ import annotations

import json
import sys
import wave
from pathlib import Path


def speak(manifest: dict) -> None:
    from piper import PiperVoice, SynthesisConfig

    # Piper runs on the CPU by default, and onnxruntime-gpu is not part of the
    # package, so CUDA is only asked for when the user named it.
    cuda = manifest.get("device") == "cuda"
    voice = PiperVoice.load(manifest["model"], use_cuda=cuda)
    print(f"piper speaking on {'cuda' if cuda else 'cpu'}")
    config = SynthesisConfig(length_scale=manifest.get("length_scale") or 1.0)
    for phrase, out in zip(manifest["phrases"], manifest["outs"]):
        with wave.open(out, "wb") as handle:
            handle.setframerate(voice.config.sample_rate)
            handle.setnchannels(1)
            handle.setsampwidth(2)
            for chunk in voice.synthesize(phrase, config):
                handle.writeframes(chunk.audio_int16_bytes)


def main(argv: list[str]) -> int:
    manifest = json.loads(Path(argv[1]).read_text(encoding="utf-8"))
    speak(manifest)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
