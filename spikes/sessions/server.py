"""Recording server for the M7 gates (docs/m7-sessions.md, steps 0 and 1).

    uv run --project ../speech python server.py      # then open http://127.0.0.1:8790

/echo plays real Kokoro replies with the mic open and keeps what the mic heard.
/record collects spoken commands for the turn-detection evaluation.
Recordings are saved as 16 kHz WAV with a JSON sidecar under .cache/recordings/, which git
ignores: they are the user's voice, kept on this machine only.
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

import numpy as np
import soundfile as sf
import uvicorn
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import FileResponse, JSONResponse, Response
from starlette.routing import Route

from prompts import REPLIES, SAY_OVER, SCENARIOS

HERE = Path(__file__).parent
CACHE = HERE / ".cache"
CLIPS = CACHE / "clips"
RECORDINGS = CACHE / "recordings"
CHIME = HERE.parent.parent / "frontend" / "src" / "voice" / "approval-chime.mp3"
RATE = 16000

parser = argparse.ArgumentParser()
parser.add_argument("--port", type=int, default=8790)
args = parser.parse_args()


def make_clips():
    """Synthesize the replies once, with the product's voice, on CPU so the GPU stays free."""
    missing = [i for i in range(len(REPLIES)) if not (CLIPS / f"{i}.wav").exists()]
    if not missing:
        return
    sys.path.insert(0, str(HERE.parent / "speech"))
    from speech import TTS
    CLIPS.mkdir(parents=True, exist_ok=True)
    tts = TTS("cpu", "int8", 4)
    for i in missing:
        print(f"synthesizing reply {i}", flush=True)
        samples, rate = tts(REPLIES[i])
        sf.write(CLIPS / f"{i}.wav", samples, rate)


def resample(audio: np.ndarray, rate: int) -> np.ndarray:
    """FFT resampling of a whole recording; exact enough for analysis, which is all this is for."""
    if rate == RATE:
        return audio
    n = int(round(len(audio) * RATE / rate))
    spectrum = np.fft.rfft(audio)[: n // 2 + 1]
    return (np.fft.irfft(spectrum, n) * (n / len(audio))).astype(np.float32)


async def page(request: Request):
    name = request.path_params.get("name") or "index"
    if name not in ("index", "echo", "record"):
        return Response(status_code=404)
    return FileResponse(HERE / f"{name}.html")


async def asset(request: Request):
    name = request.url.path.lstrip("/")
    return FileResponse(HERE / name, media_type="text/javascript" if name.endswith(".js") else "text/css")


async def prompts(_):
    clips = [{"id": i, "text": t, "url": f"/clips/{i}.wav"} for i, t in enumerate(REPLIES)]
    return JSONResponse({"replies": clips, "say_over": SAY_OVER, "scenarios": SCENARIOS, "counts": counts()})


async def clip(request: Request):
    path = CLIPS / f"{int(request.path_params['id'])}.wav"
    return FileResponse(path) if path.exists() else Response(status_code=404)


async def chime(_):
    return FileResponse(CHIME, media_type="audio/mpeg")


def counts() -> dict:
    return {kind: len(list((RECORDINGS / kind).glob("*.json"))) for kind in ("echo", "turn")}


async def upload(request: Request):
    meta = json.loads(request.headers["x-meta"])
    kind = meta["kind"]
    if kind not in ("echo", "turn"):
        return Response(status_code=400)
    audio = np.frombuffer(await request.body(), dtype="<f4")
    audio = resample(audio, int(meta["rate"]))
    # Times recorded in device-rate samples become 16 kHz ones.
    for key in [k for k, v in meta.items() if k.endswith("_sample") and v is not None]:
        meta[key] = int(round(meta[key] * RATE / int(meta["rate"])))
    meta["rate"] = RATE
    folder = RECORDINGS / kind
    folder.mkdir(parents=True, exist_ok=True)
    stem = time.strftime("%Y%m%d-%H%M%S") + f"-{int(time.time() * 1000) % 1000:03d}"
    sf.write(folder / f"{stem}.wav", audio, RATE, subtype="PCM_16")
    (folder / f"{stem}.json").write_text(json.dumps(meta, indent=1))
    print(f"saved {kind}/{stem} ({len(audio) / RATE:.1f} s) {meta.get('trial', '')}", flush=True)
    return JSONResponse({"saved": stem, "counts": counts()})


app = Starlette(routes=[
    Route("/", page),
    Route("/recorder.js", asset),
    Route("/style.css", asset),
    Route("/{name:str}", page),
    Route("/api/prompts", prompts),
    Route("/clips/{id:int}.wav", clip),
    Route("/api/chime.mp3", chime),
    Route("/api/recordings", upload, methods=["POST"]),
])

if __name__ == "__main__":
    make_clips()
    print(f"ready: http://127.0.0.1:{args.port}", flush=True)
    uvicorn.run(app, host="127.0.0.1", port=args.port, log_level="warning")
