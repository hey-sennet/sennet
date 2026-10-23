"""Command line: `agent-talk setup`, `serve`, `service …`, `pair`, `devices`, `project …` and `doctor`."""
from __future__ import annotations

import argparse
import asyncio
import logging
import shutil
import subprocess
import sys
import time

from . import __version__, access, projects, service
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

    service = commands.add_parser("service", help="run Agent Talk in the background, from login")
    service_commands = service.add_subparsers(dest="action", required=True)
    service_commands.add_parser("install", help="install the service and start it (again, to update it)")
    service_commands.add_parser("uninstall", help="stop the service and remove it")
    service_commands.add_parser("start", help="start the service")
    service_commands.add_parser("stop", help="stop the service")
    service_commands.add_parser("restart", help="restart the service")
    service_commands.add_parser("status", help="whether the service is installed and running")
    logs = service_commands.add_parser("logs", help="show the service's log")
    logs.add_argument("-f", "--follow", action="store_true", help="keep showing new lines")

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
    elif args.command == "service":
        sys.exit(service_command(settings, paths, args))
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
    if (running := service.health(port)) is not None:
        sys.exit(f"Agent Talk {running['version']} is already running on port {port}. If it's the service, "
                 "`agent-talk service stop` stops it, and `agent-talk service logs` shows its output.")
    if service.port_taken(port):
        sys.exit(f"another program is using port {port}; stop it, or pass --port or set server.port in the config")
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


def service_command(settings: Settings, paths: Paths, args) -> int:
    port = settings.server.port
    try:
        if args.action == "install":
            manager, found = service.install(port)
            print(f"Agent Talk {found['version']} is running as a {manager.name}, and starts at login.")
            print(f"  {manager.file}")
            if manager.lingering() is False:
                print("It stops when you log out. To keep it running then, run `loginctl enable-linger`.")
            if not _paired(paths):
                print()
                pair_command(settings, paths)
            return 0
        manager = service.manager()
        status = manager.status()
        if args.action == "status":
            return _show_service(manager, status, port)
        if not status.installed:
            if args.action == "uninstall":
                print("the service isn't installed")
                return 0
            print("the service isn't installed; `agent-talk service install` installs and starts it", file=sys.stderr)
            return 1
        if args.action == "uninstall":
            manager.uninstall()
            print(f"stopped the service and removed {manager.file}. Your data and models are still there; "
                  "`agent-talk remove-data` deletes them.")
        elif args.action == "logs":
            try:
                return subprocess.run(manager.logs_command(args.follow)).returncode
            except KeyboardInterrupt:
                return 0
        elif args.action == "stop":
            manager.stop()
            print("stopped; `agent-talk service start` starts it again, and it starts at the next login")
        else:  # start, restart
            getattr(manager, args.action)()
            found = service.wait_until_up(port)
            if found is None:
                print(f"the service didn't answer on port {port}; `agent-talk service logs` shows why",
                      file=sys.stderr)
                return 1
            print(f"Agent Talk {found['version']} is running")
    except service.ServiceError as error:
        print(error, file=sys.stderr)
        return 1
    return 0


def _show_service(manager, status: service.Status, port: int) -> int:
    if not status.installed:
        print("not installed; `agent-talk service install` installs and starts it")
        return 3  # as systemctl status does for a unit that isn't running
    found = service.health(port) if status.running else None
    print(f"{manager.name}: {status.detail}{f', pid {status.pid}' if status.pid else ''}")
    print(f"  file:    {manager.file}")
    if status.command:
        print(f"  runs:    {' '.join(status.command)} serve")
    if found:
        print(f"  answers: Agent Talk {found['version']} on port {port}")
    elif status.running:
        print(f"  answers: nothing on port {port} yet")
    return 0 if status.running else 3


def _paired(paths: Paths) -> bool:
    db = Database(paths.database)
    try:
        return bool(db.devices())
    finally:
        db.close()


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
    from .server import WEB_DIR

    print(f"version:  {__version__} ({' '.join(service.program())})")
    print(f"config:   {paths.config_file}{'' if paths.config_file.exists() else ' (defaults)'}")
    print(f"data:     {paths.database}")
    if not (WEB_DIR / "index.html").is_file():
        print("client:   problem: the web client isn't built, so browsers get only the API "
              "(see frontend/README.md)")
        problems += 1
    agents = registry.build(settings.agents.commands)
    for name, agent in agents.items():
        status = asyncio.run(agent.status())
        ok = status.installed and status.logged_in
        problems += not ok
        print(f"{name + ':':<9} {'ok' if ok else 'problem'}: {status.detail}")
    problems += not speech_ok(settings, paths)
    problems += not access_ok(settings, paths)
    problems += not service_ok(settings, agents)
    return 1 if problems else 0


def service_ok(settings: Settings, agents: dict) -> bool:
    """Whether the service runs this install, answers, and finds the agents on its PATH."""
    try:
        manager = service.manager()
    except service.ServiceError as error:
        print(f"service:  {error}")
        return True
    status = manager.status()
    port = settings.server.port
    if not status.installed:
        print("service:  not installed; `agent-talk service install` runs Agent Talk in the background, from login")
        return True
    ok = True
    if status.command != service.program():
        print(f"service:  problem: it runs {' '.join(status.command or ['?'])}, not this install; "
              "run `agent-talk service install` to update it")
        ok = False
    found = service.health(port) if status.running else None
    if not status.running:
        print(f"service:  problem: installed but {status.detail}; `agent-talk service logs` shows why, "
              "and `agent-talk service start` starts it")
        ok = False
    elif found is None:
        print(f"service:  problem: running, but nothing answers on port {port}; see `agent-talk service logs`")
        ok = False
    elif found["version"] != __version__:
        print(f"service:  problem: running {found['version']}, but {__version__} is installed; "
              "run `agent-talk service restart`")
        ok = False
    else:
        lingering = manager.lingering()
        later = " (stops when you log out; `loginctl enable-linger` keeps it running)" if lingering is False else ""
        print(f"service:  ok: running{f' (pid {status.pid})' if status.pid else ''}, starts at login{later}")
    for name, agent in agents.items():
        program = agent.command[0]
        if shutil.which(program) and not shutil.which(program, path=status.path or ""):
            print(f"service:  problem: the service can't find {program} ({agent.title}) on its PATH; run "
                  "`agent-talk service install` from a shell that finds it")
            ok = False
    return ok


def access_ok(settings: Settings, paths: Paths) -> bool:
    """Paired devices, the address phones use, and what Tailscale exposes."""
    from . import tailscale

    db = Database(paths.database)
    try:
        paired = len(db.devices())
    finally:
        db.close()
    print(f"devices:  {paired} paired" if paired else "devices:  none paired yet; run `agent-talk pair`")
    port = settings.server.port
    tailnet = tailscale.address(tailscale.status()) if shutil.which("tailscale") else None
    serving = tailscale.serve_status() if tailnet else None
    if settings.server.public_url:
        print(f"phones:   {settings.server.public_url} (server.public_url)")
    elif tailnet:
        print(f"phones:   not set; run `agent-talk setup` to use {tailnet}")
    else:
        print("phones:   not set (needs Tailscale or a reverse proxy with HTTPS; see server.public_url)")
    if not shutil.which("tailscale"):
        print("tailscale: not installed (one way to reach Agent Talk from a phone)")
    elif tailnet is None:
        print("tailscale: installed, not connected")
    elif tailscale.funnel(serving):
        print("tailscale: problem: Funnel is on, so this machine serves the public internet. A paired device "
              "can run commands as you; turn it off with `tailscale funnel reset` unless you mean it.")
        return False
    elif tailscale.serves(serving, port):
        print(f"tailscale: ok: {tailnet} serves Agent Talk")
    else:
        print(f"tailscale: up at {tailnet}, not serving Agent Talk; run `tailscale serve --bg {port}`")
    return True


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
