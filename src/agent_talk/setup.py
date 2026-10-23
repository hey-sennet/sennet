"""`agent-talk setup`: a guided install of speech and the voice.

Every choice is a list of all the options, the recommended one first and marked,
with why. Options that won't fit can still be chosen, with a warning; options that
can't work at all are shown greyed out with the reason. A second run marks and
pre-selects the current settings. All questions come first, then one summary of
what will be downloaded; then the checks: speech hears itself, and the voice is
chosen by ear.

Phone access: with Tailscale up, its HTTPS address becomes `server.public_url`, which
pairing links use, and setup offers to run `tailscale serve` for it. Last, setup installs
the service (or updates and restarts it, so it picks up the new settings) and prints a
pairing link when no browser is paired yet.
"""
from __future__ import annotations

import asyncio
import os
import re
import shutil
import socket
import subprocess
import sys
import time
import tomllib
from dataclasses import dataclass
from pathlib import Path

import numpy as np
from rich.panel import Panel
from rich.table import Table

from . import service, tailscale
from .config import Paths, Settings
from .prompts import Abort, Option, Prompter, gigabytes, size, tagged
from .speech import hardware, models, voices
from .speech.models import Model
from .speech.runtime import runtime_distribution

RUNTIMES = {"cpu": "onnxruntime", "cuda": "onnxruntime-gpu"}
NAMES = {"cpu": "CPU", "cuda": "GPU (CUDA)"}
# Release to first reply audio, measured in M2 (docs/m2-backend.md, step 7).
REPLY_TIME = {"cpu": "~1.4 s", "cuda": "~0.2 s"}
SELF_TEST = "Agent Talk is ready to listen."

MORE = "more"


@dataclass
class Machine:
    """What setup found, and what the daemon already holds."""

    hardware: hardware.Hardware
    speech_loaded: bool            # Agent Talk is running, so its speech is in the free figures
    tailnet: str | None = None     # this machine's HTTPS address on the tailnet, when Tailscale is up
    served: bool = False           # `tailscale serve` passes that address on to Agent Talk
    serves_other: bool = False     # it serves something else, which serving Agent Talk would replace


def run(settings: Settings, paths: Paths, device: str | None, assume_yes: bool) -> int:
    if not assume_yes and not sys.stdin.isatty():
        print("Setup asks before installing or downloading anything. Run it in a terminal, "
              "or pass --yes to accept its recommendations.", file=sys.stderr)
        return 2
    ui = Prompter(interactive=not assume_yes)
    try:
        return _run(ui, settings, paths, device)
    except Abort:
        ui.console.print("\n[yellow]Setup stopped.[/] Run `agent-talk setup` again to pick up where you left off.")
        return 130


def _run(ui: Prompter, settings: Settings, paths: Paths, device: str | None) -> int:
    configured = paths.config_file.exists()
    ui.console.print()
    with ui.working("Looking at this computer…"):
        machine = _machine(settings)
    _show_machine(ui, machine)

    ui.heading("Speech")
    if device == "cuda" and machine.hardware.system != "Linux":
        ui.fail("The GPU tier needs Linux with an NVIDIA GPU.")
        return 1
    if device is None:
        device = _choose_speech(ui, machine, settings.speech.device if configured else None)
    if not _runtime_ready(ui, device):
        return 1

    ui.heading("Phone access")
    _choose_address(ui, machine, settings)

    ui.heading("Downloads")
    speech_models = list(models.MODELS[device].values())
    downloads = [(m.id, m) for m in speech_models if not models.present(paths.models, m)]
    if downloads:
        _show_downloads(ui, paths, downloads)
        total = sum(m.size for _, m in downloads)
        if not ui.confirm(f"Download {size(total)} now?"):
            ui.info("Run `agent-talk setup` again when you're ready.")
            return 1
    else:
        ui.ok("Everything is downloaded already.")

    settings.speech.device = device
    settings.save(paths.config_file)
    ui.note(f"Saved to {paths.config_file}")

    if not _fetch(ui, paths, speech_models, downloads):
        return 1

    ui.heading("Speech check")
    speech = _speech_check(ui, paths, device, settings)
    if speech is None:
        return 1
    try:
        ui.heading("Voice")
        settings.speech.voice = _choose_voice(ui, speech, settings.speech.voice if configured else None)
        settings.save(paths.config_file)
    finally:
        speech.close()

    ui.heading("Service")
    running = _choose_service(ui, settings)

    _show_summary(ui, settings, device, running)
    from .cli import pair_command, paired

    if not paired(paths):
        ui.console.print()
        pair_command(settings, paths)
    return 0


# What this computer has

def _machine(settings: Settings) -> Machine:
    tailnet = tailscale.address(tailscale.status())
    serving = tailscale.serve_status() if tailnet else None
    port = settings.server.port
    return Machine(hardware.detect(), _daemon_running(settings), tailnet,
                   tailscale.serves(serving, port), tailscale.serves_other(serving, port))


def _show_machine(ui: Prompter, machine: Machine):
    found = machine.hardware
    lines = [f"{found.system} {found.machine} · {found.cpu_threads} CPU threads · {gigabytes(found.ram_mb)} RAM"]
    for gpu in found.gpus:
        lines.append(f"{gpu.name} · {gigabytes(gpu.free_mb)} free of {gigabytes(gpu.total_mb)}")
    if machine.speech_loaded:
        lines.append("[grey62]Agent Talk is running, so its speech models are already in the free figures.[/]")
    ui.console.print(Panel("\n".join(lines), title="[bold]Agent Talk setup[/]", title_align="left",
                           border_style="grey37", expand=False, padding=(0, 1)))


# Speech

def _choose_speech(ui: Prompter, machine: Machine, current: str | None) -> str:
    found = machine.hardware
    advice = hardware.recommend(found)
    for warning in advice.warnings:
        ui.warn(warning)
    gpu = max(found.gpus, key=lambda g: g.free_mb, default=None)
    no_cuda = None if found.system == "Linux" and gpu else "needs Linux and an NVIDIA GPU"
    if gpu and no_cuda is None:
        gpu_note = f"Uses about {gigabytes(hardware.GPU_NEEDS_MB)} of {gpu.name}'s memory; {gigabytes(gpu.free_mb)} free."
    else:
        gpu_note = ""
    options = [
        Option("cuda", NAMES["cuda"], f"{size(_models_size('cuda'))} · replies {REPLY_TIME['cuda']}",
               note=advice.reason if advice.device == "cuda" else gpu_note, disabled=no_cuda,
               tags=tagged("cuda", advice.device, current)),
        Option("cpu", NAMES["cpu"], f"{size(_models_size('cpu'))} · replies {REPLY_TIME['cpu']}",
               note=advice.reason if advice.device == "cpu" else "Works anywhere; captions update less often.",
               tags=tagged("cpu", advice.device, current)),
    ]
    default = current if current in ("cpu", "cuda") and not (current == "cuda" and no_cuda) else advice.device
    return ui.select("Speech runs on", options, default)


def _models_size(device: str) -> int:
    return sum(m.size for m in models.MODELS[device].values())


def _runtime_ready(ui: Prompter, device: str) -> bool:
    wanted = RUNTIMES[device]
    installed = runtime_distribution()
    if installed == wanted:
        return True
    command, rerun = _install_command(device)
    have = f"{installed} is installed" if installed else "no ONNX runtime is installed"
    ui.warn(f"The {NAMES[device]} tier needs {wanted}; {have}.")
    if command is None:
        ui.info(f"Install Agent Talk with the `{device}` extra (for example `pip install 'agent-talk[{device}]'`), "
                "then run `agent-talk setup` again.")
        return False
    ui.info(f"[bold]{' '.join(command)}[/]")
    if not ui.confirm("Run this now?"):
        ui.info("Run it yourself, then `agent-talk setup` again.")
        return False
    if subprocess.run(command).returncode != 0:
        ui.fail("The install failed; see the output above.")
        return False
    ui.ok(f"Installed {wanted}.")
    # This process still has the old packages loaded; continue in the new install.
    again = ["setup", "--device", device]
    os.execvp(rerun[0], rerun + again + (["--yes"] if not ui.interactive else []))


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
    rerun = [sys.executable, "-m", "agent_talk"]
    project = _source_checkout()
    if project is not None and shutil.which("uv"):
        return ["uv", "sync", "--project", str(project), "--extra", extra], rerun
    return [sys.executable, "-m", "pip", "install", f"agent-talk[{extra}]"], rerun


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


# Downloads

def _show_downloads(ui: Prompter, paths: Paths, downloads: list[tuple[str, Model]]):
    table = Table(box=None, padding=(0, 2), show_edge=False, header_style="grey62")
    table.add_column("To download")
    table.add_column("Size", justify="right")
    table.add_column("Licence", style="grey62")
    for label, model in downloads:
        table.add_row(label, size(model.size), model.licence)
    ui.console.print(table)
    ui.note(f"Into {paths.data_dir}")


def _fetch(ui: Prompter, paths: Paths, speech_models: list[Model], downloads: list[tuple[str, Model]]) -> bool:
    wanted = {id(m) for _, m in downloads}
    try:
        if downloads:
            with ui.downloads() as bars:
                for label, model in downloads:
                    models.fetch(paths.models, model, bars.bar(label, model))
        present = [m for m in speech_models if id(m) not in wanted]
        if present:
            with ui.working(f"Checking {len(present)} downloaded model{'s' if len(present) > 1 else ''}…"):
                for model in present:
                    models.fetch(paths.models, model)
    except (OSError, models.ChecksumError) as error:
        ui.fail(str(error))
        return False
    ui.ok("Downloads are in place and match their checksums.")
    return True


# Checks

def _speech_check(ui: Prompter, paths: Paths, device: str, settings: Settings):
    """The loaded speech engines, once they have heard themselves; None on failure."""
    from .speech import Speech

    speech = Speech()
    config = settings.speech
    started = time.perf_counter()
    with ui.working(f"Loading speech on {NAMES[device]}…"):
        asyncio.run(speech.load(paths.models, device, config.threads, config.voice))
    if speech.status != {"stt": "ready", "tts": "ready"}:
        for role, status in speech.status.items():
            ui.fail(f"{role}: {status} {speech.detail[role]}")
        speech.close()
        return None
    ui.ok(f"Loaded on {NAMES[device]} in {time.perf_counter() - started:.1f} s")

    with ui.working("Speaking and listening…"):
        started = time.perf_counter()
        audio = speech.tts.synthesize(SELF_TEST)
        tts_ms = (time.perf_counter() - started) * 1000
        rate = speech.tts.rate
        started = time.perf_counter()
        heard = speech.stt.text(_resample(audio, rate, 16000))
        stt_ms = (time.perf_counter() - started) * 1000
    ui.ok(f"Spoke \"{SELF_TEST}\" ({len(audio) / rate:.1f} s of audio) in {tts_ms:.0f} ms")
    if _words(heard) != _words(SELF_TEST):
        ui.fail(f"Heard \"{heard}\". Speech works, but check your models and device.")
        speech.close()
        return None
    ui.ok(f"Heard it back word for word in {stt_ms:.0f} ms")
    return speech


def _choose_voice(ui: Prompter, speech, current: str | None) -> str:
    """Pick a voice by ear: each choice plays a short preview, then keep it or try another."""
    from .speech import preview

    player = preview.player() if ui.interactive else None
    if ui.interactive and player is None:
        ui.warn("No audio player found (pw-play, paplay, aplay or afplay), so voices can't be previewed.")
    voice = current or voices.DEFAULT
    show_all = not next((v for v in voices.VOICES if v.id == voice), voices.VOICES[0]).good
    while True:
        listed = [v for v in voices.VOICES if show_all or v.good]
        options = [Option(v.id, v.name, f"{v.accent} {v.gender} · grade {v.grade}",
                          note="Kokoro's grade for its training data; voices sound different to different people.",
                          tags=tagged(v.id, voices.DEFAULT, current)) for v in listed]
        if not show_all:
            options.append(Option(MORE, "More voices…", f"{len(voices.VOICES) - len(listed)} more, lower grades"))
        choice = ui.select("Voice", options, voice)
        if choice == MORE:
            show_all = True
            continue
        voice = choice
        if player is None:
            break
        name = next(v.name for v in voices.VOICES if v.id == voice)
        while True:
            with ui.working(f"{name} is speaking…"):
                speech.tts.use_voice(voice)
                preview.play(speech.tts.synthesize(voices.PREVIEW), speech.tts.rate, player)
            next_step = ui.select(f"Use {name}?", [Option("keep", f"Use {name}"), Option("again", "Hear it again"),
                                                   Option("other", "Hear another voice")], "keep")
            if next_step != "again":
                break
        if next_step == "keep":
            break
    name = next(v.name for v in voices.VOICES if v.id == voice)
    ui.ok(f"Voice: {name}")
    return voice


# Phone access

def _choose_address(ui: Prompter, machine: Machine, settings: Settings):
    """The address pairing links use on a phone: Tailscale's, unless another is set already."""
    current = settings.server.public_url
    if current and current != machine.tailnet:
        ui.ok(f"Phones reach Agent Talk at {current} (server.public_url).")
        return
    if machine.tailnet is None:
        ui.info("A phone needs an HTTPS address to reach Agent Talk. Tailscale (tailscale.com) is the "
                "simplest; a reverse proxy works too. Set server.public_url to the address to get "
                "pairing links and QR codes for it.")
        return
    if ui.confirm(f"Reach Agent Talk from your phone at {machine.tailnet}, through Tailscale?"):
        settings.server.public_url = machine.tailnet
        if not machine.served:
            _serve_on_tailnet(ui, machine, settings.server.port)
    elif current:
        settings.server.public_url = ""


def _serve_on_tailnet(ui: Prompter, machine: Machine, port: int):
    """Offer to run `tailscale serve`, which passes the tailnet address on to Agent Talk."""
    command = f"tailscale serve --bg {port}"
    if machine.serves_other:
        ui.warn(f"{machine.tailnet} serves something else now, which `{command}` would replace "
                "(`tailscale serve status` shows what).")
    if not ui.confirm(f"Run `{command}` now, so that address reaches Agent Talk?", default=not machine.serves_other):
        ui.note(f"Run `{command}` yourself when you're ready.")
        return
    if tailscale.serve(port):
        ui.ok(f"{machine.tailnet} now passes requests on to Agent Talk.")
        return
    ui.fail(f"`{command}` didn't work; see its output above.")
    if machine.hardware.system == "Linux":
        ui.info("If it said access was denied, let your user change Tailscale's settings with "
                "`sudo tailscale set --operator=$USER`, then run it again.")


# Service

def _choose_service(ui: Prompter, settings: Settings) -> bool:
    """Install the service, or update and restart it so it runs with the new settings. True when it runs."""
    try:
        manager = service.manager()
    except service.ServiceError as error:
        ui.info(str(error))
        return False
    status = manager.status()
    if status.installed:
        options = [Option("install", "Update and restart it", "so it uses these settings",
                          tags=("recommended", "current")),
                   Option("skip", "Leave it as it is", "until `agent-talk service restart`")]
    else:
        options = [Option("install", "Run it in the background", f"as a {manager.name}, from login",
                          note="Phones can reach it whenever this computer is on and you're logged in.",
                          tags=("recommended",)),
                   Option("skip", "Not now", "start it with `agent-talk serve` when you want it")]
    if ui.select("Agent Talk runs", options, "install") == "skip":
        return status.running
    with ui.working("Starting the service…"):
        try:
            manager, found = service.install(settings.server.port)
        except service.ServiceError as error:
            failure = error
        else:
            failure = None
    if failure is not None:
        ui.fail(str(failure))
        return False
    ui.ok(f"Agent Talk {found['version']} is running as a {manager.name}, and starts at login.")
    if manager.lingering() is False:
        _choose_linger(ui, manager)
    return True


def _choose_linger(ui: Prompter, manager):
    options = [Option("no", "Only while I'm logged in", "a locked screen counts as logged in",
                      tags=("recommended",)),
               Option("yes", "Always, from boot", "turns on `loginctl enable-linger` for your user",
                      note="Your user's services then start at boot and keep running after you log out.")]
    if ui.select("Keep it running when you log out?", options, "no") == "no":
        return
    try:
        manager.linger()
    except service.ServiceError as error:
        ui.fail(str(error))
        ui.info("Run `sudo loginctl enable-linger $USER` to turn it on.")
        return
    ui.ok("Agent Talk keeps running after you log out.")


def _show_summary(ui: Prompter, settings: Settings, device: str, running: bool):
    voice = next((v.name for v in voices.VOICES if v.id == settings.speech.voice), settings.speech.voice)
    lines = [f"[green]✓[/] Speech on {NAMES[device]}, voice {voice}"]
    if settings.server.public_url:
        lines.append(f"[green]✓[/] Phones reach it at {settings.server.public_url}")
    if running:
        lines.append(f"[green]✓[/] Running in the background on {settings.server.local_url}")
    steps = ["[bold]agent-talk project add ~/code/app --name app[/]"]
    if not running:
        steps.append("[bold]agent-talk serve[/]")
    steps.append("[bold]agent-talk pair[/] for each browser and phone")
    lines += ["", "Next: " + steps[0], *("      " + step for step in steps[1:])]
    ui.console.print()
    ui.console.print(Panel("\n".join(lines), title="[bold green]Agent Talk is ready[/]", title_align="left",
                           border_style="green", expand=False, padding=(0, 1)))


# Helpers

def _daemon_running(settings: Settings) -> bool:
    """Whether Agent Talk is serving, and so already holds its speech models."""
    try:
        with socket.create_connection(("127.0.0.1", settings.server.port), timeout=0.3):
            return True
    except OSError:
        return False


def _resample(audio: np.ndarray, rate: int, target: int) -> np.ndarray:
    positions = np.arange(0, len(audio), rate / target)
    return np.interp(positions, np.arange(len(audio)), audio).astype(np.float32)


def _words(text: str) -> list[str]:
    return re.sub(r"[^\w\s']", "", text.lower()).split()
