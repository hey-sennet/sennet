"""Stand-in engines for tests: deterministic, instant, and needing no models."""
from __future__ import annotations

import numpy as np

from .base import RATE, Word


class FakeVAD:
    """Speech wherever the audio is louder than a whisper."""

    def stream(self) -> FakeVADStream:
        return FakeVADStream()


class FakeVADStream:
    chunk = 512

    def __call__(self, chunk: np.ndarray) -> float:
        return 1.0 if float(np.sqrt(np.mean(chunk ** 2))) > 0.01 else 0.0


class FakeRecognizer:
    """Hears `transcript`, one word per `word_s` seconds of audio."""

    def __init__(self, transcript: str = "run the tests in backend", word_s: float = 0.25):
        self.transcript = transcript
        self.word_s = word_s
        self.calls: list[str] = []

    def words(self, audio: np.ndarray) -> list[Word]:
        self.calls.append("words")
        heard = self.transcript.split()[:int(len(audio) / RATE / self.word_s)]
        return [Word(w, i * self.word_s, (i + 1) * self.word_s) for i, w in enumerate(heard)]

    def text(self, audio: np.ndarray) -> str:
        self.calls.append("text")
        return self.transcript if len(audio) else ""


class FakeSynthesizer:
    """A quiet 440 Hz tone, 100 ms per word."""

    rate = 24000

    def __init__(self):
        self.spoken: list[str] = []

    def synthesize(self, text: str) -> np.ndarray:
        self.spoken.append(text)
        t = np.arange(int(self.rate * 0.1 * max(len(text.split()), 1))) / self.rate
        return (0.1 * np.sin(2 * np.pi * 440 * t)).astype(np.float32)
