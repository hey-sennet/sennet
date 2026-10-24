# "Hey Sennet" wake word

Trains an [openWakeWord](https://github.com/dscripka/openWakeWord) model for "hey sennet"
(said like "senate"). The output is a ~200 KB `hey_sennet.onnx` that openWakeWord's runtime
loads directly.

The web client runs openWakeWord in the browser (frontend/src/voice/wakeword.ts). `sennet setup`
downloads its models, and the phrase you choose (`speech.wake_word`), into
`~/.local/share/sennet/models/openwakeword-v0.5.1/`, pinned and
checksummed in src/sennet/speech/models.py, with openWakeWord's pre-trained phrases to choose from
until this model is trained (they're CC BY-NC-SA, so they're never committed or packaged). To try the
trained model, copy `hey_sennet.onnx` there and link `wakeword.onnx` to it; the server prefers it:

```sh
cd ~/.local/share/sennet/models/openwakeword-v0.5.1
ln -sfn hey_sennet.onnx wakeword.onnx
```

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
converted. Any 16–24 GB NVIDIA GPU is enough (RTX 4090, L4, A5000); the model is tiny. Many
vCPUs help more, since augmentation runs on the CPU. Rough timings: generation ~1 h,
augmentation and features 1–2 h, training under an hour.

1. Create a pod from a PyTorch template (it needs `git` and `curl`) with a 100 GB volume
   at `/workspace`. Prefer the pod's own volume to a network volume: training reads the
   17 GB features file at random.
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
   clips. Copy the model back, then stop the pod:

   ```sh
   scp -P <port> root@<pod ip>:/workspace/wakeword-data/output/hey_sennet.onnx .
   ```

If anything fails, rerun `runpod.sh`: generation tops up the clips it's missing, and
augmentation is skipped once the features exist. To change the phrases or negatives after
generating, delete the matching folders under `wakeword-data/output/hey_sennet/` (and its
`.npy` features), or they're reused.

## Pins

openWakeWord's training dependencies date from 2022 (torch 1.13, TensorFlow 2.8). This uses
torch 2.8, which runs on current GPUs, and leaves out TensorFlow (only for `.tflite` export).
openWakeWord and piper-sample-generator are pinned to commits from December 2025 and March 2026.
Upstream changes may break the fixes in `train.py`, so move the pins deliberately.
