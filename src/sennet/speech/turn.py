"""smart-turn v3.2: the probability that the speaker has finished their turn.

The fp32 build runs on CPU on every tier. The int8 build moved by up to 0.14 on input noise
of 1e-5, and fp32 costs about 25 ms on four threads, so it needs no GPU
(spikes/sessions). Its input is Whisper's log-mel features, computed here in numpy;
spikes/sessions/check_features.py checks them against `transformers` to 2e-6 on the score.
"""
from __future__ import annotations

from pathlib import Path

import numpy as np

from .base import RATE
from .runtime import ort, session_options

N_FFT = 400
HOP = 160
MELS = 80
SECONDS = 8


def _hz_to_mel(hz):
    # Slaney scale: linear below 1 kHz, logarithmic above.
    hz = np.asarray(hz, dtype=np.float64)
    return np.where(hz >= 1000.0, 15.0 + np.log(np.maximum(hz, 1e-10) / 1000.0) * (27.0 / np.log(6.4)), 3.0 * hz / 200.0)


def _mel_to_hz(mel):
    mel = np.asarray(mel, dtype=np.float64)
    return np.where(mel >= 15.0, 1000.0 * np.exp(np.log(6.4) / 27.0 * (mel - 15.0)), 200.0 * mel / 3.0)


def _mel_filters() -> np.ndarray:
    """(201, 80) Slaney-normalised triangular filters from 0 to 8 kHz."""
    bins = np.linspace(0, RATE // 2, N_FFT // 2 + 1)
    edges = _mel_to_hz(np.linspace(_hz_to_mel(0.0), _hz_to_mel(RATE / 2), MELS + 2))
    widths = np.diff(edges)
    slopes = edges[None, :] - bins[:, None]
    filters = np.maximum(0, np.minimum(-slopes[:, :-2] / widths[:-1], slopes[:, 2:] / widths[1:]))
    return filters * (2.0 / (edges[2:MELS + 2] - edges[:MELS]))[None, :]


FILTERS = _mel_filters()
WINDOW = 0.5 - 0.5 * np.cos(2 * np.pi * np.arange(N_FFT) / N_FFT)  # periodic Hann


def features(audio: np.ndarray) -> np.ndarray:
    """(1, 80, 800) input: the last 8 s, zero-padded at the start so speech sits at the end."""
    n = SECONDS * RATE
    audio = np.asarray(audio, dtype=np.float64)[-n:]
    if len(audio) < n:
        audio = np.pad(audio, (n - len(audio), 0))
    audio = (audio - audio.mean()) / np.sqrt(audio.var() + 1e-7)
    padded = np.pad(audio, N_FFT // 2, mode="reflect")
    frames = 1 + (len(padded) - N_FFT) // HOP
    index = np.arange(N_FFT)[None, :] + HOP * np.arange(frames)[:, None]
    power = np.abs(np.fft.rfft(padded[index] * WINDOW, n=N_FFT)) ** 2
    log_spec = np.log10(np.maximum(power @ FILTERS, 1e-10)).T[:, :-1]
    log_spec = np.maximum(log_spec, log_spec.max() - 8.0)
    return ((log_spec + 4.0) / 4.0)[None].astype(np.float32)


class SmartTurn:
    def __init__(self, directory: Path, threads: int | None):
        options = session_options(threads)
        options.execution_mode = ort().ExecutionMode.ORT_SEQUENTIAL
        self.session = ort().InferenceSession(str(directory / "smart-turn.onnx"), options,
                                              providers=["CPUExecutionProvider"])

    def __call__(self, audio: np.ndarray) -> float:
        return float(self.session.run(None, {"input_features": features(audio)})[0][0, 0])
