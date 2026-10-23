"""Step 1: replay the turn recordings through end-of-turn settings and count the cut-offs.

    uv run --project ../speech python eval_turn.py

Each recording is one whole turn: every pause before the last speech is mid-turn, and the
end of the last speech is the true end, leaving out the click of Done. The recording is cut
there and extended with 3 s of the room's noise from before the speaker started, so the end
can be decided.

Per pause long enough to check (224 ms, seven VAD frames) the expensive parts run once and
are cached in .cache/turn-pauses.json: smart-turn v3.2 on the audio so far (fp32 and int8
builds), and a Parakeet transcript of it for the completeness check. The settings are then
swept over the cache:

- smart-turn threshold;
- completeness: none, by last word, or by last word and closing punctuation;
- silence fallback, which ends the turn regardless;
- continuation grace: a cut is undone if speech resumes within this long after the
  decision, as the final pass would still be running (m7-sessions, "Continuation").

A recording is cut off if any mid-turn pause ends the turn and speech doesn't resume within
the grace. Delay is from the end of the last speech to the decision.
"""
from __future__ import annotations

import itertools
import json
import time
from collections import defaultdict

import numpy as np
import onnxruntime as ort

from common import (FRAME, HERE, MODELS, RATE, Parakeet, SileroVAD, recordings, vad_probabilities, words)
from features import features

SPEECH = 0.5
MIN_SPEECH_S = 0.1
CHECK_FRAMES = 7          # 224 ms of silence before smart-turn runs (HLD §6.2: about 200 ms)
CHECK_S = CHECK_FRAMES * FRAME / RATE
PRE_ROLL_S = 0.3
EXTEND_S = 3.0
CACHE = HERE / ".cache" / "turn-pauses.json"

# A transcript ending in one of these is mid-thought (m7-sessions, "Completeness").
UNFINISHED = set("""
and or but so then because if when while though although unless until whether nor
the a an this that these those my your our their its his her some any every each no
to of in on at for with from into onto about by over under after before between through as than like
is are was were be been am do does did can could would should will shall may might must have has had
um uh er erm hmm also plus versus vs which who whose what where how
""".split())


def complete_words(text: str) -> bool:
    w = words(text)
    return bool(w) and w[-1] not in UNFINISHED


def complete_punct(text: str) -> bool:
    return complete_words(text) and text.rstrip().endswith((".", "?", "!"))


COMPLETENESS = {"none": lambda text: True, "words": complete_words, "words+punct": complete_punct}


def smart_turn(name: str):
    options = ort.SessionOptions()
    options.intra_op_num_threads = 4
    options.inter_op_num_threads = 1
    session = ort.InferenceSession(str(MODELS / "smart-turn" / name), options, providers=["CPUExecutionProvider"])
    return lambda audio: float(session.run(None, {"input_features": features(audio)})[0][0, 0])


def pauses(stem: str, meta: dict, audio: np.ndarray, vad, models, stt) -> dict | None:
    probabilities = vad_probabilities(vad, audio)
    speech = probabilities >= SPEECH
    if not speech.any():
        return None
    last = _last_word(speech, meta.get("done_sample"))
    if last is None:
        return None
    # End at the last word, so the click of Done isn't in it, and extend with the room's noise
    # from before the speaker started (or quiet noise if there is too little).
    first = int(np.argmax(speech))
    room = audio[:max(first * FRAME - int(0.1 * RATE), 0)]
    if len(room) < int(0.2 * RATE):
        room = np.random.default_rng(0).normal(0, 1e-3, int(0.5 * RATE)).astype(np.float32)
    audio = audio[:(last + 1) * FRAME + int(0.1 * RATE)]
    extended = np.concatenate([audio, np.tile(room, int(EXTEND_S * RATE / len(room)) + 1)[:int(EXTEND_S * RATE)]])
    probabilities = vad_probabilities(vad, extended)
    speech = probabilities >= SPEECH
    first = int(np.argmax(speech))
    begin = max(first * FRAME - int(PRE_ROLL_S * RATE), 0)

    found, voiced, started, run_start = [], 0, False, None
    for i, is_speech in enumerate(speech):
        if is_speech:
            voiced += 1
            started = started or voiced * FRAME / RATE >= MIN_SPEECH_S
            if run_start is not None and found and found[-1]["frame"] == run_start:
                found[-1]["length_s"] = (i - run_start) * FRAME / RATE
            run_start = None
            continue
        if not started:
            continue
        if run_start is None:
            run_start = i
        if i - run_start + 1 == CHECK_FRAMES:
            upto = (i + 1) * FRAME
            found.append({"frame": run_start, "final": run_start > last, "length_s": None,
                          **{name: model(extended[:upto]) for name, model in models.items()},
                          "text": stt.text(extended[begin:upto]).strip()})
    # Pauses still running at the end of the extended audio have no length: they never resume.
    found = [p for p in found if p["final"] or p["length_s"] is not None]
    return {"stem": stem, "category": meta.get("category", "?"), "say": meta.get("say", ""),
            "device": meta.get("device", ""), "speech_s": (last + 1 - first) * FRAME / RATE, "pauses": found}


CLICK_S = 0.25  # pressing Done registers as a blip of "speech" this short, at the press


def _last_word(speech: np.ndarray, done_sample: int | None) -> int | None:
    """The last speech frame, leaving out the click of the Done button or Space key."""
    frames = np.nonzero(speech)[0]
    runs, start = [], frames[0]
    for a, b in zip(frames, frames[1:]):
        if b - a > 3:
            runs.append((start, a))
            start = b
    runs.append((start, frames[-1]))
    if done_sample is not None:
        done = done_sample // FRAME
        while runs and runs[-1][0] >= done - CLICK_S * RATE / FRAME and (runs[-1][1] - runs[-1][0] + 1) * FRAME / RATE < CLICK_S:
            runs.pop()
    return int(runs[-1][1]) if runs else None


def load() -> list[dict]:
    cache = json.loads(CACHE.read_text()) if CACHE.exists() else {}
    items = list(recordings("turn"))
    todo = [item for item in items if item[0] not in cache]
    if todo:
        vad = SileroVAD()
        models = {"fp32": smart_turn("smart-turn-v3.2-gpu.onnx"), "int8": smart_turn("smart-turn-v3.2-cpu.onnx")}
        stt = Parakeet("cpu", "int8", 4)
        started = time.perf_counter()
        for n, (stem, meta, audio) in enumerate(todo, 1):
            cache[stem] = pauses(stem, meta, audio, vad, models, stt)
            print(f"\rprepared {n}/{len(todo)} ({time.perf_counter() - started:.0f} s)", end="", flush=True)
        print()
        CACHE.write_text(json.dumps(cache, indent=1))
    return [cache[stem] for stem, _, _ in items if cache.get(stem)]


def simulate(turn: dict, model: str, threshold: float, completeness, fallback: float, grace: float):
    """(cut off, rescued cuts, delay at the true end, ended by smart-turn)."""
    rescued = 0
    for pause in turn["pauses"]:
        ends = pause[model] > threshold and completeness(pause["text"])
        decided = CHECK_S if ends else fallback
        if pause["final"]:
            return False, rescued, decided, ends
        if pause["length_s"] >= decided:
            if pause["length_s"] - decided <= grace:
                rescued += 1
            else:
                return True, rescued, None, ends
    return False, rescued, fallback, False  # no final pause found: the fallback ends it


def summarize(turns: list[dict], config) -> dict:
    results = [simulate(t, *config) for t in turns]
    delays = [d for cut, _, d, _ in results if not cut]
    return {"cut": sum(r[0] for r in results) / len(results), "rescued": sum(r[1] for r in results),
            "p50": float(np.median(delays)) if delays else None, "p90": float(np.percentile(delays, 90)) if delays else None,
            "mean": float(np.mean(delays)) if delays else None,
            "quick": sum(r[3] for r in results if not r[0]) / max(len(delays), 1), "results": results}


def main():
    turns = load()
    if not turns:
        print("No turn recordings yet: record some at /record.")
        return
    n_pauses = sum(1 for t in turns for p in t["pauses"] if not p["final"])
    print(f"{len(turns)} recordings, {n_pauses} mid-turn pauses of {CHECK_S * 1000:.0f} ms or more, "
          f"{sum(t['speech_s'] for t in turns):.0f} s of speech\n")

    thresholds, graces = [0.5, 0.6, 0.7, 0.8, 0.9], [0.0, 0.3, 0.6]
    for fallback in [1.5, 2.0]:
        print(f"Silence fallback {fallback} s. Each cell: cut off % / mean delay ms from the end of speech "
              "to the decision. A grace longer than the final pass delays dispatch by the difference.\n")
        print("| Model | Completeness | Threshold | " + " | ".join(f"Grace {g} s" for g in graces) + " |")
        print("| --- " * (3 + len(graces)) + "|")
        for model, completeness, threshold in itertools.product(["fp32", "int8"], COMPLETENESS, thresholds):
            cells = []
            for grace in graces:
                summary = summarize(turns, (model, threshold, COMPLETENESS[completeness], fallback, grace))
                cells.append(f"{summary['cut'] * 100:.0f} / {summary['mean'] * 1000:.0f}")
            print(f"| {model} | {completeness} | {threshold} | " + " | ".join(cells) + " |")
        print()

    config = ("fp32", 0.5, COMPLETENESS["none"], 1.5, 0.3)
    print("Still cut off with fp32, 0.5, no completeness check, 1.5 s fallback, 0.3 s grace:")
    for turn, (cut, *_rest) in zip(turns, summarize(turns, config)["results"]):
        if cut:
            pause = next(p for p in turn["pauses"] if not p["final"] and p["length_s"] >= CHECK_S + 0.3)
            print(f"  {turn['stem']} [{turn['category']}] pause {pause['length_s']:.2f} s, "
                  f"fp32 {pause['fp32']:.2f}: “{pause['text']}”")

    by_kind = defaultdict(lambda: [0, 0])
    for turn, (cut, *_rest) in zip(turns, summarize(turns, config)["results"]):
        by_kind[turn["category"]][0] += cut
        by_kind[turn["category"]][1] += 1
    print("\nBy kind: " + ", ".join(f"{k} {c}/{n}" for k, (c, n) in sorted(by_kind.items())))

if __name__ == "__main__":
    main()
