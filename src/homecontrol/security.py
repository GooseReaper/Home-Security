"""Security modes and the rules that decide when to alert."""

from __future__ import annotations

import logging

from .config import Config
from .events import Event, EventLog
from .ha import HomeAssistant

log = logging.getLogger(__name__)

OPEN_STATES = {"on", "open"}


def classify(event: Event, config: Config) -> str | None:
    """Map a raw event to the alert kind that modes refer to."""
    if event.kind == "detection":
        if event.data.get("score", 0) < config.alerts.min_score:
            return None
        if event.data.get("label") in config.alerts.person_labels:
            if "face" in event.data:
                return "known_face"
            return "person"
        return "motion"
    if event.kind == "state":
        entity = event.data.get("entity_id", "")
        new = event.data.get("new_state")
        if new not in OPEN_STATES:
            return None
        device_class = event.data.get("device_class")
        if device_class in {"door", "window", "opening"} or "contact" in entity:
            return "door_open"
        if device_class in {"motion", "occupancy", "presence"} or "motion" in entity:
            return "motion"
    return None


def should_alert(kind: str | None, mode: str, config: Config) -> bool:
    if kind is None:
        return False
    mode_config = config.modes.get(mode)
    if mode_config is None:
        return False
    if kind == "known_face":
        # A recognised face is never an intruder, but in modes that alert
        # on people we still report who arrived.
        return False
    if kind == "person" and "unknown_face" in mode_config.alert_on:
        return True
    return kind in mode_config.alert_on


class SecurityManager:
    def __init__(self, config: Config, ha: HomeAssistant, events: EventLog) -> None:
        self.config = config
        self.ha = ha
        self.events = events
        self.mode = "home"

    async def set_mode(self, mode: str) -> None:
        if mode not in self.config.modes:
            raise ValueError(f"Unknown mode {mode!r}")
        self.mode = mode
        self.events.publish(Event(kind="mode", source="homecontrol", message=f"Mode set to {mode}"))
        mode_config = self.config.modes[mode]
        if mode_config.lock_all:
            for lock in self.config.entities_of("locks"):
                await self._safe_call("lock", "lock", entity_id=lock)
        if mode_config.lights_off:
            for light in self.config.entities_of("lights"):
                await self._safe_call("light", "turn_off", entity_id=light)

    async def handle(self, event: Event) -> None:
        self.events.publish(event)
        kind = classify(event, self.config)
        if not should_alert(kind, self.mode, self.config):
            return
        title = f"{self.config.home.name}: {kind.replace('_', ' ')}"
        alert = Event(kind="alert", source="homecontrol", message=f"{title} - {event.message}",
                      data={"cause": event.model_dump(mode="json")})
        self.events.publish(alert)
        for service in self.config.alerts.notify_services:
            try:
                await self.ha.notify(service, title, event.message)
            except Exception as exc:  # an alert failure must never crash the loop
                log.error("Notify via %s failed: %s", service, exc)

    async def _safe_call(self, domain: str, service: str, **data) -> None:
        try:
            await self.ha.call_service(domain, service, **data)
        except Exception as exc:
            log.error("%s.%s failed for %s: %s", domain, service, data, exc)
