"""Voice approvals: the fixed flow from HLD §4.4 (docs/voice-protocol.md §8).

An approval isn't read out: the client plays a chime and the card shows what it
wants to do, and the next turn answers it. Only a clear answer
counts: yes, no, always or cancel, and "confirm" after a yes for anything
destructive. Anything else repeats the question. Pending approvals never time out
into approval.

Everything here reads the provider-neutral approval (kind, command, paths, tool,
risk, can_always), never an agent's own wording.
"""
from __future__ import annotations

import re

APPROVE = {"yes", "yeah", "yep", "yes please", "approve", "approved", "approve it", "go ahead", "do it",
           "sure", "ok", "okay", "allow", "allow it", "confirm"}
DECLINE = {"no", "nope", "no thanks", "decline", "declined", "deny", "dont", "do not", "dont do it", "reject"}
ALWAYS = {"always", "approve always", "always approve", "yes always", "always allow", "allow always"}
CANCEL = {"cancel", "cancel it", "cancel that", "cancel the task"}
CONFIRM = {"confirm", "confirmed", "i confirm"}

def answer(text: str) -> str | None:
    """approve, decline, approve_always, cancel or confirm; None when it isn't a clear answer."""
    words = " ".join(re.sub(r"[^\w\s]", "", text.lower()).split())
    for decision, phrases in (("approve_always", ALWAYS), ("cancel", CANCEL), ("decline", DECLINE)):
        if words in phrases:
            return decision
    if words in CONFIRM:
        return "confirm"
    return "approve" if words in APPROVE else None


def reprompt(approval: dict) -> list[str]:
    choices = "yes, no, or always" if approval.get("can_always") else "yes or no"
    return [f"Sorry, I need a clear answer. Say {choices}."]


def no_always() -> list[str]:
    return ["That one can't be approved always. Say yes or no."]


def ask_confirm() -> list[str]:
    return ["This could be hard to undo. Say confirm to go ahead, or no."]


def acknowledge(decision: str, project: str) -> list[str]:
    return [{"approve": "Approved.",
             "approve_always": f"Approved, and I'll remember that for {project}.",
             "decline": "Declined.",
             "cancel": "Cancelled."}[decision]]
