"""The /api/voice protocol (docs/voice-protocol.md), on fake speech engines."""
import asyncio
import json
import threading

import numpy as np
import pytest
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from agent_talk.server import create_app
from agent_talk.speech import Speech
from agent_talk.speech.fakes import FakeRecognizer, FakeSynthesizer, FakeVAD
from agent_talk.voice import frames
from agent_talk.voice.session import VoiceSession
from conftest import HEADERS, wait_for

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
    """Holds the second and later segments until `gate` is set."""

    def __init__(self):
        super().__init__()
        self.gate = threading.Event()

    def synthesize(self, text):
        if self.spoken:
            self.gate.wait(10)
        return super().synthesize(text)


@pytest.fixture
def speech():
    return Speech.with_engines(FakeVAD(), FakeRecognizer("run the tests in backend"), GatedSynthesizer())


@pytest.fixture
def client(settings, paths, codex_log, speech):
    with TestClient(create_app(settings, paths, speech=speech), headers=HEADERS) as client:
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
    client.portal.call(lambda: voice_session.speak(list(segments), task_id=task_id))


# Session start

def test_ready_describes_the_session(voice):
    ready = voice.ready
    assert ready["generation"] == 0
    assert ready["input"] == {"rate": 16000, "format": "pcm_s16le", "channels": 1}
    assert ready["output"]["rate"] == 24000
    assert ready["speech"] == {"stt": "ready", "tts": "ready"}


def test_other_sites_cannot_connect(client):
    with pytest.raises(WebSocketDisconnect):
        with client.websocket_connect("/api/voice", headers={"origin": "https://evil.example"}) as ws:
            ws.receive_text()


# Turns and direct mode

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
    with TestClient(create_app(settings, paths, speech=speech), headers=HEADERS) as client:
        with client.websocket_connect("/api/voice") as ws:
            voice = Voice(ws)
            assert voice.ready["speech"]["stt"] == "unavailable"
            voice.send(type="start", turn="t1", project_id=1)
            assert voice.next("voice_error")["code"] == "stt_unavailable"
    speech.close()


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
    for size in (1, 100, 3202):
        with pytest.raises(frames.BadFrame):
            frames.decode_uplink(b"\x00" * size)


# Speech out and the playback generation

def test_a_reply_is_spoken_with_tagged_frames(client, voice, speech):
    speech.tts.gate.set()
    speak(client, "All tests pass.", "Two files changed.", task_id=7)
    assert voice.next("voice_state")["state"] == "speaking"
    first = voice.next("speech")
    assert first == {"type": "speech", "generation": 0, "segment": 0, "text": "All tests pass.",
                     "task_id": 7, "approval_id": None}
    assert voice.next("speech")["segment"] == 1
    assert voice.next("speech_end") == {"type": "speech_end", "generation": 0}
    assert voice.next("voice_state")["state"] == "idle"
    decoded = [frames.decode_downlink(f) for f in voice.audio]
    assert {(kind, generation) for kind, _, generation, _ in decoded} == {(frames.AUDIO, 0)}
    assert [segment for _, segment, _, _ in decoded] == [0, 0, 0, 1, 1, 1]
    assert sum(len(samples) for _, _, _, samples in decoded) == int(24000 * 0.6)


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


def test_a_closed_socket_drops_its_turn(client, project):
    with client.websocket_connect("/api/voice") as ws:
        voice = Voice(ws)
        voice.send(type="start", turn="t1", project_id=project["id"])
        voice.ws.send_bytes(SPEECH_FRAME)
    assert client.get("/api/state").json()["tasks"] == []
    assert client.app.state.voice.sessions == set()
