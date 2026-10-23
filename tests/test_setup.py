import io
import subprocess
import sys
import wave
from pathlib import Path

import numpy as np
import pytest
from rich.console import Console

from agent_talk import setup
from agent_talk.config import Settings
from agent_talk.prompts import Abort, Downloads, Option, Prompter
from agent_talk.speech import hardware, preview, voices
from agent_talk.speech.fakes import FakeSynthesizer
from agent_talk.speech.models import Model, ModelFile

LAPTOP = hardware.Hardware("Linux", "x86_64", 16, 32000, [hardware.Gpu("NVIDIA GeForce RTX 4060 Laptop GPU", 8188, 7900)])


class Scripted(Prompter):
    """Answers questions from a list, and records what was asked."""

    def __init__(self, *answers):
        super().__init__(True, Console(file=io.StringIO(), width=120))
        self.answers = list(answers)
        self.asked: list[tuple[str, list[Option], object]] = []

    def select(self, message, options, default=None):
        self.asked.append((message, options, default))
        answer = self.answers.pop(0)
        assert answer in [o.value for o in options if not o.disabled], (message, answer)
        return answer

    def confirm(self, message, default=True):
        self.asked.append((message, [], default))
        return self.answers.pop(0)

    def text(self, message, default="", validate=None):
        self.asked.append((message, [], default))
        answer = self.answers.pop(0)
        return default if answer is None else answer

    def output(self) -> str:
        return self.console.file.getvalue()

    def options(self, message) -> dict:
        return {o.value: o for asked, options, _ in self.asked if asked == message for o in options}


def machine(hw=LAPTOP, speech_loaded=False) -> setup.Machine:
    return setup.Machine(hw, speech_loaded)


# The prompter

def test_without_a_terminal_each_question_takes_its_default():
    ui = Prompter(False, Console(file=io.StringIO(), width=120))
    options = [Option("a", "A", disabled="no"), Option("b", "B"), Option("c", "C")]
    assert ui.select("Pick", options, "c") == "c"
    assert ui.select("Pick", options, "a") == "b"  # a disabled default falls to the first enabled
    assert ui.confirm("Go?") is True and ui.text("URL", "http://x") == "http://x"
    assert "Pick C" in ui.console.file.getvalue()


def test_option_titles_line_up_with_their_tags():
    first = Prompter._title(Option(1, "GPU (CUDA)", "2.9 GB", tags=("recommended", "current")), 12, 8)
    second = Prompter._title(Option(2, "CPU", "0.8 GB"), 12, 8)
    assert first == "GPU (CUDA)  2.9 GB  recommended  current"
    assert second == "CPU         0.8 GB"


def test_download_bars_add_up_across_files():
    model = Model("m", "stt", "MIT", (ModelFile("a", "", "", 100), ModelFile("b", "", "", 300)))
    updates = []

    class Progress:
        def add_task(self, label, total):
            assert total == 400
            return 1

        def update(self, task, completed):
            updates.append(completed)

    bar = Downloads(Progress()).bar("m", model)
    bar(model.files[0], 100)
    bar(model.files[1], 300)
    assert updates == [100, 400]


# Speech

def test_speech_lists_both_tiers_and_marks_the_recommendation():
    ui = Scripted("cpu")
    assert setup._choose_speech(ui, machine(), None) == "cpu"  # not the recommendation, but allowed
    message, options, default = ui.asked[0]
    assert [o.value for o in options] == ["cuda", "cpu"] and default == "cuda"
    assert "recommended" in options[0].tags and "voice delay" in options[0].detail


def test_speech_greys_out_the_gpu_without_nvidia():
    ui = Scripted("cpu")
    setup._choose_speech(ui, machine(hw=hardware.Hardware("Darwin", "arm64", 8, 16000)), None)
    gpu = ui.options("Speech runs on")["cuda"]
    assert gpu.disabled == "needs Linux and an NVIDIA GPU"


def test_a_second_run_preselects_the_current_tier():
    ui = Scripted("cpu")
    setup._choose_speech(ui, machine(), "cpu")
    _, options, default = ui.asked[0]
    assert default == "cpu" and options[1].tags == ("current",)


def test_a_checkout_continues_setup_in_the_same_interpreter(tmp_path, monkeypatch):
    monkeypatch.setattr(sys, "prefix", str(tmp_path))  # no uv receipt here
    command, rerun = setup._install_command("cpu")
    assert command[:2] == ["uv", "sync"] and command[-2:] == ["--extra", "cpu"]
    assert rerun == [sys.executable, "-m", "agent_talk"]


# The voice

class Speech:
    def __init__(self):
        self.tts = FakeSynthesizer()


def test_a_voice_is_chosen_by_ear(monkeypatch):
    played = []
    monkeypatch.setattr(preview, "player", lambda: ["play"])
    monkeypatch.setattr(preview, "play", lambda audio, rate, command: played.append(speech.tts.voice))
    speech = Speech()
    ui = Scripted("bf_emma", "again", "other", "af_bella", "keep")
    assert setup._choose_voice(ui, speech, None) == "af_bella"
    assert played == ["bf_emma", "bf_emma", "af_bella"]
    assert speech.tts.spoken == [voices.PREVIEW] * 3
    _, options, default = ui.asked[0]
    assert default == voices.DEFAULT and "recommended" in options[0].tags


def test_more_voices_shows_the_lower_grades(monkeypatch):
    monkeypatch.setattr(preview, "player", lambda: None)
    ui = Scripted(setup.MORE, "am_adam")
    assert setup._choose_voice(ui, Speech(), None) == "am_adam"
    first, everything = (len(options) for _, options, _ in ui.asked)
    assert first < everything == len(voices.VOICES)
    assert "can't be previewed" in ui.output()


def test_without_a_terminal_the_current_voice_stays(monkeypatch):
    ui = Prompter(False, Console(file=io.StringIO()))
    assert setup._choose_voice(ui, Speech(), "bm_george") == "bm_george"


def test_voices_are_english_and_graded():
    assert all(v.id[0] in "ab" for v in voices.VOICES) and len(voices.VOICES) == 28
    emma = next(v for v in voices.VOICES if v.id == "bf_emma")
    assert (emma.name, emma.accent, emma.gender, emma.good) == ("Emma", "British", "female", True)


def test_previews_are_written_as_16_bit_wav(tmp_path):
    path = tmp_path / "preview.wav"
    preview.write_wav(path, np.array([0.0, 0.5, -1.5], dtype=np.float32), 24000)
    with wave.open(str(path)) as audio:
        assert (audio.getframerate(), audio.getsampwidth(), audio.getnframes()) == (24000, 2, 3)
        assert np.frombuffer(audio.readframes(3), "<i2").tolist() == [0, 16383, -32767]


# The whole run

def test_leaving_with_ctrl_c_says_how_to_continue(settings, paths, monkeypatch, capsys):
    def stop(*args):
        raise Abort()

    monkeypatch.setattr(setup, "_run", stop)
    assert setup.run(settings, paths, None, assume_yes=True) == 130
    assert "pick up where you left off" in capsys.readouterr().out
