"""Task manager: runs agent turns, queues follow-ups, enforces concurrency.

A task is one piece of agent work in one project: an agent conversation plus the
messages sent to it. Messages queue; each becomes one agent turn. Tasks in one project
run side by side, as people run agents; keeping them out of each other's way (a git
worktree each, say) is up to the user. At most `max_concurrent` run overall. Agent
processes stay warm for `idle_timeout_s` after a turn so follow-ups start instantly;
after that the conversation is resumed from its resume token, when the agent can resume.

A process can also start before its message arrives (`prewarm`): when someone starts talking
or typing to a task or project, since saying and transcribing a request takes longer than
an agent takes to start. A follow-up warms the task's own session; a new task warms a spare
session for its project, which the task takes over if it matches. Unused, either closes
after `PREWARM_IDLE_S`. And when an interrupt ends an agent's process (Claude's ends on
SIGINT), the session is resumed at once, since a correction usually follows.

An agent may leave work running when its turn ends: the task is then `background`,
its process stays up (the idle timer waits for the work to end), and follow-ups run
as usual. When the work is done an agent with `background_turns` starts a turn
itself; the task runs it like any other, and sends messages queued meanwhile after it.

A task is titled from its first message, then renamed with the title its agent writes at
the start of its first reply, which never reaches the timeline or speech (titles.py).

Tokens a turn used are added to its task. The usage limits agents report belong to
their account, and go to `limits` (usage.py) rather than to the task.
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
from . import titles
from .db import FINISHED, Database
from .usage import Limits, limit_reached

log = logging.getLogger(__name__)

ACTIVE = ("running", "awaiting_approval")
INTERRUPT_GRACE_S = 10
PREWARM_IDLE_S = 120    # a process started for a message that never came


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
    opening: asyncio.Lock = field(default_factory=asyncio.Lock)  # one session start at a time
    streams: dict = field(default_factory=dict)  # message item → its text as it streams, title held back
    titled: bool = False                          # the agent's title is in


@dataclass
class Spare:
    """A session started for a new task in a project, before the task exists."""
    key: tuple                              # agent, policy and model it was started with
    ready: asyncio.Task                     # the start; its result is the session
    task_id: int | None = None              # the task that took it over
    timer: asyncio.TimerHandle | None = None


class TaskError(Exception):
    pass


class TaskBusy(TaskError):
    """The task is still working, or its agent's work is: it has to stop first."""


class TaskManager:
    def __init__(self, db: Database, settings: Settings, agents: dict[str, Agent], approvals: Approvals, publish):
        self.db = db
        self.settings = settings
        self.agents = agents
        self.approvals = approvals
        self.publish = publish
        self.runners: dict[int, Runner] = {}
        self.spares: dict[int, Spare] = {}      # by project id
        self.warming: set[asyncio.Task] = set()
        self.closing: set[asyncio.Task] = set()     # idle closes under way, which shutdown waits for
        self.stopping = False
        self.limits = Limits(agents, publish)

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
        for job in list(self.warming):
            job.cancel()
        await asyncio.gather(*self.warming, *self.closing, return_exceptions=True)
        await asyncio.gather(*(r.session.close() for r in self.runners.values() if r.session),
                             *(self._close_spare(p) for p in list(self.spares)), return_exceptions=True)
        await self.limits.close()

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
        task_id = self.db.add_task(project_id, project["agent"], titles.from_request(text), model, effort)
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

    async def delete(self, task_id: int):
        """Delete a finished task from Sennet. The agent keeps its own copy of the conversation."""
        task = self.db.task(task_id)
        if task is None:
            raise TaskError("no such task")
        runner = self.runners.get(task_id)
        if task["status"] not in FINISHED or (runner and (runner.background or (runner.job and not runner.job.done()))):
            raise TaskBusy("the task is still working; stop it before deleting it")
        if runner:
            if runner.idle_timer:
                runner.idle_timer.cancel()
            await self._close_session(task_id)  # kept warm for a follow-up that won't come now
            self.runners.pop(task_id, None)
        self.db.delete_task(task_id)
        await self.publish({"type": "task_removed", "task_id": task_id})

    async def choose_model(self, task_id: int, model: str | None, effort: str | None) -> dict:
        """Use this model and effort for the task from its next turn (None for both: the project's)."""
        if self.db.task(task_id) is None:
            raise TaskError("no such task")
        self.db.update_task(task_id, model=model, effort=effort)
        await self._publish_task(task_id)
        return self.db.task(task_id)

    def prewarm(self, task_id: int | None = None, project_id: int | None = None, model: str | None = None,
                effort: str | None = None):
        """A message for this task, or a new task in this project, is likely on its way: start the
        agent now so it is ready when the message arrives. Best effort, and never waited on."""
        if self.stopping or (task_id is None and project_id is None):
            return
        job = asyncio.ensure_future(self._prewarm_task(task_id) if task_id is not None
                                    else self._prewarm_spare(project_id, model, effort))
        self.warming.add(job)
        job.add_done_callback(self.warming.discard)

    def snapshot(self, task_id: int) -> dict | None:
        task = self.db.task(task_id)
        if task is None:
            return None
        return {**task, "messages": self.db.messages(task_id), "events": self.db.events(task_id)}

    # Scheduling

    def _schedule(self):
        if self.stopping:
            return
        running = self.db.query("SELECT id FROM tasks WHERE status IN ('running', 'awaiting_approval')")
        slots = self.settings.tasks.max_concurrent - len(running)
        for task in self.db.query("SELECT id FROM tasks WHERE status = 'queued' ORDER BY updated_at, id"):
            if slots <= 0:
                break
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
            task = self.db.task(task_id)
            agent = self.agents.get(task["agent"])
            if runner.session and not runner.session.alive and task["status"] == "cancelled" and \
                    agent and agent.capabilities.resume:
                self.prewarm(task_id)  # the interrupt ended the process, and a correction usually follows

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
        runner = self.runners.setdefault(task_id, Runner())
        async with runner.opening:  # a prewarm may be starting it already
            await self._open_session(task_id, runner)

    async def _open_session(self, task_id: int, runner: Runner):
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
        path, policy = self._policy(agent, project, choice)
        if task["resume_token"] is None and (session := await self._take_spare(task_id, agent, policy)):
            runner.session = session
        else:
            # Without resume, a new process starts a new conversation; the old one can't be continued.
            token = task["resume_token"] if agent.capabilities.resume else None
            runner.session = await agent.open(path, token, policy,
                                              lambda event: self._on_event(task_id, event),
                                              lambda request: self._on_approval(task_id, request))
        runner.choice = choice
        if runner.session.resume_token != task["resume_token"]:
            self.db.update_task(task_id, resume_token=runner.session.resume_token)

    def _policy(self, agent: Agent, project: dict, choice: tuple[str | None, str | None]) -> tuple[Path, TurnPolicy]:
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
        return path, policy

    # Prewarming

    async def _prewarm_task(self, task_id: int):
        """Start the task's session for a follow-up, unless it has one or is about to run."""
        task = self.db.task(task_id)
        runner = self.runners.get(task_id)
        if task is None or task["status"] in (*ACTIVE, "queued") or (runner and runner.session and runner.session.alive):
            return
        try:
            await self._ensure_session(task_id)
        except Exception as error:
            log.info("task %s: couldn't start the agent early: %s", task_id, error)
            return
        if self.db.task(task_id)["status"] not in (*ACTIVE, "queued"):
            log.debug("task %s: agent started early", task_id)
            self._arm_idle_close(task_id, PREWARM_IDLE_S)

    async def _prewarm_spare(self, project_id: int, model: str | None, effort: str | None):
        """Start a session for a new task in the project, unless a matching one is up or starting."""
        project = self.db.project(project_id)
        if project is None or (agent := self.agents.get(project["agent"])) is None:
            return
        choice = _choice({"agent": project["agent"], "model": model, "effort": effort}, project)
        try:
            path, policy = self._policy(agent, project, choice)
        except TaskError:
            return
        key = (project["agent"], policy)
        spare = self.spares.get(project_id)
        if spare and spare.key == key and not (spare.ready.done() and not self._spare_alive(spare)):
            self._arm_spare_close(project_id, spare)
            return
        if spare:
            await self._close_spare(project_id)
        spare = Spare(key, asyncio.ensure_future(agent.open(path, None, policy,
                                                             lambda event: self._spare_event(spare, event),
                                                             lambda request: self._spare_approval(spare, request))))
        self.spares[project_id] = spare
        try:
            await asyncio.shield(spare.ready)
        except Exception as error:
            log.info("project %s: couldn't start a spare agent: %s", project_id, error)
            if self.spares.get(project_id) is spare:
                del self.spares[project_id]
            return
        if self.spares.get(project_id) is spare:
            log.debug("project %s: spare agent started", project_id)
            self._arm_spare_close(project_id, spare)

    async def _take_spare(self, task_id: int, agent: Agent, policy: TurnPolicy) -> AgentSession | None:
        """The project's spare session for this new task, if it was started the same way."""
        project_id = self.db.task(task_id)["project_id"]
        spare = self.spares.get(project_id)
        if spare is None or spare.key != (agent.name, policy):
            return None
        del self.spares[project_id]
        if spare.timer:
            spare.timer.cancel()
        try:
            session = await spare.ready
        except Exception:
            return None
        if not session.alive:
            await session.close()
            return None
        spare.task_id = task_id
        log.debug("task %s: took over the project's spare agent", task_id)
        return session

    def _spare_alive(self, spare: Spare) -> bool:
        return spare.ready.done() and not spare.ready.cancelled() and spare.ready.exception() is None \
            and spare.ready.result().alive

    def _arm_spare_close(self, project_id: int, spare: Spare):
        if spare.timer:
            spare.timer.cancel()
        spare.timer = asyncio.get_running_loop().call_later(
            PREWARM_IDLE_S, lambda: self._closing(self._close_spare(project_id, spare)))

    def _closing(self, coroutine):
        job = asyncio.ensure_future(coroutine)
        self.closing.add(job)
        job.add_done_callback(self.closing.discard)

    async def _close_spare(self, project_id: int, only: Spare | None = None):
        spare = self.spares.get(project_id)
        if spare is None or (only is not None and spare is not only):
            return
        del self.spares[project_id]
        if spare.timer:
            spare.timer.cancel()
        try:
            session = await spare.ready
        except Exception:
            return
        await session.close()

    async def _spare_event(self, spare: Spare, event: AgentEvent):
        if spare.task_id is not None:
            await self._on_event(spare.task_id, event)

    async def _spare_approval(self, spare: Spare, request: ApprovalRequest) -> Decision:
        if spare.task_id is None:
            return "cancel"  # nothing was asked of it yet
        return await self._on_approval(spare.task_id, request)

    def _agent(self, task: dict) -> Agent:
        agent = self.agents.get(task["agent"])
        if agent is None:
            raise TaskError(f"agent {task['agent']!r} is not available")
        return agent

    def _arm_idle_close(self, task_id: int, timeout: float | None = None):
        """Close the agent's process once it has been idle a while; never while its work runs."""
        runner = self.runners.get(task_id)
        if runner and runner.idle_timer:
            runner.idle_timer.cancel()
            runner.idle_timer = None
        if runner and runner.session and not runner.background and self.db.task(task_id)["status"] not in ACTIVE:
            loop = asyncio.get_running_loop()
            runner.idle_timer = loop.call_later(timeout if timeout is not None else self.settings.tasks.idle_timeout_s,
                                                lambda: self._closing(self._close_session(task_id)))

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
            title, delta = runner.streams.setdefault(event.data["item_id"], titles.Stream()).feed(event.data["delta"])
            if title:
                await self._retitle(task_id, title)
            if delta:
                await self.publish({"type": "delta", "task_id": task_id, **event.data, "delta": delta})
            return
        if event.kind == "message":
            runner.streams.pop(event.data["item_id"], None)
            title, text = titles.take(event.data["text"])
            if title:
                await self._retitle(task_id, title)
            if not text:
                return  # nothing but its title
            message_id = self.db.add_message(task_id, "agent", text, "done")
            await self.publish({"type": "message", "task_id": task_id,
                                "message": self.db.one("SELECT * FROM task_messages WHERE id = ?", message_id)})
            return
        if event.kind == "usage":
            self.db.add_usage(task_id, event.data["input"], event.data["cached"], event.data["output"])
            await self._publish_task(task_id)
            return
        if event.kind == "limits":
            await self.limits.report(self.db.task(task_id)["agent"], event.data["windows"])
            return
        if event.kind == "turn_completed" and event.data.get("limit") is not None:
            event = AgentEvent(event.kind, {**event.data, **self._limit_reached(task_id, event.data["limit"])})
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

    def _limit_reached(self, task_id: int, limit: dict) -> dict:
        """The turn stopped at a usage limit: say which, and when it frees up, in Sennet's own words.
        What the agent didn't say, the limits it reported last may."""
        agent = self.db.task(task_id)["agent"]
        if limit["resets_at"] is None and (full := self.limits.reached(agent)):
            limit = {"window": full["title"], "resets_at": full["resets_at"]}
        title = self.agents[agent].title if agent in self.agents else agent
        return {"limit": limit, "error": limit_reached(title, limit["window"], limit["resets_at"])}

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

    async def _retitle(self, task_id: int, title: str):
        """The agent's title for the task: from its first message only, once."""
        runner = self.runners[task_id]
        if runner.titled or self.db.one("SELECT 1 FROM task_messages WHERE task_id = ? AND role = 'agent'", task_id):
            return
        runner.titled = True
        self.db.update_task(task_id, title=title)
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
