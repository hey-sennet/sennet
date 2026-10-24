"""The agents' usage limits, and how they are put into words.

Limits belong to the account an agent is logged in to, not to a task. They arrive two ways:
the agent reports them during its turns (a `limits` event, which may hold only some
windows), and Sennet asks for them (`Agent.limits`), which uses none. Asking takes a
process, so it happens only when a page loads or looks at them, and they are older than
`STALE_S`.
"""
from __future__ import annotations

import asyncio
import datetime
import logging
import time
from dataclasses import asdict

from .agents.base import Agent

log = logging.getLogger(__name__)

STALE_S = 300
ASK_TIMEOUT_S = 30
# Used percentages at which a window is worth a word: once at each, per window, until it resets.
WARN_AT = (80, 95)


class Limits:
    def __init__(self, agents: dict[str, Agent], publish):
        self.agents = agents
        self.publish = publish
        self.known: dict[str, dict] = {}         # agent → {"windows": {id: window}, "checked_at"}
        self.asking: dict[str, asyncio.Task] = {}

    def snapshot(self) -> dict[str, dict]:
        return {name: self.present(name) for name in self.known}

    def present(self, agent: str) -> dict:
        known = self.known[agent]
        return {"windows": sorted(known["windows"].values(), key=_order), "checked_at": known["checked_at"]}

    def windows(self, agent: str) -> list[dict]:
        return self.present(agent)["windows"] if agent in self.known else []

    async def report(self, agent: str, windows: list[dict], complete: bool = False):
        """Windows the agent reported. A report may leave windows out, which keep their last values,
        unless it is `complete`, as an answer to asking is."""
        known = self.known.setdefault(agent, {"windows": {}, "checked_at": 0.0})
        if complete:
            known["windows"] = {}
        for window in windows:
            known["windows"][window["id"]] = dict(window)
        known["checked_at"] = time.time()
        await self.publish({"type": "limits", "agent": agent, "limits": self.present(agent)})

    def refresh(self, force: bool = False):
        """Ask each agent whose limits are older than `STALE_S`, in the background."""
        for name in self.agents:
            if force or time.time() - self.known.get(name, {}).get("checked_at", 0.0) >= STALE_S:
                if name not in self.asking:
                    job = asyncio.ensure_future(self._ask(name))
                    self.asking[name] = job
                    job.add_done_callback(lambda _, name=name: self.asking.pop(name, None))

    async def _ask(self, name: str):
        asked = getattr(self.agents[name], "limits", None)
        if asked is None:
            return
        try:
            windows = await asyncio.wait_for(asked(), ASK_TIMEOUT_S)
        except Exception as error:
            log.info("couldn't ask %s for its limits: %s", name, error or type(error).__name__)
            return
        if windows is None:
            if self.known.pop(name, None) is not None:  # logged out, or onto an API key
                await self.publish({"type": "limits", "agent": name, "limits": None})
            return
        await self.report(name, [asdict(w) for w in windows], complete=True)

    async def close(self):
        for job in list(self.asking.values()):
            job.cancel()
        await asyncio.gather(*self.asking.values(), return_exceptions=True)

    def reached(self, agent: str) -> dict | None:
        """The full window that frees up last, if any is full."""
        full = [w for w in self.windows(agent) if w["used"] >= 100]
        return max(full, key=lambda w: w["resets_at"] or 0) if full else None


class Warnings:
    """Which windows have been warned about, so each level is said once until the window resets."""

    def __init__(self):
        self.said: dict[tuple[str, str], int] = {}

    def due(self, agent: str, windows: list[dict]) -> list[dict]:
        """The windows past a warning level not yet said, each marked as said."""
        due = []
        for window in windows:
            key = (agent, window["id"])
            level = max((at for at in WARN_AT if window["used"] >= at), default=0)
            if level == 0 or window["used"] >= 100:
                if window["used"] < WARN_AT[0]:
                    self.said.pop(key, None)  # reset: warn again next time round
                continue
            if self.said.get(key, 0) < level:
                self.said[key] = level
                due.append(window)
        return due


def limit_reached(agent_title: str, window: str | None, resets_at: float | None, now: float | None = None) -> str:
    """"Claude Code's 5-hour limit is reached. It resets at 4:30 pm.", for speech and the task."""
    which = f"{agent_title}'s {window} limit" if window else f"{agent_title}'s usage limit"
    when = f" It resets {when_resets(resets_at, now)}." if resets_at else ""
    return f"{which} is reached.{when}"


def limit_warning(agent_title: str, window: dict, now: float | None = None) -> str:
    when = f" It resets {when_resets(window['resets_at'], now)}." if window["resets_at"] else ""
    return f"You've used {window['used']} percent of {agent_title}'s {window['title']} limit.{when}"


def when_resets(at: float, now: float | None = None) -> str:
    """"at 4 pm", "tomorrow at 9:30 am", "on Sunday at 9 am" or "on 3 November", in this machine's time."""
    moment = datetime.datetime.fromtimestamp(round(at / 60) * 60)
    today = datetime.datetime.fromtimestamp(now if now is not None else time.time()).date()
    clock = moment.strftime("%I:%M %p" if moment.minute else "%I %p").lstrip("0").lower()
    days = (moment.date() - today).days
    if days <= 0:
        return f"at {clock}"
    if days == 1:
        return f"tomorrow at {clock}"
    if days < 7:
        return f"on {moment.strftime('%A')} at {clock}"
    return f"on {moment.day} {moment.strftime('%B')}"


def _order(window: dict) -> tuple:
    """The five-hour window first, then the weekly ones, then any other."""
    return ({"session": 0, "weekly": 1}.get(window["id"], 2), window["id"])
