"""Wire bytes for voice audio, on the same stack as the daemon (uvicorn, websockets,
permessage-deflate on by default), with real speech framed by sennet.voice.frames.

Usage: python voice.py <dir of 24 kHz Kokoro wavs> <dir of 16 kHz LibriSpeech wavs>
                       [<spikes/sessions recordings dir>]

Downlink: every Kokoro wav is sent as one reply segment (a `speech` message, then 100 ms
frames). Push-to-talk uplink: LibriSpeech utterances are sent as 20, 40 and 60 ms frames,
each inside `start` and `end`. Each run goes through tap.py, so the totals are TCP payload
after compression.

With the recordings from spikes/sessions (real phone and laptop mics, captured with the
client's own settings), it also measures a conversation (voice-protocol §12), which
streams the whole time it is open:
- open mic: the `turn` recordings, speech with its pauses, back to back as one stream;
- quiet: only their 32 ms windows under -45 dBFS, the cost of an open mic nobody talks into;
- over a reply: the `echo` recordings, the mic while a reply played on the speakers. During
  a reply both directions run at once; each has its own deflate context, so the two
  bitrates add.
"""
import asyncio
import json
import subprocess
import sys
import wave
from pathlib import Path

import numpy as np
import uvicorn
import websockets
from starlette.applications import Starlette
from starlette.routing import WebSocketRoute

from sennet.voice import frames

HERE = Path(__file__).parent
APP_PORT, TAP_PORT = 7730, 7731
kokoro_dir, libri_dir = Path(sys.argv[1]), Path(sys.argv[2])
recordings_dir = Path(sys.argv[3]) if len(sys.argv) > 3 else None
LOG = Path("voice-wire.jsonl")
UTTERANCES = 20
QUIET_DBFS, WINDOW = -45, 512  # 32 ms at 16 kHz


def read(path: Path) -> np.ndarray:
    with wave.open(str(path)) as w:
        return np.frombuffer(w.readframes(w.getnframes()), dtype="<i2")


def quiet(pcm: np.ndarray) -> np.ndarray:
    windows = pcm[:len(pcm) // WINDOW * WINDOW].reshape(-1, WINDOW).astype(np.float64)
    level = 20 * np.log10(np.sqrt((windows ** 2).mean(axis=1)) / 32768 + 1e-9)
    return windows[level < QUIET_DBFS].astype("<i2").ravel()


replies = [read(p) for p in sorted(kokoro_dir.glob("tts-cuda-fp32-*.wav"))]
utterances = [read(p) for p in sorted(libri_dir.glob("*.wav"))[:UTTERANCES]]
if recordings_dir:
    open_mic = np.concatenate([read(p) for p in sorted((recordings_dir / "turn").glob("*.wav"))])
    silence = quiet(open_mic)
    echo = np.concatenate([read(p) for p in sorted((recordings_dir / "echo").glob("*.wav"))])


async def send_reply(ws):
    for segment, pcm in enumerate(replies):
        await ws.send_text(json.dumps({"type": "speech", "generation": 0, "segment": segment,
                                       "text": "x" * 80, "task_id": 1, "approval_id": None}))
        for frame in frames.encode_downlink(pcm.astype(np.float32) / 32768, 24000, 0, segment):
            await ws.send_bytes(frame)
    await ws.send_text(json.dumps({"type": "speech_end", "generation": 0}))


async def stream(ws, pcm: np.ndarray, ms: int) -> int:
    """A conversation's uplink: one unbroken stream of frames between open_mic and close_mic."""
    await ws.send(json.dumps({"type": "open_mic", "task_id": 1, "project_id": None, "model": None, "effort": None}))
    step = 16 * ms
    for i in range(0, len(pcm), step):
        await ws.send(pcm[i:i + step].tobytes())
    await ws.send(json.dumps({"type": "close_mic"}))
    return -(-len(pcm) // step)


def report(label: str, got: dict, key: str, pcm_bytes: int, seconds: float, sent: int):
    print(f"{label}: {seconds:.0f} s, {sent} frames ({sent / seconds:.0f}/s); PCM {pcm_bytes / 1024:.0f} KB, "
          f"on the wire {got[key] / 1024:.0f} KB ({got[key] / pcm_bytes:.0%}) = {got[key] * 8 / seconds / 1000:.0f} kbps")


async def down(ws):
    await ws.accept()
    await send_reply(ws)
    await ws.receive()


async def up(ws):
    await ws.accept()
    while (await ws.receive())["type"] != "websocket.disconnect":
        pass


def wire_total(request_prefix: str) -> dict:
    rows = [json.loads(line) for line in LOG.read_text().splitlines()]
    return [r for r in rows if r["request"] and r["request"].startswith(request_prefix)][-1]


async def main():
    LOG.unlink(missing_ok=True)
    tap = subprocess.Popen([sys.executable, str(HERE / "tap.py"), str(TAP_PORT), str(APP_PORT), str(LOG)])
    app = Starlette(routes=[WebSocketRoute("/down", down), WebSocketRoute("/up/{ms}", up)])
    server = uvicorn.Server(uvicorn.Config(app, port=APP_PORT, log_level="warning"))
    serving = asyncio.ensure_future(server.serve())
    await asyncio.sleep(1.5)
    try:
        # Downlink
        audio_s = sum(len(p) for p in replies) / 24000
        raw = sum(len(p) * 2 for p in replies)
        async with websockets.connect(f"ws://127.0.0.1:{TAP_PORT}/down", max_size=None) as ws:
            messages = 0
            while json.loads(m).get("type") != "speech_end" if isinstance(m := await ws.recv(), str) else True:
                messages += 1
            await ws.send("done")
        await asyncio.sleep(0.3)
        got = wire_total("GET /down")
        print(f"downlink: {audio_s:.1f} s of speech in {len(replies)} segments, {messages} messages")
        print(f"  PCM {raw / 1024:.0f} KB, on the wire {got['down'] / 1024:.0f} KB "
              f"({got['down'] / raw:.0%}) = {got['down'] * 8 / audio_s / 1000:.0f} kbps")

        # Uplink, at three frame sizes
        speech_s = sum(len(u) for u in utterances) / 16000
        raw = sum(len(u) * 2 for u in utterances)
        for ms in (20, 40, 60):
            step = 16 * ms
            async with websockets.connect(f"ws://127.0.0.1:{TAP_PORT}/up/{ms}", max_size=None) as ws:
                sent = 0
                for pcm in utterances:
                    await ws.send(json.dumps({"type": "start", "turn": "t", "task_id": 1}))
                    for i in range(0, len(pcm), step):
                        await ws.send(pcm[i:i + step].tobytes())
                        sent += 1
                    await ws.send(json.dumps({"type": "end", "turn": "t"}))
            await asyncio.sleep(0.3)
            got = wire_total(f"GET /up/{ms}")
            print(f"uplink {ms} ms frames: {speech_s:.0f} s of speech, {sent} frames "
                  f"({sent / speech_s:.0f}/s); PCM {raw / 1024:.0f} KB, on the wire {got['up'] / 1024:.0f} KB "
                  f"({got['up'] / raw:.0%}) = {got['up'] * 8 / speech_s / 1000:.0f} kbps")

        # A conversation's uplink: everything the mic hears while it is open
        if recordings_dir:
            runs = [("open mic", open_mic, (20, 60)), ("quiet", silence, (20, 60)), ("over a reply", echo, (20,))]
            for label, pcm, sizes in runs:
                for ms in sizes:
                    path = f"/up/{label.replace(' ', '-')}-{ms}"
                    async with websockets.connect(f"ws://127.0.0.1:{TAP_PORT}{path}", max_size=None) as ws:
                        sent = await stream(ws, pcm, ms)
                    await asyncio.sleep(0.3)
                    report(f"{label}, {ms} ms frames", wire_total(f"GET {path}"), "up", len(pcm) * 2, len(pcm) / 16000, sent)
    finally:
        server.should_exit = True
        await serving
        tap.terminate()


asyncio.run(main())
