"""Long agent replies cut for speech, and "read me the details"."""
import pytest
from fastapi.testclient import TestClient

from sennet.server import create_app
from sennet.speech import Speech
from sennet.speech.fakes import FakeRecognizer, FakeVAD
from sennet.voice import summary
from sennet.voice.dispatch import Details, Request, parse
from conftest import HEADERS, wait_for, paired
from test_voice import GatedSynthesizer, Voice

REPLY = """I fixed the flaky test in `tests/test_tasks.py`: the fake agent now answers in a fixed order.

**What changed**
- `tests/fake_codex.py` sorts its pending approvals before asking.
- Added a regression test that runs the approval flow 20 times.
- The timeout in the approval helper is now read from the settings instead of being fixed.
- Removed the sleep that hid the race, since the fixed order makes it unnecessary.

The race only showed up when two approvals arrived within the same event loop tick, which is why \
it failed about once in thirty runs on CI and never locally.

```
uv run pytest tests/test_tasks.py
```

All 40 tests pass, and two files changed. Want me to look at the other flaky test too?"""


# A heading runs into the sentence after it when that starts with a file name.
OPENING = ("I fixed the flaky test in test_tasks.py: the fake agent now answers in a fixed order. "
           "What changed. fake_codex.py sorts its pending approvals before asking. "
           "Added a regression test that runs the approval flow 20 times. "
           "The timeout in the approval helper is now read from the settings instead of being fixed.")


# Shortening

def test_a_short_reply_is_spoken_as_it_is():
    assert summary.shorten("All **40** tests pass.") == "All 40 tests pass."


def test_a_long_reply_is_cut_to_its_opening():
    assert summary.shorten(REPLY) == OPENING


def test_a_spoken_version_is_left_out():
    assert summary.shorten("<spoken>Heard already.</spoken>\nAll 40 tests pass.") == "All 40 tests pass."


# Read me the details

@pytest.mark.parametrize("text", ["Read me the details.", "Details.", "Read it all, please.",
                                  "OK read the full reply"])
def test_details_words(text):
    assert isinstance(parse(text, []), Details)


def test_details_inside_a_request_is_a_request():
    assert parse("Read the details of the API and summarise them.", []) == \
        Request("Read the details of the API and summarise them.")


# In the daemon

@pytest.fixture
def speech():
    speech = Speech.with_engines(FakeVAD(), FakeRecognizer("hello"), GatedSynthesizer())
    speech.tts.gate.set()
    yield speech
    speech.close()


@pytest.fixture
def client(settings, paths, codex_log, speech):
    with paired(TestClient(create_app(settings, paths, speech=speech), headers=HEADERS)) as client:
        yield client


@pytest.fixture
def voice(client):
    with client.websocket_connect("/api/voice") as ws:
        yield Voice(ws)


def spoken(voice) -> str:
    texts = [voice.next("speech")["text"]]
    while (message := voice.receive())["type"] != "speech_end":
        if message["type"] == "speech":
            texts.append(message["text"])
    return " ".join(texts)


LONG = ("Please check every module. " * 15).strip()   # the fake agent says it back: "You said: …"
HEARD = "You said: " + " ".join(["Please check every module."] * 4)


def test_a_long_reply_is_heard_as_its_opening(client, voice, speech, project):
    speech.stt.transcript = LONG
    voice.talk(project_id=project["id"])
    task_id = voice.next("final")["task_id"]
    assert spoken(voice) == HEARD
    # The timeline keeps the whole reply.
    task = wait_for(client, task_id, lambda t: t["status"] == "completed")
    assert task["messages"][-1]["text"] == f"You said: {LONG}"


def test_read_me_the_details_reads_the_whole_reply(client, voice, speech, project):
    speech.stt.transcript = LONG
    voice.talk(project_id=project["id"])
    task_id = voice.next("final")["task_id"]
    assert spoken(voice) == HEARD
    speech.stt.transcript = "Read me the details."
    voice.talk("t2")   # nothing selected: the reply last heard here
    assert voice.next("final", turn="t2") == {"type": "final", "turn": "t2", "text": "Read me the details.",
                                              "outcome": "details", "task_id": task_id}
    assert spoken(voice) == f"You said: {LONG}"


def test_details_with_nothing_heard_says_so(voice, speech):
    speech.stt.transcript = "Read me the details."
    voice.talk()
    assert voice.next("voice_error")["code"] == "no_target"
    assert voice.next("final")["outcome"] == "failed"
