"""Voice approvals: the fixed flow from HLD §4.4 (docs/voice-protocol.md §8).

An approval is read out with its project and what it wants to do; a command
itself is never read out, since the card shows it. Only a clear answer
counts: yes, no, always or cancel, and "confirm" after a yes for anything
destructive. Anything else repeats the question. Pending approvals never time out
into approval.

Everything here reads the provider-neutral approval (kind, command, paths, tool,
risk, can_always), never an agent's own wording.
"""
from __future__ import annotations

import re

from .speakable import for_speech

APPROVE = {"yes", "yeah", "yep", "yes please", "approve", "approved", "approve it", "go ahead", "do it",
           "sure", "ok", "okay", "allow", "allow it", "confirm"}
DECLINE = {"no", "nope", "no thanks", "decline", "declined", "deny", "dont", "do not", "dont do it", "reject"}
ALWAYS = {"always", "approve always", "always approve", "yes always", "always allow", "allow always"}
CANCEL = {"cancel", "cancel it", "cancel that", "cancel the task"}
CONFIRM = {"confirm", "confirmed", "i confirm"}

TOOL_INPUT_SPOKEN = 60


def answer(text: str) -> str | None:
    """approve, decline, approve_always, cancel or confirm; None when it isn't a clear answer."""
    words = " ".join(re.sub(r"[^\w\s]", "", text.lower()).split())
    for decision, phrases in (("approve_always", ALWAYS), ("cancel", CANCEL), ("decline", DECLINE)):
        if words in phrases:
            return decision
    if words in CONFIRM:
        return "confirm"
    return "approve" if words in APPROVE else None


def prompt(approval: dict, project: str) -> list[str]:
    """What to say to ask for an approval: the project, then the action."""
    detail = approval["detail"]
    kind = approval["kind"]
    if kind == "command":
        action = "wants to run a command."  # commands read badly aloud; the card has it
    elif kind == "edit":
        action = f"wants to {approval['summary']}."
    elif kind == "tool":
        tool = _tool_name(detail.get("tool") or "a tool")
        tool_input = for_speech(detail.get("tool_input") or "").rstrip(".")
        on = f" on {tool_input}" if tool_input and len(tool_input) <= TOOL_INPUT_SPOKEN else ""
        action = f"wants to use {tool}{on}."
    else:
        action = "wants network access" if detail.get("network") else "wants more access"
        action += f", because {_mid_sentence(for_speech(detail['reason']).rstrip('.'))}." if detail.get("reason") else "."
    segments = [f"{project} {action}"]
    if approval["risk"] == "destructive":
        segments.append("This could be hard to undo.")
    return segments


def reprompt(approval: dict) -> list[str]:
    choices = "yes, no, or always" if approval.get("can_always") else "yes or no"
    return [f"Sorry, I need a clear answer. Say {choices}."]


def no_always() -> list[str]:
    return ["That one can't be approved always. Say yes or no."]


def ask_confirm() -> list[str]:
    return ["Say confirm to go ahead, or no."]


def acknowledge(decision: str, project: str) -> list[str]:
    return [{"approve": "Approved.",
             "approve_always": f"Approved, and I'll remember that for {project}.",
             "decline": "Declined.",
             "cancel": "Cancelled."}[decision]]


def _mid_sentence(text: str) -> str:
    """Lower-case a leading capital, unless it starts an acronym like "NPM"."""
    return text[:1].lower() + text[1:] if text[1:2].islower() else text


def _tool_name(tool: str) -> str:
    """MCP tools are named like mcp__github__create_issue; say "github create issue"."""
    name = re.sub(r"^mcp__", "", tool)
    return re.sub(r"_+", " ", name).strip() or tool
