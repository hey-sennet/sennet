# M7 session spikes

The two gates in [the M7 plan](../../docs/m7-sessions.md): step 0 (echo) and step 1 (turn
detection). Throwaway tools, not product code. It reuses the M0 spike's environment and
model cache (`../speech`), and everything runs on CPU, so the service can keep the GPU.

Recordings are your voice: they are kept under `.cache/recordings/`, which git ignores, and
never leave this machine. Only the summary numbers below are committed.

## Run it

```sh
cd spikes/sessions
uv run --project ../speech python server.py   # first run synthesizes the replies; then http://127.0.0.1:8790
```

The microphone needs HTTPS off this computer. To use a phone, serve the spike on its own
Tailscale port, next to Sennet's, and turn it off afterwards:

```sh
tailscale serve --bg --https=8443 http://127.0.0.1:8790   # then https://<this machine>.ts.net:8443
tailscale serve --https=8443 off
```

- **`/record`** (step 1): a card describes something to say; say it in your own words, with
  your natural pauses, and press Done when finished. The true end of each turn is the last
  speech before Done; every earlier pause is mid-turn.
- **`/echo`** (step 0): plays real replies with the mic open. "Stay quiet" measures echo
  alone, "Talk over it" measures barge-in speech, "Speak, no reply" is the baseline,
  "Chime" checks the approval chime. The page notes what the browser actually applied.

Then:

```sh
uv run --project ../speech python eval_turn.py      # sweeps end-of-turn settings
uv run --project ../speech python analyze_echo.py   # one row per browser, device, output and setting
uv run --project ../speech python check_features.py # numpy features against transformers
```

## Results so far

### smart-turn without `transformers` (`features.py`)

- The numpy log-mel features match `WhisperFeatureExtractor` to 6e-6 on 14 clips, and
  smart-turn's score to 2e-6 (fp32 model).
- **The int8 build is unstable.** Input noise of 1e-5 moves its score by up to 0.14, and on
  one LibriSpeech clip it gave 0.85 where fp32 gave 0.03. This likely explains M0's "the CPU
  int8 model is clearly worse".
- **fp32 is cheap on CPU:** 24 ms p50 with 4 threads (int8: 12 ms), plus 7 ms for features,
  on the i7-12650H. So the product can use the fp32 build on every tier and run it on CPU,
  leaving the GPU to Parakeet.

### Turn detection (`eval_turn.py`)

82 recordings by one speaker, 2026-10-23: 56 on the laptop mic, 26 on an Android phone, both
in Firefox. 337 s of speech, 37 mid-turn pauses of 224 ms or more. Pressing Done registers as
a short blip of speech; the script drops it, or the real end looks like a mid-turn pause.

| Setting (fp32 smart-turn, 1.5 s fallback) | Cut off | Mean end of speech → decision |
| --- | --- | --- |
| Threshold 0.5, no grace | 11% (9/82) | 311 ms |
| Threshold 0.9, no grace | 7% | 358 ms |
| Threshold 0.5, completeness by last word, no grace | 10% | 448 ms |
| **Threshold 0.5, 0.3 s continuation grace** | **1% (1/82)** | **303 ms** |
| Threshold 0.5, 0.6 s grace | 1% | 303 ms |

- **The cut-offs happen at complete sentences.** "In front end, update React." [0.4 s]
  "…to the latest version." "Tell me about the recent commit history." [0.26 s]
  "…predominantly the last commit." The speaker adds a second thought after a short pause.
  smart-turn is right that the sentence is finished, so no threshold fixes it; that's why
  0.9 helps little, and why a completeness check on the words helps by one point while
  adding 130 ms to every turn. The check is dropped.
- **Continuation fixes it.** Those second thoughts start within 0.3 s of the decision, while
  the final pass would still be running. Rejoining them takes cut-offs from 11% to 1%. A 0.6 s
  grace catches nothing more.
- The one left: a 0.9 s pause after "…in the documents, uh, directory." before "and then
  check how each of those handles errors". Only a longer wait would catch it.
- Short commands ("stop", "yes", "confirm") all ended at the first check: 0/25 cut off.
- int8 at 0.8 cut 0/82 against fp32's 1/82, which is one recording and within noise. fp32
  stays, for the instability above.
- Decision delay is 224 ms at the median (the first check), so the target of 700 ms is met.
  Dispatch adds the final pass, or the grace if it is longer: on GPU about 0.5 s from the end
  of speech, on CPU 0.5 to 1 s. Neither needs the GPU for turn detection.
- Caveats: one speaker, prompted scenarios, Firefox only, and few long pauses (37 in 82
  turns). Worth re-running after real use.

### Echo (`analyze_echo.py`)

57 recordings, 2026-10-23: Firefox on the laptop's speakers, Firefox and Chrome on an Android
phone's speaker, at normal volume, with playback through Web Audio as the product does it
("separate"). Not yet measured: Safari on iOS, headphones, and the other two playback paths.

Echo alone, while the user stays quiet:

| | Laptop Firefox | Phone Firefox | Phone Chrome |
| --- | --- | --- | --- |
| Echo above room noise, AEC on / off | −3 / +18 dB | +8 / +32 dB | −3 / +45 dB |
| Silero speech during the reply, AEC on | 0% | 0% | 0% |
| Longest speech run, AEC on (a false barge-in at ≥ 150 ms) | 32 ms (0/5) | 53 ms (0/3) | 0 ms (0/4) |
| Reply's words transcribed, AEC on / off | none / all | none / all | none / all |
| Approval chime, AEC on | no speech | no speech | no speech |

- **Both browsers' echo cancellation removes the reply completely**, on both speakers.
  Without it, Parakeet transcribes whole replies word for word, so it's required, and the
  step 5 echo check stays as a guard.

Speaking over a reply, AEC on:

- **The canceller damages the user's words while the reply plays.** The clearest evidence
  doesn't depend on reading the prompt word for word: "Stop." said over a reply was
  recognised 2 times in 6 ("Stop stop.", "Done stop."; otherwise "Um let's not", "Oh, dunk."
  or nothing). Read on its own it was right every time. In the first laptop trials only
  27–77% of the speaking time registered as speech, against 100% when read on its own.
- **Stopping the reply quickly helps but doesn't save the first words.** "Interrupt it" stops
  the reply once the mic is 15 dB over the room for 150 ms, as a client-side barge-in would.
  In 9 trials it stopped 8 times, after 125–640 ms of overlap; a lone "stop" never set it off.
  - 3 came through exactly: "Hang on, which file was that in?", "Actually, cancel that and
    look at the login page instead.", "Okay, and can you also update the readme?"
  - 4 lost or changed their first words: "Wait, that's not what I meant" became "Wait,
    that's not one." and "That's no woman."; "Hang on, which file…" became "And then which
    file…"; "No, run the tests in backend first" became "No, we're in the test in the back
    end first."
  - 2 were "Stop." and came out empty.
- Chrome kept more of the speech than Firefox when talking over a whole reply ("Wait, that's
  not what I meant." exactly), but with three trials each that is a hint, not a result.
- Four trials ("Laptop firefox", and the first "talk over retry") ran with AEC off by
  mistake: the page remembered the checkbox from the round's AEC-off trials. It now always
  starts on and warns when off. They are left out of the numbers above.
