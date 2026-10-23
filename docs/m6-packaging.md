# M6: packaging

Status: planned. Date: 2026-10-23.

M6 takes Agent Talk from a source checkout to the one-line install in HLD §1:
`uv tool install agent-talk && agent-talk setup`. It adds the user service, upgrade
and uninstall, license notices in the wheel and the app, and CI that tests, builds and
publishes a release. Gemini CLI (the rest of M4) is independent of it.

Much is already in place: `setup` picks a speech tier and reinstalls Agent Talk with
the matching extra (including under `uv tool`), `doctor` checks agents, speech and
access, NOTICES lists the speech and font licenses, and the wheel includes the built
client when it is present. What's missing is everything between "it runs from my
checkout" and "a stranger installs it from PyPI and it is still running tomorrow".

## Decisions

- **One wheel, pure Python.** `py3-none-any`, with the built client inside. Nothing
  native is ours: the ONNX runtime and espeak-ng come from their own wheels, and the
  models are downloaded by `setup`. Users never need Node, Docker or a compiler.
- **`uv tool install` is the documented install.** uv fetches a suitable Python, and
  `setup` already knows how to reinstall a uv tool with an extra. `pip install` and
  `pipx` work, but aren't what the README leads with.
- **The service runs the installed `agent-talk serve`, as the user.** A systemd
  `--user` unit on Linux, a LaunchAgent on macOS. No root, no system-wide install, no
  container.
- **Upgrades keep data and models.** Config, database and models live in the platform
  directories (`platformdirs`), outside the tool's environment, and the database already
  migrates itself by schema version.
- **Data removal is its own command**, never a side effect of uninstalling (HLD §9).
- **The GPL position doesn't change** (m2-backend, option A). The wheel contains only
  Agent Talk's own code and client, so it stays Apache-2.0; the GPL-3 speech parts
  arrive from pip on the user's machine. A container image or distro package would be
  GPL-3, and isn't part of M6.

## Design

### Install

```sh
uv tool install agent-talk
agent-talk setup            # speech tier and models, phone address, then the service
agent-talk project add ~/code/api --name backend
agent-talk pair
```

`setup` gains two steps at the end, each asked like the others (recommendation first,
`--yes` accepts it):

- **Serve on the tailnet.** When Tailscale is up and nothing serves its address yet,
  offer to run `tailscale serve --bg 7710` rather than only telling the user to. It
  changes the user's Tailscale configuration, so it is always asked, and the default
  is yes only when no other `serve` rule exists.
- **Install the service** (below), then print a pairing link, so setup ends with a
  running Agent Talk and a way in.

### Service

`agent-talk service install | uninstall | status | restart | logs`.

**Linux.** A unit at `~/.config/systemd/user/agent-talk.service`:

```ini
[Unit]
Description=Agent Talk
After=network-online.target

[Service]
ExecStart=<absolute path to agent-talk> serve
Environment=PATH=<PATH when installed>
Restart=on-failure
RestartSec=5

[Install]
WantedBy=default.target
```

`install` writes it, runs `systemctl --user daemon-reload` and
`enable --now`, and waits for a new `/api/health` to answer. It returns the version
only, and like the app shell needs no paired device. Logs go to the
journal; `service logs` runs `journalctl --user -u agent-talk -f`.

A user service starts at login and stops at logout. For a machine that should answer
while nobody is logged in, `loginctl enable-linger` keeps it running; setup explains
that and asks, since it is a system-wide setting for the user.

**macOS.** A LaunchAgent at `~/Library/LaunchAgents/dev.agent-talk.plist` with
`RunAtLoad`, `KeepAlive` on crash, and logs under `~/Library/Logs/agent-talk/`, loaded
with `launchctl bootstrap gui/<uid>`. There's no Mac on the development machine, so
this is written against Apple's documentation and needs a real Mac before release.

**PATH.** The agents are found on `PATH`, and a service doesn't get the login shell's:
`claude` in `~/.local/bin`, `codex` under nvm or Homebrew would be missing. `install`
records the installing shell's `PATH` in the unit, and `doctor` checks each agent from
the service's `PATH` as well as its own, and says to rerun `service install` when they
differ. Resolving each agent to an absolute path was rejected: nvm and Homebrew paths
change on every Node or package upgrade.

**Running twice.** `serve` in a terminal while the service holds the port fails with a
message naming the service and how to stop it, rather than a bare "address in use".
The pairing link `serve` prints on first run goes to the journal under the service,
which is why setup prints one itself.

### Upgrade and uninstall

- **`agent-talk --version`**, and the version in `doctor`, `/api/health` and Settings.
- **`agent-talk upgrade`** runs `uv tool upgrade agent-talk` (which keeps the extra
  from the receipt), then restarts the service. A restart ends running tasks, which
  recovery marks `unknown`, so `upgrade` first reads the database for running or
  background tasks and asks before going on. Outside a uv tool install it prints the
  command to run instead.
- **The client notices a new version.** The service worker caches the app, so an open
  tab would keep the old client against a new server. The client compares its build's
  version with the server's on each reconnect and offers to reload.
- **`agent-talk service uninstall`** stops and removes the unit or agent.
  `uv tool uninstall agent-talk` then removes the program.
- **`agent-talk remove-data`** lists the config, database and models with their sizes,
  asks, and deletes them. It refuses while the service is running.

### Notices

- `LICENSE` and `NOTICES` go into the wheel's metadata (`license-files`).
- The built client bundles npm packages (React, Radix and others) whose MIT and ISC
  licenses ask for their notice to travel with the code. The client build writes their
  license texts to `web/licenses.txt`, from the bundle's actual contents, and NOTICES
  points to it.
- **Settings → About** shows the version, Agent Talk's license, NOTICES (served by the
  daemon) and the bundled-package licenses. HLD §5.1 requires Parakeet's CC-BY
  attribution in the app as well as in NOTICES; this is where it goes.

### Release CI

GitHub Actions, two workflows.

**On every push and pull request:**

- Backend: `uv sync --extra cpu`, `pytest`, on Ubuntu and macOS.
- Client: `npm ci`, lint, unit tests, `npm run build`, and the Playwright suites
  (desktop and phone) on Ubuntu. Screenshot baselines are Linux-only, as now.
- Package: build the wheel and sdist after the client, and check the wheel contains
  `agent_talk/web/index.html`, NOTICES and the license files.

The `models` tests download about 3 GB and need a GPU for the CUDA tier, so they stay
manual, run before a release on the development machine.

**On a `v*` tag:**

- Check the tag matches `__version__` and `pyproject.toml`.
- Build the client and the wheel and sdist, as above.
- Smoke test the built wheel on a clean runner: `uv tool install ./dist/*.whl`,
  `agent-talk --version`, `agent-talk serve` in the background, then `/` serves the app
  and `/api/projects` answers 401.
- Publish to PyPI with trusted publishing (no stored token), and create a GitHub release
  with the wheel, sdist and notes.

## Steps

### 1. Service

`service` commands for systemd and launchd, the recorded `PATH`, the "already running"
message, `/api/health`, and `doctor` reporting whether the service is installed, enabled and
answering. Tests write the unit and plist into a temporary home and check their
contents; a test on the CI runner, which has a systemd user session, installs and starts
the real unit.

### 2. Setup and doctor

The Tailscale serve and service steps in `setup`, linger on Linux, and the pairing link
at the end. `doctor` checks agents from the service's `PATH`, and that the web client is
present.

### 3. Version, upgrade and data removal

`--version`, the version in `doctor` and the client, the reload offer, `upgrade`
with its running-task check, and `remove-data`.

### 4. Notices

License files in the wheel, `web/licenses.txt` from the client build, and Settings →
About.

### 5. CI

The test workflow, then the release workflow with the smoke test. The first release
goes to TestPyPI.

### 6. First release

A clean Linux machine and a Mac, from `uv tool install` to a task spoken from a real
phone: setup, service, pairing over Tailscale, Codex and Claude Code. M5 hasn't been
tried on a real phone yet, and this is where it is. Then the README's install section
moves to `uv tool install`, HLD §9 and §11 are updated, and 0.1.0 is tagged.

## Open questions

- **The PyPI name.** `agent-talk` is taken by an unrelated project (0.1.0, "an
  email-like messaging platform for AI agents"). Options: ask for it under PEP 541
  (slow, and only if it is abandoned), publish under another distribution name while
  the command stays `agent-talk`, or rename. This blocks step 5's real publish, not
  TestPyPI.
- **The repository.** There's no remote yet; CI and trusted publishing need the GitHub
  repository it will live in.
- **Linger by default?** Off by default is safer, but a desktop that is left logged out
  is a common case for "talk to my machine from my phone".
- **macOS speech.** The `cpu` extra's onnxruntime includes CoreML on macOS, but no tier
  has been measured there (HLD §12). Release could say macOS is CPU-only until it is.
