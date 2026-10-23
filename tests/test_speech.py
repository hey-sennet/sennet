import asyncio
import hashlib
import os
import sys
import threading
import time
from pathlib import Path

import numpy as np
import pytest

from sennet import setup
from sennet.config import Settings
from sennet.speech import Speech, hardware, models
from sennet.speech.base import AudioBuffer
from sennet.speech.fakes import FakeRecognizer, FakeSynthesizer, FakeVAD
from sennet.speech.stt import StreamingTranscriber
from sennet.speech.tts import VOCAB, split, tokens, trim
from sennet.speech.worker import CAPTION, FINAL, Worker


# Inference worker

async def test_worker_runs_final_passes_before_captions():
    worker = Worker("test")
    gate, order = threading.Event(), []
    blocker = asyncio.ensure_future(worker.run(gate.wait))
    await asyncio.sleep(0.05)  # the worker is now busy with the blocker
    caption = asyncio.ensure_future(worker.run(order.append, "caption", priority=CAPTION))
    final = asyncio.ensure_future(worker.run(order.append, "final", priority=FINAL))
    await asyncio.sleep(0.01)
    gate.set()
    await asyncio.gather(blocker, caption, final)
    assert order == ["final", "caption"]
    worker.close()


async def test_worker_hands_errors_to_the_caller():
    worker = Worker("test")

    def fail():
        raise ValueError("bad audio")

    with pytest.raises(ValueError, match="bad audio"):
        await worker.run(fail)
    assert await worker.run(lambda: 42) == 42
    worker.close()


# Model files

def file_model(tmp_path: Path, content: bytes, sha: str | None = None) -> models.Model:
    source = tmp_path / "source.onnx"
    source.write_bytes(content)
    return models.Model("test-model", "vad", "MIT", (
        models.ModelFile("model.onnx", source.as_uri(), sha or hashlib.sha256(content).hexdigest(), len(content)),
    ))


def test_fetch_downloads_and_verifies(tmp_path):
    model = file_model(tmp_path, b"weights")
    root = tmp_path / "models"
    assert not models.present(root, model)
    models.fetch(root, model)
    assert models.present(root, model)
    assert (root / "test-model" / "model.onnx").read_bytes() == b"weights"


def test_fetch_rejects_a_bad_checksum(tmp_path):
    model = file_model(tmp_path, b"weights", sha="0" * 64)
    root = tmp_path / "models"
    with pytest.raises(models.ChecksumError):
        models.fetch(root, model)
    assert list((root / "test-model").iterdir()) == []


def test_fetch_keeps_files_that_already_match(tmp_path):
    model = file_model(tmp_path, b"weights")
    root = tmp_path / "models"
    models.fetch(root, model)
    (tmp_path / "source.onnx").unlink()  # a second download would now fail
    models.fetch(root, model)
    assert models.present(root, model)


def test_every_tier_has_all_three_roles():
    for chosen in models.MODELS.values():
        assert {role: m.role for role, m in chosen.items()} == {"vad": "vad", "stt": "stt", "tts": "tts"}


# Hardware recommendation

def machine(**overrides) -> hardware.Hardware:
    values = dict(system="Linux", machine="x86_64", cpu_threads=16, ram_mb=16000, gpus=[])
    return hardware.Hardware(**{**values, **overrides})


def test_recommends_the_gpu_with_enough_free_memory():
    advice = hardware.recommend(machine(gpus=[hardware.Gpu("RTX 4060", 8188, 7600)]))
    assert advice.device == "cuda"
    assert "RTX 4060" in advice.reason and not advice.warnings


def test_recommends_the_cpu_when_the_gpu_is_full():
    advice = hardware.recommend(machine(gpus=[hardware.Gpu("RTX 3050", 4096, 3000)]))
    assert advice.device == "cpu"
    assert "too little free" in advice.reason


def test_recommends_the_cpu_without_an_nvidia_gpu():
    assert hardware.recommend(machine()).device == "cpu"
    assert "Apple Silicon" in hardware.recommend(machine(system="Darwin", machine="arm64")).reason


def test_warns_about_small_machines():
    advice = hardware.recommend(machine(cpu_threads=2, ram_mb=4000))
    assert len(advice.warnings) == 2


# Text to speech helpers

def test_split_keeps_batches_under_the_limit_at_punctuation():
    phonemes = "ðɪs ɪz wʌn, " * 60
    batches = split(phonemes, limit=100)
    assert all(len(b) <= 100 for b in batches)
    assert all(b.endswith(",") for b in batches[:-1])
    assert "".join(batches).replace(" ", "") == phonemes.replace(" ", "")


def test_tokens_drop_symbols_outside_the_vocabulary():
    assert tokens("a☃b") == [VOCAB["a"], VOCAB["b"]]


def test_trim_removes_quiet_ends():
    audio = np.concatenate([np.zeros(24000), np.full(2400, 0.5), np.zeros(24000)]).astype(np.float32)
    trimmed = trim(audio)
    assert 2400 <= len(trimmed) <= 2400 + 2 * 240 + 1
    assert len(trim(np.zeros(100, dtype=np.float32))) == 0


# Fakes and the streaming transcriber

def test_streaming_transcriber_commits_words_two_passes_agree_on():
    stream = StreamingTranscriber(FakeRecognizer("one two three four", word_s=0.25))
    audio = AudioBuffer()
    audio.append(np.zeros(16000 // 2, dtype=np.float32))  # two words heard
    first = stream.tick(audio.view())
    assert (first.committed, first.provisional) == ("", "one two")
    audio.append(np.zeros(16000 // 4, dtype=np.float32))
    second = stream.tick(audio.view())
    assert (second.committed, second.provisional) == ("one two", "three")


def test_audio_buffer_grows_without_changing_earlier_views():
    audio = AudioBuffer(capacity=4)
    audio.append(np.arange(3, dtype=np.float32))
    early = audio.view()
    audio.append(np.arange(3, 10, dtype=np.float32))
    assert list(early) == [0, 1, 2]
    assert list(audio.view()) == list(range(10))
    assert list(audio.view(2, 5)) == [2, 3, 4] and len(audio) == 10


def test_fake_vad_hears_sound_not_silence():
    vad = FakeVAD().stream()
    assert vad(np.zeros(512, dtype=np.float32)) == 0.0
    assert vad(np.full(512, 0.2, dtype=np.float32)) == 1.0


# The shared service

async def test_speech_with_fakes_is_ready():
    speech = Speech.with_engines(FakeVAD(), FakeRecognizer(), FakeSynthesizer())
    assert speech.snapshot() == {"stt": "ready", "tts": "ready"}
    audio = await speech.run("tts", speech.tts.synthesize, "hello there")
    assert len(audio) == int(24000 * 0.2)
    speech.close()


async def test_speech_without_models_is_unavailable_and_says_why(tmp_path):
    speech = Speech()
    await speech.load(tmp_path / "models", "cpu", 4, "af_heart")
    assert speech.snapshot() == {"stt": "unavailable", "tts": "unavailable"}
    assert "sennet setup" in speech.detail["stt"]
    speech.close()


def test_the_daemon_starts_without_models(settings, paths):
    from fastapi.testclient import TestClient

    from sennet.server import create_app

    from conftest import paired

    with paired(TestClient(create_app(settings, paths))) as client:
        assert client.get("/api/state").status_code == 200
        speech = client.app.state.speech
        deadline = time.monotonic() + 10
        while speech.status["stt"] == "loading" and time.monotonic() < deadline:
            time.sleep(0.02)
        assert speech.snapshot() == {"stt": "unavailable", "tts": "unavailable"}


def test_config_rejects_an_unknown_device(tmp_path):
    config = tmp_path / "config.toml"
    config.write_text('[speech]\ndevice = "tpu"\n')
    with pytest.raises(ValueError, match="speech.device"):
        Settings.load(config)


# Setup

def test_setup_needs_a_terminal_or_yes(settings, paths, monkeypatch, capsys):
    monkeypatch.setattr(sys.stdin, "isatty", lambda: False)
    assert setup.run(settings, paths, None, assume_yes=False) == 2
    assert "--yes" in capsys.readouterr().err


def test_setup_reinstalls_a_uv_tool_with_the_chosen_extra(tmp_path, monkeypatch):
    (tmp_path / "uv-receipt.toml").write_text(
        '[tool]\nrequirements = [{ name = "sennet", specifier = ">=0.1" }]\n')
    monkeypatch.setattr(sys, "prefix", str(tmp_path))
    command, rerun = setup._install_command("cuda")
    assert command == ["uv", "tool", "install", "--reinstall", "sennet[cuda]>=0.1"]
    assert rerun


def test_setup_leaves_a_git_install_to_the_user(tmp_path, monkeypatch):
    (tmp_path / "uv-receipt.toml").write_text(
        '[tool]\nrequirements = [{ name = "sennet", git = "https://example.com/sennet" }]\n')
    monkeypatch.setattr(sys, "prefix", str(tmp_path))
    assert setup._install_command("cuda") == (None, None)


# Real models (opt-in: SENNET_MODELS=<models dir> uv run pytest -m models)

@pytest.mark.models
@pytest.mark.parametrize("device", ["cpu", "cuda"])
async def test_real_engines_hear_what_they_say(device):
    from sennet.speech.runtime import cuda_available

    if device == "cuda" and not cuda_available():
        pytest.skip("no CUDA runtime")
    speech = Speech()
    await speech.load(Path(os.environ["SENNET_MODELS"]), device, 4, "af_heart")
    try:
        assert speech.snapshot() == {"stt": "ready", "tts": "ready"}, speech.detail
        audio = await speech.run("tts", speech.tts.synthesize, setup.SELF_TEST)
        heard = await speech.run("stt", speech.stt.text, setup._resample(audio, speech.tts.rate, 16000))
        assert setup._words(heard) == setup._words(setup.SELF_TEST)
    finally:
        speech.close()
