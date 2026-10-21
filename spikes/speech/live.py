"""Live speech demo: mic → streaming captions → end of turn → final transcript → spoken reply.

    uv run python live.py --device cuda          # then open http://127.0.0.1:8765

Protocol on /ws: client sends JSON {"type": "start", "auto": bool} / {"type": "stop"}
and binary 16 kHz mono PCM16 frames; server sends JSON caption/final/timing events
and binary TTS audio (PCM16, sample rate announced in a preceding "audio" event).
"""
import argparse
import asyncio
import json
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import numpy as np
import uvicorn
from starlette.applications import Starlette
from starlette.responses import FileResponse
from starlette.routing import Route, WebSocketRoute
from starlette.websockets import WebSocket, WebSocketDisconnect

from speech import RATE, Parakeet, SileroVAD, SmartTurn, StreamingTranscriber, TTS, TurnDetector

parser = argparse.ArgumentParser()
parser.add_argument("--device", default="cpu", choices=["cpu", "cuda"])
parser.add_argument("--quantization", default=None, choices=["int8", "fp32"],
                    help="default: int8 on cpu, fp32 on cuda")
parser.add_argument("--threads", type=int, default=4)
parser.add_argument("--window", type=float, default=8.0)
parser.add_argument("--interval", type=float, default=0.25)
parser.add_argument("--tts-device", default=None, choices=["cpu", "cuda"])
parser.add_argument("--port", type=int, default=8765)
args = parser.parse_args()
args.quantization = args.quantization or ("fp32" if args.device == "cuda" else "int8")

print("loading models…", flush=True)
STT = Parakeet(args.device, None if args.quantization == "fp32" else "int8", args.threads)
TURN = SmartTurn(args.device)
SPEAK = TTS(args.tts_device or args.device, "int8" if (args.tts_device or args.device) == "cpu" else None,
            args.threads)
STT.text(np.zeros(RATE, dtype=np.float32)); TURN(np.zeros(RATE, dtype=np.float32)); SPEAK("Ready.")
# One worker per model so a slow decode never queues behind another model.
STT_POOL, TURN_POOL, TTS_POOL = (ThreadPoolExecutor(1) for _ in range(3))
print(f"ready: http://127.0.0.1:{args.port}", flush=True)


class Session:
    def __init__(self, ws: WebSocket):
        self.ws = ws
        self.loop = asyncio.get_running_loop()
        self.active = False
        self.ticker: asyncio.Task | None = None
        self.turn_busy = False

    async def send(self, **event):
        await self.ws.send_text(json.dumps(event))

    async def start(self, auto: bool):
        self.transcriber = StreamingTranscriber(STT, max_window=args.window)
        self.detector = TurnDetector(SileroVAD(), TURN)
        self.auto = auto
        self.active = True
        self.started = time.perf_counter()
        self.pending = []
        self.ticker = asyncio.create_task(self.tick_loop())

    async def audio(self, data: bytes):
        if not self.active:
            return
        chunk = np.frombuffer(data, dtype="<i2").astype(np.float32) / 32768
        self.transcriber.add(chunk)
        if self.auto:
            # Turn detection runs on its own worker; buffer audio while a check is in flight.
            self.pending.append(chunk)
            if not self.turn_busy:
                self.turn_busy = True
                asyncio.create_task(self.check_turn())

    async def check_turn(self):
        while self.pending and self.active:
            block = np.concatenate(self.pending)
            self.pending = []
            if await self.loop.run_in_executor(TURN_POOL, self.detector.feed, block):
                self.turn_busy = False
                await self.finish("turn detected")
                return
        self.turn_busy = False

    async def tick_loop(self):
        last = time.perf_counter()
        while self.active:
            caption = await self.loop.run_in_executor(STT_POOL, self.transcriber.tick)
            now = time.perf_counter()
            if not self.active:
                break
            await self.send(type="caption", committed=caption.committed, provisional=caption.provisional,
                            decode_ms=round(caption.decode_ms), period_ms=round((now - last) * 1000),
                            window_s=round(len(self.transcriber.audio[self.transcriber.anchor:]) / RATE, 1))
            last = now
            await asyncio.sleep(max(0.0, args.interval - caption.decode_ms / 1000))

    async def finish(self, reason: str):
        if not self.active:
            return
        self.active = False
        ended = time.perf_counter()
        if self.ticker:
            await self.ticker
        checks = [round(ms) for _, _, ms in self.detector.checks]
        started = time.perf_counter()
        text = await self.loop.run_in_executor(STT_POOL, self.transcriber.finish)
        final_ms = (time.perf_counter() - started) * 1000
        await self.send(type="final", text=text, reason=reason, final_ms=round(final_ms),
                        wait_ms=round((started - ended) * 1000), turn_checks_ms=checks,
                        utterance_s=round(len(self.transcriber.audio) / RATE, 1))
        if text:
            await self.speak(f"You said: {text}", ended)

    async def speak(self, text: str, ended: float):
        first = None
        async for samples, rate in SPEAK.stream(text):
            if first is None:
                first = (time.perf_counter() - ended) * 1000
                await self.send(type="audio", rate=rate, first_audio_ms=round(first))
            pcm = (np.clip(samples, -1, 1) * 32767).astype("<i2").tobytes()
            await self.ws.send_bytes(pcm)
        await self.send(type="spoken")


async def index(_):
    return FileResponse(Path(__file__).parent / "live.html")


async def ws_endpoint(ws: WebSocket):
    await ws.accept()
    session = Session(ws)
    await session.send(type="config", **{k: v for k, v in vars(args).items()})
    try:
        while True:
            message = await ws.receive()
            if message["type"] == "websocket.disconnect":
                break
            if message.get("bytes") is not None:
                await session.audio(message["bytes"])
                continue
            command = json.loads(message["text"])
            if command["type"] == "start":
                await session.start(bool(command.get("auto")))
            elif command["type"] == "stop":
                await session.finish("released")
    except WebSocketDisconnect:
        pass
    finally:
        session.active = False


app = Starlette(routes=[Route("/", index), WebSocketRoute("/ws", ws_endpoint)])

if __name__ == "__main__":
    uvicorn.run(app, host="127.0.0.1", port=args.port, log_level="warning")
