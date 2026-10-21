"""Speech pipeline components for the M0 spike: VAD, turn detection, streaming STT, TTS.

Everything runs on 16 kHz mono float32 audio. Each component takes a `device`
of "cpu" or "cuda".
"""
from __future__ import annotations

import re
import time
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
import onnxruntime as ort

RATE = 16000
MODELS = Path(__file__).parent / ".cache" / "models"

ort.preload_dlls()
ort.set_default_logger_severity(3)


def providers(device: str) -> list:
    if device != "cuda":
        return ["CPUExecutionProvider"]
    # Live captions decode a different input length on every tick. Exhaustive cuDNN
    # algorithm search (the default) re-benchmarks convolutions for each new shape.
    return [("CUDAExecutionProvider", {"cudnn_conv_algo_search": "HEURISTIC"}), "CPUExecutionProvider"]


def session_options(threads: int | None) -> ort.SessionOptions:
    options = ort.SessionOptions()
    if threads:
        options.intra_op_num_threads = threads
        options.inter_op_num_threads = 1
    return options


class SileroVAD:
    """Streaming Silero v5: feed 512-sample (32 ms) chunks, get speech probability."""

    CHUNK = 512
    CONTEXT = 64

    def __init__(self):
        # Tiny model; CPU with one thread is faster than any GPU round trip.
        self.session = ort.InferenceSession(str(MODELS / "silero_vad.onnx"), session_options(1),
                                            providers=["CPUExecutionProvider"])
        self.reset()

    def reset(self):
        self.state = np.zeros((2, 1, 128), dtype=np.float32)
        self.context = np.zeros(self.CONTEXT, dtype=np.float32)

    def __call__(self, chunk: np.ndarray) -> float:
        assert len(chunk) == self.CHUNK
        x = np.concatenate([self.context, chunk])[None, :].astype(np.float32)
        out, self.state = self.session.run(None, {"input": x, "state": self.state,
                                                  "sr": np.array(RATE, dtype=np.int64)})
        self.context = chunk[-self.CONTEXT:]
        return float(out[0, 0])


class SmartTurn:
    """Pipecat smart-turn v3.2: probability that the speaker has finished their turn."""

    SECONDS = 8

    def __init__(self, device: str = "cpu"):
        from transformers import WhisperFeatureExtractor
        name = "smart-turn-v3.2-gpu.onnx" if device == "cuda" else "smart-turn-v3.2-cpu.onnx"
        options = session_options(None)
        options.execution_mode = ort.ExecutionMode.ORT_SEQUENTIAL
        options.inter_op_num_threads = 1
        self.session = ort.InferenceSession(str(MODELS / "smart-turn" / name), options,
                                            providers=providers(device))
        self.features = WhisperFeatureExtractor(chunk_length=self.SECONDS)

    def __call__(self, audio: np.ndarray) -> float:
        n = self.SECONDS * RATE
        # Keep the end of the turn; pad at the start so speech sits at the end.
        audio = audio[-n:]
        if len(audio) < n:
            audio = np.pad(audio, (n - len(audio), 0))
        features = self.features(audio, sampling_rate=RATE, return_tensors="np", padding="max_length",
                                 max_length=n, truncation=True, do_normalize=True).input_features
        return float(self.session.run(None, {"input_features": features.astype(np.float32)})[0][0, 0])


@dataclass
class TurnDetector:
    """VAD gates when to ask smart-turn; smart-turn decides whether the turn is over.

    After `first_check` seconds of silence smart-turn runs once on the utterance so far.
    If it judges the turn incomplete (a mid-thought pause), the detector waits for
    speech to resume, and `max_silence` ends the turn regardless. Smart-turn is not
    re-run during the same pause: extra trailing silence alone pushes its score towards
    "complete", which cut speakers off mid-sentence in testing.
    """

    vad: SileroVAD
    turn: SmartTurn
    speech_threshold: float = 0.5
    first_check: float = 0.2
    max_silence: float = 2.0
    min_speech: float = 0.1
    started: bool = False
    voiced: int = 0
    silence: int = 0
    checked: bool = False
    pending: np.ndarray = field(default_factory=lambda: np.zeros(0, dtype=np.float32))
    audio: list = field(default_factory=list)
    checks: list = field(default_factory=list)  # (silence seconds, probability, ms)

    def feed(self, chunk: np.ndarray) -> bool:
        """Add audio; return True once the turn has ended."""
        self.pending = np.concatenate([self.pending, chunk])
        frame = SileroVAD.CHUNK
        while len(self.pending) >= frame:
            block, self.pending = self.pending[:frame], self.pending[frame:]
            self.audio.append(block)
            if self.vad(block) >= self.speech_threshold:
                self.voiced += 1
                self.silence = 0
                self.checked = False
                if self.voiced * frame / RATE >= self.min_speech:
                    self.started = True
                continue
            self.silence += 1
            if not self.started:
                continue
            quiet = self.silence * frame / RATE
            if quiet >= self.max_silence:
                return True
            if quiet >= self.first_check and not self.checked:
                self.checked = True
                started = time.perf_counter()
                probability = self.turn(np.concatenate(self.audio))
                self.checks.append((quiet, probability, (time.perf_counter() - started) * 1000))
                if probability > 0.5:
                    return True
        return False

    def reset(self):
        self.vad.reset()
        self.started, self.voiced, self.silence, self.checked = False, 0, 0, False
        self.pending = np.zeros(0, dtype=np.float32)
        self.audio, self.checks = [], []


@dataclass
class Word:
    text: str
    start: float  # seconds, relative to the decoded audio
    end: float


class Parakeet:
    """Parakeet TDT 0.6B v2 via onnx-asr, returning words with timestamps."""

    def __init__(self, device: str = "cpu", quantization: str | None = "int8", threads: int | None = None):
        import onnx_asr
        directory = MODELS / f"parakeet-tdt-0.6b-v2-{quantization or 'fp32'}"
        self.model = onnx_asr.load_model("nemo-parakeet-tdt-0.6b-v2", directory, quantization=quantization,
                                         providers=providers(device), sess_options=session_options(threads)
                                         ).with_timestamps()

    def __call__(self, audio: np.ndarray) -> list[Word]:
        result = self.model.recognize(audio.astype(np.float32), sample_rate=RATE)
        words: list[Word] = []
        for token, start in zip(result.tokens, result.timestamps):
            if token.startswith(" ") or not words:
                words.append(Word(token.strip(), start, start + 0.08))
            else:
                words[-1].text += token
                words[-1].end = start + 0.08
        return [w for w in words if w.text]

    def text(self, audio: np.ndarray) -> str:
        return self.model.recognize(audio.astype(np.float32), sample_rate=RATE).text


def norm(word: str) -> str:
    return re.sub(r"[^\w']", "", word.lower())


@dataclass
class Caption:
    committed: str
    provisional: str
    decode_ms: float


@dataclass
class StreamingTranscriber:
    """Live captions by re-decoding a sliding window of recent audio.

    LocalAgreement-2: words that two consecutive decodes of the same window agree
    on become committed; the rest are provisional. The window start (`anchor`)
    advances to a committed word boundary once the window exceeds `max_window`,
    so per-tick decode cost stays bounded however long the user talks.
    """

    stt: Parakeet
    max_window: float = 8.0
    audio: np.ndarray = field(default_factory=lambda: np.zeros(0, dtype=np.float32))
    anchor: int = 0                       # sample index where the window starts
    committed: list[str] = field(default_factory=list)
    window_committed: int = 0             # committed words that lie inside the window
    previous: list[Word] | None = None

    def add(self, chunk: np.ndarray):
        self.audio = np.concatenate([self.audio, chunk])

    def tick(self) -> Caption:
        window = self.audio[self.anchor:]
        started = time.perf_counter()
        words = self.stt(window) if len(window) >= RATE // 4 else []
        decode_ms = (time.perf_counter() - started) * 1000

        if self.previous is not None:
            agreed = 0
            for a, b in zip(self.previous, words):
                if norm(a.text) != norm(b.text):
                    break
                agreed += 1
            if agreed > self.window_committed:
                self.committed += [w.text for w in words[self.window_committed:agreed]]
                self.window_committed = agreed
        self.previous = words

        # A decode can return fewer words than were committed (e.g. a word merged);
        # advance only when the current words still cover the committed ones.
        if len(window) / RATE > self.max_window and 1 < self.window_committed <= len(words):
            self._advance(words)

        provisional = " ".join(w.text for w in words[self.window_committed:])
        return Caption(" ".join(self.committed), provisional, decode_ms)

    def _advance(self, words: list[Word]):
        # Cut between the last two committed words, at the midpoint of the gap,
        # keeping the last committed word as overlap so its boundary is not decoded twice.
        cut_index = self.window_committed - 1
        cut = (words[cut_index - 1].end + words[cut_index].start) / 2
        self.anchor += int(cut * RATE)
        # The kept word is already committed; drop it from the committed list so the
        # next agreement re-commits it from the new window instead of duplicating it.
        self.committed = self.committed[:-1]
        self.window_committed = 0
        self.previous = None

    def finish(self, full_pass_limit: float = 60.0) -> str:
        """Final transcript: one full decode of the utterance when it fits the model."""
        if len(self.audio) / RATE <= full_pass_limit:
            return self.stt.text(self.audio).strip()
        tail = " ".join(w.text for w in self.stt(self.audio[self.anchor:])[self.window_committed:])
        return " ".join(self.committed + [tail]).strip()


class TTS:
    """Kokoro v1.0 via kokoro-onnx."""

    def __init__(self, device: str = "cpu", quantization: str | None = "int8", threads: int | None = None,
                 voice: str = "af_heart"):
        from kokoro_onnx import Kokoro
        name = "kokoro-v1.0.int8.onnx" if quantization == "int8" else "kokoro-v1.0.onnx"
        session = ort.InferenceSession(str(MODELS / name), session_options(threads), providers=providers(device))
        self.kokoro = Kokoro.from_session(session, str(MODELS / "voices-v1.0.bin"))
        self.voice = voice

    def __call__(self, text: str) -> tuple[np.ndarray, int]:
        return self.kokoro.create(text, voice=self.voice, lang="en-us")

    def stream(self, text: str):
        return self.kokoro.create_stream(text, voice=self.voice, lang="en-us")
