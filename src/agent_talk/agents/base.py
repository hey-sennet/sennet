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

# Given to every agent as developer instructions. Replies are read aloud, so a long one
# starts with a version written to be heard; the voice hub speaks it as soon as it is
# streamed, and the timeline hides it (voice-protocol §9).
SPOKEN_INSTRUCTIONS = """\
The user may hear your messages read aloud by a voice interface instead of reading them.

When a message to the user is longer than about three sentences, or reports the outcome of \
your work, start it with a version written to be heard, inside <spoken></spoken> tags, then \
write the message as you normally would. The spoken version:
- comes first, before anything else in the message;
- talks to the user directly, as "you", in plain sentences, most important first: what was \
done or found, or what went wrong, then anything they need to know or decide;
- keeps every point that matters, with numbers and names exactly as in the full message, and \
adds nothing that isn't in it;
- has no markdown, lists, code, URLs or file paths (say a file's name, not its path), and \
writes symbols as words: "24 dollars", not "$24";
- is usually one to five sentences: shorter than the message, but not cut to a headline;
- ends with your question, if the message asks the user something.

Short messages need no spoken version. Use the tags only in your messages to the user, \
never in files, commands, commit messages or other tool input.

Don't ask the user questions while you are still working, and don't use a tool to ask them \
(such as request_user_input_async): they can't answer until your turn ends. Work out what you \
can, then put any questions at the end of your final message, where its spoken version ends \
with them too."""


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


ApprovalKind = Literal["command", "edit", "tool", "access"]


@dataclass
class FileChange:
    path: str
    kind: ChangeKind
    diff: str = ""                     # unified diff, if the agent shows one


@dataclass
class ApprovalRequest:
    """Something the agent wants to do that needs the user's say.

    command: run `command`. edit: change the files in `changes` (or `paths`, when the
    agent doesn't show the changes). tool: use any other tool, named in `tool`.
    access: widen the agent's access, such as network or paths outside the project.

    The engine writes the one-line summary from these fields; adapters don't.
    """

    kind: ApprovalKind
    command: str | None = None         # as the user would type it, without shell wrappers
    read_only: bool | None = None      # the adapter's judgement that the command only reads
    paths: list[str] = field(default_factory=list)
    changes: list[FileChange] = field(default_factory=list)
    tool: str | None = None            # e.g. WebFetch, an MCP tool
    tool_input: str | None = None      # short and human-readable, e.g. the URL
    reason: str | None = None          # the agent's explanation, if any
    network: bool = False
    title: str | None = None           # the agent's own one-line description, if it gives one
    extra: dict = field(default_factory=dict)  # provider-only detail: shown raw, never interpreted


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
