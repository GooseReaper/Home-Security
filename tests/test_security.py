from pathlib import Path

import pytest

from homecontrol.config import load_config
from homecontrol.events import Event, EventLog
from homecontrol.mqtt import parse_frigate_event
from homecontrol.security import SecurityManager, classify, should_alert

CONFIG = load_config(Path(__file__).parent.parent / "config" / "home.example.yaml")


class FakeHA:
    def __init__(self):
        self.calls = []

    async def call_service(self, domain, service, **data):
        self.calls.append((domain, service, data))

    async def notify(self, service, title, message):
        self.calls.append(("notify", service, title))


def frigate(label="person", score=0.9, face=None, type_="new"):
    after = {"label": label, "camera": "front_door", "top_score": score, "id": "1"}
    if face:
        after["sub_label"] = [face, 0.95]
    return {"type": type_, "after": after}


def test_parse_frigate_only_new():
    assert parse_frigate_event(frigate(type_="update")) is None
    event = parse_frigate_event(frigate(face="Goose"))
    assert event.data["face"] == "Goose"


@pytest.mark.parametrize("payload,expected", [
    (frigate(), "person"),
    (frigate(face="Goose"), "known_face"),
    (frigate(label="car"), "motion"),
    (frigate(score=0.3), None),
])
def test_classify_detections(payload, expected):
    assert classify(parse_frigate_event(payload), CONFIG) == expected


def test_classify_door():
    event = Event(kind="state", source="ha", message="",
                  data={"entity_id": "binary_sensor.front_door_contact", "new_state": "on"})
    assert classify(event, CONFIG) == "door_open"


def test_alert_rules_by_mode():
    assert not should_alert("person", "home", CONFIG)
    assert should_alert("person", "night", CONFIG)
    assert not should_alert("motion", "night", CONFIG)
    assert should_alert("motion", "away", CONFIG)
    assert not should_alert("known_face", "away", CONFIG)


async def test_away_mode_locks_and_alerts():
    ha, log = FakeHA(), EventLog()
    security = SecurityManager(CONFIG, ha, log)
    await security.set_mode("away")
    assert ("lock", "lock", {"entity_id": "lock.front_door"}) in ha.calls
    assert ("light", "turn_off", {"entity_id": "light.living_room"}) in ha.calls

    await security.handle(parse_frigate_event(frigate()))
    assert any(c[0] == "notify" for c in ha.calls)
    assert log.recent(1)[0].kind == "alert"


async def test_bad_mode_rejected():
    security = SecurityManager(CONFIG, FakeHA(), EventLog())
    with pytest.raises(ValueError):
        await security.set_mode("party")
