"""The daemon's MCP endpoint for agent CLIs: who may call it, and a tool call that waits."""
import asyncio
import threading

import pytest
from fastapi.testclient import TestClient

from agent_talk.server import create_app

TOOL = {"name": "approve", "description": "", "inputSchema": {"type": "object"}}


@pytest.fixture
def local(settings, paths):
    with TestClient(create_app(settings, paths), client=("127.0.0.1", 50000)) as client:
        yield client


def rpc(client, url, method, params=None, request_id=1, **kw):
    return client.post(url, json={"jsonrpc": "2.0", "id": request_id, "method": method, "params": params or {}}, **kw)


def register(client, call):
    return client.app.state.callbacks.register([TOOL], call)


def test_the_url_points_at_the_daemon(local):
    async def call(name, arguments):
        return ""

    registration = register(local, call)
    assert registration.url.startswith("http://127.0.0.1:7710/mcp/")
    assert len(registration.secret) >= 40


def test_a_session_lists_and_calls_its_tools(local):
    async def call(name, arguments):
        return f"{name} {arguments['tool_name']}"

    url = "/mcp/" + register(local, call).secret
    assert rpc(local, url, "server/discover").json()["result"] == {}
    assert rpc(local, url, "initialize", {"protocolVersion": "2025-11-25"}).json()["result"]["protocolVersion"] == \
        "2025-11-25"
    assert local.post(url, json={"jsonrpc": "2.0", "method": "notifications/initialized"}).status_code == 202
    assert rpc(local, url, "tools/list").json()["result"]["tools"] == [TOOL]
    response = rpc(local, url, "tools/call", {"name": "approve", "arguments": {"tool_name": "Bash"}}).json()
    assert response["result"]["content"] == [{"type": "text", "text": "approve Bash"}]
    assert rpc(local, url, "tools/call", {"name": "other"}).json()["error"]["code"] == -32602


def test_only_a_live_sessions_secret_is_answered(local):
    assert rpc(local, "/mcp/guess", "tools/list").status_code == 404


def test_browsers_and_other_machines_are_refused(settings, paths, local):
    async def call(name, arguments):
        return ""

    url = "/mcp/" + register(local, call).secret
    assert rpc(local, url, "tools/list", headers={"Origin": "http://127.0.0.1:7710"}).status_code == 403
    with TestClient(create_app(settings, paths), client=("100.64.0.9", 50000)) as remote:
        remote_url = "/mcp/" + register(remote, call).secret
        assert rpc(remote, remote_url, "tools/list").status_code == 403


def test_a_call_waits_for_its_answer_and_ends_with_the_session(local):
    started = threading.Event()

    async def call(name, arguments):
        started.set()
        await asyncio.Event().wait()  # the user never answers

    registration = register(local, call)
    responses = []
    caller = threading.Thread(target=lambda: responses.append(
        rpc(local, f"/mcp/{registration.secret}", "tools/call", {"name": "approve", "arguments": {}}).json()))
    caller.start()
    assert started.wait(5)
    local.portal.call(lambda: local.app.state.callbacks.unregister(registration))
    caller.join(5)
    assert responses[0]["error"]["message"] == "the session ended"
