# Using Sennet

## The screen

The disc in the middle shows who's talking: cream lines move with your voice, blue with
the agent's, and amber when an approval is waiting. Your words appear under it as you
speak, and the agent's as it replies.

The label above your words says where they'll go: a new task in the selected project, or
a follow-up to the selected task. Beside it is the model the agent will use; tap it to
pick another model or effort, for the next new task or for the selected task's next
message.

The dock along the bottom has, from left to right:

- **New task** (`N`): the next request starts a new task instead of following up.
- **Conversation**: open the mic, so you can just talk.
- **Mute replies**: silence the agent on this device. Replies still come, with their
  captions, and play on unheard; unmuting picks up where they've got to.
- **Type instead** (`/`): a keyboard, for when you can't talk. A typed request is
  answered aloud like a spoken one; mute the device if you'd rather read.
- **Tasks**: your tasks, with their status, timeline, commands and file changes, and a
  search box.

## Projects and tasks

A request goes to a **project**: a directory with an agent and a name. Add
projects with `sennet project add` or in Settings. Each request starts a **task**, which
keeps its conversation with the agent, so follow-ups pick up where it left off. Tasks in
a project run side by side, so they can edit the same files unless each has its own
worktree (below). Up to eight tasks run at once in all (`tasks.max_concurrent`); more
queue.

### What a task changed

**Changes**, beside a task's **Timeline**, shows everything it changed in one place: each
file with its lines added and removed, opening to its diff, under **Not committed** or
**Committed**, and whether the change as a whole is uncommitted, committed, pushed or, from
its own worktree, merged. The task's name and both tabs stay at the top as it scrolls. A file edited five times
is one diff, from the commit the project was at when the task started to the file as it is
now. It's read-only: ask the agent to commit or undo it.

In its own worktree, everything that changed there is the task's, edits made by commands
such as a formatter included. In the project's own checkout, only the files its edits name
are counted, so another task's or your own edits aren't; other files that changed since it
started are listed below, by name. A task from before 0.3.0 is counted from the commit the
project was at when it was made. Without git, or once a task's worktree is removed, Changes
shows each edit's diff as it was made, one after another.

### A worktree for each task

For a project that's a git repository, turn on **Each task in its own worktree** in its
settings. Each new task then gets a git worktree on a branch of its own,
`sennet/<number>-<words from its title>`, made from the branch the project has checked
out. The worktree is kept in Sennet's data directory, and the agent works there. The task
shows its branch, how many files it changed and its commits not yet in that branch at the
top of its Changes, with what follows.

When the task is done:

- **Merge into main** (or whichever branch it came from) commits whatever the agent left
  uncommitted, titled with the task, then merges the branch in the project's own checkout.
  It fast-forwards when it can and makes a merge commit when it can't. The project has to
  have that branch checked out. If the merge would conflict, nothing is merged: the task
  names the files, and **Ask the agent to resolve it** asks the agent to merge the branch
  in and fix them.
- **Remove worktree** removes its folder and anything left uncommitted in it, and can
  delete the branch too. If you keep the branch, a follow-up puts the worktree back.
- Deleting the task asks whether to keep its worktree and branch, remove the worktree, or
  remove both.

A fresh worktree has none of what isn't committed, such as `node_modules` or `.venv`, so
the agent may need to install them first. Approvals work as in the project: "Always" rules
are the project's, and `workspace` access means the task's worktree. A project with
worktrees doesn't start an agent early for a new task, so the first reply can take a
moment longer.

To aim a request, **select** a project or task on screen, then talk. With a task
selected, what you say is a follow-up to it. Nothing guesses where a request should go:
if no project is selected, Sennet says so instead of picking one.

### The task list

Tasks that need you and tasks at work come first. Below them, **Earlier** shows the ten
most recently finished; **Show more** brings older ones.

A task is named from what you first said, without "um"s and "okay so"s, until the agent
starts its first reply: it writes a short title for the task, which replaces that.

**Search** at the top finds a task by its title or anything said in it, by you or the
agent. Part of a word is enough ("auth" finds "authentication"), and a typo or two in a
longer word still matches. The best 20 show, each with the words it matched.

**Delete** a finished task with the trash button on its row (it appears when you point
at the row), or on a phone by swiping the row left. A task that's still working,
queued or waiting on you has to be stopped first. Deleting removes it from Sennet only:
Claude Code and Codex keep their own copy of the conversation.

## Talking

**Push to talk.** Hold the disc (or, with it focused, the space bar) while you speak, and let go to send.
Slide off the disc before letting go to throw the turn away.

**Conversations.** Tap the conversation button and just talk; Sennet decides when you've
finished from your words and pauses, not from silence alone. Speaking over a reply stops
it, and what you said is sent. A lone word, or "wait" or "hang on", only stops the reply.
A conversation closes after five minutes with nobody talking. Holding the disc during a
conversation keeps the turn open, through pauses, until you let go.

**The wake word.** If you chose one in setup, turn it on for each device under **Wake
word** in Settings. Sennet then listens for the phrase in the browser from your first tap
on the page, with the screen kept on. An ear at the top of the page shows it listening, and
tapping it pauses or resumes. Saying the phrase plays a soft chime, so you know
it's listening, and opens a conversation, which closes again after 10 seconds of quiet.
Nothing leaves the browser until the phrase is heard.

Only the task on screen is spoken. A reply on any other task is kept quiet, and a blue
dot on the task, and on the tasks button, shows there's something to read; it clears when
you open the task, which then offers **Hear what you missed**: the answers it gave since
you left it, oldest first. Any of the agent's messages can be heard again with the play
button below it. Neither plays by itself, so opening a task to read it stays quiet. The
task you just spoke to is the one on screen.

Use headphones or a quiet room for conversations. Sennet cancels its own echo, but a loud
speaker next to the mic can still cut a reply short.

## Images

Tap the image button in the dock to take a photo or choose images, or, on a computer,
paste or drop one anywhere on the page. Up to eight wait above the dock, each with an ×
to take it out, and go with the next thing you say or type: while you talk they light up,
as going with what you're saying. A message can be only images.

Photos are taken in Sennet's own camera rather than the phone's camera app, so a
conversation keeps listening while you take one, and you can talk about what you're
pointing at. Images are shrunk on the device to a long side of 1600 pixels before
they're sent, as WebP where the browser can write it (JPEG otherwise; a screenshot stays
a PNG so its text stays sharp), which also drops what a photo carries besides the
picture, such as where it was taken. A small copy goes with each for the timeline, which
loads the whole image only when you open it. An upload that drops is tried again; one
that still fails can be tapped to try again. They're kept with the task under Sennet's data directory, shown in its
timeline, and deleted with it. Codex and Claude Code both take images.

## What you hear

The agent is asked to say what it's going to do, give short updates as it works, and
start a long reply with a version written to be heard. That version is what's read out;
the full reply is in the timeline. Code, paths and links are left out of speech, and
symbols are said as words.

## Approvals

What an agent may do without asking depends on the project's **approval mode** and
**access**, both set in Settings (or as defaults in [config.toml](configuration.md)).

| Mode | What happens |
| --- | --- |
| Ask first (`ask`, the default) | The agent asks before running anything outside its trusted set. |
| Auto within its access (`auto`) | It works freely within its access and asks only to go beyond it. |
| Never ask (`never`) | Nothing is asked. Anything that needs more access fails. |
| Classifier decides (`classifier`) | Claude Code only. Its own safety classifier allows or blocks each action. It isn't a sandbox, so it can reach outside the project. |

Access is **read only**, **this project** (the agent may edit the project directory, the
default) or **full**. Codex enforces it with its own sandbox. Claude Code's sandbox
doesn't run everywhere, so Sennet uses its permission modes instead, which is stricter in
two cases: with `auto` it asks before commands that aren't read-only, and with `never`
it can't run them at all.

When the agent asks, a chime plays and a card shows what it wants to do. Answer on the
card: **Approve**, **Decline**, or **Always in** the project to approve and remember it
(say, `npm test`). Approvals aren't answered by voice; what you say meanwhile goes to the
agent as a request.

Something hard to undo, like `git push` or `rm`, is shown in red and always asked, even
if you chose Always before. Remembered approvals are listed in Settings, where you can
forget them.

Claude Code loads your own settings, but not a project's `.claude/settings.json` hooks
or `.mcp.json` servers, which would run without asking. Turn them on per project in
Settings, for projects you trust.

## No voice commands

Everything you say goes to the agent, word for word; Sennet doesn't listen for command
words. To stop a reply, press the disc, or in a conversation just start talking. To stop
a task, use **Stop task** under the disc.

## Usage limits

Your Claude and Codex plans have a five-hour limit and a weekly one. In Settings, under
**Agents**, each agent shows how much of each you've used and when it resets. The
numbers come from the agents during their turns, and Sennet asks again when you open
Settings if they're more than a few minutes old. Asking doesn't use any of your limit.

Once you're past 80% of a limit, a line above the label says so. After a spoken reply,
Sennet mentions it once at 80% and again at 95%. If a request hits the limit, you hear
which one and when it resets, such as "Claude Code's 5-hour limit is reached. It resets
at 4 pm." Follow up after that time and the task carries on.

Each task shows the tokens it has used, in and out, beside its project.

## Background work

When Claude Code leaves a command running in the background, the task shows "Working in
the background". When the command ends, a chime plays and the agent's report is read out, if that task is
on screen; otherwise the task gets its blue dot.

## More than one device

Pair as many browsers as you like. A reply is spoken only on the device that asked.
Approvals show with a chime on the device that started the task; other devices show a
quiet "Another task needs you" line, which you can tap to answer there instead.

## Notifications

A phone stops listening, and stops speaking, once Sennet is in the background, so turn on
notifications to hear about your tasks while it is: in Settings, under Notifications,
choose **Notify this device**, allow it when the browser asks, and pick what notifies you:
a task waiting for your approval, finishing, or failing. **Send a test** checks it works.
Tapping a notification opens its task.

An approval's notification says what's asked, such as "Run npm test", and has **Approve**
and **Decline** buttons, so you can answer without opening Sennet; other devices see the
answer as if it was given on the card. A destructive request (the ones shown in red, like
`git push` or `rm`) has only **Open**: it's answered on its card, never from a lock screen.
**Always in this project** is on the card only. Chrome and Edge show the buttons, on Android
and on a computer; on an iPhone or iPad, tap the notification to answer in Sennet.

They are per device, and off until you turn them on. A device with Sennet showing isn't
notified: it hears the reply there. They need Sennet over HTTPS, as with its Tailscale
address. On an iPhone or iPad, add Sennet to your Home Screen first (Share, then Add to
Home Screen) and turn them on in that app; Safari itself can't receive them.

Notifications go through your browser's own push service (Google's for Chrome, Mozilla's
for Firefox, Apple's for Safari), encrypted so that only your device can read them. Sennet
uses no other service. A notification says only what happened, such as "Approval needed",
the task's title, and for an approval what it asks.
