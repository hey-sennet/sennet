"""Voice approvals over /api/voice, with the fake Codex asking (voice-protocol §8)."""
import itertools

import pytest

from conftest import pending_approval, wait_for
from test_voice import SPEECH_FRAME, Voice, client, speech, voice  # noqa: F401  (fixtures)

pytestmark = pytest.mark.timeout(20)
turns = itertools.count(1)


def say(voice, speech, text, **target) -> dict:
    """One push-to-talk turn heard as `text`; returns its final."""
    speech.stt.transcript = text
    turn = f"t{next(turns)}"
    voice.talk(turn=turn, **target)
    return voice.next("final", turn=turn)


def heard(voice) -> list[str]:
    """The sentences of the next spoken reply."""
    texts = [voice.next("speech")["text"]]
    while (message := voice.receive())["type"] != "speech_end":
        if message["type"] == "speech":
            texts.append(message["text"])
    return texts


def ask_to_run(voice, speech, project, command="npm test") -> dict:
    """Have the agent ask to run `command`, and wait for the question."""
    say(voice, speech, f"run:{command}", project_id=project["id"])
    return voice.next("speech")


@pytest.fixture(autouse=True)
def open_gate(speech):
    speech.tts.gate.set()


def test_the_question_names_the_project_and_the_command(client, voice, speech, project):
    question = ask_to_run(voice, speech, project)
    assert question["text"] == "backend wants to run npm test."
    assert question["approval_id"] == pending_approval(client)["id"]
    assert voice.next("speech")["text"] == "Approve?"


def test_yes_approves_and_the_reply_follows(client, voice, speech, project):
    ask_to_run(voice, speech, project)
    approval_id = pending_approval(client)["id"]
    voice.next("speech_end")
    final = say(voice, speech, "Yes.")
    assert final["outcome"] == "approval" and final["decision"] == "approve" and final["approval_id"] == approval_id
    assert heard(voice) == ["Approved."]
    assert heard(voice) == ["Ran it."]
    assert client.app.state.db.approval(approval_id)["decided_by"] == "voice"


def test_no_declines(client, voice, speech, project):
    ask_to_run(voice, speech, project)
    voice.next("speech_end")
    assert say(voice, speech, "No.")["decision"] == "decline"
    assert heard(voice) == ["Declined."]
    assert heard(voice) == ["Okay, I won't run it."]


def test_an_unclear_answer_asks_again(client, voice, speech, project):
    ask_to_run(voice, speech, project)
    voice.next("speech_end")
    final = say(voice, speech, "Run the linter too.")
    assert final["outcome"] == "reprompt"
    assert heard(voice) == ["Sorry, I need a clear answer. Say yes, no, or always."]
    assert pending_approval(client)["id"] == final["approval_id"]  # never decided by a guess
    assert say(voice, speech, "Yes.")["decision"] == "approve"


def test_always_remembers_the_command(client, voice, speech, project):
    ask_to_run(voice, speech, project)
    voice.next("speech_end")
    assert say(voice, speech, "Always.")["decision"] == "approve_always"
    assert heard(voice) == ["Approved, and I'll remember that for backend."]
    assert [r["pattern"] for r in client.get("/api/state").json()["rules"]] == ["npm test"]


def test_destructive_requests_need_confirm(client, voice, speech, project):
    ask_to_run(voice, speech, project, "git push origin main")
    assert voice.next("speech")["text"] == "This could be hard to undo."
    voice.next("speech_end")
    first = say(voice, speech, "Yes.")
    assert (first["outcome"], first["confirm"]) == ("reprompt", True)
    assert heard(voice) == ["Say confirm to go ahead, or no."]
    assert say(voice, speech, "Yes.")["outcome"] == "reprompt"  # a second yes is not a confirm
    heard(voice)
    assert say(voice, speech, "Confirm.")["decision"] == "approve"
    assert heard(voice) == ["Approved."]


def test_destructive_requests_cannot_be_approved_always(client, voice, speech, project):
    ask_to_run(voice, speech, project, "git push origin main")
    voice.next("speech_end")
    assert say(voice, speech, "Always.")["outcome"] == "reprompt"
    assert heard(voice) == ["That one can't be approved always. Say yes or no."]
    assert client.get("/api/state").json()["rules"] == []


def test_stop_keeps_the_question_open(client, voice, speech, project):
    ask_to_run(voice, speech, project)
    voice.next("speech_end")
    assert say(voice, speech, "Stop.")["outcome"] == "stop"
    assert pending_approval(client)
    assert say(voice, speech, "Yes.")["decision"] == "approve"


def test_the_card_can_answer_first(client, voice, speech, project):
    speech.tts.gate.clear()  # the question's second sentence waits here
    question = ask_to_run(voice, speech, project)
    client.post(f"/api/approvals/{question['approval_id']}", json={"decision": "approve"})
    speech.tts.gate.set()
    # The rest of the question is dropped; the agent's reply is what plays next.
    assert heard(voice) == ["Ran it."]


def test_a_turn_before_the_question_plays_is_a_request(client, voice, speech, project):
    task = client.post("/api/tasks", json={"project_id": project["id"], "text": "hello"}).json()
    wait_for(client, task["id"], lambda t: t["status"] == "completed")
    speech.tts.free = 0  # nothing can be synthesized, so nothing is heard
    speech.tts.gate.clear()
    say(voice, speech, "run:npm test", task_id=task["id"])
    pending_approval(client)
    voice.send(type="start", turn="hold", project_id=project["id"])
    for _ in range(10):
        voice.ws.send_bytes(SPEECH_FRAME)
    speech.stt.transcript = "Yes."
    voice.send(type="end", turn="hold")
    final = voice.next("final", turn="hold")
    assert final["outcome"] == "dispatched"  # the question hadn't been heard yet
    speech.tts.gate.set()
    assert voice.next("speech")["text"] == "backend wants to run npm test."  # and is asked after the turn


def test_approvals_are_asked_one_at_a_time(client, voice, speech, project, tmp_path):
    other_dir = tmp_path / "site"
    other_dir.mkdir()
    other = client.post("/api/projects", json={"path": str(other_dir), "name": "site"}).json()
    say(voice, speech, "run:ls", project_id=project["id"])
    say(voice, speech, "run:pwd", project_id=other["id"])
    assert heard(voice)[0] == "backend wants to run ls."
    assert say(voice, speech, "Yes.")["decision"] == "approve"
    replies = [heard(voice) for _ in range(3)]  # "Approved.", the next question, "Ran it." in some order
    assert ["Approved."] == replies[0]
    assert ["site wants to run pwd.", "Approve?"] in replies


def test_only_the_owning_device_is_asked(client, voice, speech, project):
    with client.websocket_connect("/api/voice") as other_socket:
        other = Voice(other_socket)
        ask_to_run(voice, speech, project)
        other.send(type="stop")
        seen = other.until("stopped")
        assert not [m for m in seen if m["type"] == "speech"]
