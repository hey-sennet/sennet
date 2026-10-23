"""Paired devices: who may use Sennet (HLD §8, docs/m5-access.md).

There are no accounts. A device pairs once by redeeming a pairing code, and gets a
token of its own, which it keeps in a cookie until the device is revoked. A pairing
code comes in two forms, either of which redeems it, once: a long code carried in a
link (`<url>/pair#<code>`), and a short one to type on a phone that can't scan the
link's QR code. Codes and tokens are stored only as SHA-256 hashes.

`sennet pair` makes codes straight in the database, so anyone who can run it (the
user) can always pair a device, with or without the daemon running.
"""
from __future__ import annotations

import hashlib
import re
import secrets
import time
from collections import deque
from dataclasses import dataclass

from .db import Database

COOKIE = "sennet_device"
COOKIE_MAX_AGE_S = 365 * 24 * 60 * 60
TERMINAL = "the terminal"
TERMINAL_TTL_S = 15 * 60       # a code from `sennet pair`
DEVICE_TTL_S = 5 * 60          # a code another device shows
# Typed codes leave out characters that are easy to confuse: 0 and O, 1, I and L.
ALPHABET = "23456789ABCDEFGHJKMNPQRSTUVWXYZ"
TYPED_LENGTH = 8               # about 40 bits; with expiry and the throttle, enough to type
SEEN_EVERY_S = 60              # how often a device's last-seen time is written
NAME_LENGTH = 60


@dataclass
class Pairing:
    link_code: str
    typed_code: str              # shown as XXXX-XXXX
    expires_at: float

    def link(self, base_url: str) -> str:
        return f"{base_url.rstrip('/')}/pair#{self.link_code}"


def _hash(secret: str) -> str:
    return hashlib.sha256(secret.encode()).hexdigest()


def _typed(code: str) -> str:
    """A typed code as stored: capitals, without the dash or spaces people add."""
    return re.sub(r"[^0-9A-Z]", "", code.upper())


def new_pairing(db: Database, created_by: str, ttl_s: float) -> Pairing:
    typed = "".join(secrets.choice(ALPHABET) for _ in range(TYPED_LENGTH))
    pairing = Pairing(secrets.token_urlsafe(16), f"{typed[:4]}-{typed[4:]}", time.time() + ttl_s)
    db.add_pairing(_hash(pairing.link_code), _hash(typed), created_by, pairing.expires_at)
    return pairing


def redeem(db: Database, code: str, user_agent: str) -> tuple[dict, str] | None:
    """Pair a device with a code in either form: the new device and its token, or None for a code that
    is wrong, used or expired."""
    code = code.strip()
    if not code or len(code) > 64:
        return None
    pairing = db.take_pairing(_hash(code)) or db.take_pairing(_hash(_typed(code)))
    if pairing is None:
        return None
    token = secrets.token_urlsafe(32)
    device = db.add_device(device_name(user_agent), _hash(token), pairing["created_by"])
    return device, token


def device_for(db: Database, token: str | None) -> dict | None:
    """The device a cookie's token belongs to, noting that it was seen."""
    if not token:
        return None
    device = db.device_by_token(_hash(token))
    if device and time.time() - device["last_seen_at"] > SEEN_EVERY_S:
        device["last_seen_at"] = time.time()
        db.update_device(device["id"], last_seen_at=device["last_seen_at"])
    return device


def rename(db: Database, device_id: int, name: str) -> dict | None:
    name = " ".join(name.split())[:NAME_LENGTH]
    if not name:
        raise ValueError("a device needs a name")
    db.update_device(device_id, name=name)
    return db.device(device_id)


def device_name(user_agent: str) -> str:
    """A name a person recognises, such as "Firefox on Linux", from a browser's user agent."""
    browser = next((name for pattern, name in (
        (r"Edg/", "Edge"), (r"OPR/|Opera", "Opera"), (r"Firefox/|FxiOS/", "Firefox"),
        (r"Chrome/|CriOS/", "Chrome"), (r"Safari/", "Safari")) if re.search(pattern, user_agent)), "Browser")
    system = next((name for pattern, name in (
        (r"iPhone", "iPhone"), (r"iPad", "iPad"), (r"Android", "Android"), (r"Mac OS X|Macintosh", "macOS"),
        (r"Windows", "Windows"), (r"CrOS", "ChromeOS"), (r"Linux", "Linux")) if re.search(pattern, user_agent)), None)
    return f"{browser} on {system}" if system else browser


class Throttle:
    """Wrong pairing codes, counted across every client: past `limit` a minute, redeeming waits."""

    def __init__(self, limit: int = 10, window_s: float = 60):
        self.limit = limit
        self.window_s = window_s
        self.failures: deque[float] = deque()

    def wait(self) -> float:
        """Seconds until a code may be tried again; 0 when it may be now."""
        now = time.monotonic()
        while self.failures and now - self.failures[0] > self.window_s:
            self.failures.popleft()
        return self.failures[0] + self.window_s - now if len(self.failures) >= self.limit else 0

    def failed(self):
        self.failures.append(time.monotonic())
