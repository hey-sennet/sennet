"""Kokoro TTS benchmark: time to first audio and real-time factor.

The pipeline synthesizes one sentence at a time, so time to first audio is
the synthesis time of the first sentence of a reply.

    uv run python bench_tts.py --device cpu --quantization int8 --threads 4
"""
import argparse
import json
import time
from pathlib import Path

import numpy as np
import soundfile as sf

from speech import TTS

RESULTS = Path(__file__).parent / "results"

# Short, assistant-style sentences of the kind the front assistant will speak.
SENTENCES = [
    "On it.",
    "Starting that in the backend project now.",
    "The backend task wants to run git push to origin main. Approve?",
    "All forty two tests passed, and two files changed.",
    "The build failed because the configuration file is missing a database URL.",
    "I've updated the login handler to retry expired tokens once before signing the user out.",
    "Three tasks are running: the API refactor, the frontend tests, and the docs update.",
    "Codex finished the migration. It added a new column for the last login time and backfilled it from the audit log.",
]


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--device", default="cpu", choices=["cpu", "cuda"])
    parser.add_argument("--quantization", default="int8", choices=["int8", "fp32"])
    parser.add_argument("--threads", type=int, default=None)
    parser.add_argument("--voice", default="af_heart")
    parser.add_argument("--save", action="store_true", help="write sample WAVs to results/")
    args = parser.parse_args()

    started = time.perf_counter()
    tts = TTS(args.device, None if args.quantization == "fp32" else "int8", args.threads, args.voice)
    load_s = time.perf_counter() - started
    tts("Warm up.")

    rows = []
    for index, text in enumerate(SENTENCES):
        runs = []
        for _ in range(2):
            started = time.perf_counter()
            audio, rate = tts(text)
            runs.append(time.perf_counter() - started)
        seconds = float(np.median(runs))
        rows.append({"chars": len(text), "synth_ms": round(seconds * 1000), "audio_s": round(len(audio) / rate, 2),
                     "rtf": round(seconds / (len(audio) / rate), 3)})
        print(f"{seconds * 1000:6.0f}ms for {len(audio) / rate:4.1f}s audio  {text[:60]}")
        if args.save:
            RESULTS.mkdir(exist_ok=True)
            sf.write(RESULTS / f"tts-{args.device}-{args.quantization}-{index}.wav", audio, rate)

    short = [r["synth_ms"] for r in rows if r["chars"] <= 70]
    summary = {
        "config": vars(args),
        "load_s": round(load_s, 2),
        "first_audio_ms_short_sentence_p50": round(float(np.median(short))),
        "first_audio_ms_max": max(r["synth_ms"] for r in rows),
        "rtf_p50": round(float(np.median([r["rtf"] for r in rows])), 3),
        "rows": rows,
    }
    print(json.dumps({k: v for k, v in summary.items() if k != "rows"}, indent=1))
    RESULTS.mkdir(exist_ok=True)
    tag = f"tts-{args.device}-{args.quantization}-t{args.threads or 'all'}"
    (RESULTS / f"{tag}.json").write_text(json.dumps(summary, indent=1))


if __name__ == "__main__":
    main()
