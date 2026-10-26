"""The Claude Code adapter's translation, permissions and approvals, line by line.

Messages are shaped like the spike's transcripts (spikes/claude). The whole adapter
runs against tests/fake_claude.py in the contract tests.
"""
import json
import time
from pathlib import Path

import pytest

from conftest import pending_approval, wait_for

from sennet.agents.base import AgentEvent, TurnPolicy, check_event, spoken_instructions, untested
from sennet.agents.callbacks import CallbackServer
from sennet.agents.claude import ClaudeAgent, ClaudeSession, approval_request, permission_args, tool_title

CWD = Path("/home/me/repo")


def session() -> ClaudeSession:
    return ClaudeSession(CWD, None, None, CallbackServer())


def kinds(events: list[AgentEvent]) -> list[str]:
    return [e.kind for e in events]


def translate(s: ClaudeSession, *messages: dict) -> list[AgentEvent]:
    events = [e for m in messages for e in s._translate(m)]
    assert [p for e in events for p in check_event(e)] == []
    return events


INIT = {"type": "system", "subtype": "init", "session_id": "s"}


def assistant(message_id: str, block: dict, parent=None) -> dict:
    return {"type": "assistant", "message": {"id": message_id, "content": [block]}, "parent_tool_use_id": parent}


def tool_result(use_id: str, content, is_error=False) -> dict:
    return {"type": "user", "parent_tool_use_id": None,
            "message": {"content": [{"type": "tool_result", "tool_use_id": use_id, "content": content,
                                     "is_error": is_error}]}}


def result(subtype="success", **fields) -> dict:
    return {"type": "result", "subtype": subtype, "is_error": subtype != "success", **fields}


def test_a_chat_turn_streams_text_and_completes():
    s = session()
    s.unstarted = 1
    events = translate(
        s, INIT,
        {"type": "stream_event", "parent_tool_use_id": None, "event": {"type": "message_start", "message": {"id": "msg_1"}}},
        assistant("msg_1", {"type": "thinking", "thinking": ""}),
        {"type": "stream_event", "parent_tool_use_id": None,
         "event": {"type": "content_block_delta", "index": 1, "delta": {"type": "text_delta", "text": "Hel"}}},
        assistant("msg_1", {"type": "text", "text": "Hello."}),
        result())
    assert kinds(events) == ["message_delta", "message", "turn_completed"]
    assert events[0].data["item_id"] == events[1].data["item_id"] == "msg_1:1"
    assert events[2].data == {"status": "completed", "error": None}


def test_a_subagents_messages_are_not_the_conversations():
    s = session()
    events = translate(s, assistant("msg_2", {"type": "text", "text": "inner"}, parent="toolu_x"))
    assert events == []


def test_commands_report_exit_codes_from_their_failures():
    s = session()
    events = translate(
        s,
        assistant("m", {"type": "tool_use", "id": "t1", "name": "Bash", "input": {"command": "ls -la", "description": "List"}}),
        tool_result("t1", "total 4\napp.py"),
        assistant("m", {"type": "tool_use", "id": "t2", "name": "Bash", "input": {"command": "false"}}),
        tool_result("t2", "Exit code 1", is_error=True))
    assert kinds(events) == ["command_started", "command_finished", "command_started", "command_finished"]
    assert (events[1].data["status"], events[1].data["exit_code"]) == ("completed", 0)
    assert (events[3].data["status"], events[3].data["exit_code"]) == ("failed", 1)


def test_other_tool_calls_are_tool_used_from_start_to_end():
    s = session()
    events = translate(
        s,
        assistant("m", {"type": "tool_use", "id": "t1", "name": "Grep", "input": {"pattern": "def main", "path": str(CWD / "src")}}),
        assistant("m", {"type": "tool_use", "id": "t2", "name": "mcp__claude_ai_Gmail__search_threads",
                        "input": {"query": "from:ci", "max_results": 5}}),
        tool_result("t1", "src/app.py"),
        tool_result("t2", "not connected", is_error=True))
    assert [(e.data["item_id"], e.data["tool"], e.data["summary"], e.data["status"]) for e in events] == [
        ("t1", "Grep", "def main", "running"), ("t2", "Gmail: search threads", "from:ci", "running"),
        ("t1", "Grep", "def main", "completed"), ("t2", "Gmail: search threads", "from:ci", "failed")]


def test_the_agents_bookkeeping_and_sennets_own_tools_stay_out_of_the_timeline():
    s = session()
    events = translate(
        s,
        assistant("m", {"type": "tool_use", "id": "t1", "name": "TodoWrite", "input": {"todos": []}}),
        assistant("m", {"type": "tool_use", "id": "t2", "name": "mcp__sennet__approve", "input": {}}),
        assistant("m", {"type": "tool_use", "id": "t3", "name": "ToolSearch", "input": {"query": "select:WebSearch"}}),
        tool_result("t1", "ok"), tool_result("t2", "ok"), tool_result("t3", "ok"))
    assert events == []


@pytest.mark.parametrize("name, title", [
    ("Read", "Read"), ("WebFetch", "WebFetch"),
    ("mcp__claude_ai_Google_Calendar__list_events", "Google Calendar: list events"),
    ("mcp__plugin_runpod_runpod__list-pods", "runpod: list pods"),
    ("mcp__plugin_tools_extra__run", "tools extra: run"),
    ("mcp__blender__get_scene_info", "blender: get scene info"),
])
def test_tools_are_named_as_the_user_reads_them(name, title):
    assert tool_title(name) == title


def test_a_tool_summary_is_its_input_in_one_short_line():
    s = session()
    long = "a " * 200
    events = translate(s, assistant("m", {"type": "tool_use", "id": "t1", "name": "Read", "input": {"file_path": str(CWD / "README.md")}}),
                       assistant("m", {"type": "tool_use", "id": "t2", "name": "Agent", "input": {"description": "Find\nthe bug", "prompt": long}}),
                       assistant("m", {"type": "tool_use", "id": "t3", "name": "mcp__x__y", "input": {"limit": 3, "body": long}}))
    assert [e.data["summary"] for e in events[:2]] == ["README.md", "Find the bug"]
    assert len(events[2].data["summary"]) == 120 and events[2].data["summary"].endswith("…")


def test_a_declined_tool_call_is_declined():
    s = session()
    s.declined.add("t1")
    events = translate(
        s,
        assistant("m", {"type": "tool_use", "id": "t1", "name": "Bash", "input": {"command": "touch x"}}),
        tool_result("t1", "The user declined this.", is_error=True))
    assert (events[1].data["status"], events[1].data["exit_code"]) == ("declined", None)


def test_edits_are_file_changes(tmp_path):
    s = ClaudeSession(tmp_path, None, None, CallbackServer())
    (tmp_path / "app.py").write_text("print('hi')\n")
    events = translate(
        s,
        assistant("m", {"type": "tool_use", "id": "e1", "name": "Edit",
                        "input": {"file_path": str(tmp_path / "app.py"), "old_string": "hi", "new_string": "yo"}}),
        tool_result("e1", "updated"),
        assistant("m", {"type": "tool_use", "id": "e2", "name": "Write",
                        "input": {"file_path": str(tmp_path / "notes.txt"), "content": "done\n"}}),
        tool_result("e2", "created"))
    assert [[{k: c[k] for k in ("path", "kind")} for c in e.data["changes"]] for e in events] == [
        [{"path": "app.py", "kind": "update"}], [{"path": "notes.txt", "kind": "add"}]]
    # Each carries its diff, worked out before the edit ran, for the timeline.
    edit, write = (e.data["changes"][0]["diff"] for e in events)
    assert "-print('hi')" in edit and "+print('yo')" in edit
    assert "+done" in write


def test_background_work_and_the_turn_claude_starts_itself():
    s = session()
    s.unstarted = 1
    events = translate(
        s, INIT,
        assistant("m", {"type": "tool_use", "id": "t1", "name": "Bash",
                        "input": {"command": "sleep 25", "run_in_background": True}}),
        {"type": "system", "subtype": "background_tasks_changed",
         "tasks": [{"task_id": "b5", "run_id": "r", "task_type": "local_bash", "description": "Sleep 25 seconds"}]},
        tool_result("t1", "Command running in background with ID: b5."),
        result(),
        {"type": "system", "subtype": "background_tasks_changed", "tasks": []},
        INIT,
        assistant("n", {"type": "text", "text": "It finished."}),
        result())
    assert kinds(events) == ["command_started", "background", "command_finished", "turn_completed",
                             "background", "turn_started", "message", "turn_completed"]
    assert events[1].data == {"tasks": [{"id": "b5", "description": "Sleep 25 seconds"}]}
    assert events[2].data["exit_code"] is None
    assert events[5].data == {"by": "agent"}


def test_an_interrupted_turn_is_cancelled_and_other_errors_fail():
    s = session()
    s.interrupting = True
    assert translate(s, result("error_during_execution"))[0].data == {"status": "cancelled", "error": None}
    assert translate(s, result("error_max_turns", errors=["too many turns"]))[0].data == \
        {"status": "failed", "error": "too many turns"}


def test_api_retries_are_errors_that_retry():
    events = translate(session(), {"type": "system", "subtype": "api_retry", "attempt": 1, "error": "overloaded"})
    assert events[0].data == {"message": "overloaded; retrying", "will_retry": True}


@pytest.mark.parametrize("mode, access, expected_mode, extra", [
    ("ask", "workspace", "default", []),
    ("ask", "read-only", "default", ["Edit", "Write", "NotebookEdit"]),
    ("auto", "workspace", "acceptEdits", []),
    ("auto", "full", "bypassPermissions", []),
    ("never", "workspace", "dontAsk", []),
    ("never", "read-only", "dontAsk", ["Edit", "Write", "NotebookEdit"]),
    ("never", "full", "bypassPermissions", []),
    ("classifier", "workspace", "auto", []),
    ("classifier", "read-only", "auto", ["Edit", "Write", "NotebookEdit"]),
    ("classifier", "full", "auto", []),
])
def test_policy_maps_to_permission_modes(mode, access, expected_mode, extra):
    args = permission_args(TurnPolicy(mode, access), CWD)
    assert args[args.index("--permission-mode") + 1] == expected_mode
    disallowed = args[args.index("--disallowedTools") + 1:]
    disallowed = disallowed[:next((i for i, a in enumerate(disallowed) if a.startswith("--")), len(disallowed))]
    assert disallowed == ["AskUserQuestion", *extra]
    assert ("--permission-prompts" in args) == (mode == "never")


def test_never_with_workspace_access_allows_edits_in_the_project_only():
    args = permission_args(TurnPolicy("never", "workspace"), CWD)
    allowed = args[args.index("--allowedTools") + 1:]
    assert allowed == ["Edit(//home/me/repo/**)", "Write(//home/me/repo/**)", "NotebookEdit(//home/me/repo/**)"]
    assert "--allowedTools" not in permission_args(TurnPolicy("auto", "workspace"), CWD)


def test_approval_requests_are_the_contracts(tmp_path):
    command = approval_request("Bash", {"command": "npm test", "description": "Run the tests"}, tmp_path)
    assert (command.kind, command.command, command.title) == ("command", "npm test", "Run the tests")
    (tmp_path / "app.py").write_text("a\nb\n")
    edit = approval_request("Edit", {"file_path": str(tmp_path / "app.py"), "old_string": "b", "new_string": "c"},
                            tmp_path)
    assert edit.kind == "edit" and edit.paths == ["app.py"]
    assert "-b\n+c\n" in edit.changes[0].diff and edit.changes[0].kind == "update"
    write = approval_request("Write", {"file_path": str(tmp_path / "new.txt"), "content": "x\n"}, tmp_path)
    assert write.changes[0].kind == "add" and "+x" in write.changes[0].diff
    fetch = approval_request("WebFetch", {"url": "https://example.com", "prompt": "summarise"}, tmp_path)
    assert (fetch.kind, fetch.tool, fetch.tool_input, fetch.network) == ("tool", "WebFetch", "https://example.com", True)


async def test_the_prompt_tool_allows_or_denies():
    decisions = iter(["approve", "decline"])

    async def on_approval(request):
        return next(decisions)

    s = ClaudeSession(CWD, None, on_approval, CallbackServer())
    call = {"tool_name": "Bash", "input": {"command": "ls"}, "tool_use_id": "t1"}
    assert json.loads(await s._tool("approve", call)) == {"behavior": "allow", "updatedInput": {"command": "ls"}}
    assert json.loads(await s._tool("approve", call))["behavior"] == "deny"
    assert s.declined == {"t1"}


@pytest.mark.parametrize("auth, logged_in, detail", [
    ({"loggedIn": True, "authMethod": "claude.ai", "subscriptionType": "max", "email": "me@example.com"}, True,
     "logged in (max plan)"),
    ({"loggedIn": True, "authMethod": "api_key"}, True, "logged in with api_key (subscription login is recommended)"),
    ({"loggedIn": False}, False, "not logged in; run `claude auth login`"),
])
async def test_status_comes_from_auth_status_and_never_shows_the_account(tmp_path, auth, logged_in, detail):
    script = tmp_path / "claude"
    script.write_text(f"#!/bin/sh\necho '{json.dumps(auth)}'\n")
    script.chmod(0o755)
    status = await ClaudeAgent([str(script)]).status()
    assert (status.installed, status.logged_in, status.detail) == (True, logged_in, detail)
    assert "example.com" not in status.detail


async def test_status_reports_the_version_and_warns_when_it_is_newer_than_tested(tmp_path):
    script = tmp_path / "claude"
    script.write_text('#!/bin/sh\n[ "$1" = --version ] && echo "9.0.1 (Claude Code)" && exit\n'
                      'echo \'{"loggedIn": true, "authMethod": "claude.ai", "subscriptionType": "max"}\'\n')
    script.chmod(0o755)
    status = await ClaudeAgent([str(script)]).status()
    assert (status.logged_in, status.version) == (True, "9.0.1")
    assert status.warning == f"Claude Code 9.0.1 is newer than Sennet has been tested with ({ClaudeAgent.tested_version}.x)"


@pytest.mark.parametrize("version, tested, newer", [
    ("2.1.310", "2.1", False), ("2.2.0", "2.1", True), ("2.0.9", "2.1", False), ("3.0.0", "2.1", True),
    ("0.161.0", "0.161.0", False), ("0.161.1", "0.161.0", True), ("2.1.310-beta.1", "2.1", False),
    ("10.0.0", "9.9", True), (None, "2.1", False),
])
def test_only_a_version_newer_than_the_tested_one_is_warned_about(version, tested, newer):
    assert bool(untested("Claude Code", version, tested)) is newer


# Whole sessions, against tests/fake_claude.py on a live server

def claude_project(client, tmp_path, **settings):
    (tmp_path / "repo").mkdir(exist_ok=True)
    project = client.post("/api/projects", json={"path": str(tmp_path / "repo"), "agent": "claude"}).json()
    if settings:
        client.patch(f"/api/projects/{project['id']}", json=settings)
    return project


def task(client, project, text):
    return client.post("/api/tasks", json={"project_id": project["id"], "text": text}).json()


def settled(status="completed"):
    return lambda t: t["status"] == status


def test_claude_runs_with_the_session_id_as_resume_token(live, tmp_path, claude_log):
    project = claude_project(live, tmp_path)
    done = wait_for(live, task(live, project, "hello")["id"], settled())
    assert done["messages"][-1]["text"] == "You said: hello"
    argv = claude_log("argv")[0]
    assert argv[argv.index("--session-id") + 1] == done["resume_token"]
    assert argv[argv.index("--append-system-prompt") + 1] == spoken_instructions()
    assert "--bare" not in argv
    assert argv[argv.index("--setting-sources") + 1] == "user" and "--strict-mcp-config" not in argv
    # The callback secret goes in a 0600 file, not on argv where /proc would leak it.
    assert argv[argv.index("--mcp-config") + 1].endswith(".json")
    assert claude_log("mcp_mode")[0] == "0o600"
    server = claude_log("mcp_server")[0]
    assert server["timeout"] > 60_000
    assert claude_log("cwd")[0] == project["path"]


def test_approvals_reach_the_user_through_the_prompt_tool(live, tmp_path):
    project = claude_project(live, tmp_path)
    started = task(live, project, "run:npm test")
    approval = pending_approval(live)
    assert (approval["kind"], approval["summary"]) == ("command", "run npm test")
    live.post(f"/api/approvals/{approval['id']}", json={"decision": "approve"})
    done = wait_for(live, started["id"], settled())
    assert done["messages"][-1]["text"] == "Ran it."
    finished = [e["data"] for e in done["events"] if e["kind"] == "command_finished"]
    assert (finished[0]["status"], finished[0]["exit_code"]) == ("completed", 0)


def test_a_declined_edit_is_declined(live, tmp_path):
    project = claude_project(live, tmp_path)
    started = task(live, project, "edit:notes.txt")
    approval = pending_approval(live)
    assert approval["kind"] == "edit" and approval["detail"]["changes"][0]["kind"] == "add"
    live.post(f"/api/approvals/{approval['id']}", json={"decision": "decline"})
    done = wait_for(live, started["id"], settled())
    assert done["messages"][-1]["text"] == "Left it alone."
    assert [e["data"]["status"] for e in done["events"] if e["kind"] == "file_change"] == ["declined"]


def test_background_work_is_reported_in_claudes_own_turn(live, tmp_path):
    project = claude_project(live, tmp_path)
    started = task(live, project, "bg:make build")
    live.post(f"/api/approvals/{pending_approval(live)['id']}", json={"decision": "approve"})
    done = wait_for(live, started["id"], lambda t: t["status"] == "completed" and len(t["messages"]) == 3)
    assert [m["text"] for m in done["messages"][1:]] == ["Started it in the background.",
                                                         "The background command make build finished."]
    kinds = [e["kind"] for e in done["events"]]
    assert kinds.index("turn_started") > kinds.index("background")


def test_cancelling_interrupts_and_the_next_message_resumes(live, tmp_path, claude_log):
    project = claude_project(live, tmp_path)
    started = task(live, project, "hang")
    wait_for(live, started["id"], lambda t: any(e["kind"] == "command_started" for e in t["events"]))
    live.post(f"/api/tasks/{started['id']}/cancel")
    cancelled = wait_for(live, started["id"], settled("cancelled"))
    # SIGINT ended the process; the session is resumed straight away, before any follow-up.
    deadline = time.monotonic() + 10
    while len(claude_log("argv")) < 2 and time.monotonic() < deadline:
        time.sleep(0.02)
    second = claude_log("argv")[1]
    assert second[second.index("--resume") + 1] == cancelled["resume_token"]
    live.post(f"/api/tasks/{started['id']}/messages", json={"text": "still there?"})
    done = wait_for(live, started["id"], settled())
    assert done["messages"][-1]["text"] == "You said: still there?"
    assert len(claude_log("argv")) == 2  # the follow-up used the resumed process


def test_access_and_mode_reach_claude(live, tmp_path, claude_log):
    project = claude_project(live, tmp_path, approval_mode="never", access="read-only")
    wait_for(live, task(live, project, "hi")["id"], settled())
    argv = claude_log("argv")[0]
    assert argv[argv.index("--permission-mode") + 1] == "dontAsk"
    assert "Edit" in argv[argv.index("--disallowedTools"):]


def test_a_projects_own_configuration_loads_only_when_it_opts_in(live, tmp_path, claude_log):
    project = claude_project(live, tmp_path)
    first = task(live, project, "hi")
    wait_for(live, first["id"], settled())
    response = live.patch(f"/api/projects/{project['id']}", json={"project_config": True})
    assert response.json()["project_config"] is True
    wait_for(live, task(live, project, "hi again")["id"], settled())
    default, opted_in = claude_log("argv")
    assert default[default.index("--setting-sources") + 1] == "user" and "--strict-mcp-config" not in default
    assert opted_in[opted_in.index("--setting-sources") + 1] == "user,project,local"
    assert "--strict-mcp-config" not in opted_in


def test_an_agent_without_project_configuration_refuses_the_opt_in(client, project):
    response = client.patch(f"/api/projects/{project['id']}", json={"project_config": True})
    assert response.status_code == 400 and "no project configuration" in response.json()["detail"]
