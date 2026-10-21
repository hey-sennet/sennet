"""CPU thread sweep for Parakeet decode at short window lengths."""
import time
from pathlib import Path

import numpy as np
import soundfile as sf

from speech import RATE, Parakeet

audio = np.concatenate([sf.read(f, dtype="float32")[0] for f in sorted(Path(".cache/librispeech").glob("*.wav"))[:4]])
for quantization in ["int8", "fp32"]:
    for threads in [2, 4, 6, 8]:
        model = Parakeet("cpu", None if quantization == "fp32" else "int8", threads)
        model.text(audio[:RATE])
        timings = []
        for seconds in [2, 4, 6, 8]:
            runs = []
            for _ in range(4):
                started = time.perf_counter()
                model(audio[:seconds * RATE])
                runs.append(time.perf_counter() - started)
            timings.append(f"{seconds}s={np.median(runs) * 1000:.0f}ms")
        print(quantization, f"threads={threads}", " ".join(timings), flush=True)
