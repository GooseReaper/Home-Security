from pathlib import Path

from fastapi.testclient import TestClient

from homecontrol.app import create_app
from homecontrol.config import load_config

CONFIG = load_config(Path(__file__).parent.parent / "config" / "home.example.yaml")


class FakeHA:
    def __init__(self):
        self.calls = []

    async def ping(self):
        return True

    async def states(self):
        return [{"entity_id": "light.living_room", "state": "on", "attributes": {"friendly_name": "Lamp"}}]

    async def call_service(self, domain, service, **data):
        self.calls.append((domain, service, data))

    async def close(self):
        pass


def client():
    ha = FakeHA()
    return TestClient(create_app(CONFIG, ha=ha, background=False)), ha


def test_status_and_mode():
    c, _ = client()
    with c:
        assert c.get("/api/status").json()["mode"] == "home"
        assert c.post("/api/mode", json={"mode": "night"}).json()["mode"] == "night"
        assert c.post("/api/mode", json={"mode": "nope"}).status_code == 400


def test_rooms_and_dashboard():
    c, _ = client()
    with c:
        rooms = c.get("/api/rooms").json()
        lamp = next(d for r in rooms for d in r["devices"] if d["entity_id"] == "light.living_room")
        assert lamp["name"] == "Lamp" and lamp["state"] == "on"
        assert "Home Control" in c.get("/").text


def test_service_allowlist():
    c, ha = client()
    with c:
        ok = c.post("/api/service", json={"domain": "light", "service": "turn_off", "entity_id": "light.living_room"})
        assert ok.status_code == 200
        bad = c.post("/api/service", json={"domain": "shell_command", "service": "run", "entity_id": "x"})
        assert bad.status_code == 403
    assert ha.calls == [("light", "turn_off", {"entity_id": "light.living_room"})]


def test_unknown_camera_404():
    c, _ = client()
    with c:
        assert c.get("/api/cameras/camera.nope/snapshot").status_code == 404
