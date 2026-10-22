"""Turn recognition: voice activity, captions and the final pass (HLD §6.2)."""
import asyncio

import numpy as np
import pytest

from agent_talk.speech import Speech
from agent_talk.speech.fakes import FakeRecognizer, FakeSynthesizer, FakeVAD
from agent_talk.voice import recognition
from agent_talk.voice.recognition import Recognition, caption_period

pytestmark = pytest.mark.timeout(30)

RATE = 16000


def loud(seconds: float) -> np.ndarray:
    return np.full(int(RATE * seconds), 0.2, dtype=np.float32)


def quiet(seconds: float) -> np.ndarray:
    return np.zeros(int(RATE * seconds), dtype=np.float32)


class Recorder(FakeRecognizer):
    """Names each final pass by its order and keeps the lengths it was given."""

    def __init__(self):
        super().__init__("one two three four five six seven eight")
        self.passes: list[int] = []

    def text(self, audio):
        self.passes.append(len(audio))
        return f"part{len(self.passes)}"


@pytest.fixture
def stt():
    return Recorder()


def make(stt, incremental: bool):
    speech = Speech.with_engines(FakeVAD(), stt, FakeSynthesizer(), incremental=incremental)
    captions = []

    async def on_caption(caption):
        captions.append((caption.committed, caption.provisional))

    return speech, Recognition(speech, on_caption), captions


async def feed(turn: Recognition, *parts: np.ndarray, frame: float = 0.02):
    """Feed audio in 20 ms frames, as the socket does."""
    step = int(RATE * frame)
    for part in parts:
        for i in range(0, len(part), step):
            await turn.add(part[i:i + step])


async def test_a_silent_turn_is_empty_without_decoding(stt):
    speech, turn, _ = make(stt, incremental=False)
    await feed(turn, quiet(1.5))
    assert await turn.finish() == ""
    assert stt.passes == []
    speech.close()


async def test_gpu_finals_are_one_pass_over_the_whole_turn(stt):
    speech, turn, _ = make(stt, incremental=False)
    await feed(turn, loud(1.2), quiet(0.6), loud(1.0))
    assert await turn.finish() == "part1"
    assert stt.passes == [int(RATE * 2.8)]
    speech.close()


async def test_cpu_finalizes_a_segment_during_the_pause(stt):
    speech, turn, _ = make(stt, incremental=True)
    await feed(turn, loud(1.2), quiet(0.6))
    await asyncio.sleep(0.05)
    assert len(stt.passes) == 1, "the first segment is decoded while the user is still talking"
    await feed(turn, loud(1.0))
    assert await turn.finish() == "part1 part2"
    assert sum(stt.passes) == int(RATE * 2.8)
    # The cut falls inside the pause, not in the speech on either side.
    assert int(RATE * 1.2) < stt.passes[0] < int(RATE * 1.8)
    speech.close()


async def test_short_pauses_and_short_speech_do_not_cut(stt):
    speech, turn, _ = make(stt, incremental=True)
    await feed(turn, loud(1.2), quiet(0.3), loud(0.5), quiet(0.6), loud(0.5))
    assert len(stt.passes) == 1  # only after the 1.2 + 0.3 + 0.5 s run is there a long enough pause
    assert await turn.finish() == "part1 part2"
    speech.close()


async def test_long_turns_are_decoded_in_pieces_even_on_gpu(stt):
    speech, turn, _ = make(stt, incremental=False)
    await feed(turn, loud(61), frame=0.1)
    assert await turn.finish() == "part1 part2"
    assert sorted(stt.passes) == [int(RATE * 1), int(RATE * 60)]
    speech.close()


async def test_captions_preview_the_words_and_commit_them(stt, monkeypatch):
    monkeypatch.setattr(recognition, "CAPTION_PERIOD_S", 0.05)
    speech, turn, captions = make(stt, incremental=False)
    await feed(turn, loud(0.5))
    await asyncio.sleep(0.15)
    await feed(turn, loud(0.25))
    await asyncio.sleep(0.15)
    await turn.finish()
    assert captions[0] == ("", "one two")
    assert captions[-1] == ("one two", "three")
    speech.close()


async def test_captions_skip_ticks_without_new_audio(stt, monkeypatch):
    monkeypatch.setattr(recognition, "CAPTION_PERIOD_S", 0.02)
    speech, turn, captions = make(stt, incremental=False)
    await feed(turn, loud(0.5))
    await asyncio.sleep(0.2)
    assert stt.calls.count("words") == 1
    await turn.finish()
    speech.close()


def test_slow_caption_decodes_stretch_the_period():
    assert caption_period(0.05) == 0.25
    assert caption_period(0.4) == pytest.approx(0.6)


async def test_cancel_stops_background_passes(stt):
    speech, turn, _ = make(stt, incremental=True)
    await feed(turn, loud(1.2), quiet(0.6))
    turn.cancel()
    await asyncio.sleep(0.05)
    assert turn.caption_job.cancelled()
    speech.close()


# Real models (opt-in: AGENT_TALK_MODELS=<models dir> uv run pytest -m models)

@pytest.mark.models
@pytest.mark.timeout(120)
@pytest.mark.parametrize("device", ["cpu", "cuda"])
async def test_real_turn_with_a_pause(device):
    import os
    from pathlib import Path

    from agent_talk import setup
    from agent_talk.speech.runtime import cuda_available

    if device == "cuda" and not cuda_available():
        pytest.skip("no CUDA runtime")
    speech = Speech()
    await speech.load(Path(os.environ["AGENT_TALK_MODELS"]), device, 4, "af_heart")
    first, second = "Please run the unit tests in the backend.", "Then tell me which ones failed."
    said = [setup._resample(await speech.run("tts", speech.tts.synthesize, s), speech.tts.rate, RATE)
            for s in (first, second)]
    captions = []

    async def on_caption(caption):
        captions.append(caption)

    turn = Recognition(speech, on_caption)
    await feed(turn, quiet(0.3), said[0])
    await asyncio.sleep(0.6)  # fed faster than real time; give the caption tick a turn
    await feed(turn, quiet(0.8), said[1], quiet(0.3))
    if device == "cpu":
        await asyncio.sleep(1.5)  # let the background pass over the first sentence finish
        assert len(turn.segments) == 1 and turn.segments[0].done()
    text = await turn.finish()
    speech.close()
    # Parakeet writes "backend" as "back end" or "back-end"; the rest must match exactly.
    heard = text.replace("back-end", "backend").replace("back end", "backend")
    assert setup._words(heard) == setup._words(f"{first} {second}")
    assert captions, "captions arrived while the turn was being fed"
