# M6: packaging

Status: done; tried from a clean install on a laptop, with a phone paired. Not released
yet: that waits on the open questions below. Date: 2026-10-23.

M6 takes Sennet from a source checkout to the one-line install in HLD §1:
`uv tool install sennet && sennet setup`. It adds the user service, upgrade
and uninstall, license notices in the wheel and the app, and CI that tests, builds and
publishes a release. Gemini CLI (the rest of M4) is independent of it.

Much is already in place: `setup` picks a speech tier and reinstalls Sennet with
the matching extra (including under `uv tool`), `doctor` checks agents, speech and
access, NOTICES lists the speech and font licenses, and the wheel includes the built
client when it is present. What's missing is everything between "it runs from my
checkout" and "a stranger installs it from PyPI and it is still running tomorrow".

## Decisions

- **One wheel, pure Python.** `py3-none-any`, with the built client inside. Nothing
  native is ours: the ONNX runtime and espeak-ng come from their own wheels, and the
  models are downloaded by `setup`. Users never need Node, Docker or a compiler.
- **`uv tool install --python 3.14` is the documented install.** uv fetches that Python if
  it's missing, and `setup` already knows how to reinstall a uv tool with an extra (on the
  same Python). The `--python` is needed: uv picks its default interpreter, not one that
  fits `requires-python`, and the first release run's macOS smoke test failed on Homebrew's
  3.15, which onnxruntime has no wheels for yet. `pip install` and
  `pipx` work, but aren't what the README leads with.
- **The service runs the installed `sennet serve`, as the user.** A systemd
  `--user` unit on Linux, a LaunchAgent on macOS. No root, no system-wide install, no
  container.
- **Upgrades keep data and models.** Config, database and models live in the platform
  directories (`platformdirs`), outside the tool's environment, and the database already
  migrates itself by schema version.
- **Data removal is its own command**, never a side effect of uninstalling (HLD §9).
- **The GPL position doesn't change** (m2-backend, option A). The wheel contains only
  Sennet's own code and client, so it stays Apache-2.0; the GPL-3 speech parts
  arrive from pip on the user's machine. A container image or distro package would be
  GPL-3, and isn't part of M6.

## Design

### Install

```sh
uv tool install sennet
sennet setup            # speech tier and models, phone address, then the service
sennet project add ~/code/api --name backend
sennet pair
```

`setup` gains two steps at the end, each asked like the others (recommendation first,
`--yes` accepts it):

- **Serve on the tailnet.** When Tailscale is up and nothing serves its address yet,
  offer to run `tailscale serve --bg 7710` rather than only telling the user to. It
  changes the user's Tailscale configuration, so it is always asked, and the default
  is yes only when no other `serve` rule exists.
- **Install the service** (below), then print a pairing link, so setup ends with a
  running Sennet and a way in.

### Service

`sennet service install | uninstall | status | restart | logs`.

**Linux.** A unit at `~/.config/systemd/user/sennet.service`:

```ini
[Unit]
Description=Sennet
After=network-online.target

[Service]
ExecStart=<absolute path to sennet> serve
Environment=PATH=<PATH when installed>
Restart=on-failure
RestartSec=5

[Install]
WantedBy=default.target
```

`install` writes it, runs `systemctl --user daemon-reload`, `enable` and `restart`, and
waits for `/api/health` to answer. That returns `{"app": "sennet", "version": …}`
only, and like the app shell needs no paired device. Logs go to the journal;
`service logs [-f]` runs `journalctl --user -u sennet`. The variables that choose
where data lives (`SENNET_HOME`, `XDG_CONFIG_HOME`, `XDG_DATA_HOME`) are carried into
the unit when set, so the service uses the same data as the shell that installed it.

A user service starts at login and stops at logout. For a machine that should answer
while nobody is logged in, `loginctl enable-linger` keeps it running; setup explains
that and asks, since it is a system-wide setting for the user.

**macOS.** A LaunchAgent at `~/Library/LaunchAgents/sennet.plist` (label
`sennet`) with `RunAtLoad`, `KeepAlive` on crash, and logs in
`~/Library/Logs/sennet/sennet.log`, loaded with `launchctl bootstrap gui/<uid>`.
`stop` boots it out, since `KeepAlive` would restart a killed agent. There's no Mac on the development machine, so
this is written against Apple's documentation and needs a real Mac before release.

**PATH.** The agents are found on `PATH`, and a service doesn't get the login shell's:
`claude` in `~/.local/bin`, `codex` under nvm or Homebrew would be missing. `install`
records the installing shell's `PATH` in the unit, and `doctor` checks each agent from
the service's `PATH` as well as its own, and says to rerun `service install` when they
differ. Resolving each agent to an absolute path was rejected: nvm and Homebrew paths
change on every Node or package upgrade.

**Running twice.** `serve` in a terminal while the service holds the port fails with a
message naming the service and how to stop it, rather than a bare "address in use";
another program on the port gets its own message. `service install` refuses while an
`sennet serve` outside the service holds the port. The pairing link `serve` prints
on first run goes to the journal under the service, which is why `service install` and
setup print one themselves when nothing is paired.

### Upgrade and uninstall

- **`sennet --version`**, and the version in `doctor`, `/api/health`, `/api/state`
  and Settings → About.
- **`sennet upgrade`** runs `uv tool upgrade sennet` (which keeps the extra
  from the receipt), then restarts the service. A restart ends running tasks, which
  recovery marks `unknown`, so `upgrade` first reads the database for running or
  background tasks and asks before going on (`--yes` doesn't). Outside a uv tool install
  from the index (a checkout, a wheel file, git) it says how to update that instead.
- **The client notices a new version.** An open tab would keep the old client against
  the new server. The client remembers the version it first loaded with, compares it with
  `/api/state`'s on each reconnect, and offers to reload when it differs.
- **`sennet service uninstall`** stops and removes the unit or agent.
  `uv tool uninstall sennet` then removes the program.
- **`sennet remove-data`** lists the config and data directories with their sizes,
  asks (default no; `--yes` doesn't), and deletes them. It refuses while the service runs
  or anything holds the port, since a Sennet too old to answer `/api/health` is still
  using the data.

### Notices

- `LICENSE` and `NOTICES` go into the wheel's metadata (`license-files`).
- The built client bundles npm packages (React, Base UI, the markdown stack and others)
  whose MIT and ISC licenses ask for their notice to travel with the code. A Vite plugin
  (`frontend/licenses.ts`) writes `licenses.txt` beside the client: every package in the
  bundle, found from the bundle's own modules and assets plus the stylesheet's `@import`s
  (which Tailwind inlines), with its license file. It also copies NOTICES to
  `notices.txt`. Both are static files, served without pairing like the app shell.
- **Settings → About** shows the version and license, names the speech models and the
  GPL-3 components, and links to both files. HLD §5.1 requires Parakeet's CC-BY
  attribution in the app as well as in NOTICES; this is where it goes.
- NOTICES listed Instrument Serif, which the client no longer uses; it now lists
  Newsreader.
- shadcn/ui's stylesheet is copied into `frontend/src/shadcn.css` rather than installed
  (the `shadcn` package is its CLI, which had 7 audit warnings), so `licenses.txt` can't
  find it from a package; NOTICES carries its MIT notice instead.

### Release CI

GitHub Actions, two workflows.

**On every push and pull request:**

- Backend: `uv sync --extra cpu`, `pytest`, on Ubuntu and macOS.
- Client: `npm ci`, lint, unit tests, `npm run build`, and the Playwright suites
  (desktop and phone) on Ubuntu. Screenshot baselines are Linux-only, as now.
- Package: build the wheel and sdist after the client, check them
  (`scripts/check-dist.sh`: the client, the notices, no caches or models, under 10 MB),
  and run the smoke test below.

The `models` tests download about 3 GB and need a GPU for the CUDA tier, so they stay
manual, run before a release on the development machine.

**On a `v*` tag:**

- Check the tag matches `__version__`, which is the package's only version
  (`pyproject.toml` reads it).
- Build the client and the wheel and sdist, as above.
- Smoke test the built wheel on Ubuntu and macOS (`scripts/smoke-test.sh`): install it
  with `uv tool install` into a throwaway tool directory, check `--version`, start
  `serve`, and check `/api/health`, the app shell, `licenses.txt` and `notices.txt`, that
  `/api/state` answers 401, that a pairing link is printed, and that `doctor` runs.
- Publish with trusted publishing (no stored token): a tag to PyPI, then a GitHub release
  with the wheel, sdist and generated notes; running the workflow by hand publishes to
  TestPyPI instead.

The sdist holds the package, its tests, the client's source and the built client; not
the spikes, whose models and caches are gigabytes.

## Steps

### 1. Service

Done (029529c). `service.py` and `service install | uninstall | start | stop | restart |
status | logs`, the recorded `PATH`, the "already running" messages, `/api/health`, and
`doctor` reporting the version, the client, and whether the service runs this install,
answers with this version, and finds each agent on its `PATH`. Tests write the unit and
plist into a temporary home and stand in for `systemctl` and `launchctl`. GitHub's
runners have no systemd user session, so the real unit was tried by hand here: install,
status, logs, stop, start, restart, a `kill -9` (back in 5 s), reinstall while running,
and uninstall. launchd is untested.

### 2. Setup and doctor

Done (a1a7e77). The Tailscale serve and service steps in `setup`, linger on Linux, and
the pairing link at the end. Serving over something else Tailscale serves warns and
defaults to no; a refusal suggests `tailscale set --operator`. An installed service is
updated and restarted, so it picks up the new settings. A running service is stopped while
setup runs and started again however it ends (across its re-exec after a runtime
reinstall too): it holds the speech models, so free GPU memory read low (setup
recommended CPU) and the speech check ran out of memory. Later (aa92361), `doctor`'s
findings are coloured by severity and setup calls each tier's reply time its "voice delay".

### 3. Version, upgrade and data removal

Done (33037e6). `install.py`: `upgrade` with its running-task check, `remove-data`, and
how the install is recognised (moved from setup). The version in `/api/state`, the reload
offer and Settings → About, with a browser test that drops the events socket and comes
back "upgraded". Later (05975f6), setup learned to reinstall a wheel-file install with
the extra it picks, so a local build goes through the same path as PyPI, and the extra no
longer vanishes from its messages (Rich took `[cuda]` for markup).

### 4. Notices

Done (2d461cf). License files in the wheel's metadata, `licenses.txt` and `notices.txt`
from the client build, Settings → About, the sdist limited to what it needs (it was
picking up 6 GB of spike models), and `scripts/smoke-test.sh`. Later (49ab7bd), shadcn's
stylesheet was copied in, with its notice in NOTICES.

### 5. CI

Done (7449d68). `.github/workflows/test.yml` (backend on Ubuntu and macOS, client, package)
and `release.yml` (build, smoke test on both, publish, GitHub release), checked with
actionlint. Not run yet: the repository has no remote. The Playwright screenshots were
made on this machine and may need regenerating on the runner.

### Tried

A clean install on a laptop from a locally built wheel (`uv tool install` of the file,
then `setup`), with a phone paired; everything worked. `uv tool install` picked the
newest Python it could (3.14, against `>=3.12`), and the speech stack worked on it.

## Open questions

- **The name.** `agent-talk` was taken on PyPI by an unrelated project (an "email-like
  messaging platform for AI agents"), so the project is now Sennet: `sennet` was free on
  PyPI, TestPyPI and npm when checked (2026-10-23), and nothing else in this space uses
  it. Nothing holds the name until the first real publish. Still to do before release:
  a USPTO and UK IPO search for SENNET in classes 9 and 42, and a domain (sennet.com,
  .dev, .io and .app are taken; getsennet.com looked free).
- **The repository.** It will be `github.com/hey-sennet/sennet`, under a GitHub
  organization (the `sennet` user is taken). Not created yet; CI and trusted publishing
  need it.
- **Linger by default?** Off by default is safer, but a desktop that is left logged out
  is a common case for "talk to my machine from my phone".
- **macOS speech.** The `cpu` extra's onnxruntime includes CoreML on macOS, but no tier
  has been measured there (HLD §12). Release could say macOS is CPU-only until it is.
