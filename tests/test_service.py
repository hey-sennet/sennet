"""The user service: the files it writes, what it reads back, and doctor's checks (docs/m6-packaging.md)."""
import logging
import plistlib
import subprocess
import sys

import pytest
from fastapi.testclient import TestClient

from sennet import __version__, cli, service, setup
from sennet.agents import registry
from sennet.config import Settings
from sennet.server import create_app
from test_setup import LAPTOP, Scripted


@pytest.fixture
def home(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / ".config"))
    return tmp_path


class Commands:
    """Stands in for systemctl, launchctl and loginctl: records each call and answers from a table."""

    def __init__(self, monkeypatch, answers: dict[str, tuple[int, str]] | None = None):
        self.calls: list[list[str]] = []
        self.answers = answers or {}
        monkeypatch.setattr(service, "_run", self.run)

    def run(self, command, check=True):
        self.calls.append(command)
        code, out = next((a for key, a in self.answers.items() if key in " ".join(command)), (0, ""))
        if check and code:
            raise service.ServiceError(f"{command[0]} failed")
        return subprocess.CompletedProcess(command, code, out, "")

    def ran(self, *words) -> bool:
        return any(all(w in call for w in words) for call in self.calls)


# What the unit runs

def test_the_service_runs_this_installs_script(monkeypatch, tmp_path):
    (tmp_path / "bin").mkdir()
    script = tmp_path / "bin" / "sennet"
    script.write_text("#!/bin/sh\n")
    script.chmod(0o755)
    monkeypatch.setattr(sys, "prefix", str(tmp_path))
    assert service.program() == [str(script)]


def test_without_a_script_the_service_runs_the_module(monkeypatch, tmp_path):
    monkeypatch.setattr(sys, "prefix", str(tmp_path))
    assert service.program() == [sys.executable, "-m", "sennet"]


def test_the_environment_keeps_path_once_and_the_data_location(monkeypatch):
    monkeypatch.setenv("PATH", "/a:/b::/a:/c")
    monkeypatch.setenv("SENNET_HOME", "/srv/talk")
    monkeypatch.delenv("XDG_DATA_HOME", raising=False)
    env = service.environment()
    assert env["PATH"] == "/a:/b:/c"
    assert env["SENNET_HOME"] == "/srv/talk"
    assert "XDG_DATA_HOME" not in env and env["PYTHONUNBUFFERED"] == "1"


# systemd

def test_the_unit_reads_back_with_spaces_quotes_and_percent_signs():
    command = ["/home/a b/.local/bin/sennet"]
    path = '/home/a b/bin:/opt/100%/bin:/odd"dir'
    unit = service.Systemd().unit(command, {"PATH": path, "PYTHONUNBUFFERED": "1"})
    assert 'ExecStart="/home/a b/.local/bin/sennet" serve' in unit
    assert "100%%" in unit
    assert "Restart=on-failure" in unit and "WantedBy=default.target" in unit
    assert service.parse_unit(unit) == (command, path)


def test_installing_writes_the_unit_and_enables_it(home, monkeypatch):
    commands = Commands(monkeypatch)
    monkeypatch.setattr(service.shutil, "which", lambda name, path=None: f"/usr/bin/{name}")
    systemd = service.Systemd()
    systemd.install(["/opt/sennet"], {"PATH": "/usr/bin"})
    assert systemd.file == home / ".config" / "systemd" / "user" / "sennet.service"
    assert "ExecStart=/opt/sennet serve" in systemd.file.read_text()
    assert commands.calls == [["systemctl", "--user", "daemon-reload"], ["systemctl", "--user", "enable", "sennet"],
                              ["systemctl", "--user", "restart", "sennet"]]


def test_status_comes_from_systemctl_and_the_unit(home, monkeypatch):
    monkeypatch.setattr(service.shutil, "which", lambda name, path=None: f"/usr/bin/{name}")
    systemd = service.Systemd()
    assert systemd.status() == service.Status(False, False)
    systemd.file.parent.mkdir(parents=True)
    systemd.file.write_text(systemd.unit(["/opt/sennet"], {"PATH": "/usr/bin:/home/a/.local/bin"}))
    Commands(monkeypatch, {"show": (0, "ActiveState=active\nSubState=running\nUnitFileState=enabled\nMainPID=42\n")})
    status = systemd.status()
    assert status == service.Status(True, True, True, 42, ["/opt/sennet"], "/usr/bin:/home/a/.local/bin",
                                    "active (running)")
    Commands(monkeypatch, {"show": (0, "ActiveState=failed\nSubState=failed\nUnitFileState=enabled\nMainPID=0\n")})
    status = systemd.status()
    assert not status.running and status.pid is None and status.detail == "failed (failed)"


def test_uninstalling_disables_and_removes_the_unit(home, monkeypatch):
    commands = Commands(monkeypatch)
    systemd = service.Systemd()
    systemd.file.parent.mkdir(parents=True)
    systemd.file.write_text("x")
    systemd.uninstall()
    assert not systemd.file.exists()
    assert commands.ran("disable", "--now")


def test_linger_is_read_from_loginctl(monkeypatch):
    monkeypatch.setattr(service.shutil, "which", lambda name, path=None: f"/usr/bin/{name}")
    Commands(monkeypatch, {"Linger": (0, "Linger=no\n")})
    assert service.Systemd.lingering() is False
    Commands(monkeypatch, {"Linger": (0, "Linger=yes\n")})
    assert service.Systemd.lingering() is True
    Commands(monkeypatch, {"Linger": (1, "")})
    assert service.Systemd.lingering() is None


# launchd

def test_the_launch_agent_runs_at_load_and_restarts_after_a_crash(home, monkeypatch):
    commands = Commands(monkeypatch)
    launchd = service.Launchd()
    launchd.install(["/opt/sennet"], {"PATH": "/usr/bin:/opt/homebrew/bin"})
    plist = plistlib.loads(launchd.file.read_bytes())
    assert launchd.file == home / "Library" / "LaunchAgents" / "sennet.plist"
    assert plist["ProgramArguments"] == ["/opt/sennet", "serve"]
    assert plist["EnvironmentVariables"]["PATH"] == "/usr/bin:/opt/homebrew/bin"
    assert plist["RunAtLoad"] is True and plist["KeepAlive"] == {"SuccessfulExit": False}
    assert plist["StandardOutPath"] == str(home / "Library" / "Logs" / "sennet" / "sennet.log")
    assert commands.ran("launchctl", "bootstrap")


def test_launch_agent_status_comes_from_launchctl_print(home, monkeypatch):
    launchd = service.Launchd()
    launchd.file.parent.mkdir(parents=True)
    launchd.file.write_bytes(launchd.unit(["/opt/sennet"], {"PATH": "/usr/bin"}))
    Commands(monkeypatch, {"print": (0, "gui/501/sennet = {\n\tstate = running\n\tpid = 77\n}")})
    status = launchd.status()
    assert status.running and status.pid == 77 and status.command == ["/opt/sennet"]
    Commands(monkeypatch, {"print": (113, "")})
    assert launchd.status().detail == "not loaded"


# Installing and serving

def test_install_refuses_while_sennet_runs_outside_the_service(home, monkeypatch):
    Commands(monkeypatch)
    monkeypatch.setattr(service, "manager", lambda: service.Systemd())
    monkeypatch.setattr(service, "health", lambda port, timeout=1.0: {"app": "sennet", "version": "0.1.0"})
    with pytest.raises(service.ServiceError, match="outside the service"):
        service.install(7710)
    monkeypatch.setattr(service, "health", lambda port, timeout=1.0: None)
    monkeypatch.setattr(service, "port_taken", lambda port: True)
    with pytest.raises(service.ServiceError, match="another program"):
        service.install(7710)


def test_install_waits_for_the_daemon_to_answer(home, monkeypatch):
    Commands(monkeypatch)
    monkeypatch.setattr(service.shutil, "which", lambda name, path=None: f"/usr/bin/{name}")
    monkeypatch.setattr(service, "manager", lambda: service.Systemd())
    monkeypatch.setattr(service, "health", lambda port, timeout=1.0: None)
    monkeypatch.setattr(service, "port_taken", lambda port: False)
    monkeypatch.setattr(service, "wait_until_up", lambda port: None)
    with pytest.raises(service.ServiceError, match="didn't answer"):
        service.install(7710)
    monkeypatch.setattr(service, "wait_until_up", lambda port: {"app": "sennet", "version": "9"})
    assert service.install(7710)[1]["version"] == "9"


def test_serve_refuses_when_sennet_already_runs(monkeypatch, paths):
    monkeypatch.setattr(service, "health", lambda port, timeout=1.0: {"app": "sennet", "version": "0.1.0"})
    with pytest.raises(SystemExit, match="already running"):
        cli.serve_command(Settings(), paths, type("Args", (), {"host": None, "port": None, "log_level": None})())


@pytest.mark.parametrize(("configured", "flag", "sennet", "libraries"), [
    ("info", None, logging.INFO, logging.INFO),
    ("debug", None, logging.DEBUG, logging.INFO),  # libraries stay at info, or they drown Sennet out
    ("info", "warning", logging.WARNING, logging.WARNING),  # the flag wins over the config
])
def test_serve_logs_at_the_configured_level(monkeypatch, paths, configured, flag, sennet, libraries):
    monkeypatch.setattr(service, "health", lambda port, timeout=1.0: {"app": "sennet", "version": "0.1.0"})
    monkeypatch.setattr(logging, "basicConfig", lambda level, **_: logging.getLogger().setLevel(level))
    root, own = logging.getLogger(), logging.getLogger("sennet")
    before = root.level, own.level
    settings = Settings()
    settings.server.log_level = configured
    try:
        with pytest.raises(SystemExit):  # stops at "already running", after logging is set up
            cli.serve_command(settings, paths, type("Args", (), {"host": None, "port": None, "log_level": flag})())
        assert (own.level, root.level) == (sennet, libraries)
    finally:
        root.setLevel(before[0])
        own.setLevel(before[1])


def test_the_log_level_must_be_known(tmp_path):
    config = tmp_path / "config.toml"
    config.write_text('[server]\nlog_level = "loud"\n')
    with pytest.raises(ValueError, match="server.log_level"):
        Settings.load(config)


def test_health_answers_without_pairing(settings, paths):
    with TestClient(create_app(settings, paths)) as client:
        assert client.get("/api/health").json() == {"app": "sennet", "version": __version__}
        assert client.get("/api/state").status_code == 401


def test_the_health_check_ignores_other_programs(monkeypatch):
    class Response:
        def __init__(self, body):
            self.body = body

        def read(self):
            return self.body

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

    monkeypatch.setattr(service.urllib.request, "urlopen", lambda url, timeout: Response(b'{"ok": true}'))
    assert service.health(7710) is None
    monkeypatch.setattr(service.urllib.request, "urlopen",
                        lambda url, timeout: Response(b'{"app": "sennet", "version": "1"}'))
    assert service.health(7710) == {"app": "sennet", "version": "1"}


# doctor

class Installed:
    name = "systemd user service"

    def __init__(self, status, lingering=True):
        self._status, self._lingering = status, lingering

    def status(self):
        return self._status

    def lingering(self):
        return self._lingering


def check(monkeypatch, capsys, status, health=None, lingering=True, which=None):
    monkeypatch.setattr(service, "manager", lambda: Installed(status, lingering))
    monkeypatch.setattr(service, "health", lambda port, timeout=1.0: health)
    monkeypatch.setattr(cli.shutil, "which", which or (lambda name, path=None: f"/bin/{name}"))
    ok = cli.service_ok(Settings(), registry.build({}))
    return ok, capsys.readouterr().out


def running(**changes) -> service.Status:
    status = service.Status(True, True, True, 42, service.program(), "/bin", "active (running)")
    for key, value in changes.items():
        setattr(status, key, value)
    return status


def test_doctor_is_happy_with_a_running_service(monkeypatch, capsys):
    ok, out = check(monkeypatch, capsys, running(), {"app": "sennet", "version": __version__})
    assert ok and "ok: running (pid 42), starts at login" in out


def test_doctor_mentions_linger_when_it_is_off(monkeypatch, capsys):
    ok, out = check(monkeypatch, capsys, running(), {"app": "sennet", "version": __version__}, lingering=False)
    assert ok and "loginctl enable-linger" in out


def test_doctor_without_a_service_is_not_a_problem(monkeypatch, capsys):
    ok, out = check(monkeypatch, capsys, service.Status(False, False))
    assert ok and "not installed" in out


def test_doctor_flags_a_service_for_another_install(monkeypatch, capsys):
    ok, out = check(monkeypatch, capsys, running(command=["/old/sennet"]),
                    {"app": "sennet", "version": __version__})
    assert not ok and "not this install" in out


def test_doctor_flags_a_stopped_service_and_an_old_version(monkeypatch, capsys):
    ok, out = check(monkeypatch, capsys, running(running=False, detail="failed (failed)"))
    assert not ok and "installed but failed" in out
    ok, out = check(monkeypatch, capsys, running(), {"app": "sennet", "version": "0.0.1"})
    assert not ok and "sennet service restart" in out


def test_doctor_flags_an_agent_the_service_cant_find(monkeypatch, capsys):
    def which(name, path=None):
        return f"/home/a/.local/bin/{name}" if path is None or name == "codex" else None

    ok, out = check(monkeypatch, capsys, running(), {"app": "sennet", "version": __version__}, which=which)
    assert not ok and "can't find claude (Claude Code)" in out and "can't find codex" not in out


# setup's last step

class Manager(Installed):
    def __init__(self, status, lingering=False, fail_linger=False):
        super().__init__(status, lingering)
        self.lingered, self.fail_linger = False, fail_linger

    def linger(self):
        if self.fail_linger:
            raise service.ServiceError("loginctl enable-linger failed: denied")
        self.lingered = True


def test_setup_installs_the_service_and_asks_about_linger(monkeypatch):
    manager = Manager(service.Status(False, False))
    installed = []
    monkeypatch.setattr(service, "manager", lambda: manager)
    monkeypatch.setattr(service, "install",
                        lambda port: installed.append(port) or (manager, {"version": __version__}))
    ui = Scripted("install", "yes")
    assert setup._choose_service(ui, Settings()) is True
    assert installed == [7710] and manager.lingered
    assert ui.options("Sennet runs")["install"].tags == ("recommended",)
    assert ui.asked[1][2] == "no"  # linger isn't the default
    assert "keeps running after you log out" in ui.output()


def test_setup_updates_an_installed_service(monkeypatch):
    manager = Manager(running(), lingering=True)
    monkeypatch.setattr(service, "manager", lambda: manager)
    monkeypatch.setattr(service, "install", lambda port: (manager, {"version": __version__}))
    ui = Scripted("install")
    assert setup._choose_service(ui, Settings()) is True
    assert "Update and restart it" in ui.options("Sennet runs")["install"].label
    assert len(ui.asked) == 1  # linger is on already


def test_setup_can_leave_the_service_out(monkeypatch):
    monkeypatch.setattr(service, "manager", lambda: Manager(service.Status(False, False)))
    monkeypatch.setattr(service, "install", lambda port: pytest.fail("installed"))
    assert setup._choose_service(Scripted("skip"), Settings()) is False


def test_setup_reports_a_service_that_wont_start(monkeypatch):
    def fail(port):
        raise service.ServiceError("the service started but didn't answer")

    monkeypatch.setattr(service, "manager", lambda: Manager(service.Status(False, False)))
    monkeypatch.setattr(service, "install", fail)
    ui = Scripted("install")
    assert setup._choose_service(ui, Settings()) is False
    assert "didn't answer" in ui.output()


def test_a_refused_linger_says_how_to_do_it_with_sudo(monkeypatch):
    manager = Manager(service.Status(False, False), fail_linger=True)
    monkeypatch.setattr(service, "manager", lambda: manager)
    monkeypatch.setattr(service, "install", lambda port: (manager, {"version": __version__}))
    ui = Scripted("install", "yes")
    setup._choose_service(ui, Settings())
    assert "sudo loginctl enable-linger $USER" in ui.output()


def test_setup_skips_the_service_where_there_is_none(monkeypatch):
    def unsupported():
        raise service.ServiceError("services aren't supported on Windows; run `sennet serve` instead")

    monkeypatch.setattr(service, "manager", unsupported)
    ui = Scripted()
    assert setup._choose_service(ui, Settings()) is False
    assert "aren't supported on Windows" in ui.output()


class Stoppable(Manager):
    def __init__(self, running=True):
        super().__init__(service.Status(True, running), lingering=True)
        self.calls = []

    def stop(self):
        self.calls.append("stop")
        self._status = service.Status(True, False)

    def start(self):
        self.calls.append("start")
        self._status = service.Status(True, True)


def test_setup_stops_a_running_service_and_starts_it_again(monkeypatch):
    manager = Stoppable()
    monkeypatch.setattr(service, "manager", lambda: manager)
    monkeypatch.setattr(service, "wait_until_up", lambda port: {"version": __version__})
    monkeypatch.delenv(setup.RESUME, raising=False)
    ui = Scripted()
    assert setup._pause_service(ui) is manager and manager.calls == ["stop"]
    assert setup._choose_service(Scripted("skip"), Settings(), paused=True) is True
    setup._resume_service(ui, manager, 7710)
    assert manager.calls == ["stop", "start"] and "Started the service again" in ui.output()


def test_setup_leaves_a_stopped_service_alone(monkeypatch):
    manager = Stoppable(running=False)
    monkeypatch.setattr(service, "manager", lambda: manager)
    monkeypatch.delenv(setup.RESUME, raising=False)
    assert setup._pause_service(Scripted()) is None and not manager.calls


def test_setup_after_reinstalling_itself_still_starts_the_service_it_stopped(monkeypatch):
    manager = Stoppable(running=False)
    monkeypatch.setattr(service, "manager", lambda: manager)
    monkeypatch.setenv(setup.RESUME, "1")
    assert setup._pause_service(Scripted()) is manager
    assert setup.RESUME not in setup.os.environ


def test_setup_stops_and_restarts_the_service_around_the_whole_run(monkeypatch, paths):
    manager = Stoppable()
    monkeypatch.setattr(service, "manager", lambda: manager)
    monkeypatch.setattr(service, "wait_until_up", lambda port: {"version": __version__})
    monkeypatch.delenv(setup.RESUME, raising=False)

    def stop_early(*args):
        assert manager.calls == ["stop"]
        raise setup.Abort()

    monkeypatch.setattr(setup, "_run", stop_early)
    monkeypatch.setattr(setup.sys.stdin, "isatty", lambda: True)
    assert setup.run(Settings(), paths, None, assume_yes=True) == 130
    assert manager.calls == ["stop", "start"]
