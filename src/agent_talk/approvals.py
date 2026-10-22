"""Approval policy: classify requests, apply remembered rules, wait for the user.

Remembered "approve always" rules are scoped to a project. Command rules match a
command prefix (program plus subcommand, e.g. `npm test`); an edit rule allows all
file edits in the project; a tool rule allows one tool by name. Destructive commands,
edits that delete files, and requests for more access never get a rule and are always
asked.

Everything here works on the provider-neutral ApprovalRequest; adapters have already
unwrapped commands and judged whether they only read.
"""
from __future__ import annotations

import asyncio
import posixpath
import re
import shlex
from dataclasses import dataclass

from .agents.base import ApprovalRequest, Decision
from .db import Database

DESTRUCTIVE = [
    r"\brm\b", r"\brmdir\b", r"\bshred\b", r"\bdd\b", r"\bmkfs", r"\bsudo\b", r"\bchmod\s+-R\b", r"\bchown\s+-R\b",
    r"\bgit\s+push\b", r"\bgit\s+reset\s+--hard\b", r"\bgit\s+clean\b", r"\bgit\s+checkout\s+--\s", r"\bgit\s+branch\s+-D\b",
    r"\bgit\s+rebase\b", r"\bgit\s+filter-", r"--force\b", r"\bdrop\s+(table|database)\b", r"\btruncate\b",
    r"\bkubectl\s+delete\b", r"\bterraform\s+(apply|destroy)\b", r"\bnpm\s+publish\b", r"\bdocker\s+(rm|rmi|system\s+prune)\b",
]
DESTRUCTIVE_RE = re.compile("|".join(DESTRUCTIVE), re.IGNORECASE)
TOOL_INPUT_SHOWN = 80


def classify(request: ApprovalRequest) -> str:
    """Risk class: read, edit, execute, network or destructive."""
    if request.kind == "edit":
        return "destructive" if any(c.kind == "delete" for c in request.changes) else "edit"
    if request.kind == "access":
        return "network" if request.network else "execute"
    if request.kind == "command" and DESTRUCTIVE_RE.search(request.command or ""):
        return "destructive"
    if request.network:
        return "network"
    if request.read_only:
        return "read"
    return "execute"


def describe(request: ApprovalRequest) -> str:
    """The one-line summary, completing "Wants to …". Safe to show and to speak."""
    if request.kind == "command":
        return f"run {request.command or 'a command'}"
    if request.kind == "edit":
        deleted = [c.path for c in request.changes if c.kind == "delete"]
        edited = [p for p in (request.paths or [c.path for c in request.changes]) if p not in deleted]
        parts = [f"edit {_names(edited)}"] if edited else []
        parts += [f"delete {_names(deleted)}"] if deleted else []
        return " and ".join(parts) or "edit files"
    if request.kind == "tool":
        tool_input = (request.tool_input or "").strip().replace("\n", " ")
        if len(tool_input) > TOOL_INPUT_SHOWN:
            tool_input = tool_input[:TOOL_INPUT_SHOWN - 1] + "…"
        return f"use {request.tool or 'a tool'}" + (f": {tool_input}" if tool_input else "")
    what = "network access" if request.network else "more access"
    return f"get {what}" + (f": {request.reason}" if request.reason else "")


def _names(paths: list[str]) -> str:
    names = [posixpath.basename(p) for p in paths]
    shown = ", ".join(names[:3])
    return f"{shown} and {len(names) - 3} more" if len(names) > 3 else shown


def detail(request: ApprovalRequest) -> dict:
    """What is stored and shown with an approval: the common fields, then provider extras."""
    return {"command": request.command, "read_only": request.read_only, "paths": request.paths,
            "changes": [{"path": c.path, "kind": c.kind, "diff": c.diff} for c in request.changes],
            "tool": request.tool, "tool_input": request.tool_input, "reason": request.reason,
            "network": request.network, "title": request.title, "extra": request.extra}


def command_prefix(command: str) -> str | None:
    """The scope an 'approve always' rule covers: program plus subcommand, when there is one."""
    command = command.strip()
    if re.search(r"[|;&<>`$()]", command):
        return None  # pipelines and substitutions are too broad to remember
    try:
        words = shlex.split(command)
    except ValueError:
        return None
    if not words:
        return None
    prefix = [words[0]]
    if len(words) > 1 and re.fullmatch(r"[a-z][a-z0-9:_-]*", words[1]):
        prefix.append(words[1])
    return " ".join(prefix)


def matches(prefix: str, command: str) -> bool:
    try:
        words = shlex.split(command)
    except ValueError:
        return False
    if re.search(r"[|;&<>`$()]", command):
        return False
    rule = prefix.split()
    return words[:len(rule)] == rule


@dataclass
class Pending:
    approval_id: int
    future: asyncio.Future


class Approvals:
    def __init__(self, db: Database, publish):
        self.db = db
        self.publish = publish
        self.pending: dict[int, Pending] = {}

    async def request(self, task: dict, request: ApprovalRequest) -> Decision:
        risk = classify(request)
        rule = self._matching_rule(task["project_id"], request, risk)
        approval_id = self.db.add_approval(task["id"], request.kind, risk, describe(request), detail(request))
        if rule:
            self.db.decide_approval(approval_id, "approved", f"rule:{rule['id']}")
            await self.publish({"type": "approval", "approval": self.get(approval_id)})
            return "approve"
        future = asyncio.get_running_loop().create_future()
        self.pending[approval_id] = Pending(approval_id, future)
        await self.publish({"type": "approval", "approval": self.get(approval_id)})
        try:
            return await future
        finally:
            self.pending.pop(approval_id, None)

    async def decide(self, approval_id: int, decision: Decision, decided_by: str = "user") -> dict:
        pending = self.pending.get(approval_id)
        approval = self.db.approval(approval_id)
        if pending is None or approval is None or approval["status"] != "pending":
            raise LookupError("no pending approval with that id")
        if decision == "approve_always":
            rule = self._rule_for(approval)
            if rule is None:
                raise ValueError("this request can't be approved always; approve it once instead")
            task = self.db.task(approval["task_id"])
            self.db.add_rule(task["project_id"], *rule)
        status = {"approve": "approved", "approve_always": "approved", "decline": "declined", "cancel": "cancelled"}
        self.db.decide_approval(approval_id, status[decision], decided_by)
        pending.future.set_result(decision)
        approval = self.get(approval_id)
        await self.publish({"type": "approval", "approval": approval})
        return approval

    def get(self, approval_id: int) -> dict | None:
        """An approval as clients see it: the stored row plus whether "always" can apply."""
        approval = self.db.approval(approval_id)
        return self.present(approval) if approval else None

    def present(self, approval: dict) -> dict:
        return {**approval, "can_always": self._rule_for(approval) is not None}

    async def cancel_for_task(self, task_id: int):
        for approval_id in [p.approval_id for p in self.pending.values()]:
            approval = self.db.approval(approval_id)
            if approval and approval["task_id"] == task_id:
                await self.decide(approval_id, "cancel", "cancelled")

    def _rule_for(self, approval: dict) -> tuple[str, str] | None:
        """The rule "approve always" would create, or None when it can't apply."""
        if approval["risk"] == "destructive" or approval["kind"] == "access":
            return None
        if approval["kind"] == "edit":
            return ("edit", "*")
        if approval["kind"] == "tool":
            tool = approval["detail"].get("tool")
            return ("tool", tool) if tool else None
        prefix = command_prefix(approval["detail"].get("command") or "")
        return ("command", prefix) if prefix else None

    def _matching_rule(self, project_id: int, request: ApprovalRequest, risk: str) -> dict | None:
        if risk == "destructive" or request.kind == "access":
            return None
        for rule in self.db.rules(project_id):
            if request.kind == "edit" and rule["kind"] == "edit":
                return rule
            if request.kind == "command" and rule["kind"] == "command" and matches(rule["pattern"], request.command or ""):
                return rule
            if request.kind == "tool" and rule["kind"] == "tool" and rule["pattern"] == request.tool:
                return rule
        return None
