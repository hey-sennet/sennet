"""The daemon's voice sessions, and where their transcripts go.

A task is owned by the session that last dispatched to it: that session, and no
other, speaks its replies and asks its approvals.
"""
from __future__ import annotations

import asyncio

from ..speech import Speech
from ..tasks import TaskError, TaskManager
from .dispatch import Cancel, Stop, parse
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

    def close(self):
        self.speech.listeners.discard(self._speech_changed)

    def _speech_changed(self):
        for session in list(self.sessions):
            asyncio.ensure_future(session.send(type="speech_status", speech=self.speech.snapshot()))
