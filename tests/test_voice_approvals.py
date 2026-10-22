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
    """Have the agent ask to run `command`, and wait until the session is asking."""
    say(voice, speech, f"run:{command}", project_id=project["id"])
    return voice.next("asking")


@pytest.fixture(autouse=True)
def open_gate(speech):
    speech.tts.gate.set()


def test_the_question_is_asked_without_words(client, voice, speech, project):
    say(voice, speech, "run:npm test", project_id=project["id"])
    seen = voice.until("asking")
    assert seen[-1]["approval_id"] == pending_approval(client)["id"]
    assert not [m for m in seen if m["type"] == "speech"]  # the client chimes; nothing is read out


def test_yes_approves_and_the_reply_follows(client, voice, speech, project):
    approval_id = ask_to_run(voice, speech, project)["approval_id"]
    final = say(voice, speech, "Yes.")
    assert final["outcome"] == "approval" and final["decision"] == "approve" and final["approval_id"] == approval_id
    assert heard(voice) == ["Approved."]
    assert heard(voice) == ["Ran it."]
    assert client.app.state.db.approval(approval_id)["decided_by"] == "voice"


def test_no_declines(client, voice, speech, project):
    ask_to_run(voice, speech, project)
    assert say(voice, speech, "No.")["decision"] == "decline"
    assert heard(voice) == ["Declined."]
    assert heard(voice) == ["Okay, I won't run it."]


def test_an_unclear_answer_asks_again(client, voice, speech, project):
    ask_to_run(voice, speech, project)
    final = say(voice, speech, "Run the linter too.")
    assert final["outcome"] == "reprompt"
    assert heard(voice) == ["Sorry, I need a clear answer. Say yes, no, or always."]
    assert pending_approval(client)["id"] == final["approval_id"]  # never decided by a guess
    assert say(voice, speech, "Yes.")["decision"] == "approve"


def test_always_remembers_the_command(client, voice, speech, project):
    ask_to_run(voice, speech, project)
    assert say(voice, speech, "Always.")["decision"] == "approve_always"
    assert heard(voice) == ["Approved, and I'll remember that for backend."]
    assert [r["pattern"] for r in client.get("/api/state").json()["rules"]] == ["npm test"]


def test_destructive_requests_need_confirm(client, voice, speech, project):
    ask_to_run(voice, speech, project, "git push origin main")
    first = say(voice, speech, "Yes.")
    assert (first["outcome"], first["confirm"]) == ("reprompt", True)
    assert heard(voice) == ["This could be hard to undo. Say confirm to go ahead, or no."]
    assert say(voice, speech, "Yes.")["outcome"] == "reprompt"  # a second yes is not a confirm
    heard(voice)
    assert say(voice, speech, "Confirm.")["decision"] == "approve"
    assert heard(voice) == ["Approved."]


def test_destructive_requests_cannot_be_approved_always(client, voice, speech, project):
    ask_to_run(voice, speech, project, "git push origin main")
    assert say(voice, speech, "Always.")["outcome"] == "reprompt"
    assert heard(voice) == ["That one can't be approved always. Say yes or no."]
    assert client.get("/api/state").json()["rules"] == []


def test_stop_keeps_the_question_open(client, voice, speech, project):
    ask_to_run(voice, speech, project)
    assert say(voice, speech, "Stop.")["outcome"] == "stop"
    assert pending_approval(client)
    assert say(voice, speech, "Yes.")["decision"] == "approve"


def test_the_card_can_answer_first(client, voice, speech, project):
    asking = ask_to_run(voice, speech, project)
    client.post(f"/api/approvals/{asking['approval_id']}", json={"decision": "approve"})
    assert heard(voice) == ["Ran it."]
    assert say(voice, speech, "hello", project_id=project["id"])["outcome"] == "dispatched"  # no longer asking


def test_a_turn_open_when_the_approval_arrives_is_a_request(client, voice, speech, project):
    task_id = say(voice, speech, "hello", project_id=project["id"])["task_id"]
    wait_for(client, task_id, lambda t: t["status"] == "completed")
    heard(voice)
    voice.send(type="start", turn="hold", task_id=task_id)
    for _ in range(10):
        voice.ws.send_bytes(SPEECH_FRAME)
    client.post(f"/api/tasks/{task_id}/messages", json={"text": "run:npm test"})
    pending_approval(client)
    speech.stt.transcript = "Yes."
    voice.send(type="end", turn="hold")
    seen = voice.until("final", turn="hold")
    assert seen[-1]["outcome"] == "dispatched"  # the approval wasn't asked yet
    assert "asking" not in [m["type"] for m in seen]
    assert voice.next("asking")["approval_id"] == pending_approval(client)["id"]  # and is asked after the turn


def test_approvals_are_asked_one_at_a_time(client, voice, speech, project, tmp_path):
    other_dir = tmp_path / "site"
    other_dir.mkdir()
    other = client.post("/api/projects", json={"path": str(other_dir), "name": "site"}).json()
    tasks = []
    for target in (project, other):
        tasks.append(say(voice, speech, "hello", project_id=target["id"])["task_id"])
        heard(voice)
    voice.send(type="start", turn="hold", project_id=project["id"])  # both arrive while a turn is open
    for task_id, command in zip(tasks, ("run:ls", "run:pwd")):
        client.post(f"/api/tasks/{task_id}/messages", json={"text": command})
        wait_for(client, task_id, lambda t: t["status"] == "awaiting_approval")
    voice.send(type="cancel", turn="hold")
    first = voice.next("asking")
    assert client.app.state.db.approval(first["approval_id"])["task_id"] == tasks[0]  # oldest first
    assert say(voice, speech, "Yes.")["approval_id"] == first["approval_id"]
    second = voice.next("asking")
    assert client.app.state.db.approval(second["approval_id"])["task_id"] == tasks[1]


def test_only_the_owning_device_is_asked(client, voice, speech, project):
    with client.websocket_connect("/api/voice") as other_socket:
        other = Voice(other_socket)
        ask_to_run(voice, speech, project)
        other.send(type="stop")
        seen = other.until("stopped")
        assert not [m for m in seen if m["type"] in ("speech", "asking")]
