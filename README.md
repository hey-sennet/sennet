# Sennet

Talk to your coding agents.

Sennet is a free and open source, self-hosted voice interface for coding agents such
as Codex, Claude Code, and Gemini CLI. Speak a request, let an agent work on your
computer under your existing subscription, and hear what it did. Speech runs locally;
only the agents themselves talk to the cloud.

**Status:** early development. Typed and spoken tasks with Codex and Claude Code work
today; Gemini CLI is next. See the
[high-level design](docs/HLD.md) for the plan. The earlier Rust/Docker prototype is on
the `archive/rust-prototype` branch.

## Install

Requirements: Linux or macOS, [uv](https://docs.astral.sh/uv/), and at least one agent:
the [Codex CLI](https://developers.openai.com/codex) logged in with `codex login`, or
[Claude Code](https://code.claude.com/docs) logged in with `claude auth login` on your
Claude subscription. uv brings its own Python; there's no Node, Docker or compiler to
install.

```sh
uv tool install sennet
sennet setup            # speech, the phone address, then the background service
sennet project add ~/code/my-app --name app --alias frontend --agent claude
sennet pair             # a one-time link to pair this browser, or a phone
sennet doctor           # checks the agents, speech, devices, Tailscale and the service
```

Sennet isn't on PyPI yet; until it is, build a wheel from a checkout and install that
(see [Develop](#develop)).

The service runs Sennet from login (a systemd user service on Linux, a LaunchAgent on
macOS): `sennet service status | logs | restart | stop | uninstall`. Without it,
`sennet serve` runs it in the terminal. `sennet upgrade` upgrades and restarts it,
keeping your data and models. To remove everything: `sennet service uninstall`,
`sennet remove-data` (config, tasks, paired devices and models; your projects are
not touched), then `uv tool uninstall sennet`.

Every browser pairs once before it can use Sennet, this computer's included: open the
link `sennet pair` prints, or type its code. A paired device can show a code for
another in Settings, and Settings or `sennet devices` unpairs them.

Agents run on this computer as you, in the project directory. Pick each project's agent
in Settings or with `--agent`, and its default model and effort in Settings; a task can
have its own, from the model button beside the keyboard: chosen before a task starts, or
for the selected task from its next message. By default the agent asks before running anything
outside its trusted set, and you approve or decline in the app or by voice. "Always" remembers a command (e.g. `npm test`) for that project;
destructive commands such as `git push` or `rm` are always asked. Change the policy per
project in Settings, or the default in `config.toml`:

```toml
[approvals]
mode = "ask"               # ask | auto (works within its access, asks to go beyond) | never
access = "workspace"       # read-only | workspace (edit the project) | full

[tasks]
max_concurrent = 3         # one running task per project regardless

[server]
log_level = "info"         # debug | info | warning | error, for `sennet service logs`
```

Codex enforces access with its own sandbox. Claude Code's sandbox doesn't run everywhere,
so Sennet uses its permission modes instead, which is stricter in two cases: with
`auto` and `workspace` it asks before commands that aren't read-only, and with `never`
and `workspace` it can't run them at all. Claude Code loads your own settings but not a
project's `.claude/settings.json` hooks or `.mcp.json` servers, which would run without
asking; turn that on per project in Settings for projects you trust. When Claude leaves
a command running in the background, the task shows "Working in the background", and
its report is spoken after a chime when the command ends.

Setup looks at the CPU, RAM and GPU and asks each question as a list of every option:
the recommended one is marked and explained, options that won't fit say so, and a second
run marks your current settings. It asks where speech runs (CPU or GPU, installing the
matching `cpu` or `cuda` extra), then shows what it will download (about 0.8 GB of
speech models on CPU, 2.9 GB on GPU). After downloading it checks that speech hears
itself, and plays each voice you pick so you can choose by ear.
`--yes` accepts every recommendation without asking. Typed tasks work without speech.

Long agent replies are heard as a version the agent writes to be spoken, at the start of
its reply in `<spoken>` tags (hidden in the timeline); without one, its opening sentences
are spoken. Say "read me the details" to hear the whole reply.

Licensing note: the speech dependencies include phonemizer and espeak-ng, which are
GPL-3. Sennet's own code is Apache-2.0, but anything that bundles it with its speech
dependencies is GPL-3; see [NOTICES](NOTICES).

`sennet doctor` prints where the config and data live. The server binds to
127.0.0.1 only. A phone needs an HTTPS address in front of it: Tailscale (`sennet setup`
finds its address and offers to run `tailscale serve --bg 7710`) or a reverse proxy; see
[docs/hosting.md](docs/hosting.md).

## Develop

Also needs Node 22+, to build the client.

```sh
(cd frontend && npm ci && npm run build)
uv sync --extra cuda          # or --extra cpu
uv run sennet setup           # as above, but from the checkout (`uv run sennet serve` to run it)
uv run pytest                 # backend, against scripted fake agents and fake speech
SENNET_MODELS=~/.local/share/sennet/models uv run pytest -m models   # real speech models
cd frontend && npm test       # browser tests, desktop and phone
```

To install your build the way users do:

```sh
(cd frontend && npm run build) && uv build
uv tool install dist/sennet-*.whl    # then `sennet setup`, which adds the speech extra
scripts/smoke-test.sh dist/sennet-*.whl   # what release CI checks on each wheel
```
