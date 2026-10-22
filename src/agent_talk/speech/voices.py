"""Kokoro's English voices, best first.

Grades are Kokoro's own estimates of each voice's training data, from
https://huggingface.co/hexgrad/Kokoro-82M/blob/main/VOICES.md. Voices in other
languages are in the voices file too, but Agent Talk is English only.
"""
from __future__ import annotations

from dataclasses import dataclass

DEFAULT = "af_heart"
PREVIEW = "Backend finished. All forty tests pass, and two files changed."


@dataclass(frozen=True)
class Voice:
    id: str
    grade: str

    @property
    def name(self) -> str:
        return self.id.split("_", 1)[1].capitalize()

    @property
    def accent(self) -> str:
        return "British" if self.id.startswith("b") else "American"

    @property
    def gender(self) -> str:
        return "female" if self.id[1] == "f" else "male"

    @property
    def good(self) -> bool:
        """Graded C or better: listed first in setup."""
        return self.grade[0] in "ABC" and self.grade != "C-"


_GRADES = {
    "af_heart": "A", "af_bella": "A-", "af_nicole": "B-", "bf_emma": "B-",
    "af_aoede": "C+", "af_kore": "C+", "af_sarah": "C+", "am_fenrir": "C+", "am_michael": "C+", "am_puck": "C+",
    "af_alloy": "C", "af_nova": "C", "bf_isabella": "C", "bm_fable": "C", "bm_george": "C",
    "af_sky": "C-", "bm_lewis": "D+",
    "af_jessica": "D", "af_river": "D", "am_echo": "D", "am_eric": "D", "am_liam": "D", "am_onyx": "D",
    "bf_alice": "D", "bf_lily": "D", "bm_daniel": "D", "am_santa": "D-", "am_adam": "F+",
}
VOICES = [Voice(id, grade) for id, grade in _GRADES.items()]  # best first
