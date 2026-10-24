# Contributing to Sennet

Thanks for helping. Sennet is a small community project, maintained by one person for
now, so a little care up front makes changes quick to review and merge.

## Before you start

- **Bugs:** open an [issue](https://github.com/hey-sennet/sennet/issues) with what you
  did, what happened, and the output of `sennet doctor`.
- **Small fixes** (typos, docs, an obvious bug): send a pull request.
- **Anything bigger** (a feature, a new agent, a change to how voice or approvals
  behave): open an issue first to talk it over, so you don't spend time on something
  that can't be merged.
- **Security problems:** don't open an issue. See [SECURITY.md](SECURITY.md).

## What Sennet is, and isn't

Some decisions are settled, and changes that undo them won't be merged:

- **Agents run through their official CLIs, on the user's own subscription.** No API keys,
  and no calling a provider's API directly.
- **Speech runs locally.** No cloud speech recognition or voices.
- **Nothing guesses where a request goes.** The user selects a task or project, and what
  they say is sent word for word, with no spoken commands. A local model that routed
  requests was tried, and sent them to the wrong projects ([m3-summaries.md](docs/m3-summaries.md)).
- **It runs on the host, as a user service.** No Docker-first install.
- **Self-hosted, free and open source.** Not a hosted service.

The [design](docs/HLD.md) and the milestone notes (`docs/m*.md`) say why things are the
way they are, including what was tried and dropped. Please read the relevant one before
proposing to change something it covers.

## Set up

You need [uv](https://docs.astral.sh/uv/), Node 22 or later, and git. libopus is
optional (`sudo apt install libopus0` or `brew install opus`); without it, the Opus tests
are skipped.

```sh
git clone https://github.com/hey-sennet/sennet.git
cd sennet
(cd frontend && npm ci && npm run build)
uv sync --extra cpu              # or --extra cuda, for GPU speech on Linux
uv run sennet setup              # downloads the speech models; `uv run sennet serve` runs it
```

To keep a development copy apart from an installed Sennet, point it at its own config
and data with `SENNET_HOME=~/sennet-dev`, and run it on another port
(`uv run sennet serve --port 7711`).

For the client with hot reload, run `npm run dev` in `frontend/`. It passes `/api` on to
a server on port 7710.

## Test

```sh
uv run pytest                                  # backend, with fake agents and fake speech
cd frontend
npm run lint                                   # oxlint
npm run test:unit                              # Vitest
npm run build && npx playwright test           # browser tests against the real server
```

The first Playwright run needs `npx playwright install chromium`.

The backend tests need no models, network or agent logins. To also run the real speech
models, after `sennet setup` has downloaded them:

```sh
SENNET_MODELS=~/.local/share/sennet/models uv run pytest -m models
```

To check a change the way users get it, build and install the wheel:

```sh
(cd frontend && npm run build) && uv build
uv tool install --reinstall dist/sennet-*.whl
scripts/smoke-test.sh dist/sennet-*.whl        # what release CI runs on each wheel
```

CI runs the backend tests on Linux and macOS, the client's lint, unit and browser tests,
and a packaging check, on every pull request. They need to pass before a merge.

## Making a change

- **Keep a pull request to one change.** A bug fix and a refactor are two pull requests.
- **Add tests** for what you change. Agents are tested against the fake CLIs in
  `tests/`, speech against `speech/fakes.py`. Tests that need a real agent or real models
  aren't run in CI.
- **Match the code around it**: its naming, comment style and structure. Python is 3.12
  or later with type hints; comments say why, not what.
- **Update the docs** a user or contributor would read: `docs/using.md` for behaviour,
  `docs/configuration.md` for settings, `docs/voice-protocol.md` for the voice socket.
- **Say how you tested it** in the pull request, including anything you tried by hand,
  by voice, or on a phone.
- **Words Sennet says or shows** should be short and plain, and written to be heard.
  Speech can't show formatting, and long sentences are hard to follow aloud.

### Commit messages

The first line says what changed, from the user's side where there is one, as a
sentence: "Hold the words under the disc at a fixed height". The body, if any, says why,
and what was tried. Look at `git log` for examples.

### New dependencies

Sennet is installed by people on their own machines and redistributed, so every
dependency and model needs a licence that allows that. Say what a new one is for and
what its licence is, and add it to [NOTICES](NOTICES) (and `frontend/licenses.ts` for
the client). Avoid anything that needs a system package, Docker or a compiler to
install.

### AI-assisted contributions

Using an AI assistant is fine. You are responsible for what you submit: you've read it,
run it, and can explain it.

## Licence

Sennet is [Apache-2.0](LICENSE). By contributing, you agree that your contribution is
licensed under it, as section 5 of the licence describes. You keep your copyright; there's
no separate agreement to sign.

## Conduct

Everyone taking part is expected to follow the [code of conduct](CODE_OF_CONDUCT.md).
