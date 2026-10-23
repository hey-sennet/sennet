"""Play audio on this computer, for setup's voice previews.

The system's own player is used, so there is no audio dependency: PipeWire,
PulseAudio or ALSA on Linux, afplay on macOS.
"""
from __future__ import annotations

import shutil
import subprocess
import tempfile
import wave
from pathlib import Path

import numpy as np

PLAYERS = (["pw-play"], ["paplay"], ["aplay", "-q"], ["afplay"])


def player() -> list[str] | None:
    return next((command for command in PLAYERS if shutil.which(command[0])), None)


def play(audio: np.ndarray, rate: int, command: list[str]):
    with tempfile.TemporaryDirectory() as directory:
        path = Path(directory) / "preview.wav"
        write_wav(path, audio, rate)
        subprocess.run([*command, str(path)], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                       timeout=60, check=False)


def write_wav(path: Path, audio: np.ndarray, rate: int):
    samples = (np.clip(audio, -1.0, 1.0) * 32767).astype("<i2")
    with wave.open(str(path), "wb") as out:
        out.setnchannels(1)
        out.setsampwidth(2)
        out.setframerate(rate)
        out.writeframes(samples.tobytes())
