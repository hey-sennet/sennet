"""Images in a message (attachments.py): uploaded first, named by a typed or spoken message, passed
to the agent as its CLI takes them, shown back to paired devices, and removed with their task."""
import base64
import sqlite3
import struct
import time
import zlib

import pytest
from fastapi.testclient import TestClient

from sennet import attachments
from sennet.attachments import AttachmentError, sniff
from sennet.db import SCHEMA_VERSION, Database
from sennet.server import create_app
from conftest import HEADERS, paired, wait_for
from test_contract import add_project, limited_client
from test_voice import SPEECH_FRAME, Voice, speech  # noqa: F401 (the fixture)


def png(width: int = 3, height: int = 2) -> bytes:
    def chunk(kind: bytes, data: bytes) -> bytes:
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data))
    rows = b"".join(b"\x00" + b"\xff\x00\x00" * width for _ in range(height))
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(rows)) + chunk(b"IEND", b""))


def jpeg(width: int, height: int) -> bytes:
    app0 = b"\xff\xe0\x00\x10JFIF\x00\x01\x01\x00\x00\x01\x00\x01\x00\x00"
    sof0 = b"\xff\xc0\x00\x11\x08" + struct.pack(">HH", height, width) + b"\x03" + b"\x01\x22\x00\x02\x11\x01\x03\x11\x01"
    return b"\xff\xd8" + app0 + sof0 + b"\xff\xd9"


def webp_lossless(width: int, height: int) -> bytes:
    bits = (width - 1) | ((height - 1) << 14)
    body = b"VP8L" + struct.pack("<I", 5) + b"\x2f" + bits.to_bytes(4, "little")
    return b"RIFF" + struct.pack("<I", 4 + len(body)) + b"WEBP" + body


def finished(task):
    return task["status"] in ("completed", "failed", "cancelled", "unknown")


def upload(client, data: bytes | None = None) -> dict:
    response = client.post("/api/attachments", content=data if data is not None else png())
    assert response.status_code == 201, response.text
    return response.json()


# Headers

def test_images_are_known_by_their_header():
    assert sniff(png(640, 480)) == attachments.Sniffed("image/png", 640, 480)
    assert sniff(jpeg(1600, 1200)) == attachments.Sniffed("image/jpeg", 1600, 1200)
    assert sniff(webp_lossless(800, 600)) == attachments.Sniffed("image/webp", 800, 600)


@pytest.mark.parametrize("data", [b"", b"GIF89a....", b"<svg xmlns='http://www.w3.org/2000/svg'/>",
                                  b"\xff\xd8\xff\xe0\x00\x10JFIF", png(0, 5), jpeg(20000, 10)])
def test_anything_else_is_refused(data):
    with pytest.raises(AttachmentError):
        sniff(data)


# The API

def test_an_image_is_uploaded_and_shown_back(client):
    image = upload(client)
    assert image == {"id": image["id"], "media_type": "image/png", "width": 3, "height": 2, "thumbnail": None}
    shown = client.get(f"/api/attachments/{image['id']}")
    assert shown.status_code == 200 and shown.content == png()
    assert shown.headers["content-type"] == "image/png"
    assert shown.headers["x-content-type-options"] == "nosniff"


def test_what_isnt_an_image_is_refused(client):
    response = client.post("/api/attachments", content=b"#!/bin/sh\nrm -rf ~\n")
    assert response.status_code == 400 and "isn't a JPEG, PNG or WebP" in response.json()["detail"]


def test_a_big_upload_is_refused(client, monkeypatch):
    monkeypatch.setattr("sennet.server.MAX_BYTES", 1000)
    response = client.post("/api/attachments", content=png() + b"\0" * 2000)
    assert response.status_code == 413


def test_images_are_only_for_paired_devices(settings, paths, codex_log):
    with paired(TestClient(create_app(settings, paths), headers=HEADERS)) as client:
        image = upload(client)
        client.cookies.clear()
        assert client.get(f"/api/attachments/{image['id']}").status_code == 401
        assert client.post("/api/attachments", content=png()).status_code == 401


def test_an_unsent_image_can_be_taken_out(client, project):
    image = upload(client)
    assert client.delete(f"/api/attachments/{image['id']}").status_code == 204
    assert client.get(f"/api/attachments/{image['id']}").status_code == 404
    sent = upload(client)
    client.post("/api/tasks", json={"project_id": project["id"], "text": "hi", "attachments": [sent["id"]]})
    assert client.delete(f"/api/attachments/{sent['id']}").status_code == 409


def test_a_thumbnail_is_shown_in_the_timeline_but_never_sent_to_the_agent(client, project, codex_log, paths):
    image = upload(client, jpeg(1600, 1200))
    assert client.get(f"/api/attachments/{image['id']}/thumbnail").status_code == 404
    small = webp_lossless(480, 360)
    response = client.put(f"/api/attachments/{image['id']}/thumbnail", content=small)
    assert response.status_code == 200 and response.json()["thumbnail"] == "image/webp"
    shown = client.get(f"/api/attachments/{image['id']}/thumbnail")
    assert shown.content == small and shown.headers["content-type"] == "image/webp"
    assert "immutable" in shown.headers["cache-control"] and "private" in shown.headers["cache-control"]
    task = client.post("/api/tasks", json={"project_id": project["id"], "text": "x", "attachments": [image["id"]]}).json()
    task = wait_for(client, task["id"], finished)
    assert task["messages"][0]["attachments"][0]["thumbnail"] == "image/webp"
    assert [i["path"] for i in codex_log("turn/start")[0]["params"]["input"] if i["type"] == "localImage"] == \
        [str(paths.data_dir / "attachments" / f"{image['id']}.jpg")]
    # Sent, it is as it was: the timeline caches it for good.
    again = client.put(f"/api/attachments/{image['id']}/thumbnail", content=small)
    assert again.status_code == 409


@pytest.mark.parametrize("data, why", [(webp_lossless(2000, 100), "pixels"), (b"not an image", "isn't a JPEG")])
def test_a_thumbnail_has_to_be_small_and_an_image(client, data, why):
    image = upload(client)
    response = client.put(f"/api/attachments/{image['id']}/thumbnail", content=data)
    assert response.status_code == 400 and why in response.json()["detail"]


def test_a_big_thumbnail_is_refused_as_it_arrives(client):
    image = upload(client)
    response = client.put(f"/api/attachments/{image['id']}/thumbnail", content=png() + b"\0" * 300 * 1024)
    assert response.status_code == 413


def test_a_thumbnail_goes_with_its_image(client, project, paths):
    image = upload(client)
    client.put(f"/api/attachments/{image['id']}/thumbnail", content=png(2, 1))
    folder = paths.data_dir / "attachments"
    assert sorted(f.name for f in folder.iterdir()) == [f"{image['id']}-thumbnail.png", f"{image['id']}.png"]
    client.app.state.attachments.sweep()  # a thumbnail isn't a stray file
    assert len(list(folder.iterdir())) == 2
    client.delete(f"/api/attachments/{image['id']}")
    assert list(folder.iterdir()) == []


# Messages

def test_a_task_starts_with_images_and_codex_gets_them_by_path(client, project, codex_log, paths):
    images = [upload(client), upload(client, jpeg(10, 10))]
    task = client.post("/api/tasks", json={"project_id": project["id"], "text": "what's wrong here?",
                                           "attachments": [i["id"] for i in images]}).json()
    task = wait_for(client, task["id"], finished)
    assert task["status"] == "completed"
    sent = task["messages"][0]
    assert sent["text"] == "what's wrong here?"
    assert [a["id"] for a in sent["attachments"]] == [i["id"] for i in images]
    assert sent["attachments"][1] == {"id": images[1]["id"], "media_type": "image/jpeg", "width": 10, "height": 10,
                                      "thumbnail": None}
    inputs = codex_log("turn/start")[0]["params"]["input"]
    folder = paths.data_dir / "attachments"
    assert inputs == [{"type": "localImage", "path": str(folder / f"{images[0]['id']}.png")},
                      {"type": "localImage", "path": str(folder / f"{images[1]['id']}.jpg")},
                      {"type": "text", "text": "what's wrong here?", "text_elements": []}]


def test_claude_gets_images_as_blocks_before_the_text(live, tmp_path, claude_log):
    (tmp_path / "repo").mkdir()
    project = live.post("/api/projects", json={"path": str(tmp_path / "repo"), "agent": "claude"}).json()
    task = live.post("/api/tasks", json={"project_id": project["id"], "text": "hi"}).json()
    wait_for(live, task["id"], finished)
    image = upload(live)
    live.post(f"/api/tasks/{task['id']}/messages", json={"text": "and this?", "attachments": [image["id"]]})
    wait_for(live, task["id"], lambda t: finished(t) and len(t["messages"]) == 4)
    first, follow_up = [entry["message"]["content"] for entry in claude_log("in")]
    assert first == "hi"  # without images, as before
    assert follow_up == [{"type": "image", "source": {"type": "base64", "media_type": "image/png",
                                                      "data": base64.b64encode(png()).decode()}},
                         {"type": "text", "text": "and this?"}]


def test_a_message_can_be_only_images(client, project, codex_log):
    image = upload(client)
    task = client.post("/api/tasks", json={"project_id": project["id"], "attachments": [image["id"]]}).json()
    assert task["title"] == "An image"
    wait_for(client, task["id"], finished)
    assert [i["type"] for i in codex_log("turn/start")[0]["params"]["input"]] == ["localImage"]
    response = client.post("/api/tasks", json={"project_id": project["id"], "text": " "})
    assert response.status_code == 400 and "empty" in response.json()["detail"]


def test_an_image_goes_with_one_message_only(client, project):
    image = upload(client)
    first = client.post("/api/tasks", json={"project_id": project["id"], "text": "a", "attachments": [image["id"]]})
    assert first.status_code == 201
    again = client.post(f"/api/tasks/{first.json()['id']}/messages", json={"text": "b", "attachments": [image["id"]]})
    assert again.status_code == 400 and "already sent" in again.json()["detail"]
    missing = client.post("/api/tasks", json={"project_id": project["id"], "text": "c", "attachments": [999]})
    assert missing.status_code == 400 and "gone" in missing.json()["detail"]


def test_too_many_images_are_refused(client, project):
    ids = [upload(client)["id"] for _ in range(attachments.MAX_PER_MESSAGE + 1)]
    response = client.post("/api/tasks", json={"project_id": project["id"], "text": "x", "attachments": ids})
    assert response.status_code == 400 and "up to" in response.json()["detail"]


def test_an_agent_that_cant_take_images_is_not_sent_them(settings, paths, codex_log, tmp_path):
    with limited_client(settings, paths, images=False) as client:
        project = add_project(client, tmp_path)
        image = upload(client)
        response = client.post("/api/tasks", json={"project_id": project["id"], "text": "look",
                                                    "attachments": [image["id"]]})
        assert response.status_code == 400 and "Codex can't take images" in response.json()["detail"]
        assert client.get("/api/state").json()["tasks"] == []


def test_deleting_a_task_deletes_its_images(client, project, paths):
    image = upload(client)
    task = client.post("/api/tasks", json={"project_id": project["id"], "text": "x", "attachments": [image["id"]]}).json()
    wait_for(client, task["id"], finished)
    file = paths.data_dir / "attachments" / f"{image['id']}.png"
    assert file.is_file()
    assert client.delete(f"/api/tasks/{task['id']}").status_code == 204
    assert not file.exists()
    assert client.app.state.db.attachment(image["id"]) is None


def test_images_no_message_took_are_swept_after_a_day(client, project, monkeypatch):
    old, recent = upload(client), upload(client)
    store = client.app.state.attachments
    client.app.state.db.execute("UPDATE attachments SET created_at = ? WHERE id = ?",
                                time.time() - attachments.UNSENT_TTL_S - 1, old["id"])
    stray = store.folder / "77.png"
    stray.write_bytes(png())
    store.sweep()
    assert client.get(f"/api/attachments/{old['id']}").status_code == 404
    assert client.get(f"/api/attachments/{recent['id']}").status_code == 200
    assert not stray.exists()


def test_files_are_kept_to_this_user(client):
    image = upload(client)
    store = client.app.state.attachments
    assert store.folder.stat().st_mode & 0o777 == 0o700
    assert store.path(client.app.state.db.attachment(image["id"])).stat().st_mode & 0o777 == 0o600


# Voice

@pytest.fixture
def voice_client(settings, paths, codex_log, speech):  # noqa: F811
    with paired(TestClient(create_app(settings, paths, speech=speech), headers=HEADERS)) as client:
        yield client
    speech.tts.gate.set()
    speech.close()


def test_a_spoken_message_takes_the_images_attached_for_it(voice_client, tmp_path, codex_log):
    client = voice_client
    project = add_project(client, tmp_path)
    image = upload(client)
    with client.websocket_connect("/api/voice") as ws:
        voice = Voice(ws)
        voice.send(type="attach", attachments=[image["id"]])
        voice.talk(project_id=project["id"])
        final = voice.next("final")
        assert final["outcome"] == "dispatched" and final["attachments"] == [image["id"]]
        task = wait_for(client, final["task_id"], finished)
        assert [a["id"] for a in task["messages"][0]["attachments"]] == [image["id"]]
        assert codex_log("turn/start")[0]["params"]["input"][0]["type"] == "localImage"
        voice.talk(turn="t2", task_id=task["id"])  # the next one goes without them
        final = voice.next("final")
        assert final["outcome"] == "dispatched" and "attachments" not in final


def test_a_spoken_message_whose_images_cant_go_keeps_them(voice_client, tmp_path):
    client = voice_client
    project = add_project(client, tmp_path)
    image = upload(client)
    client.post("/api/tasks", json={"project_id": project["id"], "text": "x", "attachments": [image["id"]]})
    with client.websocket_connect("/api/voice") as ws:
        voice = Voice(ws)
        voice.send(type="attach", attachments=[image["id"]])
        voice.talk(project_id=project["id"])
        error = voice.next("voice_error")
        assert error["code"] == "dispatch_failed" and "already sent" in error["message"]
        assert voice.next("final")["outcome"] == "failed"


def test_attach_takes_only_ids(voice_client):
    with voice_client.websocket_connect("/api/voice") as ws:
        voice = Voice(ws)
        voice.send(type="attach", attachments=["../../etc/passwd"])
        assert voice.next("voice_error")["code"] == "bad_message"


def test_a_database_from_before_thumbnails_gets_them(tmp_path):
    path = tmp_path / "sennet.db"
    Database(path).close()
    with sqlite3.connect(path) as conn:  # as 0.1.0's images left it: version 11, no thumbnail column
        conn.execute("ALTER TABLE attachments DROP COLUMN thumbnail")
        conn.execute("INSERT INTO attachments (media_type, width, height, size, created_at) VALUES ('image/png', 1, 1, 9, 0)")
        conn.execute("PRAGMA user_version = 11")
    db = Database(path)
    assert db.attachment(1)["thumbnail"] is None
    assert db.one("PRAGMA user_version")["user_version"] == SCHEMA_VERSION
