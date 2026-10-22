"""The daemon's voice sessions, and where their transcripts go.

A task is owned by the session that last dispatched to it: that session, and no
other, speaks its replies and asks its approvals.
"""
from __future__ import annotations

import asyncio

from ..speech import Speech
from ..tasks import TaskError, TaskManager
from .session import Turn, VoiceSession


class VoiceHub:
    def __init__(self, speech: Speech, tasks: TaskManager):
        self.speech = speech
        self.tasks = tasks
        self.sessions: set[VoiceSession] = set()
        self.owners: dict[int, VoiceSession] = {}
        speech.listeners.add(self._speech_changed)

    def add(self, session: VoiceSession):
        self.sessions.add(session)

    def remove(self, session: VoiceSession):
        self.sessions.discard(session)
        for task_id in [t for t, owner in self.owners.items() if owner is session]:
            del self.owners[task_id]

    def owner(self, task_id: int) -> VoiceSession | None:
        return self.owners.get(task_id)

    async def deliver(self, session: VoiceSession, turn: Turn, text: str) -> dict:
        """Send a final transcript to its target. Returns the `final` message's outcome fields."""
        if not text.strip():
            return {"outcome": "empty"}
        try:
            if turn.task_id is not None:
                task = await self.tasks.follow_up(turn.task_id, text)
            elif turn.project_id is not None:
                task = await self.tasks.create(turn.project_id, text)
            else:
                await session.error("no_target", "choose a task or a project first", turn.id)
                return {"outcome": "failed"}
        except TaskError as error:
            await session.error("dispatch_failed", str(error), turn.id)
            return {"outcome": "failed"}
        self.owners[task["id"]] = session
        return {"outcome": "dispatched", "task_id": task["id"]}

    def close(self):
        self.speech.listeners.discard(self._speech_changed)

    def _speech_changed(self):
        for session in list(self.sessions):
            asyncio.ensure_future(session.send(type="speech_status", speech=self.speech.snapshot()))
