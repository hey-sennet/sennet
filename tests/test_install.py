"""`agent-talk upgrade` and `remove-data`, and how the install is recognised (docs/m6-packaging.md)."""
import subprocess
import sys

import pytest

from agent_talk import __version__, install, service
from agent_talk.config import Paths
from agent_talk.db import Database


def receipt(tmp_path, monkeypatch, requirement: str):
    (tmp_path / "uv-receipt.toml").write_text(f"[tool]\nrequirements = [{requirement}]\n")
    monkeypatch.setattr(sys, "prefix", str(tmp_path))


def test_an_index_install_can_be_upgraded(tmp_path, monkeypatch):
    receipt(tmp_path, monkeypatch, '{ name = "agent-talk", extras = ["cuda"] }')
    assert install.uv_tool_requirement() == {"name": "agent-talk", "extras": ["cuda"]}


def test_an_install_from_a_directory_or_git_cant(tmp_path, monkeypatch):
    receipt(tmp_path, monkeypatch, '{ name = "agent-talk", directory = "/src/agent-talk" }')
    assert install.uv_tool_receipt() is not None and install.uv_tool_requirement() is None
    receipt(tmp_path, monkeypatch, '{ name = "agent-talk", git = "https://example.com/agent-talk" }')
    assert install.uv_tool_requirement() is None


class Service:
    name = "systemd user service"

    def __init__(self, running=True, installed=True):
        self._status = service.Status(installed, running)

    def status(self):
        return self._status


class Ran:
    """Stands in for subprocess.run in install.py: records commands, and plays the new version."""

    def __init__(self, monkeypatch, new_version="0.2.0", upgrade_code=0):
        self.calls = []
        self.new_version, self.upgrade_code = new_version, upgrade_code
        monkeypatch.setattr(install.subprocess, "run", self.run)

    def run(self, command, **kwargs):
        self.calls.append(command)
        if command[-1] == "--version":
            return subprocess.CompletedProcess(command, 0, self.new_version + "\n", "")
        if command[:3] == ["uv", "tool", "upgrade"]:
            return subprocess.CompletedProcess(command, self.upgrade_code)
        return subprocess.CompletedProcess(command, 0)


@pytest.fixture
def uv_tool(tmp_path, monkeypatch):
    receipt(tmp_path, monkeypatch, '{ name = "agent-talk", extras = ["cpu"] }')
    monkeypatch.setattr(install.shutil, "which", lambda name: f"/bin/{name}")
    monkeypatch.setattr(service, "program", lambda: ["/tools/agent-talk/bin/agent-talk"])


def use_service(monkeypatch, managed):
    monkeypatch.setattr(service, "manager", lambda: managed)


def add_task(paths, status):
    db = Database(paths.database)
    try:
        project = db.add_project("app", str(paths.data_dir), [], "codex")
        task = db.add_task(project["id"], "codex", "fix the tests")
        db.update_task(task, status=status)
    finally:
        db.close()


def test_upgrade_from_a_checkout_says_how(monkeypatch, paths, capsys, tmp_path):
    monkeypatch.setattr(sys, "prefix", str(tmp_path))
    assert install.upgrade(paths, 7710, False) == 1
    assert "git pull" in capsys.readouterr().out


def test_upgrade_restarts_a_running_service_with_the_new_version(uv_tool, monkeypatch, paths, capsys):
    use_service(monkeypatch, Service(running=True))
    ran = Ran(monkeypatch)
    assert install.upgrade(paths, 7710, False) == 0
    assert ran.calls[0] == ["uv", "tool", "upgrade", "agent-talk"]
    assert ran.calls[-1] == ["/tools/agent-talk/bin/agent-talk", "service", "restart"]
    assert f"from {__version__} to 0.2.0" in capsys.readouterr().out


def test_upgrade_without_a_service_says_to_restart(uv_tool, monkeypatch, paths, capsys):
    use_service(monkeypatch, Service(running=False, installed=False))
    ran = Ran(monkeypatch)
    assert install.upgrade(paths, 7710, False) == 0
    assert "service" not in ran.calls[-1]
    assert "Restart `agent-talk serve`" in capsys.readouterr().out


def test_upgrade_to_the_same_version_restarts_nothing(uv_tool, monkeypatch, paths, capsys):
    use_service(monkeypatch, Service(running=True))
    ran = Ran(monkeypatch, new_version=__version__)
    assert install.upgrade(paths, 7710, False) == 0
    assert len(ran.calls) == 2 and "is the latest version" in capsys.readouterr().out


def test_a_failed_upgrade_stops_there(uv_tool, monkeypatch, paths):
    use_service(monkeypatch, Service(running=True))
    ran = Ran(monkeypatch, upgrade_code=1)
    assert install.upgrade(paths, 7710, False) == 1
    assert len(ran.calls) == 1


def test_upgrade_asks_before_stopping_running_tasks(uv_tool, monkeypatch, paths, capsys):
    use_service(monkeypatch, Service(running=True))
    add_task(paths, "running")
    asked = []
    monkeypatch.setattr(install, "_confirm", lambda message, default=True: asked.append(message) or False)
    ran = Ran(monkeypatch)
    assert install.upgrade(paths, 7710, False) == 1
    assert asked and not ran.calls
    assert "1 task still going (“fix the tests”)" in capsys.readouterr().out
    assert install.upgrade(paths, 7710, True) == 0  # --yes doesn't ask
    assert len(asked) == 1


def test_finished_tasks_dont_hold_up_an_upgrade(uv_tool, monkeypatch, paths):
    use_service(monkeypatch, Service(running=True))
    add_task(paths, "completed")
    monkeypatch.setattr(install, "_confirm", lambda message, default=True: pytest.fail("asked"))
    Ran(monkeypatch)
    assert install.upgrade(paths, 7710, False) == 0


# remove-data

def test_remove_data_refuses_while_anything_holds_the_port(monkeypatch, paths, capsys):
    use_service(monkeypatch, Service(running=False))
    monkeypatch.setattr(service, "port_taken", lambda port: True)
    assert install.remove_data(paths, 7710, True) == 1
    assert "Stop it first" in capsys.readouterr().err


def test_remove_data_deletes_config_data_and_models_only(monkeypatch, tmp_path, capsys):
    use_service(monkeypatch, Service(running=False, installed=True))
    monkeypatch.setattr(service, "port_taken", lambda port: False)
    paths = Paths(tmp_path / "config", tmp_path / "data")
    project = tmp_path / "code"
    project.mkdir()
    (project / "main.py").write_text("print('hi')")
    paths.config_dir.mkdir()
    paths.config_file.write_text("[speech]\n")
    (paths.models / "parakeet").mkdir(parents=True)
    (paths.models / "parakeet" / "model.onnx").write_bytes(b"x" * 2_000_000)
    add_task(paths, "completed")
    assert install.remove_data(paths, 7710, True) == 0
    out = capsys.readouterr().out
    assert not paths.config_dir.exists() and not paths.data_dir.exists()
    assert (project / "main.py").exists()
    assert "(2 MB)" in out and "under 1 MB" in out
    assert "still installed" in out and "uv tool uninstall agent-talk" in out


def test_remove_data_asks_and_keeps_everything_on_no(monkeypatch, paths):
    use_service(monkeypatch, Service(running=False, installed=False))
    monkeypatch.setattr(service, "port_taken", lambda port: False)
    monkeypatch.setattr(install, "_confirm", lambda message, default=True: False)
    paths.config_dir.mkdir()
    paths.config_file.write_text("")
    assert install.remove_data(paths, 7710, False) == 1
    assert paths.config_file.exists()


def test_remove_data_with_a_single_home_lists_it_once(monkeypatch, tmp_path, capsys):
    use_service(monkeypatch, Service(running=False, installed=False))
    monkeypatch.setattr(service, "port_taken", lambda port: False)
    paths = Paths(tmp_path / "home", tmp_path / "home")
    paths.config_dir.mkdir()
    paths.config_file.write_text("")
    assert install.remove_data(paths, 7710, True) == 0
    assert capsys.readouterr().out.count(str(tmp_path / "home")) == 1


def test_nothing_to_remove(monkeypatch, paths, capsys):
    use_service(monkeypatch, Service(running=False, installed=False))
    monkeypatch.setattr(service, "port_taken", lambda port: False)
    assert install.remove_data(paths, 7710, True) == 0
    assert "no Agent Talk data" in capsys.readouterr().out
