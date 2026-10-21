"""Approval policy: classify requests, apply remembered rules, wait for the user.

Remembered "approve always" rules are scoped to a project. Command rules match a
command prefix (program plus subcommand, e.g. `npm test`); an edit rule allows all
file edits in the project. Destructive commands never get a rule and are always asked.
"""
from __future__ import annotations

import asyncio
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
# Shell wrappers Codex reports around the real command.
WRAPPER_RE = re.compile(r"^(?:/\S*/)?(?:bash|sh|zsh)\s+-l?c\s+(.*)$", re.DOTALL)


def unwrap(command: str) -> str:
    match = WRAPPER_RE.match(command.strip())
    if not match:
        return command.strip()
    inner = match.group(1).strip()
    try:
        parts = shlex.split(inner)
        return parts[0] if len(parts) == 1 else inner
    except ValueError:
        return inner


def classify(request: ApprovalRequest) -> str:
    """Risk class: read, edit, execute, network or destructive."""
    if request.kind == "edit":
        return "edit"
    if request.kind == "permissions":
        return "network" if request.network else "execute"
    command = unwrap(request.command or "")
    if DESTRUCTIVE_RE.search(command):
        return "destructive"
    if request.network:
        return "network"
    if request.actions and all(a.get("type") in ("read", "listFiles", "search") for a in request.actions):
        return "read"
    return "execute"


def command_prefix(command: str) -> str | None:
    """The scope an 'approve always' rule covers: program plus subcommand, when there is one."""
    command = unwrap(command)
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
        words = shlex.split(unwrap(command))
    except ValueError:
        return False
    if re.search(r"[|;&<>`$()]", unwrap(command)):
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
        detail = {"command": request.command, "paths": request.paths, "reason": request.reason, **request.detail}
        approval_id = self.db.add_approval(task["id"], request.kind, risk, request.summary, detail)
        if rule:
            self.db.decide_approval(approval_id, "approved", f"rule:{rule['id']}")
            await self.publish({"type": "approval", "approval": self.db.approval(approval_id)})
            return "approve"
        future = asyncio.get_running_loop().create_future()
        self.pending[approval_id] = Pending(approval_id, future)
        await self.publish({"type": "approval", "approval": self.db.approval(approval_id)})
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
        approval = self.db.approval(approval_id)
        await self.publish({"type": "approval", "approval": approval})
        return approval

    async def cancel_for_task(self, task_id: int):
        for approval_id in [p.approval_id for p in self.pending.values()]:
            approval = self.db.approval(approval_id)
            if approval and approval["task_id"] == task_id:
                await self.decide(approval_id, "cancel", "cancelled")

    def _rule_for(self, approval: dict) -> tuple[str, str] | None:
        if approval["risk"] == "destructive" or approval["kind"] == "permissions":
            return None
        if approval["kind"] == "edit":
            return ("edit", "*")
        prefix = command_prefix(approval["detail"].get("command") or "")
        return ("command", prefix) if prefix else None

    def _matching_rule(self, project_id: int, request: ApprovalRequest, risk: str) -> dict | None:
        if risk == "destructive" or request.kind == "permissions":
            return None
        for rule in self.db.rules(project_id):
            if request.kind == "edit" and rule["kind"] == "edit":
                return rule
            if request.kind == "command" and rule["kind"] == "command" and matches(rule["pattern"], request.command or ""):
                return rule
        return None
