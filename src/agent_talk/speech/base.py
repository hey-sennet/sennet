"""Speech contracts shared by the real engines and the test fakes.

Input audio is 16 kHz mono float32 throughout; synthesized audio is float32 at
the synthesizer's own rate (24 kHz for Kokoro).
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

import numpy as np

RATE = 16000


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


class VoiceActivity(Protocol):
    """Per-turn speech detector. Holds stream state, so each turn gets its own."""

    chunk: int  # samples per call

    def __call__(self, chunk: np.ndarray) -> float:
        """Speech probability for one chunk."""


class VoiceActivityModel(Protocol):
    def stream(self) -> VoiceActivity:
        """A detector with fresh state, sharing the loaded model."""
