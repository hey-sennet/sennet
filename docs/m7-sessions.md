# M7: conversation sessions

Status: built and tested on fake engines, in the browser tests and on the real models;
waiting on a try on real devices (step 7). Measurements in
[spikes/sessions](../spikes/sessions/README.md). Date: 2026-10-23.

M7 adds the conversation from HLD §6.5: the mic stays open, the server decides where turns
begin and end, and talking over a reply stops it (barge-in). Push-to-talk is unchanged and
works inside a conversation too. The protocol is voice-protocol.md §12.

Two risks decided whether this could ship, so each was measured on real recordings before
product code was written:

1. **Premature end of turn.** M0 cut speakers off at 26% (GPU) / 42% (CPU) of 1 s
   mid-sentence pauses. In open mic a premature cut sends half a request to an agent.
2. **Echo.** If Sennet hears its own voice it either interrupts itself or sends its own
   words as a request.

## Decisions

- **Push-to-talk is unchanged.** A conversation is a toggle in the dock. Pressing the disc
  during one holds the turn being heard (or opens one) until it is let go, ignoring pauses.
- **End of turn is smart-turn plus continuation.** No model chooses or rewrites anything.
  A check on whether the transcript looked finished was tried and dropped: the cut-offs
  happen at finished sentences, so it helped by one point and slowed every turn by 130 ms.
- **smart-turn's fp32 build runs on CPU, on every tier.** The int8 build is unstable, and
  fp32 costs about 25 ms on four threads. Its features are computed in NumPy, so the
  product doesn't need `transformers`.
- **Barge-in stops the reply, and sends what you say.** A turn that starts over a reply
  ends by smart-turn like any other, and a request said over it is sent. The echo
  canceller damages the first words said over a reply ("stop" was recognised 2 times in
  6 in step 0), but that was single short words; whole sentences said over replies have
  transcribed well. The risk is accepted for a live feel: having to say it again into
  silence was easy to miss and looked like nothing happened. A lone word, or a "wait" or
  "hang on", only stops the reply (outcome `interrupted`). Fixed commands and approval
  answers are acted on as before. (Until 2026-10-23 such a request wasn't sent; Sennet
  said "Go ahead." instead.)
- **No resuming after a barge-in.** An empty barge-in transcript was usually a "stop" the
  canceller swallowed, so the reply stays stopped. It is in the timeline, and "read me the
  details" reads it.
- **The client doesn't stop playback on its own mic level.** The server detects barge-in
  within about 160 ms of speech plus a round trip. Stopping sooner would save a fraction
  of a second, and a level trigger would fire on noise the VAD ignores.
- **Echo is handled in layers:** the browser's echo cancellation, a faster start over a
  reply that echo alone doesn't reach, and a check that drops a transcript made mostly of
  what Sennet just said. Server-side echo cancellation wasn't needed in Firefox or Chrome.
- **Words are never dropped silently** (HLD §6.1). Audio outside a turn holds no words; a
  turn opens as soon as speech is heard, with 300 ms of pre-roll. A barge-in that only
  stopped the reply comes back as outcome `interrupted`.
- **Raw audio is still never stored.** The spikes recorded locally as dev tools.
- **Safari is a known gap.** Untested for lack of a device: its echo cancellation, and
  whether an open mic moves playback to the earpiece on iOS.

## Design

### Turns without a button

`voice/conversation.py` runs the open mic on one voice socket. Voice activity runs over all
of the audio, and `voice/endpoint.py`, a state machine over 32 ms frames, decides where
turns start and end. Each turn is recognised by the push-to-talk `Recognition` and
delivered by the same hub.

- **Start of turn:** six speech frames in eight (about 200 ms), so clicks and coughs don't
  open one. 300 ms of pre-roll keeps the first syllable.
- **End of turn:** smart-turn runs once per pause, after 224 ms of silence, and ends the
  turn above 0.5. 1.5 s of silence ends it regardless. Higher thresholds and a 2 s fallback
  only made turns slower in step 1.
- **Continuation:** if speech resumes within 0.3 s of the decision, the turn carries on
  and is recognised again from its start. Dispatch waits for the later of the final pass
  and those 0.3 s: on CPU the final pass takes longer anyway; on GPU it adds about 0.2 s.
  In step 1 this took cut-offs from 11% to 1% of turns. It is not the retraction window M2
  rejected (voice-protocol §11), which would have held every dispatch for seconds.
- **Filler only** ("um", "uh", "hmm") is `empty`.
- **Limits:** the 2 s uplink backlog and the 5-minute turn, as for push-to-talk. A
  conversation closes itself after five minutes with nobody talking, Sennet included, and
  the client says so.

### Barge-in

The server estimates when the client will finish playing what it was sent; until then a
reply counts as playing.

1. Five speech frames in six (160 ms) open a turn while a reply plays. Echo alone never
   made more than 53 ms with Firefox's or Chrome's cancellation; a lone "stop" over a reply
   never made 300 ms in a row.
2. The server bumps the generation and sends `barge_in`; the client silences playback, as
   for `stopped`. The rest of that agent turn isn't spoken either: an agent often sends
   several messages in one turn ("Let me check the log", then the answer), and the next
   one would otherwise start talking again. A failure is still said. The messages are in
   the timeline, and "read me the details" reads the last.
3. The turn ends as any other does (smart-turn plus continuation), so a whole request is
   heard.
4. A fixed command or an approval answer is acted on; a request is sent. A lone word, or
   a "wait" or "hang on", only stopped the reply: `interrupted`.
5. A transcript that is mostly words spoken in the last 20 s is Sennet's own echo: `empty`.

### Approvals

With the mic always open, a television or another person can say "yes".

- A voice answer counts within 30 s of the chime. A later one asks again (the chime, and a
  fresh 30 s) instead.
- Pressing talk to answer has no window, since it can't be the television.
- Destructive requests keep the "confirm" step (voice-protocol §8).
- The echo check means words in Sennet's own speech never answer an approval.

### Client

- A conversation toggle in the dock, shown when recognition and turn detection are ready.
  The eyebrow reads "Conversation · …" while one is open and nobody is talking.
- A screen wake lock while it is open, where the browser allows one. Hiding the page ends
  it, since phones suspend a hidden page's mic anyway.
- Changing the selection sends `target`, so the next turn goes there.
- The uplink was about 256 kbps while open. With Opus it is about 21 kbps, or about 37 kbps
  counting packet headers ([performance.md](performance.md) steps 5 and 6).

### Setup

smart-turn (32 MB) is a fourth speech model, fetched by `sennet setup` on every tier. It is
optional: an install from before M7 keeps push-to-talk, `doctor` reports conversations as
off, and `ready.speech.turn` says `unavailable` until setup fetches it.

## Steps

### 0. Echo (done for Firefox and Chrome)

`spikes/sessions` `/echo` plays real Kokoro replies with the mic open and keeps what the
mic heard. Firefox on the laptop and an Android phone, and Chrome on Android, all on
speakers: no echo reaches VAD or Parakeet, the chime is never taken for speech, and speech
over a reply is detected but its first words are damaged, even when the reply stops within
a few hundred milliseconds ("stop" was recognised 2 times in 6). Not measured: Safari,
headphones.

### 1. Turn detection (passed)

82 real spoken commands, one speaker, laptop and phone mics, recorded at `/record` and
swept by `eval_turn.py`. Target: under 5% cut off and a median decision under 700 ms.
Result: 1% cut off with continuation, 224 ms median, all on CPU.

### 2–6. Protocol, endpointer, client, barge-in, approvals (done)

- voice-protocol.md §12.
- `speech/turn.py`, `voice/endpoint.py`, `voice/conversation.py`, and the session's routing.
- The dock toggle and `voice.ts`.
- Tests: `tests/test_endpoint.py` frame by frame; `tests/test_conversation.py` over the
  socket on fakes (turns, coughs, continuation, the fallback, pressing to hold, filler,
  retargeting, close and idle close, the turn limit, barge-in, echo, and the approval
  window); one real-model test (`-m models`), in which a request with a 0.35 s breath in it
  goes out as one turn, word for word; and a browser test toggling a conversation and
  holding the disc.

### 7. Real devices (to do)

Use it for real, with Codex and Claude Code: laptop and phone, speakers and headphones.
Watch for turns ending too early in real use, barge-ins that don't fire, and the
television problem below.

## Open questions

- **Background talk.** An open mic in a room with a television or other people will hear
  them, and a turn that is a request goes to the agent. Approvals are guarded; requests
  are not. Is the toggle enough, or does a conversation need a "Sennet, …" prefix, or a
  confirmation before a request in some cases?
- **Tap-only approvals during conversations,** as a setting. Not built; the 30 s window
  is the guard for now.
- **Safari.** As above; no device to test on.
