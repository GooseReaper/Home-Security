"""The web API and dashboard."""

from __future__ import annotations

import asyncio
import logging
from contextlib import asynccontextmanager
from pathlib import Path

from urllib.parse import urlsplit

from fastapi import FastAPI, HTTPException, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse, Response
from pydantic import BaseModel
from starlette.middleware.trustedhost import TrustedHostMiddleware

from . import mqtt
from .auth import SESSION_COOKIE, LoginLimiter, Sessions, verify_password
from .config import Config
from .events import Event, EventLog
from .ha import HomeAssistant
from .modules import load_modules, module_status
from .security import SecurityManager

log = logging.getLogger(__name__)
STATIC = Path(__file__).parent / "static"


class LoginRequest(BaseModel):
    password: str


class ModeRequest(BaseModel):
    mode: str


class ServiceRequest(BaseModel):
    domain: str
    service: str
    entity_id: str


# Pages and endpoints that work before logging in.
PUBLIC_PATHS = {"/login", "/api/login"}

SECURITY_HEADERS = {
    "X-Frame-Options": "DENY",
    "X-Content-Type-Options": "nosniff",
    "Referrer-Policy": "no-referrer",
    "Cache-Control": "no-store",
    "Content-Security-Policy": (
        "default-src 'self'; script-src 'self' 'unsafe-inline'; style-src 'self' 'unsafe-inline'; "
        "img-src 'self'; connect-src 'self'; frame-ancestors 'none'; form-action 'self'"
    ),
}

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


def _same_origin(origin: str | None, host: str | None) -> bool:
    """Browsers send Origin on cross-site requests; it must match the page's own host.

    No Origin means a non-browser client or an old same-origin request, which
    still has to pass the login check.
    """
    if origin is None:
        return True
    return host is not None and urlsplit(origin).netloc == host


def create_app(config: Config, ha: HomeAssistant | None = None, background: bool = True) -> FastAPI:
    if not config.dashboard.password_hash:
        raise ValueError(
            "No dashboard password set. Run `python -m homecontrol hash-password` and put the "
            "result in dashboard.password_hash, or set HC_DASHBOARD_PASSWORD."
        )
    ha = ha or HomeAssistant(config.home_assistant)
    events = EventLog()
    security = SecurityManager(config, ha, events)
    modules = load_modules(config)
    sessions = Sessions(config.dashboard.session_hours)
    limiter = LoginLimiter()
    allowed_hosts = config.dashboard.allowed_hosts

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

    app = FastAPI(title="Home Control", lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)
    app.state.security = security
    app.state.events = events

    @app.middleware("http")
    async def require_login(request: Request, call_next):
        if request.method not in {"GET", "HEAD"} and not _same_origin(request.headers.get("origin"), request.headers.get("host")):
            response = JSONResponse({"detail": "Cross-site request refused"}, status_code=403)
        elif request.url.path in PUBLIC_PATHS or sessions.valid(request.cookies.get(SESSION_COOKIE)):
            response = await call_next(request)
        elif request.url.path.startswith("/api/"):
            response = JSONResponse({"detail": "Not logged in"}, status_code=401)
        else:
            response = RedirectResponse("/login", status_code=303)
        response.headers.update(SECURITY_HEADERS)
        return response

    # Added last so it runs first: refuse unknown Host headers (DNS rebinding).
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=allowed_hosts)

    @app.get("/login")
    async def login_page():
        return FileResponse(STATIC / "login.html")

    @app.post("/api/login")
    async def login(body: LoginRequest, request: Request):
        client = request.client.host if request.client else "unknown"
        if limiter.locked(client):
            raise HTTPException(429, "Too many failed logins. Try again in 15 minutes.")
        if not await asyncio.to_thread(verify_password, body.password, config.dashboard.password_hash):
            limiter.failed(client)
            log.warning("Failed dashboard login from %s", client)
            events.publish(Event(kind="security", source="homecontrol",
                                 message=f"Failed dashboard login from {client}"))
            raise HTTPException(401, "Wrong password")
        limiter.succeeded(client)
        log.info("Dashboard login from %s", client)
        response = JSONResponse({"ok": True})
        response.set_cookie(
            SESSION_COOKIE, sessions.create(), max_age=int(sessions.ttl), httponly=True,
            samesite="strict", secure=request.url.scheme == "https",
        )
        return response

    @app.post("/api/logout")
    async def logout(request: Request):
        sessions.revoke(request.cookies.get(SESSION_COOKIE))
        response = JSONResponse({"ok": True})
        response.delete_cookie(SESSION_COOKIE)
        return response

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
        # Only devices listed in the home config, so the dashboard can't reach
        # every lock or garage door Home Assistant knows about.
        configured = {e for room in config.rooms for e in room.entities()}
        if request.entity_id not in configured or not request.entity_id.startswith(f"{request.domain}."):
            raise HTTPException(403, "Device not in the home config")
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
        return [e.model_dump(mode="json") for e in events.recent(max(0, min(limit, 500)))]

    @app.websocket("/ws")
    async def stream(ws: WebSocket):
        # Browsers don't apply CORS to websockets, so check who is asking.
        if not _same_origin(ws.headers.get("origin"), ws.headers.get("host")) or not sessions.valid(
            ws.cookies.get(SESSION_COOKIE)
        ):
            await ws.close(code=1008)
            return
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
