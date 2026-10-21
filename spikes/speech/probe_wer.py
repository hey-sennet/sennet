"""Full-utterance WER on all LibriSpeech samples per model variant (no streaming)."""
import json
from pathlib import Path

import jiwer
import soundfile as sf

from bench_stt import clean
from speech import Parakeet

data = Path(".cache/librispeech")
refs = json.loads((data / "refs.json").read_text())
for device, quantization in [("cuda", None), ("cuda", "int8"), ("cpu", "int8")]:
    model = Parakeet(device, quantization, 4)
    hyps, truth = [], []
    for name, ref in sorted(refs.items()):
        hyps.append(clean(model.text(sf.read(data / name, dtype="float32")[0])))
        truth.append(clean(ref))
    out = jiwer.process_words(truth, hyps)
    print(f"{device} {quantization or 'fp32'}: WER {out.wer:.2%} over {len(truth)} utterances", flush=True)
    if device == "cuda" and quantization is None:
        for ref, hyp in zip(truth, hyps):
            if ref != hyp:
                print("   ref:", ref[:110]); print("   hyp:", hyp[:110])
