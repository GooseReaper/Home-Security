"""Per-home configuration, loaded from a YAML file."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, Field


class HomeInfo(BaseModel):
    name: str = "Home"
    timezone: str = "UTC"


class HomeAssistantConfig(BaseModel):
    url: str = "http://localhost:8123"
    token: str = ""


class MqttConfig(BaseModel):
    host: str = "localhost"
    port: int = 1883
    username: str = ""
    password: str = ""


class Room(BaseModel):
    name: str
    cameras: list[str] = Field(default_factory=list)
    locks: list[str] = Field(default_factory=list)
    lights: list[str] = Field(default_factory=list)
    sensors: list[str] = Field(default_factory=list)

    def entities(self) -> list[str]:
        return self.cameras + self.locks + self.lights + self.sensors


class ModeConfig(BaseModel):
    # Event kinds that raise an alert in this mode: person, door_open,
    # motion, unknown_face.
    alert_on: list[str] = Field(default_factory=list)
    lock_all: bool = False
    lights_off: bool = False


class AlertConfig(BaseModel):
    notify_services: list[str] = Field(default_factory=list)
    person_labels: list[str] = Field(default_factory=lambda: ["person"])
    min_score: float = 0.7


def _default_modes() -> dict[str, ModeConfig]:
    return {
        "home": ModeConfig(),
        "night": ModeConfig(alert_on=["person", "door_open"], lock_all=True),
        "away": ModeConfig(
            alert_on=["person", "door_open", "motion", "unknown_face"],
            lock_all=True,
            lights_off=True,
        ),
    }


class Config(BaseModel):
    home: HomeInfo = Field(default_factory=HomeInfo)
    home_assistant: HomeAssistantConfig = Field(default_factory=HomeAssistantConfig)
    mqtt: MqttConfig = Field(default_factory=MqttConfig)
    rooms: list[Room] = Field(default_factory=list)
    modes: dict[str, ModeConfig] = Field(default_factory=_default_modes)
    alerts: AlertConfig = Field(default_factory=AlertConfig)
    # Settings for optional add-on modules (ad blocker, Wi-Fi sensing, ...),
    # keyed by module name. Each module validates its own section.
    modules: dict[str, dict[str, Any]] = Field(default_factory=dict)

    def entities_of(self, kind: str) -> list[str]:
        return [e for room in self.rooms for e in getattr(room, kind)]


def load_config(path: str | Path) -> Config:
    data = yaml.safe_load(Path(path).read_text(encoding="utf-8")) or {}
    config = Config.model_validate(data)
    # Secrets can come from the environment so they stay out of the file.
    if token := os.environ.get("HC_HA_TOKEN"):
        config.home_assistant.token = token
    if password := os.environ.get("HC_MQTT_PASSWORD"):
        config.mqtt.password = password
    return config
