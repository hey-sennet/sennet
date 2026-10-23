"""Paths and settings.

Settings live in `config.toml` in the user config directory; projects, tasks and
approvals live in SQLite in the user data directory. `AGENT_TALK_HOME` puts both
under one directory (used by tests and portable installs).
"""
from __future__ import annotations

import os
import re
import tomllib
from dataclasses import asdict, dataclass, field
from pathlib import Path

import platformdirs
import tomli_w

from .agents import registry
from .agents.base import ACCESS_LEVELS, APPROVAL_MODES

# Before M2, access levels used Codex's sandbox names under `approvals.sandbox`.
LEGACY_SANDBOX = {"read-only": "read-only", "workspace-write": "workspace", "danger-full-access": "full"}
DEVICES = ("auto", "cpu", "cuda")


@dataclass
class Paths:
    config_dir: Path
    data_dir: Path

    @classmethod
    def default(cls) -> Paths:
        if home := os.environ.get("AGENT_TALK_HOME"):
            return cls(Path(home), Path(home))
        return cls(Path(platformdirs.user_config_dir("agent-talk")), Path(platformdirs.user_data_dir("agent-talk")))

    @property
    def config_file(self) -> Path:
        return self.config_dir / "config.toml"

    @property
    def database(self) -> Path:
        return self.data_dir / "agent-talk.db"

    @property
    def models(self) -> Path:
        return self.data_dir / "models"

# Sections written by earlier versions and since removed: skipped, then dropped on the next save.
RETIRED = {"assistant", "summaries"}


@dataclass
class ServerSettings:
    host: str = "127.0.0.1"
    port: int = 7710
    # The address phones reach Agent Talk at, such as https://desktop.tailnet.ts.net, for pairing
    # links. Setup fills it from Tailscale. Empty: the address the request came in on.
    public_url: str = ""

    @property
    def local_url(self) -> str:
        """Where this machine reaches the daemon: agents calling back, and pairing links opened here."""
        host = {"0.0.0.0": "127.0.0.1", "localhost": "127.0.0.1", "::": "::1"}.get(self.host, self.host)
        return f"http://{f'[{host}]' if ':' in host else host}:{self.port}"


@dataclass
class TaskSettings:
    max_concurrent: int = 3
    # Keep an agent process warm after a turn so follow-ups start instantly.
    idle_timeout_s: int = 600


@dataclass
class ApprovalSettings:
    # ask: the agent asks before anything outside its trusted set; you answer.
    # auto: the agent works freely within its access level and asks only to go beyond it.
    # never: nothing is asked; anything beyond the access level fails.
    mode: str = "ask"
    # read-only, workspace (may edit the project directory) or full.
    access: str = "workspace"


@dataclass
class AgentSettings:
    # The command that starts each agent's CLI, by agent name, when it isn't the adapter's default:
    # [agents.commands] codex = ["codex", "app-server"]
    commands: dict[str, list[str]] = field(default_factory=dict)


@dataclass
class SpeechSettings:
    enabled: bool = True
    # auto: CUDA when the GPU runtime and models are installed, else CPU. Setup sets it.
    device: str = "auto"
    threads: int = 4
    voice: str = "af_heart"


@dataclass
class Settings:
    server: ServerSettings = field(default_factory=ServerSettings)
    tasks: TaskSettings = field(default_factory=TaskSettings)
    approvals: ApprovalSettings = field(default_factory=ApprovalSettings)
    agents: AgentSettings = field(default_factory=AgentSettings)
    speech: SpeechSettings = field(default_factory=SpeechSettings)

    @classmethod
    def load(cls, path: Path) -> Settings:
        settings = cls()
        if not path.exists():
            return settings
        data = tomllib.loads(path.read_text())
        for section, values in data.items():
            if section in RETIRED:
                continue
            target = getattr(settings, section, None)
            if target is None:
                raise ValueError(f"unknown config section [{section}]")
            for key, value in values.items():
                if section == "approvals" and key == "sandbox":
                    key, value = "access", LEGACY_SANDBOX.get(value, value)
                if section == "agents" and key.endswith("_command"):  # before M4: codex_command
                    settings.agents.commands[key.removesuffix("_command")] = value
                    continue
                if not hasattr(target, key):
                    raise ValueError(f"unknown config key {section}.{key}")
                setattr(target, key, value)
        settings.validate()
        return settings

    def validate(self):
        if self.approvals.mode not in APPROVAL_MODES:
            raise ValueError(f"approvals.mode must be one of {APPROVAL_MODES}")
        if self.approvals.access not in ACCESS_LEVELS:
            raise ValueError(f"approvals.access must be one of {ACCESS_LEVELS}")
        if self.tasks.max_concurrent < 1:
            raise ValueError("tasks.max_concurrent must be at least 1")
        if unknown := set(self.agents.commands) - set(registry.NAMES):
            raise ValueError(f"agents.commands has no agent called {', '.join(sorted(unknown))}; "
                             f"agents are {registry.NAMES}")
        self.server.public_url = self.server.public_url.strip().rstrip("/")
        if self.server.public_url and not re.fullmatch(r"https?://[^/\s]+(/\S*)?", self.server.public_url):
            raise ValueError("server.public_url must be an http(s) address, such as https://desktop.tailnet.ts.net")
        if self.speech.device not in DEVICES:
            raise ValueError(f"speech.device must be one of {DEVICES}")
        if self.speech.threads < 1:
            raise ValueError("speech.threads must be at least 1")

    def save(self, path: Path):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(tomli_w.dumps(asdict(self)))
