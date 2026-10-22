# M4: more agents

Status: step 1 (Claude Code spike) done; results in [spikes/claude](../spikes/claude/README.md).
Date: 2026-10-22.

M4 adds adapters beyond Codex: Claude Code first, then Gemini CLI. The generic layer was
cleaned of Codex specifics first (commit 00ca47d): adapters are listed in
`agents/registry.py`, configured under `[agents.commands]`, state every capability, and
`check_event` rejects fields the contract doesn't define.

## Terms

The HLD left open (§12) whether a third-party tool may drive Claude Code under a
subscription login. Anthropic's
[legal and compliance page](https://code.claude.com/docs/en/legal-and-compliance), read
on 2026-10-22, restricts developers from offering Claude.ai login in their own apps,
routing requests through Free, Pro or Max credentials on their users' behalf, and
collecting, storing or intermediating Claude.ai credentials or session tokens. It then
says this "does not … prevent an end user from signing in to the unmodified Claude Code
binary with their own Claude subscription".

Agent Talk is self-hosted: it runs the user's own installed `claude`, unmodified, on
their own machine, after they log in with `claude auth login`. We read that as allowed,
on these conditions, which the adapter keeps:

- Run the published `claude` binary only. No Agent SDK packages: the SDK docs direct its
  developers to API keys.
- No `--bare`: it ignores the subscription login, so it would push users to API keys.
- Never read, copy or pass on credentials. `doctor` reports `claude auth status` only.
- Sign-in happens in `claude` itself, never in Agent Talk.

This is a reading of the terms, not legal advice; if Anthropic's wording changes, check
again before a release.

## Claude Code adapter

### Transport

One long-lived process per task session, like Codex:

```
claude -p --input-format stream-json --output-format stream-json --verbose
       --include-partial-messages --append-system-prompt <spoken instructions>
       --session-id <uuid> | --resume <uuid>
       --permission-mode <mode> --mcp-config <json>
       --permission-prompt-tool mcp__agent_talk__approve
       --setting-sources user --strict-mcp-config
       --disallowedTools AskUserQuestion
```

A turn is one `{"type": "user", …}` line on stdin; the `result` message ends it. The
adapter picks the session id, so the resume token is known before the process starts.
SIGINT ends a turn and the process exits; `--resume` continues the conversation in the
next one.

### Background work

Claude can start a command in the background, say so, and end its turn. When the
command finishes, Claude starts a turn of its own, with no user message, and reports.
Agent Talk keeps that behaviour: the reply to start the work is spoken as usual, and
the report is spoken when it comes. The generic layer gains:

- A capability, `background_turns`: the agent keeps working after its turn ends and
  may start a turn itself. Codex declares it false for now.
- Two events. `background` carries the work still running (`tasks`, each with an `id`
  and `description`; empty when it is done), from `system/background_tasks_changed`.
  `turn_started` with `by: "agent"` opens a turn the user didn't start; the adapter
  sends it when a turn begins with no message of ours in flight. That turn ends with
  `turn_completed`, like any other.
- A task status, `background`: the turn is over but work goes on. Follow-ups run as
  usual meanwhile (the spike asked a question mid-command and got its answer). The
  idle timer isn't armed while work runs, so the process, and the work, stays alive.
- The task manager runs an agent turn without taking a queued message: the task is
  `running` until `turn_completed`, then `background` or `completed`. Messages queued
  during it are sent after it ends.
- The voice hub speaks an agent turn's reply on the session that owns the task (the
  one that last spoke to it), with the same `<spoken>` handling, after the approval
  chime: the report can come minutes later, so it shouldn't start out of silence. With
  no owner connected, the reply is only shown.
- Cancelling a task with background work stops the agent's process, which ends the
  work. Background work doesn't survive an Agent Talk restart: recovery marks the task
  `unknown`, as it does a running one.

### Approvals

Through `--permission-prompt-tool`, the documented route: Agent Talk's own server serves
a minimal MCP endpoint over HTTP at `/mcp/<session secret>`, given to `claude` with
`--mcp-config`, and the tool call waits for the user's answer. The server entry sets
`timeout`: HTTP MCP requests otherwise fail after 60 s, too soon for a voice approval. The adapter answers allow
or deny only. "Approve always" stays in Agent Talk's rules; it never returns
`updatedPermissions`, which would write to the project's `.claude/settings.local.json`.

Rejected: the Agent SDK's stdio control protocol (`can_use_tool` on the same pipe).
Simpler, but undocumented for direct use, and it would mean impersonating the SDK.

### Project configuration

Without `--bare`, `claude -p` runs a project's `.claude/settings.json` hooks and connects
its `.mcp.json` servers with no trust prompt. By default the adapter loads the user's
own configuration only (`--setting-sources user`, `--strict-mcp-config`); a per-project
opt-in loads the project's too. CLAUDE.md is instructions, not code, and still loads.

### Mapping

| Contract | Claude Code |
| --- | --- |
| `message_delta`, `message` | `stream_event` text deltas and `assistant` text blocks, main conversation only (`parent_tool_use_id` null); item id is message id plus block index |
| `command_started`, `command_finished` | `Bash` tool use and its `tool_result`; exit code parsed from a failure's `Exit code N`, else `None` |
| `file_change` | `Edit`, `Write`, `NotebookEdit` and their results; `add` or `update` from whether the file existed |
| `plan` | none: headless sessions on current models have no task-tracking tool |
| `error` with `will_retry` | `system/api_retry` |
| `turn_completed` | `result`: `success` → completed, `error_during_execution` after an interrupt → cancelled, other errors → failed |
| item `declined` | a tool call the adapter denied (also listed in `result.permission_denials`) |
| approval `command`, `edit`, `tool` | `Bash`; `Edit`/`Write` with a `difflib` diff; anything else by tool name |
| asking the user | `AskUserQuestion` is disallowed, so the spoken instructions name no tool |

Claude Code's bash sandbox would map `workspace` exactly, but it needs nested user
namespaces, which the spike machine doesn't allow; it can't be the default. Access is
enforced with permission modes and path-scoped rules instead (an absolute path in a
rule takes `//`, and a bare `Edit` rule allows edits anywhere):

| | read-only | workspace | full |
| --- | --- | --- | --- |
| `ask` | `default`, edit tools denied | `default` | `default` |
| `auto` | `default`, edit tools denied | `acceptEdits` | `bypassPermissions` |
| `never` | `dontAsk`, edit tools denied | `dontAsk`, `Edit(//<project>/**)` allowed | `bypassPermissions` |

`never` also passes `--permission-prompts none`. Two combinations are stricter than
Codex's: `auto` + `workspace` asks before commands that aren't read-only (Codex runs
them in its sandbox), and `never` + `workspace` can't run them at all. The adapter
supports every combination, and the settings explain the difference.

## Steps

### 1. Spike

Done: [spikes/claude](../spikes/claude/README.md). Recorded stream-json for a chat
turn, commands, edits, a denial, a slow approval, interrupts, resume and the sandbox,
against the installed CLI (2.1.308). The transport, approvals and interrupts work as
planned once the MCP server's `timeout` is set. Background commands end with a turn
Claude starts itself, which step 2 makes room for. The sandbox doesn't run here, which
changed the access mapping above.

### 2. Background turns in the generic layer

The capability, events and status above, in `agents/base.py`, `tasks.py`, the voice
hub and the client's status badge, tested with a scripted agent.

### 3. Adapter

`agents/claude.py`, registered in `agents/registry.py`, with `status()` from
`claude auth status` (warn on an API key, as for Codex).

### 4. Approval endpoint

The MCP route in `server.py`: loopback only, accepted only for a live session's secret,
handing the call to that session's approval handler.

### 5. Tests

`fake_claude.py` built from the spike's transcripts. The contract tests run over every
adapter in the registry, not Codex alone.

### 6. Client

An agent picker when adding a project (there is none yet), and the project-config opt-in.

### 7. Docs

HLD §4.2 and §12, README, and this breakdown.

## Open questions

- Whether Claude Code's sandbox runs on a typical Linux desktop (it failed here on
  nested user namespaces) and on macOS. If it does, `auto` + `workspace` could use it to
  run commands without asking, as Codex does.
- Gemini CLI: ACP over stdio, after Claude Code.
