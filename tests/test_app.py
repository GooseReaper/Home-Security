from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from homecontrol.app import create_app
from homecontrol.auth import hash_password
from homecontrol.config import load_config

PASSWORD = "correct horse battery"
CONFIG = load_config(Path(__file__).parent.parent / "config" / "home.example.yaml")
CONFIG.state_file = ""
CONFIG.dashboard.password_hash = hash_password(PASSWORD, iterations=1000)
CONFIG.dashboard.allowed_hosts.append("testserver")


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


def client(login=True):
    ha = FakeHA()
    c = TestClient(create_app(CONFIG, ha=ha, background=False))
    if login:
        assert c.post("/api/login", json={"password": PASSWORD}).status_code == 200
    return c, ha


def test_refuses_to_start_without_password():
    config = CONFIG.model_copy(deep=True)
    config.dashboard.password_hash = ""
    with pytest.raises(ValueError):
        create_app(config, ha=FakeHA(), background=False)


def test_everything_needs_login():
    c, ha = client(login=False)
    with c:
        assert c.get("/api/status").status_code == 401
        assert c.post("/api/mode", json={"mode": "home"}).status_code == 401
        assert c.post("/api/service", json={"domain": "lock", "service": "unlock",
                                            "entity_id": "lock.front_door"}).status_code == 401
        assert c.get("/api/cameras/camera.front_door/snapshot").status_code == 401
        page = c.get("/", follow_redirects=False)
        assert page.status_code == 303 and page.headers["location"] == "/login"
        assert c.get("/login").status_code == 200
        with pytest.raises(WebSocketDisconnect):
            with c.websocket_connect("/ws") as ws:
                ws.receive_json()
    assert ha.calls == []


def test_wrong_password_and_lockout():
    c, _ = client(login=False)
    with c:
        for _ in range(5):
            assert c.post("/api/login", json={"password": "nope"}).status_code == 401
        assert c.post("/api/login", json={"password": PASSWORD}).status_code == 429


def test_logout():
    c, _ = client()
    with c:
        assert c.get("/api/status").status_code == 200
        c.post("/api/logout")
        assert c.get("/api/status").status_code == 401


def test_unknown_host_refused():
    c, _ = client()
    with c:
        assert c.get("/api/status", headers={"host": "evil.example"}).status_code == 400


def test_cross_site_post_refused():
    c, ha = client()
    with c:
        r = c.post("/api/service", headers={"origin": "http://evil.example"},
                   json={"domain": "lock", "service": "unlock", "entity_id": "lock.front_door"})
        assert r.status_code == 403
        ok = c.post("/api/mode", headers={"origin": "http://testserver"}, json={"mode": "night"})
        assert ok.status_code == 200
    assert ("lock", "unlock", {"entity_id": "lock.front_door"}) not in ha.calls


def test_websocket_checks_origin():
    c, _ = client()
    with c:
        with pytest.raises(WebSocketDisconnect):
            with c.websocket_connect("/ws", headers={"origin": "http://evil.example"}) as ws:
                ws.receive_json()
        with c.websocket_connect("/ws", headers={"origin": "http://testserver"}) as ws:
            c.post("/api/mode", json={"mode": "night"})
            assert ws.receive_json()["kind"] == "mode"


def test_security_headers():
    c, _ = client()
    with c:
        r = c.get("/")
        assert r.headers["x-frame-options"] == "DENY"
        assert "frame-ancestors 'none'" in r.headers["content-security-policy"]


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


def test_service_only_configured_entities():
    c, ha = client()
    with c:
        other = c.post("/api/service", json={"domain": "lock", "service": "unlock", "entity_id": "lock.back_door"})
        assert other.status_code == 403
        mismatch = c.post("/api/service", json={"domain": "lock", "service": "unlock",
                                                "entity_id": "light.living_room"})
        assert mismatch.status_code == 403
    assert ha.calls == []


def test_unknown_camera_404():
    c, _ = client()
    with c:
        assert c.get("/api/cameras/camera.nope/snapshot").status_code == 404
