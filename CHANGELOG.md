# Changelog

Notable changes in each release. Versions follow [semantic versioning](https://semver.org/);
until 1.0, a minor version may change how things work.

## Unreleased

- Each task in its own git worktree: a project setting for git repositories. A new task
  gets a worktree on a branch of its own, so tasks side by side don't edit the same files.
  When it's done, merge it into the branch it came from, or remove it, from the task, on
  the phone too. A merge that would conflict isn't started: it names the files, and the
  agent can be asked to resolve them. Deleting the task asks what to do with its worktree
  and branch.
- `sennet doctor` and Settings show which version of Claude Code and Codex is installed,
  and warn, in yellow, when it's newer than Sennet has been tested with (Claude Code 2.1.x,
  Codex 0.164.x). Both CLIs update themselves, and a change in how they talk to Sennet
  could stop a task without saying why. It's a warning, not a problem: Sennet still runs
  them.
- `sennet doctor --report` writes one file for a bug report: doctor's checks, versions,
  hardware, settings and the end of the service log. Project paths and names, the
  machine's name and addresses, links and keys are taken out, and settings are kept only
  when they're known to be safe, so a new one isn't shared by default. Tasks, messages
  and paired devices are never read. The bug report form asks for it.
- Approve or decline from an approval's notification, without opening Sennet. The
  notification says what's asked ("Run npm test") and has Approve and Decline buttons in
  Chrome and Edge. A destructive request, shown in red on its card, has only Open, so it's
  never approved from a lock screen, and "Always in this project" stays on the card. If the
  answer doesn't get through, or the approval was already answered, a notification says so.
- A reply whose spoken version came after a line the agent wrote first showed the
  `<spoken>` tags in its timeline, and voice read the whole reply. The spoken version is
  now found after a short lead too, and the lead stays in the message.
- A typed request is answered aloud, like a spoken one, on the device that typed it, and
  sending it stops a reply that's still being said. Mute the device to read replies
  instead.
- Hear a reply again: each of the agent's messages in a task's timeline has a play
  button. A task with a blue dot, for a reply that came while you were on another task,
  opens with "Hear what you missed", which plays the answers it gave since you left it,
  oldest first. Both are only tapped, so opening a task to read it stays quiet.

## 0.2.0 (2026-10-26)

- Images in a message: take a photo in Sennet, choose images, or paste or drop one, and
  they go with the next thing you say or type, to Codex or Claude Code. They're shrunk on
  the device first, as WebP where the browser can write it, and a small copy goes with
  each for the timeline, which loads the whole image only when you open it. Uploads that
  drop are tried again. Images are deleted with their task. Taking a photo keeps a
  conversation listening.
- The task's timeline shows the agent's other tool calls too: reading and searching
  files, the web, subagents, and MCP servers and connectors such as Gmail, named as
  you'd read them ("Gmail: search threads"). A run of them is one quiet line that opens
  to list each; they're never spoken. A task looked quiet while it used them.
- Work a task left running shows below its timeline, shimmering, until it's done,
  however far you've scrolled; a list of more than two folds to its first. It showed
  only where it started.
- A command shows in the task's timeline as it starts, with light passing over it while
  it runs, and its result takes its place; it showed only once it had finished.
- Tasks get short titles written by their agent at the start of its first reply, in
  place of your first words; until then, those words without "um"s and "okay so"s.
- Push notifications, per device and off by default: a task waiting for approval,
  finishing or failing notifies a device that doesn't have Sennet showing, and tapping
  the notification opens the task. They use the browser's own push service, encrypted
  end to end, with a key of Sennet's own. On iPhone and iPad they need the Home Screen
  app.
- Tasks in one project run side by side instead of queueing behind each other. Only
  `tasks.max_concurrent` limits how many run at once, now 8 by default rather than 3
  (a config file saved earlier keeps the number in it).
- A task's header names its agent as everywhere else ("Codex"), not by its id ("codex").
- Long task titles end in an ellipsis; they were cut mid-letter.
- The dock's keyboard button showed only part of its circle, and after typing could sit
  shifted inside it.
- With notifications blocked for Sennet, "Notify this device" can't be ticked, and the
  note says where to allow them; allowing them shows at once.
- An approval under the disc names the task asking, now that two in one project can
  both be working; tapping it opens the task.
- Screenshots in the README.
- The page declares itself dark, so the browser's own controls and scrollbars match.
  Dark only is a decision, not a gap: see design/README.md.
- A task titled from your first words doesn't show them again as its first message;
  once its agent writes a title, or when the title is cut short, the message shows.
- Settings keeps its title in view as it scrolls, so nothing passes under its close
  button.
- Closing the type bar eases the dock's glyphs back to the middle; they sat to one side
  while it shrank, then jumped.

## 0.1.0 (2026-10-25)

The first release. Linux is the tested platform; macOS is experimental.

- Talk to Codex and Claude Code from a browser on your phone or desktop: push-to-talk,
  open-mic conversations with barge-in, or typing.
- Local speech: Parakeet speech recognition with live captions, smart-turn end-of-turn
  detection, and Kokoro voices, on CPU or an NVIDIA GPU.
- An optional wake word: "hey sennet", trained for Sennet, or one of openWakeWord's
  pre-trained phrases.
- Projects, tasks with follow-ups, and up to three running at once.
- Approvals with a chime and a card: ask, auto, never or classifier modes, read-only,
  project or full access, and remembered "always" rules.
- Replies read out in a version the agent writes to be heard; the timeline has the whole
  reply. No spoken commands: everything said goes to the agent, word for word.
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
