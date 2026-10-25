"""Task titles: from the user's first words at first, then from the agent's own.

Neither agent names a conversation it holds for another program (Claude Code and Codex do
that only in their own interfaces), so the instructions (agents.base.SPOKEN_INSTRUCTIONS)
ask the agent to start its first message with a short title in `<sennet-title>` tags; the
tag isn't HTML, so nothing else writes it. The task manager takes the title out of the
message, both as it streams and once it's complete, and renames the task with it.

Until then, or if the agent writes none, a task is titled from what was said, without the
fillers speech recognition keeps ("Um, okay so the build fails" is "The build fails").
"""
from __future__ import annotations

import re

OPEN, CLOSE = "<sennet-title>", "</sennet-title>"
TAG = re.compile(r"\s*<sennet-title>(.*?)</sennet-title>\s*", re.S)
LENGTH = 80
HOLD_CHARS = 200        # streamed text held while a title may be opening; past this it can't be one
FILLER = r"(?:u+m+|u+h+m*|e+r+m*|h+m+)"
LEADING = re.compile(rf"^(?:(?:{FILLER}|okay|ok|so|well|alright|yeah)\b[\s,.!?…-]*)+", re.I)
INNER = re.compile(rf"(?:,\s*)?\b{FILLER}\b[,.]?\s*", re.I)


def shorten(text: str) -> str:
    text = " ".join(text.split())
    return text if len(text) <= LENGTH else text[:LENGTH - 3].rstrip() + "…"


def from_request(text: str) -> str:
    """A title from the first line of what the user said or typed, without speech fillers."""
    line = text.strip().splitlines()[0]
    start = LEADING.sub("", line)
    cleaned = INNER.sub(" ", start).strip()
    if not cleaned:
        return shorten(line)
    if start != line:  # it began with a filler: begin with a capital in its place
        cleaned = cleaned[0].upper() + cleaned[1:]
    return shorten(cleaned)


def take(text: str) -> tuple[str | None, str]:
    """The agent's title from a message, if it wrote one, and the message without any."""
    found = TAG.search(text)
    if found is None:
        return None, text
    title = shorten(found.group(1).strip().strip("\"'."))
    return title or None, TAG.sub(" ", text).strip()


class Stream:
    """One streamed message, with a title at its start held back from the deltas."""

    def __init__(self):
        self.held: str | None = ""      # None once past where a title could be

    def feed(self, delta: str) -> tuple[str | None, str]:
        """The title, once its tag closes, and what of the text to pass on now."""
        if self.held is None:
            return None, delta
        self.held += delta
        start = self.held.lstrip()
        if start.startswith(OPEN):
            if CLOSE not in start:
                if len(self.held) <= HOLD_CHARS:
                    return None, ""
                return self._release()
            found = TAG.match(start)
            self.held = None
            return take(found.group())[0], start[found.end():]
        if OPEN.startswith(start) and len(self.held) <= HOLD_CHARS:
            return None, ""     # whitespace, or the tag so far
        return self._release()

    def _release(self) -> tuple[None, str]:
        text, self.held = self.held, None
        return None, text
