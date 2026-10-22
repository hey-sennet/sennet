"""Text to speech: Kokoro-82M v1.0, called directly through onnxruntime.

Text becomes phonemes with phonemizer driving espeak-ng (both GPL-3, see NOTICES),
the phonemes become token ids through Kokoro's vocabulary (`kokoro_vocab.json`,
from hexgrad/Kokoro-82M's config.json, Apache-2.0), and the model turns them into
24 kHz audio in the voice's style. Callers pass one clause or sentence at a time.
"""
from __future__ import annotations

import json
import logging
import threading
from pathlib import Path

import numpy as np

from .runtime import ort, providers, session_options

RATE = 24000
MAX_PHONEMES = 510
VOCAB = json.loads((Path(__file__).parent / "kokoro_vocab.json").read_text(encoding="utf-8"))

# With punctuation preserved, phonemizer warns about a word-count mismatch on nearly
# every line; it is harmless for synthesis. Its own logger is reset on each call.
_quiet = logging.getLogger(__name__ + ".phonemizer")
_quiet.setLevel(logging.ERROR)

# espeak-ng keeps process-global state; concurrent calls corrupt each other's phonemes.
_espeak_lock = threading.Lock()
_espeak_ready = False


def phonemize(text: str, language: str = "en-us") -> str:
    global _espeak_ready
    import espeakng_loader
    import phonemizer
    from phonemizer.backend.espeak.wrapper import EspeakWrapper

    with _espeak_lock:
        if not _espeak_ready:
            EspeakWrapper.set_library(espeakng_loader.get_library_path())
            EspeakWrapper.set_data_path(espeakng_loader.get_data_path())
            _espeak_ready = True
        phonemes = phonemizer.phonemize(text.strip(), language, preserve_punctuation=True, with_stress=True,
                                        logger=_quiet)
    # Newlines are not in the vocabulary; without this, lines run together with no pause.
    return " ".join("".join(p for p in phonemes if p in VOCAB).split())


def tokens(phonemes: str) -> list[int]:
    return [VOCAB[p] for p in phonemes if p in VOCAB]


def split(phonemes: str, limit: int = MAX_PHONEMES) -> list[str]:
    """Batches the model can take, cut at punctuation, else at spaces, else anywhere."""
    batches = []
    while len(phonemes) > limit:
        cut = max((phonemes.rfind(mark, 0, limit) for mark in ".!?;:,"), default=-1)
        if cut <= 0:
            cut = phonemes.rfind(" ", 0, limit)
        if cut <= 0:
            cut = limit - 1
        batches.append(phonemes[:cut + 1].strip())
        phonemes = phonemes[cut + 1:].strip()
    return [b for b in batches + [phonemes] if b]


def trim(audio: np.ndarray, threshold: float = 0.01, keep: int = RATE // 100) -> np.ndarray:
    """Drop the near-silence the model leaves at each end, keeping 10 ms."""
    loud = np.flatnonzero(np.abs(audio) > threshold)
    if not len(loud):
        return audio[:0]
    return audio[max(loud[0] - keep, 0):loud[-1] + keep]


class Kokoro:
    rate = RATE

    def __init__(self, directory: Path, device: str, threads: int | None, voice: str = "af_heart", speed: float = 1.0):
        self.session = ort().InferenceSession(str(directory / "kokoro.onnx"), session_options(threads),
                                              providers=providers(device))
        self.voices = np.load(directory / "voices-v1.0.bin")
        self.use_voice(voice)
        self.speed = speed
        names = {i.name for i in self.session.get_inputs()}
        self.tokens_input = "input_ids" if "input_ids" in names else "tokens"

    def use_voice(self, voice: str):
        if voice not in self.voices:
            raise ValueError(f"unknown Kokoro voice {voice!r}; choose one of {', '.join(sorted(self.voices))}")
        self.style = self.voices[voice]  # one style row per token count
        # British voices (bf_, bm_) were trained on British phonemes.
        self.language = "en-gb" if voice.startswith("b") else "en-us"

    def synthesize(self, text: str) -> np.ndarray:
        parts = [self._infer(tokens(batch)) for batch in split(phonemize(text, self.language))]
        parts = [p for p in parts if len(p)]
        return np.concatenate(parts) if parts else np.zeros(0, dtype=np.float32)

    def _infer(self, ids: list[int]) -> np.ndarray:
        if not ids:
            return np.zeros(0, dtype=np.float32)
        audio = self.session.run(None, {
            self.tokens_input: np.array([[0, *ids, 0]], dtype=np.int64),
            "style": self.style[min(len(ids), len(self.style)) - 1].astype(np.float32),
            "speed": np.array([self.speed], dtype=np.float32),
        })[0]
        return trim(np.asarray(audio, dtype=np.float32).ravel())
