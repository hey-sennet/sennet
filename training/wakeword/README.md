# "Hey Sennet" wake word

Trains an [openWakeWord](https://github.com/dscripka/openWakeWord) model for "hey sennet"
(said like "senate"). The output is a ~200 KB `hey_sennet.onnx` that openWakeWord's runtime
loads directly.

The web client runs openWakeWord in the browser (frontend/src/voice/wakeword.ts). `sennet setup`
downloads its models, and the phrase you choose (`speech.wake_word`), into
`~/.local/share/sennet/models/openwakeword-v0.5.1/`, pinned and
checksummed in src/sennet/speech/models.py, with openWakeWord's pre-trained phrases to choose from
until a trained model ships with setup (they're CC BY-NC-SA, so they're never committed or
packaged). To try a trained model, copy it there under a versioned name and link `wakeword.onnx` to
it; the server prefers it, reads it on each request (no restart; reload the page), and takes the
phrase from the name without its version (`_v2`):

```sh
cp hey_sennet.onnx ~/.local/share/sennet/models/openwakeword-v0.5.1/hey_sennet_v2.onnx
cd ~/.local/share/sennet/models/openwakeword-v0.5.1
ln -sfn hey_sennet_v2.onnx wakeword.onnx    # back: ln -sfn hey_sennet_v1.onnx wakeword.onnx
```

`sennet doctor` shows which model the wake word is using.

`reference.py` speaks a few phrases with the training voice and scores them with openWakeWord
itself, into a fixture `wakeword.test.ts` checks the browser's scores against.

How openWakeWord trains: a TTS voice model (piper-sample-generator's LibriTTS-R generator,
904 speakers mixed) speaks the phrase 100,000 times, and speaks as many sound-alike phrases
("hey sonnet", "hey senator", "haley sennet", "senate"…) as negatives. Each clip gets mixed
with room echo, background noise and music, and goes through openWakeWord's frozen speech
embedding. A small network then learns to tell the phrase apart, against ~2,000 hours of
precomputed general audio as further negatives. It tunes itself against ~11 hours of
validation audio to aim for 0.2 false wakes an hour.

- `hey_sennet.yml`: the training config (phrases, negatives, sizes).
- `fetch.py`: downloads the voice model, datasets and features into `data/`.
- `train.py`: runs openWakeWord's `train.py` with fixes for what has drifted upstream.
  Its docstring lists them; the one that matters most is resampling the TTS clips to 16 kHz.
- `runpod.sh`: the whole run on a GPU pod.
- `reference.py`: the fixture that checks the browser's pipeline against openWakeWord.

## Check the setup locally

```sh
cd training/wakeword
uv sync --python 3.12
uv run python fetch.py --smoke     # ~500 MB into data/
uv run python train.py --smoke     # ~1 minute; writes data/smoke-output/hey_sennet.onnx
```

The smoke model is meaningless (300 clips, 300 steps); the run only proves every stage
works. Run it on a GPU with ~3 GB free.

## Train on RunPod

The full run needs about 60 GB of disk: 17 GB of negative features, ~4 GB of background
audio, ~7 GB of generated clips, an ~8 GB venv, plus a 7.7 GB music archive while it's
converted. Any 16–24 GB NVIDIA GPU is enough (RTX 4090, L4, A5000); the model is tiny.

The first run (24 October 2026, an RTX 4090 at $0.89/hour) took 5 hours, about $4.50: setup
and downloads 15 minutes, generation 3 hours, augmentation and features 1.5 hours, training
15 minutes. Most of the generation time was converting clips to 16 kHz on one core, which now
runs on all of them. Augmentation is still one process (openWakeWord's), at ~40 minutes per
100,000 clips.

1. Create a pod from RunPod's "Runpod Pytorch 2.8.0" template with a 100 GB volume at
   `/workspace` and SSH (`22/tcp`) open. On secure cloud that volume is network storage,
   which reads the 17 GB features file at random; it was fine with the pod's RAM to cache it.
2. Copy this directory up (there's no git remote), without `data/` and `.venv/`:

   ```sh
   # -rlt, not -a: the volume refuses chown, so owners and groups can't be kept.
   rsync -rltv --exclude data --exclude .venv -e "ssh -p <port>" \
       training/wakeword/ root@<pod ip>:/workspace/wakeword/
   ```

3. On the pod:

   ```sh
   cd /workspace/wakeword
   nohup ./runpod.sh > /workspace/wakeword.log 2>&1 &
   tail -f /workspace/wakeword.log
   ```

4. At the end the log prints the false positives per hour, recall and accuracy on the held-out
   clips, and the pod stops itself 15 minutes later, done or failed (`STOP_DELAY` sets the
   wait in seconds, `STOP_POD=0` keeps it running). Copy the model off in that window: a stopped
   pod restarts only on its own machine, and once its GPU is rented out "Start Pod using CPUs"
   (enough to copy files) leaves it CPU-only for good, with migration often finding no GPU.
   Starting a new pod is then quicker than waiting.

   ```sh
   scp -P <port> root@<pod ip>:/workspace/wakeword-data/output/hey_sennet.onnx .
   ```

   The SSH port changes on every start.

   Terminate the pod once you have what you need: the stopped volume bills until then.

If anything fails, rerun `runpod.sh`: generation tops up the clips it's missing, and
augmentation is skipped once the features exist. To change the phrases or negatives after
generating, delete the matching folders under `wakeword-data/output/hey_sennet/` and all four
`.npy` feature files (augmentation redoes all four or none), or they're reused. Changing only
the negatives keeps the 110,000 positive clips, which saves about an hour and a half.

## Results

| Model | Recall | False wakes/hour | Accuracy | Notes |
| --- | --- | --- | --- | --- |
| v1 | 0.39 | 0.0 | 0.70 | Works well in use, but "hey senator" wakes it. |
| v2 | 0.28 | 0.0 | 0.64 | ~1,040 clips of each custom negative ("hey senator"…); positives reused from v1. In use: wakes well to "hey sennet", not to "hey senator" or "okay senator". |

The recall is on held-out synthetic clips with noise and echo mixed in, at a 0.5 threshold, so it
reads lower than it feels with your own voice. v1 woke to "hey senator" because each of the
`custom_negative_phrases` got about one training clip (see `train.py`); `custom_negative_share`
fixes that from v2. With a quarter of its negatives that close to the phrase, v2 is stricter, so
its recall on the synthetic clips dropped, but with a real voice (25 October) it still woke well to
"hey sennet" and didn't wake to "hey senator" or "okay senator". It's the one to use.

v2 (24–25 October, RTX 4090 at $0.89/hour) took 6 hours on the pod, about $5.50: setup and
downloads 10 minutes, the 110,000 negative clips 1.5 hours (the positives were copied from
v1's pod, 35 minutes), augmentation and features 3 hours (at ~75 minutes per 100,000 clips,
reading them from network storage), training 25 minutes.

## Next time

Measured during the v2 run (RTX 4090, `/workspace` on network storage), and not yet acted on:

- **The run waits on the disk, not the GPU.** While generating, the GPU was 0–11% busy, with
  4 GB of its 24 GB in use, and the training process used about a fifth of one core. In 34 of 40
  samples its main thread was waiting on the network filesystem (`request_wait_answer`): each batch
  of 100 clips takes the GPU well under a second, then about 4 seconds to write to `/workspace`.
  The pod-to-pod copy of the 110,000 positive clips (2.6 GB) took 35 minutes for the same reason.
- **So a cheaper GPU would do.** An RTX A5000 ($0.27/hour) or RTX 3090 ($0.50/hour) should take
  about as long as the 4090 ($0.89/hour), for a third to half the price. Check stock first: neither
  had any on secure cloud on 24 October.
- **Write the clips to the container disk.** Generating and augmenting on the pod's local disk,
  keeping only the features (and the model) on `/workspace`, should make generation several
  times faster. Local disk is wiped when the pod stops, so a failed run would regenerate its
  clips, and the container disk needs ~60 GB rather than 30.
- **Size the resampling pool to the pod.** `os.cpu_count()` reports the host's cores (48),
  not the pod's limit (~13.6 CPUs on that pod, in `/sys/fs/cgroup/cpu.max`). It was harmless
  in v2, but `train.py` should read the limit.
- **Augmentation is still one process** (openWakeWord's): ~40 minutes per 100,000 clips in v1,
  ~75 in v2 reading from network storage. Splitting it across cores means replacing that step
  of upstream's `train.py`.
- **Keep the GPU pod until the model is off it.** A stopped pod restarts only on its own
  machine. On 24 October the 4090 there was rented within the hour, so "Start Pod using
  CPUs" was the only way to get at its files and migrating found no GPU, so v2 ran on a new pod,
  with the positive clips streamed across from the old one (CPU-only) over SSH:

  ```sh
  ssh -A -p <old port> root@<old ip> 'cd /workspace/wakeword-data/output/hey_sennet &&
      tar cf - positive_train positive_test |
      ssh -p <new port> root@<new ip> "tar xf - -C /workspace/wakeword-data/output/hey_sennet"'
  ```

  `runpod.sh` now waits 15 minutes before stopping the pod, so the model can be copied off first.

## Pins

openWakeWord's training dependencies date from 2022 (torch 1.13, TensorFlow 2.8). This uses
torch 2.8, which runs on current GPUs, and leaves out TensorFlow (only for `.tflite` export).
openWakeWord and piper-sample-generator are pinned to commits from December 2025 and March 2026.
Upstream changes may break the fixes in `train.py`, so move the pins deliberately.
