"""Project registration rules shared by the CLI and the API."""
from __future__ import annotations

import re
import sqlite3
from pathlib import Path

from .agents import registry
from .agents.base import ACCESS_LEVELS, APPROVAL_MODES
from .db import Database

NAME_RE = re.compile(r"^[\w][\w .-]{0,39}$")


class ProjectError(ValueError):
    pass


def _check_names(db: Database, names: list[str], exclude_id: int | None = None):
    taken = {}
    for project in db.projects():
        if project["id"] == exclude_id:
            continue
        for name in [project["name"], *project["aliases"]]:
            taken[name.lower()] = project["name"]
    seen = set()
    for name in names:
        if not NAME_RE.match(name):
            raise ProjectError(f"invalid name {name!r}: use letters, numbers, spaces, dots, dashes (max 40)")
        key = name.lower()
        if key in taken:
            raise ProjectError(f"{name!r} is already used by project {taken[key]!r}")
        if key in seen:
            raise ProjectError(f"{name!r} is listed twice")
        seen.add(key)


def add(db: Database, path: str, name: str | None = None, aliases: list[str] | None = None,
        agent: str = registry.DEFAULT) -> dict:
    directory = Path(path).expanduser().resolve()
    if not directory.is_dir():
        raise ProjectError(f"{directory} is not a directory")
    if agent not in registry.NAMES:
        raise ProjectError(f"agent must be one of {registry.NAMES}")
    name = (name or directory.name).strip()
    aliases = [a.strip() for a in aliases or [] if a.strip()]
    _check_names(db, [name, *aliases])
    try:
        return db.add_project(name, str(directory), aliases, agent)
    except sqlite3.IntegrityError:
        raise ProjectError(f"{directory} is already a project") from None


def update(db: Database, project_id: int, **fields) -> dict:
    project = db.project(project_id)
    if project is None:
        raise ProjectError("no such project")
    allowed = {"name", "aliases", "agent", "approval_mode", "access"}
    if unknown := set(fields) - allowed:
        raise ProjectError(f"cannot change {', '.join(sorted(unknown))}")
    if "approval_mode" in fields and fields["approval_mode"] not in (None, *APPROVAL_MODES):
        raise ProjectError(f"approval_mode must be one of {APPROVAL_MODES} or null")
    if "access" in fields and fields["access"] not in (None, *ACCESS_LEVELS):
        raise ProjectError(f"access must be one of {ACCESS_LEVELS} or null")
    if "agent" in fields and fields["agent"] not in registry.NAMES:
        raise ProjectError(f"agent must be one of {registry.NAMES}")
    if "name" in fields or "aliases" in fields:
        fields["name"] = (fields.get("name") or project["name"]).strip()
        fields["aliases"] = [a.strip() for a in fields.get("aliases", project["aliases"]) if a.strip()]
        _check_names(db, [fields["name"], *fields["aliases"]], exclude_id=project_id)
    if fields:
        db.update_project(project_id, **fields)
    return db.project(project_id)


def find(db: Database, name: str) -> dict | None:
    """Look a project up by name or alias (case-insensitive) or numeric ID."""
    if name.isdigit():
        return db.project(int(name))
    key = name.strip().lower()
    for project in db.projects():
        if key in [project["name"].lower(), *(a.lower() for a in project["aliases"])]:
            return project
    return None
