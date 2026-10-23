"""The daemon's voice sessions, where their transcripts go, and their replies.

A task is owned by the session that last dispatched to it: that session, and no
other, asks its approvals. A reply is spoken on the session whose request it
answers: the hub follows each spoken request's message from `sent` to `done`
on the event bus and speaks each agent message in between as it arrives, so a
preamble ("I'll move it to Trash") is heard before the approval it leads to.

A long message starts with the agent's own version to be heard, in <spoken> tags
(agents.base.SPOKEN_INSTRUCTIONS). The hub follows the message as it streams and
speaks that version a sentence at a time as each is written, as one reply that
stays open until the closing tag, so the voice starts before the rest is written.
A long message without one is cut to its opening sentences (`summary.shorten`).
Speech still being prepared for a task holds back its later messages, approval
questions and failure, so they are heard in order. "Read me the details" reads
the last reply in full, without its spoken version.

A turn the agent starts itself (it finished work it left running) has no request:
its reply is spoken on the task's owner, if connected, after the approval chime,
since it can come minutes after anything was said.
"""
from __future__ import annotations

import asyncio
import logging
import re
from dataclasses import dataclass, field

from ..events import EventBus
from ..speech import Speech
from ..tasks import TaskError, TaskManager
from . import asking
from . import summary
from .dispatch import Cancel, Details, Stop, parse
from .session import Asking, Reply, Turn, VoiceSession
from .speakable import clauses, for_speech, spoken_part, without_spoken

OPENING_TAG = "<spoken>"
# The end of a sentence, once the next one has begun (as speakable.clauses splits them).
SENTENCE_END = re.compile(r"[.!?][\"')\]]*\s+(?=[A-Z0-9\"'(])")

log = logging.getLogger(__name__)


@dataclass
class Pending:
    """A spoken request the agent is working on."""

    session: VoiceSession
    message_id: int | None          # None for a turn the agent started
    result: dict | None = None
    cue: bool = False               # chime before the first thing said
    hushed: bool = False            # talked over: the rest of this turn isn't spoken


@dataclass
class Live:
    """A spoken version being said while it streams, a sentence at a time."""

    said: int = 0                   # characters of it handed to speech
    waiting: list[str] = field(default_factory=list)  # segments written before its reply was queued
    reply: Reply | None = None
    closed: bool = False

    def add(self, segments: list[str]):
        if self.reply is not None:
            self.reply.append(segments)
        else:
            self.waiting += segments

    def close(self):
        self.closed = True
        if self.reply is not None:
            self.reply.close()


class VoiceHub:
    def __init__(self, speech: Speech, tasks: TaskManager, bus: EventBus):
        self.speech = speech
        self.tasks = tasks
        self.bus = bus
        self.sessions: set[VoiceSession] = set()
        self.owners: dict[int, VoiceSession] = {}
        self.requests: dict[int, VoiceSession] = {}   # queued user message id → session that spoke it
        self.turns: dict[int, Pending] = {}            # task id → the spoken request now running
        self.voicing: dict[int, asyncio.Task] = {}     # task id → its speech still being prepared
        self.streams: dict[int, dict[str, str | None]] = {}  # task id → message item → text so far, None once read
        self.early: dict[int, list[str]] = {}          # task id → spoken versions said before their message ended
        self.live: dict[int, Live] = {}                # task id → its spoken version being said as it streams
        self.heard: dict[VoiceSession, int] = {}       # session → the task it last heard a reply from
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
        self.heard.pop(session, None)

    def status(self) -> dict:
        """What `ready` and `speech_status` report."""
        return self.speech.snapshot()

    def owner(self, task_id: int) -> VoiceSession | None:
        return self.owners.get(task_id)

    def hush(self, session: VoiceSession, task_id: int | None):
        """The user talked over this task's reply: say nothing more from its current turn. Its
        messages are still in the timeline, and "read me the details" reads the last one."""
        pending = self.turns.get(task_id)
        if pending is not None and pending.session is session:
            pending.hushed = True

    async def deliver(self, session: VoiceSession, turn: Turn, text: str) -> dict:
        """Act on a final transcript. Returns the `final` message's outcome fields."""
        if not text.strip():
            return {"outcome": "empty"}
        if session.asking and session.asking.started:
            return await self._answer(session, session.asking, text)
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
        if isinstance(command, Details):
            return await self._details(session, turn)
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

    async def _details(self, session: VoiceSession, turn: Turn) -> dict:
        """The selected task's last reply, or else the last one heard here, read in full."""
        task_id = turn.task_id if turn.task_id is not None else self.heard.get(session)
        message = None
        if task_id is not None:
            message = self.tasks.db.one("SELECT text FROM task_messages WHERE task_id = ? AND role = 'agent' "
                                        "ORDER BY id DESC LIMIT 1", task_id)
        if message is None:
            await session.error("no_target", "there's no reply to read yet; choose a task first", turn.id)
            return {"outcome": "failed"}
        session.speak(clauses(for_speech(without_spoken(message["text"]))), task_id=task_id)
        return {"outcome": "details", "task_id": task_id}

    async def _answer(self, session: VoiceSession, current: Asking, text: str) -> dict:
        """The turn after an approval question answers it (voice-protocol §8)."""
        approval = current.approval
        reprompt = {"outcome": "reprompt", "approval_id": approval["id"]}
        if isinstance(parse(text, []), Stop):
            await session.interrupt()  # stops the question; it still waits for an answer
            return {"outcome": "stop"}
        word = asking.answer(text)
        if current.confirming:
            decision = {"confirm": "approve", "decline": "decline", "cancel": "cancel"}.get(word)
            if decision is None:
                session.ask(asking.ask_confirm())
                return {**reprompt, "confirm": True}
        elif word in ("approve", "confirm"):
            if approval["risk"] == "destructive":
                current.confirming = True
                session.ask(asking.ask_confirm())
                return {**reprompt, "confirm": True}
            decision = "approve"
        elif word == "approve_always":
            if not approval.get("can_always"):
                session.ask(asking.no_always())
                return reprompt
            decision = "approve_always"
        elif word in ("decline", "cancel"):
            decision = word
        else:
            session.ask(asking.reprompt(approval))
            return reprompt
        try:
            await self.tasks.approvals.decide(approval["id"], decision, "voice")
        except LookupError:
            # Answered on the card while this turn was being transcribed.
            session.withdraw(approval["id"])
            return {"outcome": "approval", "approval_id": approval["id"], "decision": None}
        # The acknowledgement goes first; withdrawing queues the next question, if any.
        session.speak(asking.acknowledge(decision, current.project), task_id=approval["task_id"])
        session.withdraw(approval["id"])
        return {"outcome": "approval", "approval_id": approval["id"], "decision": decision}

    async def _dispatch(self, turn: Turn, text: str, named_project: int | None) -> dict | None:
        """A named project wins over the selection, unless the selected task is in it. The turn's
        model is for a task started in its own project."""
        selected = self.tasks.db.task(turn.task_id) if turn.task_id is not None else None
        if named_project is not None:
            if selected and selected["project_id"] == named_project:
                return await self.tasks.follow_up(selected["id"], text)
            if named_project != turn.project_id or turn.task_id is not None:
                return await self.tasks.create(named_project, text)
        elif turn.task_id is not None:
            return await self.tasks.follow_up(turn.task_id, text)
        if turn.project_id is not None:
            return await self.tasks.create(turn.project_id, text, turn.model, turn.effort)
        return None

    async def close(self):
        self.speech.listeners.discard(self._speech_changed)
        jobs = [*self.voicing.values(), *([self.watcher] if self.watcher else [])]
        for job in jobs:
            job.cancel()
        await asyncio.gather(*jobs, return_exceptions=True)

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
                    self._voice(task_id, self.turns[task_id], message["text"])
            elif message["state"] == "sent" and message["id"] in self.requests:
                self.turns[task_id] = Pending(self.requests.pop(message["id"]), message["id"])
            elif message["state"] == "cancelled":
                self.requests.pop(message["id"], None)
            elif message["state"] == "done":
                pending = self.turns.get(task_id)
                if pending and pending.message_id == message["id"]:
                    del self.turns[task_id]
                    self._forget(task_id)
                    self._reply(task_id, pending)
        elif kind == "delta":
            if task_id in self.turns:
                self._stream(task_id, self.turns[task_id], event["item_id"], event["delta"])
        elif kind == "approval":
            self._approval(event["approval"])
        elif kind == "event" and event["event"]["kind"] == "turn_started":
            owner = self.owners.get(task_id)
            if event["event"]["data"].get("by") == "agent" and owner in self.sessions and task_id not in self.turns:
                self.turns[task_id] = Pending(owner, None, cue=True)
        elif kind == "event" and event["event"]["kind"] == "turn_completed" and task_id in self.turns:
            pending = self.turns[task_id]
            pending.result = event["event"]["data"]
            if pending.message_id is None:  # an agent's turn has no message to follow to its end
                del self.turns[task_id]
                self._forget(task_id)
                self._reply(task_id, pending)
        elif kind == "task" and event["task"]["status"] in ("failed", "unknown", "cancelled"):
            # The turn ended without finishing its message, e.g. the agent failed to start.
            task = event["task"]
            pending = self.turns.pop(task["id"], None)
            self._forget(task["id"])
            if pending:
                pending.result = pending.result or {"status": "failed" if task["status"] != "cancelled"
                                                    else "cancelled", "error": task.get("error")}
                self._reply(task["id"], pending)

    def _approval(self, approval: dict):
        if approval["status"] != "pending":
            for session in self.sessions:
                session.withdraw(approval["id"])
            return
        if approval["task_id"] in self.voicing:
            self._after(approval["task_id"], lambda: self._offer(approval["id"]))
            return
        self._offer(approval["id"])

    def _offer(self, approval_id: int):
        approval = self.tasks.approvals.present(self.tasks.db.approval(approval_id))
        if approval["status"] != "pending":
            return  # answered on the card while speech was being prepared
        owner = self.owners.get(approval["task_id"])
        if owner in self.sessions:
            task = self.tasks.db.task(approval["task_id"])
            project = self.tasks.db.project(task["project_id"]) if task else None
            owner.offer(approval, project["name"] if project else "The agent")

    def _reply(self, task_id: int, pending: Pending):
        """The turn ended. Its messages have been spoken; only a failure is left to say."""
        result = pending.result or {}
        if result.get("status") in ("completed", "cancelled"):
            return  # a cancelled task was the user's doing; nothing to report
        error = for_speech(result["error"]) if result.get("error") else ""
        text = f"That task failed{': ' + error if error else '.'}"
        if task_id in self.voicing:
            self._after(task_id, lambda: self._say(task_id, pending, text, failure=True))
        else:
            self._say(task_id, pending, text, failure=True)

    def _stream(self, task_id: int, pending: Pending, item_id: str, delta: str):
        """Speak the message's spoken version as soon as it is complete, if the message starts with one."""
        items = self.streams.setdefault(task_id, {})
        if item_id in items and items[item_id] is None:
            return
        text = (items.get(item_id) or "") + delta
        start = text.lstrip()[:len(OPENING_TAG)]
        if not OPENING_TAG.startswith(start):
            items[item_id] = None  # no spoken version; the whole message is voiced when it ends
        elif (spoken := spoken_part(text)) is not None:
            items[item_id] = None
            self.early.setdefault(task_id, []).append(spoken)
            self._finish_live(task_id, pending, spoken)
        else:
            items[item_id] = text
            if len(start) == len(OPENING_TAG):
                body = text.lstrip()[len(OPENING_TAG):].lstrip()
                if (ends := list(SENTENCE_END.finditer(body))):
                    self._say_live(task_id, pending, body, ends[-1].end())

    def _say_live(self, task_id: int, pending: Pending, body: str, end: int):
        """Speak the spoken version's sentences up to `end`, those not said yet."""
        live = self.live.get(task_id)
        if live is None:
            live = self.live[task_id] = Live()
            self._in_turn(task_id, lambda: self._queue_live(task_id, pending, live))
        if end > live.said:
            live.add(clauses(for_speech(body[live.said:end])))
            live.said = end

    def _finish_live(self, task_id: int, pending: Pending, spoken: str):
        """The spoken version is complete: say what is left of it, and end its reply."""
        live = self.live.pop(task_id, None)
        if live is None:
            self._in_turn(task_id, lambda: self._say(task_id, pending, for_speech(spoken)))
            return
        if rest := spoken[live.said:].strip():
            live.add(clauses(for_speech(rest)))
        live.close()

    def _queue_live(self, task_id: int, pending: Pending, live: Live):
        if pending.hushed or pending.session not in self.sessions:
            return
        self.heard[pending.session] = task_id
        live.reply = pending.session.speak(live.waiting, task_id=task_id, cue=pending.cue, streaming=not live.closed)
        live.waiting = []
        pending.cue = False

    def _forget(self, task_id: int):
        self.streams.pop(task_id, None)
        self.early.pop(task_id, None)
        if (live := self.live.pop(task_id, None)) is not None:
            live.close()  # the message ended without closing its spoken version

    def _voice(self, task_id: int, pending: Pending, markdown: str):
        """Speak an agent message: its spoken version, or itself cut to its opening, in turn."""
        if (spoken := spoken_part(markdown)) is not None:
            if spoken in self.early.get(task_id, []):
                self.early[task_id].remove(spoken)  # already heard while it streamed
            else:
                self._finish_live(task_id, pending, spoken)  # the rest of it, if it was streaming
            return
        self._in_turn(task_id, lambda: self._say(task_id, pending, summary.shorten(markdown)))

    def _in_turn(self, task_id: int, step):
        """Run `step` now, or after the task's speech being prepared."""
        if task_id in self.voicing:
            self._after(task_id, step)
        else:
            step()

    def _after(self, task_id: int, step):
        """Run `step` (a function, or one returning a coroutine) after the task's speech being prepared."""
        previous = self.voicing.get(task_id)

        async def run():
            if previous:
                await asyncio.gather(previous, return_exceptions=True)
            if asyncio.iscoroutine(result := step()):
                await result

        job = asyncio.ensure_future(run())
        self.voicing[task_id] = job

        def done(finished: asyncio.Task):
            if self.voicing.get(task_id) is finished:
                del self.voicing[task_id]
            if not finished.cancelled() and finished.exception():
                log.error("could not speak a reply for task %s", task_id, exc_info=finished.exception())
        job.add_done_callback(done)

    def _say(self, task_id: int, pending: Pending, text: str, failure: bool = False):
        if pending.hushed and not failure:
            return
        if pending.session in self.sessions:
            self.heard[pending.session] = task_id
            pending.session.speak(clauses(text), task_id=task_id, cue=pending.cue)
            pending.cue = False

    def _speech_changed(self):
        for session in list(self.sessions):
            asyncio.ensure_future(session.send(type="speech_status", speech=self.status()))
