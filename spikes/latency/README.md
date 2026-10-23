# Latency spike

Step 0 of the latency idea: measure where the time goes between a message and the agent's
first words before choosing what to change. Not product code.

## The agent half

`agents.py` drives Claude Code and Codex through Sennet's own adapters (the same arguments,
instructions and MCP callback as the daemon) and timestamps each stage of a turn: process
open, init (`system/init` for Claude, `turn/started` for Codex), first token, first thinking,
first text, first finished sentence, the spoken part complete, and the turn's end. It
runs a cold start, a warm process, a resume after the process closed and the follow-up
after an interrupt. Each turn is a real request on your subscription.

It serves its own MCP endpoint on a free port and works in a scratch git repository, so it
never touches the running service.

```sh
.venv/bin/python spikes/latency/agents.py /some/scratch/dir --runs 3
.venv/bin/python spikes/latency/agents.py /some/scratch/dir --summary   # medians again
```

One JSON line per turn goes to `latency.jsonl` in the scratch directory, with the marks,
Claude's usage (cache reads and writes, API time) and Codex's request timings.

## The voice half

Not in this script. The service already logs, per reply, how long after it was queued it
was first heard (`voice …: reply N … first heard X s after it was queued`).

## Results, 2026-10-23

Six runs of each scenario (three of pre-warmed), a two-sentence answer with no tools, the
agents' default models, on this laptop. Medians in seconds from the start of the turn
(from `open` where a process starts, else from `send`):

| Scenario | Claude init | Claude first text | Codex open | Codex first text |
|---|---|---|---|---|
| Cold (new session) | 0.70 | 2.78 | 0.19 | 3.44 |
| Warm process | 0.01 | 0.88 | — | 1.53 |
| Resumed after close | 0.65 | 2.22 | 0.20 | 3.07 |
| After an interrupt | 0.60 | 1.73 | — (stays up) | 1.94 |
| Pre-warmed resume (opened 5 s early) | 0.04 | 1.10 | — | 1.76 |

- A new process costs about 1.3 to 1.9 s before the first words, for both agents. Only
  part of it is visible start-up (Claude's `system/init` at about 0.65 s, Codex's
  `initialize` and `thread/start` at about 0.2 s). The rest is a slower first model
  response in a fresh process.
- A process opened a few seconds before the message is as fast as a warm one: the whole
  start-up cost goes. So pre-warm on intent (idea 1) and re-warm after an interrupt
  (idea 2, Claude only, since Codex's process survives) are worth building.
- Neither agent leaves anything behind for a session opened and closed with no message:
  no Claude transcript, no Codex rollout file.
- Codex's `model/list` on resume takes 2 to 4 ms, so idea 5 isn't worth doing.
- Claude reads about 22k tokens from the prompt cache on every turn here, cold included,
  so in these runs the cache isn't what makes a cold start slow (idea 9).
- With the process warm, the model takes 0.9 s (Claude) to 1.5 s (Codex) to first text on
  this tiny request; real requests with thinking take longer. That is idea 8's territory.

The first Codex runs were discarded: the script called `interrupt()` inline from the event
callback, which blocks Codex's reader from reading the reply. It now stops from a task of
its own, as a user's stop does.
