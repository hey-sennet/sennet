"""Serving the built client: build-time gzip, immutable assets, and an index that is always rechecked."""
import gzip

import pytest
from fastapi.testclient import TestClient

from sennet import server
from sennet.server import create_app

SCRIPT = b"console.log('sennet');" * 100


@pytest.fixture
def web(tmp_path, monkeypatch, settings, paths):
    built = tmp_path / "web"
    (built / "assets").mkdir(parents=True)
    (built / "index.html").write_text("<!doctype html><title>Sennet</title>")
    (built / "assets" / "index-abc.js").write_bytes(SCRIPT)
    (built / "assets" / "index-abc.js.gz").write_bytes(gzip.compress(SCRIPT))
    (built / "assets" / "font-abc.woff2").write_bytes(bytes(range(256)) * 10)
    monkeypatch.setattr(server, "WEB_DIR", built)
    return TestClient(create_app(settings, paths))


def test_assets_are_sent_gzipped_from_the_build_and_cached_for_good(web):
    response = web.get("/assets/index-abc.js", headers={"accept-encoding": "gzip"})
    assert response.status_code == 200
    assert response.headers["content-encoding"] == "gzip"
    assert response.headers["content-type"].startswith("text/javascript")
    assert response.headers["cache-control"] == server.IMMUTABLE
    assert response.content == SCRIPT  # httpx undoes the gzip


def test_a_browser_without_gzip_gets_the_plain_file(web):
    response = web.get("/assets/index-abc.js", headers={"accept-encoding": "identity"})
    assert "content-encoding" not in response.headers
    assert response.content == SCRIPT


def test_fonts_are_not_compressed_again(web):
    response = web.get("/assets/font-abc.woff2", headers={"accept-encoding": "gzip"})
    assert "content-encoding" not in response.headers
    assert response.headers["cache-control"] == server.IMMUTABLE


def test_the_app_shell_is_always_rechecked(web):
    for path in ("/", "/tasks/3"):
        response = web.get(path)
        assert response.headers["cache-control"] == "no-cache"
        assert b"<title>Sennet" in response.content


def test_an_unchanged_file_answers_not_modified(web):
    first = web.get("/assets/index-abc.js", headers={"accept-encoding": "gzip"})
    again = web.get("/assets/index-abc.js", headers={"accept-encoding": "gzip", "if-none-match": first.headers["etag"]})
    assert again.status_code == 304
    assert again.content == b""


def test_a_missing_asset_is_not_found_rather_than_the_app_shell(web):
    # An old page asking for a chunk from a build that has since been replaced.
    assert web.get("/assets/gone-123.js").status_code == 404


def test_api_json_is_compressed(client, tmp_path):
    for n in range(3):
        (tmp_path / f"repo{n}").mkdir()
        client.post("/api/projects", json={"path": str(tmp_path / f"repo{n}"), "name": f"project {n}"})
    response = client.get("/api/state", headers={"accept-encoding": "gzip"})
    assert len(response.content) > 500
    assert response.headers["content-encoding"] == "gzip"


def test_head_is_answered(web):
    assert web.head("/assets/index-abc.js").status_code == 200
    assert web.head("/").status_code == 200


def test_folders_are_listed_to_pick_a_project(client, tmp_path):
    (tmp_path / "code" / "app" / ".git").mkdir(parents=True)
    (tmp_path / "code" / "Notes").mkdir()
    (tmp_path / "code" / ".cache").mkdir()
    (tmp_path / "code" / "readme.txt").write_text("not a folder")
    listing = client.get("/api/directories", params={"path": str(tmp_path / "code")}).json()
    assert listing["path"] == str(tmp_path / "code")
    assert listing["parent"] == str(tmp_path)
    assert [(d["name"], d["repo"]) for d in listing["directories"]] == [("app", True), ("Notes", False)]
    assert client.get("/api/directories", params={"path": str(tmp_path / "code" / "app")}).json()["repo"] is True


def test_folders_start_at_home_and_a_missing_one_is_404(client, tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    assert client.get("/api/directories").json()["path"] == str(tmp_path)
    assert client.get("/api/directories", params={"path": str(tmp_path / "gone")}).status_code == 404
