"""The pinned speech models: where to download them, their checksums, and which
ones each device uses.

Models live under `<data dir>/models/<model>/`. They are downloaded by
`sennet setup`, never vendored. Licences are listed in NOTICES.
"""
from __future__ import annotations

import hashlib
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

PARAKEET_REVISION = "0bbb45a3365852604aef28b538a8f066f4ccaa85"
PARAKEET_URL = f"https://huggingface.co/istupakov/parakeet-tdt-0.6b-v2-onnx/resolve/{PARAKEET_REVISION}/"
KOKORO_URL = "https://github.com/thewh1teagle/kokoro-onnx/releases/download/model-files-v1.0/"
SMART_TURN_REVISION = "f766f81d3cfdf7737ac64aad813d91bbfd56bf93"


@dataclass(frozen=True)
class ModelFile:
    name: str
    url: str
    sha256: str
    size: int


@dataclass(frozen=True)
class Model:
    id: str
    role: str  # vad | stt | tts | turn
    licence: str
    files: tuple[ModelFile, ...]

    @property
    def size(self) -> int:
        return sum(f.size for f in self.files)


SILERO = Model("silero-vad-v6.2", "vad", "MIT", (
    ModelFile("silero_vad.onnx", "https://github.com/snakers4/silero-vad/raw/v6.2/src/silero_vad/data/silero_vad.onnx",
              "1a153a22f4509e292a94e67d6f9b85e8deb25b4988682b7e174c65279d8788e3", 2327524),
))

_PARAKEET_COMMON = (
    ModelFile("config.json", PARAKEET_URL + "config.json",
              "666903c76b9798caf2c210afd4f6cd60b08a8dbf9800ec8d7a3bc0d2148ac466", 97),
    ModelFile("vocab.txt", PARAKEET_URL + "vocab.txt",
              "ec182b70dd42113aff6c5372c75cac58c952443eb22322f57bbd7f53977d497d", 9384),
)
PARAKEET_INT8 = Model("parakeet-tdt-0.6b-v2-int8", "stt", "CC-BY-4.0", _PARAKEET_COMMON + (
    ModelFile("encoder-model.int8.onnx", PARAKEET_URL + "encoder-model.int8.onnx",
              "3e0581fda6ab843888b51e56d7ee78b6d5bc3237ec113af1f732d1d5286aa155", 652184014),
    ModelFile("decoder_joint-model.int8.onnx", PARAKEET_URL + "decoder_joint-model.int8.onnx",
              "a449f49acd68979d418651dd2dcb737cc0f1bf0225e009e29ee326354edbf7d3", 8998286),
))
PARAKEET_FP32 = Model("parakeet-tdt-0.6b-v2-fp32", "stt", "CC-BY-4.0", _PARAKEET_COMMON + (
    ModelFile("encoder-model.onnx", PARAKEET_URL + "encoder-model.onnx",
              "3987bcd28175d829d12888a996a84e8f62a0e374d9ffd640662c1515adc679d3", 41770866),
    ModelFile("encoder-model.onnx.data", PARAKEET_URL + "encoder-model.onnx.data",
              "4dab7362d4874d85965045b1e41b2d61dd2cc0fb25671a7f6b3dc47bf120cc41", 2435420160),
    ModelFile("decoder_joint-model.onnx", PARAKEET_URL + "decoder_joint-model.onnx",
              "cbb52a07bd70ab5b67f8439d4b3cd8704b18467b4430bcacb5adabe154b8d191", 35792059),
))

_VOICES = ModelFile("voices-v1.0.bin", KOKORO_URL + "voices-v1.0.bin",
                    "bca610b8308e8d99f32e6fe4197e7ec01679264efed0cac9140fe9c29f1fbf7d", 28214398)
KOKORO_INT8 = Model("kokoro-v1.0-int8", "tts", "Apache-2.0", (
    ModelFile("kokoro.onnx", KOKORO_URL + "kokoro-v1.0.int8.onnx",
              "6e742170d309016e5891a994e1ce1559c702a2ccd0075e67ef7157974f6406cb", 92361271),
    _VOICES,
))
KOKORO_FP32 = Model("kokoro-v1.0-fp32", "tts", "Apache-2.0", (
    ModelFile("kokoro.onnx", KOKORO_URL + "kokoro-v1.0.onnx",
              "7d5df8ecf7d4b1878015a32686053fd0eebe2bc377234608764cc0ef3636a6c5", 325532387),
    _VOICES,
))

# Turn detection for conversation sessions (M7). The fp32 build on every tier: the int8 one
# is unstable, and fp32 is cheap enough on CPU (speech/turn.py).
SMART_TURN = Model("smart-turn-v3.2", "turn", "BSD-2-Clause", (
    ModelFile("smart-turn.onnx", f"https://huggingface.co/pipecat-ai/smart-turn-v3/resolve/{SMART_TURN_REVISION}/"
              "smart-turn-v3.2-gpu.onnx", "ab8dc64b88713f90b571c15b714bd1330e6c883cad8763dacf65c9376dc539be", 32411198),
))

# int8 on CPU (no accuracy cost in M0), fp32 on GPU (int8 is slow under CUDA).
MODELS = {
    "cpu": {"vad": SILERO, "stt": PARAKEET_INT8, "tts": KOKORO_INT8, "turn": SMART_TURN},
    "cuda": {"vad": SILERO, "stt": PARAKEET_FP32, "tts": KOKORO_FP32, "turn": SMART_TURN},
}
# Push-to-talk works without these; only conversation sessions need them, so an install from
# before M7 keeps working until setup fetches them.
OPTIONAL = {"turn"}


def path(root: Path, model: Model) -> Path:
    return root / model.id


def present(root: Path, model: Model) -> bool:
    """Every file is there at its expected size. Checksums are checked when fetching."""
    directory = path(root, model)
    return all((directory / f.name).is_file() and (directory / f.name).stat().st_size == f.size
               for f in model.files)


class ChecksumError(Exception):
    pass


Progress = Callable[[ModelFile, int], None]  # file, bytes done so far


def fetch(root: Path, model: Model, progress: Progress | None = None):
    """Download any missing or damaged files of a model, verifying each one."""
    directory = path(root, model)
    directory.mkdir(parents=True, exist_ok=True)
    for file in model.files:
        target = directory / file.name
        if target.is_file() and target.stat().st_size == file.size and sha256(target) == file.sha256:
            if progress:
                progress(file, file.size)
            continue
        partial = target.with_name(target.name + ".part")
        digest = hashlib.sha256()
        done = 0
        with urllib.request.urlopen(file.url) as response, partial.open("wb") as out:
            while block := response.read(1 << 20):
                out.write(block)
                digest.update(block)
                done += len(block)
                if progress:
                    progress(file, done)
        if digest.hexdigest() != file.sha256:
            partial.unlink()
            raise ChecksumError(f"{model.id}/{file.name} did not match its checksum; try again")
        partial.replace(target)


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as f:
        while block := f.read(1 << 20):
            digest.update(block)
    return digest.hexdigest()
