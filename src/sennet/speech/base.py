"""Speech contracts shared by the real engines and the test fakes.

Input audio is 16 kHz mono float32 throughout; synthesized audio is float32 at
the synthesizer's own rate (24 kHz for Kokoro).
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

import numpy as np

RATE = 16000


class AudioBuffer:
    """Audio that grows by appending, without copying the whole utterance each time.

    `view()` returns the samples so far. Appends never change samples already
    written, so a view taken on the event loop is safe to read in a worker thread.
    """

    def __init__(self, capacity: int = RATE * 10):
        self.data = np.zeros(capacity, dtype=np.float32)
        self.length = 0

    def append(self, chunk: np.ndarray):
        end = self.length + len(chunk)
        if end > len(self.data):
            grown = np.zeros(max(end, 2 * len(self.data)), dtype=np.float32)
            grown[:self.length] = self.data[:self.length]
            self.data = grown
        self.data[self.length:end] = chunk
        self.length = end

    def view(self, start: int = 0, end: int | None = None) -> np.ndarray:
        return self.data[start:self.length if end is None else min(end, self.length)]

    def __len__(self) -> int:
        return self.length


@dataclass
class Word:
    text: str
    start: float  # seconds, relative to the decoded audio
    end: float


class Recognizer(Protocol):
    def words(self, audio: np.ndarray) -> list[Word]:
        """Words with timestamps, for live captions."""

    def text(self, audio: np.ndarray) -> str:
        """The plain transcript, for the final pass."""


class Synthesizer(Protocol):
    rate: int

    def synthesize(self, text: str) -> np.ndarray:
        """Audio for one segment of text."""

    def use_voice(self, voice: str):
        """Speak in another voice from now on; raises ValueError for an unknown one."""


class VoiceActivity(Protocol):
    """Per-turn speech detector. Holds stream state, so each turn gets its own."""

    chunk: int  # samples per call

    def __call__(self, chunk: np.ndarray) -> float:
        """Speech probability for one chunk."""


class TurnModel(Protocol):
    def __call__(self, audio: np.ndarray) -> float:
        """Probability that the speaker has finished, from the turn's audio so far."""


class VoiceActivityModel(Protocol):
    def stream(self) -> VoiceActivity:
        """A detector with fresh state, sharing the loaded model."""
