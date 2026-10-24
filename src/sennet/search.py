"""Searching tasks by what was said in them: their titles and every message.

Exact matches come first: every word of three letters or more, found anywhere, even inside
a longer word ("auth" finds "authentication"), ranked by the index (db.Database.search).
If that leaves room, near misses fill it, for typos: candidates sharing three-letter
pieces with the query, kept when each query word is within a few letters of a word in them
(one letter in four may differ: added, dropped or changed).
"""
from __future__ import annotations

import re

from .db import Database

LIMIT = 20
CANDIDATES = 400         # rows a fuzzy search looks at
CLOSE_ENOUGH = 0.75      # 1 - letters that differ / the longer word's length, for the nearest word
CONTEXT = 70             # characters either side of a match in its snippet
WORD = re.compile(r"\w+")
TAGS = re.compile(r"</?spoken>")


def search(db: Database, query: str, limit: int = LIMIT) -> list[dict]:
    """The best tasks for `query`, each with a snippet of where it matched and the spans to highlight."""
    words = [w for w in query.lower().split() if len(w) >= 3]
    if not words:
        return []
    results, seen = [], set()
    for task, text in db.search(query, limit):
        seen.add(task["id"])
        results.append(_result(task, text, words))
    if len(results) < limit:
        scored = {}
        for task, text in db.search_candidates(query, CANDIDATES):
            if task["id"] in seen:
                continue
            score, near = _closeness(words, text)
            if score >= CLOSE_ENOUGH and score > scored.get(task["id"], (0,))[0]:
                scored[task["id"]] = (score, task, text, near)
        for score, task, text, near in sorted(scored.values(), key=lambda s: -s[0])[:limit - len(results)]:
            results.append(_result(task, text, near))
    return results


def _grams(word: str) -> set[str]:
    return {word[i:i + 3] for i in range(len(word) - 2)}


def _closeness(words: list[str], text: str) -> tuple[float, list[str]]:
    """How close the text comes to every query word, the weakest counting (0 to 1), with the
    text's nearest word to each."""
    tokens = {t for t in WORD.findall(text.lower()) if len(t) >= 3}
    worst, near = 1.0, []
    for word in words:
        grams = _grams(word)
        best, closest = 0.0, ""
        for token in tokens:
            if abs(len(token) - len(word)) > len(word) // 4 + 1 or not grams & _grams(token):
                continue  # too different in length, or nothing in common, to be close
            score = 1 - _distance(word, token) / max(len(word), len(token))
            if score > best:
                best, closest = score, token
        worst = min(worst, best)
        near.append(closest)
    return worst, near


def _distance(a: str, b: str) -> int:
    """Letters added, dropped or changed to make one word the other (Levenshtein)."""
    previous = list(range(len(b) + 1))
    for i, x in enumerate(a, 1):
        current = [i]
        for j, y in enumerate(b, 1):
            current.append(min(previous[j] + 1, current[j - 1] + 1, previous[j - 1] + (x != y)))
        previous = current
    return previous[-1]


def _result(task: dict, text: str, words: list[str]) -> dict:
    snippet, highlights = _snippet(text, [w for w in words if w])
    return {"task": task, "snippet": snippet, "highlights": highlights}


def _snippet(text: str, words: list[str]) -> tuple[str, list[list[int]]]:
    """Some text either side of the first match, on one line, and where each word appears in it."""
    text = " ".join(TAGS.sub("", text).split())
    lower = text.lower()
    found = [i for w in words if (i := lower.find(w)) >= 0]
    start = max(0, min(found, default=0) - CONTEXT)
    end = min(len(text), (min(found, default=0)) + CONTEXT * 2)
    if start > 0:  # begin at a word
        start = text.find(" ", start) + 1 or start
    snippet = ("…" if start > 0 else "") + text[start:end].strip() + ("…" if end < len(text) else "")
    shown = snippet.lower()
    highlights = sorted([m.start(), m.end()] for w in words for m in re.finditer(re.escape(w), shown))
    return snippet, highlights
