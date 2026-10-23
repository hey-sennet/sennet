"""The agents Agent Talk can drive. Every adapter is listed here and nowhere else.

The first one listed is the default for new projects.
"""
from __future__ import annotations

from .base import Agent, ModelOption
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


class ModelError(ValueError):
    pass


async def check_model(agent: Agent | None, model: str | None, effort: str | None, inherited: str | None = None):
    """Refuse a model or effort the agent doesn't list. An effort alone is for `inherited`, the
    model otherwise in use, or the agent's default."""
    if agent is None or (model is None and effort is None):
        return
    model_given, model = model is not None, model or inherited
    try:
        options: list[ModelOption] = await agent.models()
    except Exception as error:
        raise ModelError(f"couldn't list {agent.title}'s models: {error}")
    chosen = next((o for o in options if o.id == model), None) if model else \
        next((o for o in options if o.default), None)
    if model and chosen is None and model_given:
        raise ModelError(f"{agent.title} has no model {model!r}")
    if effort and chosen and effort not in chosen.efforts:
        raise ModelError(f"{chosen.title} doesn't take {effort!r} effort")
