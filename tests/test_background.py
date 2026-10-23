"""Work an agent leaves running after its turn, and the turns it starts itself (M4 step 2)."""
import time

import pytest
from fastapi.testclient import TestClient

from agent_talk.agents.base import AgentEvent, check_event
from agent_talk.db import Database
from agent_talk.server import create_app
from conftest import HEADERS, wait_for, paired
from scripted_agent import ScriptedAgent


@pytest.fixture
def agent():
    return ScriptedAgent()


@pytest.fixture
def scripted(settings, paths, agent, tmp_path):
    (tmp_path / "repo").mkdir()
    with paired(TestClient(create_app(settings, paths, agents={"codex": agent}), headers=HEADERS)) as client:
        client.post("/api/projects", json={"path": str(tmp_path / "repo")})
        yield client


def start(client, text="bg:sleep 25"):
    project = client.get("/api/state").json()["projects"][0]
    task = client.post("/api/tasks", json={"project_id": project["id"], "text": text}).json()
    return wait_for(client, task["id"], lambda t: t["status"] == "background")


def replies(task):
    return [m["text"] for m in task["messages"] if m["role"] == "agent"]


def test_the_contract_has_background_work_and_agent_turns():
    assert not check_event(AgentEvent("background", {"tasks": [{"id": "b1", "description": "sleep"}]}))
    assert not check_event(AgentEvent("background", {"tasks": []}))
    assert check_event(AgentEvent("background", {"tasks": [{"task_id": "b1"}]}))
    assert not check_event(AgentEvent("turn_started", {"by": "agent"}))
    assert check_event(AgentEvent("turn_started", {"by": "someone"}))


def test_work_left_running_keeps_the_task_in_the_background_until_it_reports(scripted, agent):
    task = start(scripted)
    assert replies(task) == ["Started it."]
    scripted.portal.call(agent.sessions[0].finish_background)
    task = wait_for(scripted, task["id"], lambda t: t["status"] == "completed" and len(replies(t)) == 2)
    assert replies(task) == ["Started it.", "It finished."]
    assert [e["data"] for e in task["events"] if e["kind"] == "turn_started"] == [{"by": "agent"}]
    assert len(agent.sessions) == 1


def test_a_follow_up_runs_while_the_work_goes_on(scripted, agent):
    task = start(scripted)
    scripted.post(f"/api/tasks/{task['id']}/messages", json={"text": "what is 2 + 2?"})
    task = wait_for(scripted, task["id"], lambda t: t["status"] == "background" and len(replies(t)) == 2)
    assert replies(task)[-1] == "You said: what is 2 + 2?"
    assert len(agent.sessions) == 1


def test_messages_sent_during_the_agents_turn_wait_for_it(scripted, agent):
    task = start(scripted)
    session = agent.sessions[0]
    scripted.portal.call(lambda: session.finish_background(hold=True))
    wait_for(scripted, task["id"], lambda t: t["status"] == "running")
    scripted.post(f"/api/tasks/{task['id']}/messages", json={"text": "and then?"})
    assert session.sent == ["bg:sleep 25"]  # held back until the agent's turn ends
    scripted.portal.call(session.release)
    task = wait_for(scripted, task["id"], lambda t: t["status"] == "completed" and len(replies(t)) == 3)
    assert replies(task) == ["Started it.", "It finished.", "You said: and then?"]
    assert session.sent == ["bg:sleep 25", "and then?"]


def test_the_process_stays_up_while_work_runs_and_closes_once_idle(settings, paths, agent, tmp_path):
    settings.tasks.idle_timeout_s = 0
    (tmp_path / "repo").mkdir()
    with paired(TestClient(create_app(settings, paths, agents={"codex": agent}), headers=HEADERS)) as client:
        client.post("/api/projects", json={"path": str(tmp_path / "repo")})
        start(client)
        session = agent.sessions[0]
        assert session.alive
        client.portal.call(lambda: session.finish_background(report=False))
        task = wait_for(client, 1, lambda t: t["status"] == "completed")
        deadline = time.monotonic() + 5
        while session.alive and time.monotonic() < deadline:
            client.get("/api/state")
    assert not session.alive
    assert task["status"] == "completed"


def test_cancelling_a_task_with_background_work_stops_the_agent(scripted, agent):
    task = start(scripted)
    task = scripted.post(f"/api/tasks/{task['id']}/cancel").json()
    assert task["status"] == "cancelled"
    assert not agent.sessions[0].alive


def test_background_work_does_not_survive_a_restart(tmp_path):
    db = Database(tmp_path / "db.sqlite")
    db.add_project("app", "/app", [], "codex")
    task_id = db.add_task(1, "codex", "t")
    db.update_task(task_id, status="background")
    db.recover()
    assert db.task(task_id)["status"] == "unknown"
    db.close()
