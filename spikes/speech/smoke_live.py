"""Stream a WAV into live.py in real time with auto end-of-turn and print server events.

    uv run python live.py --device cuda &   then   uv run python smoke_live.py [file.wav]
"""
import asyncio
import json
import sys
import time
from pathlib import Path

import numpy as np
import soundfile as sf
import websockets


async def main():
    path = sys.argv[1] if len(sys.argv) > 1 else sorted(Path(".cache/librispeech").glob("*.wav"))[0]
    audio = sf.read(path, dtype="float32")[0]
    audio = np.concatenate([audio, np.zeros(16000 * 3, dtype=np.float32)])
    pcm = (audio * 32767).astype("<i2").tobytes()
    async with websockets.connect("ws://127.0.0.1:8765/ws") as ws:
        print(await ws.recv())
        await ws.send(json.dumps({"type": "start", "auto": True}))
        captions, audio_bytes, started = 0, 0, time.perf_counter()

        async def sender():
            for offset in range(0, len(pcm), 1280):  # 40 ms frames
                await ws.send(pcm[offset:offset + 1280])
                await asyncio.sleep(0.04)

        task = asyncio.create_task(sender())
        async for message in ws:
            if isinstance(message, bytes):
                audio_bytes += len(message)
                continue
            event = json.loads(message)
            if event["type"] == "caption":
                captions += 1
                if captions % 8 == 0:
                    print(f"{time.perf_counter() - started:5.2f}s caption: {event['committed']} | {event['provisional']}")
            else:
                print(f"{time.perf_counter() - started:5.2f}s {event}")
            if event["type"] == "spoken":
                break
        task.cancel()
        print(f"{captions} captions, {audio_bytes // 2 / 24000:.1f}s of reply audio")


asyncio.run(main())
