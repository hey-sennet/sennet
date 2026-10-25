"""The daemon's voice sessions, where their transcripts go, and their replies.

A task is owned by the session that last dispatched to it. A reply is spoken on
the session whose request it answers: the hub follows each spoken request's
message from `sent` to `done` on the event bus and speaks each agent message in
between as it arrives, so a preamble ("I'll move it to Trash") is heard as soon as
it is written. Approvals are answered on their card, never by voice.

A device's next session takes over what its last one was waiting for, so a page
that reloads, or a phone that closed it and came back, hears the replies still to
come. What was said while it was away is in the timeline.

A long message starts with the agent's own version to be heard, in <spoken> tags
(agents.base.SPOKEN_INSTRUCTIONS). The hub follows the message as it streams and
speaks that version a sentence at a time as each is written, as one reply that
stays open until the closing tag, so the voice starts before the rest is written.
A long message without one is cut to its opening sentences (`summary.shorten`).
Speech still being prepared for a task holds back its later messages and
failure, so they are heard in order.

A turn the agent starts itself (it finished work it left running) has no request:
its reply is spoken on the task's owner, if connected, after a chime,
since it can come minutes after anything was said. An agent that doesn't start
one (no `background_turns`) gets a line in Sennet's words instead, once all of
that work has finished.

A turn that stops at a usage limit says which limit and when it resets, in Sennet's
words (tasks.py writes them), not the agent's. After a reply, a limit past 80 or 95
percent is mentioned once at each level, until it resets.
"""
from __future__ import annotations

import asyncio
import logging
import re
from dataclasses import dataclass, field

from ..events import EventBus
from ..speech import Speech
from ..tasks import TaskError, TaskManager
from ..usage import Warnings, limit_warning
from . import summary
from .session import Reply, Turn, VoiceSession
from .speakable import clauses, for_speech, spoken_part

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
class Leftover:
    """Work a task's agent left running, for an agent that won't report it itself."""

    running: set[str]               # item ids not yet finished
    finished: int = 0
    failed: int = 0


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
        self.leftover: dict[int, Leftover] = {}        # task id → its work left running, to say when it's done
        self.warnings = Warnings()
        self.watcher: asyncio.Task | None = None
        speech.listeners.add(self._speech_changed)

    def start(self):
        self.watcher = asyncio.ensure_future(self._watch())

    def add(self, session: VoiceSession):
        self.sessions.add(session)
        log.info("voice %s: connected (device %s)", session.id[:4], session.device)
        if session.device is not None:
            # The device's newest session speaks its replies. An older one is a page it has left, even
            # if its socket still looks open: a phone keeps a backgrounded page's socket up.
            self._hand_over(lambda s: s is not session and s.device == session.device, session)

    def remove(self, session: VoiceSession):
        self.sessions.discard(session)
        log.info("voice %s: disconnected", session.id[:4])
        if session.device is not None:
            # Another page on the device takes over now; if none, the device's next session does in `add`.
            successors = [s for s in self.sessions if s.device == session.device]
            if successors:
                self._hand_over(lambda s: s is session, successors[-1])
            return
        for task_id in [t for t, owner in self.owners.items() if owner is session]:
            del self.owners[task_id]
        for message_id in [m for m, owner in self.requests.items() if owner is session]:
            del self.requests[message_id]
        for task_id in [t for t, pending in self.turns.items() if pending.session is session]:
            del self.turns[task_id]

    def _hand_over(self, gone, session: VoiceSession):
        """Give `session` the tasks, requests and running turns of the sessions `gone` picks."""
        moved = set()
        for task_id, owner in self.owners.items():
            if gone(owner):
                self.owners[task_id] = session
                moved.add(task_id)
        for message_id, owner in self.requests.items():
            if gone(owner):
                self.requests[message_id] = session
        for task_id, pending in self.turns.items():
            if gone(pending.session):
                pending.session = session
                moved.add(task_id)
                if (live := self.live.pop(task_id, None)) is not None:
                    live.close()  # its reply was the old session's: the spoken version starts again here
        if moved:
            log.info("voice %s: took over task%s %s from the device's earlier session", session.id[:4],
                     "" if len(moved) == 1 else "s", ", ".join(map(str, sorted(moved))))

    def status(self) -> dict:
        """What `ready` and `speech_status` report."""
        return self.speech.snapshot()

    def owner(self, task_id: int) -> VoiceSession | None:
        return self.owners.get(task_id)

    def hush(self, session: VoiceSession, task_id: int | None):
        """The user talked over this task's reply: say nothing more from its current turn. Its
        messages are still in the timeline."""
        pending = self.turns.get(task_id)
        if pending is not None and pending.session is session:
            pending.hushed = True

    async def deliver(self, session: VoiceSession, turn: Turn, text: str) -> dict:
        """Act on a final transcript. Returns the `final` message's outcome fields."""
        if not text.strip():
            return {"outcome": "empty"}
        try:
            task = await self._dispatch(turn, text.strip())
        except TaskError as error:
            await session.error("dispatch_failed", str(error), turn.id)
            return {"outcome": "failed"}
        if task is None:
            await session.error("no_target", "choose a task or a project first", turn.id)
            return {"outcome": "failed"}
        self.owners[task["id"]] = session
        session.selected = task["id"]  # the task just spoken to is the one on screen (App selects it)
        message = self.tasks.db.one("SELECT id FROM task_messages WHERE task_id = ? AND role = 'user' "
                                    "ORDER BY id DESC LIMIT 1", task["id"])
        self.requests[message["id"]] = session
        return {"outcome": "dispatched", "task_id": task["id"]}

    async def _dispatch(self, turn: Turn, text: str) -> dict | None:
        """A follow-up to the selected task, or a new task in the selected project with the turn's model."""
        if turn.task_id is not None:
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
        elif kind == "event" and event["event"]["kind"] == "turn_started":
            owner = self.owners.get(task_id)
            if event["event"]["data"].get("by") == "agent" and owner in self.sessions and task_id not in self.turns:
                self.turns[task_id] = Pending(owner, None, cue=True)
        elif kind == "event" and event["event"]["kind"] in ("background", "command_finished"):
            self._leftover(task_id, event["event"]["kind"], event["event"]["data"])
        elif kind == "event" and event["event"]["kind"] == "turn_completed" and task_id in self.turns:
            pending = self.turns[task_id]
            pending.result = event["event"]["data"]
            if pending.message_id is None:  # an agent's turn has no message to follow to its end
                del self.turns[task_id]
                self._forget(task_id)
                self._reply(task_id, pending)
        elif kind == "task_removed":
            task_id = event["task_id"]
            self.owners.pop(task_id, None)
            self.leftover.pop(task_id, None)
            self.turns.pop(task_id, None)
            self._forget(task_id)
        elif kind == "task" and event["task"]["status"] in ("failed", "unknown", "cancelled"):
            # The turn ended without finishing its message, e.g. the agent failed to start.
            task = event["task"]
            pending = self.turns.pop(task["id"], None)
            self._forget(task["id"])
            if pending:
                pending.result = pending.result or {"status": "failed" if task["status"] != "cancelled"
                                                    else "cancelled", "error": task.get("error")}
                self._reply(task["id"], pending)

    def _leftover(self, task_id: int, kind: str, data: dict):
        """Follow work left running by an agent that won't report it, and say when all of it is done.
        Work that ends without finishing (the task was stopped) isn't mentioned."""
        if kind == "command_finished":
            if (work := self.leftover.get(task_id)) is not None and data["item_id"] in work.running:
                work.running.discard(data["item_id"])
                work.finished += 1
                work.failed += data["status"] == "failed" or (data.get("exit_code") or 0) != 0
            return
        ids = {t["id"] for t in data["tasks"]}
        if ids:
            if not self._reports_itself(task_id):
                work = self.leftover.setdefault(task_id, Leftover(set()))
                work.running |= ids
            return
        work = self.leftover.pop(task_id, None)
        owner = self.owners.get(task_id)
        if work is None or work.running or not work.finished or owner not in self.sessions:
            return
        task = self.tasks.db.task(task_id)
        agent = self.tasks.agents.get(task["agent"]) if task else None
        title = agent.title if agent else "The agent"
        what = "The command" if work.finished == 1 else "The commands"
        if work.failed == 0:
            text = f"{what} {title} left running {'has' if work.finished == 1 else 'have'} finished."
        elif work.finished == 1:
            text = f"The command {title} left running failed."
        else:
            text = f"The commands {title} left running have finished, and {work.failed} failed."
        self._say(task_id, Pending(owner, None, cue=True), text)

    def _reports_itself(self, task_id: int) -> bool:
        task = self.tasks.db.task(task_id)
        agent = self.tasks.agents.get(task["agent"]) if task else None
        return bool(agent and agent.capabilities.background_turns)

    def _reply(self, task_id: int, pending: Pending):
        """The turn ended. Its messages have been spoken; only a failure, or a limit getting close, is left to say."""
        result = pending.result or {}
        if result.get("status") == "cancelled":
            return  # the user's doing; nothing to report
        if result.get("status") == "completed":
            self._warn(task_id, pending)
            return
        error = for_speech(result["error"]) if result.get("error") else ""
        text = error if result.get("limit") and error else f"That task failed{': ' + error if error else '.'}"
        if task_id in self.voicing:
            self._after(task_id, lambda: self._say(task_id, pending, text, failure=True))
        else:
            self._say(task_id, pending, text, failure=True)

    def _warn(self, task_id: int, pending: Pending):
        task = self.tasks.db.task(task_id)
        if task is None:
            return
        agent = self.tasks.agents.get(task["agent"])
        title = agent.title if agent else task["agent"]
        due = self.warnings.due(task["agent"], self.tasks.limits.windows(task["agent"]))
        if due:
            # The nearest to full; one is enough to say.
            text = limit_warning(title, max(due, key=lambda w: w["used"]))
            self._in_turn(task_id, lambda: self._say(task_id, pending, text))

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
        if live is not None and end <= live.said:
            return
        segments = clauses(for_speech(body[live.said if live else 0:end]))
        if live is None:
            if not segments:
                return  # nothing to say yet: a reply queued empty would be dropped
            live = self.live[task_id] = Live()
            live.add(segments)  # before queueing, which may run at once and needs something to say
            live.said = end
            self._in_turn(task_id, lambda: self._queue_live(task_id, pending, live))
            return
        live.add(segments)
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
            pending.session.speak(clauses(text), task_id=task_id, cue=pending.cue)
            pending.cue = False

    def _speech_changed(self):
        for session in list(self.sessions):
            asyncio.ensure_future(session.send(type="speech_status", speech=self.status()))
