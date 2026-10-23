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
