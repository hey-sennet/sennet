"""Codex adapter: `codex app-server` over newline-delimited JSON-RPC on stdio.

One app-server process per task session. The thread is persisted by Codex, so a
closed session resumes later with `thread/resume`; the thread id is the resume
token. Codex's own statuses, kinds and modes are translated here and go no further.
The model and reasoning effort go with each turn, so they change between turns.

Usage limits come from `account/rateLimits/updated` during turns and from
`account/rateLimits/read` otherwise, which reads the account's usage without using any.

A turn can end with a command still running (Codex's background terminals): that is the
task's background work until the command's `item/completed` arrives, between turns.
Codex doesn't start a turn of its own when it ends (spikes/codex, #3).

Images go with a turn as `localImage` inputs, by path: Codex reads the file itself.
"""
from __future__ import annotations

import asyncio
import itertools
import json
import logging
import re
import shlex
import shutil
import time
from pathlib import Path

from .. import __version__
from .base import (ACCESS_LEVELS, APPROVAL_MODES, AgentEvent, AgentStatus, ApprovalHandler, ApprovalRequest,
                   Capabilities, Decision, EventSink, FileChange, Image, LimitWindow, ModelOption, TurnPolicy,
                   mcp_tool_title, spoken_instructions, tool_summary)
from .callbacks import CallbackServer

log = logging.getLogger(__name__)

REQUEST_TIMEOUT_S = 30
MODELS_TTL_S = 600
# Codex messages can carry whole diffs and command output.
STREAM_LIMIT = 32 * 1024 * 1024
OUTPUT_TAIL = 2000
DIFF_LIMIT = 20000

APPROVAL_POLICY = {"ask": "untrusted", "auto": "on-request", "never": "never"}
SANDBOX = {"read-only": "read-only", "workspace": "workspace-write", "full": "danger-full-access"}
COMMAND_DECISION = {"approve": "accept", "approve_always": "accept", "decline": "decline", "cancel": "cancel"}
TURN_STATUS = {"completed": "completed", "interrupted": "cancelled", "failed": "failed"}
ITEM_STATUS = {"inProgress": "running", "completed": "completed", "failed": "failed", "declined": "declined"}
STEP_STATUS = {"pending": "pending", "inProgress": "active", "completed": "done"}
# Codex's limit windows by their length in minutes: the contract's id and title for each.
WINDOWS = {300: ("session", "5-hour"), 10080: ("weekly", "weekly")}
# Token counts in `thread/tokenUsage/updated`, as the contract's. Input includes the cached part.
TOKENS = {"inputTokens": "input", "cachedInputTokens": "cached", "outputTokens": "output"}
# Codex skipped the spoken version more often than Claude Code, mostly on short reports of its
# work; this firmer reminder, with an example, had it write one every time (spikes/tokens, work.py).
SPOKEN_REMINDER = """

The voice reads only the <spoken> part of a long message aloud. Without one, the user hears \
the first few sentences of the written message instead. So every message longer than three \
sentences, and every final message that reports your work, must begin with a <spoken></spoken> \
part. For example:

<spoken>I fixed the failing test: the discount was applied twice. All 12 tests pass now.</spoken>
**Fixed:** the discount was applied twice in `cart.py`...

Before you send your final message, check that it begins with <spoken>."""
# Codex's own tool for asking the user mid-turn, which a voice user can't answer (see SPOKEN_INSTRUCTIONS).
INSTRUCTIONS = spoken_instructions("request_user_input_async") + SPOKEN_REMINDER


# Codex reports commands wrapped in a login shell: /bin/bash -lc 'npm test'.
WRAPPER_RE = re.compile(r"^(?:/\S*/)?(?:bash|sh|zsh)\s+-l?c\s+(.*)$", re.DOTALL)
# Codex's parsed command actions that only read.
READ_ACTIONS = {"read", "listFiles", "search"}


def unwrap(command: str) -> str:
    """The command as the user would type it."""
    match = WRAPPER_RE.match(command.strip())
    if not match:
        return command.strip()
    inner = match.group(1).strip()
    try:
        parts = shlex.split(inner)
        return parts[0] if len(parts) == 1 else inner
    except ValueError:
        return inner


def _read_only(actions: list[dict]) -> bool | None:
    if not actions:
        return None
    return all(a.get("type") in READ_ACTIONS for a in actions)


def _change_kind(kind: dict) -> str:
    """Codex reports a rename as an update with a move path."""
    if kind.get("type") == "update" and kind.get("move_path"):
        return "move"
    return kind.get("type") if kind.get("type") in ("add", "delete", "update") else "update"


def tool_used(item: dict, started: bool) -> AgentEvent | None:
    """A tool call that isn't a command or an edit, or None for an item that isn't one. Shapes are
    from spikes/codex/transcripts/tools.jsonl and codex-cli 0.161.0's ThreadItem."""
    kind = item.get("type")
    status = "running" if started else _item_status(item)
    if kind == "mcpToolCall":
        tool, summary = mcp_tool_title(item.get("server") or "", item.get("tool") or ""), tool_summary(item.get("arguments"))
    elif kind == "dynamicToolCall":
        tool, summary = mcp_tool_title(item.get("namespace") or "", item.get("tool") or ""), tool_summary(item.get("arguments"))
    elif kind == "webSearch":
        # It starts with an empty query, filled in when it completes; it has no status of its own.
        action = item.get("action") or {}
        tool = "Web page" if action.get("type") in ("openPage", "findInPage") else "Web search"
        summary = tool_summary({"query": item.get("query"), **action})
        status = "running" if started else "completed"
    elif kind == "imageView":
        tool, summary = "View image", item.get("path") or ""
        status = "running" if started else "completed"
    elif kind == "collabAgentToolCall" and item.get("tool") == "spawnAgent":
        tool, summary = "Subagent", tool_summary({"prompt": item.get("prompt")})
    else:
        return None
    return AgentEvent("tool_used", {"item_id": item["id"], "tool": tool, "summary": summary, "status": status})


class CodexError(Exception):
    pass


def limit_windows(snapshot: dict) -> list[LimitWindow]:
    """The windows in one of Codex's rate-limit snapshots. A rolling update may leave some out."""
    windows = []
    bucket = snapshot.get("limitId")
    for key in ("primary", "secondary"):
        window = snapshot.get(key)
        if not isinstance(window, dict) or window.get("usedPercent") is None:
            continue
        minutes = window.get("windowDurationMins")
        window_id, title = WINDOWS.get(minutes) or (key, f"{round(minutes / 60)}-hour" if minutes else key)
        if bucket not in (None, "codex"):  # a separate limit, such as one model's own
            window_id, title = f"{bucket}:{window_id}", f"{snapshot.get('limitName') or bucket} {title}"
        resets_at = window.get("resetsAt")
        windows.append(LimitWindow(window_id, title, max(0, min(int(window["usedPercent"]), 100)),
                                   float(resets_at) if resets_at else None))
    return windows


async def read_limits(connection: Connection) -> list[LimitWindow] | None:
    result = await connection.request("account/rateLimits/read", {"excludeResetCreditDetails": True})
    buckets = result.get("rateLimitsByLimitId") or {"codex": result.get("rateLimits") or {}}
    return [w for snapshot in buckets.values() if isinstance(snapshot, dict) for w in limit_windows(snapshot)]


async def list_models(connection: Connection) -> list[ModelOption]:
    result = await connection.request("model/list", {"limit": 100})
    return [ModelOption(m["id"], m.get("displayName") or m["id"],
                        tuple(e["reasoningEffort"] for e in m.get("supportedReasoningEfforts") or []),
                        m.get("defaultReasoningEffort"), bool(m.get("isDefault")))
            for m in result.get("data") or [] if not m.get("hidden")]


async def _initialize(connection: Connection):
    await connection.request("initialize", {
        "clientInfo": {"name": "sennet", "title": "Sennet", "version": __version__},
        "capabilities": {"experimentalApi": False, "requestAttestation": False}})
    await connection.notify("initialized")


class Connection:
    """JSON-RPC client for one app-server process."""

    def __init__(self, process: asyncio.subprocess.Process, on_notification, on_request):
        self.process = process
        self.ids = itertools.count(1)
        self.pending: dict[int, asyncio.Future] = {}
        self.on_notification = on_notification
        self.on_request = on_request
        self.closed = asyncio.Event()
        self.reader = asyncio.create_task(self._read())
        self.stderr = asyncio.create_task(self._drain_stderr())
        self.handlers: set[asyncio.Task] = set()

    async def request(self, method: str, params: dict, timeout: float = REQUEST_TIMEOUT_S) -> dict:
        request_id = next(self.ids)
        future = asyncio.get_running_loop().create_future()
        self.pending[request_id] = future
        await self._write({"id": request_id, "method": method, "params": params})
        try:
            return await asyncio.wait_for(future, timeout)
        finally:
            self.pending.pop(request_id, None)

    async def notify(self, method: str, params: dict | None = None):
        await self._write({"method": method, "params": params or {}})

    async def respond(self, request_id, result: dict | None = None, error: dict | None = None):
        message = {"id": request_id, "error": error} if error else {"id": request_id, "result": result}
        await self._write(message)

    async def _write(self, message: dict):
        if self.closed.is_set() or self.process.stdin is None:
            raise CodexError("agent process is not running")
        self.process.stdin.write(json.dumps(message).encode() + b"\n")
        await self.process.stdin.drain()

    async def _read(self):
        try:
            while line := await self.process.stdout.readline():
                try:
                    message = json.loads(line)
                except json.JSONDecodeError:
                    log.warning("codex sent invalid JSON")
                    continue
                if "method" in message and "id" in message:
                    task = asyncio.create_task(self._handle_request(message))
                    self.handlers.add(task)
                    task.add_done_callback(self.handlers.discard)
                elif "method" in message:
                    await self.on_notification(message["method"], message.get("params") or {})
                elif (future := self.pending.get(message.get("id"))) and not future.done():
                    if "error" in message:
                        future.set_exception(CodexError(message["error"].get("message", "request failed")))
                    else:
                        future.set_result(message.get("result") or {})
        except Exception:
            log.exception("codex reader failed")
        finally:
            self.closed.set()
            for future in self.pending.values():
                if not future.done():
                    future.set_exception(CodexError("agent process exited"))

    async def _handle_request(self, message: dict):
        try:
            result = await self.on_request(message["method"], message.get("params") or {})
            if result is None:
                await self.respond(message["id"], error={"code": -32601, "message": "not supported by Sennet"})
            else:
                await self.respond(message["id"], result)
        except CodexError:
            pass  # process gone; nothing to answer
        except Exception:
            log.exception("handling codex request %s failed", message["method"])
            try:
                await self.respond(message["id"], error={"code": -32603, "message": "internal error"})
            except CodexError:
                pass

    async def _drain_stderr(self):
        # Keep the pipe from filling; log only the last line on exit, never content in normal operation.
        last = b""
        while line := await self.process.stderr.readline():
            last = line
        if self.process.returncode not in (0, None) and last:
            log.warning("codex exited: %s", last.decode(errors="replace").strip()[:500])

    async def close(self):
        # Requests still waiting on the user (approvals) end with the process.
        for handler in list(self.handlers):
            handler.cancel()
        await asyncio.gather(*self.handlers, return_exceptions=True)
        if self.process.returncode is None:
            if self.process.stdin:
                self.process.stdin.close()
            try:
                await asyncio.wait_for(self.process.wait(), 5)
            except asyncio.TimeoutError:
                self.process.kill()
                await self.process.wait()
        await asyncio.gather(self.reader, self.stderr, return_exceptions=True)


class CodexSession:
    def __init__(self, on_event: EventSink, on_approval: ApprovalHandler):
        self.on_event = on_event
        self.on_approval = on_approval
        self.resume_token = ""  # the Codex thread id
        self.turn_id: str | None = None
        self.connection: Connection | None = None
        self.file_changes: dict[str, list[dict]] = {}
        self.commands: dict[str, str] = {}    # running command item → the command, as typed
        self.background: list[dict] = []      # those a turn ended with, still running
        self.turn_done: asyncio.Event = asyncio.Event()
        self.watcher: asyncio.Task | None = None
        self.model: str | None = None   # sent with each turn; None leaves it to Codex
        self.effort: str | None = None
        self.tokens = dict.fromkeys(TOKENS.values(), 0)  # used so far in this turn
        self.jobs: set[asyncio.Task] = set()

    @property
    def alive(self) -> bool:
        return self.connection is not None and not self.connection.closed.is_set()

    async def start(self, command: list[str], cwd: Path, thread_id: str | None, policy: TurnPolicy):
        process = await asyncio.create_subprocess_exec(
            *command, cwd=cwd, stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE, limit=STREAM_LIMIT)
        self.connection = Connection(process, self._notification, self._request)
        try:
            await _initialize(self.connection)
            # A resumed thread keeps the model it last ran, so the default is asked for outright.
            self.model, self.effort = await self._choice(policy.model, policy.effort, explicit=bool(thread_id))
            params = {"cwd": str(cwd), "approvalPolicy": APPROVAL_POLICY[policy.mode],
                      "sandbox": SANDBOX[policy.access], "developerInstructions": INSTRUCTIONS}
            if self.model:
                params["model"] = self.model
            if thread_id:
                result = await self.connection.request("thread/resume", {"threadId": thread_id, **params,
                                                                         "excludeTurns": True})
            else:
                result = await self.connection.request("thread/start", params)
            self.resume_token = result["thread"]["id"]
        except BaseException:
            await self.connection.close()
            raise
        self.watcher = asyncio.create_task(self._watch())

    async def send(self, text: str, images: list[Image] = ()):
        self.turn_done.clear()
        inputs = [{"type": "localImage", "path": str(image.path)} for image in images]
        if text or not inputs:
            inputs.append({"type": "text", "text": text, "text_elements": []})
        params = {"threadId": self.resume_token, "input": inputs}
        if self.model:
            params["model"] = self.model
        if self.effort:
            params["effort"] = self.effort
        result = await self.connection.request("turn/start", params)
        self.turn_id = result["turn"]["id"]

    async def set_model(self, model: str | None, effort: str | None):
        # The thread keeps an earlier turn's choice, so going back to the default names it.
        self.model, self.effort = await self._choice(model, effort, explicit=True)

    async def _choice(self, model: str | None, effort: str | None, explicit: bool) -> tuple[str | None, str | None]:
        """The model and effort to send: None for both leaves them to Codex, unless `explicit`,
        when the default model and the chosen model's default effort are named."""
        if model is None and effort is None and not explicit:
            return None, None
        if model is None or effort is None:
            options = await list_models(self.connection)
            chosen = next((o for o in options if o.id == model), None) if model else \
                next((o for o in options if o.default), None)
            model = model or (chosen.id if chosen else None)
            effort = effort or (chosen.default_effort if chosen else None)
        return model, effort

    async def interrupt(self):
        if self.turn_id and self.alive:
            await self.connection.request("turn/interrupt", {"threadId": self.resume_token, "turnId": self.turn_id})

    async def close(self):
        if self.connection:
            await self.connection.close()
        if self.watcher:
            await self.watcher
        await asyncio.gather(*self.jobs, return_exceptions=True)

    async def _watch(self):
        """If the process dies mid-turn, end the turn so the task does not hang, and its work with it."""
        await self.connection.closed.wait()
        if self.turn_id and not self.turn_done.is_set():
            self.turn_done.set()
            await self.on_event(AgentEvent("turn_completed", {"status": "failed", "error": "agent process exited"}))
        if self.background:
            self.background = []
            await self.on_event(AgentEvent("background", {"tasks": []}))

    async def _notification(self, method: str, params: dict):
        if params.get("threadId") not in (None, self.resume_token):
            return
        if method == "thread/tokenUsage/updated":
            # `last` is one model response's; a turn may take several.
            last = (params.get("tokenUsage") or {}).get("last") or {}
            for key, name in TOKENS.items():
                self.tokens[name] += last.get(key) or 0
            return
        if method == "account/rateLimits/updated":
            if windows := limit_windows(params.get("rateLimits") or {}):
                await self.on_event(AgentEvent("limits", {"windows": [vars(w) for w in windows]}))
            return
        event = self._translate(method, params)
        if event:
            if event.kind == "turn_completed":
                if self.turn_done.is_set():
                    return
                self.turn_done.set()
                # Commands still running go on after a finished turn; one that ended otherwise took them with it.
                if event.data["status"] == "completed" and self.commands:
                    self.background += [{"id": i, "description": c} for i, c in self.commands.items()]
                    await self.on_event(AgentEvent("background", {"tasks": self.background}))
                self.commands = {}
                tokens, self.tokens = self.tokens, dict.fromkeys(TOKENS.values(), 0)
                if any(tokens.values()):
                    await self.on_event(AgentEvent("usage", tokens))
                if ((params.get("turn") or {}).get("error") or {}).get("codexErrorInfo") == "usageLimitExceeded":
                    # Which limit, and when it resets, takes a request, which can't be answered while
                    # this notification holds up the reader.
                    job = asyncio.create_task(self._limit_reached(event))
                    self.jobs.add(job)
                    job.add_done_callback(self.jobs.discard)
                    return
            await self.on_event(event)
            if event.kind == "command_finished" and any(t["id"] == event.data["item_id"] for t in self.background):
                self.background = [t for t in self.background if t["id"] != event.data["item_id"]]
                await self.on_event(AgentEvent("background", {"tasks": self.background}))

    async def _limit_reached(self, event: AgentEvent):
        limit = {"window": None, "resets_at": None}
        try:
            windows = await read_limits(self.connection) or []
            await self.on_event(AgentEvent("limits", {"windows": [vars(w) for w in windows]}))
            # The full one that frees up last is the one in the way.
            full = sorted((w for w in windows if w.used >= 100), key=lambda w: w.resets_at or 0)
            if full:
                limit = {"window": full[-1].title, "resets_at": full[-1].resets_at}
        except (CodexError, asyncio.TimeoutError) as error:
            log.info("couldn't read codex's limits: %s", error)
        await self.on_event(AgentEvent("turn_completed", {**event.data, "limit": limit}))

    def _translate(self, method: str, params: dict) -> AgentEvent | None:
        item = params.get("item") or {}
        kind = item.get("type")
        if method == "item/agentMessage/delta":
            return AgentEvent("message_delta", {"item_id": params["itemId"], "delta": params["delta"]})
        if method == "item/started" and kind == "commandExecution":
            self.commands[item["id"]] = unwrap(item["command"])
            return AgentEvent("command_started", {"item_id": item["id"], "command": unwrap(item["command"]),
                                                  "cwd": item.get("cwd")})
        if method == "item/started" and kind == "fileChange":
            self.file_changes[item["id"]] = item.get("changes") or []
            return None
        if method == "item/completed" and kind == "agentMessage":
            return AgentEvent("message", {"item_id": item["id"], "text": item["text"]})
        if method == "item/completed" and kind == "commandExecution":
            self.commands.pop(item["id"], None)
            output = item.get("aggregatedOutput") or ""
            return AgentEvent("command_finished", {
                "item_id": item["id"], "command": unwrap(item["command"]), "status": _item_status(item),
                "exit_code": item.get("exitCode"), "duration_ms": item.get("durationMs"),
                "output_tail": output[-OUTPUT_TAIL:]})
        if method == "item/completed" and kind == "fileChange":
            self.file_changes.pop(item["id"], None)
            return AgentEvent("file_change", {
                "item_id": item["id"], "status": _item_status(item),
                "changes": [{"path": c["path"], "kind": _change_kind(c["kind"]), "diff": (c.get("diff") or "")[:DIFF_LIMIT]}
                            for c in item.get("changes") or []]})
        if method in ("item/started", "item/completed") and (event := tool_used(item, method == "item/started")):
            return event
        if method == "turn/plan/updated":
            return AgentEvent("plan", {"steps": [{"step": s["step"], "status": STEP_STATUS.get(s["status"], "pending")}
                                                 for s in params["plan"]]})
        if method == "error":
            return AgentEvent("error", {"message": params["error"]["message"], "will_retry": params.get("willRetry")})
        if method == "turn/completed":
            turn = params["turn"]
            error = (turn.get("error") or {}).get("message")
            return AgentEvent("turn_completed", {"status": TURN_STATUS.get(turn["status"], "failed"), "error": error})
        return None

    async def _request(self, method: str, params: dict) -> dict | None:
        if method == "item/commandExecution/requestApproval":
            command = unwrap(params.get("command") or "")
            stdin = params.get("kind") == "writeStdin"
            decision = await self.on_approval(ApprovalRequest(
                kind="command", command=command, read_only=_read_only(params.get("commandActions") or []),
                reason=params.get("reason"), network=params.get("networkApprovalContext") is not None,
                title="send input to the running command" if stdin else None,
                extra={"cwd": params.get("cwd"), "network": params.get("networkApprovalContext")}))
            return {"decision": COMMAND_DECISION[decision]}
        if method == "item/fileChange/requestApproval":
            changes = self.file_changes.get(params["itemId"], [])
            decision = await self.on_approval(ApprovalRequest(
                kind="edit", paths=[c["path"] for c in changes], reason=params.get("reason"),
                changes=[FileChange(c["path"], _change_kind(c["kind"]), c.get("diff", "")[:DIFF_LIMIT]) for c in changes],
                extra={"grant_root": params.get("grantRoot")}))
            return {"decision": COMMAND_DECISION[decision]}
        if method == "item/permissions/requestApproval":
            requested = params.get("permissions") or {}
            decision = await self.on_approval(ApprovalRequest(
                kind="access", reason=params.get("reason"), network=requested.get("network") is not None,
                extra={"permissions": requested, "cwd": params.get("cwd")}))
            granted = {k: v for k, v in requested.items() if v is not None} if decision.startswith("approve") else {}
            return {"permissions": granted, "scope": "turn"}
        return None


class CodexAgent:
    name = "codex"
    title = "Codex"
    default_command = ["codex", "app-server"]
    # Interrupts, resumes threads, and asks mid-turn in every mode. A command can outlast its turn,
    # but Codex doesn't start a turn to report it. It has no classifier to hand approvals to.
    capabilities = Capabilities(interrupt=True, resume=True, live_approvals=True,
                                approval_modes=tuple(m for m in APPROVAL_MODES if m != "classifier"),
                                access_levels=ACCESS_LEVELS,
                                background_turns=False, project_config=False, live_model_change=True, images=True)

    def __init__(self, command: list[str], callbacks: CallbackServer | None = None):
        self.command = command  # Codex asks for approvals on its own pipe, not through callbacks
        self.listed: tuple[float, list[ModelOption]] | None = None

    async def status(self) -> AgentStatus:
        if not shutil.which(self.command[0]):
            return AgentStatus(False, None, f"{self.command[0]} not found on PATH")
        session = CodexSession(_ignore_event, _decline)
        process = await asyncio.create_subprocess_exec(
            *self.command, stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE, limit=STREAM_LIMIT)
        connection = Connection(process, session._notification, session._request)
        try:
            await _initialize(connection)
            account = (await connection.request("account/read", {})).get("account")
        except (CodexError, asyncio.TimeoutError) as error:
            return AgentStatus(True, None, f"could not query codex: {error}")
        finally:
            await connection.close()
        if account is None:
            return AgentStatus(True, False, "not logged in; run `codex login`")
        if account.get("type") == "apiKey":
            return AgentStatus(True, True, "logged in with an API key (subscription login is recommended)")
        return AgentStatus(True, True, f"logged in ({account.get('planType') or 'ChatGPT'} plan)")

    async def limits(self) -> list[LimitWindow] | None:
        if not shutil.which(self.command[0]):
            return None
        process = await asyncio.create_subprocess_exec(
            *self.command, stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE, limit=STREAM_LIMIT)
        connection = Connection(process, _ignore_notification, _no_request)
        try:
            await _initialize(connection)
            account = (await connection.request("account/read", {})).get("account")
            if account is None or account.get("type") == "apiKey":
                return None  # an API key has no plan limits
            return await read_limits(connection)
        finally:
            await connection.close()

    async def models(self) -> list[ModelOption]:
        """From `model/list`, kept a few minutes: it takes a process to ask."""
        if self.listed and time.monotonic() - self.listed[0] < MODELS_TTL_S:
            return self.listed[1]
        process = await asyncio.create_subprocess_exec(
            *self.command, stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE, limit=STREAM_LIMIT)
        connection = Connection(process, _ignore_notification, _no_request)
        try:
            await _initialize(connection)
            options = await list_models(connection)
        finally:
            await connection.close()
        self.listed = (time.monotonic(), options)
        return options

    async def open(self, cwd: Path, resume_token: str | None, policy: TurnPolicy, on_event: EventSink,
                   on_approval: ApprovalHandler) -> CodexSession:
        session = CodexSession(on_event, on_approval)
        await session.start(self.command, cwd, resume_token, policy)
        return session


def _item_status(item: dict) -> str:
    return ITEM_STATUS.get(item.get("status"), "failed")


async def _ignore_event(_: AgentEvent):
    pass


async def _decline(_: ApprovalRequest) -> Decision:
    return "decline"


async def _ignore_notification(method: str, params: dict):
    pass


async def _no_request(method: str, params: dict) -> None:
    return None
