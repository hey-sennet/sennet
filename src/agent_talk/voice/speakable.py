"""Agent text made fit to speak, and split into clauses for synthesis (HLD §6.4).

Code blocks, tables, URLs and markdown are never narrated: blocks and tables
become a short note that they are in the timeline, links keep their text, URLs
become "a link", and file paths are read as their file name.
"""
from __future__ import annotations

import posixpath
import re

CODE_NOTE = "There's a code block in the timeline."
TABLE_NOTE = "There's a table in the timeline."

FIRST_CHUNK = 60    # characters; Kokoro plays nothing until a whole chunk is synthesized
MAX_CHUNK = 160

FENCE = re.compile(r"^[ \t]*(```|~~~)[^\n]*\n.*?(?:^[ \t]*\1[ \t]*$|\Z)", re.S | re.M)
TABLE = re.compile(r"(?:^[ \t]*\|.*\|[ \t]*$\n?){2,}", re.M)
IMAGE = re.compile(r"!\[[^\]]*\]\([^)]*\)")
LINK = re.compile(r"\[([^\]]+)\]\([^)]*\)")
AUTOLINK = re.compile(r"<(https?://[^>]+)>")
URL = re.compile(r"\bhttps?://[^\s<>()\[\]]+[^\s<>()\[\].,;:!?'\"]")
INLINE_CODE = re.compile(r"`([^`\n]+)`")
HTML = re.compile(r"</?[a-zA-Z][^>]*>")
PATH = re.compile(r"(?<![\w/.~-])(?:~|\.{1,2})?/?(?:[\w.@-]+/)+[\w.@-]+")
WORDS_ONLY = re.compile(r"^[A-Za-z][\w -]{0,40}$")
FILE_NAME = re.compile(r"^[\w@-]+(?:\.[\w-]+)+$")

LIST_ITEM = re.compile(r"^\s*(?:[-*+]|\d+[.)])\s+")
HEADING = re.compile(r"^\s*#{1,6}\s+")
QUOTE = re.compile(r"^\s*>\s?")
RULE = re.compile(r"^\s*(?:[-*_]\s*){3,}$")


def for_speech(markdown: str) -> str:
    text = markdown.replace("\r\n", "\n")
    text = FENCE.sub(f"\n\n{CODE_NOTE}\n\n", text)
    text = TABLE.sub(f"\n\n{TABLE_NOTE}\n\n", text)
    text = IMAGE.sub("", text)
    text = LINK.sub(r"\1", text)
    text = AUTOLINK.sub("a link", text)
    text = URL.sub("a link", text)
    text = INLINE_CODE.sub(lambda m: _inline_code(m.group(1)), text)
    text = HTML.sub("", text)
    text = PATH.sub(lambda m: _path(m.group()), text)
    text = _emphasis(text)
    return _sentences(text)


def clauses(text: str) -> list[str]:
    """Sentences, long ones split at clause marks, with a short first chunk."""
    pieces: list[str] = []
    for sentence in re.split(r"(?<=[.!?])\s+(?=[A-Z0-9\"'(])", text.strip()):
        pieces += _split(sentence, MAX_CHUNK)
    pieces = [p for p in pieces if p]
    if pieces and len(pieces[0]) > FIRST_CHUNK:
        pieces[:1] = _split(pieces[0], FIRST_CHUNK, first_only=True)
    return pieces


# Inline pieces

def _inline_code(code: str) -> str:
    code = code.strip()
    if FILE_NAME.match(code) or "/" in code and PATH.fullmatch(code):
        return _path(code)
    if WORDS_ONLY.match(code) and len(code.split()) <= 4:
        return code  # a command or name worth hearing: `npm test`, `main`
    return "some code"


def _path(path: str) -> str:
    """A path reads as its file name; a bare directory path or a ratio is left alone."""
    name = posixpath.basename(path.rstrip("/"))
    leading = path.startswith(("/", "./", "../", "~/"))
    if "." in name.strip(".") or leading or path.count("/") >= 2:
        return name or path
    return path  # "and/or", "read/write"


def _emphasis(text: str) -> str:
    text = re.sub(r"(\*\*|__)(?=\S)(.+?)(?<=\S)\1", r"\2", text)
    text = re.sub(r"(?<![\w*])\*(?=\S)(.+?)(?<=\S)\*(?![\w*])", r"\1", text)
    text = re.sub(r"(?<!\w)_(?=\S)(.+?)(?<=\S)_(?!\w)", r"\1", text)
    return re.sub(r"~~(.+?)~~", r"\1", text)


# Lines and sentences

def _sentences(text: str) -> str:
    """Join lines into speakable sentences. Headings, list items and notes end their
    own sentence; wrapped prose lines run on."""
    units: list[str] = []
    paragraph: list[str] = []

    def flush():
        if paragraph:
            units.append(_end(" ".join(paragraph)))
            paragraph.clear()

    for raw in text.split("\n"):
        if RULE.match(raw):
            flush()
            continue
        line = QUOTE.sub("", raw)
        standalone = bool(LIST_ITEM.match(line) or HEADING.match(line))
        line = HEADING.sub("", LIST_ITEM.sub("", line)).strip()
        if not line:
            flush()
            continue
        if standalone or line in (CODE_NOTE, TABLE_NOTE):
            flush()
            units.append(_end(line))
        else:
            paragraph.append(line)
    flush()

    spoken: list[str] = []
    for unit in units:
        if unit in (CODE_NOTE, TABLE_NOTE) and spoken and spoken[-1] == unit:
            continue  # two code blocks in a row get one note
        spoken.append(unit)
    return re.sub(r"\s+", " ", " ".join(spoken)).strip()


def _end(sentence: str) -> str:
    sentence = sentence.strip()
    return sentence if not sentence or sentence[-1] in ".!?:;" else sentence + "."


def _split(sentence: str, limit: int, first_only: bool = False) -> list[str]:
    """Cut at clause marks (, ; : —) so pieces fit `limit`, else at the last space.
    With `first_only`, cut once, near the start, and keep the rest whole."""
    pieces = []
    rest = sentence.strip()
    while len(rest) > limit:
        marks = [m.end() for m in re.finditer(r"[,;:—–]\s", rest[:limit + 1])]
        marks = [m for m in marks if m >= 15]
        if first_only and not marks and len(rest) <= 2 * limit:
            break  # cutting mid-clause costs more in prosody than it saves in latency
        cut = marks[0 if first_only else -1] if marks else rest.rfind(" ", 15, limit + 1)
        if cut <= 0:
            if first_only:
                break  # one long word: nothing sensible to cut
            cut = limit
        pieces.append(rest[:cut].strip())
        rest = rest[cut:].strip()
        if first_only:
            break
    return pieces + [rest] if rest else pieces
