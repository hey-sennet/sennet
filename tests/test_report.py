"""`sennet doctor --report`: what the bug report bundle keeps, and what it takes out."""
import socket
from pathlib import Path

import pytest

from sennet import cli, report, service
from sennet.config import Settings
from sennet.db import Database

HOME = str(Path.home())


def test_the_config_keeps_only_what_the_allowlist_names(monkeypatch):
    settings = Settings()
    settings.server.host, settings.server.public_url = "192.168.1.20", "https://desk.tail1234.ts.net"
    settings.agents.commands["codex"] = [f"{HOME}/bin/codex", "app-server", "--token=abc123", "-v"]
    kept = report.config(settings)
    assert kept["server"] == {"host": "<removed>", "port": 7710, "public_url": "https://<removed>", "log_level": "info"}
    assert kept["agents"]["commands"]["codex"] == ["…/codex", "app-server", "<removed>", "-v"]
    assert kept["speech"]["voice"] == "af_heart"

    # A setting the allowlist doesn't name, such as one added later, is removed.
    monkeypatch.setitem(report.KEPT, "speech", report.KEPT["speech"] - {"voice"})
    assert report.config(settings)["speech"]["voice"] == "<removed>"


def test_a_generic_bind_address_is_kept():
    settings = Settings()
    settings.server.host = "0.0.0.0"
    assert report.config(settings)["server"]["host"] == "0.0.0.0"


@pytest.fixture
def scrub(monkeypatch):
    monkeypatch.setattr(socket, "gethostname", lambda: "desk")
    monkeypatch.setattr(socket, "getfqdn", lambda: "desk.lan")
    monkeypatch.setenv("USER", "sam")
    settings = Settings()
    settings.server.public_url = "https://desk.tail1234.ts.net"
    return report.Scrubber(settings, [{"id": 3, "path": f"{HOME}/work/secret-app"}])


@pytest.mark.parametrize("text, expected", [
    (f"cwd {HOME}/work/secret-app/src", "cwd <project 3>/src"),
    (f"models in {HOME}/.local/share/sennet", "models in ~/.local/share/sennet"),
    ("Open http://127.0.0.1:7710/pair#Kx9fQ2mZ7rTt to pair", "Open http://127.0.0.1:7710/pair to pair"),
    ("push to https://fcm.googleapis.com/fcm/send/abc:def failed", "push to https://<removed> failed"),
    ("served at desk.tail1234.ts.net", "served at <tailnet name>"),
    ("Oct 26 desk sennet[1]: INFO", "Oct 26 <this machine> sennet[1]: INFO"),
    ("from desk.lan and 100.101.102.103", "from <this machine> and <address>"),
    ("bound to 127.0.0.1 and 0.0.0.0", "bound to 127.0.0.1 and 0.0.0.0"),
    ("written by sam@example.com", "written by <email>"),
    ("key sk-ant-oat01-a8Fj3kLmN0pQrS9tUvWx2yZ end", "key <removed> end"),
    ("logged in as sam", "logged in as <user>"),
    ("version 2.1.311, libopus 1.6.1, python3.12", "version 2.1.311, libopus 1.6.1, python3.12"),
    ("tasks 23, 26, 27 from the device's earlier session", "tasks 23, 26, 27 from the device's earlier session"),
])
def test_free_text_loses_what_is_private(scrub, text, expected):
    assert scrub(text) == expected


def test_projects_are_listed_by_number_agent_and_settings_only(paths, tmp_path):
    db = Database(paths.database)
    (tmp_path / "secret-app").mkdir()
    db.add_project("Secret App", str(tmp_path / "secret-app"), "claude")
    db.close()
    found = report.projects(paths)
    lines = report.project_lines(found)
    assert lines == [(f"Project {found[0]['id']}: claude, approvals default, access default, model default, "
                      "effort default, its own agent config off, a worktree for each task off")]


class Logged:
    name = "systemd user service"

    def __init__(self, project: Path):
        self.project = project

    def status(self):
        return service.Status(True, True)

    def logs_command(self, follow, lines=100):
        return ["printf", (f"Sennet on http://127.0.0.1:7710\\nOpen http://127.0.0.1:7710/pair#Kx9fQ2mZ7rTt0011\\n"
                           f"WARNING claude exited: {self.project}/src: denied\\n")]


def test_doctor_report_writes_one_file_without_private_details(settings, paths, tmp_path, monkeypatch, capsys):
    db = Database(paths.database)
    (tmp_path / "secret-app").mkdir()
    db.add_project("Secret App", str(tmp_path / "secret-app"), "codex")
    db.close()
    settings.server.public_url = "https://desk.tail1234.ts.net"
    monkeypatch.setattr(service, "manager", lambda: Logged(tmp_path / "secret-app"))
    monkeypatch.setattr(service, "health", lambda port, timeout=1.0: None)
    monkeypatch.chdir(tmp_path)
    cli.doctor(settings, paths, report=True)
    out = capsys.readouterr().out
    written, = tmp_path.glob("sennet-report-*.md")
    assert f"Wrote {written}" in out and "Read it before you attach it" in out
    text = written.read_text()
    for heading in ("## Doctor", "## Versions", "## Hardware", "## Config", "## Projects", "## Service log"):
        assert heading in text
    assert "codex:     ok: logged in (plus plan), version 0.164.0" in text
    assert "Codex 0.164.0 (tested with 0.164)" in text
    assert ": codex, approvals default" in text
    assert "http://127.0.0.1:7710/pair\n" in text
    assert "claude exited: <project 1>/src: denied" in text
    for private in ("secret-app", "Secret App", "Kx9fQ2mZ7rTt0011", "tail1234", HOME, str(tmp_path)):
        assert private not in text
    assert written.stat().st_mode & 0o777 == 0o600
