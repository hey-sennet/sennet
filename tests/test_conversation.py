"""Open-mic conversations over /api/voice (docs/m7-sessions.md), on fake speech engines,
and once on the real models (opt-in: SENNET_MODELS=<models dir> uv run pytest -m models)."""
import os
import time
from pathlib import Path

import numpy as np

import pytest
from fastapi.testclient import TestClient

from sennet.server import create_app
from sennet.speech import Speech
from sennet.speech.fakes import FakeRecognizer, FakeSynthesizer, FakeVAD
from sennet.voice import conversation
from conftest import HEADERS, paired, wait_for
from test_voice import SPEECH_FRAME, Voice, client, nothing_spoken, session, speak, speech, voice  # noqa: F401  (fixtures)

pytestmark = pytest.mark.timeout(20)

QUIET_FRAME = b"\x00" * 640  # 20 ms


def sound(voice, seconds: float):
    for _ in range(round(seconds / 0.02)):
        voice.ws.send_bytes(SPEECH_FRAME)


def quiet(voice, seconds: float):
    for _ in range(round(seconds / 0.02)):
        voice.ws.send_bytes(QUIET_FRAME)


def open_mic(voice, **target):
    voice.send(type="open_mic", **target)
    assert voice.next("mic") == {"type": "mic", "open": True}


def say(voice, speech, text: str, seconds: float = 1.0) -> dict:
    """Speak, then fall quiet long enough for the turn to end; returns its final."""
    speech.stt.transcript = text
    sound(voice, seconds)
    quiet(voice, 1.0)
    return voice.next("final")


@pytest.fixture(autouse=True)
def open_gate(speech):
    speech.tts.gate.set()


def test_a_turn_starts_and_ends_without_the_button(client, voice, speech, project):
    open_mic(voice, project_id=project["id"])
    assert voice.next("voice_state")["state"] == "open"
    speech.stt.transcript = "run the tests in backend"
    sound(voice, 1.0)
    quiet(voice, 1.0)
    turn = voice.next("turn")["turn"]
    assert voice.next("voice_state") == {"type": "voice_state", "state": "listening", "turn": turn}
    assert voice.next("voice_state")["state"] == "transcribing"
    final = voice.next("final")
    assert (final["turn"], final["outcome"]) == (turn, "dispatched")
    assert voice.next("voice_state")["state"] == "open"
    task = wait_for(client, final["task_id"], lambda t: t["messages"])
    assert task["messages"][0]["text"] == "run the tests in backend"


def test_short_sounds_dont_open_a_turn(voice, speech, project):
    open_mic(voice, project_id=project["id"])
    for _ in range(5):  # a cough, a click
        sound(voice, 0.06)
        quiet(voice, 0.5)
    final = say(voice, speech, "run the tests")
    assert (final["turn"], final["outcome"]) == ("s1", "dispatched")


def test_speech_back_within_the_grace_is_the_same_turn(voice, speech, project):
    open_mic(voice, project_id=project["id"])
    speech.stt.transcript = "update react to the latest version"
    sound(voice, 1.0)
    quiet(voice, 0.3)  # smart-turn says finished at 224 ms; the speaker carries on
    sound(voice, 0.5)
    quiet(voice, 1.0)
    seen = voice.until("final")
    assert [m["turn"] for m in seen if m["type"] == "turn"] == ["s1"]
    assert seen[-1]["outcome"] == "dispatched"
    assert [m["state"] for m in seen if m["type"] == "voice_state"] == \
        ["open", "listening", "transcribing", "listening", "transcribing"]


def test_an_unfinished_pause_waits_for_the_fallback(voice, speech, project):
    open_mic(voice, project_id=project["id"])
    speech.turn.probability = 0.1
    speech.stt.transcript = "check the router and"
    sound(voice, 1.0)
    quiet(voice, 2.0)
    assert voice.next("final")["outcome"] == "dispatched"
    assert speech.turn.calls == 1  # never asked twice about the same pause


def test_pressing_talk_holds_the_turn_until_release(voice, speech, project):
    open_mic(voice, project_id=project["id"])
    voice.send(type="start", turn="t9", project_id=project["id"])
    assert voice.next("turn") == {"type": "turn", "turn": "t9", "manual": True}
    sound(voice, 0.5)
    quiet(voice, 2.0)  # long past the fallback: only letting go ends it
    voice.send(type="end", turn="t9")
    final = voice.next("final")
    assert (final["turn"], final["outcome"]) == ("t9", "dispatched")


def test_filler_alone_sends_nothing(client, voice, speech, project):
    open_mic(voice, project_id=project["id"])
    assert say(voice, speech, "Um.")["outcome"] == "empty"
    assert client.get("/api/state").json()["tasks"] == []


def test_a_new_selection_takes_the_next_turn(client, voice, speech, project):
    task = client.post("/api/tasks", json={"project_id": project["id"], "text": "add a test"}).json()
    wait_for(client, task["id"], lambda t: t["status"] == "completed")
    open_mic(voice, project_id=project["id"])
    voice.send(type="target", task_id=task["id"])
    assert say(voice, speech, "and run it")["task_id"] == task["id"]


def test_closing_the_mic(voice, project):
    open_mic(voice, project_id=project["id"])
    voice.send(type="close_mic")
    assert voice.next("mic") == {"type": "mic", "open": False, "reason": "closed"}
    assert voice.next("voice_state", state="idle")["type"] == "voice_state"


def test_closing_during_the_grace_still_sends_the_turn(voice, speech, project):
    open_mic(voice, project_id=project["id"])
    speech.stt.transcript = "run the tests"
    sound(voice, 1.0)
    quiet(voice, 0.3)  # smart-turn has said finished; with nothing more sent, the grace is still running
    voice.next("voice_state", state="transcribing")
    voice.send(type="close_mic")
    assert voice.next("final")["outcome"] == "dispatched"


def test_a_turn_that_never_ends_is_dropped(voice, speech, project, monkeypatch):
    monkeypatch.setattr(conversation, "TURN_LIMIT_S", 1.0)
    open_mic(voice, project_id=project["id"])
    sound(voice, 1.5)  # a television
    error = voice.next("voice_error")
    assert (error["code"], error["turn"]) == ("too_long", "s1")


def test_a_quiet_conversation_closes_itself(voice, project, monkeypatch):
    monkeypatch.setattr(conversation, "IDLE_S", 0.1)
    open_mic(voice, project_id=project["id"])
    assert voice.next("mic") == {"type": "mic", "open": False, "reason": "idle"}


def test_listening_to_a_reply_is_not_idle(client, voice, monkeypatch, project):
    monkeypatch.setattr(conversation, "IDLE_S", 0.5)
    open_mic(voice, project_id=project["id"])
    started = time.monotonic()
    speak(client, " ".join(["word"] * 30))  # three seconds of reply
    voice.next("mic")
    assert time.monotonic() - started > 2.5


def test_a_failure_while_listening_closes_the_conversation_and_says_so(voice, project, monkeypatch):
    def broken(*args, **kwargs):
        raise RuntimeError("boom")

    monkeypatch.setattr(conversation.Endpointer, "frame", broken)
    open_mic(voice, project_id=project["id"])
    sound(voice, 0.1)
    assert voice.next("mic") == {"type": "mic", "open": False, "reason": "error"}


def test_a_conversation_needs_turn_detection(settings, paths, codex_log):
    speech = Speech.with_engines(FakeVAD(), FakeRecognizer(), FakeSynthesizer())
    with paired(TestClient(create_app(settings, paths, speech=speech), headers=HEADERS)) as client:
        with client.websocket_connect("/api/voice") as ws:
            voice = Voice(ws)
            voice.send(type="open_mic", project_id=1)
            assert voice.next("voice_error")["code"] == "turn_unavailable"
    speech.close()


# Barge-in

def reply_playing(client, voice, text="This reply runs on for a good few seconds, long enough to talk over."):
    speak(client, text)
    voice.next("speech")


def test_talking_over_a_reply_stops_it_and_sends_the_request(client, voice, speech, project):
    open_mic(voice, project_id=project["id"])
    reply_playing(client, voice)
    speech.stt.transcript = "no, run the tests in backend first"
    sound(voice, 0.5)
    quiet(voice, 1.0)
    seen = voice.until("final")
    assert {"type": "barge_in", "generation": 1} in seen
    assert seen[-1]["outcome"] == "dispatched"
    assert [t["id"] for t in client.get("/api/state").json()["tasks"]] == [seen[-1]["task_id"]]


def test_a_wait_over_a_reply_only_stops_it(client, voice, speech, project):
    open_mic(voice, project_id=project["id"])
    reply_playing(client, voice)
    speech.stt.transcript = "Hang on."
    sound(voice, 0.4)
    quiet(voice, 1.0)
    assert voice.next("final")["outcome"] == "interrupted"
    assert nothing_spoken(voice)
    assert client.get("/api/state").json()["tasks"] == []


def test_stop_over_a_reply_stops_it(client, voice, speech, project):
    open_mic(voice, project_id=project["id"])
    reply_playing(client, voice)
    speech.stt.transcript = "Stop."
    sound(voice, 0.4)
    quiet(voice, 1.0)
    assert voice.next("final")["outcome"] == "interrupted"


def test_sennets_own_voice_is_not_sent(client, voice, speech, project):
    task = client.post("/api/tasks", json={"project_id": project["id"], "text": "add a test"}).json()
    wait_for(client, task["id"], lambda t: t["status"] == "completed")
    open_mic(voice, task_id=task["id"])
    reply_playing(client, voice, "Should I cancel that and start again from the top?")
    speech.stt.transcript = "cancel that"  # what the microphone picked up of the reply
    sound(voice, 0.4)
    quiet(voice, 1.0)
    assert voice.next("final")["outcome"] == "empty"
    messages = client.get(f"/api/tasks/{task['id']}").json()["messages"]
    assert [m["text"] for m in messages if m["role"] == "user"] == ["add a test"]


def test_a_barge_in_hushes_the_rest_of_the_agents_turn(client, voice, speech, project):
    open_mic(voice, project_id=project["id"])
    final = say(voice, speech, "two:Let me look through the logs for that, it may take a moment.|Here is what I found.")
    assert voice.next("speech")["text"].startswith("Let me look")
    speech.stt.transcript = "Stop."
    sound(voice, 0.4)
    quiet(voice, 1.0)
    assert voice.next("final")["outcome"] == "interrupted"
    wait_for(client, final["task_id"], lambda t: t["status"] == "completed")
    assert nothing_spoken(voice)  # "Here is what I found." is in the timeline, not said


def test_a_new_request_after_a_barge_in_is_spoken(client, voice, speech, project):
    open_mic(voice, project_id=project["id"])
    final = say(voice, speech, "two:Let me look through the logs for that, it may take a moment.|Found it.")
    voice.next("speech")
    speech.stt.transcript = "Stop."
    sound(voice, 0.4)
    quiet(voice, 1.0)
    voice.next("final")
    wait_for(client, final["task_id"], lambda t: t["status"] == "completed")
    voice.send(type="target", task_id=final["task_id"])
    say(voice, speech, "and the next one")
    assert voice.next("speech")["text"] == "You said: and the next one."


def test_after_a_barge_in_the_next_turn_is_heard_as_usual(client, voice, speech, project):
    open_mic(voice, project_id=project["id"])
    reply_playing(client, voice)
    speech.stt.transcript = "wait"
    sound(voice, 0.3)
    quiet(voice, 1.0)
    assert voice.next("final")["outcome"] == "interrupted"
    assert say(voice, speech, "run the tests in backend first")["outcome"] == "dispatched"




@pytest.mark.models
@pytest.mark.timeout(240)
def test_a_pause_mid_request_is_one_turn_on_the_real_models(settings, paths, codex_log, tmp_path):
    """A request with a breath in it ("…unit tests [pause] and tell me…") goes out whole."""
    from sennet import setup
    from voice_bench import load_speech

    speech = load_speech(Path(os.environ["SENNET_MODELS"]), "cpu")
    if speech.status["turn"] != "ready":
        pytest.skip(speech.detail["turn"])
    first = setup._resample(speech.tts.synthesize("Run the unit tests"), speech.tts.rate, 16000)
    second = setup._resample(speech.tts.synthesize("and tell me which ones failed."), speech.tts.rate, 16000)
    gap = np.zeros(int(16000 * 0.35), dtype=np.float32)
    audio = np.concatenate([gap, first, gap, second, np.zeros(16000 * 2, dtype=np.float32)])
    pcm = (np.clip(audio, -1, 1) * 32767).astype("<i2")
    directory = tmp_path / "backend"
    directory.mkdir()
    try:
        with paired(TestClient(create_app(settings, paths, speech=speech), headers=HEADERS)) as client:
            project = client.post("/api/projects", json={"path": str(directory), "name": "backend"}).json()
            with client.websocket_connect("/api/voice") as ws:
                voice = Voice(ws)
                open_mic(voice, project_id=project["id"])
                for i in range(0, len(pcm), 320):  # in real time, as a browser sends it
                    ws.send_bytes(pcm[i:i + 320].tobytes())
                    time.sleep(0.02)
                seen = voice.until("final")
        finals = [m for m in seen if m["type"] == "final"]
        assert len([m for m in seen if m["type"] == "turn"]) == 1
        assert finals[0]["outcome"] == "dispatched"
        assert setup._words(finals[0]["text"]) == setup._words("Run the unit tests and tell me which ones failed.")
    finally:
        speech.close()


@pytest.mark.models
@pytest.mark.timeout(240)
def test_talking_over_a_reply_stops_it_on_the_real_models(settings, paths, codex_log, tmp_path):
    from sennet import setup
    from voice_bench import load_speech

    speech = load_speech(Path(os.environ["SENNET_MODELS"]), "cpu")
    if speech.status["turn"] != "ready":
        pytest.skip(speech.detail["turn"])
    words = setup._resample(speech.tts.synthesize("Stop."), speech.tts.rate, 16000)
    pcm = (np.clip(np.concatenate([words, np.zeros(16000, dtype=np.float32)]), -1, 1) * 32767).astype("<i2")
    directory = tmp_path / "backend"
    directory.mkdir()
    try:
        with paired(TestClient(create_app(settings, paths, speech=speech), headers=HEADERS)) as client:
            project = client.post("/api/projects", json={"path": str(directory), "name": "backend"}).json()
            with client.websocket_connect("/api/voice") as ws:
                voice = Voice(ws)
                open_mic(voice, project_id=project["id"])
                speak(client, "This is a long reply that goes on for a while, so there is time to talk over it.")
                voice.next("speech")
                for i in range(0, len(pcm), 320):
                    ws.send_bytes(pcm[i:i + 320].tobytes())
                    time.sleep(0.02)
                seen = voice.until("final")
        assert any(m["type"] == "barge_in" for m in seen)
        assert seen[-1]["outcome"] == "interrupted"
    finally:
        speech.close()
