"""The wake word: its pinned downloads, and serving them to paired browsers from the models directory."""
import hashlib
import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from sennet.server import create_app
from sennet.speech import models
from conftest import HEADERS

FRONTEND = Path(__file__).parent.parent / "frontend"


JARVIS = models.WAKE_WORDS["hey_jarvis"]


def install(paths, override: str | None = None, *chosen):
    """Lay the downloads out as setup does; files match their pinned sizes, not their contents."""
    for model in (models.OPENWAKEWORD, models.ORT_WEB, *(chosen or (JARVIS,))):
        folder = models.path(paths.models, model)
        folder.mkdir(parents=True, exist_ok=True)
        for file in model.files:
            (folder / file.name).write_bytes(file.name.encode().ljust(file.size, b"\0"))
    if override:
        folder = models.path(paths.models, models.OPENWAKEWORD)
        (folder / override).write_bytes(b"trained")
        (folder / "wakeword.onnx").symlink_to(override)


@pytest.fixture
def jarvis(settings):
    settings.speech.wake_word = "hey_jarvis"


def test_without_the_downloads_there_is_no_phrase(client, paths, jarvis):
    assert client.get("/api/wakeword").json() == {"phrase": None}
    assert client.get("/api/wakeword/wakeword.onnx").status_code == 404
    install(paths)
    (models.path(paths.models, models.ORT_WEB) / "ort-wasm-simd-threaded.wasm").unlink()
    assert client.get("/api/wakeword").json() == {"phrase": None}  # the models are no use without the runtime


def test_without_a_chosen_phrase_there_is_none(client, paths):
    install(paths)
    assert client.get("/api/wakeword").json() == {"phrase": None}  # opt-in: downloaded isn't chosen


def test_the_chosen_phrase_and_its_files_are_served(client, paths, jarvis):
    install(paths)
    assert client.get("/api/wakeword").json() == {"phrase": "hey jarvis"}
    assert client.get("/api/wakeword/wakeword.onnx").content.startswith(b"hey_jarvis_v0.1.onnx")
    assert client.get("/api/wakeword/melspectrogram.onnx").content.startswith(b"melspectrogram.onnx")
    runtime = client.get("/api/wakeword/ort-wasm-simd-threaded.wasm")
    assert runtime.headers["content-type"] == "application/wasm"  # or the browser can't compile it as it streams


def test_each_phrase_is_served_when_chosen(client, paths, settings):
    install(paths, None, *models.WAKE_WORDS.values())
    for name, model in models.WAKE_WORDS.items():
        settings.speech.wake_word = name
        assert client.get("/api/wakeword").json() == {"phrase": name.replace("_", " ")}
        assert client.get("/api/wakeword/wakeword.onnx").content.startswith(model.files[0].name.encode())


def test_a_linked_wakeword_onnx_replaces_the_chosen_phrase(client, paths, jarvis):
    install(paths, override="hey_sennet.onnx")
    assert client.get("/api/wakeword").json() == {"phrase": "hey sennet"}
    assert client.get("/api/wakeword/wakeword.onnx").content == b"trained"


def test_only_the_wake_word_files_are_served(client, paths, jarvis):
    install(paths)
    assert client.get("/api/wakeword/hey_jarvis_v0.1.onnx").status_code == 422
    assert b"SQLite" not in client.get("/api/wakeword/..%2F..%2Fsennet.db").content


def test_an_unpaired_browser_cannot_fetch_them(settings, paths):
    install(paths)
    with TestClient(create_app(settings, paths), headers=HEADERS) as unpaired:
        assert unpaired.get("/api/wakeword/wakeword.onnx").status_code == 401


def test_the_page_may_compile_webassembly(client):
    assert "script-src 'self' 'wasm-unsafe-eval';" in client.get("/api/state").headers["content-security-policy"]


def test_the_pinned_runtime_is_the_one_the_client_is_built_with():
    """The worker's bundled JavaScript only loads the WebAssembly of its own version."""
    package = json.loads((FRONTEND / "package.json").read_text())
    lock = json.loads((FRONTEND / "package-lock.json").read_text())
    assert package["dependencies"]["onnxruntime-web"] == models.ORT_WEB_VERSION  # exact, so a bump is deliberate
    assert lock["packages"]["node_modules/onnxruntime-web"]["version"] == models.ORT_WEB_VERSION
    assert models.ORT_WEB.id == f"onnxruntime-web-{models.ORT_WEB_VERSION}"
    assert f"onnxruntime-web@{models.ORT_WEB_VERSION}/" in models.ORT_WEB.files[0].url


def test_the_pinned_checksum_is_the_installed_packages():
    installed = FRONTEND / "node_modules" / "onnxruntime-web" / "dist" / "ort-wasm-simd-threaded.wasm"
    if not installed.is_file():
        pytest.skip("frontend/node_modules isn't installed")
    pinned = models.ORT_WEB.files[0]
    assert installed.stat().st_size == pinned.size
    assert hashlib.sha256(installed.read_bytes()).hexdigest() == pinned.sha256


@pytest.mark.parametrize("model", [models.OPENWAKEWORD, models.ORT_WEB])
def test_the_wake_word_runs_on_every_tier_without_being_needed(model):
    for device in ("cpu", "cuda"):
        assert model in models.MODELS[device].values()
    assert model.role in models.OPTIONAL


def test_the_shared_models_are_apache_and_each_phrase_is_non_commercial():
    assert models.OPENWAKEWORD.licence == "Apache-2.0"
    for model in models.WAKE_WORDS.values():
        assert model.licence == "CC-BY-NC-SA-4.0" and model.id == models.OPENWAKEWORD.id and len(model.files) == 1


def test_hey_sennet_is_offered_first_from_our_own_repo():
    name, model = next(iter(models.WAKE_WORDS.items()))
    assert name == "hey_sennet" and models.wake_phrase(Path(model.files[0].name)) == "hey sennet"
    assert model.files[0].url.startswith(models.HEY_SENNET_URL)


def lay_out(paths, *chosen):
    """Placeholder files at their pinned sizes, sparse, so the gigabyte models cost nothing."""
    for model in chosen:
        folder = models.path(paths.models, model)
        folder.mkdir(parents=True, exist_ok=True)
        for file in model.files:
            with (folder / file.name).open("wb") as out:
                out.truncate(file.size)


@pytest.fixture
def doctor(settings, paths, monkeypatch, capsys):
    """Doctor's speech lines, with every speech model but the wake word's in place."""
    from sennet import cli
    from sennet.speech import runtime

    monkeypatch.setattr(runtime, "runtime_distribution", lambda: "onnxruntime")
    monkeypatch.setattr(runtime, "resolve_device", lambda device: "cpu")
    lay_out(paths, *(m for m in models.MODELS["cpu"].values() if m.role not in ("wake", "wake_runtime")))

    def run():
        ok = cli.speech_ok(settings, paths)
        return ok, capsys.readouterr().out
    return run


def test_doctor_says_the_wake_word_is_off_until_one_is_chosen(doctor, paths):
    lay_out(paths, models.OPENWAKEWORD, models.ORT_WEB, JARVIS)
    ok, out = doctor()
    assert ok and "wake word: off; run `sennet setup` to choose one" in out


@pytest.mark.parametrize("missing", [models.OPENWAKEWORD, models.ORT_WEB, JARVIS])
def test_doctor_says_the_chosen_wake_word_is_off_without_its_downloads(doctor, paths, jarvis, missing):
    lay_out(paths, *(m for m in (models.OPENWAKEWORD, models.ORT_WEB, JARVIS) if m is not missing))
    ok, out = doctor()
    assert ok  # optional: not a problem
    assert f"off: {missing.files[0].name if missing is JARVIS else missing.id} not downloaded" in out
    lay_out(paths, missing)
    assert 'wake word: ok: "hey jarvis" (pre-trained, CC-BY-NC-SA-4.0)' in doctor()[1]


def test_doctor_flags_a_wake_word_it_doesnt_know(doctor, settings):
    settings.speech.wake_word = "hey_hal"
    ok, out = doctor()
    assert not ok and "speech.wake_word is 'hey_hal'" in out


def test_doctor_names_a_trained_wake_word(doctor, paths):
    lay_out(paths, models.OPENWAKEWORD, models.ORT_WEB)
    folder = models.path(paths.models, models.OPENWAKEWORD)
    (folder / "hey_sennet_v0.1.onnx").write_bytes(b"trained")
    (folder / "wakeword.onnx").symlink_to("hey_sennet_v0.1.onnx")
    out = doctor()[1]
    assert 'wake word: ok: "hey sennet" (trained, ' in out and "hey_sennet_v0.1.onnx" in out
