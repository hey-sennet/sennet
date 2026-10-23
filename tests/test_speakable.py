"""Agent text made fit to speak, and its clauses."""
import pytest

from sennet.voice.speakable import (CODE_NOTE, MAX_CHUNK, TABLE_NOTE, clauses, for_speech, pause_after, spoken_part,
                                    without_spoken)

CODEX_REPLY = """I fixed the failing test in `tests/test_tasks.py`.

**What changed**
- `TaskManager.cancel` now waits for the interrupt (see [the docs](https://example.com/docs)).
- Added a regression test.

```python
await manager.cancel(task_id)
```

Run `uv run pytest` to check. Details: https://github.com/example/repo/pull/12."""


@pytest.mark.parametrize("markdown, spoken", [
    (CODEX_REPLY,
     "I fixed the failing test in test_tasks.py. What changed. TaskManager.cancel now waits for the interrupt "
     f"(see the docs). Added a regression test. {CODE_NOTE} Run uv run pytest to check. Details: a link."),
    ("All 40 tests pass.", "All 40 tests pass."),
    ("# Summary\nDone", "Summary. Done."),
    ("Edited `src/sennet/voice/session.py` and ./README.md.", "Edited session.py and README.md."),
    ("Use read/write mode and/or retry.", "Use read/write mode and/or retry."),
    ("Set `x = foo(1, 2)` first.", "Set some code first."),
    ("The branch `main` is clean.", "The branch main is clean."),
    ("A line that wraps\nonto the next one.", "A line that wraps onto the next one."),
    ("Two blocks:\n```\na\n```\n```\nb\n```\nDone.", f"Two blocks: {CODE_NOTE} Done."),
    ("| a | b |\n|---|---|\n| 1 | 2 |\nThat's all.", f"{TABLE_NOTE} That's all."),
    ("> quoted text\n\n---\n\n*really* __done__ ~~not~~", "quoted text. really done not."),
    ("Keep snake_case_names and multiply 2*3*4.", "Keep snake_case_names and multiply 2 times 3 times 4."),
    # Symbols said as words
    ("It costs $24, or £24.50, or €3.", "It costs 24 dollars, or 24 pounds 50, or 3 euros."),
    ("Raised $1.2bn; fees were $0.05 and $1.", "Raised 1.2 billion dollars; fees were 5 cents and 1 dollar."),
    ("Paid 30 GBP and US$1,250.", "Paid 30 pounds and 1,250 dollars."),
    ("Up ~5% at 21°C.", "Up about 5 percent at 21 degrees Celsius."),
    ("Build -> test & deploy, e.g. 3-5 times.", "Build to test and deploy, for example 3 to 5 times."),
    ("Fixed #123 — a 2x speedup at 1920x1080.", "Fixed number 123, a 2 times speedup at 1920 by 1080."),
    ("✅ All 40 tests pass 🎉", "All 40 tests pass."),
    ("Released 2026-10-22, v1.2-3.", "Released 2026-10-22, v1.2-3."),
    ("See ![diagram](x.png) <b>now</b>.", "See now."),
    ("```\nunterminated code", CODE_NOTE),
    # Commit hashes, in backticks or bare; not words, numbers or ids with dashes
    ("I committed the fix as `69f7042` on main.", "I committed the fix as a commit on main."),
    ("Reverted 1300d9c and 66a27b5c0e9f1d2a3b4c5d6e7f80912a3b4c5d6e.", "Reverted a commit and a commit."),
    ("The deadbeef build ran 1234567 times.", "The deadbeef build ran 1234567 times."),
    ("Session 201c9aae-1df3-4047-bd74-da7cdbe92c68 resumed.", "Session 201c9aae-1df3-4047-bd74-da7cdbe92c68 resumed."),
    ("1. First\n2. Second", "First. Second."),
])
def test_for_speech(markdown, spoken):
    assert for_speech(markdown) == spoken


def test_clauses_split_sentences():
    assert clauses("All 40 tests pass. I changed two files! Done?") == [
        "All 40 tests pass.", "I changed two files!", "Done?"]


def test_clauses_keep_abbreviations_and_decimals():
    assert clauses("Use version 3.5, e.g. the latest one.") == ["Use version 3.5, e.g. the latest one."]


def test_sentences_are_not_cut_to_start_sooner():
    first, *rest = clauses("I looked at the failing tests in the backend project, and the cause was a race "
                           "in the task manager. It is fixed now.")
    assert first == "I looked at the failing tests in the backend project, and the cause was a race in the task manager."
    assert rest == ["It is fixed now."]


def test_long_sentences_are_cut_to_the_limit():
    sentence = "This goes on, " * 30 + "and ends."
    pieces = clauses("Short start. " + sentence)
    assert pieces[0] == "Short start."
    assert all(len(p) <= MAX_CHUNK for p in pieces)
    assert " ".join(pieces).replace("  ", " ") == ("Short start. " + sentence).strip()


def test_a_pause_is_left_only_where_a_sentence_was_cut():
    pieces = clauses("This goes on, " * 30 + "and ends. Done.")
    assert [pause_after(p) > 0 for p in pieces] == [True] * (len(pieces) - 2) + [False, False]
    assert pause_after('He said "done."') == 0


def test_the_spoken_version_is_found_and_left_out_of_the_reply():
    reply = "<spoken>\nAll 12 tests pass.\n</spoken>\n\n**Done.** All 12 tests pass."
    assert spoken_part(reply) == "All 12 tests pass."
    assert without_spoken(reply) == "**Done.** All 12 tests pass."
    assert spoken_part("All done.") is None and spoken_part("<spoken></spoken>Done.") is None
    # Only at the start: elsewhere it is the agent talking about the tags.
    about = "Put it in `<spoken>` tags.\n\n`<spoken>Hi</spoken>` is an example."
    assert spoken_part(about) is None and without_spoken(about) == about
