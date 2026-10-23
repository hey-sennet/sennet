"""What asking for a <spoken> part costs: the same requests with and without SPOKEN_INSTRUCTIONS.

Drives Claude Code and Codex through Sennet's adapters, a new session per request, and
records each turn's token usage and how long its spoken part is. Not product code, and it
never touches the running service (its own MCP endpoint, a scratch repository). Each turn
is a real request on the subscription.

    .venv/bin/python spikes/tokens/spoken.py SCRATCH_DIR [--runs 2] [--agents claude,codex]
    .venv/bin/python spikes/tokens/spoken.py SCRATCH_DIR --summary

Writes one JSON line per turn to SCRATCH_DIR/tokens.jsonl.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import socket
import statistics
import subprocess
import sys
from pathlib import Path

import uvicorn
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse, Response
from starlette.routing import Route

from sennet.agents import claude, codex, registry
from sennet.agents.base import AgentEvent, TurnPolicy
from sennet.agents.callbacks import CallbackServer
from sennet.voice.speakable import spoken_part

# Long enough answers that the instructions ask for a spoken part; no tools, to keep turns alike.
PROMPTS = [
    "Without using any tools, explain how a hash map handles collisions, with the trade-offs "
    "between chaining and open addressing.",
    "Without using any tools, compare optimistic and pessimistic locking for a web app's "
    "database, and say when you'd pick each.",
    "Without using any tools, walk me through what happens between typing a URL in a browser "
    "and the page appearing.",
]
POLICY = TurnPolicy(mode="ask", access="workspace")
INSTRUCTIONS = {"claude": claude.INSTRUCTIONS, "codex": codex.INSTRUCTIONS}

usage: dict = {}


_claude_translate = claude.ClaudeSession._translate


def claude_translate(self, message: dict):
    if message.get("type") == "result":
        u = message.get("usage") or {}
        usage.update(output=u.get("output_tokens"), input=u.get("input_tokens"),
                     cache_read=u.get("cache_read_input_tokens"), cache_write=u.get("cache_creation_input_tokens"))
    return _claude_translate(self, message)


_codex_notification = codex.CodexSession._notification


async def codex_notification(self, method: str, params: dict):
    if method == "thread/tokenUsage/updated":
        last = (params.get("tokenUsage") or {}).get("last") or {}
        usage.update(output=last.get("outputTokens"), reasoning=last.get("reasoningOutputTokens"),
                     input=last.get("inputTokens"), cache_read=last.get("cachedInputTokens"))
    return await _codex_notification(self, method, params)


claude.ClaudeSession._translate = claude_translate
codex.CodexSession._notification = codex_notification


def use_instructions(agent: str, on: bool):
    module = claude if agent == "claude" else codex
    module.INSTRUCTIONS = INSTRUCTIONS[agent] if on else ""


def callback_app(callbacks: CallbackServer) -> Starlette:
    async def mcp(request: Request):
        try:
            response = await callbacks.handle(request.path_params["secret"], await request.json())
        except LookupError:
            return Response(status_code=404)
        return JSONResponse(response) if response is not None else Response(status_code=202)
    return Starlette(routes=[Route("/mcp/{secret}", mcp, methods=["POST"])])


async def turn(agent, cwd: Path, text: str) -> dict:
    usage.clear()
    done = asyncio.Event()
    messages: list[str] = []

    async def on_event(event: AgentEvent):
        if event.kind == "message":
            messages.append(event.data["text"])
        if event.kind == "turn_completed":
            done.set()

    async def decline(_):
        return "decline"

    session = await agent.open(cwd, None, POLICY, on_event, decline)
    try:
        await session.send(text)
        await asyncio.wait_for(done.wait(), 300)
        await asyncio.sleep(0.5)  # Codex's usage can follow the turn's end
    finally:
        await session.close()
    reply = messages[-1] if messages else ""
    spoken = spoken_part(reply) or ""
    return {**usage, "reply_chars": len(reply), "spoken_chars": len(spoken)}


def summary(path: Path):
    rows: dict[tuple, list[dict]] = {}
    for line in path.read_text().splitlines():
        r = json.loads(line)
        rows.setdefault((r["agent"], r["instructions"]), []).append(r)
    print(f"{'':22}{'n':>3}{'output':>9}{'reasoning':>11}{'input+cache':>13}{'reply ch':>10}{'spoken ch':>11}")
    for (agent, on), records in sorted(rows.items()):
        def median(key):
            values = [r[key] for r in records if r.get(key) is not None]
            return f"{statistics.median(values):.0f}" if values else "—"
        inputs = [sum(r.get(k) or 0 for k in ("input", "cache_read", "cache_write")) for r in records]
        print(f"{agent + (' with' if on else ' without'):22}{len(records):>3}{median('output'):>9}"
              f"{median('reasoning'):>11}{statistics.median(inputs):>13.0f}{median('reply_chars'):>10}"
              f"{median('spoken_chars'):>11}")


async def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("scratch", type=Path)
    parser.add_argument("--runs", type=int, default=2)
    parser.add_argument("--agents", default="claude,codex")
    parser.add_argument("--summary", action="store_true")
    args = parser.parse_args()
    args.scratch.mkdir(parents=True, exist_ok=True)
    path = args.scratch / "tokens.jsonl"
    if args.summary:
        return summary(path)
    cwd = args.scratch / "project"
    if not (cwd / ".git").exists():
        cwd.mkdir(exist_ok=True)
        subprocess.run(["git", "init", "-q", str(cwd)], check=True)
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    callbacks = CallbackServer(f"http://127.0.0.1:{port}")
    server = uvicorn.Server(uvicorn.Config(callback_app(callbacks), host="127.0.0.1", port=port, log_level="warning"))
    serving = asyncio.create_task(server.serve())
    agents = registry.build({}, callbacks)
    try:
        with path.open("a") as out:
            for run in range(args.runs):
                for name in args.agents.split(","):
                    for index, prompt in enumerate(PROMPTS):
                        for on in (True, False):  # interleaved, so drift hits both alike
                            use_instructions(name, on)
                            record = {"agent": name, "instructions": on, "prompt": index, "run": run,
                                      **await turn(agents[name], cwd, prompt)}
                            out.write(json.dumps(record) + "\n")
                            out.flush()
                            print(record, flush=True)
    finally:
        server.should_exit = True
        await serving
    summary(path)


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
