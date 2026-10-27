import subprocess
import time
from pathlib import Path

import pytest

from conftest import wait_for


def done(task):
    return task["status"] == "completed"


def git(cwd, *args) -> str:
    return subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True, text=True).stdout


@pytest.fixture(autouse=True)
def git_identity(tmp_path, monkeypatch):
    """Git as on a fresh machine, apart from who commits: none of this user's own config."""
    config = tmp_path / "gitconfig"
    config.write_text("[user]\n\tname = Test\n\temail = test@example.com\n")
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", str(config))
    monkeypatch.setenv("GIT_CONFIG_NOSYSTEM", "1")


@pytest.fixture
def repo(client, tmp_path):
    directory = tmp_path / "app"
    directory.mkdir()
    git(directory, "init", "--quiet", "-b", "main")
    (directory / "a.txt").write_text("one\n")
    git(directory, "add", ".")
    git(directory, "commit", "--quiet", "-m", "first")
    project = client.post("/api/projects", json={"path": str(directory), "name": "app"}).json()
    assert project["repo"] is True and project["worktrees"] is False
    response = client.patch(f"/api/projects/{project['id']}", json={"worktrees": True})
    assert response.status_code == 200, response.text
    assert response.json()["worktrees"] is True
    return directory, project


def new_task(client, project, text="fix the login page") -> dict:
    response = client.post("/api/tasks", json={"project_id": project["id"], "text": text})
    assert response.status_code == 201, response.text
    return wait_for(client, response.json()["id"], done)


def test_only_a_repository_can_have_worktrees(client, project):
    assert project["repo"] is False
    response = client.patch(f"/api/projects/{project['id']}", json={"worktrees": True})
    assert response.status_code == 400
    assert "git repository" in response.json()["detail"]


def test_each_task_runs_in_its_own_worktree(client, repo, codex_log):
    directory, project = repo
    first = new_task(client, project)
    second = new_task(client, project, "and the signup page")
    assert first["branch"] == f"sennet/{first['id']}-fix-the-login-page"
    assert first["base"] == "main"
    assert first["worktree"] != second["worktree"]
    assert [call["cwd"] for call in codex_log("thread/start")] == [first["worktree"], second["worktree"]]
    assert (Path(first["worktree"]) / "a.txt").read_text() == "one\n"
    assert git(directory, "branch", "--show-current").strip() == "main"


def test_no_spare_agent_for_a_project_with_worktrees(client, repo, codex_log):
    _, project = repo
    assert client.post("/api/prewarm", json={"project_id": project["id"]}).status_code == 204
    time.sleep(0.3)
    assert codex_log("thread/start") == []


def test_merge_commits_what_was_left_and_fast_forwards(client, repo):
    directory, project = repo
    task = new_task(client, project)
    (Path(task["worktree"]) / "b.txt").write_text("new\n")
    status = client.get(f"/api/tasks/{task['id']}/worktree").json()
    assert (status["files"], status["uncommitted"], status["commits"]) == (["b.txt"], 1, 0)
    merged = client.post(f"/api/tasks/{task['id']}/worktree/merge").json()
    assert (merged["merged"], merged["committed"], merged["fast_forward"], merged["commits"]) == (True, True, True, 1)
    assert (directory / "b.txt").read_text() == "new\n"
    assert git(directory, "log", "-1", "--format=%s").strip() == task["title"]
    assert merged["status"]["files"] == [] and merged["status"]["commits"] == 0


def test_a_merge_that_would_conflict_is_not_started(client, repo):
    directory, project = repo
    task = new_task(client, project)
    (Path(task["worktree"]) / "a.txt").write_text("theirs\n")
    (directory / "a.txt").write_text("ours\n")
    git(directory, "commit", "--quiet", "-am", "meanwhile")
    merged = client.post(f"/api/tasks/{task['id']}/worktree/merge").json()
    assert (merged["merged"], merged["conflicts"]) == (False, ["a.txt"])
    assert git(directory, "status", "--porcelain") == ""
    assert (directory / "a.txt").read_text() == "ours\n"


def test_merge_needs_the_project_on_its_base(client, repo):
    directory, project = repo
    task = new_task(client, project)
    git(directory, "switch", "--quiet", "-c", "other")
    response = client.post(f"/api/tasks/{task['id']}/worktree/merge")
    assert response.status_code == 409
    assert "check out main" in response.json()["detail"]


def test_a_removed_worktree_comes_back_for_a_follow_up(client, repo):
    _, project = repo
    task = new_task(client, project)
    response = client.delete(f"/api/tasks/{task['id']}/worktree")
    assert response.status_code == 200, response.text
    assert response.json()["exists"] is False and response.json()["branch"] == task["branch"]
    assert not Path(task["worktree"]).exists()
    client.post(f"/api/tasks/{task['id']}/messages", json={"text": "one more thing"})
    wait_for(client, task["id"], lambda t: done(t) and len(t["messages"]) == 4)
    assert Path(task["worktree"]).is_dir()
    assert git(task["worktree"], "branch", "--show-current").strip() == task["branch"]


def test_with_its_branch_deleted_a_task_cant_go_on(client, repo):
    directory, project = repo
    task = new_task(client, project)
    assert client.delete(f"/api/tasks/{task['id']}/worktree?branch=delete").json()["branch"] is None
    assert task["branch"] not in git(directory, "branch")
    client.post(f"/api/tasks/{task['id']}/messages", json={"text": "one more thing"})
    task = wait_for(client, task["id"], lambda t: t["status"] == "failed")
    assert "start a new task" in task["error"]


def test_deleting_a_task_can_take_its_worktree_and_branch(client, repo):
    directory, project = repo
    kept, removed = new_task(client, project), new_task(client, project, "another")
    assert client.delete(f"/api/tasks/{kept['id']}").status_code == 204
    assert client.delete(f"/api/tasks/{removed['id']}?worktree=delete").status_code == 204
    assert Path(kept["worktree"]).is_dir() and kept["branch"] in git(directory, "branch")
    assert not Path(removed["worktree"]).exists() and removed["branch"] not in git(directory, "branch")


def test_a_database_from_before_worktrees_gets_them(tmp_path):
    import sqlite3

    from sennet.db import SCHEMA_VERSION, Database

    path = tmp_path / "sennet.db"
    Database(path).close()
    with sqlite3.connect(path) as conn:  # as 0.2.0 left it: version 12, none of the columns
        conn.execute("ALTER TABLE projects DROP COLUMN worktrees")
        for column in ("worktree", "branch", "base"):
            conn.execute(f"ALTER TABLE tasks DROP COLUMN {column}")
        conn.execute("INSERT INTO projects (name, path, created_at) VALUES ('old', '/nowhere', 0)")
        conn.execute("PRAGMA user_version = 12")
    db = Database(path)
    assert db.project(1)["worktrees"] is False
    db.add_task(1, "codex", "hi")
    assert {"worktree", "branch", "base"} <= set(db.task(1))
    assert db.one("PRAGMA user_version")["user_version"] == SCHEMA_VERSION


def test_a_relative_data_directory_still_puts_worktrees_there(settings, tmp_path, monkeypatch, codex_log):
    from fastapi.testclient import TestClient

    from conftest import HEADERS, paired
    from sennet.config import Paths
    from sennet.server import create_app

    monkeypatch.chdir(tmp_path)
    directory = tmp_path / "app"
    directory.mkdir()
    git(directory, "init", "--quiet", "-b", "main")
    git(directory, "commit", "--quiet", "--allow-empty", "-m", "first")
    with paired(TestClient(create_app(settings, Paths(Path("config"), Path("data"))), headers=HEADERS)) as client:
        project = client.post("/api/projects", json={"path": str(directory)}).json()
        client.patch(f"/api/projects/{project['id']}", json={"worktrees": True})
        task = new_task(client, project)
    assert task["worktree"] == str(tmp_path / "data" / "worktrees" / str(task["id"]))
    assert codex_log("thread/start")[0]["cwd"] == task["worktree"]
