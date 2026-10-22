"""The agents Agent Talk can drive. Every adapter is listed here and nowhere else.

The first one listed is the default for new projects.
"""
from __future__ import annotations

from .base import Agent
from .codex import CodexAgent

ADAPTERS: dict[str, type[Agent]] = {CodexAgent.name: CodexAgent}
NAMES: tuple[str, ...] = tuple(ADAPTERS)
DEFAULT = NAMES[0]


def build(commands: dict[str, list[str]]) -> dict[str, Agent]:
    """One of each adapter, started with its configured command or its own default."""
    return {name: adapter(commands.get(name) or adapter.default_command) for name, adapter in ADAPTERS.items()}
