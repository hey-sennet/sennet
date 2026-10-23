"""Whisper log-mel features for smart-turn v3.2, in numpy, so the product needn't import
`transformers` (m2-backend, "Smart-turn is left out"). Matches WhisperFeatureExtractor with
chunk_length=8 and do_normalize=True; `check_features.py` compares the two.
"""
from __future__ import annotations

import numpy as np

RATE = 16000
N_FFT = 400
HOP = 160
MELS = 80
SECONDS = 8


def _hz_to_mel(hz):
    # Slaney scale: linear below 1 kHz, logarithmic above.
    hz = np.asarray(hz, dtype=np.float64)
    mel = 3.0 * hz / 200.0
    log = hz >= 1000.0
    return np.where(log, 15.0 + np.log(np.maximum(hz, 1e-10) / 1000.0) * (27.0 / np.log(6.4)), mel)


def _mel_to_hz(mel):
    mel = np.asarray(mel, dtype=np.float64)
    hz = 200.0 * mel / 3.0
    log = mel >= 15.0
    return np.where(log, 1000.0 * np.exp(np.log(6.4) / 27.0 * (mel - 15.0)), hz)


def mel_filters() -> np.ndarray:
    """(201, 80) Slaney-normalised triangular filters from 0 to 8 kHz."""
    bins = np.linspace(0, RATE // 2, N_FFT // 2 + 1)
    edges = _mel_to_hz(np.linspace(_hz_to_mel(0.0), _hz_to_mel(RATE / 2), MELS + 2))
    widths = np.diff(edges)
    slopes = edges[None, :] - bins[:, None]
    down = -slopes[:, :-2] / widths[:-1]
    up = slopes[:, 2:] / widths[1:]
    filters = np.maximum(0, np.minimum(down, up))
    return filters * (2.0 / (edges[2:MELS + 2] - edges[:MELS]))[None, :]


FILTERS = mel_filters()
WINDOW = 0.5 - 0.5 * np.cos(2 * np.pi * np.arange(N_FFT) / N_FFT)  # periodic Hann


def features(audio: np.ndarray) -> np.ndarray:
    """(1, 80, 800) input for smart-turn: the last 8 s, zero-padded at the start."""
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
