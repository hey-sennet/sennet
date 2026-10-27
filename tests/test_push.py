"""Push notifications: the payload's encryption, the server's VAPID key, subscriptions, and what notifies."""
import json
import struct
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.asymmetric.utils import encode_dss_signature
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from sennet import push
from sennet.push import Push, Vapid, b64, unb64
from conftest import pending_approval, wait_for

ENDPOINT = "https://push.example.com/send/abc"


class Browser:
    """A browser's side of a subscription: its keys, and decrypting what it is sent."""

    def __init__(self):
        self.key = ec.generate_private_key(ec.SECP256R1())
        self.auth = b"sixteen byte sec"

    @property
    def keys(self) -> dict:
        return {"p256dh": b64(push._point(self.key.public_key())), "auth": b64(self.auth)}

    def decrypt(self, body: bytes) -> dict:
        salt, (size, id_length) = body[:16], struct.unpack("!IB", body[16:21])
        sender = body[21:21 + id_length]
        assert size == push.RECORD_SIZE
        secret = self.key.exchange(ec.ECDH(), ec.EllipticCurvePublicKey.from_encoded_point(ec.SECP256R1(), sender))
        ikm = push._hkdf(self.auth, secret, b"WebPush: info\0" + push._point(self.key.public_key()) + sender, 32)
        key = push._hkdf(salt, ikm, b"Content-Encoding: aes128gcm\0", 16)
        nonce = push._hkdf(salt, ikm, b"Content-Encoding: nonce\0", 12)
        plain = AESGCM(key).decrypt(nonce, body[21 + id_length:], None)
        assert plain.endswith(b"\2")
        return json.loads(plain[:-1])


class PushService:
    """A push service on loopback that keeps what it is sent, answering `status`."""

    def __init__(self):
        self.received: list[tuple[dict, bytes]] = []
        self.status = 201
        service = self

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self):
                body = self.rfile.read(int(self.headers["Content-Length"]))
                service.received.append((dict(self.headers), body))
                self.send_response(service.status)
                self.end_headers()

            def log_message(self, *args):
                pass

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.endpoint = f"http://127.0.0.1:{self.server.server_address[1]}/send/abc"
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def wait(self, count: int = 1, timeout: float = 10) -> list[tuple[dict, bytes]]:
        deadline = time.monotonic() + timeout
        while len(self.received) < count and time.monotonic() < deadline:
            time.sleep(0.02)
        assert len(self.received) >= count, f"{len(self.received)} of {count} pushes arrived"
        return self.received


@pytest.fixture
def service():
    service = PushService()
    yield service
    service.server.shutdown()


def subscribe(client, browser: Browser, endpoint: str):
    """Subscribe the client's device; straight into the database, since the test service isn't https."""
    device_id = next(d["id"] for d in client.get("/api/devices").json() if d["current"])
    client.app.state.db.set_push(device_id, endpoint, browser.keys["p256dh"], browser.keys["auth"], list(push.KINDS))
    return device_id


def test_a_payload_decrypts_with_the_browsers_keys():
    browser = Browser()
    body = push.encrypt(json.dumps({"title": "hi"}).encode(), **browser.keys)
    assert browser.decrypt(body) == {"title": "hi"}
    assert push.encrypt(b"same", **browser.keys) != push.encrypt(b"same", **browser.keys)  # a new key and salt each time


def test_the_vapid_key_is_made_once_kept_private_and_signs_for_the_push_service(tmp_path):
    path = tmp_path / "data" / "push-key.pem"
    vapid = Vapid(path)
    assert path.stat().st_mode & 0o777 == 0o600
    assert Vapid(path).public_key == vapid.public_key

    authorization = vapid.authorization(ENDPOINT)
    token, key = authorization.removeprefix("vapid t=").split(", k=")
    assert key == vapid.public_key
    header, claims, signature = token.split(".")
    assert json.loads(unb64(claims))["aud"] == "https://push.example.com"
    assert json.loads(unb64(claims))["sub"] == push.CONTACT
    raw = unb64(signature)
    public = ec.EllipticCurvePublicKey.from_encoded_point(ec.SECP256R1(), unb64(key))
    public.verify(encode_dss_signature(int.from_bytes(raw[:32]), int.from_bytes(raw[32:])),
                  f"{header}.{claims}".encode(), ec.ECDSA(hashes.SHA256()))


def test_a_device_subscribes_chooses_what_notifies_and_unsubscribes(client):
    state = client.get("/api/push").json()
    assert len(unb64(state["key"])) == 65 and state["subscription"] is None

    subscription = {"endpoint": ENDPOINT, "keys": Browser().keys}
    assert client.put("/api/push", json={**subscription, "endpoint": "http://push.example.com/x"}).status_code == 400
    assert client.put("/api/push", json={**subscription, "keys": {"p256dh": "AAAA", "auth": "BBBB"}}).status_code == 400
    assert client.put("/api/push", json=subscription).json()["kinds"] == ["approval", "finished", "failed"]
    assert client.put("/api/push", json={**subscription, "kinds": ["failed", "approval"]}).json()["kinds"] == \
        ["approval", "failed"]
    # The browser renewing its subscription keeps what was chosen.
    renewed = client.put("/api/push", json={**subscription, "endpoint": ENDPOINT + "2"}).json()
    assert renewed == {"endpoint": ENDPOINT + "2", "kinds": ["approval", "failed"]}
    assert client.get("/api/push").json()["subscription"] == renewed

    assert client.delete("/api/push").status_code == 204
    assert client.get("/api/push").json()["subscription"] is None
    assert client.post("/api/push/test").status_code == 404


def test_unpairing_a_device_drops_its_subscription(client):
    device_id = subscribe(client, Browser(), ENDPOINT)
    client.delete(f"/api/devices/{device_id}")
    assert client.app.state.db.pushes() == []


def task(status: str, task_id: int = 1, error: str | None = None) -> dict:
    return {"type": "task", "task": {"id": task_id, "title": "Fix the login page", "status": status, "error": error}}


def test_turns_that_end_and_fail_notify_but_starting_and_cancelling_dont(client):
    notifier: Push = client.app.state.push
    task_id = 1

    assert notifier.notice(task("queued", task_id)) is None
    assert notifier.notice(task("running", task_id)) is None
    kind, payload = notifier.notice(task("completed", task_id))
    # What happened, and which task: nothing of the reply.
    assert (kind, payload) == ("finished", {"title": "Task finished", "body": "Fix the login page",
                                            "task_id": task_id, "kind": "finished"})
    assert notifier.notice(task("completed", task_id)) is None  # the same status again
    assert notifier.notice(task("running", task_id)) is None
    assert notifier.notice(task("cancelled", task_id)) is None
    assert notifier.notice(task("running", task_id)) is None
    assert notifier.notice(task("background", task_id))[0] == "finished"
    assert notifier.notice(task("completed", task_id))[1]["title"] == "Background work finished"
    assert notifier.notice(task("running", task_id)) is None
    kind, payload = notifier.notice(task("failed", task_id, error="the agent exited"))
    assert (kind, payload["title"], payload["body"]) == ("failed", "Task failed", "Fix the login page")
    # Seen first after a restart: nothing to compare it with.
    assert notifier.notice(task("completed", 99)) is None


def test_a_pending_approval_notifies(client, project):
    notifier: Push = client.app.state.push
    db = client.app.state.db
    task_id = db.add_task(project["id"], "codex", "Deploy")
    approval_id = db.add_approval(task_id, "command", "execute", "run npm test", {})
    approval = {"type": "approval", "approval": db.approval(approval_id)}
    assert notifier.notice(approval) == ("approval", {"title": "Approval needed", "body": "Run npm test\nDeploy",
                                                      "task_id": task_id, "kind": "approval",
                                                      "approval_id": approval_id, "answerable": True})
    db.decide_approval(approval_id, "approved", "user")
    assert notifier.notice({"type": "approval", "approval": db.approval(approval_id)}) is None


def test_a_destructive_approval_isnt_answerable_from_its_notification_and_a_long_one_is_cut(client, project):
    notifier: Push = client.app.state.push
    db = client.app.state.db
    task_id = db.add_task(project["id"], "codex", "Deploy")
    risky = db.add_approval(task_id, "command", "destructive", "run git push", {})
    kind, payload = notifier.notice({"type": "approval", "approval": db.approval(risky)})
    assert (payload["body"], payload["answerable"]) == ("Run git push\nDeploy", False)
    long = db.add_approval(task_id, "tool", "execute", "use search: " + "x" * 200, {})
    asked = notifier.notice({"type": "approval", "approval": db.approval(long)})[1]["body"].split("\n")[0]
    assert len(asked) == push.ASKED_SHOWN and asked.endswith("…")


def test_a_notification_approves_or_declines_but_not_always_and_never_a_destructive_one(client, project):
    task = client.post("/api/tasks", json={"project_id": project["id"], "text": "run:git push origin main"}).json()
    risky = pending_approval(client)
    response = client.post(f"/api/approvals/{risky['id']}", json={"decision": "approve", "via": "notification"})
    assert response.status_code == 400 and "card" in response.json()["detail"]
    client.post(f"/api/approvals/{risky['id']}", json={"decision": "decline"})  # on its card
    wait_for(client, task["id"], lambda t: t["status"] == "completed")

    task = client.post("/api/tasks", json={"project_id": project["id"], "text": "run:npm test"}).json()
    approval = pending_approval(client)
    always = client.post(f"/api/approvals/{approval['id']}", json={"decision": "approve_always", "via": "notification"})
    assert always.status_code == 400
    answered = client.post(f"/api/approvals/{approval['id']}", json={"decision": "approve", "via": "notification"})
    assert answered.status_code == 200 and answered.json()["status"] == "approved"
    assert wait_for(client, task["id"], lambda t: t["status"] == "completed")["messages"][-1]["text"] == "Ran it."
    # Already answered: the service worker says so.
    again = client.post(f"/api/approvals/{approval['id']}", json={"decision": "approve", "via": "notification"})
    assert again.status_code == 404


def test_a_finished_task_reaches_the_device_encrypted(client, project, service):
    browser = Browser()
    subscribe(client, browser, service.endpoint)
    created = client.post("/api/tasks", json={"project_id": project["id"], "text": "hello there"}).json()
    wait_for(client, created["id"], lambda t: t["status"] == "completed")

    headers, body = service.wait()[0]
    assert headers["Content-Encoding"] == "aes128gcm" and headers["Urgency"] == "normal"
    assert headers["Authorization"].startswith("vapid t=")
    assert browser.decrypt(body) == {"title": "Task finished", "body": "hello there",
                                     "task_id": created["id"], "kind": "finished"}


def test_only_the_kinds_chosen_notify(client, service):
    device_id = subscribe(client, Browser(), service.endpoint)
    client.app.state.db.set_push(device_id, service.endpoint, *Browser().keys.values(), ["failed"])
    client.portal.call(client.app.state.push.notify, "finished", {"title": "t"})
    client.portal.call(client.app.state.push.notify, "failed", {"title": "t"})
    time.sleep(0.3)
    assert len(service.wait()) == 1


def test_a_device_with_its_page_showing_isnt_notified(client, service, monkeypatch):
    browser = Browser()
    device_id = subscribe(client, browser, service.endpoint)
    notifier: Push = client.app.state.push
    with client.websocket_connect("/api/events") as events:
        events.send_json({"type": "showing", "showing": True})
        deadline = time.monotonic() + 5
        while not notifier.showing_on(device_id) and time.monotonic() < deadline:
            time.sleep(0.01)
        assert notifier.showing_on(device_id)
        client.portal.call(notifier.notify, "finished", {"title": "not sent"})

        # A page that stops saying so has gone, even if its socket looks open.
        monkeypatch.setattr(push, "SHOWING_S", 0)
        assert not notifier.showing_on(device_id)
        monkeypatch.setattr(push, "SHOWING_S", 75)

        events.send_json({"type": "showing", "showing": False})
        while notifier.showing_on(device_id) and time.monotonic() < deadline:
            time.sleep(0.01)
        client.portal.call(notifier.notify, "finished", {"title": "sent"})
    time.sleep(0.3)
    assert [browser.decrypt(body)["title"] for _, body in service.wait()] == ["sent"]
    assert notifier.showing == {}  # the socket closed


def test_a_subscription_the_browser_dropped_is_forgotten(client, service):
    subscribe(client, Browser(), service.endpoint)
    service.status = 410
    assert client.post("/api/push/test").status_code == 204
    service.wait()
    deadline = time.monotonic() + 5
    while client.app.state.db.pushes() and time.monotonic() < deadline:
        time.sleep(0.02)
    assert client.app.state.db.pushes() == []
