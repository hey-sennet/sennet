"""`agent-talk setup`: a guided install of the speech stack.

It detects the hardware, recommends a tier and says why, installs the matching
ONNX runtime, downloads the models and checks them, then runs a short self-test.
M5 adds the service, Tailscale and front-LLM steps.
"""
from __future__ import annotations

import asyncio
import os
import re
import shutil
import subprocess
import sys
import time
import tomllib
from pathlib import Path

import numpy as np

from .config import Paths, Settings
from .speech import hardware, models
from .speech.runtime import runtime_distribution

RUNTIMES = {"cpu": "onnxruntime", "cuda": "onnxruntime-gpu"}
NAMES = {"cpu": "CPU", "cuda": "GPU (CUDA)"}
SELF_TEST = "Agent Talk is ready to listen."


def run(settings: Settings, paths: Paths, device: str | None, assume_yes: bool) -> int:
    if not assume_yes and not sys.stdin.isatty():
        print("Setup asks before installing or downloading anything. Run it in a terminal, "
              "or pass --yes to accept its recommendations.", file=sys.stderr)
        return 2
    ask = (lambda question, default=True: default) if assume_yes else _ask

    found = hardware.detect()
    print("This computer:")
    print(f"  {found.system} {found.machine}, {found.cpu_threads} CPU threads, {found.ram_mb / 1024:.1f} GB RAM")
    for gpu in found.gpus:
        print(f"  {gpu.name}, {gpu.free_mb / 1024:.1f} GB free of {gpu.total_mb / 1024:.1f} GB")

    advice = hardware.recommend(found)
    if device is None:
        print(f"\nRecommended speech tier: {NAMES[advice.device]}. {advice.reason}")
        for warning in advice.warnings:
            print(f"  Note: {warning}")
        device = advice.device
        other = "cpu" if device == "cuda" else "cuda"
        if found.gpus and found.system == "Linux" and not ask(f"Use {NAMES[device]}?"):
            device = other
    elif device == "cuda" and found.system != "Linux":
        print("The GPU tier needs Linux with an NVIDIA GPU.", file=sys.stderr)
        return 1

    if not _runtime_ready(device, ask):
        return 1

    settings.speech.device = device
    settings.save(paths.config_file)
    print(f"\nSaved speech.device = \"{device}\" to {paths.config_file}")

    chosen = list(models.MODELS[device].values())
    needed = [m for m in chosen if not models.present(paths.models, m)]
    if needed:
        total = sum(m.size for m in needed) / 1e9
        print(f"\nModels to download ({total:.1f} GB) into {paths.models}:")
        for model in needed:
            print(f"  {model.id}  {model.size / 1e9:.2f} GB  {model.licence}")
        if not ask("Download them now?"):
            print("Run `agent-talk setup` again when you're ready.")
            return 1
    for model in chosen:
        try:
            models.fetch(paths.models, model, _progress(model))
        except (OSError, models.ChecksumError) as error:
            print(f"\n{model.id}: {error}", file=sys.stderr)
            return 1
    print("Models are in place and match their checksums.")

    return asyncio.run(_self_test(paths, device, settings.speech.threads, settings.speech.voice))


def _runtime_ready(device: str, ask) -> bool:
    wanted = RUNTIMES[device]
    installed = runtime_distribution()
    if installed == wanted:
        return True
    extra = device
    command, rerun = _install_command(extra)
    have = f"{installed} is installed" if installed else "no ONNX runtime is installed"
    print(f"\nThe {NAMES[device]} tier needs {wanted}; {have}.")
    if command is None:
        print(f"Install Agent Talk with the `{extra}` extra (for example `pip install 'agent-talk[{extra}]'`), "
              "then run `agent-talk setup` again.")
        return False
    print(f"  {' '.join(command)}")
    if not ask("Run this now?"):
        print("Run it yourself, then `agent-talk setup` again.")
        return False
    if subprocess.run(command).returncode != 0:
        print("The install failed; see the output above.", file=sys.stderr)
        return False
    if rerun:
        # This process still has the old packages loaded; continue in the new install.
        print()
        os.execvp(rerun[0], rerun + ["setup", "--device", device] + (["--yes"] if "--yes" in sys.argv else []))
    print("Run `agent-talk setup` again to continue.")
    return False


def _install_command(extra: str) -> tuple[list[str] | None, list[str] | None]:
    """The command that reinstalls Agent Talk with `extra`, and what to run afterwards."""
    receipt = _uv_tool_receipt()
    if receipt is not None:
        requirement = next((r for r in receipt.get("tool", {}).get("requirements", [])
                            if r.get("name") == "agent-talk"), None)
        if requirement and not any(k in requirement for k in ("git", "url", "directory", "path", "editable")):
            spec = f"agent-talk[{extra}]" + (requirement.get("specifier") or "")
            return ["uv", "tool", "install", "--reinstall", spec], [shutil.which("agent-talk") or "agent-talk"]
        return None, None
    project = _source_checkout()
    if project is not None and shutil.which("uv"):
        return ["uv", "sync", "--project", str(project), "--extra", extra], None
    return [sys.executable, "-m", "pip", "install", f"agent-talk[{extra}]"], None


def _uv_tool_receipt() -> dict | None:
    receipt = Path(sys.prefix) / "uv-receipt.toml"
    if not receipt.is_file():
        return None
    try:
        return tomllib.loads(receipt.read_text())
    except (OSError, tomllib.TOMLDecodeError):
        return None


def _source_checkout() -> Path | None:
    """The repository this package runs from, when it is an editable install."""
    for parent in Path(__file__).resolve().parents:
        if (parent / "pyproject.toml").is_file() and (parent / "src" / "agent_talk").is_dir():
            return parent
    return None


async def _self_test(paths: Paths, device: str, threads: int, voice: str) -> int:
    from .speech import Speech

    print("\nSelf-test: loading the models…")
    speech = Speech()
    try:
        started = time.perf_counter()
        await speech.load(paths.models, device, threads, voice)
        if speech.status != {"stt": "ready", "tts": "ready"}:
            for role, status in speech.status.items():
                print(f"  {role}: {status} {speech.detail[role]}", file=sys.stderr)
            return 1
        print(f"  loaded on {device} in {time.perf_counter() - started:.1f} s")

        started = time.perf_counter()
        audio = await speech.run("tts", speech.tts.synthesize, SELF_TEST)
        tts_ms = (time.perf_counter() - started) * 1000
        heard_audio = _resample(audio, speech.tts.rate, 16000)
        started = time.perf_counter()
        heard = await speech.run("stt", speech.stt.text, heard_audio)
        stt_ms = (time.perf_counter() - started) * 1000
    finally:
        speech.close()

    print(f"  spoke \"{SELF_TEST}\" ({len(audio) / speech.tts.rate:.1f} s of audio) in {tts_ms:.0f} ms")
    print(f"  heard \"{heard}\" in {stt_ms:.0f} ms")
    if _words(heard) != _words(SELF_TEST):
        print("The transcript didn't match. Speech works, but check your models and device.", file=sys.stderr)
        return 1
    print("\nSpeech is ready. Start Agent Talk with `agent-talk serve`.")
    return 0


def _resample(audio: np.ndarray, rate: int, target: int) -> np.ndarray:
    positions = np.arange(0, len(audio), rate / target)
    return np.interp(positions, np.arange(len(audio)), audio).astype(np.float32)


def _words(text: str) -> list[str]:
    return re.sub(r"[^\w\s']", "", text.lower()).split()


def _ask(question: str, default: bool = True) -> bool:
    answer = input(f"{question} [{'Y/n' if default else 'y/N'}] ").strip().lower()
    return default if not answer else answer.startswith("y")


def _progress(model: models.Model):
    if not sys.stdout.isatty():
        return None
    shown = {"at": 0.0}

    def progress(file: models.ModelFile, done: int):
        now = time.monotonic()
        if done < file.size and now - shown["at"] < 0.2:
            return
        shown["at"] = now
        end = "\n" if done >= file.size else ""
        print(f"\r  {model.id}/{file.name}  {done / file.size:4.0%} of {file.size / 1e6:,.0f} MB", end=end, flush=True)

    return progress
