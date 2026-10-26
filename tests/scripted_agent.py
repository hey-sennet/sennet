"""An in-process agent with background work, for the generic layer's tests.

Turn behaviour is driven by the user's text:
  bg:<description>  start background work, say so, and end the turn
  anything else     a short reply
`finish_background` ends the work and, unless told not to, starts a turn of the
agent's own that reports it; with `hold`, that turn waits for `release`.
"""
from __future__ import annotations

import asyncio
from pathlib import Path

from sennet.agents.base import (ACCESS_LEVELS, APPROVAL_MODES, AgentEvent, AgentStatus, ApprovalHandler,
                                    Capabilities, EventSink, ModelOption, TurnPolicy)


class ScriptedSession:
    def __init__(self, on_event: EventSink):
        self.on_event = on_event
        self.resume_token = "scripted-1"
        self.alive = True
        self.sent: list[str] = []
        self.released = asyncio.Event()
        self.jobs: set[asyncio.Task] = set()

    async def send(self, text: str, images=()):
        self.sent.append(text)
        self._spawn(self._turn(text))

    async def _turn(self, text: str):
        if text.startswith("bg:"):
            await self.on_event(AgentEvent("background", {"tasks": [{"id": "b1", "description": text[3:]}]}))
            await self._reply("Started it.")
        else:
            await self._reply(f"You said: {text}")
        await self.on_event(AgentEvent("turn_completed", {"status": "completed", "error": None}))

    async def finish_background(self, report: bool = True, hold: bool = False):
        await self.on_event(AgentEvent("background", {"tasks": []}))
        if report:
            self._spawn(self._agent_turn(hold))

    async def _agent_turn(self, hold: bool):
        await self.on_event(AgentEvent("turn_started", {"by": "agent"}))
        if hold:
            await self.released.wait()
        await self._reply("It finished.")
        await self.on_event(AgentEvent("turn_completed", {"status": "completed", "error": None}))

    async def release(self):
        self.released.set()

    async def _reply(self, text: str):
        await self.on_event(AgentEvent("message_delta", {"item_id": "m", "delta": text}))
        await self.on_event(AgentEvent("message", {"item_id": "m", "text": text}))

    def _spawn(self, coroutine):
        job = asyncio.ensure_future(coroutine)
        self.jobs.add(job)
        job.add_done_callback(self.jobs.discard)

    async def interrupt(self):
        pass

    async def close(self):
        self.alive = False
        for job in list(self.jobs):
            job.cancel()


class ScriptedAgent:
    name = "scripted"
    title = "Scripted"
    default_command = ["scripted"]
    capabilities = Capabilities(interrupt=False, resume=False, live_approvals=False, approval_modes=APPROVAL_MODES,
                                access_levels=ACCESS_LEVELS, background_turns=True,
                                project_config=False, live_model_change=False, images=False)

    def __init__(self, command: list[str] | None = None):
        self.sessions: list[ScriptedSession] = []

    async def status(self) -> AgentStatus:
        return AgentStatus(True, True, "scripted")

    async def models(self) -> list[ModelOption]:
        return []

    async def open(self, cwd: Path, resume_token: str | None, policy: TurnPolicy, on_event: EventSink,
                   on_approval: ApprovalHandler) -> ScriptedSession:
        session = ScriptedSession(on_event)
        self.sessions.append(session)
        return session
