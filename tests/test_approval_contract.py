"""The approval engine on requests shaped as other agents would send them.

Each fixture is what an adapter would build from that agent's own request: Claude
Code's permission prompts (tool name and input) and ACP's tool calls (kind, title,
locations). Nothing here may depend on how Codex phrases or wraps things.
"""
import asyncio

import pytest

from agent_talk.agents.base import ApprovalRequest, FileChange
from agent_talk.approvals import Approvals
from agent_talk.db import Database

DIFF = "--- a/src/app.py\n+++ b/src/app.py\n-old\n+new\n"

FIXTURES = {
    "claude bash": (ApprovalRequest(kind="command", command="npm test", title="Run the unit tests"),
                    "run npm test", "execute", True),
    "claude edit": (ApprovalRequest(kind="edit", paths=["/repo/src/app.py"],
                                    changes=[FileChange("/repo/src/app.py", "update", DIFF)]),
                    "edit app.py", "edit", True),
    "claude webfetch": (ApprovalRequest(kind="tool", tool="WebFetch", tool_input="https://example.com/docs",
                                        network=True),
                        "use WebFetch: https://example.com/docs", "network", True),
    "claude mcp tool": (ApprovalRequest(kind="tool", tool="mcp__github__create_issue", tool_input="x" * 200),
                        "use mcp__github__create_issue: " + "x" * 79 + "…", "execute", True),
    "acp read": (ApprovalRequest(kind="command", command="cat README.md", read_only=True, title="Read README"),
                 "run cat README.md", "read", True),
    "acp delete": (ApprovalRequest(kind="edit", changes=[FileChange("old.py", "delete"), FileChange("a.py", "move")]),
                   "edit a.py and delete old.py", "destructive", False),
    "only deletes": (ApprovalRequest(kind="edit", paths=["/r/a.py", "/r/b.py"],
                                     changes=[FileChange("/r/a.py", "delete"), FileChange("/r/b.py", "delete")]),
                     "delete a.py, b.py", "destructive", False),
    "acp execute destructive": (ApprovalRequest(kind="command", command="git push origin main"),
                                "run git push origin main", "destructive", False),
    "access": (ApprovalRequest(kind="access", network=True, reason="it needs to download dependencies",
                               extra={"provider": {"anything": [1, 2]}}),
               "get network access: it needs to download dependencies", "network", False),
    "tool without a name": (ApprovalRequest(kind="tool"), "use a tool", "execute", False),
}


@pytest.fixture
def engine(tmp_path):
    db = Database(tmp_path / "db")
    project = db.add_project("app", str(tmp_path), [], "codex")
    task_id = db.add_task(project["id"], "codex", "t")
    published = []

    async def publish(event):
        published.append(event["approval"])

    yield Approvals(db, publish), db.task(task_id), published
    db.close()


async def ask(engine, request, decision="approve"):
    approvals, task, published = engine
    waiting = asyncio.ensure_future(approvals.request(task, request))
    await asyncio.sleep(0)
    shown = published[-1]
    if shown["status"] == "pending":
        await approvals.decide(shown["id"], decision)
    return await waiting, shown


@pytest.mark.parametrize("name", FIXTURES)
async def test_fixture(engine, name):
    request, summary, risk, can_always = FIXTURES[name]
    _, shown = await ask(engine, request)
    assert (shown["summary"], shown["risk"], shown["can_always"]) == (summary, risk, can_always)
    assert shown["detail"]["extra"] == request.extra  # kept apart, never interpreted
    assert shown["detail"]["title"] == request.title


async def test_the_common_detail_fields(engine):
    request = FIXTURES["claude edit"][0]
    _, shown = await ask(engine, request)
    assert shown["detail"]["changes"] == [{"path": "/repo/src/app.py", "kind": "update", "diff": DIFF}]
    assert set(shown["detail"]) == {"command", "read_only", "paths", "changes", "tool", "tool_input",
                                    "reason", "network", "title", "extra"}


async def test_always_for_a_tool_covers_that_tool_only(engine):
    fetch = ApprovalRequest(kind="tool", tool="WebFetch", tool_input="https://a.example", network=True)
    assert (await ask(engine, fetch, "approve_always"))[0] == "approve_always"
    decision, shown = await ask(engine, ApprovalRequest(kind="tool", tool="WebFetch", tool_input="https://b.example"))
    assert (decision, shown["status"], shown["decided_by"].startswith("rule:")) == ("approve", "approved", True)
    _, other = await ask(engine, ApprovalRequest(kind="tool", tool="WebSearch", tool_input="docs"), "decline")
    assert other["status"] == "pending"  # asked, not covered by the WebFetch rule


async def test_access_requests_cannot_be_approved_always(engine):
    approvals, task, published = engine
    waiting = asyncio.ensure_future(approvals.request(task, FIXTURES["access"][0]))
    await asyncio.sleep(0)
    with pytest.raises(ValueError):
        await approvals.decide(published[-1]["id"], "approve_always")
    await approvals.decide(published[-1]["id"], "decline")
    assert await waiting == "decline"


async def test_an_edit_rule_does_not_cover_deleting_files(engine):
    edit = ApprovalRequest(kind="edit", changes=[FileChange("a.py", "update", DIFF)])
    assert (await ask(engine, edit, "approve_always"))[0] == "approve_always"
    decision, _ = await ask(engine, ApprovalRequest(kind="edit", changes=[FileChange("b.py", "update")]))
    assert decision == "approve"  # covered by the rule, without asking
    _, shown = await ask(engine, ApprovalRequest(kind="edit", changes=[FileChange("c.py", "delete")]), "decline")
    assert (shown["status"], shown["risk"]) == ("pending", "destructive")  # asked
