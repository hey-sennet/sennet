"""Usage limits and tokens: from each adapter, through the task manager, to what is said and shown."""
import asyncio
import datetime
import time

from conftest import wait_for
from test_claude import INIT, claude_project, result, session, task, translate
from test_hub import Tasks, agent, completed, hub_with_request, user

from sennet.agents.base import check_event
from sennet.agents.claude import ClaudeAgent, usage_windows
from sennet.agents.codex import CodexSession, limit_windows
from sennet.usage import Limits, Warnings, limit_reached, limit_warning, when_resets

RESETS_AT = 1792875600
LIMIT_REPLY = {"type": "assistant", "parent_tool_use_id": None, "error": "rate_limit", "is_api_error_message": True,
               "api_error_params": {"rate_limit_info": {"status": "rejected", "resetsAt": RESETS_AT,
                                                        "rateLimitType": "five_hour"}},
               "message": {"id": "x", "content": [{"type": "text", "text": "You've hit your session limit · resets 4pm"}]}}


def at(hour: int, minute: int = 0, days: int = 0) -> float:
    today = datetime.datetime(2026, 10, 24, 9, 0)
    return (today.replace(hour=hour, minute=minute) + datetime.timedelta(days=days)).timestamp()


NOW = at(9)


# Claude


def test_claudes_limit_events_are_the_contracts_windows():
    events = translate(session(), {"type": "rate_limit_event", "rate_limit_info": {
        "status": "allowed", "unifiedWindows": {"five_hour": {"utilization": 0.47, "resetsAt": RESETS_AT},
                                                "seven_day": {"utilization": 0.37, "resetsAt": RESETS_AT + 1}}}})
    assert [e.data for e in events] == [{"windows": [
        {"id": "session", "title": "5-hour", "used": 47, "resets_at": RESETS_AT},
        {"id": "weekly", "title": "weekly", "used": 37, "resets_at": RESETS_AT + 1}]}]


def test_a_rejected_window_is_full():
    events = translate(session(), {"type": "rate_limit_event", "rate_limit_info": {
        "status": "rejected", "rateLimitType": "five_hour", "resetsAt": RESETS_AT,
        "unifiedWindows": {"five_hour": {"utilization": 0.99, "resetsAt": RESETS_AT, "status": "rejected"}}}})
    assert events[0].data["windows"][0]["used"] == 100


def test_a_turn_reports_its_tokens_with_cache_reads_and_writes_as_input():
    s = session()
    s.unstarted = 1
    events = translate(s, INIT, result(usage={"input_tokens": 10, "cache_read_input_tokens": 900,
                                              "cache_creation_input_tokens": 90, "output_tokens": 25}))
    assert [(e.kind, e.data) for e in events] == [
        ("usage", {"input": 1000, "cached": 900, "output": 25}), ("turn_completed", {"status": "completed", "error": None})]


def test_claudes_reply_at_the_limit_isnt_a_message_and_the_turn_says_which_limit():
    s = session()
    s.unstarted = 1
    events = translate(s, INIT, LIMIT_REPLY, {**result(), "is_error": True, "result": "You've hit your session limit · resets 4pm",
                                              "usage": {"input_tokens": 0, "output_tokens": 0}})
    assert [e.kind for e in events] == ["turn_completed"]
    assert events[0].data == {"status": "failed", "error": "You've hit your session limit · resets 4pm",
                              "limit": {"window": "5-hour", "resets_at": RESETS_AT}}
    # The next turn isn't held to it.
    s.unstarted = 1
    assert "limit" not in translate(s, INIT, result())[-1].data


def test_other_failed_requests_are_said_once_as_the_turns_error():
    s = session()
    s.unstarted = 1
    events = translate(s, INIT, {**LIMIT_REPLY, "error": "server_error", "api_error_params": None},
                       {**result(), "is_error": True, "result": "API Error: 500"})
    assert [(e.kind, e.data) for e in events] == [("turn_completed", {"status": "failed", "error": "API Error: 500"})]


def test_claudes_usage_answer_has_percentages_and_iso_reset_times():
    windows = usage_windows({"rate_limits_available": True, "rate_limits": {
        "five_hour": {"utilization": 70, "resets_at": "2026-10-24T16:00:00.145034+00:00"},
        "seven_day": {"utilization": 69.4, "resets_at": "2026-10-25T09:00:00+00:00"},
        "seven_day_opus": None, "iguana_necktie": None}})
    assert [(w.id, w.used) for w in windows] == [("session", 70), ("weekly", 69)]
    assert windows[0].resets_at == datetime.datetime(2026, 10, 24, 16, 0, 0, 145034, datetime.UTC).timestamp()
    assert usage_windows({"rate_limits_available": False, "rate_limits": None}) is None


def test_claude_is_asked_for_its_limits_without_a_turn(settings, claude_log):
    windows = asyncio.run(ClaudeAgent(settings.agents.commands["claude"]).limits())
    assert [(w.id, w.title, w.used) for w in windows] == [("session", "5-hour", 40), ("weekly", "weekly", 20)]
    assert claude_log("argv") == []  # no session was started


# Codex


def test_codex_windows_are_named_by_their_length():
    windows = limit_windows({"limitId": "codex", "primary": {"usedPercent": 30, "windowDurationMins": 300, "resetsAt": 5},
                             "secondary": {"usedPercent": 10, "windowDurationMins": 10080, "resetsAt": None}})
    assert [vars(w) for w in windows] == [{"id": "session", "title": "5-hour", "used": 30, "resets_at": 5.0},
                                          {"id": "weekly", "title": "weekly", "used": 10, "resets_at": None}]
    # A rolling update may leave one out; a separate limit keeps its own ids.
    other = limit_windows({"limitId": "luna", "limitName": "Luna", "primary": None,
                           "secondary": {"usedPercent": 101, "windowDurationMins": 10080}})
    assert [vars(w) for w in other] == [{"id": "luna:weekly", "title": "Luna weekly", "used": 100, "resets_at": None}]


def test_codex_sums_a_turns_responses_and_reports_limits():
    events = []

    async def run():
        s = CodexSession(lambda e: _append(events, e), None)
        s.resume_token = "t"
        last = {"inputTokens": 1000, "cachedInputTokens": 800, "outputTokens": 20}
        for _ in range(2):
            await s._notification("thread/tokenUsage/updated", {"threadId": "t", "tokenUsage": {"last": last, "total": {}}})
        await s._notification("account/rateLimits/updated", {"rateLimits": {
            "primary": {"usedPercent": 30, "windowDurationMins": 300, "resetsAt": 5}}})
        await s._notification("turn/completed", {"threadId": "t", "turn": {"status": "completed", "error": None}})

    asyncio.run(run())
    assert [p for e in events for p in check_event(e)] == []
    assert [(e.kind, e.data) for e in events] == [
        ("limits", {"windows": [{"id": "session", "title": "5-hour", "used": 30, "resets_at": 5.0}]}),
        ("usage", {"input": 2000, "cached": 1600, "output": 40}),
        ("turn_completed", {"status": "completed", "error": None})]


async def _append(events, event):
    events.append(event)


# Through the task manager


def test_tokens_add_up_on_the_task(client, project):
    created = client.post("/api/tasks", json={"project_id": project["id"], "text": "one"}).json()
    wait_for(client, created["id"], lambda t: t["status"] == "completed")
    client.post(f"/api/tasks/{created['id']}/messages", json={"text": "two"})
    done = wait_for(client, created["id"], lambda t: t["status"] == "completed" and len(t["messages"]) == 4)
    assert (done["input_tokens"], done["cached_tokens"], done["output_tokens"]) == (4000, 3200, 80)
    assert "usage" not in [e["kind"] for e in done["events"]]


def test_codex_at_its_limit_says_which_one_and_when_in_sennets_words(client, project):
    created = client.post("/api/tasks", json={"project_id": project["id"], "text": "limit"}).json()
    done = wait_for(client, created["id"], lambda t: t["status"] == "failed")
    assert done["error"] == f"Codex's 5-hour limit is reached. It resets {when_resets(1792870493)}."
    turn = next(e for e in done["events"] if e["kind"] == "turn_completed")
    assert turn["data"]["limit"] == {"window": "5-hour", "resets_at": 1792870493}
    assert client.app.state.tasks.limits.reached("codex")["id"] == "session"


def test_claude_at_its_limit_says_which_one_and_when(live, tmp_path):
    project = claude_project(live, tmp_path)
    done = wait_for(live, task(live, project, "limit")["id"], lambda t: t["status"] == "failed")
    assert done["error"] == f"Claude Code's 5-hour limit is reached. It resets {when_resets(1792875600)}."
    assert [m["role"] for m in done["messages"]] == ["user"]  # Claude's own words for it aren't a reply


def test_the_page_gets_the_limits_and_they_are_asked_for_when_stale(client):
    assert client.get("/api/state").json()["limits"] == {}  # nothing known yet; asking starts
    deadline = time.monotonic() + 10
    while set(known := client.get("/api/limits").json()) != {"codex", "claude"}:
        assert time.monotonic() < deadline, "the agents were never asked"
        time.sleep(0.02)
    assert [w["used"] for w in known["codex"]["windows"]] == [30, 10]
    assert [w["id"] for w in known["claude"]["windows"]] == ["session", "weekly"]
    assert not client.app.state.tasks.limits.asking  # fresh now: asking again waits a few minutes


# What is said


def test_when_a_limit_resets_in_words():
    assert when_resets(at(16), NOW) == "at 4 pm"
    assert when_resets(at(16, 26), NOW) == "at 4:26 pm"
    assert when_resets(at(9, 30, days=1), NOW) == "tomorrow at 9:30 am"
    assert when_resets(at(9, days=3), NOW) == f"on {datetime.date(2026, 10, 27).strftime('%A')} at 9 am"
    assert when_resets(at(9, days=10), NOW) == "on 3 November"
    assert limit_reached("Codex", None, None) == "Codex's usage limit is reached."


def test_each_warning_level_is_said_once_until_the_window_resets():
    warnings = Warnings()
    window = {"id": "session", "title": "5-hour", "used": 82, "resets_at": None}
    assert warnings.due("claude", [window]) == [window]
    assert warnings.due("claude", [{**window, "used": 90}]) == []
    assert warnings.due("claude", [{**window, "used": 96}]) != []
    assert warnings.due("claude", [{**window, "used": 100}]) == []  # full is said as the limit, not a warning
    assert warnings.due("claude", [{**window, "used": 5}]) == []
    assert warnings.due("claude", [window]) == [window]
    assert limit_warning("Claude Code", window) == "You've used 82 percent of Claude Code's 5-hour limit."


def test_a_turn_stopped_at_its_limit_is_said_once_in_sennets_words():
    hub, session, _ = hub_with_request(message_id=2)
    text = "Claude Code's 5-hour limit is reached. It resets at 4 pm."
    stopped = completed(7, "failed", text)
    stopped["event"]["data"]["limit"] = {"window": "5-hour", "resets_at": None}
    for event in [user(7, 2, "sent"), stopped, user(7, 2, "done")]:
        hub._observe(event)
    assert session.spoken == [(text, 7)]


def test_a_limit_getting_close_is_mentioned_after_the_reply_once():
    tasks = Tasks([{"id": "session", "title": "5-hour", "used": 85, "resets_at": None}])
    hub, session, _ = hub_with_request(message_id=2, tasks=tasks)
    for event in [user(7, 2, "sent"), agent(7, "Done."), completed(7), user(7, 2, "done")]:
        hub._observe(event)
    hub.requests[3] = session
    for event in [user(7, 3, "sent"), agent(7, "Done again."), completed(7), user(7, 3, "done")]:
        hub._observe(event)
    assert [text for text, _ in session.spoken] == [
        "Done.", "You've used 85 percent of Claude Code's 5-hour limit.", "Done again."]


def test_limits_report_merges_and_asking_replaces():
    published = []

    async def publish(event):
        published.append(event)

    async def run():
        limits = Limits({}, publish)
        await limits.report("codex", [{"id": "session", "title": "5-hour", "used": 30, "resets_at": 1}])
        await limits.report("codex", [{"id": "weekly", "title": "weekly", "used": 10, "resets_at": 2}])
        assert [w["id"] for w in limits.windows("codex")] == ["session", "weekly"]
        await limits.report("codex", [{"id": "weekly", "title": "weekly", "used": 11, "resets_at": 2}], complete=True)
        assert [w["id"] for w in limits.windows("codex")] == ["weekly"]

    asyncio.run(run())
    assert published[-1]["type"] == "limits" and published[-1]["agent"] == "codex"
