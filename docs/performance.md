# Performance: network data

Status: steps 0, 1 and 3 to 6 done; step 2 proposed. Date: 2026-10-23.

Sennet is used from a phone, often over Tailscale and mobile data. A first test on
rural mobile data felt fast, so nothing here is urgent. This document lists where bytes
go today, what could be sent less of, and in what order to do it. Steps 0 (measure), 1 (compress
and cache), 3 (coalesce deltas), 4 (reload the open task), 5 (Opus) and 6 (larger uplink
frames) are built; the "Where the bytes go" table is from before them.

The first test was push-to-talk. A conversation (M7) changes the voice numbers most: it
streams the mic the whole time it is open, silence included.

## Where the bytes go

Measured in step 0 (results below).

| Traffic | Today | Notes |
| --- | --- | --- |
| Page load | 879 KB cold: JS 619 KB, CSS 69 KB, two fonts 177 KB, API 13 KB | Nothing is compressed, not even by `tailscale serve`. No Cache-Control headers |
| Spoken reply (server → client) | 325 kbps on the wire, 10 messages/s | 24 kHz PCM16; deflate takes only 15% off |
| Push-to-talk (client → server) | 232 kbps on the wire, 50 messages/s | 16 kHz PCM16; deflate takes only 10% off. Only while the button is held |
| Conversation (client → server) | 170 to 240 kbps on the wire, 50 messages/s, the whole time it is open | Silence costs nearly as much as speech: about 200 kbps in a quiet room. An idle conversation stays open 5 minutes |
| Streamed agent text | 26 to 36 messages/s, one TCP packet each, to every device | 10 KB of payload per reply after deflate, but about 85 KB of packet headers |
| Reconnect | `/api/state`: 8 KB (2 KB compressed) | Small. The open task isn't reloaded, which is a bug (below) |
| Task open | `/api/tasks/{id}`: median 6 KB, largest 90 KB (26 KB gzipped) | Command output is over half of the large ones |
| Captions | One small JSON message about every 250 ms while listening | Not measured; not worth changing |

Already fine: fonts are woff2 and split by unicode range, and a cold load fetched only 2
of the 18 font files. `permessage-deflate` is negotiated on both sockets
(`server_max_window_bits=12; client_max_window_bits=12`), so JSON on the sockets crosses
the wire at about 13% of its size. Repeat visits fetch no assets today, but only by
accident (below).

## Steps

### 0. Measure first (done)

The scripts are in `spikes/perf/` (see its README). The setup:

- A second daemon on port 7720 on a copy of the real database (27 tasks, 2 projects),
  with a TCP proxy in front of it that counts bytes per connection. That is TCP payload
  after HTTP and WebSocket compression, before TLS and Tailscale's own headers.
- Chromium headless with the Pixel 7 profile, throttled to DevTools' Slow 4G preset
  (1.6 Mbps down, 750 kbps up, 562 ms round trip), for page loads.
- Two real Codex replies of about 3,700 characters each, streamed over `/api/events`, with
  TCP segment counts from the kernel (`ss -ti`).
- Voice through the same server stack (uvicorn, websockets, default deflate), framed by
  `sennet.voice.frames`, with real audio: 33 s of Kokoro replies down and 188 s of
  LibriSpeech up. The daemon's own speech wasn't loaded twice on the GPU, so captions and
  the voice JSON weren't measured; they're small.
- Conversations with the recordings from `spikes/sessions`: real laptop and Android
  microphones in Firefox and Chrome, captured with the client's own settings (echo
  cancellation and gain control on, noise suppression off). 461 s of spoken requests with
  their pauses, and 537 s of the mic while a reply played on the speakers.

Not measured: a real phone on mobile data, and TLS bytes. Packet overheads over Tailscale
below are estimates.

#### Page load

| | Requests | Bytes | Load event (Slow 4G) |
| --- | --- | --- | --- |
| Cold, today | 10 | 879 KB | 5.1 s |
| Cold, with gzip and cache headers (step 1) | same | about 400 KB | 2.4 s |
| Reload, today | 10 | 14 KB (API only) | 0.6 s |

With compression the JS is 192 KB and the CSS 15 KB. The two fonts (Newsreader 129 KB,
Inter 48 KB) are then nearly half of a cold load; they're already woff2 and can't
compress further. Brotli would make the JS 164 KB instead of 192 KB.

Reloads cost no asset bytes today only because Chrome caches heuristically from
`Last-Modified` when there is no `Cache-Control`. It does that for `index.html` too: on a
new visit it used its cached copy without asking. After an update, a phone can keep
loading the old `index.html`, and so the old bundle, for a while (browsers allow about a
tenth of the file's age). That matters for M6, which ships updates.

The JS bundle by package, from a sourcemap build:

| Part | Size | Share |
| --- | --- | --- |
| react-dom | 202 KB | 33% |
| Markdown (react-markdown, remark-gfm, micromark, mdast, hast and helpers) | 148 KB | 24% |
| @base-ui | 89 KB | 14% |
| Our own code (components 44 KB, voice 11 KB, App 9 KB, Strata 6 KB) | 74 KB | 12% |
| sonner | 33 KB | 5% |
| tailwind-merge | 27 KB | 4% |

#### API

`/api/state` is 8 KB, or 2 KB compressed, and it is all a reconnect fetches. Task opens
range up to 90 KB (task 27, 63 events), 26 KB gzipped. In that task, `command_finished`
events are 50 KB, messages 28 KB and `command_started` 11 KB. All 27 tasks together are
356 KB.

#### Streamed text

| | Reply 1 | Reply 2 |
| --- | --- | --- |
| Deltas | 683 in 26 s (26/s) | 615 in 17 s (36/s) |
| JSON | 77 KB for 3,816 characters (20× the text) | 69 KB for 3,592 characters |
| On the wire after deflate | 10.7 KB | 9.9 KB |
| TCP segments | | 622 for 620 messages |
| Messages if coalesced every 50 ms | 147 | 102 |

Each delta is about 5 characters wrapped in a 110-byte message, most of it the repeated
`item_id`. Deflate removes that repetition, but every message still goes out as its own
packet. Over Tailscale, each packet carries about 135 bytes of headers (TCP/IP 52, TLS
record 22, WireGuard 32, outer UDP/IP 28), and the phone sends ACKs back. So a reply
costs about 85 KB of headers for 10 KB of text, and the phone's radio stays awake for the
whole stream. Every delta is also sent to every device, whether or not it shows that
task, and each one re-renders the task view.

#### Voice

| | Messages/s | PCM | On the wire | Bitrate |
| --- | --- | --- | --- | --- |
| Downlink, 100 ms frames | 10 | 1,529 KB | 1,294 KB (85%) | 325 kbps |
| Uplink, 20 ms frames (today) | 50 | 5,865 KB | 5,304 KB (90%) | 232 kbps |
| Uplink, 40 ms frames | 25 | 5,865 KB | 5,072 KB (86%) | 221 kbps |
| Uplink, 60 ms frames | 17 | 5,865 KB | 4,989 KB (85%) | 218 kbps |

A minute of speech is about 1.7 MB up and 2.4 MB down. LibriSpeech is clean read speech,
so a phone microphone in a noisy room will compress less. At 50 packets/s, uplink headers
add about 54 kbps more (estimated); 60 ms frames cut that to about 18 kbps.

#### Conversations

A conversation sends frames from `open_mic` to `close_mic`, not just during turns, and the
server's voice activity detection needs them all (below). Its uplink, on real microphones:

| Uplink while a conversation is open | Frames | On the wire | Bitrate |
| --- | --- | --- | --- |
| Talking, with natural pauses | 20 ms | 13,330 KB of 14,415 KB (92%) | 237 kbps |
| Talking, with natural pauses | 60 ms | 12,457 KB (86%) | 221 kbps |
| Nobody talking: the quiet stretches only (under -45 dBFS) | 20 ms | 2,605 KB of 3,352 KB (78%) | 199 kbps |
| Nobody talking | 60 ms | 2,384 KB (71%) | 182 kbps |
| Over a reply playing on the speakers | 20 ms | 11,119 KB of 16,787 KB (66%) | 170 kbps |

- **Silence is nearly as dear as speech.** Gain control lifts the room's noise and
  noise suppression is off, so deflate finds little to remove. An open conversation costs
  about 1.5 MB a minute up, plus about 0.4 MB of packet headers (estimated), whether
  anyone talks or not. Five idle minutes before it closes itself cost about 9 MB.
- **During a reply both directions run at once:** 325 kbps down and about 170 kbps up,
  about 0.6 Mbps with headers. Echo cancellation leaves the mic near silent, which is why
  it compresses better than a quiet room.
- **Falling behind now costs a conversation, not a turn.** The server holds 2 s of
  unprocessed uplink (`UPLINK_LIMIT_S` in `voice/conversation.py`). On a weak uplink a
  burst after a stall can pass that, and the open turn fails with `uplink_behind`. With
  push-to-talk this could only happen while the button was held. Not seen yet; it needs
  the real-phone check (open questions).
- The new messages (`open_mic`, `turn`, `barge_in`, `mic`) are small, like captions.

Does the mic need to stream all the time? Transcription only runs inside a turn, but
everything that finds the turns runs on the server over all of the audio: voice activity
(stateful, over an unbroken timeline), 300 ms of pre-roll from before the speech that
opened a turn, 1.5 s of silence to end one, and 160 ms of speech to barge in over a
reply. The long silences between turns are what's wasted, and step 5 can remove them
without moving any of that to the phone.

#### Bugs found

- **The open task isn't reloaded after a reconnect.** `useLiveState` reloads
  `/api/state` when `/api/events` reopens, but `useTaskDetail` keeps the detail it
  fetched when the task was opened. Messages and events sent while the phone was offline
  are missing from the open task until it is opened again (`frontend/src/lib/store.ts`).
- **`index.html` is cached heuristically** (above). Fixed by step 1.

Both are fixed.

### 1. Compress and cache the client (done)

- Add compression to the HTTP responses for the built client and the JSON API. gzip
  (Starlette's `GZipMiddleware`) is enough. Brotli saves 28 KB more on the JS, which
  isn't worth a new dependency. Better still, compress at build time (Vite plugin) and
  serve the `.gz` files directly, so the daemon spends no CPU on it.
- Mark `/assets/*` as `Cache-Control: public, max-age=31536000, immutable`. File names
  are hashed, so this is safe. Mark `index.html` as `no-cache`, so a new build is picked
  up on the next load.
- Leave audio and fonts out of compression; they're already compressed.

Measured: a cold load drops from 879 KB to about 400 KB, and from 5.1 s to 2.4 s on
Slow 4G. Land it before M6, since packaging ships the same built client and its updates
need the `no-cache` index.

Built: the build writes a `.gz` beside each text file over 1 KB (`frontend/gzip.ts`, no
new dependency), and the server sends it to browsers that take gzip. Anything else over
500 bytes, the JSON API included, goes through Starlette's `GZipMiddleware`, which leaves
fonts and audio alone. `/assets/*` is immutable, everything else `no-cache`, with ETags
answered by a 304. A missing `/assets/*` file is now a 404 rather than the app shell, so a
page from an older build doesn't get HTML for a script.

Measured on the real database: a cold load is 391 KB in 2.4 s (was 879 KB in 5.1 s), and
a reload 3 KB (was 14 KB).

### 2. Split the bundle (cheap to medium; retargeted)

Load the markdown renderer lazily: it is 148 KB (24% of the JS) and is only needed once a
task is open. The settings and pairing screens may be worth splitting too. The Strata
canvas is only 6 KB, so leave it in the main bundle. react-dom (202 KB) can't be split.
Expected: roughly 45 KB less gzipped JS before the task list shows (estimated, not built).

### 3. Fewer, bigger text messages (done)

Coalesce streamed `delta` events on the server. Collect deltas per `item_id` and send
one message every 50 ms with the combined text. The client already concatenates deltas,
so the protocol doesn't change; it just receives fewer. Measured: 4.6 to 6 times fewer
messages, so a reply goes from about 95 KB on the wire (counting packet headers) to about
25 KB (estimated). It also cuts React re-renders on the phone.

Built: each `/api/events` socket joins its deltas per item over 50 ms
(`events.batches`). Any other event first sends the text held before it. The voice hub
still gets every delta as it comes. Measured on one Codex reply of 3,650 characters:

| | Before | After |
| --- | --- | --- |
| Delta messages | 683 (26/s) | 116 (6/s) |
| JSON | 77 KB | 16 KB |
| On the wire after deflate | 10.7 KB | 6.0 KB |
| TCP segments | 622 | about 120 |
| With Tailscale's packet headers (estimated) | about 95 KB | about 22 KB |

"Before" is step 0's first reply, so the comparison is between two replies of about the
same length. The 5.9 times fewer messages match what step 0 predicted.

Also stop sending deltas to devices that aren't showing that task. That needs the client
to tell the server which task it is viewing, which is a small protocol addition, so do it
only if several devices are often open at once.

### 4. Reconnects and task loads (fix done; the rest dropped)

- Fix the bug above: reload the open task when `/api/events` reopens, as `/api/state` is.
  Done: `useLiveState` counts reconnects and `useTaskDetail` fetches the task again on
  each. Text that was streaming when the socket dropped has a gap until its message
  completes. Covered by a Playwright test that cuts the socket off from the server.
- Event sequence numbers and replay aren't worth it for bytes: a full reconnect is 2 KB
  compressed. Reconsider only if reconnect state grows a lot.
- Don't trim `/api/tasks/{id}`: the largest is 26 KB once step 1 compresses it.

### 5. Opus audio (done)

Opus is a compression format made for speech. Deflate already ran on the PCM frames and
saved only 10 to 15%, so there was nothing cheaper to try first.

Built:

- **Licensing.** libopus is BSD-3-Clause, and Opus's patents are licensed royalty-free.
  It isn't bundled: `voice/opus.py` loads the system's libopus with ctypes. Nearly every
  Linux desktop has it (PipeWire depends on it); on macOS it is `brew install opus`. No
  Python package fitted. PyAV is the only maintained wheel with libopus inside, but it
  also bundles FFmpeg built with x264 and x265, which makes the wheel GPL, adds H.264 and
  HEVC to a voice app, and weighs 34 MB. `opuslib` needs the system library anyway, and
  `opuspy` and `ruopus` have no wheels for Python 3.12. NOTICES lists libopus, and
  `sennet doctor` says whether it is there.
- **Protocol** (voice-protocol §3). `ready` lists `codecs`, and the client picks one per
  direction with a `codecs` message. PCM stays the default and the fallback: a browser
  without WebCodecs Opus, or a server without libopus, carries on as before. A new frame
  kind, 2, carries Opus each way. Uplink frames carry a stream number and a sample
  position, so the server can place each packet on its timeline.
- **Uplink.** The browser encodes with WebCodecs `AudioEncoder` at 16 kHz, 24 kbps, VoIP,
  DTX on, and the server decodes before voice activity and the recognizer. Chrome sends
  nothing at all during DTX, not even Opus's 400 ms refreshes, so the server's timeline
  would stall in a silence, and the end of a turn is found in silence. The client
  therefore sends a header-only frame saying how far the silence runs: every 100 ms while
  a turn is open, every second otherwise. The server fills the gap with silence.
- **Downlink.** The server encodes speech at 24 kHz and 32 kbps, in 20 ms packets, five
  to a frame, so frames are still about 100 ms. The browser decodes with WebCodecs
  `AudioDecoder`. One encoder runs for the whole session. On step 0's 33 s of Kokoro
  replies: 33 kbps including headers, against 325 kbps for PCM after deflate, at the same
  10 messages a second. Encoding takes about 7 ms of CPU per second of speech.

Checked against PCM before it became the default (`spikes/perf/opus_eval.py`): every
`spikes/sessions` recording through the server's own turn-finding (Silero, the Endpointer,
smart-turn) and Parakeet, all on CPU. "Dither" is the control: PCM with inaudible noise,
which shows how much the models vary by themselves.

| Open mic, 82 recordings | PCM | Dither | Opus 20 ms | Opus 60 ms |
| --- | --- | --- | --- | --- |
| Words changed vs PCM | | 0.0% | 2.8% | 2.9% |
| Recordings with a different number of turns | | 0 | 2 | 2 |
| End of the last turn vs PCM | | | +5 ms on average | -3 ms on average |
| Uplink payload | 256 kbps | | 21 kbps | 21 kbps |
| Packets per second | 50 | | 46 | 15 |

| A reply playing, 57 recordings | PCM | Dither | Opus 20 ms | Opus 60 ms |
| --- | --- | --- | --- | --- |
| False barge-ins (staying quiet) | 8 of 24 | 8 | 8 | 8 |
| Barge-ins found (talking over it) | 24 of 25 | 24 | 24 | 24 |
| Barge-in opened vs PCM | | +2 ms | +6 ms on average | +22 ms on average |
| Uplink payload | 256 kbps | | 12 kbps | 13 kbps |

The words Opus changed are different rather than worse: of the 17 open-mic recordings
whose transcript changed, the Opus one was closer to what the card asked for in about 8
("commit" for PCM's "come out", "replay" for "riplan", "router, the cache" for "route of
the cache") and further in about 3 ("docks" for "docs"). One long turn was split in two,
and one ended 940 ms later. False barge-ins are the same 8 in every condition: they come
from the recordings without echo cancellation, not from Opus.

**DTX does almost nothing on real microphones.** Gain control lifts a quiet room to about
-50 dBFS, and noise suppression is off, so libopus hears the room as sound: DTX engaged in
3 of 115 quiet stretches of a second or more. It does engage over a reply, where echo
cancellation leaves the microphone near silent (29 packets a second instead of 46 at 20 ms
frames). So an idle conversation costs what talking does, but far less than before:

| Idle conversation, with ~135 bytes of headers per packet over Tailscale | kbps |
| --- | --- |
| PCM, 20 ms frames (before) | about 254 |
| Opus, 20 ms frames | about 71 |
| Opus, 60 ms frames (now) | about 37 |

Five idle minutes before a conversation closes itself now cost about 1.4 MB instead of
about 9 MB. Near nothing would need a gate on the phone (below), and is an open question.

Covered by `tests/test_opus.py` (framing, gaps, a conversation whose turn ends during
DTX, the PCM fallback) and two Playwright tests that look at the socket's frames: Opus
both ways in Chromium, and PCM when WebCodecs is taken away.

The alternative to DTX was a gate on the phone: run voice activity in the browser, send
only around speech (with pre-roll and a tail), and tell the server how long each gap was.
It would get an idle conversation near zero, on Opus or PCM, but it moves turn-finding
decisions to the phone, and a missed start loses words.

### 6. Larger uplink frames (done, with step 5)

The browser sends 60 ms Opus packets (`UPLINK_FRAME_MS` in `frontend/src/voice/opus.ts`).
Measured above: a third of the packets of 20 ms, which saves about 34 kbps of headers over
Tailscale, for a barge-in 16 ms later on average (the latest, 100 ms after PCM's against
60 ms for 20 ms packets) and no change in words or turns. The end of a turn didn't move. The capture worklet still makes 20 ms frames; the
encoder gathers them into packets.

## Suggested order

| Step | Size | Do when |
| --- | --- | --- |
| 0. Measure | small | done |
| 1. Compress and cache the client | small | done |
| 3. Coalesce deltas | small | done |
| 4. Reload the open task on reconnect | small | done |
| 2. Lazy-load markdown | small | after step 1, if first load still matters |
| 5. Opus, 6. Larger uplink frames | large | done |

Steps 1, 3 and 4 are independent of M6 and M7. Steps 5 and 6 still need a real phone.

## Open questions

- Is a real phone on mobile data worth a check before step 1, or are the throttled
  numbers enough? They are consistent with "felt fast": the slow part is a cold load,
  which a phone does rarely. A conversation is different: it needs a steady 0.2 to
  0.6 Mbps for as long as it is open, so try one on mobile data during M7's real-device
  check and watch for `uplink_behind`.
- Do any supported browsers lack WebCodecs `AudioDecoder` and `AudioEncoder`? Chromium
  has both (checked); Firefox and Safari on phones are untested. Without them the client
  uses PCM. Is that enough, or is a wasm codec worth shipping?
- Opus is the default wherever both sides support it. Try it on real phones: Android
  Chrome and Firefox, and an iPhone, listening for the reply's quality as well as the
  turns.
- Is a phone-side gate worth it, to take an idle conversation from about 37 kbps to near
  nothing (step 5)? It moves the decision of when speech starts to the phone.
