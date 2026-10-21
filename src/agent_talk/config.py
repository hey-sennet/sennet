"""Paths and settings.

Settings live in `config.toml` in the user config directory; projects, tasks and
approvals live in SQLite in the user data directory. `AGENT_TALK_HOME` puts both
under one directory (used by tests and portable installs).
"""
from __future__ import annotations

import os
import tomllib
from dataclasses import asdict, dataclass, field
from pathlib import Path

import platformdirs
import tomli_w

APPROVAL_MODES = ("ask", "auto", "never")
SANDBOXES = ("read-only", "workspace-write", "danger-full-access")


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


@dataclass
class ServerSettings:
    host: str = "127.0.0.1"
    port: int = 7710


@dataclass
class TaskSettings:
    max_concurrent: int = 3
    # Keep an agent process warm after a turn so follow-ups start instantly.
    idle_timeout_s: int = 600


@dataclass
class ApprovalSettings:
    # ask: the agent asks before anything outside its trusted set; you answer.
    # auto: the agent runs freely inside its sandbox and asks only to escalate.
    # never: nothing is asked; requests that need escalation fail.
    mode: str = "ask"
    sandbox: str = "workspace-write"


@dataclass
class AgentSettings:
    codex_command: list[str] = field(default_factory=lambda: ["codex", "app-server"])


@dataclass
class Settings:
    server: ServerSettings = field(default_factory=ServerSettings)
    tasks: TaskSettings = field(default_factory=TaskSettings)
    approvals: ApprovalSettings = field(default_factory=ApprovalSettings)
    agents: AgentSettings = field(default_factory=AgentSettings)

    @classmethod
    def load(cls, path: Path) -> Settings:
        settings = cls()
        if not path.exists():
            return settings
        data = tomllib.loads(path.read_text())
        for section, values in data.items():
            target = getattr(settings, section, None)
            if target is None:
                raise ValueError(f"unknown config section [{section}]")
            for key, value in values.items():
                if not hasattr(target, key):
                    raise ValueError(f"unknown config key {section}.{key}")
                setattr(target, key, value)
        settings.validate()
        return settings

    def validate(self):
        if self.approvals.mode not in APPROVAL_MODES:
            raise ValueError(f"approvals.mode must be one of {APPROVAL_MODES}")
        if self.approvals.sandbox not in SANDBOXES:
            raise ValueError(f"approvals.sandbox must be one of {SANDBOXES}")
        if self.tasks.max_concurrent < 1:
            raise ValueError("tasks.max_concurrent must be at least 1")

    def save(self, path: Path):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(tomli_w.dumps(asdict(self)))
