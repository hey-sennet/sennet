"""Train the "hey sennet" model with openWakeWord's own training script.

    uv run python train.py                     # generate, augment, train (the full run)
    uv run python train.py generate augment    # or any of the three steps
    uv run python train.py --smoke             # a few minutes, to check the setup works

Each step resumes: generation tops up what's missing, and augmentation is skipped when the
features exist (delete <output>/hey_sennet/*.npy to redo it). The model lands in
<data>/output/hey_sennet.onnx.

openWakeWord's train.py runs unchanged, with these fixes put in front of it:

- piper-sample-generator now writes clips at the voice's 22,050 Hz and needs the model path
  passed in. openWakeWord reads every clip as 16 kHz, so unconverted clips would train on
  speech ~27% slower and lower than real. `generate_samples` here passes the model and
  resamples, on every core.
- openWakeWord lists `custom_negative_phrases` among ~100,000 generated sound-alikes and speaks
  the list once through, so each hand-picked phrase got about one clip and "hey senator" still
  woke the first model. They're given `custom_negative_share` of the negatives here.
- openWakeWord's sound-alike negatives are meant to leave out homophones but don't (it compares
  a string with a list), so "hay senate" would be trained as *not* the wake word. They're
  dropped here (about 1 in 1,600).
- Its .tflite conversion runs even without --convert_to_tflite and needs TensorFlow 2.8; we
  only want the .onnx, so it's off.
- Relative paths in the config are taken from the data directory.
"""

import argparse
import logging
import os
import random
import runpy
import shutil
import sys
import types
import uuid
from concurrent.futures import ProcessPoolExecutor
from math import gcd
from pathlib import Path

import numpy as np
import pronouncing
import soundfile as sf
import yaml
from scipy.signal import resample_poly

HERE = Path(__file__).resolve().parent
STEPS = {"generate": "--generate_clips", "augment": "--augment_clips", "train": "--train_model"}
SAMPLE_RATE = 16000

log = logging.getLogger("train")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("steps", nargs="*", metavar="step", help="generate, augment, train (default: all three)")
    parser.add_argument("--data", type=Path, default=HERE / "data", help="the directory fetch.py filled")
    parser.add_argument("--config", type=Path, default=HERE / "hey_sennet.yml")
    parser.add_argument("--smoke", action="store_true", help="tiny run on the smoke data; the model is meaningless")
    args = parser.parse_args()
    steps = args.steps or list(STEPS)
    if unknown := set(steps) - set(STEPS):
        parser.error(f"unknown step: {', '.join(sorted(unknown))}")
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s", datefmt="%H:%M:%S")

    data = args.data.resolve()
    config = yaml.safe_load(args.config.read_text())
    if args.smoke:
        config.update(
            n_samples=300, n_samples_val=100, tts_batch_size=25, augmentation_rounds=1, steps=300,
            output_dir="smoke-output", background_paths=["audioset_16k"], background_paths_duplication_rate=[1],
            feature_data_files={"ACAV100M_sample": "features/smoke_negatives.npy"},
            false_positive_validation_data_path="features/smoke_validation.npy",
        )
        config["batch_n_per_class"] = {"ACAV100M_sample": 256, "adversarial_negative": 32, "positive": 32}
    config = resolve_paths(config, data)
    check_inputs(config)

    output = Path(config["output_dir"])
    output.mkdir(parents=True, exist_ok=True)
    resolved = output / f"{config['model_name']}.resolved.yml"
    resolved.write_text(yaml.safe_dump(config, sort_keys=False))

    generator = Path(config["piper_sample_generator_path"])
    sys.path.insert(0, str(generator))  # for piper_train, which unpickling the voice model needs
    install_generate_samples(generator / "models" / "en_US-libritts_r-medium.pt",
                             config["custom_negative_phrases"], config.get("custom_negative_share", 0))
    install_homophone_filter(config["target_phrase"])
    skip_tflite()

    sys.argv = ["openwakeword.train", "--training_config", str(resolved), *(STEPS[s] for s in steps)]
    runpy.run_module("openwakeword.train", run_name="__main__", alter_sys=True)
    if "train" in steps:
        log.info("Model: %s", output / f"{config['model_name']}.onnx")


def resolve_paths(config: dict, data: Path) -> dict:
    def under(path: str) -> str:
        return str(data / path)

    for key in ("piper_sample_generator_path", "output_dir", "false_positive_validation_data_path"):
        config[key] = under(config[key])
    for key in ("rir_paths", "background_paths"):
        config[key] = [under(p) for p in config[key]]
    config["feature_data_files"] = {k: under(v) for k, v in config["feature_data_files"].items()}
    return config


def check_inputs(config: dict) -> None:
    """Fail now rather than an hour in, when the step that needs it starts."""
    missing = [
        p
        for p in [
            Path(config["piper_sample_generator_path"]) / "models" / "en_US-libritts_r-medium.pt",
            Path(config["false_positive_validation_data_path"]),
            *map(Path, config["feature_data_files"].values()),
            *map(Path, config["rir_paths"]),
            *map(Path, config["background_paths"]),
        ]
        if not p.exists()
    ]
    if missing:
        sys.exit("Missing (run fetch.py first):\n  " + "\n  ".join(map(str, missing)))


def install_generate_samples(model: Path, custom_negatives: list[str], custom_share: float) -> None:
    """Provide the `generate_samples` module openWakeWord imports, with 16 kHz output."""
    from piper_sample_generator.__main__ import generate_samples as generate_22k

    custom_negatives = list(custom_negatives)  # openWakeWord extends the config's list in place
    # runpy makes openWakeWord's train.py __main__, where pickle would look for resample_clip, so
    # the (forked) workers find it under a name of its own.
    sys.modules["wakeword_train"] = sys.modules[__name__]
    resample_clip.__module__ = "wakeword_train"

    def generate_samples(text, output_dir, max_samples=None, file_names=None, **kwargs):
        kwargs.pop("auto_reduce_batch_size", None)  # the old generator's option
        if isinstance(text, list) and max_samples:
            text = weight_custom_negatives(text, custom_negatives, round(max_samples * custom_share), max_samples)
        output_dir = Path(output_dir)
        staging = output_dir.with_name(output_dir.name + ".staging")
        shutil.rmtree(staging, ignore_errors=True)
        # Unique names, so a resumed run adds clips instead of overwriting 0.wav, 1.wav…
        if file_names is None:
            file_names = [uuid.uuid4().hex + ".wav" for _ in range(max_samples or len(text))]
        generate_22k(text=text, output_dir=staging, model=model, max_samples=max_samples,
                     file_names=file_names, **kwargs)
        clips = list(staging.glob("*.wav"))
        with ProcessPoolExecutor(os.cpu_count()) as pool:
            list(pool.map(resample_clip, clips, [output_dir] * len(clips), chunksize=256))
        shutil.rmtree(staging)

    module = types.ModuleType("generate_samples")
    module.generate_samples = generate_samples
    sys.modules["generate_samples"] = module


def resample_clip(clip: Path, output_dir: Path) -> None:
    audio, rate = sf.read(clip, dtype="float32")
    divisor = gcd(rate, SAMPLE_RATE)
    sf.write(output_dir / clip.name, resample_poly(audio, SAMPLE_RATE // divisor, rate // divisor),
             SAMPLE_RATE, subtype="PCM_16")


def weight_custom_negatives(texts: list[str], custom: list[str], n_custom: int, max_samples: int) -> list[str]:
    """The negatives to speak, with the custom phrases making up n_custom of the first
    max_samples (the generator speaks the list in order) and the rest generated sound-alikes.
    Texts with no custom phrase in them, like the positives, are left alone."""
    phrases = [p for p in dict.fromkeys(texts) if p in custom]
    if not phrases or n_custom <= 0:
        return texts
    others = [t for t in texts if t not in custom]
    weighted = [phrases[i % len(phrases)] for i in range(n_custom)] + others[:max_samples - n_custom]
    random.Random(0).shuffle(weighted)
    log.info("Custom negatives: %d clips across %d phrases, of %d", n_custom, len(phrases), max_samples)
    return weighted


def install_homophone_filter(targets: list[str]) -> None:
    """Drop generated negatives that sound exactly like a target phrase ("hay senate")."""
    import openwakeword.data

    original = openwakeword.data.generate_adversarial_texts

    def sounds(word: str) -> set[str]:
        return {"".join(c for c in p if not c.isdigit()) for p in pronouncing.phones_for_word(word.lower())}

    def homophone(text: str) -> bool:
        words = text.split()
        for target in targets:
            target_words = target.split()
            if len(words) == len(target_words) and all(
                w == t or (sounds(w) and sounds(w) & sounds(t)) for w, t in zip(words, target_words)
            ):
                return True
        return False

    def generate_adversarial_texts(*args, **kwargs):
        texts = original(*args, **kwargs)
        kept = [t for t in texts if not homophone(t)]
        log.info("Dropped %d of %d sound-alike negatives that are homophones of the wake phrase",
                 len(texts) - len(kept), len(texts))
        return kept

    openwakeword.data.generate_adversarial_texts = generate_adversarial_texts


def skip_tflite() -> None:
    """Don't convert to .tflite: its flag defaults to the string "False", which is true."""
    parse_args = argparse.ArgumentParser.parse_args

    def parse(self, *args, **kwargs):
        namespace = parse_args(self, *args, **kwargs)
        if getattr(namespace, "convert_to_tflite", None) == "False":
            namespace.convert_to_tflite = False
        return namespace

    argparse.ArgumentParser.parse_args = parse


if __name__ == "__main__":
    main()
