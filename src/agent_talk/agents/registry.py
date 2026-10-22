"""The agents Agent Talk can drive. Every adapter is listed here and nowhere else.

The first one listed is the default for new projects.
"""
from __future__ import annotations

from .base import Agent
from .callbacks import CallbackServer
from .claude import ClaudeAgent
from .codex import CodexAgent

ADAPTERS: dict[str, type[Agent]] = {CodexAgent.name: CodexAgent, ClaudeAgent.name: ClaudeAgent}
NAMES: tuple[str, ...] = tuple(ADAPTERS)
DEFAULT = NAMES[0]


def build(commands: dict[str, list[str]], callbacks: CallbackServer | None = None) -> dict[str, Agent]:
    """One of each adapter, started with its configured command or its own default. `callbacks` is the
    daemon's MCP endpoint, for adapters whose CLI calls back into it."""
    callbacks = callbacks or CallbackServer()
    return {name: adapter(commands.get(name) or adapter.default_command, callbacks)
            for name, adapter in ADAPTERS.items()}
