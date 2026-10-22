# Voice protocol (M2)

Status: agreed. Date: 2026-10-22.

How a client streams push-to-talk audio to the daemon and plays back the agent's speech.
It covers the socket, the binary frames, the control and voice messages, and how
"stop" drops stale audio. The pipeline behind it is in [HLD](HLD.md) §6; the M0 spike
used a simpler version of the same shape (`spikes/speech/live.py`).

## 1. A separate voice socket

Voice uses its own WebSocket, `/api/voice`, rather than adding binary frames to
`/api/events`.

- `/api/events` is a fan-out: every client gets every event, and a client that falls
  behind is closed with 1013 so it reloads state. That is right for state and wrong for
  audio, where nothing can be reloaded.
- Voice is per device. Captions, the speaking state and the audio belong to the device
  holding the button, not to every open tab.
- Each socket keeps its own backpressure (§6), so a slow phone downlink never holds up
  task events.

The voice socket gets the same Origin check as `/api/events` (and the Tailscale
identity check once §8 lands). A client opens it when the voice UI mounts and keeps it
open, because replies are spoken over it even when the user isn't talking.

Nothing new is published on `/api/events`. A dispatched transcript already appears
there as the task's user `message`, and the agent's reply as its `message`.

## 2. Session

One session per voice socket. The server sends `ready` first:

```json
{"type": "ready", "session": "b3f1…", "generation": 0,
 "input": {"rate": 16000, "format": "pcm_s16le", "channels": 1},
 "output": {"rate": 24000, "format": "pcm_s16le", "channels": 1},
 "speech": {"stt": "ready", "tts": "ready"}}
```

`speech.stt` and `speech.tts` are `ready`, `loading` or `unavailable`. Until STT is
`ready`, the client shows type-instead only. When a model's status changes later, the
server sends `speech_status` with the same `speech` object.

Push-to-talk is per device: each session has at most one open turn, and sessions don't
block each other. A `start` while this session's own turn is still open gets
`voice_error` `busy`. STT is still one shared instance (HLD §4.3), so when two devices
talk at once their decodes queue and captions update less often; final passes go ahead
of captions.

## 3. Binary frames

All integers are little-endian.

**Uplink (client → server): raw PCM, no header.** 16 kHz mono PCM16, 20 ms per frame
(640 bytes) recommended; the server accepts any whole number of samples up to 100 ms, so
the last frame of a turn may be a partial one.
Frames are only valid between `start` and `end` (§4) and belong to that turn. WebSocket
ordering is enough, so the frames carry no turn id or timestamp. Frames outside a turn
are ignored and counted in debug logs.

**Downlink (server → client): 8-byte header, then PCM.**

| Offset | Size | Field |
| --- | --- | --- |
| 0 | u8 | version, `1` |
| 1 | u8 | kind, `1` = audio |
| 2 | u16 | segment, matching a `speech` message (§5) |
| 4 | u32 | playback generation |
| 8 | … | 24 kHz mono PCM16 (Kokoro's native rate; the browser resamples on playback) |

Chunks are about 100 ms (4800 bytes of audio). A client that sees a version or kind it
doesn't know drops the frame.

## 4. Client → server messages

JSON text frames.

| Message | Meaning |
| --- | --- |
| `{"type": "start", "turn": "t7", "task_id": 12, "project_id": 3}` | The talk control was pressed. Audio frames follow. `task_id` is the selected task, or null to start a new task in `project_id`. Stops any speech on this session (§7). |
| `{"type": "end", "turn": "t7"}` | Released. The server runs the final pass. |
| `{"type": "cancel", "turn": "t7"}` | Discard the turn (slid off the button, or held too briefly). No final pass. |
| `{"type": "stop"}` | Stop speaking. Audio only; agent work carries on (HLD §6.4). |

The client picks the `turn` id (any short string, unique per session), so it can tag its
own UI before the server answers. A message whose `turn` doesn't match the open turn is
ignored.

## 5. Server → client messages

| Message | Meaning |
| --- | --- |
| `{"type": "voice_state", "state": "listening", "turn": "t7"}` | `idle`, `listening` (turn open), `transcribing` (final pass running), `speaking`. Sent on every change. |
| `{"type": "caption", "turn": "t7", "committed": "run the", "provisional": "tests in back"}` | Every ~250 ms while listening; the interval adapts on slow CPUs (HLD §6.2). Preview only, never dispatched. Each caption replaces the last. |
| `{"type": "final", "turn": "t7", "text": "Run the tests in backend.", "outcome": "dispatched", "task_id": 12}` | The full-pass transcript and what happened to it (below). |
| `{"type": "speech", "generation": 4, "segment": 0, "text": "All 40 tests pass.", "task_id": 12, "approval_id": null}` | Sent just before a segment's audio, so the client can caption the agent's words. `task_id` and `approval_id` say what it is about. |
| `{"type": "asking", "approval_id": 9}` | This session is now asking about the approval: its next turn answers it (§8). Nothing is spoken; the client plays the approval chime when the approval's card shows. |
| `{"type": "speech_end", "generation": 4}` | The last segment of this generation has been sent. |
| `{"type": "stopped", "generation": 5}` | Reply to `stop` or `start`: the new generation. |
| `{"type": "voice_error", "turn": "t7", "code": "uplink_behind", "message": "…"}` | Codes: `busy` (this session already has a turn open), `stt_unavailable`, `tts_unavailable`, `uplink_behind`, `too_long` (over 5 minutes), `bad_frame`, `bad_message` (unreadable control message), `no_target`, `dispatch_failed`. A turn that fails with any of these is discarded. |

`final.outcome` is one of:

- `dispatched`: sent to `task_id` (a follow-up, or the task created for `project_id`).
- `approval`: taken as the answer to the approval being asked (§8). `approval_id` replaces
  `task_id`, and `decision` is `approve`, `approve_always`, `decline` or `cancel`, or null
  when the card answered first while the turn was being transcribed.
- `reprompt`: an approval was being asked and the words weren't a clear answer, or
  "always" can't apply. The question is put again. `confirm: true` when a destructive
  request got its "yes" and now needs "confirm".
- `stop`: the whole transcript was "stop" or "cancel" (§9).
- `details`: the whole transcript was "read me the details" (§9). `task_id`'s last reply
  is read in full.
- `empty`: no speech found. Nothing is sent.
- `failed`: the transcript couldn't be delivered (`no_target` or `dispatch_failed`, sent
  just before). The text is included so the client can offer to type it instead.

## 6. Backpressure and failure

- **Uplink:** the server buffers up to 2 s of audio per turn. If recognition falls
  further behind, the turn fails with `uplink_behind`, the rest of its frames are
  dropped, and the client says so. Words are never dropped silently (HLD §6.1).
- **Downlink:** the server synthesizes one segment ahead of what it has sent and awaits
  each send, so a slow link slows synthesis rather than growing a queue.
- **Socket closed mid-turn:** the turn is discarded. **Closed mid-speech:** the rest of
  the reply isn't spoken; the text is still in the task timeline. On reconnect the
  client gets a fresh `ready` with generation 0.

## 7. Playback generation

The server owns the generation. It starts at 0 and goes up by exactly 1 on `stop`,
on `start`, and on nothing else. Replies queue behind each other in the same generation;
a new reply never cancels one that is playing.

The client keeps a `floor` and plays only frames with `generation >= floor`. When the
user presses stop or starts talking, the client sets `floor = latest + 1` (where
`latest` is the highest generation it has received) and silences its scheduler at once,
without waiting a round trip. The server applies the same +1, so the two agree; the
`stopped` reply confirms the value. Frames already in flight carry the old generation
and are dropped on arrival.

## 8. Voice approvals

The fixed flow from HLD §4.4:

- An approval is asked on the session that last dispatched to that task, between turns
  only (never while `listening` or `transcribing`). If that session isn't connected,
  only the card shows. The card and the voice are both live; whichever answers first
  wins, and the other side follows the `approval` event on `/api/events`.
- The question isn't spoken. The client plays a short chime (a low singing bowl, tapped
  twice) as the approval's card shows, on any device, over any speech still playing
  (which dips under it), and again for each approval after it; the card says what the
  agent wants to do.
- The question takes its place among replies like speech: after anything already queued,
  and never during a turn. When its turn comes the server sends `asking`, and from then
  the session's next turn answers it instead of going to a task. A turn open before then
  is an ordinary request, and the question is asked after it.
- Only a clear answer counts, as the whole transcript: yes, yeah, go ahead, okay →
  `approve`; no, nope, don't, decline → `decline`; always, yes always → `approve_always`
  (only where `can_always` is true; otherwise "That one can't be approved always");
  cancel → `cancel`. "Stop" stops the question and leaves it open. Anything else gets
  `reprompt`: "Sorry, I need a clear answer. Say yes, no, or always."
- Destructive requests need a second step: after "yes", the server says "This could be
  hard to undo. Say confirm to go ahead, or no.", and only "confirm" approves. A second "yes" is not a confirm.
- A decision is acknowledged ("Approved.", "Declined.", "Approved, and I'll remember that
  for backend.") and recorded as `decided_by: "voice"`.
- Several pending approvals are asked one at a time, oldest first.

## 9. Turns and replies

- The final transcript goes to `start.task_id` as a follow-up, or starts a task in
  `start.project_id`. With neither, the turn fails with `no_target`.
- A transcript that opens with a project name or alias set off by a comma or colon
  ("in backend, …", "API: …") goes to that project instead, with the routing phrase
  removed: as a follow-up when the selected task is in that project, else as a new
  task. The rest is sent verbatim. Without the comma or colon it isn't a route ("API
  docs need updating" is a request); a wrong project is worse than none. A project name
  on its own sends nothing (`empty`).
- A transcript that is only "stop" (or "stop speaking", "be quiet") or "cancel" (or
  "cancel that") isn't dispatched. "Stop" stops speech; "cancel" interrupts the selected
  task, and fails with `no_target` when none is selected. Both end with outcome `stop`.
- Each agent message is spoken as it arrives, on the session that dispatched the
  request and on no other, split into clauses with a short first chunk (HLD §6.4).
  Markdown, code blocks, inline code, URLs and file paths are stripped or replaced
  with a short word ("a code block"). Symbols are said as words ("$24" is "24 dollars",
  "£3.50" "3 pounds 50", "~5%" "about 5 percent", "->" "to"); emoji are dropped.
- Agents are asked (developer instructions, `agents.base.SPOKEN_INSTRUCTIONS`) to start a
  long message with a version written to be heard, in `<spoken>` tags. The hub follows the
  message as it streams and speaks that version as soon as its closing tag arrives, before
  the rest is written; the timeline hides it, and "read me the details" leaves it out.
- A long message without one, longer than six sentences or 600 characters, is cut to
  its first four sentences.
- "Read me the details" (or "details", "read it all", "read the full reply") reads the
  last reply in full: the selected task's, or else the one this session last heard. With
  neither, it fails with `no_target`. The outcome is `details`.

## 10. Mapping to Strata

For Q8, and not part of the wire format.

| Strata state | When |
| --- | --- |
| `listening` | `voice_state` is `listening` |
| `thinking` | `voice_state` is `transcribing`, or the target task is `running` |
| `speaking` | `voice_state` is `speaking` |
| `approval` | an approval is pending, and the session is neither listening nor speaking |
| `idle` | otherwise |

## 11. Decisions

- Voice has its own socket, `/api/voice` (§1).
- Push-to-talk is per device: one open turn per session (§2).
- "Confirm" is the confirmation word for destructive requests (§8).
- No retraction window in M2. Push-to-talk already has two chances to back out before
  anything is sent: slide off to `cancel` the turn, or release and say "cancel" as the
  next turn to interrupt the task (§9). A retraction window would hold every dispatch
  for a few seconds, or race the agent starting. Routing stays explicit, so a wrong
  target is unlikely.
- A reply is spoken only on the session that dispatched it (§9).
- No model chooses the target. A local model that routed requests was tried in M3 and
  removed: it sent ambiguous requests to the wrong project (docs/m3-summaries.md).
