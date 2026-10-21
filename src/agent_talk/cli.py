"""Command line: `agent-talk serve`, `agent-talk project …`, `agent-talk doctor`."""
from __future__ import annotations

import argparse
import asyncio
import logging
import shutil
import sys

from . import __version__, projects
from .agents.codex import CodexAgent
from .config import Paths, Settings
from .db import Database


def main(argv: list[str] | None = None):
    parser = argparse.ArgumentParser(prog="agent-talk", description="Talk to your coding agents.")
    parser.add_argument("--version", action="version", version=__version__)
    commands = parser.add_subparsers(dest="command", required=True)

    serve = commands.add_parser("serve", help="run the server")
    serve.add_argument("--host", help="bind address (default from config: 127.0.0.1)")
    serve.add_argument("--port", type=int, help="port (default from config: 7710)")

    project = commands.add_parser("project", help="manage projects")
    project_commands = project.add_subparsers(dest="action", required=True)
    add = project_commands.add_parser("add", help="register a directory")
    add.add_argument("path")
    add.add_argument("--name", help="spoken name (default: directory name)")
    add.add_argument("--alias", action="append", default=[], help="another name to call it by (repeatable)")
    add.add_argument("--agent", default="codex", choices=projects.AGENTS)
    project_commands.add_parser("list", help="list projects")
    remove = project_commands.add_parser("remove", help="unregister a project (files are not touched)")
    remove.add_argument("name")

    commands.add_parser("doctor", help="check agents and configuration")

    args = parser.parse_args(argv)
    paths = Paths.default()
    try:
        settings = Settings.load(paths.config_file)
    except (ValueError, OSError) as error:
        sys.exit(f"config error in {paths.config_file}: {error}")

    if args.command == "serve":
        serve_command(settings, paths, args)
    elif args.command == "project":
        project_command(paths, args)
    elif args.command == "doctor":
        sys.exit(doctor(settings, paths))


def serve_command(settings: Settings, paths: Paths, args):
    import uvicorn

    from .server import WEB_DIR, create_app

    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    host = args.host or settings.server.host
    port = args.port or settings.server.port
    if host not in ("127.0.0.1", "::1", "localhost"):
        logging.warning("binding %s exposes Agent Talk beyond this machine; there is no login yet. "
                        "Use `tailscale serve` with the default loopback address instead.", host)
    if not WEB_DIR.is_dir():
        logging.warning("web client not built; only the API is available (see frontend/README.md)")
    print(f"Agent Talk on http://{host}:{port}", flush=True)
    uvicorn.run(create_app(settings, paths), host=host, port=port, log_level="warning")


def project_command(paths: Paths, args):
    db = Database(paths.database)
    try:
        if args.action == "add":
            project = projects.add(db, args.path, args.name, args.alias, args.agent)
            print(f"added {project['name']} → {project['path']}")
        elif args.action == "list":
            rows = db.projects()
            if not rows:
                print("no projects yet; add one with `agent-talk project add PATH`")
            for project in rows:
                aliases = f" (also: {', '.join(project['aliases'])})" if project["aliases"] else ""
                print(f"{project['id']:>3}  {project['name']}{aliases}  [{project['agent']}]  {project['path']}")
        elif args.action == "remove":
            project = projects.find(db, args.name)
            if project is None:
                sys.exit(f"no project called {args.name!r}")
            db.remove_project(project["id"])
            print(f"removed {project['name']} (its files were not touched)")
    except projects.ProjectError as error:
        sys.exit(str(error))
    finally:
        db.close()


def doctor(settings: Settings, paths: Paths) -> int:
    problems = 0
    print(f"config:   {paths.config_file}{'' if paths.config_file.exists() else ' (defaults)'}")
    print(f"data:     {paths.database}")
    status = asyncio.run(CodexAgent(settings.agents.codex_command).status())
    ok = status.installed and status.logged_in
    problems += not ok
    print(f"codex:    {'ok' if ok else 'problem'}: {status.detail}")
    if shutil.which("tailscale"):
        print("tailscale: installed (phone access: `tailscale serve --bg 7710`)")
    else:
        print("tailscale: not installed (needed only for phone access)")
    return 1 if problems else 0
