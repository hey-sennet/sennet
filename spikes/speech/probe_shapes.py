"""Decode time distribution when every call has a new input length (as in live captioning)."""
import sys
import time
from pathlib import Path

import numpy as np
import soundfile as sf

import speech

device = sys.argv[1] if len(sys.argv) > 1 else "cuda"
audio = np.concatenate([sf.read(f, dtype="float32")[0] for f in sorted(Path(".cache/librispeech").glob("*.wav"))[:4]])
model = speech.Parakeet(device, None if device == "cuda" else "int8", None if device == "cuda" else 4)
model.text(audio[:speech.RATE])
rng = np.random.default_rng(0)
for low, high in [(1, 3), (3, 5), (5, 8)]:
    runs = []
    for seconds in rng.uniform(low, high, 40):
        started = time.perf_counter()
        model(audio[:int(seconds * speech.RATE)])
        runs.append((time.perf_counter() - started) * 1000)
    print(f"{device} {low}-{high}s windows: p50={np.median(runs):.0f}ms p95={np.percentile(runs, 95):.0f}ms", flush=True)
