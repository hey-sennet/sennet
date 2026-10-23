"""Command line: `agent-talk setup`, `serve`, `pair`, `devices`, `project …` and `doctor`."""
from __future__ import annotations

import argparse
import asyncio
import logging
import shutil
import sys
import time

from . import __version__, access, projects
from .agents import registry
from .config import Paths, Settings
from .db import Database


def main(argv: list[str] | None = None):
    parser = argparse.ArgumentParser(prog="agent-talk", description="Talk to your coding agents.")
    parser.add_argument("--version", action="version", version=__version__)
    commands = parser.add_subparsers(dest="command", required=True)

    setup = commands.add_parser("setup", help="choose a speech tier, install it and download its models")
    setup.add_argument("--device", choices=("cpu", "cuda"), help="skip the recommendation and use this tier")
    setup.add_argument("--yes", action="store_true", help="accept the recommendations without asking")

    serve = commands.add_parser("serve", help="run the server")
    serve.add_argument("--host", help="bind address (default from config: 127.0.0.1)")
    serve.add_argument("--port", type=int, help="port (default from config: 7710)")

    project = commands.add_parser("project", help="manage projects")
    project_commands = project.add_subparsers(dest="action", required=True)
    add = project_commands.add_parser("add", help="register a directory")
    add.add_argument("path")
    add.add_argument("--name", help="spoken name (default: directory name)")
    add.add_argument("--alias", action="append", default=[], help="another name to call it by (repeatable)")
    add.add_argument("--agent", default=registry.DEFAULT, choices=registry.NAMES)
    project_commands.add_parser("list", help="list projects")
    remove = project_commands.add_parser("remove", help="unregister a project (files are not touched)")
    remove.add_argument("name")

    commands.add_parser("pair", help="print a one-time link and code to pair a browser or phone")
    devices = commands.add_parser("devices", help="list or revoke paired devices")
    device_commands = devices.add_subparsers(dest="action")
    device_commands.add_parser("list", help="list paired devices (the default)")
    revoke = device_commands.add_parser("revoke", help="unpair a device; it must pair again to get back in")
    revoke.add_argument("id", type=int, help="the device's number, from `agent-talk devices`")

    commands.add_parser("doctor", help="check agents and configuration")

    args = parser.parse_args(argv)
    paths = Paths.default()
    try:
        settings = Settings.load(paths.config_file)
    except (ValueError, OSError) as error:
        sys.exit(f"config error in {paths.config_file}: {error}")

    if args.command == "setup":
        from . import setup as guided
        sys.exit(guided.run(settings, paths, args.device, args.yes))
    elif args.command == "serve":
        serve_command(settings, paths, args)
    elif args.command == "pair":
        pair_command(settings, paths)
    elif args.command == "devices":
        devices_command(paths, args)
    elif args.command == "project":
        project_command(paths, args)
    elif args.command == "doctor":
        sys.exit(doctor(settings, paths))


def serve_command(settings: Settings, paths: Paths, args):
    import uvicorn

    from .server import WEB_DIR, create_app

    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    host = settings.server.host = args.host or settings.server.host
    port = settings.server.port = args.port or settings.server.port  # agents call back on it
    if host not in ("127.0.0.1", "::1", "localhost"):
        logging.warning("binding %s serves Agent Talk without HTTPS, so browsers won't let it use the "
                        "microphone. Use `tailscale serve` or a reverse proxy with the default loopback "
                        "address instead.", host)
    if not WEB_DIR.is_dir():
        logging.warning("web client not built; only the API is available (see frontend/README.md)")
    print(f"Agent Talk on http://{host}:{port}", flush=True)
    db = Database(paths.database)
    try:
        if not db.devices():  # first run: like Jupyter, print a way in
            pairing = access.new_pairing(db, access.TERMINAL, access.TERMINAL_TTL_S)
            print(f"No browser is paired yet. Open {pairing.link(settings.server.local_url)} "
                  f"(works once, for {access.TERMINAL_TTL_S // 60} minutes), or run `agent-talk pair`.", flush=True)
    finally:
        db.close()
    uvicorn.run(create_app(settings, paths), host=host, port=port, log_level="warning")


def pair_command(settings: Settings, paths: Paths):
    import segno

    db = Database(paths.database)
    try:
        pairing = access.new_pairing(db, access.TERMINAL, access.TERMINAL_TTL_S)
    finally:
        db.close()
    public = settings.server.public_url
    print(f"Pair a browser with Agent Talk. The code works once, for {access.TERMINAL_TTL_S // 60} minutes.\n")
    print(f"  On this computer:  {pairing.link(settings.server.local_url)}")
    if public:
        print(f"  On your phone:     {pairing.link(public)}  (or scan the code below)")
    print(f"  Or type the code:  {pairing.typed_code}")
    if public:
        print()
        segno.make(pairing.link(public), error="m").terminal(compact=True)
    else:
        print("\nOn another device, open Agent Talk's address and type the code. To get a link and a QR code "
              "for your phone, set server.public_url in the config, or run `agent-talk setup`.")


def devices_command(paths: Paths, args):
    db = Database(paths.database)
    try:
        if args.action == "revoke":
            device = db.device(args.id)
            if device is None:
                sys.exit(f"no device {args.id}; `agent-talk devices` lists them")
            db.remove_device(device["id"])
            print(f"revoked {device['name']}; it has to pair again to get back in")
            return
        rows = db.devices()
        if not rows:
            print("no devices paired yet; pair one with `agent-talk pair`")
        for device in rows:
            seen = time.strftime("%Y-%m-%d %H:%M", time.localtime(device["last_seen_at"]))
            print(f"{device['id']:>3}  {device['name']}  (paired by {device['paired_by']}, last seen {seen})")
    finally:
        db.close()


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
    for name, agent in registry.build(settings.agents.commands).items():
        status = asyncio.run(agent.status())
        ok = status.installed and status.logged_in
        problems += not ok
        print(f"{name + ':':<9} {'ok' if ok else 'problem'}: {status.detail}")
    problems += not speech_ok(settings, paths)
    if shutil.which("tailscale"):
        print("tailscale: installed (phone access: `tailscale serve --bg 7710`)")
    else:
        print("tailscale: not installed (needed only for phone access)")
    return 1 if problems else 0


def speech_ok(settings: Settings, paths: Paths) -> bool:
    from .speech import models
    from .speech.runtime import resolve_device, runtime_distribution

    if not settings.speech.enabled:
        print("speech:   off (speech.enabled = false)")
        return True
    runtime = runtime_distribution()
    if runtime is None:
        print("speech:   problem: no ONNX runtime installed; run `agent-talk setup`")
        return False
    device = resolve_device(settings.speech.device)
    missing = [m.id for m in models.MODELS[device].values() if not models.present(paths.models, m)]
    if missing:
        print(f"speech:   problem: {device} models missing ({', '.join(missing)}); run `agent-talk setup`")
        return False
    print(f"speech:   ok: {device} via {runtime}, models in {paths.models}")
    return True
