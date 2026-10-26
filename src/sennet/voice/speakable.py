"""Agent text made fit to speak, and split into clauses for synthesis (HLD §6.4).

Code blocks, tables, URLs and markdown are never narrated: blocks and tables
become a short note that they are in the timeline, links keep their text, URLs
become "a link", file paths are read as their file name, and a commit hash is "a
commit". Symbols are said as words ("$24" is "24 dollars", "~5%" is "about 5
percent"), and the ones with nothing to say, such as emoji and stray markdown, are
dropped.
"""
from __future__ import annotations

import posixpath
import re

CODE_NOTE = "There's a code block in the timeline."
TABLE_NOTE = "There's a table in the timeline."

# Characters. Each piece is synthesized as a whole utterance, so a cut inside a sentence ends
# the first half with a full stop's fall in pitch. Only sentences too long to synthesize in one
# go (tts.MAX_PHONEMES, near 480 characters of prose, fewer with numbers) are cut, and a short
# pause is left where they are.
MAX_CHUNK = 300
CUT_PAUSE_S = 0.15

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
# A commit hash, as git prints it: 7 to 40 lowercase hex characters, with letters and digits both.
COMMIT = re.compile(r"(?<![\w-])(?=[0-9a-f]*[a-f])(?=[0-9a-f]*[0-9])[0-9a-f]{7,40}(?![\w-])")
FILE_NAME = re.compile(r"^[\w@-]+(?:\.[\w-]+)+$")

LIST_ITEM = re.compile(r"^\s*(?:[-*+]|\d+[.)])\s+")
HEADING = re.compile(r"^\s*#{1,6}\s+")
QUOTE = re.compile(r"^\s*>\s?")
RULE = re.compile(r"^\s*(?:[-*_]\s*){3,}$")
# The version of a reply the agent wrote to be heard (agents.base.SPOKEN_INSTRUCTIONS). At the start,
# or on a line of its own after a short plain lead (up to three lines, no code or tags), which an agent
# sometimes writes first despite the instructions; the lead stays in the message. Anywhere else,
# "<spoken>" is the agent talking about the tags. Markdown.tsx's withoutSpoken matches it the same way.
SPOKEN = re.compile(r"^\s*((?:[^\n`<]*\n){0,3}?)[ \t]*<spoken>\s*(.*?)\s*</spoken>\s*", re.S)

CURRENCIES = {"$": ("dollar", "cent"), "£": ("pound", "pence"), "€": ("euro", "cent"), "¥": ("yen", None),
              "₹": ("rupee", "paise")}
CODES = {"USD": "$", "GBP": "£", "EUR": "€", "JPY": "¥", "INR": "₹"}
SCALES = {"k": "thousand", "m": "million", "mn": "million", "bn": "billion", "b": "billion", "tn": "trillion",
          "thousand": "thousand", "million": "million", "billion": "billion", "trillion": "trillion"}
AMOUNT = r"((?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d+)?)(?:\s?(k|mn|m|bn|b|tn|thousand|million|billion|trillion)\b)?"
MONEY = re.compile(rf"(?<![\w$£€¥₹])(?:US)?([$£€¥₹])\s?{AMOUNT}", re.I)
CODED_MONEY = re.compile(rf"\b{AMOUNT}\s?({'|'.join(CODES)})\b|\b({'|'.join(CODES)})\s?{AMOUNT}", re.I)
# Said as words wherever they stand. Order matters: "->" before "-" ranges, "e.g." before ".".
WORDS = [
    (re.compile(r"\s*(?:->|=>|→|⇒)\s*"), " to "),
    (re.compile(r"\s*(?:<-|←)\s*"), " from "),
    (re.compile(r"\s*(?:<=|≤)\s*(?=\d)"), " at most "),
    (re.compile(r"\s*(?:>=|≥)\s*(?=\d)"), " at least "),
    (re.compile(r"(?<!\w)(?:~|≈|\bapprox\.)\s?(?=\d)"), "about "),
    (re.compile(r"(\d)\s?%"), r"\1 percent"),
    (re.compile(r"(\d)\s?°\s?C\b"), r"\1 degrees Celsius"),
    (re.compile(r"(\d)\s?°\s?F\b"), r"\1 degrees Fahrenheit"),
    (re.compile(r"(\d)\s?°"), r"\1 degrees"),
    (re.compile(r"(\d)(?<!\b0)\s?[x×]\s?(?=\d)"), r"\1 by "),
    (re.compile(r"(\d)\s?[x×](?!\w)"), r"\1 times"),
    (re.compile(r"(?<=\d)\s?\*\s?(?=\d)"), " times "),
    (re.compile(r"(?<![\w&])#(\d+)"), r"number \1"),
    (re.compile(r"(?<![\w.:-])(\d+(?:\.\d+)?)\s?[–-]\s?(\d+(?:\.\d+)?)(?![\d-]|[.:]\d)"), r"\1 to \2"),
    (re.compile(r"\s+[+]\s+"), " plus "),
    (re.compile(r"\s*&\s*"), " and "),
    (re.compile(r"\s+(?:--|—|–)\s+|(?<=\w)(?:--|—)(?=\w)"), ", "),
    (re.compile(r"\be\.g\.(?=\W)", re.I), "for example"),
    (re.compile(r"\bi\.e\.(?=\W)", re.I), "that is"),
    (re.compile(r"\betc\.(?=\s+[a-z])"), "et cetera"),
    (re.compile(r"\betc\.?"), "et cetera."),
    (re.compile(r"\bvs\.?(?=\s)", re.I), "versus"),
    (re.compile(r"(?<=\w)\s?@\s?(?=\d)"), " at "),
]
# Emoji, pictographs, arrows and box drawing, and markdown left over once the rest is read.
UNSPOKEN = re.compile(r"[\u2190-\u21ff\u2300-\u23ff\u2460-\u27bf\u2b00-\u2bff\U0001f000-\U0001faff\ufe0f\u200d"
                      r"*`|\\^{}\[\]<>=#]")


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
    text = COMMIT.sub("a commit", text)
    text = _emphasis(text)
    return _sentences(_symbols(text))


def spoken_part(markdown: str) -> str | None:
    """The agent's version of a reply to be heard, if it wrote one."""
    found = SPOKEN.search(markdown)
    return found.group(2) if found and found.group(2) else None


def without_spoken(markdown: str) -> str:
    return SPOKEN.sub(r"\1", markdown, count=1).strip()


def clauses(text: str) -> list[str]:
    """Sentences, with only the very long ones split at clause marks."""
    pieces: list[str] = []
    for sentence in re.split(r"(?<=[.!?])\s+(?=[A-Z0-9\"'(])", text.strip()):
        pieces += _split(sentence, MAX_CHUNK)
    return [p for p in pieces if p]


def pause_after(piece: str) -> float:
    """Silence to leave after a piece: a breath where a sentence was cut, none at its end."""
    return 0.0 if piece.rstrip("\"')").endswith((".", "!", "?", "…")) else CUT_PAUSE_S


# Inline pieces

def _inline_code(code: str) -> str:
    code = code.strip()
    if COMMIT.fullmatch(code):
        return "a commit"
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


def _symbols(text: str) -> str:
    text = MONEY.sub(lambda m: _money(m.group(1), m.group(2), m.group(3)), text)
    text = CODED_MONEY.sub(lambda m: _money(CODES[(m.group(3) or m.group(4)).upper()],
                                            m.group(1) or m.group(5), m.group(2) or m.group(6)), text)
    for pattern, words in WORDS:
        text = pattern.sub(words, text)
    text = "\n".join(_unspoken(line) for line in text.split("\n"))
    return re.sub(r"[ \t]{2,}", " ", text)


def _money(symbol: str, amount: str, scale: str | None) -> str:
    """"$24" is "24 dollars", "£3.50" "3 pounds 50", "$1.2bn" "1.2 billion dollars"."""
    unit, small = CURRENCIES[symbol]
    whole, _, fraction = amount.partition(".")
    if scale:
        return f"{amount} {SCALES[scale.lower()]} {unit}{'' if unit == 'yen' else 's'}"
    plural = "" if whole == "1" or unit == "yen" else "s"
    if not fraction or not small or set(fraction) == {"0"}:
        return f"{whole}{f'.{fraction}' if fraction and not small else ''} {unit}{plural}"
    if len(fraction) != 2:
        return f"{amount} {unit}s"
    if whole == "0":
        return f"{int(fraction)} {small}{'s' if small == 'cent' and fraction != '01' else ''}"
    return f"{whole} {unit}{plural} {int(fraction)}" if symbol == "£" else \
        f"{whole} {unit}{plural} and {int(fraction)} {small}{'s' if small == 'cent' and fraction != '01' else ''}"


def _unspoken(line: str) -> str:
    """Drop what can't be said, keeping a list or heading mark at the start of the line."""
    lead = LIST_ITEM.match(line) or HEADING.match(line) or QUOTE.match(line)
    start = lead.end() if lead else 0
    return line[:start] + UNSPOKEN.sub(" ", line[start:])


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
    return sentence if not sentence or sentence[-1] in ".!?:;…" else sentence + "."


def _split(sentence: str, limit: int) -> list[str]:
    """Cut at clause marks (, ; : —) so pieces fit `limit`, else at the last space."""
    pieces = []
    rest = sentence.strip()
    while len(rest) > limit:
        marks = [m.end() for m in re.finditer(r"[,;:—–]\s", rest[:limit + 1])]
        marks = [m for m in marks if m >= 15]
        cut = marks[-1] if marks else rest.rfind(" ", 15, limit + 1)
        if cut <= 0:
            cut = limit
        pieces.append(rest[:cut].strip())
        rest = rest[cut:].strip()
    return pieces + [rest] if rest else pieces
