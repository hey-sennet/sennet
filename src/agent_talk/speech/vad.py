"""Silero VAD: speech probability per 32 ms chunk."""
from __future__ import annotations

from pathlib import Path

import numpy as np

from .base import RATE
from .runtime import ort, session_options


class SileroVAD:
    def __init__(self, directory: Path):
        # Tiny model; CPU with one thread is faster than any GPU round trip.
        self.session = ort().InferenceSession(str(directory / "silero_vad.onnx"), session_options(1),
                                              providers=["CPUExecutionProvider"])

    def stream(self) -> SileroStream:
        return SileroStream(self.session)


class SileroStream:
    chunk = 512
    CONTEXT = 64

    def __init__(self, session):
        self.session = session
        self.state = np.zeros((2, 1, 128), dtype=np.float32)
        self.context = np.zeros(self.CONTEXT, dtype=np.float32)

    def __call__(self, chunk: np.ndarray) -> float:
        if len(chunk) != self.chunk:
            raise ValueError(f"Silero takes {self.chunk}-sample chunks")
        x = np.concatenate([self.context, chunk])[None, :].astype(np.float32)
        out, self.state = self.session.run(None, {"input": x, "state": self.state,
                                                  "sr": np.array(RATE, dtype=np.int64)})
        self.context = chunk[-self.CONTEXT:]
        return float(out[0][0])
