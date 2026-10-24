# Adapter signals: usage and what else the agents tell us

Status: investigation, for review. Date: 2026-10-23.

Both agent CLIs send more than the adapters keep. This document lists what they send,
what the adapters drop, and what could be useful in the app, usage above all. It is an
investigation. No design is agreed, and nothing here is built.

Sources: the adapters (`agents/claude.py`, `agents/codex.py`), the contract
(`agents/base.py`), the recorded Claude transcripts in `spikes/claude/transcripts`
(real output, Claude Code 2.1.309), and the schema that `codex app-server
generate-json-schema` writes (codex-cli 0.161.0). For Claude the field names below come
from real messages. For Codex they come from the schema, not from live payloads, so
their shapes and timing need checking before anything is built on them.

## 1. Usage

### What the agents send

**Claude Code** (stream-json):

- The `result` message that ends each turn carries `usage` (input, output, cache-read,
  cache-creation and thinking tokens), `total_cost_usd`, `duration_api_ms`, `stop_reason`,
  and `modelUsage` per model, with each model's `contextWindow` and `costUSD`. The cost
  is at list price (`costBasis: "list"`), so on a subscription it is not what is paid.
- A `rate_limit_event` carries `rate_limit_info`: `status`, and `unifiedWindows` with
  `five_hour` and `seven_day` entries, each a `utilization` fraction and `resetsAt`.
  A recorded transcript shows 0.47 and 0.37. On a subscription these windows are the
  number that matters. The event arrives during turns, so it can be stale when idle.

**Codex** (app-server):

- `thread/tokenUsage/updated` carries token usage per thread and turn.
- `account/rateLimits/updated` pushes rate-limit snapshots, and an account read returns
  the same (`GetAccountRateLimitsResponse`: the plan type, credits, buckets by limit id,
  reset credits and whether ordinary usage is allowed).
- A separate account-wide token usage read exists (`GetAccountTokenUsage`).

### What the adapters do with it

Nothing. The Claude adapter reads only the status from `result` and ignores
`rate_limit_event`. The Codex adapter doesn't handle any usage method.

### Resume and re-reading context

Claude resumes with `--resume`, so each turn after a restart re-sends the whole
conversation. In the recorded multi-turn run (Haiku, small context) the later turns read
about 21,000 tokens from cache and wrote 69 new ones, so an ordinary follow-up is mostly
cheap cache reads. Two cases cost more:

- **A cold cache.** The recorded entries had a one-hour lifetime (`ephemeral_1h`).
  After a longer gap the next turn rebuilds the cache, which costs more than a read.
- **A model switch.** A different model has its own cache, so the next turn starts cold.
  On Claude this also reopens the session (`live_model_change` is false), so a switch is
  never free.

This is one small run, so it shows the shape and not the size. A long task needs
measuring. Codex's resume and caching behavior is not looked at here.

### Ideas for the UI

- **Per task:** tokens, and cost for Claude, summed over turns, in the task detail.
- **Per agent:** a small indicator of the five-hour and seven-day windows with reset
  times. Claude's updates after turns. Codex's can also be polled.
- **Cache cost:** a note on a turn that rebuilt the cache, so a model switch or a long
  gap shows what it cost.

## 2. What else is dropped

### Claude Code

Kept today: text deltas and messages, `Bash` commands and results, file edits,
background task changes, API retries and the turn result.

| Signal | What it carries | Possible use |
| --- | --- | --- |
| `system/init` | the model in use, the tools, MCP servers and slash commands | confirm a model switch worked; show what the agent can do here |
| Other tool calls | `Read`, `Grep`, `WebFetch`, `WebSearch`, subagent `Task` | a timeline of what the agent looked at, not only what it ran and changed |
| `system/thinking_tokens` | a running estimate of thinking tokens | show "thinking" with a size; a clearer pause between speech and answer |
| `system/status` | e.g. `requesting` | mark the wait before the first token |
| `result` extras | duration, stop reason, permission denials, subagent counts | timing per turn; why a turn stopped |
| `task_*` messages | status and completion summaries for background tasks | richer background list (today only id and description are kept) |
| Subagent output | skipped on purpose (`parent_tool_use_id` check) | probably stays skipped; maybe a count |

### Codex

The app-server defines 84 server notifications. The adapter handles message and plan
deltas, command and file-change items, `turn/plan/updated`, `error` and
`turn/completed`, about seven. Candidates among the rest:

| Notification | Meaning | Possible use |
| --- | --- | --- |
| `turn/diff/updated` | running diff of everything the turn changed | a "what changed" review view |
| `item/reasoning/*` | reasoning summary and text, streamed | show thinking |
| `thread/compacted` | context was summarized | explain a change in behavior and cost |
| `model/rerouted` | it ran on a different model than chosen | tell the user; the model switch may not have applied |
| `warning`, `configWarning`, `deprecationNotice` | CLI warnings | toasts |
| `item/autoApprovalReview/*` | it auto-approved or flagged a request | show in the timeline |
| `item/mcpToolCall/progress` | progress of an MCP call | show in the timeline |
| `item/commandExecution/outputDelta` | live command output | tail running commands; today only the end is kept |
| `hook/started`, `hook/completed` | hooks ran | timeline, maybe |
| `thread/name/updated` | Codex titled the thread | better task titles |
| `thread/goal/*`, `thread/queue/changed` | not looked at | unknown |

The remaining notifications are about realtime audio, Windows sandboxing, account login,
file search and similar. They aren't relevant here.

## 3. The constraint: the agent contract

`agents/base.py` defines a small, provider-neutral set of events with checked fields
(`EVENT_FIELDS`, `OPTIONAL_FIELDS`, `check_event`). Provider words stop at the adapters.
Anything new must become a neutral event, or an optional field on an existing one:

- **Usage:** one event, e.g. `usage`, with tokens by kind, an optional cost, and the
  model. Rate-limit windows are account-level, not per task, so they need their own event
  or an agent-status read.
- **Tool use:** one `tool_used` event (name, short summary, status) rather than one event
  per tool. Both CLIs could fill it.
- **Compaction and rerouting:** one `notice` event with a kind and a message.
- **Turn diff:** the contract has `file_change` per item. A turn-level diff is Codex-only
  until Claude's edits are merged into one, so it may belong behind a capability flag,
  as `project_config` and `background_turns` are.

Storage is a second constraint. A task keeps up to 500 events (`MAX_EVENTS_PER_TASK`)
and `/api/tasks/{id}` returns all of them. Streaming signals (thinking size, live command
output) should be live-only, as message deltas are. Usage and notices should be stored,
since they explain a turn after it ends.

## Suggested order

1. **Usage and the actual model.** Highest value, and the model name checks that a model
   switch applied.
2. **Compaction and rerouting notices.** Both change cost or answers invisibly.
3. **Tool-use timeline entries** for reads, searches and fetches, for the phone view.
4. **Codex turn diff,** for a review view.
5. **Warnings** as toasts.

## Open questions

- Is Claude's cost worth showing on a subscription, or only the windows?
- Do Claude's rate-limit windows stay fresh enough with updates only during turns? Is
  an occasional idle poll worth a turn's usage? (`claude` may have a status command,
  unchecked.)
- Does Codex's rate-limit snapshot match what its own app shows? This needs a live check.
- What do `thread/goal/*` and `thread/queue/changed` carry?
- Should tool-use entries be spoken, or only shown?
- Is the cold-cache cost of a Claude model switch worth warning about before it happens?
