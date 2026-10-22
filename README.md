# Home Security

A central home control and security program for your own home(s). It runs on a home computer, connects to everything through Home Assistant, and adds security modes, alerts, face IDs, a camera wall and a single web dashboard.

## How it fits together

```
 cameras ──► Frigate (detection, faces, recording) ──┐
 ESP32 Wi-Fi sensing nodes ──────────────────────────┼──► MQTT (Mosquitto)
                                                     │          │
 locks, lights, Zigbee/Z-Wave, thermostats ──► Home Assistant ◄─┘
                                                     │
                                       homecontrol (this app)
                                 modes · alerts · dashboard · modules
```

- **hub/**: Docker Compose for Home Assistant, Mosquitto and Frigate. It works best on Linux. Windows needs Docker Desktop with WSL2, and USB sticks and host networking take extra setup there.
- **src/homecontrol/**: the Python control app (FastAPI). It runs on Windows or Linux.
- **config/**: one YAML file per home. Only `home.example.yaml` is committed.

## Quick start

```bash
# 1. Hub (on the Linux box)
cd hub
cp frigate/config.example.yml frigate/config.yml   # add your cameras
cp .env.example .env                               # camera credentials
docker compose up -d
# Open http://<hub>:8123, finish Home Assistant onboarding, add the MQTT
# and Frigate integrations, then create a long-lived access token.

# 2. Control app (Windows or Linux)
python -m venv .venv && . .venv/bin/activate        # Windows: .venv\Scripts\activate
pip install -e ".[dev]"
cp config/home.example.yaml config/home.yaml        # map your rooms and entities
export HC_HA_TOKEN=...                              # Windows: set HC_HA_TOKEN=...
python -m homecontrol --config config/home.yaml
# Dashboard: http://127.0.0.1:8080
```

## Security modes

| Mode  | Alerts on | Also |
|-------|-----------|------|
| home  | nothing | |
| night | people, doors opening | locks all doors |
| away  | people, unknown faces, doors, motion | locks doors, lights off |

You can change these per home in the `modes:` section of your config. A recognised face never triggers an intruder alert.

## Modules

Add-ons such as the network ad blocker live in `src/homecontrol/modules/<name>/`. A module is turned on by giving it a section under `modules:` in the home config, and it exposes `status(settings, home)` (plus an optional `install`). Its health shows on the dashboard.

## Privacy

This repo is public, so nothing private is committed. Real configs, `.env` files, tokens, recordings, snapshots and face data are all gitignored. Keep secrets in environment variables or gitignored files.

The dashboard can unlock doors, so it listens on 127.0.0.1 by default. Only open it to your LAN on purpose, and never forward it to the internet. For remote access, use a VPN such as Tailscale or WireGuard.

## Not a life-safety system

Camera AI and Wi-Fi sensing supplement real smoke and CO alarms and professional monitoring. They don't replace them.

## Tests

```bash
pytest
```
