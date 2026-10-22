"""Claude Code adapter: `claude -p` over stream-json on stdio (docs/m4-agents.md).

One process per task session. Each user message on stdin starts a turn, which
`result` ends; `system/init` opens every turn, so one that opens with no message
of ours in flight is a turn Claude started itself, to report work it left running
in the background. The adapter picks the session id, so the resume token is known
before the process starts; `--resume` continues the conversation in a new one.

Approvals come through `--permission-prompt-tool`: an MCP tool Agent Talk serves
at a per-session URL (agents.callbacks), which waits for the user's answer.
Agent Talk runs the user's own unmodified `claude` on their subscription login,
and never reads or passes on its credentials.
"""
from __future__ import annotations

import asyncio
import difflib
import json
import logging
import os
import re
import shutil
import signal
import uuid
from pathlib import Path

from .base import (ACCESS_LEVELS, APPROVAL_MODES, AgentEvent, AgentStatus, ApprovalHandler, ApprovalRequest,
                   Capabilities, EventSink, FileChange, TurnPolicy, spoken_instructions)
from .callbacks import CallbackServer, Registration

log = logging.getLogger(__name__)

STREAM_LIMIT = 32 * 1024 * 1024
OUTPUT_TAIL = 2000
DIFF_LIMIT = 20000
STATUS_TIMEOUT_S = 15
CLOSE_GRACE_S = 5
# HTTP MCP requests fail after 60 s unless the server entry sets its own timeout (ms); an
# approval waits for the user, who may be away for a while.
APPROVAL_TIMEOUT_MS = 24 * 60 * 60 * 1000

APPROVE_TOOL = {
    "name": "approve",
    "description": "Ask the user whether a tool call may run.",
    "inputSchema": {"type": "object", "properties": {
        "tool_name": {"type": "string"}, "input": {"type": "object"}, "tool_use_id": {"type": "string"}},
        "required": ["tool_name", "input"]},
}
PROMPT_TOOL = f"mcp__agent_talk__{APPROVE_TOOL['name']}"
EDIT_TOOLS = ("Edit", "Write", "NotebookEdit")
# Claude's tool for asking the user mid-turn is disallowed, so the instructions name none.
INSTRUCTIONS = spoken_instructions()
EXIT_CODE_RE = re.compile(r"^Exit code (\d+)")


def permission_args(policy: TurnPolicy, cwd: Path) -> list[str]:
    """Agent Talk's approval mode and access level as Claude Code's permission mode and rules.

    Claude's bash sandbox would map `workspace` exactly, but it doesn't run everywhere
    (nested user namespaces), so access is enforced with modes and path-scoped rules.
    """
    if policy.access == "full":
        mode = "default" if policy.mode == "ask" else "bypassPermissions"
    elif policy.mode == "never":
        mode = "dontAsk"
    elif policy.mode == "auto" and policy.access == "workspace":
        mode = "acceptEdits"
    else:
        mode = "default"
    args = ["--permission-mode", mode]
    if policy.mode == "never":
        args += ["--permission-prompts", "none"]
    disallowed = ["AskUserQuestion"]
    if policy.access == "read-only":
        disallowed += EDIT_TOOLS
    args += ["--disallowedTools", *disallowed]
    if policy.mode == "never" and policy.access == "workspace":
        # An absolute path in a rule takes a leading `//`; a bare tool name would allow edits anywhere.
        args += ["--allowedTools", *(f"{tool}(/{cwd}/**)" for tool in EDIT_TOOLS)]
    return args


def config_args(policy: TurnPolicy) -> list[str]:
    """Without --bare, `claude -p` runs a project's hooks and connects its MCP servers with no trust
    prompt, so they load only when the project opts in. CLAUDE.md is instructions, and always loads."""
    if policy.project_config:
        return ["--setting-sources", "user,project,local"]
    return ["--setting-sources", "user", "--strict-mcp-config"]


def _text(content) -> str:
    """A tool result's content, which is a string or a list of blocks."""
    if isinstance(content, str):
        return content
    return "\n".join(block.get("text", "") for block in content or [] if isinstance(block, dict))


def _shown_path(path: str, cwd: Path) -> str:
    try:
        return str(Path(path).relative_to(cwd))
    except ValueError:
        return path


def _read(path: Path) -> str | None:
    try:
        return path.read_text(errors="replace") if path.is_file() else None
    except OSError:
        return None


def file_change(tool: str, data: dict, cwd: Path) -> FileChange:
    """The change an edit tool call would make, with a diff where it can be worked out."""
    path = data.get("file_path") or data.get("notebook_path") or ""
    shown = _shown_path(path, cwd)
    before = _read(Path(path)) if path else None
    after = None
    if tool == "Write":
        after = data.get("content") or ""
    elif tool == "Edit" and before is not None:
        old, new = data.get("old_string") or "", data.get("new_string") or ""
        after = before.replace(old, new) if data.get("replace_all") else before.replace(old, new, 1)
    diff = ""
    if after is not None:
        diff = "".join(difflib.unified_diff((before or "").splitlines(True), after.splitlines(True),
                                            f"a/{shown}", f"b/{shown}"))[:DIFF_LIMIT]
    return FileChange(shown, "add" if before is None and tool == "Write" else "update", diff)


def approval_request(tool: str, data: dict, cwd: Path) -> ApprovalRequest:
    if tool == "Bash":
        return ApprovalRequest(kind="command", command=(data.get("command") or "").strip(),
                               title=data.get("description"),
                               extra={"run_in_background": True} if data.get("run_in_background") else {})
    if tool in EDIT_TOOLS:
        change = file_change(tool, data, cwd)
        return ApprovalRequest(kind="edit", paths=[change.path], changes=[change])
    if tool == "WebFetch":
        return ApprovalRequest(kind="tool", tool=tool, tool_input=data.get("url"), network=True,
                               reason=data.get("prompt"))
    if tool == "WebSearch":
        return ApprovalRequest(kind="tool", tool=tool, tool_input=data.get("query"), network=True)
    shown = json.dumps(data, ensure_ascii=False)
    return ApprovalRequest(kind="tool", tool=tool, tool_input=shown if len(shown) <= 200 else shown[:199] + "…")


class ClaudeSession:
    def __init__(self, cwd: Path, on_event: EventSink, on_approval: ApprovalHandler, callbacks: CallbackServer):
        self.cwd = cwd
        self.on_event = on_event
        self.on_approval = on_approval
        self.callbacks = callbacks
        self.resume_token = ""          # the Claude Code session id
        self.process: asyncio.subprocess.Process | None = None
        self.registration: Registration | None = None
        self.reader: asyncio.Task | None = None
        self.stderr: asyncio.Task | None = None
        self.unstarted = 0              # messages sent whose turn hasn't opened
        self.in_turn = False
        self.interrupting = False       # the turn now ending was interrupted
        self.interrupted = False        # the process was sent SIGINT, and is ending
        self.tools: dict[str, tuple[str, dict, bool]] = {}  # tool use id → name, input, whether the file existed
        self.declined: set[str] = set()
        self.background: list[dict] = []
        self.message_id = ""            # the assistant message streaming now
        self.blocks: dict[str, int] = {}  # message id → content blocks seen, which numbers the next

    @property
    def alive(self) -> bool:
        """Until interrupted: SIGINT ends the process too, so the next message needs a new one."""
        return self.process is not None and self.process.returncode is None and \
            self.reader is not None and not self.reader.done() and not self.interrupted

    async def start(self, command: list[str], token: str | None, policy: TurnPolicy):
        self.resume_token = token or str(uuid.uuid4())
        self.registration = self.callbacks.register([APPROVE_TOOL], self._tool)
        server = {"type": "http", "url": self.registration.url, "timeout": APPROVAL_TIMEOUT_MS}
        args = [*command, "-p", "--input-format", "stream-json", "--output-format", "stream-json", "--verbose",
                "--include-partial-messages", "--append-system-prompt", INSTRUCTIONS,
                *(["--resume", token] if token else ["--session-id", self.resume_token]),
                *config_args(policy), "--mcp-config", json.dumps({"mcpServers": {"agent_talk": server}}),
                "--permission-prompt-tool", PROMPT_TOOL, *permission_args(policy, self.cwd)]
        try:
            # Its own session: a Ctrl-C meant for the daemon doesn't reach it, and closing it can stop
            # the commands it left running too.
            self.process = await asyncio.create_subprocess_exec(
                *args, cwd=self.cwd, stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE, limit=STREAM_LIMIT, start_new_session=True)
        except BaseException:
            await self.callbacks.unregister(self.registration)
            raise
        self.reader = asyncio.create_task(self._read())
        self.stderr = asyncio.create_task(self._drain_stderr())

    async def send(self, text: str):
        if not self.alive:
            raise RuntimeError("agent process is not running")
        self.unstarted += 1
        line = {"type": "user", "message": {"role": "user", "content": text}}
        self.process.stdin.write(json.dumps(line).encode() + b"\n")
        await self.process.stdin.drain()

    async def interrupt(self):
        """SIGINT ends the turn, and the process with it; the next turn resumes the session."""
        if self.alive:
            self.interrupting = self.interrupted = True
            self.process.send_signal(signal.SIGINT)

    async def close(self):
        if self.registration:
            await self.callbacks.unregister(self.registration)
        if self.process and self.process.returncode is None:
            if self.process.stdin:
                self.process.stdin.close()
            for sig in (signal.SIGTERM, signal.SIGKILL):
                try:
                    await asyncio.wait_for(self.process.wait(), CLOSE_GRACE_S)
                    break
                except asyncio.TimeoutError:
                    try:
                        os.killpg(self.process.pid, sig)
                    except ProcessLookupError:
                        break
            await self.process.wait()
        await asyncio.gather(*(t for t in (self.reader, self.stderr) if t), return_exceptions=True)

    # stdout

    async def _read(self):
        try:
            while line := await self.process.stdout.readline():
                try:
                    message = json.loads(line)
                except json.JSONDecodeError:
                    log.warning("claude sent invalid JSON")
                    continue
                for event in self._translate(message):
                    await self.on_event(event)
        except Exception:
            log.exception("claude reader failed")
        finally:
            await self.process.wait()
            await self._exited()

    async def _exited(self):
        """The process ended: end the turn it was in, and the work it was running."""
        if self.in_turn or self.unstarted:
            self.in_turn, self.unstarted = False, 0
            status = "cancelled" if self.interrupting else "failed"
            await self.on_event(AgentEvent("turn_completed", {
                "status": status, "error": None if self.interrupting else "agent process exited"}))
        if self.background:
            self.background = []
            await self.on_event(AgentEvent("background", {"tasks": []}))

    def _translate(self, message: dict) -> list[AgentEvent]:
        kind, subtype = message.get("type"), message.get("subtype")
        if kind == "system" and subtype == "init":
            return self._turn_opened()
        if kind == "stream_event" and message.get("parent_tool_use_id") is None:
            return self._stream(message.get("event") or {})
        if kind == "assistant" and message.get("parent_tool_use_id") is None:
            return self._assistant(message.get("message") or {})
        if kind == "user" and message.get("parent_tool_use_id") is None:
            content = (message.get("message") or {}).get("content")
            return [e for block in content if isinstance(block, dict) and block.get("type") == "tool_result"
                    for e in self._tool_result(block)] if isinstance(content, list) else []
        if kind == "system" and subtype == "background_tasks_changed":
            self.background = [{"id": str(t.get("task_id")), "description": t.get("description") or ""}
                               for t in message.get("tasks") or []]
            return [AgentEvent("background", {"tasks": self.background})]
        if kind == "system" and subtype == "api_retry":
            error = message.get("error") or "the request to Claude failed"
            return [AgentEvent("error", {"message": f"{error}; retrying", "will_retry": True})]
        if kind == "result":
            return self._result(message)
        return []

    def _turn_opened(self) -> list[AgentEvent]:
        if self.in_turn:
            return []
        self.in_turn = True
        if self.unstarted:
            self.unstarted -= 1
            return []
        return [AgentEvent("turn_started", {"by": "agent"})]

    def _stream(self, event: dict) -> list[AgentEvent]:
        if event.get("type") == "message_start":
            self.message_id = (event.get("message") or {}).get("id") or ""
        elif event.get("type") == "content_block_delta" and (event.get("delta") or {}).get("type") == "text_delta":
            return [AgentEvent("message_delta", {"item_id": f"{self.message_id}:{event.get('index', 0)}",
                                                 "delta": event["delta"].get("text") or ""})]
        return []

    def _assistant(self, message: dict) -> list[AgentEvent]:
        """One content block per line, all with the message's id: the block's index is its count."""
        events = []
        message_id = message.get("id") or ""
        for block in message.get("content") or []:
            index = self.blocks.get(message_id, 0)
            self.blocks[message_id] = index + 1
            if block.get("type") == "text" and block.get("text"):
                events.append(AgentEvent("message", {"item_id": f"{message_id}:{index}", "text": block["text"]}))
            elif block.get("type") == "tool_use":
                name, data = block.get("name"), block.get("input") or {}
                path = data.get("file_path") or data.get("notebook_path")
                self.tools[block["id"]] = (name, data, bool(path) and Path(path).exists())
                if name == "Bash":
                    events.append(AgentEvent("command_started", {"item_id": block["id"],
                                                                 "command": (data.get("command") or "").strip(),
                                                                 "cwd": str(self.cwd)}))
        return events

    def _tool_result(self, block: dict) -> list[AgentEvent]:
        use_id = block.get("tool_use_id")
        if use_id not in self.tools:
            return []
        name, data, existed = self.tools.pop(use_id)
        output = _text(block.get("content"))
        status = "declined" if use_id in self.declined else "failed" if block.get("is_error") else "completed"
        self.declined.discard(use_id)
        if name == "Bash":
            match = EXIT_CODE_RE.match(output) if block.get("is_error") else None
            exit_code = int(match.group(1)) if match else 0 if status == "completed" else None
            if data.get("run_in_background"):
                exit_code = None  # it goes on; Claude reports it when it ends
            return [AgentEvent("command_finished", {
                "item_id": use_id, "command": (data.get("command") or "").strip(), "status": status,
                "exit_code": exit_code, "output_tail": output[-OUTPUT_TAIL:]})]
        if name in EDIT_TOOLS:
            path = data.get("file_path") or data.get("notebook_path") or ""
            return [AgentEvent("file_change", {
                "item_id": use_id, "status": status,
                "changes": [{"path": _shown_path(path, self.cwd), "kind": "update" if existed else "add"}]})]
        return []

    def _result(self, message: dict) -> list[AgentEvent]:
        self.in_turn = False
        self.blocks.clear()
        subtype = message.get("subtype")
        if subtype == "success" and not message.get("is_error"):
            status, error = "completed", None
        elif self.interrupting:
            status, error = "cancelled", None
        else:
            errors = message.get("errors") or []
            status = "failed"
            error = "; ".join(map(str, errors)) or message.get("result") or subtype or "the turn failed"
        self.interrupting = False
        return [AgentEvent("turn_completed", {"status": status, "error": error})]

    # Approvals

    async def _tool(self, name: str, arguments: dict) -> str:
        """The permission prompt tool: Claude asks whether a tool call may run."""
        tool, data, use_id = arguments.get("tool_name") or "", arguments.get("input") or {}, arguments.get("tool_use_id")
        decision = await self.on_approval(approval_request(tool, data, self.cwd))
        if decision in ("approve", "approve_always"):
            # "Always" stays in Agent Talk's rules: updatedPermissions would write to the project's settings.
            return json.dumps({"behavior": "allow", "updatedInput": data})
        if use_id:
            self.declined.add(use_id)
        message = "The user declined this." if decision == "decline" else "The user stopped the task."
        return json.dumps({"behavior": "deny", "message": message})

    async def _drain_stderr(self):
        last = b""
        while line := await self.process.stderr.readline():
            last = line
        if self.process.returncode not in (0, None) and last:
            log.warning("claude exited: %s", last.decode(errors="replace").strip()[:500])


class ClaudeAgent:
    name = "claude"
    title = "Claude Code"
    default_command = ["claude"]
    # SIGINT ends a turn and the process; the session resumes in the next. It asks mid-turn in every
    # mode, and may report work it left running in a turn of its own.
    capabilities = Capabilities(interrupt=True, resume=True, live_approvals=True, approval_modes=APPROVAL_MODES,
                                access_levels=ACCESS_LEVELS, background_turns=True, project_config=True)

    def __init__(self, command: list[str], callbacks: CallbackServer | None = None):
        self.command = command
        self.callbacks = callbacks or CallbackServer()

    async def status(self) -> AgentStatus:
        """From `claude auth status`, which reports how it is signed in; its credentials are never read."""
        if not shutil.which(self.command[0]):
            return AgentStatus(False, None, f"{self.command[0]} not found on PATH")
        try:
            process = await asyncio.create_subprocess_exec(
                *self.command, "auth", "status", "--json", stdin=asyncio.subprocess.DEVNULL,
                stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
            stdout, _ = await asyncio.wait_for(process.communicate(), STATUS_TIMEOUT_S)
            auth = json.loads(stdout)
        except (OSError, asyncio.TimeoutError, json.JSONDecodeError) as error:
            return AgentStatus(True, None, f"could not query claude: {error or type(error).__name__}")
        if not auth.get("loggedIn"):
            return AgentStatus(True, False, "not logged in; run `claude auth login`")
        if auth.get("authMethod") != "claude.ai":
            return AgentStatus(True, True, f"logged in with {auth.get('authMethod') or 'an API key'} "
                                           "(subscription login is recommended)")
        return AgentStatus(True, True, f"logged in ({auth.get('subscriptionType') or 'Claude'} plan)")

    async def open(self, cwd: Path, resume_token: str | None, policy: TurnPolicy, on_event: EventSink,
                   on_approval: ApprovalHandler) -> ClaudeSession:
        session = ClaudeSession(cwd, on_event, on_approval, self.callbacks)
        await session.start(self.command, resume_token, policy)
        return session
