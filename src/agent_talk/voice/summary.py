"""Long agent replies, cut for speech (HLD §6.3).

A reply short enough to listen to is spoken as it is. The agent's own spoken
version comes first, when it wrote one (`speakable.spoken_part`); this is for the
replies it didn't write one for, which are cut to their opening sentences.
"""
from __future__ import annotations

import re

from .speakable import for_speech, without_spoken

SHORT_CHARS = 600           # spoken as it is: at most this long and SHORT_SENTENCES sentences
SHORT_SENTENCES = 6
OPENING_SENTENCES = 4       # spoken for a longer reply

SENTENCE = re.compile(r"(?<=[.!?])\s+(?=[A-Z0-9\"'(])")


def sentences(text: str) -> list[str]:
    return [s for s in SENTENCE.split(text.strip()) if s]


def is_short(spoken: str) -> bool:
    return len(spoken) <= SHORT_CHARS and len(sentences(spoken)) <= SHORT_SENTENCES


def opening(spoken: str) -> str:
    return " ".join(sentences(spoken)[:OPENING_SENTENCES])


def shorten(markdown: str) -> str:
    """What to speak for an agent message."""
    spoken = for_speech(without_spoken(markdown))
    return spoken if is_short(spoken) else opening(spoken)
