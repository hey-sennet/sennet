# M3: spoken replies

Status: done. Date: 2026-10-22.

M3 makes long agent replies pleasant to hear. It set out to do that with a small local
LLM that summarised them, and ended with the agent writing its own spoken version
(step 5); the local LLM was then parked (step 6). Routing stays as M2 built it,
explicit and deterministic.

Steps 1 to 4 describe code that is no longer on main. It is on the `archive/local-llm`
branch, kept for its lessons and to be rebuilt for messages between turns.

M3 began as a "front assistant" that routed requests with tool calls, answered status
questions and announced events (HLD §6.3 as first written). It was built, tried on the
real models and removed; step 2 says why.

## Steps

### 1. llama-server and the model catalog

- A pinned llama.cpp release with per-platform binaries and SHA-256s: Vulkan on Linux
  x64, CPU on Linux x64, Metal on macOS arm64. Downloaded into the data dir, never
  taken from `PATH`.
- LLM entries in the model catalog (GGUF URL, SHA-256, size, RAM/VRAM need,
  licence, tier), alongside the speech models in `speech/models.py` or split out from it.
- `LlamaServer`: starts its own binary on `127.0.0.1` with a free port and a random
  API key, in its own process group; waits for `/health`; restarts with backoff if it
  dies; stops only the process it started. Flags: `--jinja` for the models' chat templates, a
  fixed context size, `--n-gpu-layers` from config, and prompt caching on.
- A config section, now `[summaries]` (step 2): `enabled`, `model`, `gpu_layers`,
  `context`, and `endpoint` for an existing OpenAI-compatible server instead of the
  bundled one (off by default).
- The LLM part of `sennet setup`: recommend a model for the hardware and say why,
  download the binary and model, check them, and run a self-test. (The setup
  docstring said M5 adds this step; it moved to M3, because M3 can't be used without it.)
- The status joins the voice protocol's speech status (`summaries`): `loading`,
  `ready`, `unavailable` or `off`. The daemon starts and serves without it.

Tests: checksums against a local file server, port and key selection, restart after
a kill, that only our child is stopped, and the daemon starting with the model
missing. One opt-in test (`-m llm`) starts the real server.

Done. What it settled:

- Pinned release `b11513` (2026-10-08): `ubuntu-vulkan-x64` and `macos-arm64`. Linux CUDA
  builds exist now, but they need a separate 590 MB CUDA runtime archive; Vulkan stays.
- Auto device choice prefers a discrete GPU by name, then Metal, else the CPU. On the
  M0 laptop, Vulkan also lists the Intel iGPU with 6.7 GB "free" (shared RAM), more
  than the RTX 4060, so picking by free memory would choose the wrong GPU.
- llama-server reads `LLAMA_ARG_*` environment variables, so they are removed from its
  environment; the key goes in a 0600 file (`--api-key-file`), not on the command line.
  `/health` answers without the key; everything else needs it.
- A one-token warm-up runs before `ready`. Vulkan compiles its shaders on the first
  request: 25 s once on the RTX 4060, then cached by the driver (0.4 s).
- A daemon that died leaves its llama-server running. The next start stops it, but only
  if the recorded PID still runs our binary.
- Catalog: Qwen3.5 2B and 4B, Q4_K_M (Apache-2.0, February 2026; Qwen3.8 has no small
  sizes).

First numbers, M0 laptop, the routing self-test of the time (a one-line request and
one tool):

| | Memory | Load | First request | Warm |
| --- | --- | --- | --- | --- |
| 2B, RTX 4060 (Vulkan) | 1.45 GB VRAM | 1.5 s | 0.4 s | 0.27–0.3 s |
| 2B, CPU, 4 threads | — | 1.4 s | 4.2 s | 1.8 s |
| 4B, RTX 4060, partly on CPU | 2.7 GB VRAM | 3.4 s | 1.7 s | 1.15 s |

The 4B ran beside the daemon's GPU speech (4 GB), which left room for only part of it.

### 2. A routing assistant, tried and removed

Built in four commits, then reverted (`7fba32b`, `594a5bf`, `aa92a61`, `3a05754`):
a conversation table and context builder, tools (`start_task`, `follow_up`,
`cancel_task`, `task_status`, `read_details`) with names resolved in code, a turn loop
with one dispatch per turn and a direct-mode fallback, and `POST /api/assistant`.

On nine sample transcripts, and then by the user by voice:

- **The 2B could not route.** It called `follow_up("it")` with nothing selected, and
  after each error it guessed another target. It also turned on every optional flag
  it was given.
- **The 4B routed explicit requests but guessed on ambiguous ones.** "Fix the typo in
  the readme." went to whichever project was in the recent context, and "Delete
  everything in the repo." to a project it picked. Code-side guards (withdrawing the
  tools after a failed dispatch, `cancel_task` only on the user's word) stopped the
  worst cases but not the guessing. In use, it sent requests to the wrong projects.
- **It added little over direct mode.** Naming the project first, or selecting the
  task, is quick and never wrong. A wrong project costs more than saying its name.

What stays from it: the `llm` package, setup's model step, and this lesson. Summaries
need no tools, and a summary can be checked against its source.

### 3. Summaries

`voice/summary.py`, used by `VoiceHub` for every agent message it speaks:

- A message that is at most three sentences and 300 characters once cleaned for
  speech is spoken as it is, without the model.
- A longer one goes to the model (code blocks replaced by a marker, cut at 12,000
  characters) with a prompt to tell the user the outcome, as "you", in one or two
  sentences, with one worked example.
- **The check.** Every number (as digits, or as a word from two to a thousand) and
  every file name in the summary must appear in the message. A summary over 320
  characters or three sentences fails. So does one that talks about "the agent" or
  "the reply" instead of passing it on. A question is kept only when the agent asked
  one: both models offered help the agent never did ("Should I open them now?").
- **Fallbacks.** A summary that fails the check or takes over 8 s is not spoken;
  the message's first two sentences are. The same happens while the model isn't
  `ready`. With `summaries.enabled = false`, messages were spoken in full, as in M2 (until step 5).
- **Order.** Messages are still spoken as they arrive. A task's later messages,
  approval questions and failure wait for a summary in progress, so they are heard in
  order. A short message with nothing ahead of it is spoken at once, so a preamble is
  still heard before the approval it leads to.
- **"Read me the details"** (and "details", "read it all", "read the full reply") is a
  fixed command like "stop". It reads the selected task's last reply in full, or else
  the one this session last heard; the outcome is `details`.
- Setup's self-test summarises a sample reply and fails if the summary fails the check.
  Setup recommends the smallest model that fits, the 2B: summarising needs no more.
- Config: `[summaries]` replaces `[assistant]`. An old `[assistant]` section is still
  read, and `mode = "direct"` becomes `enabled = false`.

Measured on the 14 long messages among 56 real Codex replies in the M0 machine's
database (12 run), RTX 4060 with Vulkan, speech not loaded:

| | Passed the check | Time per summary |
| --- | --- | --- |
| 2B, GPU | 11 of 12 | 0.28–0.72 s |
| 2B, CPU, 4 threads | 11 of 12 | 2.2–10.9 s |
| 4B, GPU | 12 of 12 | 3.4–6.1 s |

The 4B's summaries were better, but too slow for a reply the user is waiting on. The
check catches invented numbers, file names and offers, not every wrong claim: the 2B
once said a mortgage offer was "generated for" the bank's adviser. "Read me the
details" and the timeline are the way to the exact words. On the CPU the longest
messages pass the 8 s limit and fall back to their opening.

### 4. Less summary, more rewriting

In use, one or two sentences dropped too much: what changed and why was lost, and
"read me the details" became the usual way to hear a reply. The model now rewrites a
long reply for listening instead of summarising it: every point kept, led by the
outcome, with code, markdown and repetition left out and long lists summed up.

- Spoken as it is: up to six sentences and 600 characters (was three and 300).
- The rewrite may be up to ten sentences and 1,000 characters (was three and 320),
  with up to 320 tokens (was 120). The prompt's example keeps the detail.
- The fallback speaks the first four sentences (was two).
- `speakable.for_speech` says symbols as words, before any of this: currency
  ("$24" → "24 dollars", "£24.50" → "24 pounds 50", "$1.2bn" → "1.2 billion dollars",
  "30 GBP" → "30 pounds"), "%", "°C", "~", "&", "->", "#123", "2x", "3-5", "e.g.", "vs",
  and drops emoji and stray markdown. This applies to every spoken message, rewritten
  or not.

### 5. The agent writes the spoken version

The local model rewrites well enough to be pleasant but not faithfully enough to trust
with long replies, and only the agent knows what mattered in its turn. So agents are
now asked, through Codex's `developerInstructions` on `thread/start` and
`thread/resume`, to start a long message with a version written to be heard, in
`<spoken>` tags (`agents.base.SPOKEN_INSTRUCTIONS`). It comes first so it can be spoken
while the rest is still being written: the hub reads the message's deltas and speaks
the block when its closing tag arrives, then skips it when the message completes. The
timeline and the stage hide it; "read me the details" reads the reply without it.

A long message without one is cut to its opening sentences, or rewritten by the local
model when summaries are on. They are off on the M0 machine for now. With summaries
off, a long reply is no longer spoken in full.

The first real tasks showed one more thing to rule out. Asked to "ask me anything you
need to decide first", Codex called `request_user_input_async` twice mid-turn. That tool
posts the questions as an ordinary agent message, returns `{"accepted": true}` at once
and lets the turn carry on, so the questions and their options were read aloud as
sentences (one cut off at the opening-sentences limit), nothing waited for an answer,
and the final reply asked them all again. The instructions now tell the agent to keep
its questions for the end of its final message.

### 6. The local LLM is parked

With spoken versions from the agent, the local model only handled the long replies
that came without one, and the opening sentences do well enough there. It added a
32 MB binary, a model download, a setup step and about 1,100 lines, so it was taken out
of main: the `llm` package, setup's model step, `[summaries]` (an existing section is
skipped on load and dropped on the next save), the `summaries` speech status, and
`sennet doctor`'s check. What stays: the length threshold and the four-sentence
fallback, symbols as words, spoken versions, "read me the details", and setup's
guided lists and voice previews.

When a local LLM returns, it is for messages between turns (status, announcements),
built afresh. From this milestone, keep: run only our own llama-server, on a random
loopback port with a per-start key (HLD §5.2); pick a discrete GPU by name, not by
free memory; warm it up before `ready`; give it no tools that choose a target; and
check its output against its source, since a 2B will invent counts and offers.

## Open questions

- More real replies to tune the length threshold, especially coding replies with test
  counts and file lists.
- How often agents leave out the spoken version, and whether the opening sentences are
  enough when they do.
