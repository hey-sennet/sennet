"""Task manager: runs agent turns, queues follow-ups, enforces concurrency.

A task is one piece of agent work in one project: an agent conversation plus the
messages sent to it. Messages queue; each becomes one agent turn. At most one task
runs per project (two agents editing one working tree conflict), and at most
`max_concurrent` run overall. Agent processes stay warm for `idle_timeout_s` after
a turn so follow-ups start instantly; after that the conversation is resumed from
its resume token, when the agent can resume.

An agent with `background_turns` may leave work running when its turn ends: the
task is then `background`, its process stays up (the idle timer waits for the work
to end), and follow-ups run as usual. When the work is done the agent may start a
turn itself; the task runs it like any other, and sends messages queued meanwhile
after it.
"""
from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass, field
from pathlib import Path

from .agents.base import Agent, AgentEvent, AgentSession, ApprovalRequest, Decision, TurnPolicy, check_event
from .agents.registry import ModelError, check_model
from .approvals import Approvals
from .config import Settings
from .db import Database

log = logging.getLogger(__name__)

ACTIVE = ("running", "awaiting_approval")
INTERRUPT_GRACE_S = 10


@dataclass
class Runner:
    session: AgentSession | None = None
    job: asyncio.Task | None = None
    idle_timer: asyncio.TimerHandle | None = None
    turn_done: asyncio.Event = field(default_factory=asyncio.Event)
    turn_result: dict = field(default_factory=dict)
    cancelling: bool = False
    background: list = field(default_factory=list)  # the agent's work still running after its turn
    choice: tuple[str | None, str | None] = (None, None)  # the session's model and effort


class TaskError(Exception):
    pass


class TaskManager:
    def __init__(self, db: Database, settings: Settings, agents: dict[str, Agent], approvals: Approvals, publish):
        self.db = db
        self.settings = settings
        self.agents = agents
        self.approvals = approvals
        self.publish = publish
        self.runners: dict[int, Runner] = {}
        self.stopping = False

    async def start(self):
        self.db.recover()
        self._schedule()

    async def shutdown(self):
        self.stopping = True  # a cancelled job's cleanup must not start the next queued task
        for runner in self.runners.values():
            if runner.idle_timer:
                runner.idle_timer.cancel()
            if runner.job:
                runner.job.cancel()
        await asyncio.gather(*(r.job for r in self.runners.values() if r.job), return_exceptions=True)
        await asyncio.gather(*(r.session.close() for r in self.runners.values() if r.session),
                             return_exceptions=True)

    # Public operations

    async def create(self, project_id: int, text: str, model: str | None = None, effort: str | None = None) -> dict:
        """A new task, on its own model and effort if given (else the project's)."""
        project = self.db.project(project_id)
        if project is None:
            raise TaskError("no such project")
        try:
            await check_model(self.agents.get(project["agent"]), model, effort, project["model"])
        except ModelError as error:
            raise TaskError(str(error)) from None
        text = _clean(text)
        task_id = self.db.add_task(project_id, project["agent"], _title(text), model, effort)
        self.db.add_message(task_id, "user", text, "queued")
        await self._publish_task(task_id)
        self._schedule()
        return self.db.task(task_id)

    async def follow_up(self, task_id: int, text: str) -> dict:
        task = self.db.task(task_id)
        if task is None:
            raise TaskError("no such task")
        text = _clean(text)
        message_id = self.db.add_message(task_id, "user", text, "queued")
        await self.publish({"type": "message", "task_id": task_id,
                            "message": self.db.one("SELECT * FROM task_messages WHERE id = ?", message_id)})
        if task["status"] not in ACTIVE:
            self.db.update_task(task_id, status="queued", error=None)
            await self._publish_task(task_id)
            self._schedule()
        return self.db.task(task_id)

    async def cancel(self, task_id: int) -> dict:
        task = self.db.task(task_id)
        if task is None:
            raise TaskError("no such task")
        for message in self.db.query("SELECT id FROM task_messages WHERE task_id = ? AND state = 'queued'", task_id):
            await self._set_message_state(message["id"], "cancelled")
        runner = self.runners.get(task_id)
        if task["status"] in ACTIVE and runner and runner.job:
            runner.cancelling = True
            await self.approvals.cancel_for_task(task_id)
            interruptible = self._agent(task).capabilities.interrupt
            if interruptible and runner.session and runner.session.alive:
                try:
                    await asyncio.wait_for(runner.session.interrupt(), INTERRUPT_GRACE_S)
                except Exception:
                    log.warning("interrupt failed for task %s; stopping the agent", task_id)
            try:
                if not interruptible:
                    raise asyncio.TimeoutError  # nothing to wait for: stop the process
                await asyncio.wait_for(asyncio.shield(runner.turn_done.wait()), INTERRUPT_GRACE_S)
            except asyncio.TimeoutError:
                # The agent ignored the interrupt or can't take one. Stop its process; the outcome is unknown.
                runner.job.cancel()
                await asyncio.gather(runner.job, return_exceptions=True)
                await self._close_session(task_id)
                why = "agent did not stop" if interruptible else f"{task['agent']} can't be interrupted"
                self.db.update_task(task_id, status="unknown", error=f"{why}; its process was stopped")
                await self._publish_task(task_id)
        elif task["status"] in ("queued", "background"):
            self.db.update_task(task_id, status="cancelled")
            await self._publish_task(task_id)
        if runner and runner.background:
            await self._close_session(task_id)  # the work runs in the agent's process, and ends with it
        return self.db.task(task_id)

    async def choose_model(self, task_id: int, model: str | None, effort: str | None) -> dict:
        """Use this model and effort for the task from its next turn (None for both: the project's)."""
        if self.db.task(task_id) is None:
            raise TaskError("no such task")
        self.db.update_task(task_id, model=model, effort=effort)
        await self._publish_task(task_id)
        return self.db.task(task_id)

    def snapshot(self, task_id: int) -> dict | None:
        task = self.db.task(task_id)
        if task is None:
            return None
        return {**task, "messages": self.db.messages(task_id), "events": self.db.events(task_id)}

    # Scheduling

    def _schedule(self):
        if self.stopping:
            return
        running = [t for t in self.db.query("SELECT id, project_id FROM tasks WHERE status IN ('running', 'awaiting_approval')")]
        busy_projects = {t["project_id"] for t in running}
        slots = self.settings.tasks.max_concurrent - len(running)
        for task in self.db.query("SELECT id, project_id FROM tasks WHERE status = 'queued' ORDER BY updated_at, id"):
            if slots <= 0:
                break
            if task["project_id"] in busy_projects:
                continue
            busy_projects.add(task["project_id"])
            slots -= 1
            self.db.update_task(task["id"], status="running")
            runner = self.runners.setdefault(task["id"], Runner())
            if runner.idle_timer:
                runner.idle_timer.cancel()
                runner.idle_timer = None
            runner.cancelling = False
            runner.turn_done.clear()
            runner.job = asyncio.create_task(self._run(task["id"]))

    async def _run(self, task_id: int, agent_turn: bool = False):
        """Run the task's queued messages, one turn each; first the turn the agent started, if `agent_turn`."""
        runner = self.runners[task_id]
        await self._publish_task(task_id)
        try:
            status = await self._end_turn(task_id) if agent_turn else "completed"
            while status == "completed" and not runner.cancelling and \
                    (message := self.db.next_queued_message(task_id)):
                await self._ensure_session(task_id)
                if runner.cancelling:
                    break  # cancelled while the agent was starting
                await self._set_message_state(message["id"], "sent")
                runner.turn_result = {}
                await runner.session.send(message["text"])
                if runner.cancelling:
                    await runner.session.interrupt()  # cancelled while the turn was starting
                status = await self._end_turn(task_id, message["id"])
            if self.db.task(task_id)["status"] == "running":  # cancelled before any turn ran
                self.db.update_task(task_id, status="cancelled")
            elif self.db.task(task_id)["status"] == "completed" and runner.background:
                self.db.update_task(task_id, status="background")
        except asyncio.CancelledError:
            raise
        except Exception as error:
            log.exception("task %s failed", task_id)
            self.db.update_task(task_id, status="failed", error=str(error) or type(error).__name__)
            await self._close_session(task_id)
        finally:
            runner.turn_done.set()
            if self.db.task(task_id)["status"] in ACTIVE:
                self.db.update_task(task_id, status="unknown")
            await self._publish_task(task_id)
            self._arm_idle_close(task_id)
            self._schedule()

    async def _end_turn(self, task_id: int, message_id: int | None = None) -> str:
        """Wait for the turn to end, and record how it did."""
        runner = self.runners[task_id]
        await runner.turn_done.wait()
        runner.turn_done.clear()
        if message_id is not None:
            await self._set_message_state(message_id, "done")
        status = "cancelled" if runner.cancelling else runner.turn_result.get("status", "failed")
        self.db.update_task(task_id, status=status, error=runner.turn_result.get("error"))
        return status

    async def _ensure_session(self, task_id: int):
        runner = self.runners[task_id]
        task = self.db.task(task_id)
        project = self.db.project(task["project_id"])
        agent = self._agent(task)
        choice = _choice(task, project)
        if runner.session and runner.session.alive:
            if runner.choice == choice:
                return
            if agent.capabilities.live_model_change:
                await runner.session.set_model(*choice)
                runner.choice = choice
                return
            if runner.background:
                return  # restarting would end the work; the change waits for it
        await self._close_session(task_id)  # what is left of one that ended, or used another model
        path = Path(project["path"])
        if not path.is_dir():
            raise TaskError(f"project directory {path} does not exist")
        policy = TurnPolicy(mode=project["approval_mode"] or self.settings.approvals.mode,
                            access=project["access"] or self.settings.approvals.access,
                            project_config=project["project_config"], model=choice[0], effort=choice[1])
        capabilities = agent.capabilities
        if policy.mode not in capabilities.approval_modes:
            raise TaskError(f"{agent.name} doesn't support the {policy.mode!r} approval mode")
        if policy.access not in capabilities.access_levels:
            raise TaskError(f"{agent.name} doesn't support {policy.access!r} access")
        # Without resume, a new process starts a new conversation; the old one can't be continued.
        token = task["resume_token"] if capabilities.resume else None
        runner.session = await agent.open(path, token, policy,
                                          lambda event: self._on_event(task_id, event),
                                          lambda request: self._on_approval(task_id, request))
        runner.choice = choice
        if runner.session.resume_token != task["resume_token"]:
            self.db.update_task(task_id, resume_token=runner.session.resume_token)

    def _agent(self, task: dict) -> Agent:
        agent = self.agents.get(task["agent"])
        if agent is None:
            raise TaskError(f"agent {task['agent']!r} is not available")
        return agent

    def _arm_idle_close(self, task_id: int):
        """Close the agent's process once it has been idle a while; never while its work runs."""
        runner = self.runners.get(task_id)
        if runner and runner.idle_timer:
            runner.idle_timer.cancel()
            runner.idle_timer = None
        if runner and runner.session and not runner.background and self.db.task(task_id)["status"] not in ACTIVE:
            loop = asyncio.get_running_loop()
            runner.idle_timer = loop.call_later(self.settings.tasks.idle_timeout_s,
                                                lambda: asyncio.ensure_future(self._close_session(task_id)))

    async def _close_session(self, task_id: int):
        runner = self.runners.get(task_id)
        if runner and runner.session:
            session, runner.session = runner.session, None
            runner.background = []
            await session.close()

    # Agent callbacks

    async def _on_event(self, task_id: int, event: AgentEvent):
        runner = self.runners[task_id]
        if problems := check_event(event):
            log.warning("agent event breaks the contract: %s", "; ".join(problems))
        if event.kind == "message_delta":
            # Streamed text is shown live but only the completed message is stored.
            await self.publish({"type": "delta", "task_id": task_id, **event.data})
            return
        if event.kind == "message":
            message_id = self.db.add_message(task_id, "agent", event.data["text"], "done")
            await self.publish({"type": "message", "task_id": task_id,
                                "message": self.db.one("SELECT * FROM task_messages WHERE id = ?", message_id)})
            return
        event_id = self.db.add_event(task_id, event.kind, event.data)
        await self.publish({"type": "event", "task_id": task_id,
                            "event": {"id": event_id, "kind": event.kind, "data": event.data, "created_at": time.time()}})
        if event.kind == "turn_completed":
            runner.turn_result = event.data
            runner.turn_done.set()
        elif event.kind == "background":
            await self._background_changed(task_id, event.data["tasks"])
        elif event.kind == "turn_started" and event.data["by"] == "agent":
            await self._agent_turn(task_id)

    async def _background_changed(self, task_id: int, work: list):
        runner = self.runners[task_id]
        runner.background = list(work)
        status = self.db.task(task_id)["status"]
        if not work and status == "background":
            self.db.update_task(task_id, status="completed")
            await self._publish_task(task_id)
        if status not in ACTIVE and status != "queued":
            self._arm_idle_close(task_id)

    async def _agent_turn(self, task_id: int):
        """The agent started a turn itself: run it as the task's turn, without taking a queued message."""
        runner = self.runners[task_id]
        if runner.job and not runner.job.done():
            log.warning("task %s: the agent started a turn during another", task_id)
            return
        if runner.idle_timer:
            runner.idle_timer.cancel()
            runner.idle_timer = None
        self.db.update_task(task_id, status="running", error=None)
        runner.cancelling = False
        runner.turn_result = {}
        runner.turn_done.clear()
        runner.job = asyncio.create_task(self._run(task_id, agent_turn=True))

    async def _on_approval(self, task_id: int, request: ApprovalRequest) -> Decision:
        if self.runners[task_id].cancelling:
            return "cancel"
        self.db.update_task(task_id, status="awaiting_approval")
        await self._publish_task(task_id)
        try:
            return await self.approvals.request(self.db.task(task_id), request)
        finally:
            if self.db.task(task_id)["status"] == "awaiting_approval" and not self.approvals_pending(task_id):
                self.db.update_task(task_id, status="running")
                await self._publish_task(task_id)

    def approvals_pending(self, task_id: int) -> bool:
        return any(self.db.approval(p.approval_id)["task_id"] == task_id for p in self.approvals.pending.values())

    async def _set_message_state(self, message_id: int, state: str):
        self.db.set_message_state(message_id, state)
        message = self.db.one("SELECT * FROM task_messages WHERE id = ?", message_id)
        await self.publish({"type": "message", "task_id": message["task_id"], "message": message})

    async def _publish_task(self, task_id: int):
        await self.publish({"type": "task", "task": self.db.task(task_id)})


def _choice(task: dict, project: dict) -> tuple[str | None, str | None]:
    """The task's model and effort, else the project's when it is for the same agent. A task that
    picks its own model takes that model's default effort, not the project's."""
    own = project if project["agent"] == task["agent"] else {"model": None, "effort": None}
    model = task["model"] or own["model"]
    effort = task["effort"] or (own["effort"] if task["model"] is None else None)
    return model, effort


def _clean(text: str) -> str:
    text = text.strip()
    if not text:
        raise TaskError("message is empty")
    if len(text) > 32000:
        raise TaskError("message is too long")
    return text


def _title(text: str) -> str:
    line = text.splitlines()[0]
    return line if len(line) <= 80 else line[:77].rstrip() + "…"
