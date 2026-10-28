import time
from pathlib import Path

import pytest

from conftest import git, git_repo, wait_for

pytestmark = pytest.mark.usefixtures("git_identity")


def run(client, project, *texts) -> dict:
    """A task with these messages, each run to its end."""
    task = client.post("/api/tasks", json={"project_id": project["id"], "text": texts[0]}).json()
    for i, text in enumerate(texts):
        if i:
            client.post(f"/api/tasks/{task['id']}/messages", json={"text": text})
        task = wait_for(client, task["id"], lambda t, n=2 * (i + 1): t["status"] == "completed" and len(t["messages"]) == n)
    return task


def changes(client, task) -> dict:
    response = client.get(f"/api/tasks/{task['id']}/changes")
    assert response.status_code == 200, response.text
    return response.json()


def diff(client, task, path) -> str:
    response = client.get(f"/api/tasks/{task['id']}/changes/diff", params={"path": path})
    assert response.status_code == 200, response.text
    return response.json()["diff"]


@pytest.fixture
def repo(client, tmp_path):
    directory = git_repo(tmp_path / "app")
    return directory, client.post("/api/projects", json={"path": str(directory)}).json()


def test_a_file_edited_twice_is_one_net_diff(client, repo):
    directory, project = repo
    task = run(client, project, "write:a.txt=two", "write:a.txt=three")
    assert task["start_commit"] == git(directory, "rev-parse", "HEAD").strip()
    found = changes(client, task)
    assert (found["source"], found["net"], found["branch"], found["state"]) == ("git", True, "main", "uncommitted")
    assert found["files"] == [{"path": "a.txt", "kind": "update", "added": 1, "removed": 1, "binary": False,
                               "committed": False}]
    assert found["estimated"] is False
    text = diff(client, task, "a.txt")
    assert "-one" in text and "+three" in text and "two" not in text


def test_only_the_tasks_own_files_are_its_changes(client, repo):
    directory, project = repo
    task = run(client, project, "write:new.txt=hello")
    (directory / "a.txt").write_text("someone else\n")  # another task's, or yours
    found = changes(client, task)
    assert [(f["path"], f["kind"], f["added"]) for f in found["files"]] == [("new.txt", "add", 1)]
    assert found["elsewhere"] == ["a.txt"]
    assert "+hello" in diff(client, task, "new.txt")
    assert client.get(f"/api/tasks/{task['id']}/changes/diff", params={"path": "a.txt"}).status_code == 404


def test_committed_then_pushed(client, repo, tmp_path):
    directory, project = repo
    task = run(client, project, "write:a.txt=two")
    git(directory, "commit", "--quiet", "-am", "two")
    assert changes(client, task)["state"] == "committed"
    git(tmp_path, "init", "--quiet", "--bare", "remote.git")
    git(directory, "remote", "add", "origin", str(tmp_path / "remote.git"))
    git(directory, "push", "--quiet", "-u", "origin", "main")
    found = changes(client, task)
    assert found["state"] == "pushed"
    assert found["files"][0]["path"] == "a.txt"  # still what it changed, though committed
    assert found["files"][0]["committed"] is True


def test_the_start_is_its_first_turns(client, repo):
    directory, project = repo
    task = run(client, project, "write:a.txt=two")
    started = task["start_commit"]
    git(directory, "commit", "--quiet", "-am", "two")
    task = run(client, project, "write:a.txt=two")  # another task, from the new commit
    assert task["start_commit"] != started


def test_in_a_worktree_everything_there_counts_and_stays_after_merging(client, repo):
    directory, project = repo
    client.patch(f"/api/projects/{project['id']}", json={"worktrees": True})
    task = run(client, project, "write:a.txt=two")
    (Path(task["worktree"]) / "formatted.txt").write_text("by a command\n")  # no edit event names it
    found = changes(client, task)
    assert {f["path"] for f in found["files"]} == {"a.txt", "formatted.txt"}
    assert (found["branch"], found["base"], found["elsewhere"]) == (task["branch"], "main", [])
    client.post(f"/api/tasks/{task['id']}/worktree/merge")
    found = changes(client, task)
    assert found["state"] == "merged"
    assert {f["path"] for f in found["files"]} == {"a.txt", "formatted.txt"}


def test_without_git_its_edits_are_listed_as_they_were_made(client, project):
    task = run(client, project, "write:notes.md=one", "write:notes.md=two")
    found = changes(client, task)
    assert (found["source"], found["net"], found["state"]) == ("events", False, None)
    assert found["files"] == [{"path": str(Path(project["path"]) / "notes.md"), "kind": "add", "added": 2,
                               "removed": 0, "binary": False}]
    assert diff(client, task, found["files"][0]["path"]) == "+one\n\n+two"


def test_a_task_from_before_start_commits_counts_from_when_it_was_made(client, repo):
    directory, project = repo
    task = run(client, project, "write:a.txt=two", "write:b.txt=new")
    db = client.app.state.db
    db.execute("UPDATE tasks SET start_commit = NULL, created_at = created_at + 1 WHERE id = ?", task["id"])
    git(directory, "add", "a.txt")
    found = changes(client, task)
    assert (found["source"], found["net"], found["estimated"]) == ("git", True, True)
    assert [(f["path"], f["committed"]) for f in found["files"]] == [("a.txt", False), ("b.txt", False)]
    time.sleep(2.1)  # git dates commits to the second: this one after the task was made
    git(directory, "commit", "--quiet", "-m", "a")
    assert [(f["path"], f["committed"]) for f in changes(client, task)["files"]] == [("a.txt", True), ("b.txt", False)]
