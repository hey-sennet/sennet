"""Provider-neutral agent contract.

Adapters translate a CLI's protocol into these events and requests. Nothing here
knows about Codex, Claude Code or Gemini specifics, and nothing outside an adapter
sees a provider's own words: statuses, kinds and modes are the vocabularies below,
and `check_event` holds every adapter to them.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Awaitable, Callable, Literal, Protocol, get_args

if TYPE_CHECKING:
    from .callbacks import CallbackServer

Decision = Literal["approve", "approve_always", "decline", "cancel"]

# How a turn ended. An adapter maps its provider's words to these.
TurnStatus = Literal["completed", "cancelled", "failed"]
# A command, file change or tool call: running while in progress, then one of the others.
ItemStatus = Literal["running", "completed", "failed", "declined"]
ChangeKind = Literal["add", "delete", "update", "move"]
StepStatus = Literal["pending", "active", "done"]
# Who started a turn: the user's message, or the agent itself (see `background_turns`).
TurnStarter = Literal["user", "agent"]

# What the agent may do without asking (the approval policy, §4.4 of the HLD):
#   ask:   asks before anything outside the agent's own trusted set
#   auto:  works freely within its access level, asks only to go beyond it
#   never: never asks; anything beyond its access level fails
#   classifier: the agent's own safety classifier allows or blocks each action, nothing asked
#               up front (Claude Code's auto mode); not a sandbox, so it can reach past `workspace`
ApprovalMode = Literal["ask", "auto", "never", "classifier"]
# What the agent may touch: read-only, edit the project directory, or anything.
Access = Literal["read-only", "workspace", "full"]
APPROVAL_MODES: tuple[str, ...] = get_args(ApprovalMode)
ACCESS_LEVELS: tuple[str, ...] = get_args(Access)

# Given to every agent as instructions alongside its own. Its messages are read aloud as they
# arrive, so it states its plan up front and reports briefly between steps; a long
# message starts with a version written to be heard; the voice hub speaks it as soon as it is
# streamed, and the timeline hides it (voice-protocol §9). `{ask_tool}` names the agent's
# own tool for asking the user, when it has one, because naming it is what stops its use.
SPOKEN_INSTRUCTIONS = """\
The user may hear your messages read aloud by a voice interface instead of reading them.

While you work, keep the user in the conversation:
- Before your first tool call, write one or two sentences saying what you understood them to \
want and how you'll go about it. State the plan, not the answer. Skip this when you can \
answer straight away without tools.
- Between tool calls, when you move on to a new step or find something that changes the \
plan, write a single short sentence, about ten words, saying what you're doing now or what \
you found, such as "The test fails on the date parsing, so I'm looking at that next." Never \
more than one sentence: each update is spoken before the next can be, so a long one falls \
behind your work. Say it in plain words, without commands, code or paths, and don't repeat \
what you've already said. Skip it for quick steps that follow on from the last one.

When a message to the user is longer than about three sentences, or reports the outcome of \
your work, start it with a version written to be heard, inside <spoken></spoken> tags, then \
write the message as you normally would. The spoken version:
- comes first, before anything else in the message;
- talks to the user directly, as "you", in plain sentences, most important first: what was \
done or found, or what went wrong, then anything they need to know or decide;
- keeps every point that matters, with numbers and names exactly as in the full message, and \
adds nothing that isn't in it;
- never repeats a result your updates during the work already said, since the user has just \
heard it. If the updates covered the results, the spoken version only says how it ended and \
anything new, such as "Done: both results are in the report file." The written message below \
it still has everything;
- has no markdown, lists, code, URLs or file paths (say a file's name, not its path), and \
writes symbols as words: "24 dollars", not "$24";
- is usually one to five sentences: shorter than the message, but not cut to a headline;
- ends with your question, if the message asks the user something.

Short messages need no spoken version. Use the tags only in your messages to the user, \
never in files, commands, commit messages or other tool input.

Start your first message in the conversation with a title for the task, three to six words, \
inside <sennet-title></sennet-title> tags, such as <sennet-title>Fix the login \
redirect</sennet-title>, before anything else, even the spoken version. Write it once only; \
it names the task in the user's list and is neither shown in the message nor read aloud.

Don't ask the user questions while you are still working, and don't use a tool to ask them\
{ask_tool}: they can't answer until your turn ends. Work out what you can, then put any \
questions at the end of your final message, where its spoken version ends with them too."""


def spoken_instructions(ask_tool: str | None = None) -> str:
    """SPOKEN_INSTRUCTIONS for an agent whose tool for asking the user is `ask_tool`."""
    return SPOKEN_INSTRUCTIONS.format(ask_tool=f" (such as {ask_tool})" if ask_tool else "")


@dataclass
class AgentEvent:
    """A normalized agent event. `data` holds the kind's fields (see EVENT_FIELDS).

    A turn may send several `message`s (commentary, then the answer); the last
    `message` before `turn_completed` is the turn's answer, and is what gets spoken.

    An agent may keep work running after its turn ends (`background` lists it, between
    turns too). One with `background_turns` starts a turn of its own when that work is
    done: `turn_started` with `by: "agent"`, then the turn's events and `turn_completed`.
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
    # Any other tool call: reading or searching files, the web, a subagent, an MCP server. Sent
    # `running` as it starts and again, with the same item_id, when it ends. `tool` is the name as
    # the user would read it ("Read", "Gmail: search threads"); `summary` a short line of its input,
    # never its output (see `tool_summary`).
    "tool_used": {"item_id": None, "tool": None, "summary": None, "status": get_args(ItemStatus)},
    "plan": {"steps": None},
    "error": {"message": None},
    "turn_completed": {"status": get_args(TurnStatus), "error": None},
    "turn_started": {"by": get_args(TurnStarter)},
    # The work still running after the turn, each {"id", "description"}; empty once it is done.
    "background": {"tasks": None},
    # Tokens the turn used, sent before its `turn_completed`: all input (cached included), the
    # part of it read from cache, and output (reasoning included).
    "usage": {"input": None, "cached": None, "output": None},
    # The account's usage limits as the agent last reported them, each window a LimitWindow's
    # fields. They belong to the account, not the task; a report may hold only some windows.
    "limits": {"windows": None},
}
# Fields an adapter may add when its provider reports them. Anything else is not the contract's.
OPTIONAL_FIELDS: dict[str, tuple[str, ...]] = {
    "command_started": ("cwd",),          # the directory the command runs in
    "command_finished": ("duration_ms",),
    "error": ("will_retry",),             # the agent retries on its own, so the turn goes on
    # The turn failed at a usage limit: {"window": its title, "resets_at"}, either None when unknown.
    "turn_completed": ("limit",),
}
LIMIT_WINDOW_FIELDS = {"id", "title", "used", "resets_at"}

SUMMARY_LIMIT = 120
# The input fields that say most about a call, in the order to look for them.
SUMMARY_KEYS = ("query", "q", "url", "pattern", "file_path", "notebook_path", "path", "description", "prompt", "word", "name",
                "title", "subject", "id")


def tool_summary(data, limit: int = SUMMARY_LIMIT) -> str:
    """One short line from a tool call's input: its most telling field, else its first text."""
    if not isinstance(data, dict):
        return ""
    texts = [data.get(key) for key in SUMMARY_KEYS] + list(data.values())
    text = next((t for t in texts if isinstance(t, str) and t.strip()), "")
    text = " ".join(text.split())
    return text if len(text) <= limit else text[:limit - 1] + "…"


def mcp_tool_title(server: str, tool: str) -> str:
    """An MCP server's tool as the user would read it: glossary, look_up → "glossary: look up"."""
    def words(name: str) -> str:
        return " ".join(name.replace("_", " ").replace("-", " ").split())
    return f"{words(server)}: {words(tool)}" if server else words(tool)


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
    for name in event.data.keys() - fields.keys() - set(OPTIONAL_FIELDS.get(event.kind, ())):
        problems.append(f"{event.kind} has a field the contract doesn't define: {name}")
    if event.kind == "file_change":
        for change in event.data.get("changes") or []:
            if change.get("kind") not in get_args(ChangeKind):
                problems.append(f"file_change kind {change.get('kind')!r} is not one of {get_args(ChangeKind)}")
    if event.kind == "background":
        for task in event.data.get("tasks") or []:
            if set(task) != {"id", "description"}:
                problems.append(f"background task {task!r} isn't just an id and a description")
    if event.kind == "limits":
        for window in event.data.get("windows") or []:
            if set(window) != LIMIT_WINDOW_FIELDS:
                problems.append(f"limit window {window!r} doesn't have just {sorted(LIMIT_WINDOW_FIELDS)}")
    if event.kind == "turn_completed" and (limit := event.data.get("limit")) is not None:
        if set(limit) != {"window", "resets_at"}:
            problems.append(f"turn_completed limit {limit!r} isn't just a window and resets_at")
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
    # Load the project's own agent configuration (hooks, MCP servers), which can run code unasked.
    project_config: bool = False
    # The model and reasoning effort, by the ids `Agent.models` lists; None for the agent's own choice.
    model: str | None = None
    effort: str | None = None


@dataclass(frozen=True)
class ModelOption:
    """A model an agent can run, as its CLI names it."""

    id: str                            # what the CLI takes, e.g. "opus" or "gpt-6.1-sol"
    title: str
    efforts: tuple[str, ...] = ()      # reasoning effort levels it takes, lowest first
    default_effort: str | None = None
    default: bool = False              # the agent's own default model


@dataclass(frozen=True)
class Capabilities:
    """What an agent really supports. Unsupported behaviour is shown, not faked (HLD §4.1).

    Nothing has a default: each adapter states every capability, so none is claimed by omission.
    """

    interrupt: bool                    # can stop a turn and keep the session
    resume: bool                       # can continue a task's conversation in a new process
    live_approvals: bool               # asks for approval mid-turn, so `ask` mode works
    approval_modes: tuple[str, ...]    # of APPROVAL_MODES
    access_levels: tuple[str, ...]     # of ACCESS_LEVELS
    background_turns: bool             # starts a turn itself to report work it left running
    project_config: bool               # leaves a project's own configuration out unless the project opts in
    live_model_change: bool            # changes model and effort between turns; else its process restarts, resuming


EventSink = Callable[[AgentEvent], Awaitable[None]]
ApprovalHandler = Callable[[ApprovalRequest], Awaitable[Decision]]


@dataclass(frozen=True)
class LimitWindow:
    """One of the account's usage limits, such as the five-hour one, as the agent reports it."""

    id: str                            # stable across reports: "session" (five hours), "weekly", ...
    title: str                         # as the user knows it, e.g. "5-hour"; read out as "Claude's 5-hour limit"
    used: int                          # percent of it used, 0 to 100
    resets_at: float | None            # Unix time


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

    async def set_model(self, model: str | None, effort: str | None) -> None:
        """Use this model and effort from the next turn (None: the agent's default). Only called
        when `capabilities.live_model_change`; otherwise the session is reopened with them."""

    async def close(self) -> None:
        """Stop the agent process. With `capabilities.resume`, the conversation continues
        later from `resume_token`."""

    @property
    def alive(self) -> bool: ...


class Agent(Protocol):
    """An adapter, built from the command that starts its CLI (`agents.commands` in the config) and
    the daemon's MCP endpoint (agents.callbacks), for a CLI that calls back into Sennet."""

    name: str
    title: str                         # as the user knows it, e.g. "Claude Code"
    capabilities: Capabilities
    default_command: list[str]

    def __init__(self, command: list[str], callbacks: CallbackServer | None = None): ...

    async def status(self) -> AgentStatus: ...

    async def models(self) -> list[ModelOption]:
        """The models the user may pick from, the agent's default marked if it is known."""

    async def limits(self) -> list[LimitWindow] | None:
        """The account's usage limits, asked for without using any; None when the agent can't say
        (not logged in, or logged in without a plan that has them)."""

    async def open(self, cwd: Path, resume_token: str | None, policy: TurnPolicy,
                   on_event: EventSink, on_approval: ApprovalHandler) -> AgentSession:
        """Start a session. A resumed conversation may keep the model it last used, so with
        `policy.model` None the adapter asks for the agent's default explicitly when resuming."""
