"""Web Push: tell a paired device when a task needs it while Sennet's page isn't showing there.

A phone stops a backgrounded page's microphone and audio, so nothing it would hear reaches it
until the page is opened again. A device that turns notifications on in Settings gets one when
a task waits on an approval, finishes, or fails, as it chose; tapping it opens the task.

Each device's subscription is the browser's push service URL and keys (db.push_subscriptions).
Payloads are encrypted for the browser (RFC 8291), so its push service carries them without
reading them, and requests are signed with this server's own VAPID key (RFC 8292), made on
first use and kept in the data directory. No push service is involved but the browser's own.

A page tells the server, over its events socket, while it is showing (`seen`). A device with a
page showing isn't notified: it hears the reply there. A backgrounded phone may not say it has
gone, so a page that is showing says so again every `SHOWING_S / 2` seconds, and one that hasn't
for `SHOWING_S` counts as gone. The service worker also skips a notification while a page is
showing (frontend/public/sw.js).
"""
from __future__ import annotations

import asyncio
import base64
import json
import logging
import os
import struct
import time
import urllib.error
import urllib.request
from pathlib import Path
from urllib.parse import urlsplit

from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.asymmetric.utils import decode_dss_signature
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.hkdf import HKDF

from .db import Database
from .events import EventBus

log = logging.getLogger(__name__)

KINDS = ("approval", "finished", "failed")
SHOWING_S = 75                  # a page that last said it was showing longer ago than this has gone
TTL_S = 24 * 60 * 60            # how long a push service keeps a notification for a device that's offline
RECORD_SIZE = 4096
SEND_TIMEOUT_S = 15
# Who runs this server, for push services to contact: the project, rather than this machine's address.
CONTACT = "https://github.com/hey-sennet/sennet"
# A task that was in one of these and is now completed (or left work running) has finished a turn.
WORKING = ("running", "awaiting_approval", "background")


def b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def unb64(text: str) -> bytes:
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


def _hkdf(salt: bytes, ikm: bytes, info: bytes, length: int) -> bytes:
    return HKDF(algorithm=hashes.SHA256(), length=length, salt=salt, info=info).derive(ikm)


def _point(key: ec.EllipticCurvePublicKey) -> bytes:
    return key.public_bytes(serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint)


def keys_valid(p256dh: str, auth: str) -> bool:
    """A subscription's keys as browsers give them: a P-256 public key and a 16-byte secret."""
    try:
        ec.EllipticCurvePublicKey.from_encoded_point(ec.SECP256R1(), unb64(p256dh))
        return len(unb64(auth)) == 16
    except ValueError:
        return False


def encrypt(payload: bytes, p256dh: str, auth: str) -> bytes:
    """`payload` encrypted for a browser's subscription keys, as one aes128gcm record (RFC 8291)."""
    ua_public = unb64(p256dh)
    browser = ec.EllipticCurvePublicKey.from_encoded_point(ec.SECP256R1(), ua_public)
    ephemeral = ec.generate_private_key(ec.SECP256R1())
    as_public = _point(ephemeral.public_key())
    secret = ephemeral.exchange(ec.ECDH(), browser)
    ikm = _hkdf(unb64(auth), secret, b"WebPush: info\0" + ua_public + as_public, 32)
    salt = os.urandom(16)
    key = _hkdf(salt, ikm, b"Content-Encoding: aes128gcm\0", 16)
    nonce = _hkdf(salt, ikm, b"Content-Encoding: nonce\0", 12)
    record = AESGCM(key).encrypt(nonce, payload + b"\2", None)  # \2: the last (and only) record
    return salt + struct.pack("!IB", RECORD_SIZE, len(as_public)) + as_public + record


class Vapid:
    """This server's key for signing push requests, made once and kept in `path`."""

    def __init__(self, path: Path):
        if path.exists():
            key = serialization.load_pem_private_key(path.read_bytes(), password=None)
            assert isinstance(key, ec.EllipticCurvePrivateKey)
        else:
            key = ec.generate_private_key(ec.SECP256R1())
            path.parent.mkdir(parents=True, exist_ok=True)
            pem = key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                                    serialization.NoEncryption())
            fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(fd, "wb") as out:
                out.write(pem)
        self.key = key
        self.public_key = b64(_point(key.public_key()))  # the browser's applicationServerKey

    def authorization(self, endpoint: str) -> str:
        parts = urlsplit(endpoint)
        claims = {"aud": f"{parts.scheme}://{parts.netloc}", "exp": int(time.time()) + 12 * 60 * 60, "sub": CONTACT}
        signing = b64(json.dumps({"typ": "JWT", "alg": "ES256"}).encode()) + "." + b64(json.dumps(claims).encode())
        r, s = decode_dss_signature(self.key.sign(signing.encode(), ec.ECDSA(hashes.SHA256())))
        token = signing + "." + b64(r.to_bytes(32, "big") + s.to_bytes(32, "big"))
        return f"vapid t={token}, k={self.public_key}"


class Push:
    def __init__(self, db: Database, bus: EventBus, key_path: Path):
        self.db = db
        self.bus = bus
        self.key_path = key_path
        self._vapid: Vapid | None = None
        self.showing: dict[object, tuple[int, float]] = {}  # a page's socket → its device, when it last said so
        self.statuses: dict[int, str] = {}                  # task id → its status in the last task event
        self.watcher: asyncio.Task | None = None
        self.sending: set[asyncio.Task] = set()

    @property
    def vapid(self) -> Vapid:
        if self._vapid is None:
            self._vapid = Vapid(self.key_path)
        return self._vapid

    def start(self):
        self.watcher = asyncio.ensure_future(self._watch())

    async def close(self):
        for job in [self.watcher, *self.sending]:
            if job:
                job.cancel()
        await asyncio.gather(*[j for j in (self.watcher, *self.sending) if j], return_exceptions=True)

    # Pages showing

    def seen(self, page: object, device_id: int, showing: bool):
        if showing:
            self.showing[page] = (device_id, time.monotonic())
        else:
            self.showing.pop(page, None)

    def gone(self, page: object):
        self.showing.pop(page, None)

    def showing_on(self, device_id: int) -> bool:
        now = time.monotonic()
        return any(d == device_id and now - at < SHOWING_S for d, at in self.showing.values())

    # What notifies

    async def _watch(self):
        while True:  # again after falling behind the bus
            subscriber = self.bus.subscribe()
            try:
                while (event := await subscriber.next()) is not None:
                    try:
                        if notice := self.notice(event):
                            self.notify(*notice)
                    except Exception:
                        log.exception("push: couldn't notify about an event")
            finally:
                self.bus.unsubscribe(subscriber)
            log.warning("push: fell behind the event bus; watching again")

    def notice(self, event: dict) -> tuple[str, dict] | None:
        """What `event` notifies, as a kind and a payload for the service worker; None for nothing."""
        if event.get("type") == "approval":
            approval = event["approval"]
            task = self.db.task(approval["task_id"])
            if approval["status"] != "pending" or task is None:
                return None
            return "approval", self._payload(task, "Approval needed", "approval")
        if event.get("type") == "task_removed":
            self.statuses.pop(event["task_id"], None)
        if event.get("type") != "task" or not (task := event.get("task")):
            return None
        before = self.statuses.get(task["id"])
        self.statuses[task["id"]] = task["status"]
        if task["status"] == before:
            return None
        if task["status"] == "failed":
            return "failed", self._payload(task, "Task failed", "failed")
        if task["status"] in ("completed", "background") and before in WORKING:
            said = "Background work finished" if before == "background" else "Task finished"
            return "finished", self._payload(task, said, "finished")
        return None

    def _payload(self, task: dict, what: str, kind: str) -> dict:
        """Kept short, so it reads at a glance: what happened, and the task's title (what was said
        and done is in the task); none of the task's words but its title leave this machine."""
        return {"title": what, "body": task["title"], "task_id": task["id"], "kind": kind}

    # Sending

    def notify(self, kind: str, payload: dict):
        """Send `payload` to each device that wants `kind` and has no page showing."""
        for subscription in self.db.pushes():
            if kind in subscription["kinds"] and not self.showing_on(subscription["device_id"]):
                self._send_soon(subscription, payload, urgent=kind == "approval")

    def test(self, device_id: int) -> bool:
        """A notification to this device, shown even with its page showing; False without a subscription."""
        subscription = self.db.push(device_id)
        if subscription is None:
            return False
        self._send_soon(subscription, {"title": "Sennet", "body": "Notifications are on for this device.",
                                       "kind": "test", "task_id": None}, urgent=True)
        return True

    def _send_soon(self, subscription: dict, payload: dict, urgent: bool):
        job = asyncio.ensure_future(self._send(subscription, payload, urgent))
        self.sending.add(job)
        job.add_done_callback(self.sending.discard)

    async def _send(self, subscription: dict, payload: dict, urgent: bool):
        try:
            status = await asyncio.to_thread(self._post, subscription, payload, urgent)
        except Exception as error:
            log.warning("push: couldn't reach device %s's push service: %s", subscription["device_id"], error)
            return
        if status in (404, 410):  # the browser dropped the subscription; it subscribes again from Settings
            log.info("push: device %s's subscription has gone", subscription["device_id"])
            self.db.remove_push(subscription["device_id"], subscription["endpoint"])
        elif status >= 400:
            log.warning("push: device %s's push service answered %s", subscription["device_id"], status)

    def _post(self, subscription: dict, payload: dict, urgent: bool) -> int:
        body = encrypt(json.dumps(payload).encode(), subscription["p256dh"], subscription["auth"])
        request = urllib.request.Request(subscription["endpoint"], data=body, method="POST", headers={
            "Authorization": self.vapid.authorization(subscription["endpoint"]),
            "Content-Encoding": "aes128gcm", "Content-Type": "application/octet-stream",
            "TTL": str(TTL_S), "Urgency": "high" if urgent else "normal"})
        try:
            with urllib.request.urlopen(request, timeout=SEND_TIMEOUT_S) as response:
                return response.status
        except urllib.error.HTTPError as error:
            return error.code
