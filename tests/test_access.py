"""Paired devices: pairing codes, the device check on every request and socket, and revoking."""
import sqlite3
import time

import pytest
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from sennet import access, cli, server
from sennet.db import Database
from sennet.server import create_app
from conftest import HEADERS, pair, paired

FIREFOX = "Mozilla/5.0 (X11; Linux x86_64; rv:140.0) Gecko/20100101 Firefox/140.0"
IPHONE = ("Mozilla/5.0 (iPhone; CPU iPhone OS 19_0 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) "
          "Version/19.0 Mobile/15E148 Safari/604.1")


@pytest.fixture
def unpaired(settings, paths):
    with TestClient(create_app(settings, paths), headers=HEADERS) as client:
        yield client


def closed_with(client, path) -> int:
    with pytest.raises(WebSocketDisconnect) as closed:
        with client.websocket_connect(path) as websocket:
            websocket.receive_text()
    return closed.value.code


def test_an_unpaired_browser_gets_the_app_but_not_the_api(unpaired):
    assert unpaired.get("/api/state").status_code == 401
    assert unpaired.get("/api/directories").status_code == 401
    assert unpaired.post("/api/tasks", json={"project_id": 1, "text": "hi"}).status_code == 401
    assert closed_with(unpaired, "/api/events") == server.UNPAIRED
    assert closed_with(unpaired, "/api/voice") == server.UNPAIRED


def test_loopback_is_not_trusted(settings, paths):
    with TestClient(create_app(settings, paths), headers=HEADERS, client=("127.0.0.1", 50000)) as local:
        assert local.get("/api/state").status_code == 401


def test_a_link_code_pairs_once(unpaired):
    code = access.new_pairing(unpaired.app.state.db, access.TERMINAL, 60).link_code
    response = unpaired.post("/api/pair", json={"code": code}, headers={"User-Agent": FIREFOX})
    assert response.status_code == 200
    assert response.json()["name"] == "Firefox on Linux"
    assert response.json()["paired_by"] == "the terminal"
    assert unpaired.get("/api/state").status_code == 200
    unpaired.cookies.clear()
    assert unpaired.post("/api/pair", json={"code": code}).status_code == 400
    assert unpaired.get("/api/state").status_code == 401


def test_the_typed_code_pairs_however_it_is_typed(unpaired):
    typed = access.new_pairing(unpaired.app.state.db, access.TERMINAL, 60).typed_code
    assert unpaired.post("/api/pair", json={"code": f" {typed.lower()} "}).status_code == 200
    assert unpaired.get("/api/state").status_code == 200


def test_either_form_uses_up_the_code(unpaired):
    pairing = access.new_pairing(unpaired.app.state.db, access.TERMINAL, 60)
    assert unpaired.post("/api/pair", json={"code": pairing.typed_code}).status_code == 200
    assert unpaired.post("/api/pair", json={"code": pairing.link_code}).status_code == 400


def test_an_expired_code_fails(unpaired):
    code = access.new_pairing(unpaired.app.state.db, access.TERMINAL, -1).link_code
    assert unpaired.post("/api/pair", json={"code": code}).status_code == 400


def test_wrong_codes_are_throttled_for_everyone(unpaired):
    for _ in range(10):
        assert unpaired.post("/api/pair", json={"code": "WRONG-CODE"}).status_code == 400
    code = access.new_pairing(unpaired.app.state.db, access.TERMINAL, 60).link_code
    refused = unpaired.post("/api/pair", json={"code": code})
    assert refused.status_code == 429
    assert int(refused.headers["retry-after"]) > 0


def test_pairing_still_needs_the_csrf_header(unpaired):
    code = access.new_pairing(unpaired.app.state.db, access.TERMINAL, 60).link_code
    assert unpaired.post("/api/pair", json={"code": code}, headers={"X-Sennet": ""}).status_code == 403


def test_the_cookie_is_kept_from_scripts_and_other_sites(unpaired):
    db = unpaired.app.state.db
    plain = unpaired.post("/api/pair", json={"code": access.new_pairing(db, access.TERMINAL, 60).link_code})
    cookie = plain.headers["set-cookie"].lower()
    assert "httponly" in cookie and "samesite=strict" in cookie and "secure" not in cookie
    over_https = unpaired.post("/api/pair", json={"code": access.new_pairing(db, access.TERMINAL, 60).link_code},
                               headers={"Origin": "https://desktop.tailnet.ts.net"})
    assert "secure" in over_https.headers["set-cookie"].lower()


def test_tokens_and_codes_are_stored_only_as_hashes(client, paths):
    pairing = access.new_pairing(client.app.state.db, access.TERMINAL, 60)
    token = client.cookies[access.COOKIE]
    dump = "\n".join(sqlite3.connect(paths.database).iterdump())
    assert token not in dump
    assert pairing.link_code not in dump and pairing.typed_code.replace("-", "") not in dump


def test_responses_carry_a_content_security_policy(client):
    headers = client.get("/api/state").headers
    assert "script-src 'self'" in headers["content-security-policy"]
    assert "frame-ancestors 'none'" in headers["content-security-policy"]
    assert headers["x-content-type-options"] == "nosniff"


def test_a_device_lists_the_devices_and_knows_itself(client):
    other = TestClient(client.app, headers=HEADERS)
    pair(other, client.app.state.db)
    listed = client.get("/api/devices").json()
    assert len(listed) == 2
    assert [d["current"] for d in listed] == [True, False]
    renamed = client.patch(f"/api/devices/{listed[1]['id']}", json={"name": "  Kitchen   phone "})
    assert renamed.json()["name"] == "Kitchen phone"
    assert client.patch(f"/api/devices/{listed[1]['id']}", json={"name": " "}).status_code == 400


def test_a_paired_device_shows_a_code_for_another(client, unpaired):
    shown = client.post("/api/devices/pairing").json()
    assert shown["link"].startswith("http://testserver/pair#")
    assert shown["qr"].startswith("data:image/svg+xml")
    assert shown["expires_at"] - time.time() == pytest.approx(access.DEVICE_TTL_S, abs=5)
    me = client.get("/api/devices").json()[0]
    response = unpaired.post("/api/pair", json={"code": shown["code"]}, headers={"User-Agent": IPHONE})
    assert response.json()["name"] == "Safari on iPhone"
    assert response.json()["paired_by"] == me["name"]


def test_pairing_links_use_the_public_address(settings, paths):
    settings.server.public_url = "https://desktop.tailnet.ts.net"
    with paired(TestClient(create_app(settings, paths), headers=HEADERS)) as client:
        shown = client.post("/api/devices/pairing").json()
    assert shown["link"].startswith("https://desktop.tailnet.ts.net/pair#")
    assert shown["local_only"] is False


def test_a_link_shown_on_loopback_without_a_public_address_is_local_only(settings, paths):
    with paired(TestClient(create_app(settings, paths), headers=HEADERS, base_url="http://127.0.0.1:7710")) as client:
        assert client.post("/api/devices/pairing").json()["local_only"] is True


def test_revoking_a_device_closes_its_sockets(client):
    other = TestClient(client.app, headers=HEADERS)
    phone = pair(other, client.app.state.db)
    with pytest.raises(WebSocketDisconnect) as closed:
        with other.websocket_connect("/api/events") as websocket:
            assert client.delete(f"/api/devices/{phone['id']}").status_code == 204
            websocket.receive_text()
    assert closed.value.code == server.UNPAIRED
    assert other.get("/api/state").status_code == 401
    assert client.get("/api/state").status_code == 200


def test_revoking_this_device_forgets_its_cookie(client):
    me = client.get("/api/devices").json()[0]
    response = client.delete(f"/api/devices/{me['id']}")
    assert 'sennet_device=""' in response.headers["set-cookie"] or "max-age=0" in response.headers["set-cookie"].lower()
    assert client.get("/api/state").status_code == 401


def test_a_device_revoked_from_the_terminal_loses_its_sockets(settings, paths, monkeypatch):
    monkeypatch.setattr(server, "SWEEP_S", 0.05)
    with paired(TestClient(create_app(settings, paths), headers=HEADERS)) as client:
        me = client.get("/api/devices").json()[0]
        with pytest.raises(WebSocketDisconnect) as closed:
            with client.websocket_connect("/api/events") as websocket:
                client.app.state.db.remove_device(me["id"])
                websocket.receive_text()
    assert closed.value.code == server.UNPAIRED


def test_device_names_from_user_agents():
    assert access.device_name(FIREFOX) == "Firefox on Linux"
    assert access.device_name(IPHONE) == "Safari on iPhone"
    assert access.device_name("Mozilla/5.0 (Linux; Android 16; Pixel 9) AppleWebKit/537.36 (KHTML, like Gecko) "
                              "Chrome/140.0.0.0 Mobile Safari/537.36") == "Chrome on Android"
    assert access.device_name("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) "
                              "Chrome/140.0.0.0 Safari/537.36 Edg/140.0.0.0") == "Edge on Windows"
    assert access.device_name("") == "Browser"


def test_the_terminal_pairs_and_revokes(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("SENNET_HOME", str(tmp_path))
    cli.main(["pair"])
    printed = capsys.readouterr().out
    assert "http://127.0.0.1:7710/pair#" in printed
    link_code = printed.split("/pair#")[1].split()[0]
    db = Database(tmp_path / "sennet.db")
    device, _ = access.redeem(db, link_code, FIREFOX)
    db.close()
    cli.main(["devices"])
    assert "Firefox on Linux" in capsys.readouterr().out
    cli.main(["devices", "revoke", str(device["id"])])
    assert "revoked Firefox on Linux" in capsys.readouterr().out
    cli.main(["devices"])
    assert "no devices paired yet" in capsys.readouterr().out


def test_the_terminal_shows_a_qr_code_for_the_public_address(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("SENNET_HOME", str(tmp_path))
    (tmp_path / "config.toml").write_text('[server]\npublic_url = "https://desktop.tailnet.ts.net/"\n')
    cli.main(["pair"])
    printed = capsys.readouterr().out
    assert "https://desktop.tailnet.ts.net/pair#" in printed
    assert "█" in printed or "▀" in printed


def test_a_database_from_before_pairing_gains_the_tables(paths):
    paths.data_dir.mkdir(parents=True)
    old = sqlite3.connect(paths.database)
    old.executescript("CREATE TABLE projects (id INTEGER PRIMARY KEY, name TEXT, path TEXT, aliases TEXT, "
                      "agent TEXT, approval_mode TEXT, access TEXT, project_config INTEGER, model TEXT, "
                      "effort TEXT, created_at REAL); PRAGMA user_version = 5;")
    old.close()
    db = Database(paths.database)
    assert db.devices() == []
    assert db.conn.execute("PRAGMA user_version").fetchone()[0] == 6
    db.close()


def test_revoking_a_device_ends_its_voice_session(client):
    other = TestClient(client.app, headers=HEADERS)
    phone = pair(other, client.app.state.db)
    with pytest.raises(WebSocketDisconnect) as closed:
        with other.websocket_connect("/api/voice") as websocket:
            assert websocket.receive_json()["type"] == "ready"
            client.delete(f"/api/devices/{phone['id']}")
            while True:
                websocket.receive_text()
    assert closed.value.code == server.UNPAIRED
    assert not client.app.state.voice.sessions
