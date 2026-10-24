# Configuration

Most settings are chosen in `sennet setup` or the app's Settings. Everything else is in
`config.toml`; `sennet doctor` prints where it is (`~/.config/sennet/config.toml` on
Linux, `~/Library/Application Support/sennet/config.toml` on macOS). Restart the service
after editing it: `sennet service restart`. An unknown section or key is an error, so a
typo doesn't go unnoticed.

Every key, with its default:

```toml
[server]
host = "127.0.0.1"      # keep it on loopback; put HTTPS in front (docs/hosting.md)
port = 7710
public_url = ""         # the address phones use, for pairing links; setup fills it from Tailscale
log_level = "info"      # debug | info | warning | error, for `sennet service logs`

[tasks]
max_concurrent = 3      # tasks running at once, across projects; one per project regardless
idle_timeout_s = 600    # how long an agent stays warm after a turn, so follow-ups start at once

[approvals]             # the defaults; each project can override both in Settings
mode = "ask"            # ask | auto | never | classifier (docs/using.md#approvals)
access = "workspace"    # read-only | workspace (edit the project) | full

[speech]
enabled = true          # false: typed tasks only
device = "auto"         # auto | cpu | cuda; setup sets it
threads = 4             # CPU threads per speech model; more is often slower on hybrid CPUs
voice = "af_heart"      # the voice replies are read in; setup plays each one
wake_word = ""          # hey_jarvis | hey_mycroft | hey_rhasspy | alexa, or "" for none; setup sets it

[agents.commands]       # only to run an agent's CLI some other way than the default
# codex = ["codex", "app-server"]
# claude = ["/opt/claude/bin/claude"]
```

## Environment

- `SENNET_HOME`: put the config and data under one directory instead of the usual
  places. Useful for trying a second, separate install.

## Where things are kept

- **Config:** `config.toml`, above.
- **Data:** `sennet.db` (projects, tasks, approvals, paired devices) and `models/` (speech
  and wake word models), in `~/.local/share/sennet` on Linux and
  `~/Library/Application Support/sennet` on macOS.
- **Audio** is never stored.

`sennet remove-data` deletes both.
