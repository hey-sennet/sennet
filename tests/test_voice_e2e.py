"""End to end on the real models (opt-in: SENNET_MODELS=<models dir> uv run pytest -m models).

Timings come from `python tests/voice_bench.py`; this only checks the path works.
"""
import os
from pathlib import Path

import pytest

from sennet import setup
from voice_bench import COMMAND, bench


@pytest.mark.models
@pytest.mark.timeout(180)
@pytest.mark.parametrize("device", ["cpu", "cuda"])
def test_a_spoken_command_gets_a_spoken_reply(device):
    from sennet.speech.runtime import cuda_available

    if device == "cuda" and not cuda_available():
        pytest.skip("no CUDA runtime")
    (run,) = bench(Path(os.environ["SENNET_MODELS"]), device, runs=1)
    assert setup._words(run.transcript) == setup._words(COMMAND)
    assert setup._words(run.reply) == setup._words(f"You said: {COMMAND}")
    assert run.audio_s > 1.0 and run.captions > 3
