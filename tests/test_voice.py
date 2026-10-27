"""The /api/voice protocol (docs/voice-protocol.md), on fake speech engines."""
import asyncio
import json
import logging
import threading
import time

import numpy as np
import pytest
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from sennet.server import create_app
from sennet.speech import Speech
from sennet.speech.fakes import FakeRecognizer, FakeSynthesizer, FakeTurn, FakeVAD
from sennet.voice import frames
from sennet.voice.session import VoiceSession
from conftest import HEADERS, wait_for, paired

pytestmark = pytest.mark.timeout(20)

SPEECH_FRAME = (np.full(320, 0.2 * 32767)).astype("<i2").tobytes()  # 20 ms of loud audio


class Voice:
    """A test client for one voice socket, keeping binary frames apart from messages."""

    def __init__(self, ws):
        self.ws = ws
        self.audio: list[bytes] = []
        self.ready = self.next("ready")

    def send(self, **message):
        self.ws.send_json(message)

    def talk(self, turn: str = "t1", seconds: float = 1.0, **target):
        self.send(type="start", turn=turn, **target)
        for _ in range(int(seconds / 0.02)):
            self.ws.send_bytes(SPEECH_FRAME)
        self.send(type="end", turn=turn)

    def receive(self) -> dict:
        while True:
            message = self.ws.receive()
            if message.get("bytes") is not None:
                self.audio.append(message["bytes"])
                continue
            if "text" not in message:
                raise WebSocketDisconnect(message.get("code", 1000))
            return json.loads(message["text"])

    def next(self, kind: str, **match) -> dict:
        """The next message of this type, skipping others."""
        while True:
            message = self.receive()
            if message["type"] == kind and all(message.get(k) == v for k, v in match.items()):
                return message

    def until(self, kind: str, **match) -> list[dict]:
        """Every message up to and including the next one of this type."""
        seen = []
        while True:
            seen.append(self.receive())
            if seen[-1]["type"] == kind and all(seen[-1].get(k) == v for k, v in match.items()):
                return seen


class GatedSynthesizer(FakeSynthesizer):
    """Holds segments after the first `free` until `gate` is set."""

    def __init__(self, free: int = 1):
        super().__init__()
        self.free = free
        self.gate = threading.Event()

    def synthesize(self, text):
        if len(self.spoken) >= self.free:
            self.gate.wait(10)
        return super().synthesize(text)


@pytest.fixture
def speech():
    return Speech.with_engines(FakeVAD(), FakeRecognizer("run the tests in backend"), GatedSynthesizer(),
                               turn=FakeTurn())


@pytest.fixture
def client(settings, paths, codex_log, speech):
    with paired(TestClient(create_app(settings, paths, speech=speech), headers=HEADERS)) as client:
        yield client
    speech.tts.gate.set()
    speech.close()


@pytest.fixture
def voice(client):
    with client.websocket_connect("/api/voice") as ws:
        yield Voice(ws)


def session(client) -> VoiceSession:
    (only,) = client.app.state.voice.sessions
    return only


def speak(client, *segments, task_id=None):
    voice_session = session(client)
    voice_session.selected = task_id  # on screen: only its replies are spoken
    client.portal.call(lambda: voice_session.speak(list(segments), task_id=task_id))


# Session start

def test_ready_describes_the_session(voice):
    ready = voice.ready
    assert ready["generation"] == 0
    assert ready["input"] == {"rate": 16000, "format": "pcm_s16le", "channels": 1}
    assert ready["output"]["rate"] == 24000
    assert ready["speech"] == {"stt": "ready", "tts": "ready", "turn": "ready"}


def test_other_sites_cannot_connect(client):
    with pytest.raises(WebSocketDisconnect):
        with client.websocket_connect("/api/voice", headers={"origin": "https://evil.example"}) as ws:
            ws.receive_text()


# Turns

def test_a_turn_starts_a_task_in_the_project(client, voice, project):
    voice.send(type="start", turn="t1", project_id=project["id"])
    assert voice.next("stopped")["generation"] == 1
    assert voice.next("voice_state")["state"] == "listening"
    for _ in range(50):
        voice.ws.send_bytes(SPEECH_FRAME)
    voice.send(type="end", turn="t1")
    assert voice.next("voice_state")["state"] == "transcribing"
    final = voice.next("final")
    assert final == {"type": "final", "turn": "t1", "text": "run the tests in backend",
                     "outcome": "dispatched", "task_id": final["task_id"]}
    assert voice.next("voice_state")["state"] == "idle"
    task = wait_for(client, final["task_id"], lambda t: t["messages"])
    assert task["messages"][0]["text"] == "run the tests in backend"
    assert client.app.state.voice.owner(final["task_id"]) is session(client)


def test_the_agent_starts_while_the_request_is_still_being_said(client, voice, project, codex_log):
    voice.send(type="start", turn="t1", project_id=project["id"])
    assert voice.next("voice_state")["state"] == "listening"
    deadline = time.monotonic() + 10
    while not codex_log("thread/start"):
        assert time.monotonic() < deadline, "the agent wasn't started early"
        time.sleep(0.02)
    for _ in range(50):
        voice.ws.send_bytes(SPEECH_FRAME)
    voice.send(type="end", turn="t1")
    final = voice.next("final")
    wait_for(client, final["task_id"], lambda t: t["status"] == "completed")
    assert len(codex_log("thread/start")) == 1  # the task took over the process started early


def test_a_turn_follows_up_on_the_selected_task(client, voice, project):
    task = client.post("/api/tasks", json={"project_id": project["id"], "text": "add a test"}).json()
    wait_for(client, task["id"], lambda t: t["status"] == "completed")
    voice.talk(task_id=task["id"])
    assert voice.next("final")["task_id"] == task["id"]
    task = wait_for(client, task["id"], lambda t: len([m for m in t["messages"] if m["role"] == "user"]) == 2)
    assert [m["text"] for m in task["messages"] if m["role"] == "user"] == ["add a test", "run the tests in backend"]


def test_a_turn_without_a_target_says_so(voice):
    voice.talk()
    error = voice.next("voice_error")
    assert (error["code"], error["turn"]) == ("no_target", "t1")
    assert voice.next("final")["outcome"] == "failed"


def test_a_turn_for_a_missing_task_fails_to_dispatch(voice):
    voice.talk(task_id=999)
    assert voice.next("voice_error")["code"] == "dispatch_failed"
    assert voice.next("final")["outcome"] == "failed"


def test_a_turn_with_no_audio_is_empty(voice, project):
    voice.send(type="start", turn="t1", project_id=project["id"])
    voice.send(type="end", turn="t1")
    final = voice.next("final")
    assert (final["text"], final["outcome"]) == ("", "empty")


def test_captions_arrive_while_listening(voice, project):
    voice.send(type="start", turn="t1", project_id=project["id"])
    for _ in range(25):  # 0.5 s: the fake hears two words
        voice.ws.send_bytes(SPEECH_FRAME)
    caption = voice.next("caption")
    assert caption == {"type": "caption", "turn": "t1", "committed": "", "provisional": "run the"}
    voice.send(type="end", turn="t1")
    assert voice.next("final")["outcome"] == "dispatched"


def test_a_quiet_turn_is_empty(client, voice, project):
    voice.send(type="start", turn="t1", project_id=project["id"])
    for _ in range(50):
        voice.ws.send_bytes(b"\x00" * 640)
    voice.send(type="end", turn="t1")
    final = voice.next("final")
    assert (final["text"], final["outcome"]) == ("", "empty")
    assert client.get("/api/state").json()["tasks"] == []


def test_cancel_discards_the_turn(client, voice, project):
    voice.send(type="start", turn="t1", project_id=project["id"])
    voice.ws.send_bytes(SPEECH_FRAME)
    voice.send(type="cancel", turn="t1")
    assert voice.next("voice_state", state="idle")["type"] == "voice_state"
    voice.talk(turn="t2", project_id=project["id"])  # the session takes a new turn afterwards
    assert voice.next("final")["turn"] == "t2"
    assert len(client.get("/api/state").json()["tasks"]) == 1


def test_one_turn_at_a_time_per_device(voice, project):
    voice.send(type="start", turn="t1", project_id=project["id"])
    voice.send(type="start", turn="t2", project_id=project["id"])
    error = voice.next("voice_error")
    assert (error["code"], error["turn"]) == ("busy", "t2")
    voice.send(type="end", turn="t1")
    assert voice.next("final")["turn"] == "t1"


def test_two_devices_talk_at_once(client, project):
    with client.websocket_connect("/api/voice") as a, client.websocket_connect("/api/voice") as b:
        phone, desktop = Voice(a), Voice(b)
        phone.send(type="start", turn="p1", project_id=project["id"])
        desktop.send(type="start", turn="d1", project_id=project["id"])
        phone.send(type="end", turn="p1")
        desktop.send(type="end", turn="d1")
        assert phone.next("final")["turn"] == "p1"
        assert desktop.next("final")["turn"] == "d1"


def test_start_needs_speech_recognition(settings, paths, codex_log):
    speech = Speech.with_engines(None, None, FakeSynthesizer())
    with paired(TestClient(create_app(settings, paths, speech=speech), headers=HEADERS)) as client:
        with client.websocket_connect("/api/voice") as ws:
            voice = Voice(ws)
            assert voice.ready["speech"]["stt"] == "unavailable"
            voice.send(type="start", turn="t1", project_id=1)
            assert voice.next("voice_error")["code"] == "stt_unavailable"
    speech.close()


# Commands

@pytest.fixture
def other_project(client, tmp_path):
    directory = tmp_path / "site"
    directory.mkdir()
    return client.post("/api/projects", json={"path": str(directory), "name": "web client"}).json()


def user_messages(client, task_id):
    return [m["text"] for m in client.get(f"/api/tasks/{task_id}").json()["messages"] if m["role"] == "user"]


def test_a_project_name_is_sent_as_said(client, voice, speech, project, other_project):
    speech.stt.transcript = "In backend, run the tests."
    voice.talk(project_id=other_project["id"])
    final = voice.next("final")
    assert final["outcome"] == "dispatched"
    assert client.get(f"/api/tasks/{final['task_id']}").json()["project_id"] == other_project["id"]
    assert user_messages(client, final["task_id"]) == ["In backend, run the tests."]


# Frames and limits

def test_a_bad_frame_drops_the_turn(voice, project):
    voice.send(type="start", turn="t1", project_id=project["id"])
    voice.ws.send_bytes(b"\x00" * 641)
    error = voice.next("voice_error")
    assert (error["code"], error["turn"]) == ("bad_frame", "t1")
    assert voice.next("voice_state", state="idle")


def test_frames_outside_a_turn_are_ignored(client, voice):
    voice.ws.send_bytes(SPEECH_FRAME)
    voice.send(type="stop")
    assert voice.next("stopped")["generation"] == 1  # no error came first
    assert session(client).ignored_frames == 1


def test_a_turn_that_falls_behind_fails_visibly(client, voice, project, monkeypatch):
    async def stalled(self, turn):  # recognition never takes the audio
        await asyncio.Event().wait()

    monkeypatch.setattr(VoiceSession, "_listen", stalled)
    voice.send(type="start", turn="t1", project_id=project["id"])
    for _ in range(110):  # 2.2 s
        voice.ws.send_bytes(SPEECH_FRAME)
    error = voice.next("voice_error")
    assert (error["code"], error["turn"]) == ("uplink_behind", "t1")


def test_bad_messages_are_reported(voice):
    voice.ws.send_text("not json")
    assert voice.next("voice_error")["code"] == "bad_message"
    voice.send(type="dance")
    assert voice.next("voice_error")["code"] == "bad_message"
    voice.send(type="start", turn="")
    assert voice.next("voice_error")["code"] == "bad_message"


def test_decode_uplink_checks_sizes():
    assert len(frames.decode_uplink(SPEECH_FRAME)) == 320
    assert len(frames.decode_uplink(b"\x00" * 100)) == 50  # a partial last frame
    for size in (0, 1, 3202):
        with pytest.raises(frames.BadFrame):
            frames.decode_uplink(b"\x00" * size)


# Speech out and the playback generation

def test_a_reply_is_spoken_with_tagged_frames(client, voice, speech):
    speech.tts.gate.set()
    speak(client, "All tests pass.", "Two files changed.", task_id=7)
    assert voice.next("voice_state")["state"] == "speaking"
    first = voice.next("speech")
    assert first == {"type": "speech", "reply": 1, "generation": 0, "segment": 0, "text": "All tests pass.",
                     "task_id": 7}
    assert voice.next("speech")["segment"] == 1
    assert voice.next("speech_end") == {"type": "speech_end", "reply": 1, "generation": 0}
    voice.send(type="drained", reply=1, segment=1)  # the device has played it all
    assert voice.next("voice_state")["state"] == "idle"
    decoded = [frames.decode_downlink(f) for f in voice.audio]
    assert {(kind, generation) for kind, _, generation, _ in decoded} == {(frames.AUDIO, 0)}
    assert [segment for _, segment, _, _ in decoded] == [0, 0, 0, 1, 1, 1]
    assert sum(len(samples) for _, _, _, samples in decoded) == int(24000 * 0.6)


def test_a_reply_about_a_task_not_on_screen_is_not_spoken(client, voice, speech):
    speech.tts.gate.set()
    voice.send(type="target", task_id=7)
    voice_session = session(client)
    client.portal.call(lambda: voice_session.speak(["From the other task."], task_id=8))
    client.portal.call(lambda: voice_session.speak(["From this one."], task_id=7))
    assert voice.next("unheard") == {"type": "unheard", "task_id": 8}
    speech_message = voice.next("speech")
    assert (speech_message["reply"], speech_message["text"]) == (2, "From this one.")


def test_moving_to_another_task_stops_its_reply_at_the_next_sentence(client, voice, speech):
    voice.send(type="target", task_id=7)
    voice_session = session(client)
    client.portal.call(lambda: voice_session.speak(["One.", "Two."], task_id=7))
    assert voice.next("speech")["segment"] == 0  # the second sentence is held meanwhile
    voice.send(type="target", task_id=9)
    voice.send(type="ping")  # answered after the target is taken
    voice.next("pong")
    speech.tts.gate.set()
    assert voice.next("unheard") == {"type": "unheard", "task_id": 7}


def test_a_ping_is_answered(voice):
    voice.send(type="ping")
    assert voice.next("pong") == {"type": "pong"}


def voice_log(caplog, level=logging.INFO):
    return [r.getMessage() for r in caplog.records if r.name == "sennet.voice.session" and r.levelno >= level]


def test_a_heard_reply_logs_one_line_at_info(client, voice, speech, caplog):
    caplog.set_level(logging.DEBUG, logger="sennet.voice.session")
    speech.tts.gate.set()
    speak(client, "All tests pass.", task_id=7)
    voice.next("speech_end")
    voice.send(type="played", reply=1, segment=0)
    voice.send(type="drained", reply=1, segment=0)
    voice.send(type="audio", state="suspended", hidden=True)
    voice.next("voice_state", state="idle")
    info = voice_log(caplog)
    assert [line for line in info if "reply 1" in line] == [
        "voice %s: reply 1 (task 7) heard: 1 segment, 0.3 s of speech, first heard 0.0 s after it was queued"
        % session(client).id[:4]]
    assert any("audio is suspended (page hidden)" in line for line in info)
    steps = voice_log(caplog, logging.DEBUG)
    assert any("reply 1 for task 7 queued, 1 segment" in line for line in steps)
    assert any("sending reply 1 segment 0 (task 7)" in line for line in steps)
    assert any("device playing reply 1 segment 0, " in line for line in steps)


def test_a_stopped_reply_says_how_far_it_got(client, voice, speech, caplog):
    caplog.set_level(logging.INFO, logger="sennet.voice.session")
    speak(client, "First part.", "Second part.", task_id=7)
    voice.next("speech", segment=0)
    voice.send(type="played", reply=1, segment=0)
    voice.send(type="stop")
    voice.next("stopped")
    assert any("reply 1 (task 7) stopped: after 1 of 1 segments" in line for line in voice_log(caplog))


def test_a_reply_plays_until_the_device_drains_its_last_segment(client, voice, speech):
    speech.tts.gate.set()
    speak(client, "First part.", "Second part.")
    voice.next("speech_end")
    voice_session = session(client)
    assert voice_session.playing  # sent, not yet heard
    voice.send(type="drained", reply=1, segment=0)  # ran dry between segments: the second is still to play
    voice.send(type="drained", reply=1, segment=1)
    assert voice.next("voice_state")["state"] == "idle"
    assert not voice_session.playing


def test_a_streaming_reply_plays_sentences_as_they_are_added(client, voice, speech):
    speech.tts.gate.set()
    voice_session = session(client)
    reply = client.portal.call(lambda: voice_session.speak(["First sentence."], streaming=True))
    assert voice.next("speech")["text"] == "First sentence."
    client.portal.call(lambda: reply.append(["Second sentence."]))
    second = voice.next("speech")
    assert (second["reply"], second["segment"], second["text"]) == (1, 1, "Second sentence.")
    client.portal.call(reply.close)
    assert voice.next("speech_end")["reply"] == 1


def test_stop_ends_a_reply_waiting_for_its_next_sentence(client, voice, speech):
    speech.tts.gate.set()
    voice_session = session(client)
    reply = client.portal.call(lambda: voice_session.speak(["First sentence."], streaming=True))
    voice.next("speech")
    voice.send(type="stop")
    voice.next("stopped")
    client.portal.call(lambda: reply.append(["Too late."]))
    assert nothing_spoken(voice)
    assert not voice_session.sending


def test_a_device_that_never_reports_stops_playing_after_the_estimate(client, voice, speech, monkeypatch):
    monkeypatch.setattr("sennet.voice.session.DRAIN_GRACE_S", 0.1)
    speech.tts.gate.set()
    speak(client, "All tests pass.")
    voice.next("speech_end")
    assert voice.next("voice_state")["state"] == "idle"  # the 0.3 s of audio, its tail and the grace
    assert not session(client).playing


def test_stop_ends_speech_and_raises_the_generation(client, voice, speech):
    speak(client, "First part.", "Second part.")
    voice.next("speech", segment=0)
    voice.send(type="stop")  # the second segment is still being synthesized
    assert voice.next("stopped")["generation"] == 1
    speech.tts.gate.set()
    seen = voice.until("voice_state", state="idle")
    assert not [m for m in seen if m["type"] in ("speech", "speech_end")]
    assert {frames.decode_downlink(f)[2] for f in voice.audio} == {0}


def test_talking_interrupts_speech(client, voice, speech, project):
    speak(client, "First part.", "Second part.")
    voice.next("speech", segment=0)
    voice.send(type="start", turn="t1", project_id=project["id"])
    assert voice.next("stopped")["generation"] == 1
    speech.tts.gate.set()
    voice.send(type="end", turn="t1")
    seen = voice.until("final")
    assert not [m for m in seen if m["type"] in ("speech", "speech_end")]


def test_a_reply_waits_for_the_turn_to_end(client, voice, speech, project):
    speech.tts.gate.set()
    voice.send(type="start", turn="t1", project_id=project["id"])
    voice.next("voice_state", state="listening")
    speak(client, "Done.")
    voice.ws.send_bytes(SPEECH_FRAME)
    voice.send(type="end", turn="t1")
    seen = voice.until("speech_end")
    kinds = [m["type"] for m in seen]
    assert kinds.index("final") < kinds.index("speech")
    assert seen[kinds.index("speech")]["generation"] == 1


# Spoken replies

def nothing_spoken(voice):
    """True when no speech arrived before a fresh round trip."""
    voice.send(type="stop")
    seen = voice.until("stopped")
    return not [m for m in seen if m["type"] in ("speech", "speech_end")]


def test_the_reply_is_spoken_on_the_device_that_asked(client, voice, speech, project):
    speech.tts.gate.set()
    with client.websocket_connect("/api/voice") as other_socket:
        other = Voice(other_socket)
        voice.talk(project_id=project["id"])
        task_id = voice.next("final")["task_id"]
        spoken = voice.next("speech")
        assert (spoken["text"], spoken["task_id"]) == ("You said: run the tests in backend.", task_id)
        voice.next("speech_end")
        assert nothing_spoken(other)


def test_a_typed_request_is_spoken_like_a_spoken_one(client, voice, speech, project):
    speech.tts.gate.set()
    task = client.post("/api/tasks", json={"project_id": project["id"], "text": "add a test"}).json()
    spoken = voice.next("speech")
    assert (spoken["text"], spoken["task_id"]) == ("You said: add a test.", task["id"])
    voice.next("speech_end")
    client.post(f"/api/tasks/{task['id']}/messages", json={"text": "and another"})
    assert voice.next("speech")["text"] == "You said: and another."


def test_typing_stops_the_reply_being_spoken(client, voice, speech, project):
    speak(client, "First part.", "Second part.")
    voice.next("speech", segment=0)
    client.post("/api/tasks", json={"project_id": project["id"], "text": "add a test"})
    assert voice.next("stopped")["generation"] == 1
    speech.tts.gate.set()
    assert voice.next("speech")["text"] == "You said: add a test."


def test_typed_requests_are_not_spoken_without_speech_output(client, voice, speech, project):
    client.portal.call(lambda: speech._set("tts", "unavailable", "turned off"))
    task = client.post("/api/tasks", json={"project_id": project["id"], "text": "add a test"}).json()
    wait_for(client, task["id"], lambda t: t["status"] == "completed")
    assert nothing_spoken(voice)


def test_a_reply_can_be_heard_again(client, voice, speech, project):
    speech.tts.gate.set()
    db = client.app.state.tasks.db
    task = client.post("/api/tasks", json={"project_id": project["id"], "text": "add a test"}).json()
    voice.next("speech_end")
    wait_for(client, task["id"], lambda t: t["status"] == "completed")
    written = db.add_message(task["id"], "agent", "<spoken>The short version.</spoken>\n\nThe long one.", "done")
    said = next(m["id"] for m in client.get(f"/api/tasks/{task['id']}").json()["messages"] if m["role"] == "agent")
    voice.send(type="replay", message_ids=[written, said, 999])  # 999: no such message
    spoken = [m["text"] for m in voice.until("speech_end", reply=3) if m["type"] == "speech"]
    assert spoken == ["The short version.", "You said: add a test."]


def test_only_agent_messages_are_heard_again(client, voice, speech, project):
    speech.tts.gate.set()
    task = client.post("/api/tasks", json={"project_id": project["id"], "text": "add a test"}).json()
    voice.next("speech_end")
    asked = next(m["id"] for m in client.get(f"/api/tasks/{task['id']}").json()["messages"] if m["role"] == "user")
    voice.send(type="replay", message_ids=[asked])
    assert nothing_spoken(voice)
    voice.send(type="replay", message_ids="all")
    assert voice.next("voice_error")["code"] == "bad_message"


def test_a_failed_turn_says_so(client, voice, speech, project):
    speech.tts.gate.set()
    speech.stt.transcript = "crash"
    voice.talk(project_id=project["id"])
    spoken = [voice.next("speech")["text"]]
    while (message := voice.receive())["type"] != "speech_end":
        if message["type"] == "speech":
            spoken.append(message["text"])
    assert " ".join(spoken) == "That task failed: agent process exited."


def test_a_cancelled_turn_is_not_spoken(client, voice, speech, project):
    speech.tts.gate.set()
    speech.stt.transcript = "hang"
    voice.talk(project_id=project["id"])
    task_id = voice.next("final")["task_id"]
    wait_for(client, task_id, lambda t: t["status"] == "running")
    client.post(f"/api/tasks/{task_id}/cancel")
    wait_for(client, task_id, lambda t: t["status"] == "cancelled")
    assert nothing_spoken(voice)


def test_a_closed_socket_drops_its_turn(client, project):
    with client.websocket_connect("/api/voice") as ws:
        voice = Voice(ws)
        voice.send(type="start", turn="t1", project_id=project["id"])
        voice.ws.send_bytes(SPEECH_FRAME)
    assert client.get("/api/state").json()["tasks"] == []
    assert client.app.state.voice.sessions == set()


def test_a_task_started_by_voice_takes_the_turns_model(client, voice, speech, project):
    speech.stt.transcript = "Run the tests."
    voice.talk(project_id=project["id"], model="gpt-deep", effort="max")
    task = client.get(f"/api/tasks/{voice.next('final')['task_id']}").json()
    assert (task["model"], task["effort"]) == ("gpt-deep", "max")


def test_a_model_the_agent_doesnt_list_fails_the_turn(client, voice, speech, project):
    speech.stt.transcript = "Run the tests."
    voice.talk(project_id=project["id"], model="gpt-nope")
    assert voice.next("voice_error")["code"] == "dispatch_failed"
    assert client.get("/api/state").json()["tasks"] == []
