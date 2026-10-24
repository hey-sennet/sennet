# Changelog

Notable changes in each release. Versions follow [semantic versioning](https://semver.org/);
until 1.0, a minor version may change how things work.

## 0.1.0 (unreleased)

The first release.

- Talk to Codex and Claude Code from a browser on your phone or desktop: push-to-talk,
  open-mic conversations with barge-in, or typing.
- Local speech: Parakeet speech recognition with live captions, smart-turn end-of-turn
  detection, and Kokoro voices, on CPU or an NVIDIA GPU.
- An optional wake word, from openWakeWord's pre-trained phrases.
- Projects named by voice ("in backend, …"), tasks with follow-ups, and up to three
  running at once.
- Approvals by voice or tap: ask, auto, never or classifier modes, read-only, project or
  full access, remembered "always" rules, and a second confirm for destructive actions.
- Replies read out in a version the agent writes to be heard, and "read me the details"
  for the whole reply.
- The model and effort per project and per task.
- Each agent's five-hour and weekly usage limits in Settings, with reset times, a spoken
  warning when one is nearly used up, a clear message when one is reached, and the tokens
  each task used.
- A task list that starts with the latest tasks, search across every task's conversation,
  and deleting finished tasks.
- Device pairing by link, QR code or typed code, and phone access through Tailscale or a
  reverse proxy.
- `sennet setup`, a user service on Linux and macOS, `sennet doctor`, `upgrade` and
  `remove-data`.
