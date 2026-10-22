"""The web API and dashboard."""

from __future__ import annotations

import asyncio
import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, Response
from pydantic import BaseModel

from . import mqtt
from .config import Config
from .events import Event, EventLog
from .ha import HomeAssistant
from .modules import load_modules, module_status
from .security import SecurityManager

log = logging.getLogger(__name__)
STATIC = Path(__file__).parent / "static"


class ModeRequest(BaseModel):
    mode: str


class ServiceRequest(BaseModel):
    domain: str
    service: str
    entity_id: str


# Services the dashboard may call. Keeps the API from being a raw pass-through.
ALLOWED_SERVICES = {
    ("light", "turn_on"), ("light", "turn_off"), ("light", "toggle"),
    ("switch", "turn_on"), ("switch", "turn_off"), ("switch", "toggle"),
    ("lock", "lock"), ("lock", "unlock"),
    ("cover", "open_cover"), ("cover", "close_cover"),
}


async def _watch_states(ha: HomeAssistant, security: SecurityManager, watched: set[str]) -> None:
    async for change in ha.state_changes():
        entity = change.get("entity_id", "")
        if watched and entity not in watched:
            continue
        old = (change.get("old_state") or {}).get("state")
        new_state = change.get("new_state") or {}
        new = new_state.get("state")
        if old == new:
            continue
        name = new_state.get("attributes", {}).get("friendly_name", entity)
        await security.handle(Event(
            kind="state", source="home_assistant", message=f"{name} is {new}",
            data={"entity_id": entity, "old_state": old, "new_state": new,
                  "device_class": new_state.get("attributes", {}).get("device_class")},
        ))


def create_app(config: Config, ha: HomeAssistant | None = None, background: bool = True) -> FastAPI:
    ha = ha or HomeAssistant(config.home_assistant)
    events = EventLog()
    security = SecurityManager(config, ha, events)
    modules = load_modules(config)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        tasks = []
        if background:
            watched = {e for room in config.rooms for e in room.entities()}
            tasks = [
                asyncio.create_task(_watch_states(ha, security, watched)),
                asyncio.create_task(mqtt.listen(config.mqtt, security.handle)),
            ]
        yield
        for task in tasks:
            task.cancel()
        await ha.close()

    app = FastAPI(title="Home Control", lifespan=lifespan)
    app.state.security = security
    app.state.events = events

    @app.get("/")
    async def dashboard():
        return FileResponse(STATIC / "index.html")

    @app.get("/api/status")
    async def status():
        return {
            "home": config.home.name,
            "mode": security.mode,
            "modes": list(config.modes),
            "home_assistant": await ha.ping(),
            "modules": {n: await module_status(n, m, config) for n, m in modules.items()},
        }

    @app.get("/api/rooms")
    async def rooms():
        try:
            states = {s["entity_id"]: s for s in await ha.states()}
        except Exception as exc:
            raise HTTPException(502, f"Home Assistant unavailable: {exc}")
        result = []
        for room in config.rooms:
            devices = []
            for kind in ("cameras", "locks", "lights", "sensors"):
                for entity in getattr(room, kind):
                    state = states.get(entity, {})
                    devices.append({
                        "entity_id": entity,
                        "kind": kind[:-1],
                        "name": state.get("attributes", {}).get("friendly_name", entity),
                        "state": state.get("state", "unavailable"),
                    })
            result.append({"name": room.name, "devices": devices})
        return result

    @app.post("/api/mode")
    async def set_mode(request: ModeRequest):
        try:
            await security.set_mode(request.mode)
        except ValueError as exc:
            raise HTTPException(400, str(exc))
        return {"mode": security.mode}

    @app.post("/api/service")
    async def call_service(request: ServiceRequest):
        if (request.domain, request.service) not in ALLOWED_SERVICES:
            raise HTTPException(403, "Service not allowed from the dashboard")
        await ha.call_service(request.domain, request.service, entity_id=request.entity_id)
        return {"ok": True}

    @app.get("/api/cameras/{entity_id}/snapshot")
    async def snapshot(entity_id: str):
        if entity_id not in config.entities_of("cameras"):
            raise HTTPException(404, "Unknown camera")
        try:
            image = await ha.camera_snapshot(entity_id)
        except Exception as exc:
            raise HTTPException(502, f"Snapshot failed: {exc}")
        return Response(image, media_type="image/jpeg")

    @app.get("/api/events")
    async def recent_events(limit: int = 50):
        return [e.model_dump(mode="json") for e in events.recent(limit)]

    @app.websocket("/ws")
    async def stream(ws: WebSocket):
        await ws.accept()
        queue = events.subscribe()
        try:
            while True:
                event = await queue.get()
                await ws.send_json(event.model_dump(mode="json"))
        except WebSocketDisconnect:
            pass
        finally:
            events.unsubscribe(queue)

    return app
