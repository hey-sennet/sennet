"""Long agent replies, cut for speech (HLD §6.3).

A reply short enough to listen to is spoken as it is. The agent's own spoken
version comes first, when it wrote one (`speakable.spoken_part`); this is for the
replies it didn't write one for, which are cut to their opening sentences. A reply
written for reading often opens with a heading, a table or a code block; those are
skipped, so the opening starts at its first prose.
"""
from __future__ import annotations

import re

from .speakable import FENCE, RULE, TABLE, for_speech, without_spoken

SHORT_CHARS = 600           # spoken as it is: at most this long and SHORT_SENTENCES sentences
SHORT_SENTENCES = 6
OPENING_SENTENCES = 4       # spoken for a longer reply

SENTENCE = re.compile(r"(?<=[.!?])\s+(?=[A-Z0-9\"'(])")
# What a reply can open with before its prose: a heading, a line in bold alone ("**Summary**"),
# a rule, a code block or a table.
HEADING_LINE = re.compile(r"[ \t]*(?:#{1,6}[ \t].*|\*\*[^*\n]+\*\*:?|__[^_\n]+__:?)[ \t]*$", re.M)
RULE_LINE = re.compile(RULE.pattern, re.M)
LEADS = (HEADING_LINE, RULE_LINE, FENCE, TABLE)


def sentences(text: str) -> list[str]:
    return [s for s in SENTENCE.split(text.strip()) if s]


def is_short(spoken: str) -> bool:
    return len(spoken) <= SHORT_CHARS and len(sentences(spoken)) <= SHORT_SENTENCES


def opening(spoken: str) -> str:
    return " ".join(sentences(spoken)[:OPENING_SENTENCES])


def without_lead(markdown: str) -> str:
    """The reply from its first prose on."""
    text = markdown.lstrip("\n")
    while found := next((m for p in LEADS if (m := p.match(text))), None):
        text = text[found.end():].lstrip("\n")
    return text


def shorten(markdown: str) -> str:
    """What to speak for an agent message."""
    markdown = without_spoken(markdown)
    spoken = for_speech(markdown)
    if is_short(spoken):
        return spoken
    return opening(for_speech(without_lead(markdown)) or spoken)
