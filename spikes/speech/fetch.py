"""Download spike models and test audio into .cache/ (idempotent)."""
import hashlib
import io
import json
import urllib.request
from pathlib import Path

import onnx_asr
import pyarrow.parquet as pq
import soundfile as sf
from huggingface_hub import hf_hub_download

CACHE = Path(__file__).parent / ".cache"
MODELS = CACHE / "models"

URLS = {
    "silero_vad.onnx": "https://github.com/snakers4/silero-vad/raw/master/src/silero_vad/data/silero_vad.onnx",
    "kokoro-v1.0.onnx": "https://github.com/thewh1teagle/kokoro-onnx/releases/download/model-files-v1.0/kokoro-v1.0.onnx",
    "kokoro-v1.0.int8.onnx": "https://github.com/thewh1teagle/kokoro-onnx/releases/download/model-files-v1.0/kokoro-v1.0.int8.onnx",
    "voices-v1.0.bin": "https://github.com/thewh1teagle/kokoro-onnx/releases/download/model-files-v1.0/voices-v1.0.bin",
}


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def fetch_url(name: str, url: str) -> Path:
    target = MODELS / name
    if not target.exists():
        print(f"downloading {name}")
        partial = target.with_suffix(target.suffix + ".part")
        urllib.request.urlretrieve(url, partial)
        partial.rename(target)
    return target


def parakeet_dir(quantization: str | None) -> Path:
    return MODELS / f"parakeet-tdt-0.6b-v2-{quantization or 'fp32'}"


def main():
    MODELS.mkdir(parents=True, exist_ok=True)
    hashes = {name: sha256(fetch_url(name, url)) for name, url in URLS.items()}

    for name in ["smart-turn-v3.2-cpu.onnx", "smart-turn-v3.2-gpu.onnx"]:
        path = Path(hf_hub_download("pipecat-ai/smart-turn-v3", name, local_dir=MODELS / "smart-turn"))
        hashes[name] = sha256(path)

    # onnx-asr downloads only into a missing directory, so each quantization gets its own.
    for quantization in [None, "int8"]:
        print(f"preparing parakeet-tdt-0.6b-v2 quantization={quantization}")
        onnx_asr.load_model("nemo-parakeet-tdt-0.6b-v2", parakeet_dir(quantization),
                            quantization=quantization, providers=["CPUExecutionProvider"])

    # LibriSpeech validation-clean sample (CC-BY-4.0): 73 read utterances with references.
    audio_dir = CACHE / "librispeech"
    if not (audio_dir / "refs.json").exists():
        audio_dir.mkdir(parents=True, exist_ok=True)
        parquet = hf_hub_download("hf-internal-testing/librispeech_asr_dummy",
                                  "clean/validation-00000-of-00001.parquet", repo_type="dataset",
                                  local_dir=CACHE / "hf")
        refs = {}
        for row in pq.read_table(parquet).to_pylist():
            audio, rate = sf.read(io.BytesIO(row["audio"]["bytes"]), dtype="float32")
            assert rate == 16000
            name = f"{row['id']}.wav"
            sf.write(audio_dir / name, audio, rate, subtype="PCM_16")
            refs[name] = row["text"]
        (audio_dir / "refs.json").write_text(json.dumps(refs, indent=1))

    (CACHE / "hashes.json").write_text(json.dumps(hashes, indent=1))
    print(json.dumps(hashes, indent=1))


if __name__ == "__main__":
    main()
