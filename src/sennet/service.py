"""`sennet service`: run Sennet as a user service (docs/m6-packaging.md).

A systemd `--user` unit on Linux, a LaunchAgent on macOS. Either runs this install's
`sennet serve` as the user, starts at login and restarts after a crash. A service
doesn't get the login shell's PATH, so the agents (`claude` in ~/.local/bin, `codex`
under nvm or Homebrew) wouldn't be found; install records the installing shell's PATH
in the unit, and `doctor` compares the two.
"""
from __future__ import annotations

import json
import os
import platform
import plistlib
import re
import shlex
import shutil
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path

NAME = "sennet"
LABEL = "sennet"                # launchd label
START_TIMEOUT_S = 30
# Variables the service needs to find the same install, data and agents as the installing shell.
KEPT = ("SENNET_HOME", "XDG_CONFIG_HOME", "XDG_DATA_HOME")


class ServiceError(Exception):
    pass


@dataclass
class Status:
    installed: bool
    running: bool
    enabled: bool = False
    pid: int | None = None
    command: list[str] | None = None    # what the unit runs
    path: str | None = None             # the PATH it runs with
    detail: str = ""


def program() -> list[str]:
    """The command that runs this install of Sennet."""
    script = Path(sys.prefix) / "bin" / NAME
    if script.is_file() and os.access(script, os.X_OK):
        return [str(script)]
    return [sys.executable, "-m", "sennet"]


def search_path(path: str | None = None) -> str:
    """PATH without duplicates or empty entries, in its order."""
    seen: list[str] = []
    for entry in (path if path is not None else os.environ.get("PATH", "")).split(os.pathsep):
        if entry and entry not in seen:
            seen.append(entry)
    return os.pathsep.join(seen)


def environment() -> dict[str, str]:
    env = {"PATH": search_path(), "PYTHONUNBUFFERED": "1"}
    env.update({key: os.environ[key] for key in KEPT if os.environ.get(key)})
    return env


def manager() -> Systemd | Launchd:
    system = platform.system()
    if system == "Linux":
        return Systemd()
    if system == "Darwin":
        return Launchd()
    raise ServiceError(f"services aren't supported on {system}; run `sennet serve` instead")


def health(port: int, timeout: float = 1.0) -> dict | None:
    """What answers on Sennet's port: its health, or None when nothing does or it isn't Sennet."""
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/api/health", timeout=timeout) as response:
            body = json.loads(response.read())
    except (OSError, ValueError, urllib.error.URLError):
        return None
    return body if isinstance(body, dict) and body.get("app") == NAME else None


def wait_until_up(port: int, timeout: float = START_TIMEOUT_S) -> dict | None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if (found := health(port)) is not None:
            return found
        time.sleep(0.5)
    return None


def _run(command: list[str], check: bool = True) -> subprocess.CompletedProcess:
    try:
        result = subprocess.run(command, capture_output=True, text=True, timeout=30)
    except (OSError, subprocess.TimeoutExpired) as error:
        raise ServiceError(f"{shlex.join(command)} failed: {error}") from error
    if check and result.returncode != 0:
        raise ServiceError(f"{shlex.join(command)} failed: {(result.stderr or result.stdout).strip()}")
    return result


class Systemd:
    """A systemd --user unit."""

    name = "systemd user service"

    @property
    def file(self) -> Path:
        config = os.environ.get("XDG_CONFIG_HOME") or str(Path.home() / ".config")
        return Path(config) / "systemd" / "user" / f"{NAME}.service"

    def unit(self, command: list[str], env: dict[str, str]) -> str:
        variables = "\n".join(f"Environment={_quote(f'{k}={v}')}" for k, v in env.items())
        return (
            "# Written by `sennet service install`; run it again to update this file.\n"
            "[Unit]\n"
            "Description=Sennet\n"
            "After=network-online.target\n"
            "\n"
            "[Service]\n"
            f"ExecStart={' '.join(_quote(part) for part in [*command, 'serve'])}\n"
            f"{variables}\n"
            "Restart=on-failure\n"
            "RestartSec=5\n"
            "\n"
            "[Install]\n"
            "WantedBy=default.target\n")

    def install(self, command: list[str], env: dict[str, str]):
        if not shutil.which("systemctl"):
            raise ServiceError("systemctl isn't installed, so there's no systemd to run the service")
        self.file.parent.mkdir(parents=True, exist_ok=True)
        self.file.write_text(self.unit(command, env))
        _run(["systemctl", "--user", "daemon-reload"])
        _run(["systemctl", "--user", "enable", NAME])
        _run(["systemctl", "--user", "restart", NAME])

    def uninstall(self):
        if self.file.exists():
            _run(["systemctl", "--user", "disable", "--now", NAME], check=False)
            self.file.unlink()
            _run(["systemctl", "--user", "daemon-reload"], check=False)

    def start(self):
        _run(["systemctl", "--user", "start", NAME])

    def stop(self):
        _run(["systemctl", "--user", "stop", NAME])

    def restart(self):
        _run(["systemctl", "--user", "restart", NAME])

    def logs_command(self, follow: bool) -> list[str]:
        return ["journalctl", "--user", "-u", NAME, "-n", "100", "--no-pager", *(["-f"] if follow else [])]

    def status(self) -> Status:
        if not self.file.exists():
            return Status(False, False)
        command, path = parse_unit(self.file.read_text())
        if not shutil.which("systemctl"):
            return Status(True, False, command=command, path=path, detail="systemctl isn't installed")
        shown = _run(["systemctl", "--user", "show", NAME, "-p", "ActiveState,SubState,UnitFileState,MainPID"],
                     check=False)
        fields = dict(line.split("=", 1) for line in shown.stdout.splitlines() if "=" in line)
        pid = int(fields.get("MainPID", "0") or 0) or None
        state = fields.get("ActiveState", "unknown")
        sub = fields.get("SubState", "")
        return Status(True, state == "active", fields.get("UnitFileState") == "enabled", pid, command, path,
                      f"{state} ({sub})" if sub else state)

    @staticmethod
    def lingering() -> bool | None:
        """Whether this user's services run while they're logged out; None when it can't be told."""
        if not shutil.which("loginctl"):
            return None
        shown = _run(["loginctl", "show-user", os.environ.get("USER") or str(os.getuid()), "-p", "Linger"],
                     check=False)
        if shown.returncode != 0:
            return None
        return shown.stdout.strip() == "Linger=yes"

    @staticmethod
    def linger():
        _run(["loginctl", "enable-linger"])


class Launchd:
    """A LaunchAgent, loaded into the user's GUI session."""

    name = "launchd agent"

    @property
    def file(self) -> Path:
        return Path.home() / "Library" / "LaunchAgents" / f"{LABEL}.plist"

    @property
    def log(self) -> Path:
        return Path.home() / "Library" / "Logs" / NAME / f"{NAME}.log"

    @property
    def target(self) -> str:
        return f"gui/{os.getuid()}"

    def unit(self, command: list[str], env: dict[str, str]) -> bytes:
        return plistlib.dumps({
            "Label": LABEL,
            "ProgramArguments": [*command, "serve"],
            "EnvironmentVariables": env,
            "RunAtLoad": True,
            "KeepAlive": {"SuccessfulExit": False},
            "ThrottleInterval": 5,
            "StandardOutPath": str(self.log),
            "StandardErrorPath": str(self.log),
        })

    def install(self, command: list[str], env: dict[str, str]):
        self.file.parent.mkdir(parents=True, exist_ok=True)
        self.log.parent.mkdir(parents=True, exist_ok=True)
        _run(["launchctl", "bootout", f"{self.target}/{LABEL}"], check=False)
        self.file.write_bytes(self.unit(command, env))
        _run(["launchctl", "enable", f"{self.target}/{LABEL}"], check=False)
        _run(["launchctl", "bootstrap", self.target, str(self.file)])

    def uninstall(self):
        if self.file.exists():
            _run(["launchctl", "bootout", f"{self.target}/{LABEL}"], check=False)
            self.file.unlink()

    def start(self):
        if _run(["launchctl", "print", f"{self.target}/{LABEL}"], check=False).returncode != 0:
            _run(["launchctl", "bootstrap", self.target, str(self.file)])
        else:
            _run(["launchctl", "kickstart", f"{self.target}/{LABEL}"])

    def stop(self):
        # KeepAlive would restart a killed agent; booting it out stops it until the next login or start.
        _run(["launchctl", "bootout", f"{self.target}/{LABEL}"], check=False)

    def restart(self):
        self.start()
        _run(["launchctl", "kickstart", "-k", f"{self.target}/{LABEL}"])

    def logs_command(self, follow: bool) -> list[str]:
        return ["tail", "-n", "100", *(["-f"] if follow else []), str(self.log)]

    def status(self) -> Status:
        if not self.file.exists():
            return Status(False, False)
        try:
            config = plistlib.loads(self.file.read_bytes())
        except (OSError, plistlib.InvalidFileException, ValueError):
            return Status(True, False, detail=f"{self.file} can't be read; run `sennet service install`")
        command = list(config.get("ProgramArguments", []))[:-1] or None
        path = (config.get("EnvironmentVariables") or {}).get("PATH")
        printed = _run(["launchctl", "print", f"{self.target}/{LABEL}"], check=False)
        if printed.returncode != 0:
            return Status(True, False, False, None, command, path, "not loaded")
        state = re.search(r"^\s*state = (\S+)", printed.stdout, re.M)
        pid = re.search(r"^\s*pid = (\d+)", printed.stdout, re.M)
        running = bool(state and state.group(1) == "running")
        return Status(True, running, True, int(pid.group(1)) if pid else None, command, path,
                      state.group(1) if state else "loaded")

    @staticmethod
    def lingering() -> bool | None:
        return None  # a LaunchAgent runs in the login session; there is no linger to set


def parse_unit(text: str) -> tuple[list[str] | None, str | None]:
    """The command (without `serve`) and PATH in a unit `unit()` wrote."""
    command = path = None
    for line in text.splitlines():
        if line.startswith("ExecStart="):
            parts = [part.replace("%%", "%") for part in shlex.split(line.removeprefix("ExecStart="))]
            command = parts[:-1] or None
        elif line.startswith("Environment="):
            key, _, value = shlex.split(line.removeprefix("Environment="))[0].replace("%%", "%").partition("=")
            if key == "PATH":
                path = value
    return command, path


def _quote(value: str) -> str:
    """Quote a value for a systemd unit line when it needs it; `%` is systemd's specifier character."""
    value = value.replace("%", "%%")
    if re.fullmatch(r"[\w@%+=:,./-]+", value):
        return value
    return '"' + value.replace("\\", "\\\\").replace('"', '\\"') + '"'


def install(port: int) -> tuple[Systemd | Launchd, dict]:
    """Install the service for this install of Sennet, or update it, and start it. Returns the
    manager and the started daemon's health."""
    service = manager()
    before = service.status()
    if not before.running:
        if health(port) is not None:
            raise ServiceError(f"Sennet is already running on port {port}, outside the service. Stop it "
                               "(Ctrl+C where `sennet serve` runs), then install the service again.")
        if port_taken(port):
            raise ServiceError(f"another program is using port {port}. Stop it, or set server.port in the config.")
    service.install(program(), environment())
    if (found := wait_until_up(port)) is None:
        raise ServiceError(f"the service started but didn't answer on port {port} within {START_TIMEOUT_S} s; "
                           "`sennet service logs` shows why")
    return service, found


def port_taken(port: int) -> bool:
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=0.3):
            return True
    except OSError:
        return False
