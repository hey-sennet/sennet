# Codex background work spike

A throwaway probe for [#3](https://github.com/hey-sennet/sennet/issues/3): what `codex
app-server` does with a command that outlasts its turn. Not product code.

## Run it

```sh
python probe.py PROJECT_DIR [--experimental] [--listen SECONDS]   # writes transcripts/background.jsonl
```

PROJECT_DIR should be a scratch git repo. Transcripts are git-ignored: they hold local
paths and account details.

## Results

Codex CLI 0.161.0 on Linux, default model, `effort: low`, `experimentalApi: false`,
2026-10-25.

- Asked to start a 25 s command in the background and end its turn, Codex started it
  (`item/started`, `commandExecution`, `status: inProgress`, with a `processId`) and
  ended the turn 6 s later with the item still in progress. No feature flag or
  experimental API is needed.
- A follow-up sent meanwhile ran as a normal turn and was answered at once.
- When the command ended, `item/completed` for it arrived **between turns** (exit code,
  `status: completed`), with the original `turnId`.
- Codex **didn't start a turn of its own** to report it, and said so: "I can't send a
  follow-up automatically after ending this turn." `process/exited` didn't appear; it
  is for the client's own `command/exec` processes.

So the adapter reports commands still running at the end of a completed turn as
`background`, and clears each as its `item/completed` arrives. `background_turns` stays
false, and the voice hub says the work finished in Sennet's own words.
