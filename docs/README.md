# Sennet documentation

## Using Sennet

- [Install and set up](install.md)
- [Reach Sennet from your phone](hosting.md): Tailscale, Caddy, nginx and pairing
- [Using Sennet](using.md): projects, talking, conversations, approvals and voice commands
- [Configuration](configuration.md)
- [Troubleshooting](troubleshooting.md)
- [Roadmap](roadmap.md)

## Contributing

- [Contributing](../CONTRIBUTING.md): set up, test, and send a change
- [Architecture](architecture.md): a tour of the code
- [Adding an agent](adding-an-agent.md)
- [Voice protocol](voice-protocol.md): the `/api/voice` socket
- [High-level design](HLD.md): the goals, the architecture and why

## Milestone notes

What each milestone built, what was tried and dropped, and why. Read the one that covers
something before proposing to change it.

- [M2: backend and the voice loop](m2-backend.md)
- [M3: spoken replies](m3-summaries.md), and why no model routes requests
- [M4: more agents](m4-agents.md), including the Claude Code terms
- [M5: access and pairing](m5-access.md)
- [M6: packaging](m6-packaging.md)
- [M7: conversations](m7-sessions.md)
- [Performance](performance.md): speech latency and voice traffic
- [Adapter signals](adapter-signals.md): what the agent CLIs send that Sennet doesn't use yet
