"""Direct mode: what a final transcript asks for, without a front assistant.

A transcript is one of: "stop" (stop speaking), "cancel" (interrupt the target
task), or a request for an agent. A request that opens with a project's name or
alias set off by a comma or colon ("in backend, run the tests", "API: update
the docs") goes to that project, minus the routing phrase; everything else is sent verbatim. Matching is exact on normalised words;
fuzzy matching comes with the front assistant (M3).
"""
from __future__ import annotations

import re
from dataclasses import dataclass

STOP = {"stop", "stop speaking", "stop talking", "be quiet", "quiet"}
CANCEL = {"cancel", "cancel that", "cancel it", "cancel the task", "cancel task"}
PREPOSITIONS = {"in", "for", "on", "to"}
MAX_NAME_WORDS = 4

WORD = re.compile(r"[\w'.-]+")


@dataclass
class Request:
    text: str
    project_id: int | None = None   # set when the transcript named a project


@dataclass
class Stop:
    pass


@dataclass
class Cancel:
    pass


def parse(transcript: str, projects: list[dict]) -> Request | Stop | Cancel:
    words = " ".join(_squash(w) for w in WORD.findall(transcript))
    if words in STOP:
        return Stop()
    if words in CANCEL:
        return Cancel()
    routed = _route(transcript, projects)
    return routed or Request(transcript.strip())


def _route(transcript: str, projects: list[dict]) -> Request | None:
    tokens = list(WORD.finditer(transcript))
    if not tokens:
        return None
    names = {}
    for project in projects:
        for name in [project["name"], *project["aliases"]]:
            names.setdefault(_squash(name), project["id"])

    leads = [1, 0] if _squash(tokens[0].group()) in PREPOSITIONS else [0]
    for lead in leads:
        for count in range(min(MAX_NAME_WORDS, len(tokens) - lead), 0, -1):
            span = tokens[lead:lead + count]
            project_id = names.get("".join(_squash(t.group()) for t in span))
            if project_id is None:
                continue
            after = transcript[span[-1].end():].lstrip(".")
            # Only a name set off by a comma or colon is a route: "API: update the docs"
            # and "in backend, run the tests" are; "API docs need updating" and "to API
            # clients we send JSON" are requests. A wrong project is worse than none.
            # A name on its own ("in frontend.") is a route with nothing to send.
            if not re.match(r"\s*([,:]|$)", after):
                continue
            rest = re.sub(r"^[\s,:;.-]+", "", after).strip()
            return Request(rest[:1].upper() + rest[1:], project_id)
    return None


def _squash(word: str) -> str:
    """Lower case without spaces, dots, dashes or underscores: "Back-end" and "back end" match "backend"."""
    return re.sub(r"[\s._-]+", "", word.lower()).strip("'")
