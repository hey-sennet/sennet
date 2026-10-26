# Troubleshooting

Start with:

```sh
sennet doctor
```

It checks each part and prints a fix beside anything in red. The log has more:
`sennet service logs -f`, or `log_level = "debug"` under `[server]` in `config.toml` (then
`sennet service restart`) for more detail.

## Install and setup

- **The page shows only an error or a blank screen after installing from source.** The
  web client wasn't built into the wheel. Run `(cd frontend && npm ci && npm run build)`,
  then `uv build` again.
- **Setup stopped half way.** Run `sennet setup` again; it picks up where it left off and
  marks what you chose before.
- **GPU speech isn't offered, or says it won't fit.** It needs an NVIDIA GPU with about
  5 GB free. Close what else is using the GPU, or choose CPU speech: it's slower to reply
  (about 1.3 s rather than 0.4 s) but works everywhere.
- **"no ONNX runtime installed" or "models missing".** Run `sennet setup` again. It
  reinstalls the runtime for the device you choose and downloads what's missing.

## Agents

- **An agent shows as not installed or not logged in.** Check it runs in a terminal:
  `codex login` or `claude auth login`, then `sennet doctor` again.
- **"The service can't find codex (or claude) on its PATH".** The service started with a
  different `PATH` from your shell. Run `sennet service install` from a shell where the
  agent runs; it records that shell's `PATH`.
- **A task won't start, and Settings says the agent doesn't support this mode.** The
  classifier mode is Claude Code only. Pick another mode for that project.
- **Settings shows no usage for an agent.** It's only shown for a subscription login. An agent
  logged in with an API key has no plan limits to show.
- **"Claude Code (or Codex) is newer than Sennet has been tested with".** The agent CLI
  updated itself past the versions Sennet was last checked against. Usually nothing
  changes. If something does (a task that never finishes, an approval that never shows, a
  timeline that goes quiet), check for a newer Sennet with `sennet upgrade`, else open an
  [issue](https://github.com/hey-sennet/sennet/issues) with the file `sennet doctor --report` writes.
  Until it's fixed, you can go back to the version before, the way you installed it.
- **The agent can't run commands.** With Claude Code, `never` and `workspace` together
  can't run commands that aren't read-only. Use `ask` or `auto`, or see
  [Approvals](using.md#approvals).

## Speech

- **Nothing happens when you talk.** Check the browser has microphone permission for
  Sennet's address, and that the address is `https://` (or `localhost`).
- **Sennet cuts you off, or interrupts itself.** In a conversation, use headphones or
  turn the speaker down: a loud speaker next to the mic can sound like you talking.
  Push-to-talk never cuts you off.
- **Replies come slowly.** CPU speech takes about a second to reply. `speech.threads` (4 by
  default) can help on some CPUs, but more threads is often slower on laptops with
  performance and efficiency cores.
- **"audio: PCM"** in doctor. libopus isn't installed, so voice uses about ten times the
  data. `sudo apt install libopus0`, or `brew install opus` on macOS, then restart the
  service.
- **There's no wake word in Settings.** No phrase is set up, or its models aren't
  downloaded. Run `sennet setup` and choose one.

## Phone access

See [When the phone can't reach Sennet](hosting.md#when-the-phone-cant-reach-sennet).

## Notifications

- **No Notifications switch, only a note:** the page isn't on HTTPS, or on an iPhone or
  iPad it isn't the Home Screen app. See [Notifications](using.md#notifications).
- **Turning them on fails, or nothing arrives:** check that notifications are allowed for
  the site in the browser's settings and the phone's, and press **Send a test**. If the
  test doesn't arrive, `sennet service logs` says what the push service answered.
- **Nothing while Sennet is open:** that's on purpose; a device with Sennet showing
  hears replies there instead.

## Service

- **"running, but nothing answers on port 7710".** Another program may hold the port, or
  Sennet failed to start; `sennet service logs` says which. Change `server.port` if the
  port is taken.
- **"running 0.1.0, but 0.1.1 is installed".** The service still runs the old version.
  `sennet service restart`, or use `sennet upgrade` next time, which restarts it for you.
- **It stops when you log out** (Linux). Run `loginctl enable-linger` to keep user
  services running.

## Still stuck

Run `sennet doctor --report`. It writes one file with doctor's checks, the versions of
Sennet, Python, your OS, the speech runtime and the agent CLIs, your hardware, your
settings and the end of the service log. Project paths and names, this machine's name and
addresses, links and anything shaped like a key are taken out, and your tasks, messages
and paired devices are never read. Read it anyway before you share it.

Then open an [issue](https://github.com/hey-sennet/sennet/issues) with what you did, what
happened, and that file.
