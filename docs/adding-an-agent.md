# Adding an agent

Sennet drives each agent's official CLI through an adapter in `src/sennet/agents/`. An
adapter translates the CLI's protocol into Sennet's provider-neutral contract
(`agents/base.py`), so nothing else in Sennet knows which agent it's talking to. Codex
(`codex.py`) and Claude Code (`claude.py`) are the two to learn from. Gemini CLI is next;
the plan is to drive it over ACP ([m4-agents.md](m4-agents.md)).

## Ground rules

- **The official CLI, under the user's own login.** No API keys, no calling the
  provider's API directly, no reusing the CLI's credentials. Check the provider's terms
  allow a CLI to be driven this way, as [m4-agents.md](m4-agents.md#terms) does for
  Claude Code, and say what you found in the pull request.
- **A long-running process over stdio**, where the CLI has one, so follow-ups start at
  once and approvals can be asked mid-turn.
- **Only the contract leaves the adapter.** Map the provider's statuses, kinds and modes
  to the vocabularies in `base.py`; don't add fields of your own.

## What an adapter provides

An `Agent` (see the protocols in `base.py`) has:

- `name` (used in config and the API, such as `codex`), `title` (as users know it, such
  as "Claude Code"), and `default_command`.
- `tested_version`: the newest CLI release the adapter was checked against, or a release
  series such as `"2.1"` for a CLI that releases patches daily. A newer CLI isn't refused:
  `doctor` and Settings warn about it. Raise it when you've checked a newer release (step 5).
- `capabilities`: what it can do. Every field must be stated, and Sennet adapts to them:
  an agent that can't `interrupt` is stopped instead, one that can't `resume` starts a
  fresh conversation after a restart, and an `approval_modes` or `access_levels` entry it
  doesn't list is refused before a task starts. Without `images`, a message with images
  is refused before it's sent, and the app hides its image button.
- `status()`: whether the CLI is installed and logged in, for `doctor` and Settings, and
  its `version` when it says, with `untested()`'s warning when that's newer than
  `tested_version`.
- `models()`: the models and effort levels it offers, for the model picker.
- `limits()`: the account's usage limits as `LimitWindow`s, asked for without using any,
  or None when the agent can't say (not logged in, or logged in with an API key).
- `open(cwd, resume_token, policy, on_event, on_approval)`: starts or resumes a session
  in the project directory and returns an `AgentSession`, with `send` (the message's text,
  and with `images` its `Image`s: files on this machine and their media types), `interrupt`,
  `set_model`, `close`, `alive` and a `resume_token` to store.

During a turn the session calls `on_event` with `AgentEvent`s (`message_delta`,
`message`, `command_started`, `command_finished`, `file_change`, `tool_used` for any other
tool call (the tool's readable name and a short summary of its input), `plan`, `error`,
`turn_completed`, `usage` with the tokens the turn used, `limits` whenever the agent
reports its usage limits, `background` for work left running after a turn, and for
agents with `background_turns`, `turn_started`), and awaits `on_approval` with an `ApprovalRequest` when the agent asks
to do something. The last `message` before `turn_completed` is the answer, and is what
gets spoken. A turn that stops at a usage limit says so with `turn_completed`'s `limit`
field (which window, and when it resets, if known); Sennet words it, so the agent's own
message about it shouldn't also be sent as a `message`.

Pass the agent `spoken_instructions()` alongside its own instructions, naming its tool
for asking the user if it has one. This is what makes its replies work when heard.

## Steps

1. **Record real output first.** Run the CLI by hand through a plain request, a command
   that needs approval, a file edit, an interrupt and a resume, and save the transcripts.
   `spikes/claude/transcripts` is the example. Base the adapter on what the CLI really
   sends, not only on its docs.
2. **Write a fake CLI** in `tests/` that plays back that protocol, like `fake_codex.py` and
   `fake_claude.py`, and wire it into `tests/conftest.py` beside them. Tests never run a
   real agent.
3. **Write the adapter** in `src/sennet/agents/<name>.py`, and add it to `ADAPTERS` in
   `registry.py`. That's the only place adapters are listed.
4. **Run the contract tests.** `tests/test_contract.py` and `tests/test_approval_contract.py`
   run every adapter in the registry against its fake, and check every event against the
   contract. Add tests of your own for what's particular to the agent, as
   `tests/test_claude.py` does.
5. **Try it for real**: by voice, through an approval, a follow-up, a cancel and a
   restart of the service. Set `tested_version` to the release you tried.
6. **Document it**: the requirements in [install.md](install.md), anything particular
   about its approvals or access in [using.md](using.md#approvals), and a note of what you
   learned in the milestone doc.
