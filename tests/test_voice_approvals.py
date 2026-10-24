"""Approvals are answered on their card, never by voice (voice-protocol §8)."""
import pytest

from conftest import pending_approval, wait_for
from test_voice import client, speech, voice  # noqa: F401  (fixtures)

pytestmark = pytest.mark.timeout(20)


@pytest.fixture(autouse=True)
def open_gate(speech):
    speech.tts.gate.set()


def test_a_yes_while_an_approval_waits_is_a_request(client, voice, speech, project):
    asking = client.post("/api/tasks", json={"project_id": project["id"], "text": "run:npm test"}).json()
    wait_for(client, asking["id"], lambda t: t["status"] == "awaiting_approval")
    approval = pending_approval(client)
    speech.stt.transcript = "Yes."
    voice.talk(project_id=project["id"])
    seen = voice.until("final")
    assert seen[-1]["outcome"] == "dispatched" and seen[-1]["task_id"] != asking["id"]
    assert "asking" not in [m["type"] for m in seen]
    assert client.app.state.db.approval(approval["id"])["status"] == "pending"
