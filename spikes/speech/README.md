# M0 speech spike

Throwaway prototype for the voice pipeline in [the design](../../docs/HLD.md) §6:
Silero VAD → smart-turn v3.2 → windowed Parakeet TDT 0.6B v2 captions → full-pass
final transcript → Kokoro TTS. It measures latency and accuracy before we pick defaults.
It is not product code.

## Run it

```sh
uv sync --python 3.12
uv run python fetch.py                  # ~3.5 GB of models + LibriSpeech sample into .cache/
uv run python live.py --device cuda     # or --device cpu; open http://127.0.0.1:8765
```

The live page has two modes: hold to talk, or tap to talk with automatic end-of-turn.
It streams captions, then speaks back "You said: …" and shows each stage's timing.
`smoke_live.py <wav>` drives the same server from a file.

Benchmarks: `bench_stt.py`, `bench_turn.py`, `bench_tts.py` (each takes `--device`).
`probe_*.py` are the one-off investigations behind the findings below.

## Results

Hardware: i7-12650H (16 threads), 14 GB RAM, RTX 4060 Laptop 8 GB, Linux.
Measured with a browser and other apps running.
Data: LibriSpeech validation-clean sample (73 read utterances, CC-BY-4.0).

### Speech to text (Parakeet TDT 0.6B v2 via onnx-asr)

| | GPU fp32 | CPU int8, 4 threads |
| --- | --- | --- |
| Caption update period (target 250 ms) | 250 ms (decode ~80 ms) | 250 ms p50, ~450 ms p95 with 8 s window |
| Final pass, ≤ 15 s utterance | 30–110 ms | 300–770 ms |
| Final pass, 65 s dictation | 389 ms | 4.6 s |
| WER, full pass (73 utterances) | 2.9% | 2.9% |
| WER, live captions | ~10–15% | ~10–16% |
| Model load / RAM | 4 s / ~0.9 GB | 2 s / ~1.3 GB |

- **int8 has no accuracy cost.** WER is 2.87% for both fp32 and int8. Most remaining
  errors are spellings of proper names. Use int8 on CPU and fp32 on GPU (int8 is
  slow under CUDA).
- **CPU threads: 4 is best.** Using all 16 threads was 2–4× slower (P/E cores and
  contention). The default must be a small fixed count, not "all cores".
- **GPU: set `cudnn_conv_algo_search=HEURISTIC`.** The default exhaustive search
  re-tunes for every new input length, and live captions produce a new length every tick.
- **Captions are a preview, not the transcript.** Windowed decoding is roughly 4×
  less accurate than a full pass. The final transcript must always be a full pass.
- **Long utterances on CPU** need incremental finalization: full-pass each segment
  at VAD pauses while the user is still speaking, so only the last segment is
  decoded at the end. A single pass over 65 s takes 4.6 s on CPU.

### End of turn (Silero VAD + smart-turn v3.2)

| | GPU | CPU |
| --- | --- | --- |
| smart-turn decision compute | ~22 ms | ~120 ms (int8 model) |
| Silero VAD per 32 ms chunk | 0.15 ms | 0.12 ms |
| Endpoint delay p50 (speech end → decision) | ~150 ms | ~280 ms |
| Premature cut at a 1 s mid-sentence pause | 26% | 42% |

- **Run smart-turn once per pause.** Re-checking the same pause after more silence
  pushes the score toward "complete" and cut speakers off (an early bug, fixed).
- **Accuracy on this data is not good enough for open-mic mode.** Raising the
  threshold to 0.9 cuts premature ends to 13% (GPU) / 21% (CPU), but only ~50% of
  real ends are then detected quickly; the rest wait for the 1.5–2 s silence fallback.
  The CPU int8 model is clearly worse than the GPU fp32 one.
- **Caveat:** LibriSpeech is read audiobook prose; smart-turn is trained on
  conversational speech. Evaluate on real recorded commands before drawing conclusions.
- **Not a blocker for v1:** push-to-talk ends on release. Open-mic sessions (M6)
  need real-speech evaluation and probably a second signal (transcript completeness).

### Text to speech (Kokoro v1.0 via kokoro-onnx)

| | GPU fp32 | CPU int8, 4 threads |
| --- | --- | --- |
| Short sentence (≤ 70 chars) synthesis | ~125 ms | ~750 ms |
| Longest test sentence (6.9 s audio) | 214 ms | 2.0 s |
| Real-time factor | 0.04 | 0.28 |

Sample WAVs are written to `results/` with `bench_tts.py --save`.

- **CPU needs clause-level chunking.** Kokoro synthesizes a whole sentence before
  any audio plays, so a long first sentence delays the reply (3.4 s seen live).
  Split at clauses and synthesize a short first chunk. Piper is the fallback for very
  slow machines.

### End to end (live server, file streamed in real time)

End of speech → first reply audio: **0.3–0.45 s on GPU; ~1.3 s on CPU** for a short
reply (final pass ~0.3 s + Kokoro ~0.9 s), longer for long first sentences until
clause chunking is added.

## Decisions for the design

1. STT runtime: **onnx-asr** (MIT, pip-only, CPU/CUDA/CoreML, timestamps). sherpa-onnx
   was not needed.
2. Tiers: GPU uses fp32 Parakeet + Kokoro fp32; CPU uses int8 Parakeet + Kokoro int8
   with 4 threads. Captions at 250 ms on both, adaptive on slower CPUs.
3. Final transcript: full pass always; incremental per-segment finalization on CPU.
4. Turn detection: single smart-turn check per pause; push-to-talk for v1.
5. TTS: clause-level chunking is required on CPU.

## Open follow-ups

- Real-speech turn-detection evaluation (record ~50 spoken commands with natural pauses).
- Apple Silicon (CoreML) numbers.
- A lower-end CPU (4-core laptop) to set the minimum supported tier.
- smart-turn needs `transformers` only for Whisper log-mel features. Replace with a
  small numpy implementation in the product.
