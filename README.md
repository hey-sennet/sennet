# Agent Talk

Talk to your coding agents.

Agent Talk is a free and open source, self-hosted voice interface for coding agents such
as Codex, Claude Code, and Gemini CLI. Speak a request, let an agent work on your
computer under your existing subscription, and hear what it did. Speech and the voice
assistant run locally; only the agents themselves talk to the cloud.

**Status:** early development. Typed tasks with Codex work today; voice is next. See the
[high-level design](docs/HLD.md) for the plan. The earlier Rust/Docker prototype is on
the `archive/rust-prototype` branch.

## Try it (development)

Requirements: Python 3.12+, [uv](https://docs.astral.sh/uv/), Node 22+ (to build the
client), and the [Codex CLI](https://developers.openai.com/codex) logged in with `codex login`.

```sh
(cd frontend && npm ci && npm run build)
uv run agent-talk doctor                        # checks Codex is installed and logged in
uv run agent-talk project add ~/code/my-app --name app --alias frontend
uv run agent-talk serve                         # http://127.0.0.1:7710
```

Agents run on this computer as you, in the project directory, using their own sandbox.
By default Codex asks before running anything outside its trusted set, and you approve
or decline in the app. "Always" remembers a command (e.g. `npm test`) for that project;
destructive commands such as `git push` or `rm` are always asked. Change the policy per
project in Settings, or the default in `config.toml`:

```toml
[approvals]
mode = "ask"               # ask | auto (sandboxed, asks only to escalate) | never
sandbox = "workspace-write"

[tasks]
max_concurrent = 3         # one running task per project regardless
```

`agent-talk doctor` prints where the config and data live. The server binds to
127.0.0.1 only; for phone access use `tailscale serve`, which provides HTTPS.

## Develop

```sh
uv run pytest                 # backend, against a scripted fake Codex
cd frontend && npm test       # browser tests, desktop and phone
```
