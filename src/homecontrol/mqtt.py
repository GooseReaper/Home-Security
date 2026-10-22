"""Listens to Frigate (and other) MQTT topics and turns them into events."""

from __future__ import annotations

import asyncio
import json
import logging

import aiomqtt

from .config import MqttConfig
from .events import Event

log = logging.getLogger(__name__)


def parse_frigate_event(payload: dict) -> Event | None:
    """Turn a message from the frigate/events topic into an Event.

    Only the first message of each tracked object ("new") is used so one
    person walking past makes one event, not dozens.
    """
    if payload.get("type") != "new":
        return None
    after = payload.get("after") or {}
    label = after.get("label", "object")
    camera = after.get("camera", "unknown")
    score = float(after.get("top_score") or after.get("score") or 0)
    data = {"label": label, "camera": camera, "score": score, "id": after.get("id")}
    face = after.get("sub_label")
    face_score = None
    if isinstance(face, list):  # Frigate sends [name, score]
        face, face_score = (face + [None, None])[:2]
    if face:
        data["face"] = face
        if face_score is not None:
            data["face_score"] = float(face_score)
    message = f"{label} seen on {camera}" + (f" ({face})" if face else "")
    return Event(kind="detection", source="frigate", message=message, data=data)


async def listen(config: MqttConfig, on_event) -> None:
    """Subscribe forever, reconnecting on failure."""
    while True:
        try:
            async with aiomqtt.Client(
                hostname=config.host,
                port=config.port,
                username=config.username or None,
                password=config.password or None,
            ) as client:
                await client.subscribe("frigate/events")
                log.info("Connected to MQTT at %s:%s", config.host, config.port)
                async for message in client.messages:
                    try:
                        payload = json.loads(message.payload)
                    except (ValueError, TypeError):
                        continue
                    if event := parse_frigate_event(payload):
                        await on_event(event)
        except aiomqtt.MqttError as exc:
            log.warning("MQTT connection lost (%s), retrying in 10s", exc)
            await asyncio.sleep(10)
