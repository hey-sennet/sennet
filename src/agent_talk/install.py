"""How this copy of Agent Talk was installed, and `agent-talk upgrade` and `remove-data`.

The documented install is `uv tool install agent-talk`, whose receipt says what was
asked for, including the speech extra `setup` chose. Upgrading keeps that, and keeps the
config, database and models, which live in the platform directories outside the tool.
"""
from __future__ import annotations

import shutil
import subprocess
import sys
import tomllib
from pathlib import Path

from . import __version__, service
from .config import Paths
from .db import Database
from .prompts import size

ACTIVE = ("queued", "running", "awaiting_approval", "background")   # task statuses a restart interrupts


def uv_tool_receipt() -> dict | None:
    receipt = Path(sys.prefix) / "uv-receipt.toml"
    if not receipt.is_file():
        return None
    try:
        return tomllib.loads(receipt.read_text())
    except (OSError, tomllib.TOMLDecodeError):
        return None


def uv_tool_requirement() -> dict | None:
    """The requirement `uv tool install` was given, when it came from a package index (not a checkout,
    a URL or git), which is what `uv tool upgrade` and a reinstall with another extra can follow."""
    receipt = uv_tool_receipt()
    if receipt is None:
        return None
    requirement = next((r for r in receipt.get("tool", {}).get("requirements", []) if r.get("name") == "agent-talk"),
                       None)
    if requirement and not any(k in requirement for k in ("git", "url", "directory", "path", "editable")):
        return requirement
    return None


def uv_tool_spec(extra: str) -> str | None:
    """What to give `uv tool install --reinstall` for this install with `extra`: the same package from
    the index, or the same wheel file (as when trying a local build). None for checkouts, URLs and git."""
    if (requirement := uv_tool_requirement()) is not None:
        return f"agent-talk[{extra}]" + (requirement.get("specifier") or "")
    receipt = uv_tool_receipt() or {}
    requirement = next((r for r in receipt.get("tool", {}).get("requirements", []) if r.get("name") == "agent-talk"),
                       {})
    wheel = Path(requirement.get("path") or "")
    if wheel.suffix == ".whl" and wheel.is_file() and not requirement.get("editable"):
        return f"agent-talk[{extra}] @ {wheel.resolve().as_uri()}"
    return None


def source_checkout() -> Path | None:
    """The repository this package runs from, when it is an editable install."""
    for parent in Path(__file__).resolve().parents:
        if (parent / "pyproject.toml").is_file() and (parent / "src" / "agent_talk").is_dir():
            return parent
    return None


def active_tasks(paths: Paths) -> list[dict]:
    if not paths.database.exists():
        return []
    db = Database(paths.database)
    try:
        return [t for t in db.tasks() if t["status"] in ACTIVE]
    finally:
        db.close()


def upgrade(paths: Paths, port: int, assume_yes: bool) -> int:
    if uv_tool_requirement() is None:
        if (checkout := source_checkout()) is not None:
            print(f"This Agent Talk runs from a checkout ({checkout}); update it with `git pull` and `uv sync`.")
        elif uv_tool_receipt() is not None:
            print("This Agent Talk was installed with `uv tool install` from a file, URL or git; "
                  "install the new version the same way.")
        else:
            print("This Agent Talk wasn't installed with `uv tool install`; upgrade it the way it was installed "
                  "(for example `pip install --upgrade agent-talk`).")
        return 1
    if not shutil.which("uv"):
        print("uv isn't on PATH; install it (https://docs.astral.sh/uv/), then run `agent-talk upgrade` again.")
        return 1
    manager, status = _service()
    running = active_tasks(paths) if status and status.running else []
    if running and not assume_yes:
        names = ", ".join(f"“{t['title']}”" for t in running[:3]) + (" …" if len(running) > 3 else "")
        print(f"{len(running)} task{'s' if len(running) > 1 else ''} still going ({names}). Restarting Agent Talk "
              "stops them, and they're marked unknown.")
        if not _confirm("Upgrade and restart anyway?"):
            return 1
    if subprocess.run(["uv", "tool", "upgrade", "agent-talk"]).returncode != 0:
        print("The upgrade failed; see the output above.", file=sys.stderr)
        return 1
    program = service.program()
    new = subprocess.run([*program, "--version"], capture_output=True, text=True).stdout.strip() or "?"
    if new == __version__:
        print(f"Agent Talk {__version__} is the latest version.")
        return 0
    print(f"Upgraded Agent Talk from {__version__} to {new}.")
    if status and status.running:
        # The new version restarts its own service, so it waits for itself to answer.
        return subprocess.run([*program, "service", "restart"]).returncode
    print("Restart `agent-talk serve` to use it, and reload Agent Talk in your browsers.")
    return 0


def remove_data(paths: Paths, port: int, assume_yes: bool) -> int:
    """Delete the config, the database and the downloaded models, after showing what and asking."""
    manager, status = _service()
    # Anything on the port counts: an Agent Talk too old to answer the health check is still using the data.
    if (status and status.running) or service.port_taken(port):
        print(f"Agent Talk is running (something is using port {port}). Stop it first (`agent-talk service stop`, or Ctrl+C where "
              "`agent-talk serve` runs), then run this again.", file=sys.stderr)
        return 1
    targets = [d for d in dict.fromkeys([paths.config_dir, paths.data_dir]) if d.exists()]
    if not targets:
        print("There's no Agent Talk data to remove.")
        return 0
    print("This deletes, for good:")
    for directory in targets:
        used = _bytes(directory)
        print(f"  {directory}  ({size(used) if used >= 1e6 else 'under 1 MB'})")
    print("That's the config, the projects list, every task and its history, paired devices, "
          "and the downloaded speech models. Your projects' own files are not touched.")
    if not assume_yes and not _confirm("Delete it all?", default=False):
        print("Nothing was deleted.")
        return 1
    for directory in targets:
        shutil.rmtree(directory)
    print("Deleted.")
    if status and status.installed:
        print(f"The {manager.name} is still installed; it starts with fresh data next time. "
              "`agent-talk service uninstall` removes it.")
    print("`uv tool uninstall agent-talk` removes the program itself.")
    return 0


def _service():
    try:
        manager = service.manager()
    except service.ServiceError:
        return None, None
    return manager, manager.status()


def _confirm(message: str, default: bool = True) -> bool:
    if not sys.stdin.isatty():
        print("(not a terminal, so not going on; pass --yes to)")
        return False
    import questionary

    answer = questionary.confirm(message, default=default).ask()
    return bool(answer)


def _bytes(directory: Path) -> int:
    return sum(f.stat().st_size for f in directory.rglob("*") if f.is_file() and not f.is_symlink())
