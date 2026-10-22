"""Security modes and the rules that decide when to alert."""

from __future__ import annotations

import json
import logging
from pathlib import Path

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
            # No score means we can't judge the match, so trust it as before.
            if "face" in event.data and event.data.get("face_score", 1.0) >= config.alerts.min_face_score:
                return "known_face"
            return "person"
        return "motion"
    if event.kind == "state":
        entity = event.data.get("entity_id", "")
        new = event.data.get("new_state")
        if new == "unavailable":
            return "offline"
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
        # A recognised face is never treated as an intruder, but faces can be
        # faked, so modes that alert on unknown faces still report who came.
        return config.alerts.notify_known_faces and "unknown_face" in mode_config.alert_on
    if kind == "person" and "unknown_face" in mode_config.alert_on:
        return True
    return kind in mode_config.alert_on


class SecurityManager:
    def __init__(self, config: Config, ha: HomeAssistant, events: EventLog) -> None:
        self.config = config
        self.ha = ha
        self.events = events
        self.state_file = Path(config.state_file) if config.state_file else None
        self.mode = self._load_mode()
        if self.mode != "home":
            events.publish(Event(kind="mode", source="homecontrol",
                                 message=f"Mode restored to {self.mode} after a restart"))

    def _load_mode(self) -> str:
        if not self.state_file:
            return "home"
        try:
            mode = json.loads(self.state_file.read_text(encoding="utf-8")).get("mode")
        except FileNotFoundError:
            return "home"
        except (OSError, ValueError, AttributeError) as exc:
            # Fail armed: if the saved state is damaged, don't assume disarmed.
            log.error("Could not read %s (%s), starting in the most secure mode", self.state_file, exc)
            return "away" if "away" in self.config.modes else "home"
        return mode if mode in self.config.modes else "home"

    def _save_mode(self) -> None:
        if not self.state_file:
            return
        try:
            self.state_file.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.state_file.with_suffix(".tmp")
            tmp.write_text(json.dumps({"mode": self.mode}), encoding="utf-8")
            tmp.replace(self.state_file)
        except OSError as exc:
            log.error("Could not save mode to %s: %s", self.state_file, exc)

    async def set_mode(self, mode: str) -> None:
        if mode not in self.config.modes:
            raise ValueError(f"Unknown mode {mode!r}")
        self.mode = mode
        self._save_mode()
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
