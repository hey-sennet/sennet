# Roadmap

What might come after the first release. None of it is promised, and the order may
change. To help with one, or to suggest something, open an issue.

## Next

- **Gemini CLI.** An adapter over ACP, once it can be tested on a real subscription
  ([adding an agent](adding-an-agent.md)).
- **macOS speech numbers** on Apple Silicon, and GPU speech there if it helps.
- **Safari**, on iOS and macOS: conversations and echo cancellation haven't been tested.
- **Images in a message.** Attach a photo or screenshot from any paired device, shrunk on
  the device before it's sent, and shown in the task's timeline. Codex and Claude Code
  both take images in the protocols Sennet already speaks; an agent that can't is shown
  as such.

## Ideas

- **A local front desk.** A small local model in front of the agents that handles what
  doesn't need them: filler and false starts, "is it done yet?", "what's it doing?",
  merging a few quick barge-ins into one message. Real requests are still forwarded word
  for word, it never chooses the project, and it forwards whenever it's unsure, so it
  saves your agent usage without guessing. It would start by measuring how much of what's
  said it could handle. It is also where spoken commands would go, if they come back:
  "stop", "cancel", "read me the details", answering an approval by voice, or sending a
  request to a project you name. Sennet has none of these today, because a word list
  can't tell a command from a request.
- **Announcements between turns**: a short spoken note when a task in another project
  finishes or needs you.
- **Signing in again with a passkey**, and pairing the tailnet's owner without a code.
- **A minimum hardware tier**, measured on a low-end four-core CPU.

## Not planned

- Cloud speech, API-key access to agents, or a hosted service. See
  [CONTRIBUTING.md](../CONTRIBUTING.md#what-sennet-is-and-isnt).
- Windows and native mobile apps, for now.
- Languages other than English, for now.
