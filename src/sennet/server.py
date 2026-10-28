"""HTTP API and live event WebSocket.

Commands are plain HTTP; live updates arrive over `/api/events`. State-changing
requests must carry the `X-Sennet` header: browsers cannot add custom headers
to cross-site requests without CORS permission, which this server never grants.

Everything under `/api/` needs a paired device's cookie (access.py), loopback
included: behind a proxy on this machine every request comes from 127.0.0.1. Two are
open: `POST /api/pair`, which redeems a pairing code and is throttled, and
`GET /api/health`, which says only that this is Sennet and its version. A
WebSocket from an unpaired device, or one revoked while it is open, is closed with
code 4401. The app shell is served to anyone, so an unpaired browser can pair.

Images go up on their own (`POST /api/attachments`, the image as the body) before the message
that names them, so a spoken turn can carry them too (attachments.py). They are served back
only to paired devices, like everything else under `/api/`.

A page says on its events socket while it is showing (`{"type": "showing", "showing": true}`),
so its device isn't sent push notifications meanwhile (push.py).

`/mcp/<secret>` is for agent CLIs on this machine, not browsers: it answers only
a direct loopback request, without an Origin or any proxy's forwarding header, and
only a live agent session's secret (agents.callbacks). A reverse proxy in front of
Sennet makes every request look local, so the forwarding header, not the client
address, is what keeps a proxied (or public) deployment from exposing the callback.
"""
from __future__ import annotations

import asyncio
import json
import logging
import mimetypes
import os
from contextlib import asynccontextmanager
from dataclasses import asdict
from pathlib import Path
from typing import Literal
from urllib.parse import urlsplit

import segno
from fastapi import FastAPI, HTTPException, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, JSONResponse, Response
from starlette.middleware.gzip import GZipMiddleware
from pydantic import BaseModel

from . import __version__, access, projects
from .attachments import MAX_BYTES, THUMBNAIL_MAX_BYTES, AttachmentError, Attachments
from .push import KINDS, Push, answerable as push_answerable, keys_valid as push_keys_valid
from .aio import settle
from .agents import registry
from .agents.base import Agent
from .agents.callbacks import CallbackServer
from .approvals import Approvals
from .config import Paths, Settings
from .db import Database
from .events import EventBus, batches
from .speech import Speech
from .speech import models as speech_models
from . import search
from .tasks import TaskError, TaskManager
from .voice import VoiceHub, VoiceSession

log = logging.getLogger(__name__)

WEB_DIR = Path(__file__).parent / "web"
CSRF_HEADER = "x-sennet"
UNPAIRED = 4401                 # WebSocket close code: pair this device, don't just reconnect
EARLIER = 10                    # finished tasks the page starts with; "Show more" asks for the rest
SWEEP_S = 30                    # how often open sockets are checked for devices revoked elsewhere
LOOPBACK = ("127.0.0.1", "::1", "localhost")
OPEN = ("/api/pair", "/api/health")    # the API paths an unpaired device may use
# A genuine on-machine agent calls back over loopback; a reverse proxy (tailscale serve, nginx,
# Funnel) always stamps one of these, and behind one every request looks like it came from 127.0.0.1.
FORWARDED = ("x-forwarded-for", "x-forwarded-host", "x-forwarded-proto", "forwarded", "x-real-ip")
IMMUTABLE = "public, max-age=31536000, immutable"  # built assets have hashed names
mimetypes.add_type("application/octet-stream", ".onnx")  # the wake word's models (not text/plain)


class ProjectIn(BaseModel):
    path: str
    name: str | None = None
    agent: str = registry.DEFAULT


class ProjectPatch(BaseModel):
    name: str | None = None
    agent: str | None = None
    approval_mode: str | None = None
    access: str | None = None
    project_config: bool | None = None
    model: str | None = None
    effort: str | None = None
    worktrees: bool | None = None


class TaskIn(BaseModel):
    project_id: int
    text: str = ""
    model: str | None = None
    effort: str | None = None
    attachments: list[int] = []        # images uploaded for it; the text may be empty with some


class ModelIn(BaseModel):
    model: str | None = None
    effort: str | None = None


class MessageIn(BaseModel):
    text: str = ""
    attachments: list[int] = []


class PrewarmIn(BaseModel):
    task_id: int | None = None
    project_id: int | None = None
    model: str | None = None
    effort: str | None = None


class PairIn(BaseModel):
    code: str


class DeviceIn(BaseModel):
    name: str


class PushKeys(BaseModel):
    p256dh: str
    auth: str


class PushIn(BaseModel):
    """A browser's PushSubscription as JSON, and what should notify; `kinds` unset keeps the device's choice."""
    endpoint: str
    keys: PushKeys
    kinds: list[Literal["approval", "finished", "failed"]] | None = None


class DecisionIn(BaseModel):
    decision: Literal["approve", "approve_always", "decline", "cancel"]
    via: Literal["card", "notification"] = "card"


def create_app(settings: Settings, paths: Paths, agents: dict[str, Agent] | None = None,
               speech: Speech | None = None) -> FastAPI:
    """`agents` and `speech` default to the real ones; tests pass fakes."""
    callbacks = CallbackServer(settings.server.local_url)
    agents = agents if agents is not None else registry.build(settings.agents.commands, callbacks)
    throttle = access.Throttle()
    sockets: dict[int, set[WebSocket]] = {}  # device id → its open WebSockets

    async def disconnect(device_id: int):
        for websocket in list(sockets.pop(device_id, ())):
            try:
                await websocket.close(code=UNPAIRED)
            except RuntimeError:
                pass  # already closing

    async def sweep(db: Database):
        """Close the sockets of devices revoked from the terminal, which the daemon isn't told about."""
        while True:
            await asyncio.sleep(SWEEP_S)
            for device_id in [d for d in sockets if db.device(d) is None]:
                await disconnect(device_id)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        db = Database(paths.database)
        bus = EventBus()
        approvals = Approvals(db, bus.publish)
        attachments = Attachments(db, paths.data_dir / "attachments")
        await asyncio.to_thread(attachments.sweep)
        manager = TaskManager(db, settings, agents, approvals, bus.publish, attachments, paths.data_dir / "worktrees")
        app.state.db, app.state.bus, app.state.approvals, app.state.tasks = db, bus, approvals, manager
        app.state.attachments = attachments
        app.state.speech, loading = _speech(settings, paths, speech)
        app.state.voice = VoiceHub(app.state.speech, manager, bus)
        app.state.voice.start()
        app.state.push = Push(db, bus, paths.data_dir / "push-key.pem")
        app.state.push.start()
        await manager.start()
        sweeping = asyncio.ensure_future(sweep(db))
        try:
            yield
        finally:
            sweeping.cancel()
            if loading:
                loading.cancel()
            await app.state.voice.close()
            await app.state.push.close()
            await manager.shutdown()
            if speech is None:  # a caller's speech is the caller's to close
                app.state.speech.close()
            db.close()

    app = FastAPI(title="Sennet", lifespan=lifespan, docs_url=None, redoc_url=None)
    app.state.callbacks = callbacks
    # JSON and anything built without a .gz beside it; fonts and audio are left alone.
    app.add_middleware(GZipMiddleware, minimum_size=500)

    @app.middleware("http")
    async def guard(request: Request, call_next):
        if request.url.path.startswith("/api/"):
            if request.method not in ("GET", "HEAD", "OPTIONS") and request.headers.get(CSRF_HEADER) != "1":
                return _secured(JSONResponse({"detail": f"missing {CSRF_HEADER} header"}, status_code=403), request)
            if request.url.path not in OPEN:
                device = access.device_for(request.app.state.db, request.cookies.get(access.COOKIE))
                if device is None:
                    return _secured(JSONResponse({"detail": "this device isn't paired"}, status_code=401), request)
                request.state.device = device
        return _secured(await call_next(request), request)

    async def admit(websocket: WebSocket) -> dict | None:
        """Accept a WebSocket from a paired device on this site; close any other."""
        if not _same_origin(websocket):
            await websocket.close(code=1008)
            return None
        device = access.device_for(websocket.app.state.db, websocket.cookies.get(access.COOKIE))
        await websocket.accept()  # before closing, so the browser sees why
        if device is None:
            await websocket.close(code=UNPAIRED)
            return None
        sockets.setdefault(device["id"], set()).add(websocket)
        return device

    def release(websocket: WebSocket, device: dict):
        if (open_ := sockets.get(device["id"])) is not None:
            open_.discard(websocket)
            if not open_:
                del sockets[device["id"]]

    def db(request: Request) -> Database:
        return request.app.state.db

    @app.get("/api/health")
    async def health():
        """For the service and `doctor`: is this Sennet, and which version."""
        return {"app": "sennet", "version": __version__}

    @app.post("/api/pair")
    async def pair(body: PairIn, request: Request):
        """Redeem a pairing code: this browser becomes a paired device, with its token in a cookie."""
        if wait := throttle.wait():
            raise HTTPException(429, "too many wrong codes; try again in a minute",
                                headers={"Retry-After": str(int(wait) + 1)})
        paired = access.redeem(db(request), body.code, request.headers.get("user-agent", ""))
        if paired is None:
            throttle.failed()
            raise HTTPException(400, "that code didn't work: codes work once, and only for a few minutes")
        device, token = paired
        await request.app.state.bus.publish({"type": "devices", "paired": device})
        response = JSONResponse(device)
        https = request.url.scheme == "https" or (request.headers.get("origin") or "").startswith("https://")
        response.set_cookie(access.COOKIE, token, max_age=access.COOKIE_MAX_AGE_S, path="/", httponly=True,
                            samesite="strict", secure=https)
        return response

    @app.get("/api/devices")
    async def devices(request: Request):
        current = request.state.device["id"]
        return [{**device, "current": device["id"] == current} for device in db(request).devices()]

    @app.post("/api/devices/pairing", status_code=201)
    async def new_pairing(request: Request):
        """A code for pairing another device, as a link (with its QR code) and a code to type."""
        pairing = access.new_pairing(db(request), request.state.device["name"], access.DEVICE_TTL_S)
        base = settings.server.public_url or str(request.base_url)
        link = pairing.link(base)
        return {"link": link, "code": pairing.typed_code, "expires_at": pairing.expires_at,
                "qr": segno.make(link, error="m").svg_data_uri(dark="#000", light="#fff", border=2),
                "local_only": request.url.hostname in LOOPBACK and not settings.server.public_url}

    @app.patch("/api/devices/{device_id}")
    async def rename_device(device_id: int, body: DeviceIn, request: Request):
        if db(request).device(device_id) is None:
            raise HTTPException(404, "no such device")
        try:
            device = access.rename(db(request), device_id, body.name)
        except ValueError as error:
            raise HTTPException(400, str(error))
        await request.app.state.bus.publish({"type": "devices", "paired": None})
        return device

    @app.delete("/api/devices/{device_id}", status_code=204)
    async def revoke_device(device_id: int, request: Request):
        """Unpair a device, closing its sockets; it has to pair again to get back in."""
        if db(request).device(device_id) is None:
            raise HTTPException(404, "no such device")
        db(request).remove_device(device_id)
        await disconnect(device_id)
        await request.app.state.bus.publish({"type": "devices", "paired": None})
        response = Response(status_code=204)
        if device_id == request.state.device["id"]:
            response.delete_cookie(access.COOKIE, path="/")
        return response

    @app.get("/api/push")
    async def push_state(request: Request):
        """The server's key to subscribe with, and this device's subscription: where it goes and what notifies."""
        push: Push = request.app.state.push
        subscription = db(request).push(request.state.device["id"])
        return {"key": push.vapid.public_key,
                "subscription": subscription and {"endpoint": subscription["endpoint"], "kinds": subscription["kinds"]}}

    @app.put("/api/push")
    async def subscribe_push(body: PushIn, request: Request):
        """Notify this device at the browser's push service, for the kinds given (all, the first time)."""
        if urlsplit(body.endpoint).scheme != "https":
            raise HTTPException(400, "a push endpoint must be an https address")
        if not push_keys_valid(body.keys.p256dh, body.keys.auth):
            raise HTTPException(400, "that isn't a browser's push subscription: its keys are wrong")
        device_id = request.state.device["id"]
        kinds = body.kinds
        if kinds is None:
            kinds = (db(request).push(device_id) or {"kinds": list(KINDS)})["kinds"]
        db(request).set_push(device_id, body.endpoint, body.keys.p256dh, body.keys.auth,
                             [k for k in KINDS if k in kinds])
        return {"endpoint": body.endpoint, "kinds": db(request).push(device_id)["kinds"]}

    @app.delete("/api/push", status_code=204)
    async def unsubscribe_push(request: Request):
        db(request).remove_push(request.state.device["id"])
        return Response(status_code=204)

    @app.post("/api/push/test", status_code=204)
    async def test_push(request: Request):
        """A notification to this device now, even with the page showing. 204, not 202: the client
        reads any other answer's body as JSON."""
        if not request.app.state.push.test(request.state.device["id"]):
            raise HTTPException(404, "notifications aren't on for this device")
        return Response(status_code=204)

    @app.get("/api/state")
    async def state(request: Request):
        database = db(request)
        limits = request.app.state.tasks.limits
        limits.refresh()  # those older than a few minutes; the answers arrive as `limits` events
        tasks, more = database.current_tasks(EARLIER)
        return {"version": __version__, "projects": database.projects(), "tasks": tasks, "more_tasks": more,
                "approvals": [request.app.state.approvals.present(a) for a in database.approvals("pending")],
                "rules": database.rules(), "limits": limits.snapshot(),
                "settings": {"approvals": vars(settings.approvals), "tasks": vars(settings.tasks)}}

    @app.get("/api/limits")
    async def limits(request: Request):
        """The agents' usage limits as last known, asking again for any a few minutes old."""
        request.app.state.tasks.limits.refresh()
        return request.app.state.tasks.limits.snapshot()

    @app.get("/api/agents")
    async def agent_status():
        results = await asyncio.gather(*(agent.status() for agent in agents.values()), return_exceptions=True)
        listed = await asyncio.gather(*(agent.models() for agent in agents.values()), return_exceptions=True)
        return {name: {**(vars(result) if not isinstance(result, Exception)
                          else {"installed": None, "logged_in": None, "detail": str(result)}),
                       "title": agent.title, "capabilities": asdict(agent.capabilities),
                       "models": [asdict(m) for m in models] if not isinstance(models, Exception) else []}
                for (name, agent), result, models in zip(agents.items(), results, listed)}

    def wake_files() -> dict[str, Path] | None:
        """What the client's wake word fetches, once setup has downloaded it."""
        if (model := speech_models.wake_model(paths.models, settings.speech.wake_word)) is None:
            return None
        folder = speech_models.path(paths.models, speech_models.OPENWAKEWORD)
        return {"melspectrogram.onnx": folder / "melspectrogram.onnx",
                "embedding_model.onnx": folder / "embedding_model.onnx",
                "wakeword.onnx": model,
                "ort-wasm-simd-threaded.wasm": speech_models.path(paths.models, speech_models.ORT_WEB)
                / "ort-wasm-simd-threaded.wasm"}

    @app.get("/api/wakeword")
    async def wakeword():
        """The phrase the client's wake word listens for, from the model's file name; null without one."""
        if (files := wake_files()) is None:
            return {"phrase": None}
        return {"phrase": speech_models.wake_phrase(files["wakeword.onnx"])}

    @app.get("/api/wakeword/{name}")
    async def wakeword_file(name: Literal["melspectrogram.onnx", "embedding_model.onnx", "wakeword.onnx",
                                          "ort-wasm-simd-threaded.wasm"], request: Request):
        """openWakeWord's models and the ONNX runtime they run on in the browser, from the models
        directory: setup downloads them, so they're never in the wheel."""
        if (files := wake_files()) is None:
            raise HTTPException(404, "the wake word isn't downloaded; run `sennet setup`")
        return _built(files[name], request, "no-cache")

    async def check_model(agent: Agent | None, model: str | None, effort: str | None, inherited: str | None = None):
        try:
            await registry.check_model(agent, model, effort, inherited)
        except registry.ModelError as error:
            raise HTTPException(400, str(error))

    @app.get("/api/directories")
    async def directories(path: str | None = None):
        """Folders to pick a project from: paired devices can already run agents here, so
        showing them folder names asks no new trust."""
        try:
            return await asyncio.to_thread(projects.directories, path)
        except projects.ProjectError as error:
            raise HTTPException(404, str(error))

    @app.post("/api/projects", status_code=201)
    async def add_project(body: ProjectIn, request: Request):
        try:
            project = projects.add(db(request), body.path, body.name, body.agent)
        except projects.ProjectError as error:
            raise HTTPException(400, str(error))
        await request.app.state.bus.publish({"type": "project", "project": project})
        return project

    @app.patch("/api/projects/{project_id}")
    async def update_project(project_id: int, body: ProjectPatch, request: Request):
        fields = body.model_dump(exclude_unset=True)
        current = db(request).project(project_id)
        agent = agents.get(fields.get("agent") or (current or {}).get("agent"))
        if agent is not None:
            if fields.get("approval_mode") and fields["approval_mode"] not in agent.capabilities.approval_modes:
                raise HTTPException(400, f"{agent.name} doesn't support the {fields['approval_mode']!r} approval mode")
            if fields.get("access") and fields["access"] not in agent.capabilities.access_levels:
                raise HTTPException(400, f"{agent.name} doesn't support {fields['access']!r} access")
            if fields.get("project_config") and not agent.capabilities.project_config:
                raise HTTPException(400, f"{agent.name} has no project configuration to opt into")
            if "model" in fields or "effort" in fields:
                same = current and fields.get("agent", current["agent"]) == current["agent"]
                await check_model(agent, fields.get("model"), fields.get("effort"),
                                  current["model"] if same and "model" not in fields else None)
        try:
            project = projects.update(db(request), project_id, **fields)
        except projects.ProjectError as error:
            raise HTTPException(404 if "no such" in str(error) else 400, str(error))
        await request.app.state.bus.publish({"type": "project", "project": project})
        return project

    @app.delete("/api/projects/{project_id}", status_code=204)
    async def remove_project(project_id: int, request: Request):
        database = db(request)
        active = database.one("SELECT id FROM tasks WHERE project_id = ? AND status IN "
                              "('queued', 'running', 'awaiting_approval')", project_id)
        if active:
            raise HTTPException(409, "project has active tasks; cancel them first")
        database.remove_project(project_id)
        await request.app.state.bus.publish({"type": "project_removed", "project_id": project_id})

    @app.post("/api/attachments", status_code=201)
    async def upload(request: Request):
        """An image for a message to come, as the request's body: its id goes in the message."""
        data = await body(request, MAX_BYTES)
        try:
            attachment = await asyncio.to_thread(request.app.state.attachments.add, data,
                                                 request.state.device["id"])
        except AttachmentError as error:
            raise HTTPException(400, str(error))
        return _shown(attachment)

    async def body(request: Request, limit: int) -> bytes:
        """The request's body, refused once it passes `limit` rather than read to its end."""
        data = bytearray()
        async for chunk in request.stream():
            data += chunk
            if len(data) > limit:
                raise HTTPException(413, f"that image is over {limit // 1024} KB" if limit < 1024 * 1024
                                    else f"that image is over {limit // (1024 * 1024)} MB")
        return bytes(data)

    @app.put("/api/attachments/{attachment_id}/thumbnail")
    async def upload_thumbnail(attachment_id: int, request: Request):
        """A small copy of an unsent image, for the timeline to show instead of the whole image."""
        found = db(request).attachment(attachment_id)
        if found is None:
            raise HTTPException(404, "no such image")
        data = await body(request, THUMBNAIL_MAX_BYTES)
        try:
            updated = await asyncio.to_thread(request.app.state.attachments.add_thumbnail, found, data)
        except AttachmentError as error:
            raise HTTPException(409 if "already sent" in str(error) else 400, str(error))
        return _shown(updated)

    @app.get("/api/attachments/{attachment_id}")
    async def attachment(attachment_id: int, request: Request):
        found = db(request).attachment(attachment_id)
        path = found and request.app.state.attachments.path(found)
        if found is None or not path.is_file():
            raise HTTPException(404, "no such image")
        return _image(path, found["media_type"])

    @app.get("/api/attachments/{attachment_id}/thumbnail")
    async def thumbnail(attachment_id: int, request: Request):
        found = db(request).attachment(attachment_id)
        path = found and request.app.state.attachments.thumbnail_path(found)
        if path is None or not path.is_file():
            raise HTTPException(404, "that image has no thumbnail")
        return _image(path, found["thumbnail"])

    @app.delete("/api/attachments/{attachment_id}", status_code=204)
    async def remove_attachment(attachment_id: int, request: Request):
        """An image taken out before its message was sent; one already sent stays with its message."""
        found = db(request).attachment(attachment_id)
        if found is None:
            raise HTTPException(404, "no such image")
        if found["message_id"] is not None:
            raise HTTPException(409, "that image was sent with a message")
        db(request).remove_attachment(attachment_id)
        request.app.state.attachments.remove([found])
        return Response(status_code=204)

    @app.post("/api/tasks", status_code=201)
    async def create_task(body: TaskIn, request: Request):
        try:
            task = await request.app.state.tasks.create(body.project_id, body.text, body.model, body.effort,
                                                        body.attachments)
        except TaskError as error:
            raise HTTPException(400, str(error))
        await request.app.state.voice.typed(request.state.device["id"], task["id"])
        return task

    @app.get("/api/tasks")
    async def earlier_tasks(before: float, before_id: int, request: Request, limit: int = 20):
        """Finished tasks older than the one given, for the task list's "Show more"."""
        tasks, more = db(request).earlier_tasks(before, before_id, max(1, min(limit, 100)))
        return {"tasks": tasks, "more": more}

    @app.get("/api/search")
    async def search_tasks(q: str, request: Request):
        """Tasks by their titles and what was said in them, best first."""
        return search.search(db(request), q[:200])

    @app.delete("/api/tasks/{task_id}", status_code=204)
    async def delete_task(task_id: int, request: Request, worktree: Literal["keep", "remove", "delete"] = "keep"):
        """A finished task, from Sennet only: the agent keeps its own copy of the conversation.
        Its worktree is kept, removed, or removed with its branch (`delete`)."""
        try:
            await request.app.state.tasks.delete(task_id, worktree)
        except TaskError as error:
            raise _task_error(error)
        return Response(status_code=204)

    @app.get("/api/tasks/{task_id}/changes")
    async def task_changes(task_id: int, request: Request):
        """What the task changed, file by file, and whether it's committed (changes.py)."""
        try:
            return await request.app.state.tasks.changes(task_id)
        except TaskError as error:
            raise _task_error(error)

    @app.get("/api/tasks/{task_id}/changes/diff")
    async def task_diff(task_id: int, path: str, request: Request):
        """One of those files' diffs; only a file the task changed."""
        try:
            return await request.app.state.tasks.changes(task_id, path)
        except TaskError as error:
            raise _task_error(error)

    @app.get("/api/tasks/{task_id}/worktree")
    async def task_worktree(task_id: int, request: Request):
        """The task's own worktree and branch, as they are now."""
        try:
            return await request.app.state.tasks.worktree(task_id)
        except TaskError as error:
            raise _task_error(error)

    @app.post("/api/tasks/{task_id}/worktree/merge")
    async def merge_worktree(task_id: int, request: Request):
        """Merge the finished task's branch into the branch it came from. One that would conflict
        isn't started: `merged` is false and `conflicts` names the files."""
        try:
            return await request.app.state.tasks.merge(task_id)
        except TaskError as error:
            raise _task_error(error)

    @app.delete("/api/tasks/{task_id}/worktree")
    async def remove_worktree(task_id: int, request: Request, branch: Literal["keep", "delete"] = "keep"):
        """Remove the finished task's worktree, and its branch if `branch=delete`."""
        try:
            return await request.app.state.tasks.remove_worktree(task_id, branch == "delete")
        except TaskError as error:
            raise _task_error(error)

    @app.get("/api/tasks/{task_id}")
    async def get_task(task_id: int, request: Request):
        snapshot = request.app.state.tasks.snapshot(task_id)
        if snapshot is None:
            raise HTTPException(404, "no such task")
        return snapshot

    @app.patch("/api/tasks/{task_id}")
    async def choose_model(task_id: int, body: ModelIn, request: Request):
        """The task's own model and effort, from its next turn; null for both follows the project."""
        task = db(request).task(task_id)
        if task is None:
            raise HTTPException(404, "no such task")
        project = db(request).project(task["project_id"])
        inherited = project["model"] if project and project["agent"] == task["agent"] else None
        await check_model(agents.get(task["agent"]), body.model, body.effort, inherited)
        return await request.app.state.tasks.choose_model(task_id, body.model, body.effort)

    @app.post("/api/tasks/{task_id}/messages")
    async def follow_up(task_id: int, body: MessageIn, request: Request):
        try:
            task = await request.app.state.tasks.follow_up(task_id, body.text, body.attachments)
        except TaskError as error:
            raise HTTPException(404 if "no such" in str(error) else 400, str(error))
        await request.app.state.voice.typed(request.state.device["id"], task_id)
        return task

    @app.post("/api/prewarm", status_code=204)
    async def prewarm(body: PrewarmIn, request: Request):
        """A message is likely coming (someone started typing): start the agent early."""
        request.app.state.tasks.prewarm(body.task_id, body.project_id, body.model, body.effort)
        return Response(status_code=204)

    @app.post("/api/tasks/{task_id}/cancel")
    async def cancel_task(task_id: int, request: Request):
        try:
            return await request.app.state.tasks.cancel(task_id)
        except TaskError as error:
            raise HTTPException(404, str(error))

    @app.post("/api/approvals/{approval_id}")
    async def decide(approval_id: int, body: DecisionIn, request: Request):
        if body.via == "notification":
            # The service worker offers only these, and only on what isn't destructive; held here too.
            approval = db(request).approval(approval_id)
            if body.decision not in ("approve", "decline"):
                raise HTTPException(400, "a notification can only approve or decline")
            if approval and not push_answerable(approval):
                raise HTTPException(400, "a destructive request is answered on its card, not from a notification")
        try:
            return await request.app.state.approvals.decide(approval_id, body.decision)
        except LookupError as error:
            raise HTTPException(404, str(error))
        except ValueError as error:
            raise HTTPException(400, str(error))

    @app.delete("/api/rules/{rule_id}", status_code=204)
    async def remove_rule(rule_id: int, request: Request):
        db(request).remove_rule(rule_id)
        await request.app.state.bus.publish({"type": "rules", "rules": db(request).rules()})

    @app.websocket("/api/events")
    async def events(websocket: WebSocket):
        if (device := await admit(websocket)) is None:
            return
        bus: EventBus = websocket.app.state.bus
        push: Push = websocket.app.state.push
        subscriber = bus.subscribe()

        async def pump():
            async for batch in batches(subscriber):
                for event in batch:
                    await websocket.send_json(event)
            await websocket.close(code=1013)  # fell behind; the client reconnects and reloads

        async def listen():
            while (message := await websocket.receive())["type"] != "websocket.disconnect":
                try:
                    said = json.loads(message.get("text") or "")
                except ValueError:
                    continue
                if isinstance(said, dict) and said.get("type") == "showing":
                    push.seen(websocket, device["id"], bool(said.get("showing")))

        jobs = {asyncio.ensure_future(pump()), asyncio.ensure_future(listen())}
        try:
            await asyncio.wait(jobs, return_when=asyncio.FIRST_COMPLETED)
        except WebSocketDisconnect:
            pass
        finally:
            for job in jobs:
                job.cancel()
            await settle(jobs)
            bus.unsubscribe(subscriber)
            push.gone(websocket)
            release(websocket, device)

    @app.websocket("/api/voice")
    async def voice(websocket: WebSocket):
        if (device := await admit(websocket)) is None:
            return
        hub: VoiceHub = websocket.app.state.voice
        session = VoiceSession(websocket, websocket.app.state.speech, hub, device["id"])
        hub.add(session)
        try:
            await session.run()
        except WebSocketDisconnect:
            pass
        finally:
            hub.remove(session)
            release(websocket, device)

    @app.post("/mcp/{secret}", include_in_schema=False)
    async def mcp(secret: str, request: Request):
        # client.host is 127.0.0.1 for a local agent *and* for anything a proxy forwards on, so it
        # can't tell them apart on its own: a forwarding header means a proxy relayed this, which an
        # agent's direct loopback call never does. Without this, a proxied (even public) deployment
        # would expose the callback to the whole network, guarded only by the per-session secret.
        local = request.client and request.client.host in ("127.0.0.1", "::1", settings.server.host)
        proxied = any(header in request.headers for header in FORWARDED)
        if not local or proxied or request.headers.get("origin"):
            raise HTTPException(403, "only agents on this machine may call back")
        try:
            message = await request.json()
        except ValueError:
            raise HTTPException(400, "not JSON")
        try:
            response = await callbacks.handle(secret, message)
        except LookupError:
            raise HTTPException(404, "no such session")
        return JSONResponse(response) if response is not None else Response(status_code=202)

    if WEB_DIR.is_dir():
        @app.api_route("/{path:path}", methods=["GET", "HEAD"], include_in_schema=False)
        async def web(path: str, request: Request):
            file = (WEB_DIR / path).resolve()
            if path and file.is_file() and file.is_relative_to(WEB_DIR.resolve()):
                return _built(file, request, IMMUTABLE if path.startswith("assets/") else "no-cache")
            if path.startswith("assets/"):
                raise HTTPException(404)  # a chunk from an older build: not the app shell under its name
            # no-cache: a phone must ask each time, or it keeps an old index.html and so an old build.
            return _built(WEB_DIR / "index.html", request, "no-cache")

    return app


def _task_error(error: TaskError) -> HTTPException:
    """No such task is 404; one still working, or a worktree git won't change, is 409."""
    return HTTPException(404 if str(error).startswith("no such") else 409, str(error))


def _speech(settings: Settings, paths: Paths, speech: Speech | None) -> tuple[Speech, asyncio.Task | None]:
    if speech is not None:
        return speech, None
    if not settings.speech.enabled:
        return Speech.unavailable("speech is turned off in config.toml"), None
    speech = Speech()
    config = settings.speech
    return speech, asyncio.ensure_future(speech.load(paths.models, config.device, config.threads, config.voice))


def _built(file: Path, request: Request, cache: str) -> Response:
    """A file of the built client, as the .gz the build left beside it when the browser takes gzip."""
    headers = {"Cache-Control": cache, "Vary": "Accept-Encoding"}
    gzipped = file.with_name(file.name + ".gz")
    media_type = None
    if "gzip" in request.headers.get("accept-encoding", "") and gzipped.is_file():
        file, media_type = gzipped, mimetypes.guess_type(file.name)[0] or "application/octet-stream"
    response = FileResponse(file, media_type=media_type, stat_result=os.stat(file),
                            headers={**headers, "Content-Encoding": "gzip"} if media_type else headers)
    etag = response.headers["etag"]
    if etag in [tag.strip().removeprefix("W/") for tag in request.headers.get("if-none-match", "").split(",")]:
        return Response(status_code=304, headers={**headers, "ETag": etag})
    return response


def _shown(attachment: dict) -> dict:
    """An image as a client sees it: what `task_messages` lists for each of a message's images."""
    return {key: attachment[key] for key in ("id", "media_type", "width", "height", "thumbnail")}


def _image(path: Path, media_type: str) -> Response:
    # An image never changes under its url (a thumbnail is set before its image is sent, and the
    # client asks for it only after). Private: it is only for paired devices.
    return FileResponse(path, media_type=media_type, headers={"Cache-Control": "private, max-age=31536000, immutable"})


def _secured(response: Response, request: Request) -> Response:
    """Headers that keep the page to itself: agent output is shown as markdown, and a paired device's
    cookie is a shell on this machine."""
    host = request.headers.get("host", "")
    response.headers["Content-Security-Policy"] = (
        "default-src 'self'; script-src 'self' 'wasm-unsafe-eval'; style-src 'self' 'unsafe-inline'; img-src 'self' data: blob:; "
        f"media-src 'self' data: blob:; font-src 'self' data:; connect-src 'self' ws://{host} wss://{host}; "
        "worker-src 'self' blob:; object-src 'none'; base-uri 'none'; form-action 'self'; frame-ancestors 'none'")
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["Referrer-Policy"] = "no-referrer"
    return response


def _same_origin(websocket: WebSocket) -> bool:
    """Browsers send Origin on WebSocket upgrades; a cross-site page must not connect."""
    origin = websocket.headers.get("origin")
    return not origin or origin.split("://", 1)[-1] == websocket.headers.get("host")
