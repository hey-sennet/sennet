# Token cost of the spoken part

What asking for a `<spoken>` part costs: `spoken.py` sends the same three requests with and
without `SPOKEN_INSTRUCTIONS`, interleaved, a new session each, through Sennet's adapters,
and records each turn's usage and how long its spoken part is. Each turn is a real request
on your subscription; it never touches the running service.

```sh
.venv/bin/python spikes/tokens/spoken.py /some/scratch/dir --runs 2
.venv/bin/python spikes/tokens/spoken.py /some/scratch/dir --summary
```

## Results, 2026-10-24

Three explanation requests with no tools ("explain how a hash map handles collisions…"),
two runs each, both agents on their default models. Medians:

| | Output tokens | Input + cache | Reply | Spoken part |
|---|---|---|---|---|
| Claude, with | 1810 | 21,760 | 5,000 chars | 520 chars |
| Claude, without | 2050 | 21,035 | 5,420 chars | — |
| Codex, with | 336 | 27,432 | 1,616 chars | none written |
| Codex, without | 339 | 26,886 | 1,588 chars | — |

- **Output:** no measurable cost. Claude's replies vary by a few hundred tokens between
  runs either way, and were if anything shorter with the instructions. A spoken part of
  about 520 characters is roughly 130 tokens, inside that noise. Neither agent reported
  thinking tokens here (Codex's reasoning count was 0 at its default effort).
- **Input:** the instructions add about 550 (Codex) to 700 (Claude) tokens to every turn,
  almost all of it read from the prompt cache. Small next to the ~21–27k each agent already
  starts with.
- **Codex wrote no spoken part at all**, in six of six answers of 1,200 to 2,500
  characters, though the instructions ask for one past three sentences. Its answers were
  ordinary markdown, tables included, so the voice fell back to their first four sentences
  (`summary.shorten`). Worth checking on real work, where it reports an outcome; the
  instructions may need to be firmer for Codex.

So shortening the instructions, or sending them only when a voice client is listening,
isn't worth it for cost.

## Real work, 2026-10-26 (issue #26)

`work.py` sends three requests that do work and report it (fix a failing test, explain the
discount code and what it gets wrong, add a function with a test) to a small scratch
project, reset before each turn, with `auto` approvals and `workspace` access. Codex ran three
ways: the shared instructions, those plus a firmer reminder with an example (`codex-firm`),
and the shared ones with a reminder appended to each message (`codex-reminder`).

```sh
.venv/bin/python spikes/tokens/work.py /some/scratch/dir --runs 2 --pause 30
.venv/bin/python spikes/tokens/work.py /some/scratch/dir --summary
```

Codex CLI 0.161.0 and Claude Code 2.1.310, default models. "Long" is a reply the voice would
cut to its opening without a spoken part (`summary.is_short`).

| | Turns | Spoken part | Long | Long with spoken part | Reply | Spoken part |
|---|---|---|---|---|---|---|
| Claude | 9 | 9 | 9 | 9 | 1,432 chars | 334 chars |
| Codex | 6 | 4 | 2 | 2 | 342 chars | 201 chars |
| Codex, firm | 6 | 6 | 2 | 2 | 374 chars | 120 chars |
| Codex, reminder | 6 | 2 | 2 | 2 | 194 chars | 256 chars |

- **On real work, Codex wrote a spoken part for every long reply**, in all three ways. What
  it skipped were short reports of its work ("Fixed cart.py: `total()` applied the
  discount twice…"), which are spoken as they are, links and code included.
- **The firm wording had it write one every time**, short reports included ("Fixed. All
  three tests now pass."), at no measurable cost in output. It's now part of Codex's
  instructions (`codex.SPOKEN_REMINDER`).
- **The per-message reminder made it worse**: shorter replies, and a spoken part on only
  the long ones.
- The six of six misses in the first spike were explanations with no tools, tables
  included; the firm wording hasn't been measured on those.
- The spike declined every approval, so in `auto` mode Claude couldn't run the tests and
  said so; Codex ran them inside its sandbox without asking.
- Codex answered `429 Too Many Requests` to turns in quick succession, with 2% of its
  5-hour window used: a failed turn with no message. `work.py` waits and tries again, and
  `--pause` spaces turns out. The planned third round was stopped there.

Separately, a long reply without a spoken part now skips a heading, table or code block it
opens with, so the voice starts at its first prose (`summary.without_lead`).
