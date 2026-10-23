"""End-to-end voice timings on the real speech models, with the fake Codex as the agent.

    uv run python tests/voice_bench.py --models ~/.local/share/sennet/models --device cpu
    uv run python tests/voice_bench.py --models ... --device cuda --wav my-command.wav

Each run streams a spoken command over /api/voice in real time, 20 ms frames, as a
browser would, routed by its leading project name. It times the final transcript
after release, the (fake) agent's turn, and the reply's first audio after the
agent's message. Without --wav the command is synthesized by Kokoro, which is
cleaner than a real voice; record real commands to measure what users will see.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import statistics
import sys
import tempfile
import threading
import time
import wave
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
from fastapi.testclient import TestClient

from sennet import setup
from sennet.config import Paths, Settings
from sennet.server import create_app
from sennet.speech import Speech
from conftest import paired

COMMAND = "In backend, run the unit tests and tell me which ones failed."
FAKE_CODEX = Path(__file__).with_name("fake_codex.py")
FRAME = 320  # 20 ms at 16 kHz


@dataclass
class Run:
    transcript: str
    reply: str
    final_ms: float          # release → final transcript
    agent_ms: float          # final → the agent's message (the fake agent: process start and turn)
    speech_ms: float         # the agent's message → first reply audio
    captions: int
    caption_period_ms: float | None
    audio_s: float           # reply audio received
    timeline: list[str] = field(default_factory=list)


def load_speech(models: Path, device: str, threads: int = 4) -> Speech:
    speech = Speech()
    asyncio.run(speech.load(models, device, threads, "af_heart"))
    if speech.status != {"stt": "ready", "tts": "ready"}:
        raise SystemExit(f"speech not ready: {speech.detail}")
    return speech


def spoken_command(speech: Speech, wav: Path | None) -> np.ndarray:
    """16 kHz float32 audio of the command, with a little silence either side."""
    if wav:
        with wave.open(str(wav)) as w:
            if (w.getframerate(), w.getnchannels(), w.getsampwidth()) != (16000, 1, 2):
                raise SystemExit("--wav must be 16 kHz mono 16-bit PCM")
            audio = np.frombuffer(w.readframes(w.getnframes()), dtype="<i2").astype(np.float32) / 32768
    else:
        audio = setup._resample(speech.tts.synthesize(COMMAND), speech.tts.rate, 16000)
    pad = np.zeros(4800, dtype=np.float32)
    return np.concatenate([pad, audio, pad])


def run_once(client: TestClient, audio: np.ndarray, turn: str) -> Run:
    agent_message = {}

    def watch_events():
        with client.websocket_connect("/api/events") as events:
            while True:
                event = events.receive_json()
                if event["type"] == "message" and event["message"]["role"] == "agent":
                    agent_message["at"] = time.perf_counter()
                    return

    watcher = threading.Thread(target=watch_events, daemon=True)
    watcher.start()
    time.sleep(0.2)  # let the events socket subscribe

    pcm = (np.clip(audio, -1, 1) * 32767).astype("<i2").tobytes()
    timeline, captions, reply = [], [], []
    marks: dict[str, float] = {}
    audio_samples = 0
    with client.websocket_connect("/api/voice") as ws:
        ws.receive_json()  # ready

        def read():  # on its own thread, so messages are timed as they arrive, not after sending
            nonlocal audio_samples
            while True:
                message = ws.receive()
                now = time.perf_counter()
                if message.get("bytes") is not None:
                    marks.setdefault("first_audio", now)
                    audio_samples += (len(message["bytes"]) - 8) // 2
                    continue
                event = json.loads(message["text"])
                timeline.append((now, event))
                if event["type"] == "caption":
                    captions.append(now)
                elif event["type"] == "final":
                    marks["final"], marks["final_text"] = now, event["text"]
                elif event["type"] == "speech":
                    reply.append(event["text"])
                elif event["type"] == "speech_end":
                    return

        reader = threading.Thread(target=read, daemon=True)
        reader.start()
        ws.send_json({"type": "start", "turn": turn})
        started = time.perf_counter()
        for i, offset in enumerate(range(0, len(pcm), FRAME * 2)):
            ws.send_bytes(pcm[offset:offset + FRAME * 2])
            time.sleep(max(0.0, started + (i + 1) * 0.02 - time.perf_counter()))  # real time
        released = time.perf_counter()
        ws.send_json({"type": "end", "turn": turn})
        reader.join(30)
    watcher.join(5)
    periods = np.diff(captions) * 1000 if len(captions) > 1 else []
    return Run(transcript=marks["final_text"], reply=" ".join(reply), final_ms=1000 * (marks["final"] - released),
               agent_ms=1000 * (agent_message["at"] - marks["final"]),
               speech_ms=1000 * (marks["first_audio"] - agent_message["at"]),
               captions=len(captions), caption_period_ms=float(np.median(periods)) if len(periods) else None,
               audio_s=audio_samples / 24000,
               timeline=[f"{1000 * (at - released):7.0f} ms  {event['type']}" for at, event in timeline])


def bench(models: Path, device: str, runs: int, wav: Path | None = None, threads: int = 4) -> list[Run]:
    speech = load_speech(models, device, threads)
    audio = spoken_command(speech, wav)
    with tempfile.TemporaryDirectory() as home:
        home = Path(home)
        (home / "repo").mkdir()
        settings = Settings()
        settings.agents.commands["codex"] = [sys.executable, str(FAKE_CODEX)]
        settings.speech.device = device
        try:
            with paired(TestClient(create_app(settings, Paths(home, home), speech=speech),
                                   headers={"X-Sennet": "1"})) as client:
                client.post("/api/projects", json={"path": str(home / "repo"), "name": "backend"})
                return [run_once(client, audio, f"t{i}") for i in range(runs)]
        finally:
            speech.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--models", type=Path, required=True)
    parser.add_argument("--device", choices=("cpu", "cuda"), default="cpu")
    parser.add_argument("--runs", type=int, default=3)
    parser.add_argument("--threads", type=int, default=4)
    parser.add_argument("--wav", type=Path, help="a 16 kHz mono recording of a command, instead of Kokoro")
    parser.add_argument("--timeline", action="store_true", help="print each run's messages")
    args = parser.parse_args()

    results = bench(args.models.expanduser(), args.device, args.runs, args.wav, args.threads)
    for run in results:
        if args.timeline:
            print("\n".join(run.timeline), end="\n\n")
    median = lambda values: statistics.median(values)  # noqa: E731
    print(f"device {args.device}, {len(results)} runs, medians:")
    print(f"  heard:              {results[-1].transcript!r}")
    print(f"  replied:            {results[-1].reply!r}")
    print(f"  release → final:    {median([r.final_ms for r in results]):.0f} ms")
    print(f"  final → agent reply: {median([r.agent_ms for r in results]):.0f} ms  (fake agent)")
    print(f"  reply → first audio: {median([r.speech_ms for r in results]):.0f} ms")
    print(f"  release → first audio, without the agent: "
          f"{median([r.final_ms + r.speech_ms for r in results]):.0f} ms")
    periods = [r.caption_period_ms for r in results if r.caption_period_ms]
    print(f"  captions:           {median([r.captions for r in results]):.0f} per turn"
          + (f", every {median(periods):.0f} ms" if periods else ""))


if __name__ == "__main__":
    main()
