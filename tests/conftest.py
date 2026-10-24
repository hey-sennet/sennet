import json
import shlex
import socket
import sys
import threading
import time
from contextlib import contextmanager
from pathlib import Path

import httpx
import pytest
import uvicorn
from fastapi.testclient import TestClient

from sennet import access, service
from sennet.config import Paths, Settings
from sennet.server import create_app

FAKE_CODEX = Path(__file__).parent / "fake_codex.py"
FAKE_CLAUDE = Path(__file__).parent / "fake_claude.py"
HEADERS = {"X-Sennet": "1"}


@pytest.fixture(autouse=True)
def no_real_service(monkeypatch):
    """Keep tests off this machine's service: a test that stops it kills Sennet, and an agent running
    the tests inside Sennet with it. Tests that drive the service manager fake `_run` themselves."""
    def refuse(command, check=True):
        pytest.fail(f"a test ran {shlex.join(command)} for real; fake service.manager or service._run")

    monkeypatch.setattr(service, "_run", refuse)


def pair(client, db) -> dict:
    """Pair a test client as a browser would, so it gets a device cookie."""
    code = access.new_pairing(db, access.TERMINAL, 60).link_code
    response = client.post("/api/pair", json={"code": code}, headers=HEADERS)
    assert response.status_code == 200, response.text
    return response.json()


@contextmanager
def paired(client: TestClient):
    """Start a TestClient's app and pair the client."""
    with client:
        pair(client, client.app.state.db)
        yield client


@pytest.fixture
def settings():
    settings = Settings()
    settings.agents.commands["codex"] = [sys.executable, str(FAKE_CODEX)]
    settings.agents.commands["claude"] = [sys.executable, str(FAKE_CLAUDE)]
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
def claude_log(tmp_path, monkeypatch):
    path = tmp_path / "claude.log"
    monkeypatch.setenv("FAKE_CLAUDE_LOG", str(path))

    def entries(key: str) -> list:
        if not path.exists():
            return []
        return [e[key] for e in map(json.loads, path.read_text().splitlines()) if key in e]

    return entries


@pytest.fixture
def live(settings, paths, codex_log, claude_log):
    """The app on a real loopback port, for agents that call back into it over HTTP."""
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        settings.server.port = probe.getsockname()[1]
    app = create_app(settings, paths)
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1",
                                           port=settings.server.port, log_level="warning"))
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    deadline = time.monotonic() + 10
    while not server.started and time.monotonic() < deadline:
        time.sleep(0.02)
    with httpx.Client(base_url=f"http://127.0.0.1:{settings.server.port}", headers=HEADERS) as client:
        pair(client, app.state.db)
        yield client
    server.should_exit = True
    thread.join(10)


@pytest.fixture
def client(settings, paths, codex_log):
    with paired(TestClient(create_app(settings, paths), headers=HEADERS)) as client:
        yield client


@pytest.fixture
def project(client, tmp_path):
    directory = tmp_path / "repo"
    directory.mkdir()
    response = client.post("/api/projects", json={"path": str(directory), "name": "backend"})
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
