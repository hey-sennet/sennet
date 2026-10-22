"""Voice approvals: reading a request out, and hearing the answer."""
import pytest

from agent_talk.voice.asking import answer, prompt


@pytest.mark.parametrize("text, expected", [
    ("Yes.", "approve"), ("Yeah, go ahead", None), ("Go ahead.", "approve"), ("OK", "approve"),
    ("No.", "decline"), ("Don't.", "decline"), ("Nope!", "decline"),
    ("Always.", "approve_always"), ("Yes, always.", "approve_always"),
    ("Cancel.", "cancel"), ("Confirm.", "confirm"),
    ("Yes, but run it in the backend instead.", None), ("Maybe.", None), ("", None),
])
def test_only_clear_answers_count(text, expected):
    assert answer(text) == expected


def approval(kind, risk="execute", **detail):
    base = {"command": None, "paths": [], "changes": [], "tool": None, "tool_input": None,
            "reason": None, "network": False}
    return {"id": 1, "task_id": 1, "kind": kind, "risk": risk, "summary": detail.pop("summary", ""),
            "detail": {**base, **detail}, "can_always": True}


def test_a_command_is_not_read_out():
    assert prompt(approval("command", command="npm test -- --watch"), "Backend") == [
        "Backend wants to run a command."]


def test_destructive_requests_say_so():
    assert prompt(approval("command", "destructive", command="git push origin main"), "Backend") == [
        "Backend wants to run a command.", "This could be hard to undo."]


def test_edits_use_the_engine_summary():
    assert prompt(approval("edit", "edit", summary="edit app.py and 2 more"), "Web")[0] == "Web wants to edit app.py and 2 more."


def test_tools_are_named_plainly():
    assert prompt(approval("tool", tool="mcp__github__create_issue"), "Web")[0] == "Web wants to use github create issue."
    assert prompt(approval("tool", tool="WebFetch", tool_input="https://example.com/a"), "Web")[0] == \
        "Web wants to use WebFetch on a link."


def test_access_requests_give_the_reason():
    assert prompt(approval("access", "network", network=True, reason="It needs to install packages."), "Web")[0] == \
        "Web wants network access, because it needs to install packages."
