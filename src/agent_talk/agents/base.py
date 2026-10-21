"""Provider-neutral agent contract.

Adapters translate a CLI's protocol into these events and requests. Nothing here
knows about Codex, Claude Code or Gemini specifics.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Awaitable, Callable, Literal, Protocol

Decision = Literal["approve", "approve_always", "decline", "cancel"]


@dataclass
class AgentEvent:
    """A normalized agent event.

    kinds: message_delta, message, command_started, command_finished, file_change,
    plan, error, turn_completed. `data` holds the kind-specific fields.
    """

    kind: str
    data: dict = field(default_factory=dict)


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
    """How much the agent may do without asking. Mapped to each CLI's own modes."""

    mode: Literal["ask", "auto", "never"]
    sandbox: Literal["read-only", "workspace-write", "danger-full-access"]


EventSink = Callable[[AgentEvent], Awaitable[None]]
ApprovalHandler = Callable[[ApprovalRequest], Awaitable[Decision]]


@dataclass
class AgentStatus:
    installed: bool
    logged_in: bool | None
    detail: str


class AgentSession(Protocol):
    thread_id: str

    async def send(self, text: str) -> None:
        """Start a turn. Events and approval requests arrive through the callbacks
        until a `turn_completed` event."""

    async def interrupt(self) -> None:
        """Ask the agent to stop the current turn."""

    async def close(self) -> None:
        """Stop the agent process. The thread can be resumed later by ID."""

    @property
    def alive(self) -> bool: ...


class Agent(Protocol):
    name: str

    async def status(self) -> AgentStatus: ...

    async def open(self, cwd: Path, thread_id: str | None, policy: TurnPolicy,
                   on_event: EventSink, on_approval: ApprovalHandler) -> AgentSession: ...
