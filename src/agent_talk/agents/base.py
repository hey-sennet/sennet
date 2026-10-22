"""Provider-neutral agent contract.

Adapters translate a CLI's protocol into these events and requests. Nothing here
knows about Codex, Claude Code or Gemini specifics, and nothing outside an adapter
sees a provider's own words: statuses, kinds and modes are the vocabularies below,
and `check_event` holds every adapter to them.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Awaitable, Callable, Literal, Protocol, get_args

Decision = Literal["approve", "approve_always", "decline", "cancel"]

# How a turn ended. An adapter maps its provider's words to these.
TurnStatus = Literal["completed", "cancelled", "failed"]
# A command or file change: running while in progress, then one of the others.
ItemStatus = Literal["running", "completed", "failed", "declined"]
ChangeKind = Literal["add", "delete", "update", "move"]
StepStatus = Literal["pending", "active", "done"]

# What the agent may do without asking (the approval policy, §4.4 of the HLD):
#   ask:   asks before anything outside the agent's own trusted set
#   auto:  works freely within its access level, asks only to go beyond it
#   never: never asks; anything beyond its access level fails
ApprovalMode = Literal["ask", "auto", "never"]
# What the agent may touch: read-only, edit the project directory, or anything.
Access = Literal["read-only", "workspace", "full"]
APPROVAL_MODES: tuple[str, ...] = get_args(ApprovalMode)
ACCESS_LEVELS: tuple[str, ...] = get_args(Access)


@dataclass
class AgentEvent:
    """A normalized agent event. `data` holds the kind's fields (see EVENT_FIELDS).

    A turn may send several `message`s (commentary, then the answer); the last
    `message` before `turn_completed` is the turn's answer, and is what gets spoken.
    """

    kind: str
    data: dict = field(default_factory=dict)


# Required fields per event kind, and the vocabulary a field must use, if any.
EVENT_FIELDS: dict[str, dict[str, tuple | None]] = {
    "message_delta": {"item_id": None, "delta": None},
    "message": {"item_id": None, "text": None},
    "command_started": {"item_id": None, "command": None},
    "command_finished": {"item_id": None, "command": None, "status": get_args(ItemStatus),
                         "exit_code": None, "output_tail": None},
    "file_change": {"item_id": None, "status": get_args(ItemStatus), "changes": None},
    "plan": {"steps": None},
    "error": {"message": None},
    "turn_completed": {"status": get_args(TurnStatus), "error": None},
}


def check_event(event: AgentEvent) -> list[str]:
    """What is wrong with an event, by the contract; empty when it conforms."""
    fields = EVENT_FIELDS.get(event.kind)
    if fields is None:
        return [f"unknown event kind {event.kind!r}"]
    problems = []
    for name, allowed in fields.items():
        if name not in event.data:
            problems.append(f"{event.kind} has no {name}")
        elif allowed is not None and event.data[name] not in allowed:
            problems.append(f"{event.kind}.{name} is {event.data[name]!r}, not one of {allowed}")
    if event.kind == "file_change":
        for change in event.data.get("changes") or []:
            if change.get("kind") not in get_args(ChangeKind):
                problems.append(f"file_change kind {change.get('kind')!r} is not one of {get_args(ChangeKind)}")
    if event.kind == "plan":
        for step in event.data.get("steps") or []:
            if step.get("status") not in get_args(StepStatus):
                problems.append(f"plan step status {step.get('status')!r} is not one of {get_args(StepStatus)}")
    return problems


@dataclass
class ApprovalRequest:
    kind: Literal["command", "edit", "permissions"]
    summary: str                       # one line, safe to speak
    command: str | None = None
    actions: list[dict] = field(default_factory=list)   # parsed command actions, if known
    paths: list[str] = field(default_factory=list)       # files to change, if known
    reason: str | None = None
    network: bool = False
    detail: dict = field(default_factory=dict)


@dataclass
class TurnPolicy:
    """How much the agent may do without asking. Each adapter maps it to its CLI's own modes."""

    mode: ApprovalMode
    access: Access


@dataclass(frozen=True)
class Capabilities:
    """What an agent really supports. Unsupported behaviour is shown, not faked (HLD §4.1)."""

    interrupt: bool = True             # can stop a turn and keep the session
    resume: bool = True                # can continue a task's conversation in a new process
    live_approvals: bool = True        # asks for approval mid-turn, so `ask` mode works
    approval_modes: tuple[str, ...] = APPROVAL_MODES
    access_levels: tuple[str, ...] = ACCESS_LEVELS


EventSink = Callable[[AgentEvent], Awaitable[None]]
ApprovalHandler = Callable[[ApprovalRequest], Awaitable[Decision]]


@dataclass
class AgentStatus:
    installed: bool
    logged_in: bool | None
    detail: str


class AgentSession(Protocol):
    # Opaque to everything but the adapter: a thread, session or conversation id that
    # `Agent.open` takes back to continue the same conversation.
    resume_token: str

    async def send(self, text: str) -> None:
        """Start a turn. Events and approval requests arrive through the callbacks
        until a `turn_completed` event."""

    async def interrupt(self) -> None:
        """Ask the agent to stop the current turn. Only called when `capabilities.interrupt`."""

    async def close(self) -> None:
        """Stop the agent process. With `capabilities.resume`, the conversation continues
        later from `resume_token`."""

    @property
    def alive(self) -> bool: ...


class Agent(Protocol):
    name: str
    capabilities: Capabilities

    async def status(self) -> AgentStatus: ...

    async def open(self, cwd: Path, resume_token: str | None, policy: TurnPolicy,
                   on_event: EventSink, on_approval: ApprovalHandler) -> AgentSession: ...
