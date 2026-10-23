# M4 Claude Code spike

Throwaway probes for the Claude Code adapter in [M4](../../docs/m4-agents.md): how
`claude -p` behaves over stream-json, with approvals answered by a permission prompt
tool served over HTTP MCP. Not product code.

## Run it

```sh
python probe.py SCENARIO PROJECT_DIR [--model haiku]   # writes transcripts/SCENARIO.jsonl
python summarize.py transcripts/SCENARIO.jsonl
```

Scenarios: `command`, `edit`, `deny`, `slow_approval` (`DELAY` seconds), `multiturn`
(two turns, then a resume in a new process), `sigint`, `control_interrupt`,
`background`, `sandbox_auto`, `sandbox_never`, `scoped_never`. `MCP_SERVER_TIMEOUT` sets the MCP
server's `timeout` (ms); `CLAUDE_CODE_DISABLE_BACKGROUND_TASKS=1` passes through.
PROJECT_DIR should be a scratch git repo with an `app.py` printing "hi".

Transcripts are git-ignored: they hold local paths and account rate-limit data. The
test fixtures are built from them, scrubbed, in step 4.

## Results

Claude Code 2.1.308 on Linux, `--model haiku`, 2026-10-22.

### Transport

- One process serves many turns: each `{"type": "user", …}` line on stdin starts a turn
  and a `result` line ends it. `system/init` is sent again at the start of every turn.
  Closing stdin ends the process after the turn, with exit code 0.
- `--session-id <uuid>` is honoured, so the adapter chooses the resume token. `--resume`
  in a new process continues the conversation.
- Assistant output arrives once per content block: a message with text and a tool use
  is two `assistant` lines with the same `message.id`, each holding one block. Text
  deltas are `stream_event` `content_block_delta` / `text_delta` with an `index`; the
  message id comes from `message_start` (and `api_message_id` on later events). Item
  ids are therefore message id plus block index.
- `result.subtype` is `success` for a finished turn, including one whose tool was
  declined; `permission_denials` lists the declined calls. An interrupted turn is
  `error_during_execution` with `terminal_reason: aborted_tools`.

### Commands and edits

- `Bash` tool use carries `command` (unwrapped) and `description`. Its `tool_result`
  has `is_error`; a failure's content starts `Exit code N`. There is no structured exit
  code, so the adapter parses that prefix and otherwise reports `None`.
- Read-only commands (`ls`, `false`, `sleep`, `echo`) run without asking.
- `Edit` gives `file_path`, `old_string`, `new_string`, `replace_all`; `Write` gives
  `file_path`, `content`. The approval request carries the whole input, so the adapter
  can build a diff.
- No task-tracking tool exists in a headless session on current models: the Task tools
  and `TodoWrite` are opt-in (`CLAUDE_CODE_ENABLE_TODO_TOOLS=1`). The adapter sends no
  `plan` events.

### Approvals

- The permission prompt tool works over HTTP MCP. Claude Code sends `server/discover`
  (an empty result is fine), `initialize`, `notifications/initialized` and
  `tools/list` at startup, then a `tools/call` with `tool_name`, `input` and
  `tool_use_id` for each prompt. The answer is a text content block holding
  `{"behavior": "allow", "updatedInput": …}` or `{"behavior": "deny", "message": …}`.
- A denial reaches Claude as an `is_error` tool result with our message, and Claude
  doesn't retry.
- **HTTP MCP requests time out after 60 s by default**: a slower answer fails the
  tool call ("The operation timed out."), and Claude carries on. A per-server
  `"timeout"` (ms) in `--mcp-config` lifts it; a 150 s wait then worked.

### Interrupts

- SIGINT mid-command ends the turn (the tool call is rejected and a
  `[Request interrupted by user for tool use]` message follows), sends `result`, and
  the process exits with 0. `--resume` continues.
- A `{"type": "control_request", "request": {"subtype": "interrupt"}}` line does the
  same and keeps the process alive for the next turn. It is the Agent SDK's control
  protocol, so the adapter uses SIGINT.

### Background work

- Claude moved a long `sleep` to a background command on its own, said so, and ended
  its turn. When the command finished, Claude started a **new turn by itself**
  (`init`, a reply, `result`) with no user message.
- `system/background_tasks_changed` lists the work running (`task_id`, `description`)
  and is sent again, empty, when it ends; `task_started` and `task_notification`
  bracket each task.
- A user message sent while the work ran got its own turn and answer straight away
  (`background` scenario); the report came in a separate turn when the command ended.
- `CLAUDE_CODE_DISABLE_BACKGROUND_TASKS=1` turns all of this off. Sennet keeps it
  on and treats the agent's own turns as part of the contract (M4 step 2).

### Access

- The bash sandbox doesn't run on this machine: every sandboxed command fails with
  `apply-seccomp: … nested userns is capability-restricted`, because nested user
  namespaces aren't allowed here (`unshare -U unshare -U true` fails), even with
  `failIfUnavailable`. With the sandbox enabled, Claude asks to retry each command with
  `dangerouslyDisableSandbox: true`, through the prompt tool. The adapter can't rely on
  the sandbox, so access is enforced with permission rules.
- A bare `--allowedTools Write` allows writes **anywhere**, including outside the
  project. Rules must be path-scoped, and an absolute path needs a leading `//`:
  `Edit(//home/me/repo/**)`. With `dontAsk`, that allowed edits in the project and
  denied a write outside it and a `touch` with Bash.
- `acceptEdits` wrote inside the project without asking and sent a write outside it to
  the prompt tool.
