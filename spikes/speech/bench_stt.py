"""Streaming STT benchmark on LibriSpeech, simulated in real time.

Audio "arrives" at real-time speed on a virtual clock. A tick fires every
--interval seconds, or as soon as the previous decode finishes if it took longer,
which is how the live pipeline adapts on slow hardware.

    uv run python bench_stt.py --device cpu --quantization int8
"""
import argparse
import json
import re
import time
from pathlib import Path

import jiwer
import numpy as np
import psutil
import soundfile as sf

from speech import RATE, Parakeet, StreamingTranscriber

DATA = Path(__file__).parent / ".cache" / "librispeech"
RESULTS = Path(__file__).parent / "results"


def clean(text: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"[^\w' ]", " ", text.lower())).strip()


def load(limit: int, long: int) -> list[tuple[str, np.ndarray, str]]:
    refs = json.loads((DATA / "refs.json").read_text())
    items = [(name, sf.read(DATA / name, dtype="float32")[0], ref) for name, ref in sorted(refs.items())]
    items = items[:limit]
    # Long dictations: join consecutive utterances with 0.6 s pauses to exercise window advancing.
    pause = np.zeros(int(0.6 * RATE), dtype=np.float32)
    for i in range(long):
        group = items[i * 5:(i + 1) * 5]
        audio = np.concatenate([part for _, a, _ in group for part in (a, pause)])
        items.append((f"long-{i}", audio, " ".join(r for _, _, r in group)))
    return items


def stream(stt: Parakeet, audio: np.ndarray, interval: float, window: float):
    transcriber = StreamingTranscriber(stt, max_window=window)
    duration = len(audio) / RATE
    clock = interval
    fed = 0
    ticks = []
    while True:
        available = int(min(clock, duration) * RATE)
        transcriber.add(audio[fed:available])
        fed = available
        caption = transcriber.tick()
        ticks.append((len(transcriber.audio[transcriber.anchor:]) / RATE, caption.decode_ms))
        # The next tick waits for the interval, or for this decode if it overran.
        clock += max(interval, caption.decode_ms / 1000)
        if fed >= len(audio):
            break
    live = clean(f"{caption.committed} {caption.provisional}")
    started = time.perf_counter()
    final = transcriber.finish()
    final_ms = (time.perf_counter() - started) * 1000
    periods = [max(interval * 1000, ms) for _, ms in ticks]
    return live, clean(final), final_ms, ticks, periods


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--device", default="cpu", choices=["cpu", "cuda"])
    parser.add_argument("--quantization", default="int8", choices=["int8", "fp32"])
    parser.add_argument("--threads", type=int, default=None)
    parser.add_argument("--interval", type=float, default=0.25)
    parser.add_argument("--window", type=float, default=8.0)
    parser.add_argument("--limit", type=int, default=8)
    parser.add_argument("--long", type=int, default=1, help="number of ~40 s dictations")
    args = parser.parse_args()

    process = psutil.Process()
    rss_before = process.memory_info().rss
    started = time.perf_counter()
    stt = Parakeet(args.device, None if args.quantization == "fp32" else "int8", args.threads)
    load_s = time.perf_counter() - started
    stt.text(np.zeros(RATE, dtype=np.float32))  # warm up kernels and allocators

    refs, lives, finals, final_ms, decode_ms, periods, rtf = [], [], [], [], [], [], []
    for name, audio, ref in load(args.limit, args.long):
        live, final, ms, ticks, tick_periods = stream(stt, audio, args.interval, args.window)
        refs.append(clean(ref)); lives.append(live); finals.append(final); final_ms.append(ms)
        decode_ms += [d for _, d in ticks]
        periods += tick_periods
        rtf += [d / 1000 / max(w, 0.25) for w, d in ticks]
        print(f"{name:>22} {len(audio) / RATE:5.1f}s  ticks={len(ticks):3d}  "
              f"decode p50={np.median([d for _, d in ticks]):6.0f}ms  final={ms:6.0f}ms")

    pct = lambda xs, p: float(np.percentile(xs, p))
    summary = {
        "config": vars(args),
        "load_s": round(load_s, 2),
        "rss_mb": round((process.memory_info().rss - rss_before) / 2**20),
        "decode_ms_p50": round(pct(decode_ms, 50)), "decode_ms_p95": round(pct(decode_ms, 95)),
        "update_period_ms_p50": round(pct(periods, 50)), "update_period_ms_p95": round(pct(periods, 95)),
        "final_ms_p50": round(pct(final_ms, 50)), "final_ms_p95": round(pct(final_ms, 95)),
        "window_rtf_p50": round(pct(rtf, 50), 3),
        "wer_live": round(jiwer.wer(refs, lives), 4),
        "wer_final": round(jiwer.wer(refs, finals), 4),
    }
    print(json.dumps(summary, indent=1))
    RESULTS.mkdir(exist_ok=True)
    tag = f"stt-{args.device}-{args.quantization}-t{args.threads or 'all'}-w{args.window:g}"
    (RESULTS / f"{tag}.json").write_text(json.dumps(summary, indent=1))


if __name__ == "__main__":
    main()
