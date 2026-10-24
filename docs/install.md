# Install and set up

## Requirements

- **Linux or macOS.** Linux x64 is tested most. macOS is supported, but speech runs on
  the CPU there for now. Windows isn't supported.
- **[uv](https://docs.astral.sh/uv/).** It brings its own Python, so there's no Python,
  Node, Docker or compiler to install.
- **At least one agent, logged in:**
  - the [Codex CLI](https://developers.openai.com/codex), logged in with `codex login`, or
  - [Claude Code](https://code.claude.com/docs), logged in with `claude auth login` on
    your Claude subscription.

  Gemini CLI is coming.
- **For speech:** about 1 GB of disk for CPU speech, 3 GB for GPU speech. GPU speech
  needs an NVIDIA GPU with about 5 GB of free memory. Typed tasks work without speech.
- **Recommended:** libopus, which cuts voice traffic to about a tenth (most Linux
  desktops have it; `sudo apt install libopus0`, or `brew install opus` on macOS), and
  [Tailscale](https://tailscale.com) to reach Sennet from your phone.

## Install

```sh
uv tool install sennet
sennet setup
```

Setup looks at the CPU, RAM and GPU, then asks each question as a list of every option.
The recommended one is marked and explained, options that won't fit say so, and running
setup again marks your current settings. `sennet setup --yes` accepts every
recommendation without asking. If you stop it part way, run it again to carry on.

It goes through:

1. **Speech.** Where speech runs, CPU or GPU. Setup installs the matching runtime and
   shows what it will download before it does: about 0.8 GB of models on CPU, 2.9 GB on
   GPU. It then checks that speech recognition hears its own voice.
2. **The voice.** It plays each voice you pick, so you can choose by ear.
3. **A wake word** (optional). A phrase that starts a conversation without touching the
   screen. The phrases on offer are openWakeWord's pre-trained ones, which are for
   non-commercial use only, so setup says so and never picks one for you.
4. **The phone address.** If Tailscale is running, setup finds this computer's HTTPS
   address and offers to run `tailscale serve` for it. See
   [Reach Sennet from your phone](hosting.md).
5. **The background service.** Sennet runs from login: a systemd user service on Linux,
   a LaunchAgent on macOS. No root is needed.
6. **Pairing.** Setup ends with a link and QR code to pair your first browser.

## Add a project

A project is a directory an agent works in, with a name:

```sh
sennet project add ~/code/my-app --name app --agent claude
sennet project list
sennet project remove app          # unregisters it; your files are not touched
```

`--name` defaults to the directory's name. `--agent` is `codex` (the default) or `claude`. You can also add
projects, and change their agent, model and approval mode, in the app's Settings.

## Pair a device

Every browser pairs once before it can use Sennet, including one on the same computer.

```sh
sennet pair
```

prints a link for this computer, a link and QR code for your phone (once there's a phone
address), and a code to type. Each works once, for 15 minutes. A paired device can pair
another from Settings → Devices.

```sh
sennet devices                     # list paired devices
sennet devices revoke 3            # unpair one; it has to pair again to get back in
```

## Check it

```sh
sennet doctor
```

checks the agents and their logins, speech and its models, the wake word, audio, the
phone address and Tailscale, and the service, and says how to fix anything it finds. It
also prints where your config and data live. See [Troubleshooting](troubleshooting.md).

## The service

```sh
sennet service status      # installed, running, and which version answers
sennet service logs -f     # follow the log
sennet service restart     # also: start, stop, install, uninstall
```

A user service stops when you log out. On Linux, to keep Sennet running while you're
logged out (say, on a desktop you reach from your phone), run `loginctl enable-linger`.

Without the service, `sennet serve` runs Sennet in the terminal.

## Upgrade

```sh
sennet upgrade
```

upgrades Sennet and restarts the service, keeping your config, tasks, paired devices and
models. It asks first if tasks are still running (`--yes` doesn't).

## Uninstall

```sh
sennet service uninstall       # stop and remove the service
sennet remove-data             # config, tasks, paired devices and models; your projects are not touched
uv tool uninstall sennet
```

If setup ran `tailscale serve` for Sennet, `tailscale serve reset` stops it. That clears
everything this computer serves on its tailnet address, so check `tailscale serve status`
first if you serve anything else.

## Install from source

Sennet isn't on PyPI until the first release. Until then, or to try a change, build a
wheel from a checkout (this needs Node 22 or later, to build the web client):

```sh
git clone https://github.com/hey-sennet/sennet.git
cd sennet
(cd frontend && npm ci && npm run build)
uv build
uv tool install dist/sennet-*.whl
sennet setup
```

To work on Sennet itself, see [CONTRIBUTING.md](../CONTRIBUTING.md).
