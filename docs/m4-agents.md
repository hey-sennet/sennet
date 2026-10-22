# M4: more agents

Status: Claude Code adapter done (steps 1 to 7); model and effort selection (step 8)
awaiting review; Gemini CLI next.
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

Done (d7bd852). The capability, events and status above, in `agents/base.py`,
`tasks.py`, the voice hub and the client's status badge and timeline, tested with a
scripted in-process agent (`tests/scripted_agent.py`). The chime before an agent's own
reply is a `cue: true` on the reply's first `speech` message (voice-protocol §5, §9):
the client plays the chime and holds the speech until it ends.

An agent's own turn doesn't wait for a concurrency slot (it has already started), and a
task in `background` doesn't hold its project's slot, so another task in the project
can run while the work goes on, and the report can arrive during it.

### 3. Adapter

Done (83c2441). `agents/claude.py`, registered in `agents/registry.py`, with `status()`
from `claude auth status --json` (warns on anything but a claude.ai login; never shows
the account). Each adapter now has a `title` ("Claude Code") for the client.

### 4. Approval endpoint

Done (76717a1). `agents/callbacks.py` serves the least of MCP's HTTP transport a
client needs (JSON-RPC in POST bodies, one JSON response each); `server.py` routes
`/mcp/<secret>` to it, refusing requests from other machines or with an Origin. Closing
a session unregisters its secret and ends any call still waiting. The server entry's
`timeout` is 24 hours: an approval waits for the user.

### 5. Tests

Done (cd1c971). `tests/fake_claude.py`, shaped like the spike's transcripts, asks its
approvals over HTTP MCP, so the contract tests run every adapter in the registry
against a live server. Running them showed that a follow-up sent straight after a
cancel reached the interrupted process before it exited: an interrupted Claude session
now counts as ended, and the task manager closes what is left of a session before
opening the next.

### 6. Client

Done (42a4aca). Settings lists the agents by title, picks one for a new project (the
first that is set up) and changes it per project; new tasks use it. The project-config
opt-in is a checkbox, shown when the agent declares the `project_config` capability
(Codex doesn't); turned on, `claude` gets `--setting-sources user,project,local` and
no `--strict-mcp-config`. Schema version 4 stores it. A browser test adds a Claude Code
project and follows a background report.

### 7. Docs

Done. HLD §4.1, §4.2, §11 and §12, the README, voice-protocol §5 and §9, and this
breakdown.

### 8. Model and effort

Each agent lists the models the user may pick (`Agent.models`): Codex from
`model/list` (hidden models left out, the default marked, each with its effort levels),
kept ten minutes since asking takes a process; Claude Code has no list, so its adapter
offers the `opus`, `sonnet`, `haiku` and `fable` aliases with efforts `low` to `max`,
none marked as the default (that depends on the plan and the user's settings). Fable
needs usage credits on some plans and says so in its reply.

A project has a model and effort for new tasks, and a task can override both; null
follows the next level up, and finally the CLI's own choice. A task that picks its own
model takes that model's default effort, not the project's; the project's choice only
applies to tasks of the same agent, and changing a project's agent or model clears what
belonged to the old one. The server refuses ids the agent doesn't list, and checks an
effort alone against the model in use. Schema version 5.

A change applies from the task's next turn. Codex sends model and effort with each
`turn/start` (`live_model_change`), so the session stays warm. Claude Code takes them at
start, so the task manager reopens the session with `--resume`; not while background
work runs, which the restart would end: the change waits for it. A resumed conversation
keeps the model it last used, in both CLIs, so going back to "Default" names it
outright: Codex's listed default with its default effort, `--model default` for Claude.
On a new conversation "Default" passes nothing, leaving the user's own CLI settings in
charge.

The client shows the picker under the task's status ("From your next message" while it
works) and as each project's default in Settings. Not yet by voice.

Checked on the real CLIs: a Claude task switched from haiku to sonnet on the same
session, and a Codex task from `gpt-6.1-sol`/low to `gpt-6-luna`/high between turns,
both confirmed in the CLIs' own session logs.

### Checked against the real CLI

Claude Code 2.1.308, `--model haiku` (set in `[agents.commands]`), through the daemon's
HTTP API: a chat turn; a write and a command, each approved; a declined command
(`declined` in the timeline, and Claude says so); background work under `auto` + `full`
(`bypassPermissions` ran it unasked, the task went `background`, then Claude's own
turn reported "finished" 14 s later); cancelling `sleep 60` and a follow-up resuming
the session; `never` + `workspace` writing inside the project and refusing a write
outside it and `touch` with Bash.

## Open questions

- Whether Claude Code's sandbox runs on a typical Linux desktop (it failed here on
  nested user namespaces) and on macOS. If it does, `auto` + `workspace` could use it to
  run commands without asking, as Codex does.
- Gemini CLI: ACP over stdio, after Claude Code.
