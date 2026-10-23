"""Where the time goes between sending a message to an agent and its first words.

Step 0 of the latency idea: drives Claude Code and Codex through Sennet's own adapters, with
the same arguments, instructions and MCP callback as the daemon, and timestamps each stage of
a turn. Scenarios, per agent and run:

  cold       a new session: start the process, then the first message
  warm       a second message to the same process
  resumed    the process closed (as the idle timeout does), then resumed for a message
  pre-warmed resumed, then the message a few seconds later, as pre-warm on intent would
  interrupt  a message interrupted at its first words, then the follow-up. Claude's process
             ends on SIGINT, so its follow-up is a resume; Codex's stays up.

It is not product code, and it never touches the running service: it serves its own MCP
endpoint on a free port and runs the agents in a scratch directory. Each turn is a real
request on the subscription.

    .venv/bin/python spikes/latency/agents.py SCRATCH_DIR [--runs 3] [--agents claude,codex]

Writes one JSON line per turn to SCRATCH_DIR/latency.jsonl and prints medians.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import socket
import statistics
import subprocess
import sys
import time
from pathlib import Path

import uvicorn
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse, Response
from starlette.routing import Route

from sennet.agents import claude, codex, registry
from sennet.agents.base import AgentEvent, ApprovalRequest, Decision, TurnPolicy
from sennet.agents.callbacks import CallbackServer

PROMPT = "Without using any tools, tell me in two sentences why the sky is blue."
LONG_PROMPT = "Without using any tools, explain in about ten sentences how a rainbow forms."
FOLLOW_UP = "Actually, just one sentence: why is a sunset red?"
PREWARM_S = 5          # about how long a spoken request takes to say and transcribe
POLICY = TurnPolicy(mode="ask", access="workspace")  # the defaults in config.toml


class Clock:
    """Marks for one turn, in seconds since it began. The first time each mark is reached."""

    def __init__(self):
        self.start = time.monotonic()
        self.marks: dict[str, float] = {}
        self.text = ""
        self.extra: dict = {}

    def mark(self, name: str):
        self.marks.setdefault(name, round(time.monotonic() - self.start, 3))


clock: Clock | None = None


def _mark(name: str):
    if clock:
        clock.mark(name)


# Instrument the adapters: raw messages, before they are translated.

_claude_translate = claude.ClaudeSession._translate


def claude_translate(self, message: dict):
    kind, subtype = message.get("type"), message.get("subtype")
    if kind == "system" and subtype == "init":
        _mark("init")
    elif kind == "stream_event":
        event = message.get("event") or {}
        if event.get("type") == "message_start":
            _mark("first_token")  # the API's first response to this turn
        delta = event.get("delta") or {}
        if delta.get("type") == "thinking_delta":
            _mark("first_thinking")
        if delta.get("type") == "text_delta":
            _mark("first_text")
            _text(delta.get("text") or "")
    elif kind == "result" and clock:
        usage = message.get("usage") or {}
        clock.extra.update(duration_api_ms=message.get("duration_api_ms"), input=usage.get("input_tokens"),
                           cache_read=usage.get("cache_read_input_tokens"),
                           cache_write=usage.get("cache_creation_input_tokens"),
                           output=usage.get("output_tokens"))
    return _claude_translate(self, message)


_codex_notification = codex.CodexSession._notification


async def codex_notification(self, method: str, params: dict):
    if method == "turn/started":
        _mark("init")
    elif method == "item/reasoning/summaryTextDelta" or method == "item/reasoning/textDelta":
        _mark("first_thinking")
    elif method == "item/started":
        _mark("first_token")  # the first item of the turn: the model has begun
    elif method == "item/agentMessage/delta":
        _mark("first_text")
        _text(params.get("delta") or "")
    elif method == "thread/tokenUsage/updated" and clock:
        clock.extra["usage"] = params.get("tokenUsage", {}).get("last")
    return await _codex_notification(self, method, params)


_codex_request = codex.Connection.request


async def codex_request(self, method: str, params: dict, **kwargs):
    began = time.monotonic()
    try:
        return await _codex_request(self, method, params, **kwargs)
    finally:
        if clock:
            clock.extra.setdefault("requests", []).append([method, round(time.monotonic() - began, 3)])


def _text(delta: str):
    clock.text += delta
    if "</spoken>" in clock.text:
        _mark("spoken_complete")
    # The hub speaks a finished sentence sooner only with sentence-by-sentence speech (idea 7);
    # this is when that would be possible.
    spoken = clock.text.split("<spoken>", 1)[-1]
    if any(mark in spoken for mark in (". ", "! ", "? ", ".\n")):
        _mark("first_sentence")


claude.ClaudeSession._translate = claude_translate
codex.CodexSession._notification = codex_notification
codex.Connection.request = codex_request


# The MCP endpoint Claude calls for approvals, like the daemon's `/mcp/{secret}`.

def callback_app(callbacks: CallbackServer) -> Starlette:
    async def mcp(request: Request):
        try:
            response = await callbacks.handle(request.path_params["secret"], await request.json())
        except LookupError:
            return Response(status_code=404)
        return JSONResponse(response) if response is not None else Response(status_code=202)
    return Starlette(routes=[Route("/mcp/{secret}", mcp, methods=["POST"])])


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


# Turns

class Turn:
    def __init__(self):
        self.done = asyncio.Event()
        self.status = None
        self.on_text = None
        self.stopping = None

    async def on_event(self, event: AgentEvent):
        if event.kind == "message_delta" and self.on_text:
            # In a task of its own, as a user's stop is: called inline, Codex's reader would wait on
            # the reply to `turn/interrupt` that only it can read.
            callback, self.on_text = self.on_text, None
            self.stopping = asyncio.create_task(callback())
        if event.kind == "turn_completed":
            self.status = event.data.get("status")
            self.done.set()


async def decline(_: ApprovalRequest) -> Decision:
    return "decline"


async def run_turn(agent, cwd: Path, session, token: str | None, text: str, record: dict,
                   interrupt_at_text: bool = False):
    """One message, opening a session first if `session` is None. Returns the session."""
    global clock
    clock = Clock()
    turn = Turn()
    if session is None:
        session = await agent.open(cwd, token, POLICY, turn.on_event, decline)
        clock.mark("opened")  # Claude: process spawned; Codex: initialized, thread started
    else:
        session.on_event = turn.on_event
    if interrupt_at_text:
        async def stop():
            clock.mark("interrupted")
            await session.interrupt()
        turn.on_text = stop
    await session.send(text)
    clock.mark("sent")
    try:
        await asyncio.wait_for(turn.done.wait(), 300)
    except asyncio.TimeoutError:
        turn.status = "timeout"
    clock.mark("turn_completed")
    record.update(marks=clock.marks, status=turn.status, **clock.extra)
    clock = None
    return session


async def scenarios(name: str, agent, cwd: Path, out, run: int):
    def write(scenario: str, record: dict):
        record.update(agent=name, scenario=scenario, run=run)
        out.write(json.dumps(record) + "\n")
        out.flush()
        print(f"  {name} {scenario}: {record['status']} {record['marks']}", flush=True)

    record = {}
    session = await run_turn(agent, cwd, None, None, PROMPT, record)
    write("cold", record)

    record = {}
    session = await run_turn(agent, cwd, session, None, PROMPT, record)
    write("warm", record)

    token = session.resume_token
    await session.close()
    record = {}
    session = await run_turn(agent, cwd, None, token, PROMPT, record)
    write("resumed", record)

    # Pre-warmed: opened while the user is still talking, then the message a few seconds later.
    await session.close()
    turn = Turn()
    session = await agent.open(cwd, token, POLICY, turn.on_event, decline)
    await asyncio.sleep(PREWARM_S)
    record = {}
    session = await run_turn(agent, cwd, session, None, PROMPT, record)
    write("pre-warmed resume", record)

    record = {}
    session = await run_turn(agent, cwd, session, None, LONG_PROMPT, record, interrupt_at_text=True)
    write("interrupted turn", record)
    record = {}
    if session.alive:
        session = await run_turn(agent, cwd, session, None, FOLLOW_UP, record)
    else:
        await session.close()
        session = await run_turn(agent, cwd, None, token, FOLLOW_UP, record)
    write("after interrupt", record)
    await session.close()


ORDER = ["opened", "sent", "init", "first_token", "first_thinking", "first_text", "first_sentence",
         "spoken_complete", "turn_completed"]


def summary(path: Path):
    rows: dict[tuple[str, str], list[dict]] = {}
    for line in path.read_text().splitlines():
        record = json.loads(line)
        rows.setdefault((record["agent"], record["scenario"]), []).append(record["marks"])
    print("\nMedian seconds from the start of each turn (n runs):")
    print(f"{'':28}" + "".join(f"{m[:13]:>14}" for m in ORDER))
    for (agent, scenario), marks in rows.items():
        cells = []
        for m in ORDER:
            values = [r[m] for r in marks if m in r]
            cells.append(f"{statistics.median(values):>14.2f}" if values else f"{'—':>14}")
        print(f"{agent + ' ' + scenario + f' ({len(marks)})':28}" + "".join(cells))


async def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("scratch", type=Path)
    parser.add_argument("--runs", type=int, default=3)
    parser.add_argument("--agents", default="claude,codex")
    parser.add_argument("--summary", action="store_true", help="only print the summary of latency.jsonl")
    args = parser.parse_args()
    args.scratch.mkdir(parents=True, exist_ok=True)
    path = args.scratch / "latency.jsonl"
    if args.summary:
        return summary(path)

    cwd = args.scratch / "project"
    if not (cwd / ".git").exists():
        cwd.mkdir(exist_ok=True)
        subprocess.run(["git", "init", "-q", str(cwd)], check=True)

    port = free_port()
    callbacks = CallbackServer(f"http://127.0.0.1:{port}")
    server = uvicorn.Server(uvicorn.Config(callback_app(callbacks), host="127.0.0.1", port=port, log_level="warning"))
    serving = asyncio.create_task(server.serve())
    agents = registry.build({}, callbacks)
    try:
        with path.open("a") as out:
            for run in range(args.runs):
                for name in args.agents.split(","):
                    print(f"run {run + 1}, {name}", flush=True)
                    await scenarios(name, agents[name], cwd, out, run)
    finally:
        server.should_exit = True
        await serving
    summary(path)


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
