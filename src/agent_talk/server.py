"""HTTP API and live event WebSocket.

Commands are plain HTTP; live updates arrive over `/api/events`. State-changing
requests must carry the `X-Agent-Talk` header: browsers cannot add custom headers
to cross-site requests without CORS permission, which this server never grants.
"""
from __future__ import annotations

import asyncio
import logging
from contextlib import asynccontextmanager
from dataclasses import asdict
from pathlib import Path
from typing import Literal

from fastapi import FastAPI, HTTPException, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from . import projects
from .agents.base import Agent
from .agents.codex import CodexAgent
from .approvals import Approvals
from .config import Paths, Settings
from .db import Database
from .events import EventBus
from .speech import Speech
from .tasks import TaskError, TaskManager
from .voice import VoiceHub, VoiceSession

log = logging.getLogger(__name__)

WEB_DIR = Path(__file__).parent / "web"
CSRF_HEADER = "x-agent-talk"


class ProjectIn(BaseModel):
    path: str
    name: str | None = None
    aliases: list[str] = []
    agent: str = "codex"


class ProjectPatch(BaseModel):
    name: str | None = None
    aliases: list[str] | None = None
    agent: str | None = None
    approval_mode: str | None = None
    access: str | None = None


class TaskIn(BaseModel):
    project_id: int
    text: str


class MessageIn(BaseModel):
    text: str


class DecisionIn(BaseModel):
    decision: Literal["approve", "approve_always", "decline", "cancel"]


def create_app(settings: Settings, paths: Paths, agents: dict[str, Agent] | None = None,
               speech: Speech | None = None) -> FastAPI:
    """`agents` and `speech` default to the real ones; tests pass fakes."""
    agents = agents if agents is not None else {"codex": CodexAgent(settings.agents.codex_command)}

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        db = Database(paths.database)
        bus = EventBus()
        approvals = Approvals(db, bus.publish)
        manager = TaskManager(db, settings, agents, approvals, bus.publish)
        app.state.db, app.state.bus, app.state.approvals, app.state.tasks = db, bus, approvals, manager
        app.state.speech, loading = _speech(settings, paths, speech)
        app.state.voice = VoiceHub(app.state.speech, manager, bus)
        app.state.voice.start()
        await manager.start()
        try:
            yield
        finally:
            if loading:
                loading.cancel()
            await app.state.voice.close()
            await manager.shutdown()
            if speech is None:  # a caller's speech is the caller's to close
                app.state.speech.close()
            db.close()

    app = FastAPI(title="Agent Talk", lifespan=lifespan, docs_url=None, redoc_url=None)

    @app.middleware("http")
    async def require_header(request: Request, call_next):
        if request.method not in ("GET", "HEAD", "OPTIONS") and request.url.path.startswith("/api/"):
            if request.headers.get(CSRF_HEADER) != "1":
                return JSONResponse({"detail": f"missing {CSRF_HEADER} header"}, status_code=403)
        return await call_next(request)

    def db(request: Request) -> Database:
        return request.app.state.db

    @app.get("/api/state")
    async def state(request: Request):
        database = db(request)
        return {"projects": database.projects(), "tasks": database.tasks(),
                "approvals": database.approvals("pending"), "rules": database.rules(),
                "settings": {"approvals": vars(settings.approvals), "tasks": vars(settings.tasks)}}

    @app.get("/api/agents")
    async def agent_status():
        results = await asyncio.gather(*(agent.status() for agent in agents.values()), return_exceptions=True)
        return {name: {**(vars(result) if not isinstance(result, Exception)
                          else {"installed": None, "logged_in": None, "detail": str(result)}),
                       "capabilities": asdict(agent.capabilities)}
                for (name, agent), result in zip(agents.items(), results)}

    @app.post("/api/projects", status_code=201)
    async def add_project(body: ProjectIn, request: Request):
        try:
            project = projects.add(db(request), body.path, body.name, body.aliases, body.agent)
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

    @app.post("/api/tasks", status_code=201)
    async def create_task(body: TaskIn, request: Request):
        try:
            return await request.app.state.tasks.create(body.project_id, body.text)
        except TaskError as error:
            raise HTTPException(400, str(error))

    @app.get("/api/tasks/{task_id}")
    async def get_task(task_id: int, request: Request):
        snapshot = request.app.state.tasks.snapshot(task_id)
        if snapshot is None:
            raise HTTPException(404, "no such task")
        return snapshot

    @app.post("/api/tasks/{task_id}/messages")
    async def follow_up(task_id: int, body: MessageIn, request: Request):
        try:
            return await request.app.state.tasks.follow_up(task_id, body.text)
        except TaskError as error:
            raise HTTPException(404 if "no such" in str(error) else 400, str(error))

    @app.post("/api/tasks/{task_id}/cancel")
    async def cancel_task(task_id: int, request: Request):
        try:
            return await request.app.state.tasks.cancel(task_id)
        except TaskError as error:
            raise HTTPException(404, str(error))

    @app.post("/api/approvals/{approval_id}")
    async def decide(approval_id: int, body: DecisionIn, request: Request):
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
        if not _same_origin(websocket):
            await websocket.close(code=1008)
            return
        await websocket.accept()
        bus: EventBus = websocket.app.state.bus
        subscriber = bus.subscribe()

        async def pump():
            while (event := await subscriber.next()) is not None:
                await websocket.send_json(event)
            await websocket.close(code=1013)  # fell behind; the client reconnects and reloads

        async def listen():
            while (await websocket.receive())["type"] != "websocket.disconnect":
                pass

        jobs = {asyncio.ensure_future(pump()), asyncio.ensure_future(listen())}
        try:
            await asyncio.wait(jobs, return_when=asyncio.FIRST_COMPLETED)
        except WebSocketDisconnect:
            pass
        finally:
            for job in jobs:
                job.cancel()
            await asyncio.gather(*jobs, return_exceptions=True)
            bus.unsubscribe(subscriber)

    @app.websocket("/api/voice")
    async def voice(websocket: WebSocket):
        if not _same_origin(websocket):
            await websocket.close(code=1008)
            return
        await websocket.accept()
        hub: VoiceHub = websocket.app.state.voice
        session = VoiceSession(websocket, websocket.app.state.speech, hub)
        hub.add(session)
        try:
            await session.run()
        except WebSocketDisconnect:
            pass
        finally:
            hub.remove(session)

    if WEB_DIR.is_dir():
        app.mount("/assets", StaticFiles(directory=WEB_DIR / "assets"), name="assets")

        @app.get("/{path:path}", include_in_schema=False)
        async def web(path: str):
            file = (WEB_DIR / path).resolve()
            if path and file.is_file() and file.is_relative_to(WEB_DIR.resolve()):
                return FileResponse(file)
            return FileResponse(WEB_DIR / "index.html")

    return app


def _speech(settings: Settings, paths: Paths, speech: Speech | None) -> tuple[Speech, asyncio.Task | None]:
    if speech is not None:
        return speech, None
    if not settings.speech.enabled:
        return Speech.unavailable("speech is turned off in config.toml"), None
    speech = Speech()
    config = settings.speech
    return speech, asyncio.ensure_future(speech.load(paths.models, config.device, config.threads, config.voice))


def _same_origin(websocket: WebSocket) -> bool:
    """Browsers send Origin on WebSocket upgrades; a cross-site page must not connect."""
    origin = websocket.headers.get("origin")
    return not origin or origin.split("://", 1)[-1] == websocket.headers.get("host")
