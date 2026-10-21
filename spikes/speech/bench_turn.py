"""End-of-turn benchmark: Silero VAD + smart-turn v3.2 on LibriSpeech.

Two cases per utterance:
- complete: the utterance followed by silence. Measures endpoint delay, from the
  last word's end to the turn-ended decision (audio time + decision compute time).
- mid-pause: the utterance split at a word boundary near its middle by a pause
  of --pause seconds. Ending inside that pause is a premature cut; ending earlier,
  at a pause already in the read audio, is counted separately.

    uv run python bench_turn.py --device cpu
"""
import argparse
import json
import time
from pathlib import Path

import numpy as np
import soundfile as sf

from speech import RATE, Parakeet, SileroVAD, SmartTurn, TurnDetector

DATA = Path(__file__).parent / ".cache" / "librispeech"
RESULTS = Path(__file__).parent / "results"
CHUNK = 640  # 40 ms, as the client sends it


def silence(seconds: float, rng) -> np.ndarray:
    # Faint noise floor: real microphones are never digitally silent.
    return (rng.standard_normal(int(seconds * RATE)) * 1e-3).astype(np.float32)


def run(detector: TurnDetector, audio: np.ndarray) -> tuple[float | None, float]:
    """Feed audio in client-sized chunks; return (audio time of the decision, decision compute ms)."""
    detector.reset()
    for offset in range(0, len(audio), CHUNK):
        started = time.perf_counter()
        ended = detector.feed(audio[offset:offset + CHUNK])
        compute_ms = (time.perf_counter() - started) * 1000
        if ended:
            return min(offset + CHUNK, len(audio)) / RATE, compute_ms
    return None, 0.0


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--device", default="cpu", choices=["cpu", "cuda"])
    parser.add_argument("--pause", type=float, default=1.0)
    parser.add_argument("--limit", type=int, default=20)
    parser.add_argument("--first-check", type=float, default=0.2)
    parser.add_argument("--max-silence", type=float, default=2.0)
    args = parser.parse_args()

    rng = np.random.default_rng(0)
    stt = Parakeet("cuda" if args.device == "cuda" else "cpu", "int8", 4)  # word timestamps only
    detector = TurnDetector(SileroVAD(), SmartTurn(args.device), first_check=args.first_check,
                            max_silence=args.max_silence)
    detector.turn(np.zeros(RATE, dtype=np.float32))  # warm up

    delays, premature, natural, missed, decisions_ms = [], 0, 0, 0, []
    files = sorted(DATA.glob("*.wav"))[:args.limit]
    for path in files:
        audio = sf.read(path, dtype="float32")[0]
        words = stt(audio)
        if len(words) < 6:
            continue
        speech_end = words[-1].end

        decided, ms = run(detector, np.concatenate([audio, silence(3.0, rng)]))
        if decided is None:
            missed += 1
        else:
            delays.append(decided - speech_end + ms / 1000)
            decisions_ms.append(ms)

        middle = len(words) // 2
        cut = int((words[middle - 1].end + words[middle].start) / 2 * RATE)
        split = np.concatenate([audio[:cut], silence(args.pause, rng), audio[cut:], silence(3.0, rng)])
        decided, _ = run(detector, split)
        if decided is not None and decided < cut / RATE:
            natural += 1  # ended at a pause already in the recording, before the inserted one
        elif decided is not None and decided < cut / RATE + args.pause:
            premature += 1

    vad = SileroVAD()
    block = silence(SileroVAD.CHUNK / RATE, rng)
    started = time.perf_counter()
    for _ in range(500):
        vad(block)
    vad_ms = (time.perf_counter() - started) * 2  # per 32 ms chunk

    tested = len(delays) + missed
    summary = {
        "config": vars(args),
        "utterances": tested,
        "endpoint_delay_ms_p50": round(float(np.percentile(delays, 50)) * 1000),
        "endpoint_delay_ms_p95": round(float(np.percentile(delays, 95)) * 1000),
        "ended_by_max_silence": sum(d >= args.max_silence for d in delays),
        "cut_in_inserted_pause_rate": round(premature / tested, 3),
        "cut_at_natural_pause_rate": round(natural / tested, 3),
        "missed_end": missed,
        "decision_compute_ms_p50": round(float(np.percentile(decisions_ms, 50)), 1),
        "decision_compute_ms_p95": round(float(np.percentile(decisions_ms, 95)), 1),
        "vad_ms_per_32ms_chunk": round(vad_ms, 3),
    }
    print(json.dumps(summary, indent=1))
    RESULTS.mkdir(exist_ok=True)
    (RESULTS / f"turn-{args.device}-p{args.pause:g}.json").write_text(json.dumps(summary, indent=1))


if __name__ == "__main__":
    main()
