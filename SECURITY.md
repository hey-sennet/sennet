# Security

A paired device can run a coding agent on your computer, as you. So a way past pairing,
or a way to make an agent do something its approval settings don't allow, is serious.
Thank you for reporting it privately.

## Reporting a vulnerability

Use GitHub's private reporting: the repository's **Security** tab, then **Report a
vulnerability** ([direct link](https://github.com/hey-sennet/sennet/security/advisories/new)).
Please don't open a public issue or pull request for it.

Say what's affected, how to reproduce it, and what an attacker would need (for example,
a device on the same tailnet, or a paired device). You'll get a reply within a week. This
is a volunteer project, so a fix may take longer, but you'll be kept up to date and
credited in the advisory unless you'd rather not be.

## Supported versions

Fixes go into the latest release only. `sennet upgrade` installs it.

## What's in scope

- Using Sennet without pairing, or pairing without a valid code.
- Getting past an approval: an agent acting beyond its project's approval mode or access
  without being asked, or a remembered "always" rule covering more than it should.
- Reaching Sennet's agent callback endpoint (`/mcp/`) from anywhere but the agents it
  started.
- Sennet sending anything to the cloud other than what the agent CLIs send themselves,
  or storing audio.
- Anything in setup, the service or upgrades that could run code that isn't Sennet's or
  the pinned models' (such as a checksum not being checked).

## What isn't

- **What a paired device does.** A paired device is trusted to run agents as you; that's
  what pairing is for. Unpair devices you stop using.
- **What an agent does within the access you gave it**, or with `full` access, or in the
  `never` or `classifier` modes. Those are the agent's own sandbox and permission modes;
  report problems with them to the agent's maker.
- **Sennet on the public internet** (Tailscale Funnel or an open port) is your choice;
  [docs/hosting.md](docs/hosting.md#on-the-public-internet) explains the risk. Pairing
  weaknesses there are still in scope.
- A misheard transcript sent to an agent. Approvals still apply.

## Keeping your install safe

- Keep the server on its default `127.0.0.1`, with HTTPS in front
  ([docs/hosting.md](docs/hosting.md)).
- Prefer Tailscale or another private network to the public internet.
- Leave approvals on `ask` for projects you care about, and check what "always" rules
  you've made, in Settings.
- Only let Claude Code load a project's own hooks and MCP servers for projects you trust.
- Run `sennet doctor` now and then: it warns when Funnel is on.
