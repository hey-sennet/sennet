import pytest

from sennet import projects
from sennet.agents.base import ApprovalRequest
from sennet.agents.codex import _read_only, unwrap
from sennet.approvals import classify, command_prefix, matches
from sennet.db import Database


@pytest.mark.parametrize("command, unwrapped", [
    ("/bin/bash -lc 'npm test'", "npm test"),
    ("bash -c 'ls -la | head'", "ls -la | head"),
    ("pytest -q", "pytest -q"),
])
def test_codex_unwraps_its_shell_wrapper(command, unwrapped):
    assert unwrap(command) == unwrapped


def test_codex_judges_read_only_commands_from_its_actions():
    assert _read_only([{"type": "read"}, {"type": "search"}]) is True
    assert _read_only([{"type": "read"}, {"type": "unknown"}]) is False
    assert _read_only([]) is None


@pytest.mark.parametrize("command, prefix", [
    ("npm test", "npm test"),
    ("npm test -- --watch", "npm test"),
    ("pytest tests/test_x.py", "pytest"),
    ("cargo build --release", "cargo build"),
    ("ls -la", "ls"),
    ("cat a | grep b", None),
    ("echo $(whoami)", None),
])
def test_command_prefix(command, prefix):
    assert command_prefix(command) == prefix


def test_rule_matching_is_by_whole_words():
    assert matches("npm test", "npm test -- -u")
    assert not matches("npm test", "npm testing")
    assert not matches("npm test", "npm test && rm -rf /")


@pytest.mark.parametrize("command, risk", [
    ("git push origin main", "destructive"),
    ("rm -rf build", "destructive"),
    ("npm publish", "destructive"),
    ("git status", "execute"),
])
def test_classify_commands(command, risk):
    assert classify(ApprovalRequest(kind="command", command=command)) == risk


def test_classify_reads_and_network():
    assert classify(ApprovalRequest(kind="command", command="cat a", read_only=True)) == "read"
    assert classify(ApprovalRequest(kind="command", command="rm -rf x", read_only=True)) == "destructive"
    assert classify(ApprovalRequest(kind="command", command="curl x", network=True)) == "network"
    assert classify(ApprovalRequest(kind="edit")) == "edit"


def test_project_names_are_unique(tmp_path):
    db = Database(tmp_path / "db")
    (tmp_path / "a").mkdir()
    (tmp_path / "b").mkdir()
    projects.add(db, str(tmp_path / "a"), "Backend")
    with pytest.raises(projects.ProjectError, match="already used"):
        projects.add(db, str(tmp_path / "b"), "backend")
    with pytest.raises(projects.ProjectError, match="already a project"):
        projects.add(db, str(tmp_path / "a"), "other")
    with pytest.raises(projects.ProjectError, match="not a directory"):
        projects.add(db, str(tmp_path / "missing"))
    assert projects.find(db, "BACKEND")["name"] == "Backend"


def test_project_update_validates(tmp_path):
    db = Database(tmp_path / "db")
    (tmp_path / "a").mkdir()
    project = projects.add(db, str(tmp_path / "a"))
    with pytest.raises(projects.ProjectError, match="approval_mode"):
        projects.update(db, project["id"], approval_mode="yolo")
    with pytest.raises(projects.ProjectError, match="cannot change"):
        projects.update(db, project["id"], path="/etc")
    assert projects.update(db, project["id"], approval_mode="never")["approval_mode"] == "never"
