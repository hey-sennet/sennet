<!-- The disc is Strata's (design/icons.py), as `sennet setup` draws it; blanks are U+2800 so it stays aligned. -->
```text
⠀⠀⠀⣀⣀⣀⣤⠶⠚⠛⠛⠓⠶⣤⣀⣀⣀⠀⠀⠀
⠀⠈⠉⠉⠉⠁⢀⡤⠞⠋⠙⠳⢤⡀⠈⠉⠉⠉⠁⠀
⢠⣤⡤⠴⠶⠚⠉⠀⣀⣤⣤⣀⠀⠉⠓⠶⠦⢤⣤⡄   S E N N E T.
⠀⠀⠀⠀⢀⣠⡴⠛⠁⠀⠀⠈⠛⢦⣄⡀⠀⠀⠀⠀   Talk to your coding agents.
⠀⠛⠛⠋⠉⠁⠀⠀⣀⣀⣀⣀⠀⠀⠈⠉⠙⠛⠛⠀
⠀⠀⢤⣤⣤⠴⠖⠛⠉⠁⠈⠉⠛⠲⠦⣤⣤⡤⠀⠀
```

Sennet is a free, open source, self-hosted voice interface for coding agents. Speak a
request from your phone or desktop, let Codex or Claude Code work on your own computer
under your existing subscription, approve what it wants to do, and hear what it did.

- **Your agents, your subscription.** Sennet drives the official agent CLIs under your
  own login. It never holds API keys or calls a provider itself.
- **Speech stays on your machine.** Recognition and the voice run locally, on CPU or an
  NVIDIA GPU. The only cloud traffic is what the agent CLIs already send.
- **Talk like a conversation.** Push to talk, or open the mic and just talk: interrupt
  a reply by speaking over it, or start with a wake word.
- **You stay in charge.** The agent asks before running anything outside its trusted
  set, with a chime and a card you answer with a tap.
- **From anywhere.** Pair your phone once and reach Sennet over
  [Tailscale](https://tailscale.com) or any HTTPS reverse proxy.

**Status:** first release coming (0.1). Codex and Claude Code work today; Gemini CLI is
coming. Linux and macOS, English only. See the [roadmap](https://github.com/hey-sennet/sennet/blob/main/docs/roadmap.md).

## Quick start

You need Linux or macOS, [uv](https://docs.astral.sh/uv/), and at least one agent
logged in: the [Codex CLI](https://developers.openai.com/codex) (`codex login`) or
[Claude Code](https://code.claude.com/docs) (`claude auth login`, on a Claude
subscription).

```sh
uv tool install --python 3.14 sennet
sennet setup                                   # speech, the phone address, the background service
sennet project add ~/code/my-app --name app    # a project you can talk to
sennet pair                                    # a one-time link or QR code for this browser or your phone
```

Open the link `sennet pair` prints, pick your project, hold the disc and talk. `sennet
doctor` checks everything if something doesn't work.

Setup looks at your hardware and recommends where speech runs. It downloads about 0.8 GB
of speech models for CPU, or 2.9 GB for a GPU.

## Documentation

For users:

- [Install and set up](https://github.com/hey-sennet/sennet/blob/main/docs/install.md): requirements, setup, the service, upgrading and removing
- [Reach Sennet from your phone](https://github.com/hey-sennet/sennet/blob/main/docs/hosting.md): Tailscale, Caddy or nginx, and pairing
- [Using Sennet](https://github.com/hey-sennet/sennet/blob/main/docs/using.md): projects, talking, conversations, the wake word, approvals and voice commands
- [Configuration](https://github.com/hey-sennet/sennet/blob/main/docs/configuration.md): every setting in `config.toml`
- [Troubleshooting](https://github.com/hey-sennet/sennet/blob/main/docs/troubleshooting.md)

For contributors:

- [Contributing](https://github.com/hey-sennet/sennet/blob/main/CONTRIBUTING.md): dev setup, tests and how changes are made
- [Architecture](https://github.com/hey-sennet/sennet/blob/main/docs/architecture.md): a tour of the code
- [All documents](https://github.com/hey-sennet/sennet/blob/main/docs/README.md), including the design and the milestone notes

## Security

Sennet lets a paired device run an agent on your computer, as you. Only pair devices you
own, keep the server on its default loopback address behind HTTPS, and unpair devices you
stop using. To report a vulnerability, see [SECURITY.md](https://github.com/hey-sennet/sennet/blob/main/SECURITY.md).

## Licence

Sennet's own code is [Apache-2.0](https://github.com/hey-sennet/sennet/blob/main/LICENSE). Its speech dependencies include phonemizer and
espeak-ng, which are GPL-3, so an install that bundles Sennet with them is GPL-3. The
speech models carry their own licences, and the optional wake words are for
non-commercial use only. All of it is listed in [NOTICES](https://github.com/hey-sennet/sennet/blob/main/NOTICES).
