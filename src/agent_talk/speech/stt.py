"""Speech to text: Parakeet TDT 0.6B v2 through onnx-asr, and live captions."""
from __future__ import annotations

import re
import time
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

from .base import RATE, Recognizer, Word
from .runtime import ort, providers, session_options


class Parakeet:
    def __init__(self, directory: Path, device: str, threads: int | None):
        ort()  # raises RuntimeMissing before onnx-asr fails less clearly
        import onnx_asr

        quantization = "int8" if directory.name.endswith("-int8") else None
        self.model = onnx_asr.load_model("nemo-parakeet-tdt-0.6b-v2", directory, quantization=quantization,
                                         providers=providers(device), sess_options=session_options(threads)
                                         ).with_timestamps()

    def words(self, audio: np.ndarray) -> list[Word]:
        result = self.model.recognize(audio.astype(np.float32), sample_rate=RATE)
        words: list[Word] = []
        for token, start in zip(result.tokens, result.timestamps):
            if token.startswith(" ") or not words:
                words.append(Word(token.strip(), start, start + 0.08))
            else:
                words[-1].text += token
                words[-1].end = start + 0.08
        return [w for w in words if w.text]

    def text(self, audio: np.ndarray) -> str:
        return self.model.recognize(audio.astype(np.float32), sample_rate=RATE).text.strip()


def norm(word: str) -> str:
    return re.sub(r"[^\w']", "", word.lower())


@dataclass
class Caption:
    committed: str
    provisional: str
    decode_ms: float


@dataclass
class StreamingTranscriber:
    """Live captions by re-decoding a sliding window of recent audio.

    LocalAgreement-2: words that two consecutive decodes of the same window agree
    on become committed; the rest are provisional. The window start (`anchor`)
    advances to a committed word boundary once the window exceeds `max_window`,
    so per-tick decode cost stays bounded however long the user talks.
    """

    stt: Recognizer
    max_window: float = 8.0
    audio: np.ndarray = field(default_factory=lambda: np.zeros(0, dtype=np.float32))
    anchor: int = 0                       # sample index where the window starts
    committed: list[str] = field(default_factory=list)
    window_committed: int = 0             # committed words that lie inside the window
    previous: list[Word] | None = None

    def add(self, chunk: np.ndarray):
        self.audio = np.concatenate([self.audio, chunk])

    def tick(self) -> Caption:
        window = self.audio[self.anchor:]
        started = time.perf_counter()
        words = self.stt.words(window) if len(window) >= RATE // 4 else []
        decode_ms = (time.perf_counter() - started) * 1000

        if self.previous is not None:
            agreed = 0
            for a, b in zip(self.previous, words):
                if norm(a.text) != norm(b.text):
                    break
                agreed += 1
            if agreed > self.window_committed:
                self.committed += [w.text for w in words[self.window_committed:agreed]]
                self.window_committed = agreed
        self.previous = words

        # A decode can return fewer words than were committed (e.g. a word merged);
        # advance only when the current words still cover the committed ones.
        if len(window) / RATE > self.max_window and 1 < self.window_committed <= len(words):
            self._advance(words)

        provisional = " ".join(w.text for w in words[self.window_committed:])
        return Caption(" ".join(self.committed), provisional, decode_ms)

    def _advance(self, words: list[Word]):
        # Cut between the last two committed words, at the midpoint of the gap,
        # keeping the last committed word as overlap so its boundary is not decoded twice.
        cut_index = self.window_committed - 1
        cut = (words[cut_index - 1].end + words[cut_index].start) / 2
        self.anchor += int(cut * RATE)
        # The kept word is already committed; drop it from the committed list so the
        # next agreement re-commits it from the new window instead of duplicating it.
        self.committed = self.committed[:-1]
        self.window_committed = 0
        self.previous = None

    def finish(self, full_pass_limit: float = 60.0) -> str:
        """Final transcript: one full decode of the utterance when it fits the model."""
        if len(self.audio) / RATE <= full_pass_limit:
            return self.stt.text(self.audio).strip()
        tail = " ".join(w.text for w in self.stt.words(self.audio[self.anchor:])[self.window_committed:])
        return " ".join(self.committed + [tail]).strip()
