# Architecture

A tour of the code for contributors. The reasoning behind it is in the
[high-level design](HLD.md); this says where things are.

## The shape

Sennet is one Python process, the daemon, run as a user service. It serves a React web
client, keeps projects and tasks in SQLite, runs speech locally, and drives the agent
CLIs as child processes over stdio.

```text
browser (React)  ── HTTPS ──  proxy (Tailscale serve, Caddy…)  ──  daemon on 127.0.0.1:7710
  disc, captions, tasks          /api/…        HTTP commands         server.py ── tasks.py ── agent CLIs
  mic capture, playback          /api/events   live task events                   │            (codex, claude)
  wake word (in a worker)        /api/voice    audio both ways      voice/ ── speech/
```

A few rules hold everywhere, and changes should keep them:

- **Only the agents talk to the cloud.** Speech is local. Sennet never calls a provider's
  API or holds its credentials; agents run under the user's own login.
- **Nothing guesses the target.** A request goes to the selected task or project, or the
  one named first. No model routes requests ([why](m3-summaries.md)).
- **Agents are behind one contract.** Nothing outside `agents/` sees a provider's own
  words, events or modes.
- **Inference never runs on the event loop.** Speech models run in worker threads.
- **The server binds to loopback**, and every request needs a paired device.

## Backend: `src/sennet`

| Module | What it does |
| --- | --- |
| `cli.py` | Every `sennet` command, and `doctor`. |
| `setup.py`, `prompts.py` | `sennet setup` and its terminal UI. |
| `service.py`, `install.py` | The systemd or launchd user service; `upgrade` and `remove-data`. |
| `config.py` | `config.toml` and where data lives. |
| `server.py` | The FastAPI app: HTTP API, the event socket, the voice socket, and the built client. |
| `access.py` | Pairing and paired devices. |
| `db.py` | SQLite: projects, tasks, messages, events, approvals, devices, and migrations. |
| `projects.py` | Project rules shared by the CLI and the API. |
| `tasks.py` | The task manager: runs agent turns, queues follow-ups, keeps agents warm, limits concurrency. |
| `approvals.py` | The approval policy: what's destructive, remembered "always" rules, waiting for an answer. |
| `events.py` | Fans live events out to connected clients. |
| `tailscale.py` | What Tailscale says about this machine, for setup and doctor. |

### `agents/`: the agent adapters

- `base.py` is the contract: the events an adapter emits (`EVENT_FIELDS`), approval
  requests, capabilities, and the `Agent` and `AgentSession` protocols. `check_event`
  holds every adapter to it. It also has `SPOKEN_INSTRUCTIONS`, given to every agent so
  its replies work when heard.
- `codex.py` drives `codex app-server` (JSON-RPC over stdio).
- `claude.py` drives `claude -p` (stream-json over stdio).
- `callbacks.py` is Sennet's own MCP endpoint, which Claude Code calls to ask for
  approvals.
- `registry.py` lists the adapters. A new agent is added there and nowhere else.

See [Adding an agent](adding-an-agent.md).

### `speech/`: local models

Silero VAD (`vad.py`), Parakeet speech to text with live captions (`stt.py`), smart-turn
end-of-turn detection (`turn.py`) and Kokoro text to speech (`tts.py`), all on ONNX
Runtime (`runtime.py`), each in its own worker thread (`worker.py`). `models.py` pins every
model's URL, checksum and licence. `hardware.py` recommends a tier. `fakes.py` has
instant stand-ins, so tests never need the real models.

### `voice/`: the voice pipeline

- `session.py`: one voice session per `/api/voice` socket. Turns come in, speech goes out.
- `frames.py`, `opus.py`: the socket's binary audio frames, as PCM or Opus.
- `recognition.py`: one turn, from audio to live captions to the final transcript.
- `conversation.py`, `endpoint.py`: open-mic conversations, where turns start and end,
  and barge-in.
- `dispatch.py`: what a final transcript asks for: a fixed command, a project named
  first, an approval answer, or a request.
- `hub.py`: all voice sessions, where their transcripts go, and which session hears
  each reply.
- `asking.py`: approvals asked and answered by voice.
- `speakable.py`, `summary.py`: agent text made fit to speak, and long replies cut down.

The wire protocol is in [voice-protocol.md](voice-protocol.md).

## Client: `frontend/`

React, TypeScript and Vite, with shadcn/ui components and Tailwind. `npm run build`
writes the built client into `src/sennet/web/`, which ships inside the wheel; it isn't
committed.

- `src/App.tsx`: the layout and keyboard shortcuts.
- `src/components/`: the stage, dock, captions, task list and timeline, approval card,
  settings, pairing screen.
- `src/strata/`: the disc's animation. The design reference is in `design/`.
- `src/voice/`: mic capture and playback in audio worklets, the voice socket, Opus,
  push-to-talk, and the wake word (openWakeWord on ONNX Runtime Web, in a worker).
- `src/lib/`: the API client and the store.

## Tests

- `tests/`: the backend, with pytest. Agents are scripted fakes (`fake_codex.py`,
  `fake_claude.py`, `scripted_agent.py`) and speech uses `speech/fakes.py`, so the suite
  needs no models, no network and no agent logins. Tests marked
  `models` run the real speech models.
- `frontend/src/**/*.test.ts`: client unit tests, with Vitest.
- `frontend/tests/*.spec.ts`: browser tests, with Playwright, against the real Python
  server with fake agents and speech (`tests/e2e_server.py`), on desktop and phone sizes.

## Other folders

- `docs/`: these documents. The `m*.md` files are milestone notes: what each milestone
  built, what was tried, and why things are the way they are. Read them before
  reopening a decision.
- `design/`: the Strata visual design reference and icon sources. Not product code.
- `spikes/`: measurements and experiments behind decisions (speech, sessions, Claude
  transcripts). Not product code.
- `training/wakeword/`: trains the "hey sennet" wake word model.
- `scripts/`: packaging checks and the smoke test that release CI runs.
