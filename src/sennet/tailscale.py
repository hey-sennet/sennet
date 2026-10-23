"""What Tailscale says about this machine, for setup and doctor (docs/m5-access.md).

Sennet reads this machine's tailnet address, to fill `server.public_url` for pairing
links, and what `tailscale serve` exposes: whether it passes Sennet's port on, and
whether Funnel puts it on the public internet. The one change it makes is `tailscale
serve --bg <port>`, and only when setup has asked.
"""
from __future__ import annotations

import json
import shutil
import subprocess

TIMEOUT_S = 5
SERVE_TIMEOUT_S = 300   # the first `serve` on a tailnet may wait while the user turns HTTPS on


def _json(*args: str) -> dict | None:
    if not shutil.which("tailscale"):
        return None
    try:
        result = subprocess.run(["tailscale", *args, "--json"], capture_output=True, text=True, timeout=TIMEOUT_S)
    except (OSError, subprocess.TimeoutExpired):
        return None
    if result.returncode != 0:
        return None
    try:
        return json.loads(result.stdout or "{}")
    except ValueError:
        return None


def status() -> dict | None:
    """`tailscale status`, or None without Tailscale or when it doesn't answer."""
    return _json("status")


def serve_status() -> dict | None:
    """`tailscale serve status`: what this machine serves to the tailnet, and what through Funnel."""
    return _json("serve", "status")


def address(tailnet: dict | None) -> str | None:
    """This machine's HTTPS address on the tailnet, when Tailscale is up and it has a name."""
    if not tailnet or tailnet.get("BackendState") != "Running":
        return None
    name = ((tailnet.get("Self") or {}).get("DNSName") or "").rstrip(".")
    return f"https://{name}" if name else None


def serves(serving: dict | None, port: int) -> bool:
    """Whether `tailscale serve` passes requests on to Sennet's port on this machine."""
    for site in ((serving or {}).get("Web") or {}).values():
        for handler in (site.get("Handlers") or {}).values():
            proxy = handler.get("Proxy") or ""
            local = any(host in proxy for host in ("127.0.0.1", "localhost", "[::1]"))
            if (local and proxy.rstrip("/").endswith(f":{port}")) or proxy == str(port):
                return True
    return False


def funnel(serving: dict | None) -> bool:
    """Whether Funnel puts anything this machine serves on the public internet."""
    return any((serving or {}).get("AllowFunnel", {}).values())


def serve(port: int) -> bool:
    """Run `tailscale serve --bg <port>`, its output shown as it comes: the first time on a tailnet it
    prints a link to turn HTTPS on and waits for it."""
    try:
        return subprocess.run(["tailscale", "serve", "--bg", str(port)], timeout=SERVE_TIMEOUT_S).returncode == 0
    except (OSError, subprocess.TimeoutExpired):
        return False


def serves_other(serving: dict | None, port: int) -> bool:
    """Whether `tailscale serve` passes anything on besides Sennet, which serving it at / would replace."""
    for site in ((serving or {}).get("Web") or {}).values():
        for handler in (site.get("Handlers") or {}).values():
            if not serves({"Web": {"_": {"Handlers": {"/": handler}}}}, port):
                return True
    return bool((serving or {}).get("TCP")) and not serves(serving, port)
