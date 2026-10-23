"""Compare features.py with transformers' WhisperFeatureExtractor, and smart-turn's output on each.

The fp32 model is used: the int8 one moves by up to 0.14 on input noise of 1e-5, so it
can't show whether the features match.

    uv run --project ../speech python check_features.py
"""
import sys
from pathlib import Path

import numpy as np
import onnxruntime as ort
from transformers import WhisperFeatureExtractor

from features import RATE, SECONDS, features

SPEECH = Path(__file__).parent.parent / "speech"
sys.path.insert(0, str(SPEECH))
from speech import MODELS  # noqa: E402

reference = WhisperFeatureExtractor(chunk_length=SECONDS)
session = ort.InferenceSession(str(MODELS / "smart-turn" / "smart-turn-v3.2-gpu.onnx"),
                               providers=["CPUExecutionProvider"])


def theirs(audio):
    n = SECONDS * RATE
    audio = audio[-n:]
    if len(audio) < n:
        audio = np.pad(audio, (n - len(audio), 0))
    return reference(audio, sampling_rate=RATE, return_tensors="np", padding="max_length", max_length=n,
                     truncation=True, do_normalize=True).input_features.astype(np.float32)


def score(x):
    return float(session.run(None, {"input_features": x})[0][0, 0])


rng = np.random.default_rng(0)
librispeech = sorted((SPEECH / ".cache" / "librispeech").glob("*.wav"))[:12]
clips = [rng.normal(0, 0.1, RATE * 3).astype(np.float32), rng.normal(0, 0.1, RATE * 11).astype(np.float32)]
if librispeech:
    import soundfile as sf
    clips += [sf.read(p, dtype="float32")[0] for p in librispeech]

worst_feature = worst_score = 0.0
for clip in clips:
    a, b = features(clip), theirs(clip)
    assert a.shape == b.shape, (a.shape, b.shape)
    worst_feature = max(worst_feature, float(np.abs(a - b).max()))
    worst_score = max(worst_score, abs(score(a) - score(b)))
print(f"{len(clips)} clips: max feature difference {worst_feature:.2e}, max smart-turn difference {worst_score:.2e}")
