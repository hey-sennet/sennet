"""Time single Parakeet decodes by window length for each device/quantization."""
import sys
import time
from pathlib import Path

import numpy as np
import soundfile as sf

from speech import RATE, Parakeet

audio = np.concatenate([sf.read(f, dtype="float32")[0] for f in sorted(Path(".cache/librispeech").glob("*.wav"))[:8]])
configs = [("cpu", "int8", None), ("cpu", "int8", 4), ("cpu", "fp32", None), ("cuda", "fp32", None)]
for device, quantization, threads in configs[int(sys.argv[1]):int(sys.argv[2])] if len(sys.argv) > 2 else configs:
    started = time.perf_counter()
    model = Parakeet(device, None if quantization == "fp32" else "int8", threads)
    load = time.perf_counter() - started
    model.text(audio[:RATE])
    timings = []
    for seconds in [1, 2, 4, 8, 16]:
        runs = []
        for _ in range(3):
            started = time.perf_counter()
            model(audio[:seconds * RATE])
            runs.append(time.perf_counter() - started)
        timings.append(f"{seconds}s={min(runs) * 1000:.0f}ms")
    print(device, quantization, f"threads={threads or 'all'}", f"load={load:.1f}s", " ".join(timings), flush=True)
