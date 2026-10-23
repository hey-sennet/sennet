"""Models and helpers shared by the analysis scripts. Everything runs on CPU, so the GPU
can stay with the running service."""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

import numpy as np
import soundfile as sf

HERE = Path(__file__).parent
sys.path.insert(0, str(HERE.parent / "speech"))
from speech import MODELS, Parakeet, SileroVAD  # noqa: E402

RATE = 16000
FRAME = SileroVAD.CHUNK  # 32 ms
RECORDINGS = HERE / ".cache" / "recordings"


def recordings(kind: str):
    for meta_path in sorted((RECORDINGS / kind).glob("*.json")):
        audio, rate = sf.read(meta_path.with_suffix(".wav"), dtype="float32")
        assert rate == RATE
        yield meta_path.stem, json.loads(meta_path.read_text()), audio


def vad_probabilities(vad: SileroVAD, audio: np.ndarray) -> np.ndarray:
    vad.reset()
    n = len(audio) // FRAME
    return np.array([vad(audio[i * FRAME:(i + 1) * FRAME]) for i in range(n)])


def longest_run(mask: np.ndarray) -> int:
    best = run = 0
    for value in mask:
        run = run + 1 if value else 0
        best = max(best, run)
    return best


def words(text: str) -> list[str]:
    return [w for w in (re.sub(r"[^\w']", "", t.lower()) for t in text.split()) if w]


def browser(user_agent: str) -> str:
    for name, pattern in [("Firefox", "Firefox/"), ("Edge", "Edg/"), ("Chrome", "Chrome/"), ("Safari", "Safari/")]:
        if pattern in user_agent:
            platform = ("Android" if "Android" in user_agent else "iOS" if "iPhone" in user_agent or "iPad" in user_agent
                        else "macOS" if "Mac OS X" in user_agent else "Linux" if "Linux" in user_agent
                        else "Windows" if "Windows" in user_agent else "")
            return f"{name} {platform}".strip()
    return "other"


def db(audio: np.ndarray) -> float:
    return float(20 * np.log10(np.sqrt(np.mean(np.square(audio, dtype=np.float64))) + 1e-9))
