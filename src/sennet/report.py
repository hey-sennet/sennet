"""`sennet doctor --report`: one Markdown file to attach to a bug report, with nothing private in it.

The config is kept by an allowlist (`KEPT`): a setting not named there, including one added later,
comes out as <removed>, so a new secret isn't leaked by default. Free text, doctor's report and the
service log, can't be allowlisted, so `Scrubber` takes out what's known to be private in it: project
paths, the home directory, this machine's names, addresses other than its own loopback, links (a
pairing link's code is in one), emails and anything shaped like a token. Never read: the database
beyond project settings, transcripts, task content, images and device tokens.
"""
from __future__ import annotations

import datetime
import importlib.metadata
import ipaddress
import os
import platform
import re
import socket
import subprocess
from dataclasses import asdict
from pathlib import Path
from urllib.parse import urlsplit

import tomli_w

from . import __version__, service
from .agents.base import AgentStatus
from .config import Paths, Settings
from .db import Database

REMOVED = "<removed>"
LOG_LINES = 300
LOG_TIMEOUT_S = 10

# What's kept from the config, by section: anything else comes out as <removed>.
KEPT = {
    "server": {"port", "log_level"},
    "tasks": {"max_concurrent", "idle_timeout_s"},
    "approvals": {"mode", "access"},
    "speech": {"enabled", "device", "threads", "voice", "wake_word"},
}
# Bind addresses that say how Sennet is reached without saying where.
GENERIC_HOSTS = {"127.0.0.1", "0.0.0.0", "::", "::1", "localhost"}
# Words of an agent's command kept as they are: a program or subcommand name, or a bare flag.
PLAIN_WORD = re.compile(r"[\w.+-]+|--?[\w-]+")


def config(settings: Settings) -> dict:
    """The settings Sennet runs with, defaults included, holding only what `KEPT` allows."""
    kept = {}
    for section, values in asdict(settings).items():
        kept[section] = {}
        for key, value in values.items():
            if key in KEPT.get(section, ()):
                kept[section][key] = value
            elif (section, key) == ("server", "host"):
                kept[section][key] = value if value in GENERIC_HOSTS else REMOVED
            elif (section, key) == ("server", "public_url"):
                kept[section][key] = f"{urlsplit(value).scheme}://{REMOVED}" if value else ""
            elif (section, key) == ("agents", "commands"):  # names are the registry's; their words may be paths
                kept[section][key] = {name: [_word(w) for w in words] for name, words in value.items()}
            else:
                kept[section][key] = REMOVED
    return kept


def _word(word: str) -> str:
    if PLAIN_WORD.fullmatch(word):
        return word
    if re.fullmatch(r"[\w./~+-]+", word) and "/" in word:
        return f"…/{Path(word).name}"  # a program's path: its name is enough
    return REMOVED


LOCAL_HOSTS = {"127.0.0.1", "localhost", "::1", "0.0.0.0"}
URL = re.compile(r"\b[a-z][a-z0-9+.-]*://[^\s'\"<>`)\]]+", re.IGNORECASE)
EMAIL = re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+")
TAILNET_NAME = re.compile(r"\b[\w-]+(?:\.[\w-]+)*\.ts\.net\b", re.IGNORECASE)
IPV4 = re.compile(r"\b(?:\d{1,3}\.){3}\d{1,3}\b")
# Long runs of letters and digits mixing both: keys, codes, secrets, ids. Not words or paths.
TOKEN = re.compile(r"(?<![\w/.-])(?=[A-Za-z_-]*\d)(?=[\d_-]*[A-Za-z])[A-Za-z0-9_-]{24,}(?![\w/.-])")


class Scrubber:
    """Takes what's private out of free text."""

    def __init__(self, settings: Settings, projects: list[dict], paths: Paths | None = None):
        # Longest first, so a project inside the home directory is named as a project.
        replacements = [(p["path"], f"<project {p['id']}>") for p in projects]
        replacements.append((str(Path.home()), "~"))
        if paths:  # SENNET_HOME can put them anywhere; under the home directory, `~` covers them
            replacements += [(str(d), label) for d, label in ((paths.config_dir, "<config dir>"),
                                                               (paths.data_dir, "<data dir>"))
                             if not d.is_relative_to(Path.home())]
        self.paths = sorted(replacements, key=lambda r: -len(r[0]))
        names = {socket.gethostname(), socket.getfqdn(), urlsplit(settings.server.public_url).hostname or ""}
        if settings.server.host not in GENERIC_HOSTS:
            names.add(settings.server.host)
        self.names = sorted((n for n in names if n and n not in LOCAL_HOSTS), key=len, reverse=True)
        self.user = os.environ.get("USER") or Path.home().name

    def __call__(self, text: str) -> str:
        text = URL.sub(self._url, text)
        for old, new in self.paths:
            text = text.replace(old, new)
        text = EMAIL.sub("<email>", text)
        text = TAILNET_NAME.sub("<tailnet name>", text)
        for name in self.names:
            text = re.sub(rf"(?<![\w-]){re.escape(name)}(?![\w-])", "<this machine>", text, flags=re.IGNORECASE)
        text = IPV4.sub(self._address, text)
        text = TOKEN.sub(REMOVED, text)
        if len(self.user) > 2:
            text = re.sub(rf"(?<![\w-]){re.escape(self.user)}(?![\w-])", "<user>", text)
        return text

    @staticmethod
    def _url(found: re.Match) -> str:
        """A link on this machine keeps its port and path, without its query or fragment; any other
        is removed but for its scheme."""
        parts = urlsplit(found.group())
        try:
            host, port = parts.hostname, parts.port
        except ValueError:
            host, port = None, None
        if host in LOCAL_HOSTS:
            shown = f"[{host}]" if ":" in host else host
            return f"{parts.scheme}://{shown}{f':{port}' if port else ''}{parts.path}"
        return f"{parts.scheme}://{REMOVED}"

    @staticmethod
    def _address(found: re.Match) -> str:
        try:
            address = ipaddress.ip_address(found.group())
        except ValueError:
            return found.group()  # a version number, say
        return found.group() if address.is_loopback or address.is_unspecified else "<address>"


def versions(agents: dict, statuses: dict[str, AgentStatus]) -> list[str]:
    lines = [f"Sennet {__version__}", f"Python {platform.python_version()} ({platform.python_implementation()})"]
    system = platform.platform()
    try:
        system = f"{platform.freedesktop_os_release()['PRETTY_NAME']}, {system}"
    except (OSError, KeyError, AttributeError):
        pass
    lines.append(system)
    lines.append(_onnx())
    for name, agent in agents.items():
        status = statuses.get(name)
        found = status.version if status and status.version else "version unknown"
        if status and not status.installed:
            found = "not installed"
        lines.append(f"{agent.title} {found} (tested with {agent.tested_version})")
    return lines


def _onnx() -> str:
    from .speech.runtime import RuntimeMissing, ort, runtime_distribution

    name = runtime_distribution()
    if name is None:
        return "No ONNX runtime"
    try:
        providers = ", ".join(ort().get_available_providers())
    except (RuntimeMissing, OSError) as error:
        providers = f"couldn't load: {error}"
    return f"{name} {importlib.metadata.version(name)} ({providers})"


def hardware(settings: Settings) -> list[str]:
    from .speech import hardware as probe
    from .speech.runtime import resolve_device

    found = probe.detect()
    lines = [f"{found.system} on {found.machine}, {found.cpu_threads} CPU threads, {found.ram_mb / 1024:.1f} GB RAM"]
    lines += [f"GPU: {gpu.name}, {gpu.total_mb} MB ({gpu.free_mb} MB free)" for gpu in found.gpus] or ["No NVIDIA GPU found"]
    recommended = probe.recommend(found)
    lines.append(f"Speech set to {settings.speech.device}, runs on {resolve_device(settings.speech.device)}; "
                 f"setup would recommend {recommended.device}")
    return lines


def projects(paths: Paths) -> list[dict]:
    if not paths.database.exists():
        return []
    db = Database(paths.database)
    try:
        return db.projects()
    finally:
        db.close()


def project_lines(found: list[dict]) -> list[str]:
    """Each project by its number, agent and settings: never its name or path."""
    def setting(value):
        return value if value else "default"
    return [f"Project {p['id']}: {p['agent']}, approvals {setting(p['approval_mode'])}, access {setting(p['access'])}, "
            f"model {setting(p['model'])}, effort {setting(p['effort'])}, "
            f"its own agent config {'on' if p['project_config'] else 'off'}" for p in sorted(found, key=lambda p: p["id"])]


def service_log() -> str:
    try:
        manager = service.manager()
        if not manager.status().installed:
            return "(the service isn't installed)"
        result = subprocess.run(manager.logs_command(False, LOG_LINES), capture_output=True, text=True, check=False,
                                timeout=LOG_TIMEOUT_S)
    except (service.ServiceError, OSError, subprocess.TimeoutExpired) as error:
        return f"(couldn't read the log: {error})"
    return result.stdout.strip() or result.stderr.strip() or "(the log is empty)"


def build(settings: Settings, paths: Paths, doctor_text: str, agents: dict, statuses: dict[str, AgentStatus]) -> str:
    found = projects(paths)
    scrub = Scrubber(settings, found, paths)

    def bullets(lines):
        return "\n".join(f"- {line}" for line in lines)

    return f"""# Sennet report

Made by `sennet doctor --report` on {datetime.date.today().isoformat()}, to attach to a bug report. It's meant
to hold nothing private: project paths and names, this machine's name and addresses, links and anything like
a key are taken out. Read it before you attach it, and remove anything you'd rather not share.

## Doctor

````text
{scrub(doctor_text.strip())}
````

## Versions

{bullets(scrub(line) for line in versions(agents, statuses))}

## Hardware

{bullets(scrub(line) for line in hardware(settings))}

## Config

The settings Sennet runs with, defaults included. Only those known to be safe are shown; the rest say {REMOVED}.

````toml
{tomli_w.dumps(config(settings)).strip()}
````

## Projects

{bullets(project_lines(found)) if found else "None yet."}

## Service log

The last {LOG_LINES} lines.

````text
{scrub(service_log())}
````
"""


def write(text: str, directory: Path) -> Path:
    path = directory / f"sennet-report-{datetime.datetime.now():%Y%m%d-%H%M%S}.md"
    path.touch(mode=0o600)
    path.write_text(text)
    return path
