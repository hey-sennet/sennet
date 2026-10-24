import time

from sennet.config import Settings
from sennet.db import Database
from conftest import pending_approval, wait_for, paired


def done(status="completed"):
    return lambda task: task["status"] == status


def test_task_runs_and_stores_the_reply(client, project, codex_log):
    task = client.post("/api/tasks", json={"project_id": project["id"], "text": "hello there"}).json()
    task = wait_for(client, task["id"], done())
    assert [(m["role"], m["text"]) for m in task["messages"]] == [("user", "hello there"), ("agent", "You said: hello there")]
    assert task["resume_token"] == "thread-1"
    assert codex_log("thread/start")[0]["params"]["cwd"] == project["path"]
    assert codex_log("thread/start")[0]["cwd"] == project["path"]


def test_policy_maps_to_codex_modes(client, project, codex_log):
    client.patch(f"/api/projects/{project['id']}", json={"approval_mode": "auto"})
    task = client.post("/api/tasks", json={"project_id": project["id"], "text": "hi"}).json()
    wait_for(client, task["id"], done())
    params = codex_log("thread/start")[0]["params"]
    assert (params["approvalPolicy"], params["sandbox"]) == ("on-request", "workspace-write")


def test_follow_up_reuses_the_warm_session(client, project, codex_log):
    task = client.post("/api/tasks", json={"project_id": project["id"], "text": "one"}).json()
    wait_for(client, task["id"], done())
    client.post(f"/api/tasks/{task['id']}/messages", json={"text": "two"})
    task = wait_for(client, task["id"], lambda t: done()(t) and len(t["messages"]) == 4)
    assert task["messages"][-1]["text"] == "You said: two"
    assert len(codex_log("initialize")) == 1


def test_follow_up_resumes_the_thread_after_idle_close(settings, paths, codex_log, tmp_path):
    from fastapi.testclient import TestClient

    from sennet.server import create_app
    from conftest import HEADERS

    settings.tasks.idle_timeout_s = 0
    (tmp_path / "repo").mkdir()
    with paired(TestClient(create_app(settings, paths), headers=HEADERS)) as client:
        project = client.post("/api/projects", json={"path": str(tmp_path / "repo")}).json()
        task = client.post("/api/tasks", json={"project_id": project["id"], "text": "one"}).json()
        wait_for(client, task["id"], done())
        client.post(f"/api/tasks/{task['id']}/messages", json={"text": "two"})
        wait_for(client, task["id"], lambda t: done()(t) and len(t["messages"]) == 4)
    assert len(codex_log("initialize")) == 2
    assert codex_log("thread/resume")[0]["params"]["threadId"] == "thread-1"


def until(predicate, timeout: float = 10):
    deadline = time.monotonic() + timeout
    while not predicate():
        assert time.monotonic() < deadline, "timed out"
        time.sleep(0.02)


def test_a_new_task_takes_over_the_spare_started_early(client, project, codex_log):
    assert client.post("/api/prewarm", json={"project_id": project["id"]}).status_code == 204
    until(lambda: codex_log("thread/start"))
    task = client.post("/api/tasks", json={"project_id": project["id"], "text": "hello"}).json()
    task = wait_for(client, task["id"], done())
    assert task["messages"][-1]["text"] == "You said: hello"
    assert len(codex_log("initialize")) == len(codex_log("thread/start")) == 1
    assert task["resume_token"] == "thread-1"


def test_prewarming_twice_starts_one_spare(client, project, codex_log):
    client.post("/api/prewarm", json={"project_id": project["id"]})
    client.post("/api/prewarm", json={"project_id": project["id"]})
    until(lambda: codex_log("thread/start"))
    time.sleep(0.2)
    assert len(codex_log("initialize")) == 1


def test_a_spare_for_another_model_is_not_taken(client, project, codex_log):
    client.post("/api/prewarm", json={"project_id": project["id"]})
    until(lambda: codex_log("thread/start"))
    task = client.post("/api/tasks", json={"project_id": project["id"], "text": "hi", "model": "gpt-deep"})
    assert task.status_code == 201, task.text
    wait_for(client, task.json()["id"], done())
    assert len(codex_log("thread/start")) == 2


def test_a_follow_up_resumes_early_after_idle_close(settings, paths, codex_log, tmp_path):
    from fastapi.testclient import TestClient

    from sennet.server import create_app
    from conftest import HEADERS

    settings.tasks.idle_timeout_s = 0
    (tmp_path / "repo").mkdir()
    with paired(TestClient(create_app(settings, paths), headers=HEADERS)) as client:
        project = client.post("/api/projects", json={"path": str(tmp_path / "repo")}).json()
        task = client.post("/api/tasks", json={"project_id": project["id"], "text": "one"}).json()
        wait_for(client, task["id"], done())
        until(lambda: client.app.state.tasks.runners[task["id"]].session is None)  # closed when idle
        client.post("/api/prewarm", json={"task_id": task["id"]})
        until(lambda: codex_log("thread/resume"))
        client.post(f"/api/tasks/{task['id']}/messages", json={"text": "two"})
        wait_for(client, task["id"], lambda t: done()(t) and len(t["messages"]) == 4)
    assert len(codex_log("thread/resume")) == 1


def test_command_approval_approve(client, project):
    task = client.post("/api/tasks", json={"project_id": project["id"], "text": "run:npm test"}).json()
    approval = pending_approval(client)
    assert approval["summary"] == "run npm test"
    assert approval["risk"] == "execute"
    assert client.get(f"/api/tasks/{task['id']}").json()["status"] == "awaiting_approval"
    client.post(f"/api/approvals/{approval['id']}", json={"decision": "approve"})
    task = wait_for(client, task["id"], done())
    assert task["messages"][-1]["text"] == "Ran it."
    finished = [e for e in task["events"] if e["kind"] == "command_finished"]
    assert finished[0]["data"]["exit_code"] == 0


def test_command_approval_decline(client, project):
    task = client.post("/api/tasks", json={"project_id": project["id"], "text": "run:make deploy"}).json()
    client.post(f"/api/approvals/{pending_approval(client)['id']}", json={"decision": "decline"})
    task = wait_for(client, task["id"], done())
    assert task["messages"][-1]["text"] == "Okay, I won't run it."


def test_approve_always_remembers_the_command_prefix(client, project):
    first = client.post("/api/tasks", json={"project_id": project["id"], "text": "run:npm test"}).json()
    response = client.post(f"/api/approvals/{pending_approval(client)['id']}", json={"decision": "approve_always"})
    assert response.status_code == 200
    wait_for(client, first["id"], done())
    assert [(r["kind"], r["pattern"]) for r in client.get("/api/state").json()["rules"]] == [("command", "npm test")]

    second = client.post("/api/tasks", json={"project_id": project["id"], "text": "run:npm test -- --watch=false"}).json()
    second = wait_for(client, second["id"], done())
    assert second["messages"][-1]["text"] == "Ran it."


def test_destructive_commands_cannot_be_approved_always(client, project):
    task = client.post("/api/tasks", json={"project_id": project["id"], "text": "run:git push origin main"}).json()
    approval = pending_approval(client)
    assert approval["risk"] == "destructive"
    response = client.post(f"/api/approvals/{approval['id']}", json={"decision": "approve_always"})
    assert response.status_code == 400
    client.post(f"/api/approvals/{approval['id']}", json={"decision": "approve"})
    wait_for(client, task["id"], done())
    assert client.get("/api/state").json()["rules"] == []


def test_edit_approval_and_rule(client, project):
    task = client.post("/api/tasks", json={"project_id": project["id"], "text": "edit:src/app.py"}).json()
    approval = pending_approval(client)
    assert (approval["kind"], approval["summary"], approval["risk"]) == ("edit", "edit app.py", "edit")
    assert approval["detail"]["changes"][0]["diff"] == "-a\n+b\n"
    client.post(f"/api/approvals/{approval['id']}", json={"decision": "approve_always"})
    wait_for(client, task["id"], done())
    second = client.post("/api/tasks", json={"project_id": project["id"], "text": "edit:src/other.py"}).json()
    assert wait_for(client, second["id"], done())["messages"][-1]["text"] == "Edited."


def test_cancel_interrupts_the_turn(client, project):
    task = client.post("/api/tasks", json={"project_id": project["id"], "text": "hang"}).json()
    wait_for(client, task["id"], done("running"))
    client.post(f"/api/tasks/{task['id']}/cancel")
    wait_for(client, task["id"], done("cancelled"))


def test_cancel_while_awaiting_approval(client, project):
    task = client.post("/api/tasks", json={"project_id": project["id"], "text": "run:rm -rf build"}).json()
    approval = pending_approval(client)
    client.post(f"/api/tasks/{task['id']}/cancel")
    wait_for(client, task["id"], done("cancelled"))
    assert client.get("/api/state").json()["approvals"] == []
    assert client.post(f"/api/approvals/{approval['id']}", json={"decision": "approve"}).status_code == 404


def test_agent_crash_fails_the_task(client, project):
    task = client.post("/api/tasks", json={"project_id": project["id"], "text": "crash"}).json()
    task = wait_for(client, task["id"], done("failed"))
    assert task["error"] == "agent process exited"


def test_one_running_task_per_project(client, project, tmp_path):
    first = client.post("/api/tasks", json={"project_id": project["id"], "text": "hang"}).json()
    wait_for(client, first["id"], done("running"))
    second = client.post("/api/tasks", json={"project_id": project["id"], "text": "hi"}).json()
    assert client.get(f"/api/tasks/{second['id']}").json()["status"] == "queued"

    (tmp_path / "other").mkdir()
    other = client.post("/api/projects", json={"path": str(tmp_path / "other")}).json()
    third = client.post("/api/tasks", json={"project_id": other["id"], "text": "hi"}).json()
    wait_for(client, third["id"], done())

    client.post(f"/api/tasks/{first['id']}/cancel")
    wait_for(client, second["id"], done())


def test_global_concurrency_limit(settings, paths, tmp_path, codex_log):
    from fastapi.testclient import TestClient

    from sennet.server import create_app
    from conftest import HEADERS

    settings.tasks.max_concurrent = 1
    with paired(TestClient(create_app(settings, paths), headers=HEADERS)) as client:
        ids = []
        for name in ("a", "b"):
            (tmp_path / name).mkdir()
            ids.append(client.post("/api/projects", json={"path": str(tmp_path / name)}).json()["id"])
        first = client.post("/api/tasks", json={"project_id": ids[0], "text": "hang"}).json()
        wait_for(client, first["id"], done("running"))
        second = client.post("/api/tasks", json={"project_id": ids[1], "text": "hi"}).json()
        assert client.get(f"/api/tasks/{second['id']}").json()["status"] == "queued"
        client.post(f"/api/tasks/{first['id']}/cancel")
        wait_for(client, second["id"], done())


def test_restart_marks_in_flight_work_unknown(paths):
    db = Database(paths.database)
    project = db.add_project("p", "/tmp", "codex")
    running = db.add_task(project["id"], "codex", "x")
    db.update_task(running, status="running")
    db.add_approval(running, "command", "execute", "run x", {})
    db.recover()
    assert db.task(running)["status"] == "unknown"
    assert db.approvals("pending") == []


def test_events_stream_over_websocket(client, project):
    with client.websocket_connect("/api/events") as socket:
        client.post("/api/tasks", json={"project_id": project["id"], "text": "hi"})
        kinds = []
        while "completed" not in kinds:
            event = socket.receive_json()
            kinds.append(event["type"] if event["type"] != "task" else event["task"]["status"])
        assert "delta" in kinds and "message" in kinds


def test_state_changes_need_the_header(client, project):
    from fastapi.testclient import TestClient

    response = TestClient(client.app).post("/api/tasks", json={"project_id": project["id"], "text": "hi"})
    assert response.status_code == 403


def test_cross_origin_websocket_is_refused(client):
    import pytest
    from starlette.websockets import WebSocketDisconnect

    with pytest.raises(WebSocketDisconnect):
        with client.websocket_connect("/api/events", headers={"origin": "http://evil.example"}) as socket:
            socket.receive_json()


def test_settings_round_trip(paths):
    settings = Settings()
    settings.approvals.mode = "never"
    settings.save(paths.config_file)
    assert Settings.load(paths.config_file).approvals.mode == "never"


def test_shutdown_does_not_start_queued_tasks(settings, paths, codex_log, tmp_path):
    from fastapi.testclient import TestClient

    from sennet.db import Database
    from sennet.server import create_app
    from conftest import HEADERS

    (tmp_path / "repo").mkdir()
    with paired(TestClient(create_app(settings, paths), headers=HEADERS)) as client:
        project = client.post("/api/projects", json={"path": str(tmp_path / "repo")}).json()
        running = client.post("/api/tasks", json={"project_id": project["id"], "text": "hang"}).json()
        wait_for(client, running["id"], lambda t: t["messages"][0]["state"] == "sent")
        queued = client.post("/api/tasks", json={"project_id": project["id"], "text": "next"}).json()
    db = Database(paths.database)
    assert db.task(queued["id"])["status"] == "queued"  # still waiting, not started as the daemon stopped
    db.close()
