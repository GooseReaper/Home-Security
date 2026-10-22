"""Client for the Home Assistant REST and WebSocket APIs."""

from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import AsyncIterator
from typing import Any

import httpx
import websockets

from .config import HomeAssistantConfig

log = logging.getLogger(__name__)


class HomeAssistant:
    def __init__(self, config: HomeAssistantConfig) -> None:
        self.url = config.url.rstrip("/")
        self._token = config.token
        self._http = httpx.AsyncClient(
            base_url=f"{self.url}/api",
            headers={"Authorization": f"Bearer {config.token}"},
            timeout=10,
        )

    async def close(self) -> None:
        await self._http.aclose()

    async def ping(self) -> bool:
        try:
            response = await self._http.get("/")
            return response.status_code == 200
        except httpx.HTTPError:
            return False

    async def states(self) -> list[dict[str, Any]]:
        response = await self._http.get("/states")
        response.raise_for_status()
        return response.json()

    async def state(self, entity_id: str) -> dict[str, Any]:
        response = await self._http.get(f"/states/{entity_id}")
        response.raise_for_status()
        return response.json()

    async def call_service(self, domain: str, service: str, **data: Any) -> None:
        response = await self._http.post(f"/services/{domain}/{service}", json=data)
        response.raise_for_status()

    async def camera_snapshot(self, entity_id: str) -> bytes:
        response = await self._http.get(f"/camera_proxy/{entity_id}")
        response.raise_for_status()
        return response.content

    async def notify(self, service: str, title: str, message: str) -> None:
        domain, _, name = service.partition(".")
        await self.call_service(domain, name, title=title, message=message)

    async def state_changes(self) -> AsyncIterator[dict[str, Any]]:
        """Yield state_changed events forever, reconnecting on failure."""
        ws_url = self.url.replace("http", "ws", 1) + "/api/websocket"
        while True:
            try:
                async with websockets.connect(ws_url) as ws:
                    await ws.recv()  # auth_required
                    await ws.send(json.dumps({"type": "auth", "access_token": self._token}))
                    auth = json.loads(await ws.recv())
                    if auth.get("type") != "auth_ok":
                        raise RuntimeError(f"Home Assistant auth failed: {auth}")
                    await ws.send(json.dumps(
                        {"id": 1, "type": "subscribe_events", "event_type": "state_changed"}
                    ))
                    async for raw in ws:
                        message = json.loads(raw)
                        if message.get("type") == "event":
                            yield message["event"]["data"]
            except (OSError, websockets.WebSocketException, RuntimeError) as exc:
                log.warning("Home Assistant websocket dropped (%s), retrying in 10s", exc)
                await asyncio.sleep(10)
