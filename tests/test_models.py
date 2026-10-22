"""Model and reasoning effort: listed per agent, set per project and per task, applied from the next turn."""
from conftest import pending_approval, wait_for


def done(n_messages):
    return lambda t: t["status"] == "completed" and len(t["messages"]) == n_messages


def test_agents_list_their_models(client):
    agents = client.get("/api/agents").json()
    assert [(m["id"], m["default"], m["efforts"]) for m in agents["codex"]["models"]] == [
        ("gpt-fast", True, ["low", "medium", "high"]), ("gpt-deep", False, ["medium", "high", "max"])]
    assert [m["id"] for m in agents["claude"]["models"]] == ["opus", "sonnet", "haiku", "fable"]


def test_a_new_conversation_leaves_the_model_to_the_agent(client, project, codex_log):
    task = client.post("/api/tasks", json={"project_id": project["id"], "text": "hi"}).json()
    wait_for(client, task["id"], done(2))
    assert "model" not in codex_log("thread/start")[0]["params"]
    assert {"model", "effort"}.isdisjoint(codex_log("turn/start")[0]["params"])


def test_the_projects_model_is_used_with_its_default_effort(client, project, codex_log):
    client.patch(f"/api/projects/{project['id']}", json={"model": "gpt-deep"})
    task = client.post("/api/tasks", json={"project_id": project["id"], "text": "hi"}).json()
    wait_for(client, task["id"], done(2))
    assert codex_log("thread/start")[0]["params"]["model"] == "gpt-deep"
    turn = codex_log("turn/start")[0]["params"]
    assert (turn["model"], turn["effort"]) == ("gpt-deep", "high")


def test_a_task_changes_model_between_turns_without_a_restart(client, project, codex_log):
    task = client.post("/api/tasks", json={"project_id": project["id"], "text": "one"}).json()
    wait_for(client, task["id"], done(2))
    changed = client.patch(f"/api/tasks/{task['id']}", json={"model": "gpt-deep", "effort": "max"}).json()
    assert (changed["model"], changed["effort"]) == ("gpt-deep", "max")
    client.post(f"/api/tasks/{task['id']}/messages", json={"text": "two"})
    wait_for(client, task["id"], done(4))
    client.patch(f"/api/tasks/{task['id']}", json={"model": None, "effort": None})
    client.post(f"/api/tasks/{task['id']}/messages", json={"text": "three"})
    wait_for(client, task["id"], done(6))
    turns = [(t["params"].get("model"), t["params"].get("effort")) for t in codex_log("turn/start")]
    # Back to the default names it: the thread would otherwise keep gpt-deep.
    assert turns == [(None, None), ("gpt-deep", "max"), ("gpt-fast", "low")]
    assert len([c for c in codex_log("initialize") if c["cwd"] == project["path"]]) == 1  # not the listing


def test_a_tasks_own_model_takes_its_default_effort_not_the_projects(client, project, codex_log):
    client.patch(f"/api/projects/{project['id']}", json={"model": "gpt-fast", "effort": "medium"})
    task = client.post("/api/tasks", json={"project_id": project["id"], "text": "one"}).json()
    wait_for(client, task["id"], done(2))
    client.patch(f"/api/tasks/{task['id']}", json={"model": "gpt-deep"})
    client.post(f"/api/tasks/{task['id']}/messages", json={"text": "two"})
    wait_for(client, task["id"], done(4))
    turns = [(t["params"].get("model"), t["params"].get("effort")) for t in codex_log("turn/start")]
    assert turns == [("gpt-fast", "medium"), ("gpt-deep", "high")]


def test_unknown_models_and_efforts_are_refused(client, project):
    task = client.post("/api/tasks", json={"project_id": project["id"], "text": "hi"}).json()
    response = client.patch(f"/api/tasks/{task['id']}", json={"model": "gpt-nope"})
    assert response.status_code == 400 and "no model 'gpt-nope'" in response.json()["detail"]
    response = client.patch(f"/api/tasks/{task['id']}", json={"model": "gpt-fast", "effort": "max"})
    assert response.status_code == 400 and "doesn't take 'max'" in response.json()["detail"]
    response = client.patch(f"/api/projects/{project['id']}", json={"effort": "max"})  # the default model's
    assert response.status_code == 400
    assert client.patch("/api/tasks/999", json={"model": None}).status_code == 404


def test_changing_a_projects_agent_or_model_resets_what_belonged_to_it(client, project):
    client.patch(f"/api/projects/{project['id']}", json={"model": "gpt-deep", "effort": "max"})
    updated = client.patch(f"/api/projects/{project['id']}", json={"model": "gpt-fast"}).json()
    assert (updated["model"], updated["effort"]) == ("gpt-fast", None)
    updated = client.patch(f"/api/projects/{project['id']}", json={"agent": "claude"}).json()
    assert (updated["model"], updated["effort"]) == (None, None)


def claude_task(live, tmp_path, text="one"):
    (tmp_path / "repo").mkdir(exist_ok=True)
    project = live.post("/api/projects", json={"path": str(tmp_path / "repo"), "agent": "claude"}).json()
    task = live.post("/api/tasks", json={"project_id": project["id"], "text": text}).json()
    return project, wait_for(live, task["id"], done(2))


def model_args(argv):
    return [a for i, a in enumerate(argv) if argv[i - 1] in ("--model", "--effort")]


def test_claude_restarts_on_the_same_conversation_for_another_model(live, tmp_path, claude_log):
    project, task = claude_task(live, tmp_path)
    live.patch(f"/api/tasks/{task['id']}", json={"model": "sonnet", "effort": "high"})
    live.post(f"/api/tasks/{task['id']}/messages", json={"text": "two"})
    wait_for(live, task["id"], done(4))
    live.post(f"/api/tasks/{task['id']}/messages", json={"text": "three"})  # same model: same process
    wait_for(live, task["id"], done(6))
    live.patch(f"/api/tasks/{task['id']}", json={"model": None, "effort": None})
    live.post(f"/api/tasks/{task['id']}/messages", json={"text": "four"})
    wait_for(live, task["id"], done(8))
    argvs = claude_log("argv")
    assert [model_args(a) for a in argvs] == [[], ["sonnet", "high"], ["default"]]
    assert all(a[a.index("--resume") + 1] == task["resume_token"] for a in argvs[1:])


def test_claude_waits_for_background_work_before_changing_model(live, tmp_path, claude_log, monkeypatch):
    monkeypatch.setenv("FAKE_CLAUDE_BG_S", "30")
    (tmp_path / "repo").mkdir()
    project = live.post("/api/projects", json={"path": str(tmp_path / "repo"), "agent": "claude"}).json()
    task = live.post("/api/tasks", json={"project_id": project["id"], "text": "bg:make build"}).json()
    live.post(f"/api/approvals/{pending_approval(live)['id']}", json={"decision": "approve"})
    wait_for(live, task["id"], lambda t: t["status"] == "background")
    live.patch(f"/api/tasks/{task['id']}", json={"model": "haiku"})
    live.post(f"/api/tasks/{task['id']}/messages", json={"text": "still going?"})
    wait_for(live, task["id"], lambda t: t["status"] == "background" and len(t["messages"]) == 4)
    assert len(claude_log("argv")) == 1  # the work goes on in the first process


def test_an_effort_alone_is_checked_against_the_model_in_use(client, project):
    client.patch(f"/api/projects/{project['id']}", json={"model": "gpt-deep"})
    assert client.patch(f"/api/projects/{project['id']}", json={"effort": "max"}).status_code == 200
    task = client.post("/api/tasks", json={"project_id": project["id"], "text": "hi"}).json()
    assert client.patch(f"/api/tasks/{task['id']}", json={"effort": "max"}).status_code == 200  # gpt-deep's
    assert client.patch(f"/api/tasks/{task['id']}", json={"effort": "low"}).status_code == 400
