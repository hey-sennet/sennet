"""The provider-neutral agent contract: vocabularies, capabilities and migrations.

Every adapter must pass the conformance checks here; Codex is the first.
"""
import json
import sqlite3
from dataclasses import replace
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from agent_talk.agents import registry
from agent_talk.agents.base import AgentEvent, check_event, spoken_instructions
from agent_talk.agents.codex import CodexAgent, CodexSession
from agent_talk.config import Settings
from agent_talk.db import SCHEMA_VERSION, Database
from agent_talk.server import create_app
from conftest import HEADERS, pending_approval, wait_for


def finished(task):
    return task["status"] in ("completed", "failed", "cancelled", "unknown")


def assert_conforms(events: list[dict]):
    problems = [p for e in events for p in check_event(AgentEvent(e["kind"], e["data"]))]
    assert problems == []


# Vocabularies

def test_check_event_catches_provider_words():
    assert check_event(AgentEvent("turn_completed", {"status": "interrupted", "error": None}))
    assert check_event(AgentEvent("plan", {"steps": [{"step": "x", "status": "inProgress"}]}))
    assert check_event(AgentEvent("file_change", {"item_id": "1", "status": "completed",
                                                  "changes": [{"path": "a", "kind": "rename"}]}))
    assert check_event(AgentEvent("command_finished", {"item_id": "1"}))  # fields missing
    assert check_event(AgentEvent("thinking", {}))
    assert check_event(AgentEvent("message", {"item_id": "1", "text": "hi", "phase": "final"}))  # not the contract's
    assert not check_event(AgentEvent("error", {"message": "slow down", "will_retry": True}))  # optional
    assert not check_event(AgentEvent("turn_completed", {"status": "cancelled", "error": None}))


@pytest.mark.parametrize("method, params, kind, expected", [
    ("turn/completed", {"turn": {"status": "interrupted"}}, "turn_completed", {"status": "cancelled"}),
    ("turn/completed", {"turn": {"status": "someNewStatus"}}, "turn_completed", {"status": "failed"}),
    ("turn/plan/updated", {"plan": [{"step": "a", "status": "inProgress"}, {"step": "b", "status": "completed"}]},
     "plan", {"steps": [{"step": "a", "status": "active"}, {"step": "b", "status": "done"}]}),
    ("item/completed", {"item": {"type": "commandExecution", "id": "c", "command": "/bin/bash -lc 'ls'",
                                 "status": "declined"}}, "command_finished", {"status": "declined", "command": "ls"}),
    ("item/completed", {"item": {"type": "fileChange", "id": "f", "status": "inProgress", "changes": [
        {"path": "a.py", "kind": {"type": "update", "move_path": "b.py"}},
        {"path": "c.py", "kind": {"type": "add"}}]}},
     "file_change", {"status": "running", "changes": [{"path": "a.py", "kind": "move"}, {"path": "c.py", "kind": "add"}]}),
])
def test_codex_translates_into_the_contract(method, params, kind, expected):
    event = CodexSession(None, None)._translate(method, params)
    assert event.kind == kind
    assert {k: event.data[k] for k in expected} == expected
    assert check_event(event) == []


def test_every_event_from_a_codex_session_conforms(client, project):
    texts = ["hello", "run:npm test", "edit:src/app.py", "hang", "crash"]
    for text in texts:
        task = client.post("/api/tasks", json={"project_id": project["id"], "text": text}).json()
        if text.startswith(("run:", "edit:")):
            client.post(f"/api/approvals/{pending_approval(client)['id']}", json={"decision": "approve"})
        if text == "hang":
            wait_for(client, task["id"], lambda t: t["messages"][0]["state"] == "sent")
            client.post(f"/api/tasks/{task['id']}/cancel")
        snapshot = wait_for(client, task["id"], finished)
        assert_conforms(snapshot["events"])
        assert snapshot["events"], text


# Capabilities

class LimitedCodex(CodexAgent):
    def __init__(self, command, **limits):
        super().__init__(command)
        self.capabilities = replace(CodexAgent.capabilities, **limits)


def limited_client(settings, paths, **limits):
    agent = LimitedCodex(settings.agents.commands["codex"], **limits)
    return TestClient(create_app(settings, paths, agents={"codex": agent}), headers=HEADERS)


def add_project(client, tmp_path):
    (tmp_path / "repo").mkdir(exist_ok=True)
    return client.post("/api/projects", json={"path": str(tmp_path / "repo")}).json()


def test_an_agent_that_cannot_interrupt_is_stopped(settings, paths, codex_log, tmp_path):
    with limited_client(settings, paths, interrupt=False) as client:
        project = add_project(client, tmp_path)
        task = client.post("/api/tasks", json={"project_id": project["id"], "text": "hang"}).json()
        wait_for(client, task["id"], lambda t: t["status"] == "running")
        client.post(f"/api/tasks/{task['id']}/cancel")
        task = wait_for(client, task["id"], finished)
    assert task["status"] == "unknown"
    assert "can't be interrupted" in task["error"]
    assert codex_log("turn/interrupt") == []


def test_an_agent_that_cannot_resume_starts_a_new_conversation(settings, paths, codex_log, tmp_path):
    settings.tasks.idle_timeout_s = 0
    with limited_client(settings, paths, resume=False) as client:
        project = add_project(client, tmp_path)
        task = client.post("/api/tasks", json={"project_id": project["id"], "text": "one"}).json()
        wait_for(client, task["id"], lambda t: t["status"] == "completed")
        client.post(f"/api/tasks/{task['id']}/messages", json={"text": "two"})
        wait_for(client, task["id"], lambda t: t["status"] == "completed" and len(t["messages"]) == 4)
    assert codex_log("thread/resume") == []
    assert len(codex_log("thread/start")) == 2


def test_an_unsupported_approval_mode_is_refused(settings, paths, codex_log, tmp_path):
    with limited_client(settings, paths, approval_modes=("never",)) as client:
        project = add_project(client, tmp_path)
        response = client.patch(f"/api/projects/{project['id']}", json={"approval_mode": "ask"})
        assert response.status_code == 400 and "doesn't support" in response.json()["detail"]
        task = client.post("/api/tasks", json={"project_id": project["id"], "text": "hi"}).json()  # default is ask
        task = wait_for(client, task["id"], finished)
    assert task["status"] == "failed" and "doesn't support the 'ask' approval mode" in task["error"]
    assert codex_log("thread/start") == []


def test_agents_report_their_capabilities(client):
    codex = client.get("/api/agents").json()["codex"]
    assert codex["capabilities"] == {"interrupt": True, "resume": True, "live_approvals": True,
                                     "approval_modes": ["ask", "auto", "never"],
                                     "access_levels": ["read-only", "workspace", "full"], "background_turns": False}


def test_agents_are_asked_for_a_version_to_be_heard(client, project, codex_log):
    task = client.post("/api/tasks", json={"project_id": project["id"], "text": "hi"}).json()
    wait_for(client, task["id"], finished)
    instructions = codex_log("thread/start")[0]["params"]["developerInstructions"]
    assert instructions == spoken_instructions("request_user_input_async")
    assert "<spoken></spoken>" in instructions


def test_the_shared_spoken_instructions_name_no_tool():
    assert "(such as" not in spoken_instructions()
    assert "don't use a tool to ask them: they can't answer" in spoken_instructions()


# Access levels and migrations

def test_access_maps_to_the_codex_sandbox(client, project, codex_log):
    client.patch(f"/api/projects/{project['id']}", json={"access": "read-only"})
    task = client.post("/api/tasks", json={"project_id": project["id"], "text": "hi"}).json()
    wait_for(client, task["id"], finished)
    assert codex_log("thread/start")[0]["params"]["sandbox"] == "read-only"
    assert client.patch(f"/api/projects/{project['id']}", json={"access": "workspace-write"}).status_code == 400


def test_old_config_sandbox_key_still_loads(tmp_path):
    config = tmp_path / "config.toml"
    config.write_text('[approvals]\nsandbox = "danger-full-access"\n')
    assert Settings.load(config).approvals.access == "full"


V1_SCHEMA = (Path(__file__).parent / "fixtures" / "schema_v1.sql").read_text()


def test_a_v1_database_is_migrated(tmp_path):
    path = tmp_path / "old.db"
    old = sqlite3.connect(path)
    old.executescript(V1_SCHEMA)
    old.execute("INSERT INTO projects (id, name, path, sandbox, created_at) VALUES (1, 'app', '/app', 'danger-full-access', 0)")
    old.execute("INSERT INTO tasks (id, project_id, agent, title, status, thread_id, created_at, updated_at) "
                "VALUES (1, 1, 'codex', 't', 'completed', 'thread-9', 0, 0)")
    for kind, data in [("turn_completed", {"status": "interrupted", "error": None}),
                       ("plan", {"steps": [{"step": "a", "status": "inProgress"}, {"step": "b", "status": "completed"}]}),
                       ("file_change", {"item_id": "f", "status": "inProgress", "changes": []}),
                       ("command_finished", {"item_id": "c", "command": "ls", "status": "declined",
                                             "exit_code": None, "output_tail": ""})]:
        old.execute("INSERT INTO task_events (task_id, kind, data, created_at) VALUES (1, ?, ?, 0)", (kind, json.dumps(data)))
    old.commit()
    old.close()

    db = Database(path)
    assert db.project(1)["access"] == "full"
    assert db.task(1)["resume_token"] == "thread-9"
    events = {e["kind"]: e["data"] for e in db.events(1)}
    assert events["turn_completed"]["status"] == "cancelled"
    assert [s["status"] for s in events["plan"]["steps"]] == ["active", "done"]
    assert events["file_change"]["status"] == "running"
    assert events["command_finished"]["status"] == "declined"
    assert db.one("PRAGMA user_version")["user_version"] == SCHEMA_VERSION
    db.close()
    Database(path).close()  # opening again changes nothing


def test_a_new_database_starts_at_the_current_version(tmp_path):
    db = Database(tmp_path / "new.db")
    assert db.one("PRAGMA user_version")["user_version"] == SCHEMA_VERSION
    assert "access" in {c["name"] for c in db.query("PRAGMA table_info(projects)")}
    db.close()


# The registry and configuration

def test_every_adapter_states_its_capabilities():
    with pytest.raises(TypeError):
        type(CodexAgent.capabilities)()  # nothing is claimed by default
    for adapter in registry.ADAPTERS.values():
        assert adapter.default_command


def test_agents_are_built_from_the_registry_with_configured_commands():
    agents = registry.build({"codex": ["my-codex", "app-server"]})
    assert list(agents) == list(registry.NAMES)
    assert agents["codex"].command == ["my-codex", "app-server"]
    assert registry.build({})["codex"].command == CodexAgent.default_command


def test_the_old_codex_command_setting_still_loads(tmp_path):
    config = tmp_path / "config.toml"
    config.write_text('[agents]\ncodex_command = ["codex-dev", "app-server"]\n')
    settings = Settings.load(config)
    assert settings.agents.commands == {"codex": ["codex-dev", "app-server"]}
    settings.save(config)
    assert Settings.load(config).agents.commands == {"codex": ["codex-dev", "app-server"]}


def test_a_command_for_an_unknown_agent_is_a_config_error(tmp_path):
    config = tmp_path / "config.toml"
    config.write_text('[agents.commands]\nnobody = ["x"]\n')
    with pytest.raises(ValueError, match="no agent called nobody"):
        Settings.load(config)
