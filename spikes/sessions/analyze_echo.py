"""Step 0: how much of Sennet's own voice reaches the server, and what VAD and Parakeet make of it.

    uv run --project ../speech python analyze_echo.py

Per recording, inside the reply's window (from just before it starts to just after its tail):

- residual: level above the room's noise floor before the reply, in dB. With echo
  cancellation off, the same number is the echo as the mic hears it; the difference between
  the two is roughly what the browser removed.
- speech: share of 32 ms frames Silero calls speech, and the longest run of them. A run of
  300 ms or more would be a false barge-in at the threshold step 5 proposes.
- echo words: words Parakeet transcribes from the window, and how many of them are the
  reply's own (what the transcript echo check in step 5 would catch).
- WER: for "talk over it" and "speak, no reply", the error rate on the sentence the user
  said. The difference between the two is what talking over a reply costs.
"""
from __future__ import annotations

import json
from collections import defaultdict

import jiwer
import numpy as np

from common import (FRAME, HERE, RATE, Parakeet, SileroVAD, browser, db, longest_run, recordings,
                    vad_probabilities, words)

BARGE_IN_MS = 300
SPEECH = 0.5

vad = SileroVAD()
stt = Parakeet("cpu", "int8", 4)

rows = []
for stem, meta, audio in recordings("echo"):
    probabilities = vad_probabilities(vad, audio)
    trial = meta["trial"]
    row = {"stem": stem, "trial": trial, "device": meta["device"] or ("Phone" if "Android" in meta["user_agent"] else "Laptop"), "output": meta["output"],
           "browser": browser(meta["user_agent"]), "path": meta["path"], "aec": meta["aec"],
           "applied_aec": meta["applied"].get("echoCancellation"), "since_open": meta["trials_since_open"]}
    if meta["play_sample"] is not None:
        start = max(meta["play_sample"] - int(0.1 * RATE), 0)
        end = min(meta["play_sample"] + int((meta["play_duration_s"] + 0.8) * RATE), len(audio))
        floor = audio[int(0.1 * RATE):max(start, int(0.5 * RATE))]
        window = audio[start:end]
        row["residual_db"] = db(window) - db(floor)
        mask = probabilities[start // FRAME:end // FRAME] >= SPEECH
        row["speech_share"] = float(mask.mean()) if len(mask) else 0.0
        row["longest_ms"] = longest_run(mask) * FRAME * 1000 / RATE
        if trial in ("echo", "chime"):
            heard = words(stt.text(window))
            reply = set(words(meta["reply_text"] or ""))
            row["echo_words"] = len(heard)
            row["echo_own"] = sum(w in reply for w in heard)
            row["heard"] = " ".join(heard)
    if trial in ("talk", "interrupt", "baseline"):
        text = stt.text(audio)
        row["heard"] = text
        row["wer"] = jiwer.wer(" ".join(words(meta["sentence"])), " ".join(words(text)) or "-")
        if trial == "interrupt":
            # How long the user spoke over the reply before it stopped.
            spoken = np.nonzero(probabilities >= SPEECH)[0]
            after_prompt = spoken[spoken * FRAME >= meta["speak_sample"] - int(0.5 * RATE)]
            began = int(after_prompt[0]) * FRAME if len(after_prompt) else None
            stop = meta.get("stop_sample")
            row["stopped"] = stop is not None
            row["early_stop"] = stop is not None and began is not None and stop < began
            row["overlap_ms"] = (stop - began) * 1000 / RATE if stop is not None and began is not None else None
        if trial in ("talk", "interrupt"):
            reply = set(words(meta["reply_text"] or "")) - set(words(meta["sentence"]))
            row["echo_own"] = sum(w in reply for w in words(text))
    rows.append(row)

(HERE / ".cache" / "echo-results.json").write_text(json.dumps(rows, indent=1))

groups = defaultdict(list)
for row in rows:
    groups[(row["browser"], row["device"], row["output"], row["path"], row["aec"], row["trial"])].append(row)


def mean(rows, key, scale=1.0, digits=1):
    values = [r[key] for r in rows if r.get(key) is not None]
    return f"{np.mean(values) * scale:.{digits}f}" if values else "–"


print("| Browser | Device | Output | Playback | AEC | Trial | n | Residual dB | Speech % | Longest ms | "
      f"Barge-ins ≥{BARGE_IN_MS} ms | Echo words (own) | WER % |")
print("| --- " * 13 + "|")
for (b, device, output, path, aec, trial), group in sorted(groups.items()):
    false_barge = sum(r.get("longest_ms", 0) >= BARGE_IN_MS for r in group) if trial in ("echo", "chime") else None
    applied = {r["applied_aec"] for r in group}
    aec_text = ("on" if aec else "off") + ("" if applied == {aec} else f" (applied {applied})")
    echo = (f"{mean(group, 'echo_words')} ({mean(group, 'echo_own')})" if trial in ("echo", "chime")
            else f"– ({mean(group, 'echo_own')})" if trial == "talk" else "–")
    print(f"| {b} | {device} | {output} | {path} | {aec_text} | {trial} | {len(group)} | {mean(group, 'residual_db')} | "
          f"{mean(group, 'speech_share', 100, 0)} | {mean(group, 'longest_ms', 1, 0)} | "
          f"{'–' if false_barge is None else f'{false_barge}/{len(group)}'} | {echo} | {mean(group, 'wer', 100, 0)} |")

interrupts = [r for r in rows if r["trial"] == "interrupt"]
if interrupts:
    print("\nInterrupt it: the reply stopped once the mic was 15 dB over the room for 150 ms.")
    for r in interrupts:
        overlap = "never stopped" if not r["stopped"] else "stopped before speech (false trigger)" if r["early_stop"] \
            else f"spoke over it for {r['overlap_ms']:.0f} ms"
        print(f"  {r['stem']} {r['browser']}, {r['device']}: {overlap}; WER {r['wer'] * 100:.0f}%")

print("\nWhat was heard:")
for row in rows:
    if row.get("heard"):
        print(f"  {row['stem']} {row['browser']}, {row['device']}, {row['trial']}, aec {row['aec']}: {row['heard']}")
