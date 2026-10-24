"""Write the fixture that checks the browser's wake word against openWakeWord itself.

Speaks a few phrases with the training voice, pads them with silence, and scores them with
openWakeWord's own streaming `predict`. The browser's pipeline (frontend/src/voice/wakeword.ts)
must give the same scores from the same audio. Its warm-up noise is replaced by a fixed pattern
both sides make, so they agree from the first chunk.

    uv run python reference.py ~/.local/share/sennet/models/openwakeword-v0.5.1/hey_jarvis_v0.1.onnx
"""
from __future__ import annotations

import argparse
import json
import sys
import wave
from pathlib import Path

import numpy as np
from scipy.signal import resample_poly

HERE = Path(__file__).parent
GENERATOR = HERE / "data" / "piper-sample-generator"
FIXTURE = HERE.parent.parent / "frontend" / "src" / "voice" / "fixtures" / "wakeword.json"
PHRASES = {"hey_jarvis": "hey jarvis", "negative": "what's the weather like"}
CHUNK = 1280


def noise() -> np.ndarray:
    """The warm-up noise, made the same way in wakeword.test.ts."""
    return ((np.arange(16000 * 4) * 7919) % 2000 - 1000).astype(np.int16)


def speak(text: str, out: Path) -> np.ndarray:
    sys.path.insert(0, str(GENERATOR))
    from piper_sample_generator.__main__ import generate_samples

    out.mkdir(parents=True, exist_ok=True)
    generate_samples(text=[text], output_dir=out, model=GENERATOR / "models" / "en_US-libritts_r-medium.pt",
                     max_samples=1, file_names=["clip.wav"], length_scales=(1.0,), max_speakers=1)
    with wave.open(str(out / "clip.wav")) as clip:
        rate = clip.getframerate()
        audio = np.frombuffer(clip.readframes(clip.getnframes()), dtype=np.int16)
    audio = resample_poly(audio.astype(np.float32), 16000, rate)
    return np.clip(audio, -32768, 32767).astype(np.int16)


def scores(model_path: str, audio: np.ndarray) -> list[float]:
    from openwakeword.model import Model

    model = Model(wakeword_models=[model_path], inference_framework="onnx")
    model.preprocessor.feature_buffer = model.preprocessor._get_embeddings(noise())
    name = next(iter(model.models))
    return [float(model.predict(audio[i:i + CHUNK])[name]) for i in range(0, len(audio) - CHUNK + 1, CHUNK)]


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("model", help="the wake word model, e.g. hey_jarvis_v0.1.onnx")
    args = parser.parse_args()
    silence = np.zeros(16000, dtype=np.int16)
    clips = {}
    for name, text in PHRASES.items():
        audio = np.concatenate([silence, speak(text, HERE / "data" / "reference" / name), silence])
        audio = audio[:len(audio) // CHUNK * CHUNK]
        clips[name] = {"text": text, "audio": audio.tolist(), "scores": scores(args.model, audio)}
        print(f"{text!r}: peak {max(clips[name]['scores']):.3f}")
    FIXTURE.parent.mkdir(parents=True, exist_ok=True)
    FIXTURE.write_text(json.dumps({"model": Path(args.model).resolve().name, "clips": clips}))
    print(f"Wrote {FIXTURE}")


if __name__ == "__main__":
    main()
