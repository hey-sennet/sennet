import json
import sys
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from agent_talk.config import Paths, Settings
from agent_talk.server import create_app

FAKE_CODEX = Path(__file__).parent / "fake_codex.py"
HEADERS = {"X-Agent-Talk": "1"}


@pytest.fixture
def settings():
    settings = Settings()
    settings.agents.commands["codex"] = [sys.executable, str(FAKE_CODEX)]
    return settings


@pytest.fixture
def paths(tmp_path):
    return Paths(tmp_path / "config", tmp_path / "data")


@pytest.fixture
def codex_log(tmp_path, monkeypatch):
    path = tmp_path / "codex.log"
    monkeypatch.setenv("FAKE_CODEX_LOG", str(path))

    def calls(method: str | None = None) -> list[dict]:
        if not path.exists():
            return []
        entries = [json.loads(line) for line in path.read_text().splitlines()]
        return [e for e in entries if method is None or e["method"] == method]

    return calls


@pytest.fixture
def client(settings, paths, codex_log):
    with TestClient(create_app(settings, paths), headers=HEADERS) as client:
        yield client


@pytest.fixture
def project(client, tmp_path):
    directory = tmp_path / "repo"
    directory.mkdir()
    response = client.post("/api/projects", json={"path": str(directory), "name": "backend", "aliases": ["api"]})
    assert response.status_code == 201, response.text
    return response.json()


def wait_for(client, task_id: int, predicate, timeout: float = 10) -> dict:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        task = client.get(f"/api/tasks/{task_id}").json()
        if predicate(task):
            return task
        time.sleep(0.02)
    raise AssertionError(f"task {task_id} never matched; last state: {task['status']}")


def pending_approval(client, timeout: float = 10) -> dict:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        approvals = client.get("/api/state").json()["approvals"]
        if approvals:
            return approvals[0]
        time.sleep(0.02)
    raise AssertionError("no approval was requested")
