"""What this machine has, and which speech tier to recommend for it."""
from __future__ import annotations

import os
import platform
import shutil
import subprocess
from dataclasses import dataclass, field

# Measured on an RTX 4060 Laptop: models, cuDNN workspace and a 60 s final pass.
GPU_NEEDS_MB = 4600
# Headroom so a browser or desktop using the GPU too doesn't push speech out.
GPU_RECOMMEND_FREE_MB = 5000


@dataclass
class Gpu:
    name: str
    total_mb: int
    free_mb: int


@dataclass
class Hardware:
    system: str        # Linux | Darwin | …
    machine: str       # x86_64 | arm64 | …
    cpu_threads: int
    ram_mb: int
    gpus: list[Gpu] = field(default_factory=list)


@dataclass
class Recommendation:
    device: str        # cpu | cuda
    reason: str
    warnings: list[str] = field(default_factory=list)


def detect() -> Hardware:
    return Hardware(platform.system(), platform.machine(), os.cpu_count() or 1, _ram_mb(), _nvidia_gpus())


def recommend(hardware: Hardware) -> Recommendation:
    warnings = []
    if hardware.ram_mb and hardware.ram_mb < 6000:
        warnings.append(f"{hardware.ram_mb / 1024:.1f} GB of RAM is tight; speech on CPU needs about 2.5 GB.")
    if hardware.cpu_threads < 4:
        warnings.append(f"{hardware.cpu_threads} CPU threads: captions will update less often.")

    gpu = max(hardware.gpus, key=lambda g: g.free_mb, default=None)
    if hardware.system == "Linux" and gpu:
        summary = f"{gpu.name}, {gpu.free_mb / 1024:.1f} GB free of {gpu.total_mb / 1024:.1f} GB"
        if gpu.free_mb >= GPU_RECOMMEND_FREE_MB:
            return Recommendation("cuda", f"{summary}. Speech uses about {GPU_NEEDS_MB / 1024:.1f} GB.", warnings)
        return Recommendation("cpu", f"{summary}: too little free for speech "
                                     f"(about {GPU_NEEDS_MB / 1024:.1f} GB). Close what is using it to switch later.",
                              warnings)
    if hardware.system == "Darwin" and hardware.machine == "arm64":
        return Recommendation("cpu", "Apple Silicon. CoreML acceleration isn't measured yet, so speech runs on the CPU.",
                              warnings)
    return Recommendation("cpu", "No NVIDIA GPU found.", warnings)


def _ram_mb() -> int:
    try:
        return os.sysconf("SC_PAGE_SIZE") * os.sysconf("SC_PHYS_PAGES") // (1 << 20)
    except (ValueError, OSError, AttributeError):
        return 0


def _nvidia_gpus() -> list[Gpu]:
    if not shutil.which("nvidia-smi"):
        return []
    try:
        out = subprocess.run(["nvidia-smi", "--query-gpu=name,memory.total,memory.free",
                              "--format=csv,noheader,nounits"], capture_output=True, text=True, timeout=10).stdout
    except (OSError, subprocess.TimeoutExpired):
        return []
    gpus = []
    for line in out.strip().splitlines():
        try:
            name, total, free = (part.strip() for part in line.split(","))
            gpus.append(Gpu(name, int(total), int(free)))
        except ValueError:
            continue
    return gpus
