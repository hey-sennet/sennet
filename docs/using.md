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
- **Type instead** (`/`): a keyboard, for when you can't talk.
- **Tasks**: your tasks, with their status, timeline, commands and file changes, and a
  search box.

## Projects and tasks

A request goes to a **project**: a directory with an agent and a name. Add
projects with `sennet project add` or in Settings. Each request starts a **task**, which
keeps its conversation with the agent, so follow-ups pick up where it left off. Tasks in
a project run side by side, so keep them out of each other's way (a git worktree each,
say) if they edit the same files. Up to eight tasks run at once in all
(`tasks.max_concurrent`); more queue.

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
you open the task. The task you just spoke to is the one on screen.

Use headphones or a quiet room for conversations. Sennet cancels its own echo, but a loud
speaker next to the mic can still cut a reply short.

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

They are per device, and off until you turn them on. A device with Sennet showing isn't
notified: it hears the reply there. They need Sennet over HTTPS, as with its Tailscale
address. On an iPhone or iPad, add Sennet to your Home Screen first (Share, then Add to
Home Screen) and turn them on in that app; Safari itself can't receive them.

Notifications go through your browser's own push service (Google's for Chrome, Mozilla's
for Firefox, Apple's for Safari), encrypted so that only your device can read them. Sennet
uses no other service. A notification says only what happened, such as "Approval needed",
and the task's title.
