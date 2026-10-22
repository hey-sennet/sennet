"""The daemon's voice sessions, where their transcripts go, and their replies.

A task is owned by the session that last dispatched to it: that session, and no
other, asks its approvals. A reply is spoken on the session whose request it
answers: the hub follows each spoken request's message from `sent` to `done`
on the event bus and speaks the last agent message in between.
"""
from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass

from ..events import EventBus
from ..speech import Speech
from ..tasks import TaskError, TaskManager
from .dispatch import Cancel, Stop, parse
from .session import Turn, VoiceSession
from .speakable import clauses, for_speech

log = logging.getLogger(__name__)


@dataclass
class Pending:
    """A spoken request the agent is working on."""

    session: VoiceSession
    message_id: int
    reply: str | None = None
    result: dict | None = None


class VoiceHub:
    def __init__(self, speech: Speech, tasks: TaskManager, bus: EventBus):
        self.speech = speech
        self.tasks = tasks
        self.bus = bus
        self.sessions: set[VoiceSession] = set()
        self.owners: dict[int, VoiceSession] = {}
        self.requests: dict[int, VoiceSession] = {}   # queued user message id → session that spoke it
        self.turns: dict[int, Pending] = {}            # task id → the spoken request now running
        self.watcher: asyncio.Task | None = None
        speech.listeners.add(self._speech_changed)

    def start(self):
        self.watcher = asyncio.ensure_future(self._watch())

    def add(self, session: VoiceSession):
        self.sessions.add(session)

    def remove(self, session: VoiceSession):
        self.sessions.discard(session)
        for task_id in [t for t, owner in self.owners.items() if owner is session]:
            del self.owners[task_id]
        for message_id in [m for m, owner in self.requests.items() if owner is session]:
            del self.requests[message_id]
        for task_id in [t for t, pending in self.turns.items() if pending.session is session]:
            del self.turns[task_id]

    def owner(self, task_id: int) -> VoiceSession | None:
        return self.owners.get(task_id)

    async def deliver(self, session: VoiceSession, turn: Turn, text: str) -> dict:
        """Act on a final transcript. Returns the `final` message's outcome fields."""
        if not text.strip():
            return {"outcome": "empty"}
        command = parse(text, self.tasks.db.projects())
        if isinstance(command, Stop):
            await session.interrupt()
            return {"outcome": "stop"}
        if isinstance(command, Cancel):
            if turn.task_id is None:
                await session.error("no_target", "choose the task to cancel first", turn.id)
                return {"outcome": "failed"}
            try:
                await self.tasks.cancel(turn.task_id)
            except TaskError as error:
                await session.error("dispatch_failed", str(error), turn.id)
                return {"outcome": "failed"}
            return {"outcome": "stop", "task_id": turn.task_id}
        if not command.text:
            return {"outcome": "empty"}  # only a project name was said
        try:
            task = await self._dispatch(turn, command.text, command.project_id)
        except TaskError as error:
            await session.error("dispatch_failed", str(error), turn.id)
            return {"outcome": "failed"}
        if task is None:
            await session.error("no_target", "choose a task or a project first, or start with a project's name",
                                turn.id)
            return {"outcome": "failed"}
        self.owners[task["id"]] = session
        message = self.tasks.db.one("SELECT id FROM task_messages WHERE task_id = ? AND role = 'user' "
                                    "ORDER BY id DESC LIMIT 1", task["id"])
        self.requests[message["id"]] = session
        return {"outcome": "dispatched", "task_id": task["id"]}

    async def _dispatch(self, turn: Turn, text: str, named_project: int | None) -> dict | None:
        """A named project wins over the selection, unless the selected task is in it."""
        selected = self.tasks.db.task(turn.task_id) if turn.task_id is not None else None
        if named_project is not None:
            if selected and selected["project_id"] == named_project:
                return await self.tasks.follow_up(selected["id"], text)
            return await self.tasks.create(named_project, text)
        if turn.task_id is not None:
            return await self.tasks.follow_up(turn.task_id, text)
        if turn.project_id is not None:
            return await self.tasks.create(turn.project_id, text)
        return None

    async def close(self):
        self.speech.listeners.discard(self._speech_changed)
        if self.watcher:
            self.watcher.cancel()
            await asyncio.gather(self.watcher, return_exceptions=True)

    # Replies

    async def _watch(self):
        subscriber = self.bus.subscribe()
        try:
            while True:
                event = await subscriber.next()
                if event is None:  # fell behind and was dropped; carry on from here
                    log.warning("voice fell behind the event bus; replies in flight may not be spoken")
                    subscriber = self.bus.subscribe()
                    continue
                try:
                    self._observe(event)
                except Exception:
                    log.exception("could not handle event %s", event.get("type"))
        finally:
            self.bus.unsubscribe(subscriber)

    def _observe(self, event: dict):
        kind, task_id = event["type"], event.get("task_id")
        if kind == "message":
            message = event["message"]
            if message["role"] == "agent":
                if task_id in self.turns:
                    self.turns[task_id].reply = message["text"]
            elif message["state"] == "sent" and message["id"] in self.requests:
                self.turns[task_id] = Pending(self.requests.pop(message["id"]), message["id"])
            elif message["state"] == "cancelled":
                self.requests.pop(message["id"], None)
            elif message["state"] == "done":
                pending = self.turns.get(task_id)
                if pending and pending.message_id == message["id"]:
                    del self.turns[task_id]
                    self._reply(task_id, pending)
        elif kind == "event" and event["event"]["kind"] == "turn_completed" and task_id in self.turns:
            self.turns[task_id].result = event["event"]["data"]
        elif kind == "task" and event["task"]["status"] in ("failed", "unknown", "cancelled"):
            # The turn ended without finishing its message, e.g. the agent failed to start.
            task = event["task"]
            pending = self.turns.pop(task["id"], None)
            if pending:
                pending.result = pending.result or {"status": "failed" if task["status"] != "cancelled"
                                                    else "cancelled", "error": task.get("error")}
                self._reply(task["id"], pending)

    def _reply(self, task_id: int, pending: Pending):
        if pending.session not in self.sessions:
            return
        result = pending.result or {}
        if result.get("status") == "cancelled":
            return  # the user cancelled it; nothing to report
        text = for_speech(pending.reply) if pending.reply else ""
        if result.get("status") != "completed":
            error = for_speech(result["error"]) if result.get("error") else ""
            text = f"{text} That task failed{': ' + error if error else '.'}".strip()
        pending.session.speak(clauses(text), task_id=task_id)

    def _speech_changed(self):
        for session in list(self.sessions):
            asyncio.ensure_future(session.send(type="speech_status", speech=self.speech.snapshot()))
