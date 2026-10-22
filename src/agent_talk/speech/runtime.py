"""ONNX Runtime setup: execution providers and session options.

onnxruntime comes from the `cpu` or `cuda` extra, so it may be missing; nothing
imports it at module level.
"""
from __future__ import annotations

import importlib.metadata
from functools import cache


class RuntimeMissing(RuntimeError):
    pass


def runtime_distribution() -> str | None:
    """Which onnxruntime build is installed: "onnxruntime", "onnxruntime-gpu" or None."""
    for name in ("onnxruntime-gpu", "onnxruntime"):
        try:
            importlib.metadata.distribution(name)
            return name
        except importlib.metadata.PackageNotFoundError:
            pass
    return None


@cache
def ort():
    try:
        import onnxruntime
    except ImportError as error:
        raise RuntimeMissing("no ONNX runtime is installed; run `agent-talk setup`") from error
    onnxruntime.set_default_logger_severity(3)
    if hasattr(onnxruntime, "preload_dlls") and runtime_distribution() == "onnxruntime-gpu":
        # Loads the CUDA and cuDNN libraries from the nvidia-* wheels.
        onnxruntime.preload_dlls()
    return onnxruntime


def cuda_available() -> bool:
    try:
        return "CUDAExecutionProvider" in ort().get_available_providers()
    except RuntimeMissing:
        return False


def resolve_device(device: str) -> str:
    """"auto" becomes "cuda" when the CUDA provider is usable, else "cpu"."""
    if device == "auto":
        return "cuda" if cuda_available() else "cpu"
    return device


def providers(device: str) -> list:
    if device != "cuda":
        return ["CPUExecutionProvider"]
    # Live captions decode a different input length on every tick. Exhaustive cuDNN
    # algorithm search (the default) re-benchmarks convolutions for each new shape.
    return [("CUDAExecutionProvider", {"cudnn_conv_algo_search": "HEURISTIC"}), "CPUExecutionProvider"]


def session_options(threads: int | None):
    options = ort().SessionOptions()
    if threads:
        # A small fixed count: all cores was 2-4x slower on a hybrid P/E-core CPU in M0.
        options.intra_op_num_threads = threads
        options.inter_op_num_threads = 1
    return options
