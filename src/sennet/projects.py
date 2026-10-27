"""Project registration rules shared by the CLI and the API."""
from __future__ import annotations

import os
import re
import sqlite3
from pathlib import Path

from .agents import registry
from .agents.base import ACCESS_LEVELS, APPROVAL_MODES
from .db import Database

NAME_RE = re.compile(r"^[\w][\w .-]{0,39}$")
LISTING_LIMIT = 500


class ProjectError(ValueError):
    pass


def _check_name(db: Database, name: str, exclude_id: int | None = None):
    if not NAME_RE.match(name):
        raise ProjectError(f"invalid name {name!r}: use letters, numbers, spaces, dots, dashes (max 40)")
    for project in db.projects():
        if project["id"] != exclude_id and project["name"].lower() == name.lower():
            raise ProjectError(f"{name!r} is already used by another project")


def add(db: Database, path: str, name: str | None = None, agent: str = registry.DEFAULT) -> dict:
    directory = Path(path).expanduser().resolve()
    if not directory.is_dir():
        raise ProjectError(f"{directory} is not a directory")
    if agent not in registry.NAMES:
        raise ProjectError(f"agent must be one of {registry.NAMES}")
    name = (name or directory.name).strip()
    _check_name(db, name)
    try:
        return db.add_project(name, str(directory), agent)
    except sqlite3.IntegrityError:
        raise ProjectError(f"{directory} is already a project") from None


def update(db: Database, project_id: int, **fields) -> dict:
    project = db.project(project_id)
    if project is None:
        raise ProjectError("no such project")
    allowed = {"name", "agent", "approval_mode", "access", "project_config", "model", "effort", "worktrees"}
    if unknown := set(fields) - allowed:
        raise ProjectError(f"cannot change {', '.join(sorted(unknown))}")
    if "approval_mode" in fields and fields["approval_mode"] not in (None, *APPROVAL_MODES):
        raise ProjectError(f"approval_mode must be one of {APPROVAL_MODES} or null")
    if "access" in fields and fields["access"] not in (None, *ACCESS_LEVELS):
        raise ProjectError(f"access must be one of {ACCESS_LEVELS} or null")
    if "agent" in fields and fields["agent"] not in registry.NAMES:
        raise ProjectError(f"agent must be one of {registry.NAMES}")
    if "project_config" in fields and not isinstance(fields["project_config"], bool):
        raise ProjectError("project_config must be true or false")
    if "worktrees" in fields and not isinstance(fields["worktrees"], bool):
        raise ProjectError("worktrees must be true or false")
    if fields.get("worktrees") and not project["repo"]:
        raise ProjectError("a worktree for each task needs the top directory of a git repository")
    if fields.get("agent", project["agent"]) != project["agent"]:
        fields.setdefault("model", None)  # another agent's models
        fields.setdefault("effort", None)
    elif "model" in fields and fields["model"] != project["model"]:
        fields.setdefault("effort", None)  # the new model's own default
    if "name" in fields:
        fields["name"] = (fields["name"] or project["name"]).strip()
        _check_name(db, fields["name"], exclude_id=project_id)
    if fields:
        db.update_project(project_id, **fields)
    return db.project(project_id)


def find(db: Database, name: str) -> dict | None:
    """Look a project up by name (case-insensitive) or numeric ID."""
    if name.isdigit():
        return db.project(int(name))
    key = name.strip().lower()
    for project in db.projects():
        if project["name"].lower() == key:
            return project
    return None


def directories(path: str | None = None) -> dict:
    """The folders in `path` (home by default), for picking a project without typing its path.
    Names only, no files; hidden folders are left out, and git repositories are marked."""
    directory = Path(path).expanduser() if path else Path.home()
    try:
        directory = directory.resolve(strict=True)
    except (OSError, RuntimeError):
        raise ProjectError(f"{directory} does not exist") from None
    if not directory.is_dir():
        raise ProjectError(f"{directory} is not a directory")
    folders = []
    try:
        with os.scandir(directory) as entries:
            for entry in entries:
                if entry.name.startswith("."):
                    continue
                try:
                    if not entry.is_dir():
                        continue
                except OSError:
                    continue
                folders.append({"name": entry.name, "path": str(directory / entry.name), "repo": _repo(entry.path)})
    except PermissionError:
        raise ProjectError(f"{directory} can't be read") from None
    folders.sort(key=lambda f: f["name"].lower())
    return {"path": str(directory), "parent": str(directory.parent) if directory.parent != directory else None,
            "home": str(Path.home()), "repo": _repo(directory), "directories": folders[:LISTING_LIMIT],
            "more": max(len(folders) - LISTING_LIMIT, 0)}


def _repo(path) -> bool:
    try:
        return os.path.exists(os.path.join(path, ".git"))
    except OSError:
        return False
