"""Sennet's own MCP endpoint, for agent CLIs that call back into it.

Claude Code asks a permission prompt tool, an MCP tool, before a tool call that
needs the user's say. The daemon serves such tools over HTTP at `/mcp/<secret>`,
with one secret per agent session: the adapter registers its tools when the
session starts, passes the URL to its CLI, and unregisters when it closes, which
abandons any call still waiting. Only a live session's secret is answered.

This is the least of MCP's HTTP transport a client needs to call a tool: JSON-RPC
requests in POST bodies, answered with one JSON response each, and no sessions,
streams or server-sent requests.
"""
from __future__ import annotations

import asyncio
import logging
import secrets
from dataclasses import dataclass, field
from typing import Awaitable, Callable

from .. import __version__

log = logging.getLogger(__name__)

PROTOCOL_VERSION = "2025-06-18"
SERVER_NAME = "sennet"

# A tool call: the tool's name and arguments in, its text result out.
ToolCall = Callable[[str, dict], Awaitable[str]]


@dataclass
class Registration:
    secret: str
    url: str
    tools: list[dict]           # MCP tool definitions: name, description, inputSchema
    call: ToolCall
    calls: set[asyncio.Task] = field(default_factory=set)


class CallbackServer:
    def __init__(self, base_url: str = ""):
        self.base_url = base_url    # where the daemon listens, as its agents reach it
        self.sessions: dict[str, Registration] = {}

    def register(self, tools: list[dict], call: ToolCall) -> Registration:
        secret = secrets.token_urlsafe(32)
        registration = Registration(secret, f"{self.base_url}/mcp/{secret}", tools, call)
        self.sessions[secret] = registration
        return registration

    async def unregister(self, registration: Registration):
        self.sessions.pop(registration.secret, None)
        for call in list(registration.calls):
            call.cancel()
        await asyncio.gather(*registration.calls, return_exceptions=True)

    async def handle(self, secret: str, message: dict) -> dict | None:
        """The JSON-RPC response to `message`, or None for a notification. LookupError for an unknown secret."""
        registration = self.sessions.get(secret)
        if registration is None:
            raise LookupError("no such session")
        method, request_id = message.get("method"), message.get("id")
        if request_id is None:
            return None
        params = message.get("params") or {}
        if method == "initialize":
            result = {"protocolVersion": params.get("protocolVersion", PROTOCOL_VERSION),
                      "capabilities": {"tools": {}},
                      "serverInfo": {"name": SERVER_NAME, "title": "Sennet", "version": __version__}}
        elif method == "tools/list":
            result = {"tools": registration.tools}
        elif method == "tools/call":
            names = {tool["name"] for tool in registration.tools}
            if params.get("name") not in names:
                return _error(request_id, -32602, f"no tool called {params.get('name')!r}")
            call = asyncio.ensure_future(registration.call(params["name"], params.get("arguments") or {}))
            registration.calls.add(call)
            try:
                text = await call
            except asyncio.CancelledError:
                if registration.secret in self.sessions:
                    raise  # the request itself was cancelled
                return _error(request_id, -32603, "the session ended")
            except Exception:
                log.exception("tool %s failed", params["name"])
                return _error(request_id, -32603, "internal error")
            finally:
                registration.calls.discard(call)
            result = {"content": [{"type": "text", "text": text}]}
        elif method in ("ping", "server/discover"):
            result = {}
        else:
            return _error(request_id, -32601, f"method {method!r} isn't supported")
        return {"jsonrpc": "2.0", "id": request_id, "result": result}


def _error(request_id, code: int, message: str) -> dict:
    return {"jsonrpc": "2.0", "id": request_id, "error": {"code": code, "message": message}}
