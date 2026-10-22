"""In-memory event log that the dashboard can stream from."""

from __future__ import annotations

import asyncio
from collections import deque
from datetime import datetime, timezone
from typing import Any

from pydantic import BaseModel, Field


class Event(BaseModel):
    # person, door_open, motion, unknown_face, known_face, state, mode, alert
    kind: str
    source: str
    message: str
    data: dict[str, Any] = Field(default_factory=dict)
    time: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class EventLog:
    def __init__(self, size: int = 500) -> None:
        self._events: deque[Event] = deque(maxlen=size)
        self._subscribers: set[asyncio.Queue[Event]] = set()

    def recent(self, limit: int = 50) -> list[Event]:
        return list(self._events)[-limit:][::-1]

    def publish(self, event: Event) -> None:
        self._events.append(event)
        for queue in self._subscribers:
            if not queue.full():
                queue.put_nowait(event)

    def subscribe(self) -> asyncio.Queue[Event]:
        queue: asyncio.Queue[Event] = asyncio.Queue(maxsize=100)
        self._subscribers.add(queue)
        return queue

    def unsubscribe(self, queue: asyncio.Queue[Event]) -> None:
        self._subscribers.discard(queue)
