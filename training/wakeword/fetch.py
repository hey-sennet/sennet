"""Download everything training needs into a data directory.

    uv run python fetch.py                # the full set, ~40 GB once converted (for the GPU pod)
    uv run python fetch.py --smoke        # ~1 GB, enough for `train.py --smoke`

Each part is skipped when it is already there, so it can be rerun after a failure.
"""

import argparse
import io
import logging
import shutil
import tarfile
import urllib.request
import zipfile
from math import gcd
from pathlib import Path

import numpy as np
import soundfile as sf
from huggingface_hub import hf_hub_download, snapshot_download
from scipy.signal import resample_poly
from tqdm import tqdm

HERE = Path(__file__).resolve().parent

# piper-sample-generator, pinned. Its package leaves out piper_train, which unpickling the
# generator model needs, so we use a checkout, as openWakeWord's train.py expects.
GENERATOR_COMMIT = "2971426a55072f7d22fec416ca7800df8bd23207"
GENERATOR_URL = f"https://github.com/rhasspy/piper-sample-generator/archive/{GENERATOR_COMMIT}.tar.gz"
GENERATOR_MODEL_URL = (
    "https://github.com/rhasspy/piper-sample-generator/releases/download/v2.0.0/en_US-libritts_r-medium.pt"
)
FEATURES_REPO = "davidscripka/openwakeword_features"
ACAV_FEATURES = "openwakeword_features_ACAV100M_2000_hrs_16bit.npy"  # 17.3 GB, ~2,000 h of negatives
VALIDATION_FEATURES = "validation_set_features.npy"  # 185 MB, ~11 h, for the false-positive rate
RIR_REPO = "davidscripka/MIT_environmental_impulse_responses"
AUDIOSET_REPO = "agkphysics/AudioSet"  # balanced train: 38 parquet shards of 500 ten-second clips
FMA_URL = "https://os.unil.cloud.switch.ch/fma/fma_small.zip"  # 7.7 GB of 30-second music clips

SAMPLE_RATE = 16000

log = logging.getLogger("fetch")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--data", type=Path, default=HERE / "data", help="where to put it all (default: ./data)")
    parser.add_argument("--smoke", action="store_true", help="only what a quick pipeline check needs")
    parser.add_argument("--audioset-shards", type=int, default=6, help="AudioSet shards for background noise (~1.4 h each)")
    parser.add_argument("--fma-hours", type=float, default=3, help="hours of music for background noise")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s", datefmt="%H:%M:%S")
    logging.getLogger("httpx").setLevel(logging.WARNING)

    data: Path = args.data.resolve()
    data.mkdir(parents=True, exist_ok=True)
    downloads = data / "downloads"

    fetch_generator(data)
    fetch_openwakeword_models()
    fetch_rirs(data)
    features = data / "features"
    hf_hub_download(FEATURES_REPO, VALIDATION_FEATURES, repo_type="dataset", local_dir=features)
    if args.smoke:
        fetch_audioset(data / "audioset_16k", downloads, shards=1, max_clips=200)
        make_smoke_negatives(features)
    else:
        log.info("Negative features (17.3 GB)")
        hf_hub_download(FEATURES_REPO, ACAV_FEATURES, repo_type="dataset", local_dir=features)
        fetch_audioset(data / "audioset_16k", downloads, shards=args.audioset_shards)
        fetch_fma(data / "fma_16k", downloads, hours=args.fma_hours)
    shutil.rmtree(downloads, ignore_errors=True)
    log.info("Done: %s", data)


def fetch_generator(data: Path) -> None:
    checkout = data / "piper-sample-generator"
    if not (checkout / "piper_train").is_dir():
        log.info("piper-sample-generator %s", GENERATOR_COMMIT[:7])
        with urllib.request.urlopen(GENERATOR_URL) as response:
            tarfile.open(fileobj=io.BytesIO(response.read()), mode="r:gz").extractall(data, filter="data")
        shutil.rmtree(checkout, ignore_errors=True)
        (data / f"piper-sample-generator-{GENERATOR_COMMIT}").rename(checkout)
    model = checkout / "models" / "en_US-libritts_r-medium.pt"
    if not model.exists():
        log.info("Generator voice model (200 MB)")
        download(GENERATOR_MODEL_URL, model)


def fetch_openwakeword_models() -> None:
    """The melspectrogram and embedding models that turn audio into features.

    They aren't in openWakeWord's package; this puts them where it looks (inside the venv).
    A name that matches no wake word model fetches just these and the VAD.
    """
    import openwakeword.utils

    openwakeword.utils.download_models(["none"])


def fetch_rirs(data: Path) -> None:
    """270 room impulse responses (MIT), already 16 kHz."""
    out = data / "mit_rirs"
    if len(list(out.glob("16khz/*.wav"))) >= 270:
        return
    log.info("Room impulse responses")
    snapshot_download(RIR_REPO, repo_type="dataset", allow_patterns="16khz/*", local_dir=out)


def fetch_audioset(out: Path, downloads: Path, shards: int, max_clips: int | None = None) -> None:
    """Ten-second clips of everything (speech, music, noise, animals, traffic) as 16 kHz mono."""
    import pyarrow.parquet as pq

    out.mkdir(parents=True, exist_ok=True)
    for shard in range(shards):
        marker = out / f".shard-{shard:02d}"
        if marker.exists():
            continue
        name = f"data/bal_train/{shard:02d}.parquet"
        log.info("AudioSet %s (~690 MB)", name)
        path = hf_hub_download(AUDIOSET_REPO, name, repo_type="dataset", local_dir=downloads / "audioset")
        written = 0
        for batch in pq.ParquetFile(path).iter_batches(batch_size=50, columns=["video_id", "audio"]):
            for row in batch.to_pylist():
                try:
                    audio, rate = sf.read(io.BytesIO(row["audio"]["bytes"]), dtype="float32")
                except Exception as error:  # a few clips are damaged
                    log.warning("Skipping %s: %s", row["video_id"], error)
                    continue
                write_16k(out / f"{row['video_id']}.wav", audio, rate)
                written += 1
                if max_clips is not None and written >= max_clips:
                    break
            if max_clips is not None and written >= max_clips:
                break
        Path(path).unlink()
        marker.touch()


def fetch_fma(out: Path, downloads: Path, hours: float) -> None:
    """Music (Free Music Archive, small set), converted to 16 kHz mono until `hours` is reached."""
    if hours <= 0:
        return
    out.mkdir(parents=True, exist_ok=True)
    marker = out / f".hours-{hours:g}"
    if marker.exists():
        return
    archive = downloads / "fma_small.zip"
    if not archive.exists():
        log.info("FMA small (7.7 GB)")
        download(FMA_URL, archive)
    seconds = 0.0
    with zipfile.ZipFile(archive) as zf, tqdm(total=hours * 3600, unit="s", desc="FMA") as bar:
        for member in zf.namelist():
            if not member.endswith(".mp3"):
                continue
            try:
                audio, rate = sf.read(io.BytesIO(zf.read(member)), dtype="float32")
            except Exception as error:  # fma_small has a few unreadable files
                log.warning("Skipping %s: %s", member, error)
                continue
            write_16k(out / (Path(member).stem + ".wav"), audio, rate)
            duration = len(audio) / rate
            seconds += duration
            bar.update(duration)
            if seconds >= hours * 3600:
                break
    archive.unlink()
    marker.touch()


def make_smoke_negatives(features: Path) -> None:
    """Small stand-ins for the 17 GB negative set and the validation set, from the latter.

    Only for checking the pipeline runs; a model trained on them is meaningless. The full
    validation set goes to the GPU as one 3 GB batch, too much beside a running Sennet.
    """
    val = np.load(features / VALIDATION_FEATURES, mmap_mode="r")
    negatives = features / "smoke_negatives.npy"
    if not negatives.exists():
        np.save(negatives, np.stack([val[i : i + 16] for i in range(0, 200_000, 16)]).astype(np.float16))
    validation = features / "smoke_validation.npy"
    if not validation.exists():
        np.save(validation, np.array(val[200_000:240_000]))  # ~55 minutes


def write_16k(path: Path, audio: np.ndarray, rate: int) -> None:
    if audio.ndim == 2:
        audio = audio.mean(axis=1)
    if rate != SAMPLE_RATE:
        divisor = gcd(rate, SAMPLE_RATE)
        audio = resample_poly(audio, SAMPLE_RATE // divisor, rate // divisor)
    sf.write(path, np.clip(audio, -1, 1), SAMPLE_RATE, subtype="PCM_16")


def download(url: str, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    partial = path.with_name(path.name + ".part")
    with urllib.request.urlopen(url) as response, open(partial, "wb") as f:
        total = int(response.headers.get("Content-Length", 0)) or None
        with tqdm(total=total, unit="B", unit_scale=True, desc=path.name) as bar:
            while chunk := response.read(1 << 20):
                f.write(chunk)
                bar.update(len(chunk))
    partial.rename(path)


if __name__ == "__main__":
    main()
