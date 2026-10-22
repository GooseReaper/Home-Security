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
cp .env.example .env                               # camera and MQTT credentials
# MQTT users (passwords are asked for and stored in the gitignored mosquitto/passwd):
docker compose run --rm mosquitto mosquitto_passwd -c /mosquitto/config/passwd frigate
docker compose run --rm mosquitto mosquitto_passwd /mosquitto/config/passwd homeassistant
docker compose run --rm mosquitto mosquitto_passwd /mosquitto/config/passwd homecontrol
docker compose up -d
# Open http://<hub>:8123, finish Home Assistant onboarding, add the MQTT
# and Frigate integrations, then make a non-admin user for this app and
# create a long-lived access token as that user.
# Frigate's UI is at https://<hub>:8971 (login required).

# 2. Control app (Windows or Linux)
python -m venv .venv && . .venv/bin/activate        # Windows: .venv\Scripts\activate
pip install -e ".[dev]"
cp config/home.example.yaml config/home.yaml        # map your rooms and entities
export HC_HA_TOKEN=...                              # Windows: set HC_HA_TOKEN=...
python -m homecontrol hash-password                 # paste into dashboard.password_hash
python -m homecontrol --config config/home.yaml
# Dashboard: http://127.0.0.1:8080 (log in with that password)
pip install pre-commit && pre-commit install       # blocks commits containing secrets
```

## Security modes

| Mode  | Alerts on | Also |
|-------|-----------|------|
| home  | nothing | |
| night | people, doors opening | locks all doors |
| away  | people, unknown faces, doors, motion | locks doors, lights off |

Night and away also alert when a camera, lock or sensor goes offline, since a cut cable or a jammed Wi-Fi camera looks like that. You can change all of this per home in the `modes:` section of your config.

A recognised face is never treated as an intruder. In away mode you're still told who showed up, because a photo or a lookalike can fool face recognition. Matches below `alerts.min_face_score` count as unknown people.

The current mode is saved to `state_file`, so a crash, update or power cut doesn't quietly disarm the house.

## Modules

Add-ons such as the network ad blocker live in `src/homecontrol/modules/<name>/`. A module is turned on by giving it a section under `modules:` in the home config, and it exposes `status(settings, home)` (plus an optional `install`). Its health shows on the dashboard.

## Privacy

This repo is public, so nothing private is committed. Real configs, `.env` files, tokens, recordings, snapshots and face data are all gitignored. Keep secrets in environment variables or gitignored files.

A [gitleaks](https://github.com/gitleaks/gitleaks) pre-commit hook (`.pre-commit-config.yaml`) stops commits that contain passwords, tokens or keys.

## Security

The dashboard can unlock doors, so:

- It always needs a password, and locks an address out for 15 minutes after 5 wrong tries. Failed logins show up in the activity feed.
- It only answers to the host names in `dashboard.allowed_hosts` and refuses cross-site requests, which blocks DNS rebinding and malicious web pages.
- It can only control devices listed in your home config.
- It listens on 127.0.0.1 by default. To use it from other devices, add the hub's name to `allowed_hosts`, run with `--host 0.0.0.0 --ssl-certfile ... --ssl-keyfile ...` (Tailscale's `tailscale cert` or mkcert make these), and never forward it to the internet.

On the hub, MQTT needs a password per client and each user may only touch its own topics (`hub/mosquitto/acl`), so nobody on the network can fake a Frigate detection. MQTT and Frigate's unauthenticated API and restream are only reachable from the hub itself; Frigate's UI is on port 8971 with a login.

For remote access, use a VPN such as Tailscale or WireGuard. Never forward ports on your router. Put cameras and IoT devices on their own network, change every default password, and turn on two-factor login for Home Assistant.

## Not a life-safety system

Camera AI and Wi-Fi sensing supplement real smoke and CO alarms and professional monitoring. They don't replace them.

## Tests

```bash
pytest
```
