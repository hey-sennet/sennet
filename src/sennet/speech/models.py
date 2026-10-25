"""The pinned speech models: where to download them, their checksums, and which
ones each device uses.

Models live under `<data dir>/models/<model>/`. They are downloaded by
`sennet setup`, never vendored. Licences are listed in NOTICES.
"""
from __future__ import annotations

import hashlib
import re
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

PARAKEET_REVISION = "0bbb45a3365852604aef28b538a8f066f4ccaa85"
PARAKEET_URL = f"https://huggingface.co/istupakov/parakeet-tdt-0.6b-v2-onnx/resolve/{PARAKEET_REVISION}/"
KOKORO_URL = "https://github.com/thewh1teagle/kokoro-onnx/releases/download/model-files-v1.0/"
SMART_TURN_REVISION = "f766f81d3cfdf7737ac64aad813d91bbfd56bf93"
OPENWAKEWORD_URL = "https://github.com/dscripka/openWakeWord/releases/download/v0.5.1/"
# Our own "hey sennet" (training/wakeword).
HEY_SENNET_REVISION = "47166353ece5eff7333d3affcb7bda8293d17ed4"
HEY_SENNET_URL = f"https://huggingface.co/hey-sennet/wakeword/resolve/{HEY_SENNET_REVISION}/"
# The browser's ONNX runtime: must be the onnxruntime-web version the client is built with
# (frontend/package.json pins it; tests/test_wakeword.py checks the two agree).
ORT_WEB_VERSION = "1.30.0"


@dataclass(frozen=True)
class ModelFile:
    name: str
    url: str
    sha256: str
    size: int


@dataclass(frozen=True)
class Model:
    id: str
    role: str  # vad | stt | tts | turn | wake | wake_runtime | wake_word
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

# The wake word, run in the browser rather than here: openWakeWord's mel spectrogram and speech
# embedding (Apache-2.0), shared by every phrase, then the phrase's own model. The server hands
# them to the client (server.py).
OPENWAKEWORD = Model("openwakeword-v0.5.1", "wake", "Apache-2.0", (
    ModelFile("melspectrogram.onnx", OPENWAKEWORD_URL + "melspectrogram.onnx",
              "ba2b0e0f8b7b875369a2c89cb13360ff53bac436f2895cced9f479fa65eb176f", 1087958),
    ModelFile("embedding_model.onnx", OPENWAKEWORD_URL + "embedding_model.onnx",
              "70d164290c1d095d1d4ee149bc5e00543250a7316b59f31d056cff7bd3075c1f", 1326578),
))


def _wake_word(name: str, sha256: str, size: int, url: str = OPENWAKEWORD_URL) -> Model:
    """A pre-trained phrase, beside the shared models. They're CC-BY-NC-SA-4.0 (trained on data
    with restrictive licences, ours as much as openWakeWord's), so setup only fetches the one asked for."""
    return Model(OPENWAKEWORD.id, "wake_word", "CC-BY-NC-SA-4.0",
                 (ModelFile(f"{name}.onnx", f"{url}{name}.onnx", sha256, size),))


# The phrases setup offers, the first preselected.
WAKE_WORDS = {
    "hey_sennet": _wake_word("hey_sennet_v2", "192922bab5f3cef842055c40697647959504e86d9268661cd33143810058fbb8", 205430,
                             HEY_SENNET_URL),
    "hey_jarvis": _wake_word("hey_jarvis_v0.1", "94a13cfe60075b132f6a472e7e462e8123ee70861bc3fb58434a73712ee0d2cb", 1271370),
    "hey_mycroft": _wake_word("hey_mycroft_v0.1", "c2a311e8fa1338de89c31b3b46dc4dffd4af2f9a8d6ddead48893c2d301b1f18", 857691),
    "hey_rhasspy": _wake_word("hey_rhasspy_v0.1", "5a9b3ed3be2910e35780e097905aa9f35a9c10038df47914cf2b3ec4d670f6ea", 204081),
    "alexa": _wake_word("alexa_v0.1", "6ff566a01d12670e8d9e3c59da32651db1575d17272a601b7f8a39283dfbae3e", 854246),
}
ORT_WEB = Model(f"onnxruntime-web-{ORT_WEB_VERSION}", "wake_runtime", "MIT", (
    ModelFile("ort-wasm-simd-threaded.wasm",
              f"https://cdn.jsdelivr.net/npm/onnxruntime-web@{ORT_WEB_VERSION}/dist/ort-wasm-simd-threaded.wasm",
              "3398c10d07d229bd91b364548e130e0e51a8e5704b88c7c083ebbeb78842dee2", 14239897),
))

# int8 on CPU (no accuracy cost in M0), fp32 on GPU (int8 is slow under CUDA).
MODELS = {
    "cpu": {"vad": SILERO, "stt": PARAKEET_INT8, "tts": KOKORO_INT8, "turn": SMART_TURN,
            "wake": OPENWAKEWORD, "wake_runtime": ORT_WEB},
    "cuda": {"vad": SILERO, "stt": PARAKEET_FP32, "tts": KOKORO_FP32, "turn": SMART_TURN,
             "wake": OPENWAKEWORD, "wake_runtime": ORT_WEB},
}
# Push-to-talk works without these: only conversation sessions need turn detection, and only
# the wake word needs its models, so an older install keeps working until setup fetches them.
OPTIONAL = {"turn", "wake", "wake_runtime"}


def path(root: Path, model: Model) -> Path:
    return root / model.id


def present(root: Path, model: Model) -> bool:
    """Every file is there at its expected size. Checksums are checked when fetching."""
    directory = path(root, model)
    return all((directory / f.name).is_file() and (directory / f.name).stat().st_size == f.size
               for f in model.files)


def wake_model(root: Path, name: str) -> Path | None:
    """The model the wake word listens with: the chosen phrase (speech.wake_word), once it, the
    shared models and the runtime they run on are downloaded. A `wakeword.onnx` beside them (a
    link to a trained one) replaces any pre-trained phrase."""
    if not (present(root, OPENWAKEWORD) and present(root, ORT_WEB)):
        return None
    override = path(root, OPENWAKEWORD) / "wakeword.onnx"
    if override.is_file():
        return override
    chosen = WAKE_WORDS.get(name)
    return path(root, chosen) / chosen.files[0].name if chosen and present(root, chosen) else None


def wake_phrase(model: Path) -> str:
    """The phrase a wake word model listens for, from its file name: hey_jarvis_v0.1.onnx is "hey jarvis"."""
    return re.sub(r"_v[\d.]+$", "", model.resolve().stem).replace("_", " ")


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
