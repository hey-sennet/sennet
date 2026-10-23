"""Opus uplink against PCM on the real session recordings, through the product's own turn-finding.

Usage: python opus_eval.py <spikes/sessions recordings dir> <models dir> [stems…]

Each recording is put through each condition, then through what a conversation runs on the
server (voice/conversation.py): Silero VAD per 32 ms, the Endpointer, smart-turn at each pause
and Parakeet on each turn. All on CPU, so the service keeps the GPU.

- pcm20: as today.
- dither: PCM with inaudible noise added, the control: Parakeet's own variation.
- opus20, opus60: what the client would send: libopus at 16 kHz, VoIP, 24 kbps, DTX on.
  Packets of 2 bytes or less are not sent (Chrome sends nothing during DTX at all) and the
  server fills the gap with silence.

A decision can only be made once the frame holding its last sample has arrived, so its time
is rounded up to the frame: that is the latency step 6 trades for fewer packets.
"""
import json
import re
import sys
import wave
from collections import defaultdict
from pathlib import Path

import numpy as np

from sennet.speech import models
from sennet.speech.stt import Parakeet
from sennet.speech.turn import SmartTurn
from sennet.speech.vad import SileroVAD
from sennet.voice import endpoint, opus
from sennet.voice.conversation import PRE_ROLL_S

RATE = 16000
recordings, models_root = Path(sys.argv[1]), Path(sys.argv[2])
only = set(sys.argv[3:])
chosen = models.MODELS["cuda"]  # the fp32 builds, which this machine has; run on CPU
vad_model = SileroVAD(models.path(models_root, chosen["vad"]))
turn_model = SmartTurn(models.path(models_root, chosen["turn"]), 4)
stt = Parakeet(models.path(models_root, chosen["stt"]), "cpu", 4)
CONDITIONS = {"pcm20": None, "dither": None, "opus20": 320, "opus60": 960}
FRAMES = {"pcm20": 320, "dither": 320, "opus20": 320, "opus60": 960}
DITHER = 3e-5  # about -90 dBFS: inaudible, and enough to show how much Parakeet varies by itself


def read(path: Path) -> np.ndarray:
    with wave.open(str(path)) as w:
        return np.frombuffer(w.readframes(w.getnframes()), dtype="<i2").astype(np.float32) / 32768


def through_opus(audio: np.ndarray, frame: int) -> tuple[np.ndarray, int, int]:
    """(decoded audio, bytes sent, packets sent)."""
    encoder = opus.Encoder(RATE, 24000, opus.APPLICATION_VOIP, dtx=True)
    decoder = opus.Decoder(RATE)
    padded = np.concatenate([audio, np.zeros(-len(audio) % frame, np.float32)])
    out, sent, packets = [], 0, 0
    for i in range(0, len(padded), frame):
        packet = encoder.encode(padded[i:i + frame])
        if len(packet) <= 2:
            out.append(np.zeros(frame, np.float32))
            continue
        sent, packets = sent + len(packet), packets + 1
        out.append(decoder.decode(packet))
    return np.concatenate(out)[:len(audio)], sent, packets


def arrival(sample: int, frame: int) -> int:
    return -(-sample // frame) * frame


def find_turns(audio: np.ndarray, frame: int, reply: tuple[int, int] | None) -> list[dict]:
    """The Endpointer's turns, as conversation.py drives it."""
    vad, ep, size = vad_model.stream(), endpoint.Endpointer(), 512
    turns, turn = [], None
    for done in range(size, len(audio) + 1, size):
        over_reply = reply is not None and reply[0] <= done < reply[1]
        event = ep.frame(vad(audio[done - size:done]) >= endpoint.SPEECH, over_reply)
        if event == "start":
            begin = max(0, done - ep.start_back * size - int(PRE_ROLL_S * RATE))
            turn = {"start": begin, "opened": arrival(done, frame), "over_reply": over_reply}
        elif event == "check":
            if ep.checked(turn_model(audio[turn["start"]:done]), ep.pause) == "end":
                turn["end"] = done
        elif event == "end":
            turn["end"] = done
        elif event == "resume":
            turn.pop("end", None)
        elif event == "settled":
            turn["ended"] = arrival(turn["end"], frame)
            turns.append(turn)
            turn = None
    if turn is not None:
        turn.setdefault("end", len(audio))
        turn["ended"] = arrival(turn["end"], frame)
        turns.append(turn)
    for turn in turns:
        turn["text"] = stt.text(audio[turn["start"]:turn["end"]])
    return turns


def words(text: str) -> list[str]:
    return re.findall(r"[a-z0-9']+", text.lower())


def edits(a: list[str], b: list[str]) -> int:
    row = list(range(len(b) + 1))
    for i, x in enumerate(a, 1):
        prev, row[0] = row[0], i
        for j, y in enumerate(b, 1):
            prev, row[j] = row[j], min(row[j] + 1, row[j - 1] + 1, prev + (x != y))
    return row[-1]


results = defaultdict(list)
for meta_path in sorted(recordings.glob("*/*.json")):
    if only and meta_path.stem not in only:
        continue
    meta = json.loads(meta_path.read_text())
    audio = read(meta_path.with_suffix(".wav"))
    reply = None
    if meta["kind"] == "echo" and meta.get("play_sample") is not None:
        start = meta["play_sample"] + int(meta.get("output_latency_s", 0) * RATE)
        reply = (start, start + int(meta["play_duration_s"] * RATE))
    row = {"stem": meta_path.stem, "kind": meta["kind"], "trial": meta.get("trial") or meta.get("category"),
           "say": meta.get("say"), "seconds": len(audio) / RATE}
    for name, frame in CONDITIONS.items():
        if name == "dither":
            noise = np.random.default_rng(0).normal(0, DITHER, len(audio)).astype(np.float32)
            heard, sent, packets = audio + noise, len(audio) * 2, len(audio) // 320
        elif frame is None:
            heard, sent, packets = audio, len(audio) * 2, len(audio) // 320
        else:
            heard, sent, packets = through_opus(audio, frame)
        row[name] = {"turns": find_turns(heard, FRAMES[name], reply), "bytes": sent, "packets": packets}
    results[meta["kind"]].append(row)
    base = " | ".join(t["text"] for t in row["pcm20"]["turns"])
    print(f"{row['stem']} {row['trial']}: {base!r}", flush=True)
    for name in ("dither", "opus20", "opus60"):
        text = " | ".join(t["text"] for t in row[name]["turns"])
        if text != base:
            print(f"    {name}: {text!r}", flush=True)

Path("opus-eval.json").write_text(json.dumps(results, indent=1))


def spread(values: list[int]) -> str:
    return f"{np.mean(values) / 16:+.0f} ms on average (max {np.max(values) / 16:+.0f})" if values else "n/a"


def summary():
    print("\n## Turn recordings (open mic, no reply)")
    rows = results["turn"]
    for name in CONDITIONS:
        ref_words = sum(len(words(" ".join(t["text"] for t in r["pcm20"]["turns"]))) for r in rows)
        wer = sum(edits(words(" ".join(t["text"] for t in r["pcm20"]["turns"])),
                        words(" ".join(t["text"] for t in r[name]["turns"]))) for r in rows) / max(ref_words, 1)
        counts = sum(len(r[name]["turns"]) != len(r["pcm20"]["turns"]) for r in rows)
        ends = [r[name]["turns"][-1]["ended"] - r["pcm20"]["turns"][-1]["ended"] for r in rows
                if r[name]["turns"] and r["pcm20"]["turns"]]
        seconds = sum(r["seconds"] for r in rows)
        print(f"{name}: words changed vs PCM {wer:.1%}; "
              f"recordings with a different number of turns {counts}/{len(rows)}; "
              f"last turn ends {spread(ends)} vs PCM; "
              f"{sum(r[name]['bytes'] for r in rows) * 8 / seconds / 1000:.1f} kbps payload, "
              f"{sum(r[name]['packets'] for r in rows) / seconds:.1f} packets/s")
    print("\n## Echo recordings (a reply playing)")
    rows = results["echo"]
    for name in CONDITIONS:
        quiet = [r for r in rows if r["trial"] in ("echo", "chime")]
        false = sum(any(t["over_reply"] for t in r[name]["turns"]) for r in quiet)
        talk = [r for r in rows if r["trial"] in ("talk", "interrupt")]
        barged = [r for r in talk if any(t["over_reply"] for t in r[name]["turns"])]
        delays = [next(t for t in r[name]["turns"] if t["over_reply"])["opened"]
                  - next(t for t in r["pcm20"]["turns"] if t["over_reply"])["opened"]
                  for r in barged if any(t["over_reply"] for t in r["pcm20"]["turns"])]
        seconds = sum(r["seconds"] for r in rows)
        print(f"{name}: false barge-ins {false}/{len(quiet)}; barge-ins {len(barged)}/{len(talk)}, "
              f"opened {spread(delays)} vs PCM; "
              f"{sum(r[name]['bytes'] for r in rows) * 8 / seconds / 1000:.1f} kbps payload, "
              f"{sum(r[name]['packets'] for r in rows) / seconds:.1f} packets/s")


summary()
