"""Network-wide ad blocker (AdGuard Home on a Raspberry Pi).

The control app calls status() often and install() once. Both take the
`modules.adblock` section of home.yaml as `settings` and the `home` section
as `home`, and return a plain dict with at least "ok" and "summary".

status() works from any machine on the home network. install() must run on
the Pi itself, as root, because it installs software and changes networking.
"""

from __future__ import annotations

import base64
import getpass
import ipaddress
import json
import os
import platform
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from typing import Any, Callable

ADGUARD_INSTALL_SCRIPT = (
    "https://raw.githubusercontent.com/AdguardTeam/AdGuardHome/master/scripts/install.sh"
)
ADGUARD_BINARY = "/opt/AdGuardHome/AdGuardHome"
SETUP_PORT = 3000  # AdGuard Home serves its first-run wizard here
PASSWORD_ENV = "ADBLOCK_ADMIN_PASSWORD"

DEFAULTS: dict[str, Any] = {
    "enabled": True,
    "router_ip": "",
    "pi_ip": "",
    "static_ip_method": "router",
    "network_prefix": 24,
    "interface": "eth0",
    "admin_username": "admin",
    "admin_password": "",
    "web_port": 80,
    "upstream_dns": [
        "https://dns.quad9.net/dns-query",
        "https://cloudflare-dns.com/dns-query",
    ],
    "bootstrap_dns": ["9.9.9.9", "1.1.1.1"],
    "blocklists": [],
    "allowlist": [],
}

Log = Callable[[str], None]


# --------------------------------------------------------------------------
# Settings
# --------------------------------------------------------------------------

def load_settings(settings: dict | None) -> dict:
    """Fill in defaults for anything the home.yaml section leaves out."""
    merged = dict(DEFAULTS)
    merged.update({k: v for k, v in (settings or {}).items() if v is not None})
    return merged


def validate(settings: dict) -> list[str]:
    """Return a list of human-readable problems; empty means the config is usable."""
    s = load_settings(settings)
    errors = []
    for key in ("router_ip", "pi_ip"):
        try:
            ipaddress.IPv4Address(str(s[key]))
        except ValueError:
            errors.append(f"{key} must be an IPv4 address like 192.168.1.53 (got {s[key]!r})")
    if not errors:
        try:
            net = ipaddress.IPv4Network(f"{s['router_ip']}/{s['network_prefix']}", strict=False)
        except ValueError:
            errors.append(f"network_prefix must be 8-30 (got {s['network_prefix']!r})")
        else:
            if ipaddress.IPv4Address(s["pi_ip"]) not in net:
                errors.append(f"pi_ip {s['pi_ip']} is not on the router's network {net}")
            if s["pi_ip"] == s["router_ip"]:
                errors.append("pi_ip must be different from router_ip")
    if s["static_ip_method"] not in ("router", "pi"):
        errors.append("static_ip_method must be 'router' or 'pi'")
    if not isinstance(s["web_port"], int) or not 1 <= s["web_port"] <= 65535:
        errors.append("web_port must be a number between 1 and 65535")
    if s["web_port"] in (53, SETUP_PORT):
        errors.append(f"web_port can't be {s['web_port']}")
    if not s["admin_username"]:
        errors.append("admin_username can't be blank")
    for i, bl in enumerate(s["blocklists"] or []):
        if not isinstance(bl, dict) or not bl.get("url"):
            errors.append(f"blocklists[{i}] needs a url")
    return errors


def _password(s: dict) -> str:
    return s["admin_password"] or os.environ.get(PASSWORD_ENV, "")


# --------------------------------------------------------------------------
# AdGuard Home HTTP API
# --------------------------------------------------------------------------

class _Api:
    def __init__(self, host: str, port: int, user: str = "", password: str = ""):
        self.base = f"http://{host}:{port}"
        self.auth = None
        if user:
            token = base64.b64encode(f"{user}:{password}".encode()).decode()
            self.auth = f"Basic {token}"

    def call(self, method: str, path: str, body: Any = None, timeout: float = 5) -> Any:
        data = json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request(self.base + path, data=data, method=method)
        if data is not None:
            req.add_header("Content-Type", "application/json")
        if self.auth:
            req.add_header("Authorization", self.auth)
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read()
        if not raw:
            return None
        try:
            return json.loads(raw)
        except ValueError:
            return raw.decode(errors="replace")

    def reachable(self, path: str = "/") -> bool:
        try:
            self.call("GET", path, timeout=2)
            return True
        except urllib.error.HTTPError:
            return True  # it answered, even if with 401/403
        except (urllib.error.URLError, OSError):
            return False

    def wait(self, path: str = "/", seconds: float = 30) -> bool:
        deadline = time.monotonic() + seconds
        while time.monotonic() < deadline:
            if self.reachable(path):
                return True
            time.sleep(1)
        return False


# --------------------------------------------------------------------------
# status()
# --------------------------------------------------------------------------

def status(settings: dict, home: dict | None = None) -> dict:
    """Ask the Pi how the ad blocker is doing. Safe to call often (short timeouts)."""
    s = load_settings(settings)
    if not s["enabled"]:
        return {"ok": True, "summary": "Ad blocker is turned off in home.yaml", "enabled": False}
    errors = validate(s)
    if errors:
        return {"ok": False, "summary": "Ad blocker config needs fixing", "errors": errors}

    api = _Api(s["pi_ip"], s["web_port"], s["admin_username"], _password(s))
    try:
        st = api.call("GET", "/control/status", timeout=3)
        stats = api.call("GET", "/control/stats", timeout=3)
    except urllib.error.HTTPError as e:
        if e.code in (401, 403):
            return {
                "ok": False,
                "summary": f"Ad blocker is up but login failed; set {PASSWORD_ENV}",
                "dashboard": api.base,
            }
        return {"ok": False, "summary": f"Ad blocker returned HTTP {e.code}", "dashboard": api.base}
    except (urllib.error.URLError, OSError) as e:
        return {
            "ok": False,
            "summary": f"Can't reach the ad blocker at {api.base}",
            "error": str(getattr(e, "reason", e)),
        }

    queries = int(stats.get("num_dns_queries", 0)) if isinstance(stats, dict) else 0
    blocked = int(stats.get("num_blocked_filtering", 0)) if isinstance(stats, dict) else 0
    pct = (100 * blocked / queries) if queries else 0.0
    running = bool(st.get("running"))
    protecting = bool(st.get("protection_enabled"))
    if running and protecting:
        summary = f"Blocking on: {blocked:,} of {queries:,} lookups blocked ({pct:.1f}%)"
    elif running:
        summary = "Ad blocker is running but protection is paused"
    else:
        summary = "Ad blocker DNS server is not running"
    return {
        "ok": running and protecting,
        "summary": summary,
        "version": st.get("version"),
        "queries": queries,
        "blocked": blocked,
        "blocked_percent": round(pct, 1),
        "dashboard": api.base,
    }


# --------------------------------------------------------------------------
# install()
# --------------------------------------------------------------------------

def install(
    settings: dict,
    home: dict | None = None,
    *,
    password: str | None = None,
    dry_run: bool = False,
    log: Log = print,
) -> dict:
    """Install and configure AdGuard Home on this Pi. Re-running it is safe."""
    s = load_settings(settings)
    errors = validate(s)
    if errors:
        return {"ok": False, "summary": "Fix home.yaml first", "errors": errors}
    password = password or _password(s)
    if not password:
        return {"ok": False, "summary": f"No admin password: set admin_password or {PASSWORD_ENV}"}
    if len(password) < 8:
        return {"ok": False, "summary": "Admin password must be at least 8 characters"}
    if not dry_run:
        if platform.system() != "Linux":
            return {"ok": False, "summary": "install() must run on the Pi (Linux), not this machine"}
        if os.geteuid() != 0:
            return {"ok": False, "summary": "install() needs root: run it with sudo"}

    done: list[str] = []

    def step(msg: str) -> None:
        log(("[dry run] " if dry_run else "") + msg)
        done.append(msg)

    try:
        _free_port_53(step, dry_run)
        _install_binary(step, dry_run)
        if not dry_run:
            _configure(s, password, step)
        else:
            step("Would run first-time setup, set upstream DNS, add block lists and allowlist")
        if s["static_ip_method"] == "pi":
            _set_static_ip(s, step, dry_run)
    except Exception as e:  # report, don't crash the control app's worker thread
        return {"ok": False, "summary": f"Install stopped: {e}", "steps": done}

    tz = (home or {}).get("timezone")
    next_steps = _router_instructions(s)
    if tz:
        next_steps.append(f"Set the Pi's clock to your home timezone: sudo timedatectl set-timezone {tz}")
    return {
        "ok": True,
        "summary": "Ad blocker installed" + (" (dry run, nothing changed)" if dry_run else ""),
        "dashboard": f"http://{s['pi_ip']}:{s['web_port']}",
        "steps": done,
        "next_steps": next_steps,
    }


def _run(cmd: list[str], check: bool = True) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, check=check, capture_output=True, text=True)


def _free_port_53(step: Callable[[str], None], dry_run: bool) -> None:
    """Ubuntu-style systems run a DNS stub on port 53 that blocks AdGuard Home."""
    if not shutil.which("systemctl"):
        return
    active = _run(["systemctl", "is-active", "systemd-resolved"], check=False).stdout.strip()
    if active != "active":
        return
    step("Turning off systemd-resolved's port 53 stub so AdGuard Home can use it")
    if dry_run:
        return
    os.makedirs("/etc/systemd/resolved.conf.d", exist_ok=True)
    with open("/etc/systemd/resolved.conf.d/adguardhome.conf", "w") as f:
        f.write("[Resolve]\nDNS=127.0.0.1\nDNSStubListener=no\n")
    if os.path.islink("/etc/resolv.conf") or os.path.exists("/etc/resolv.conf"):
        os.replace("/etc/resolv.conf", "/etc/resolv.conf.backup")
    os.symlink("/run/systemd/resolve/resolv.conf", "/etc/resolv.conf")
    _run(["systemctl", "reload-or-restart", "systemd-resolved"])


def _install_binary(step: Callable[[str], None], dry_run: bool) -> None:
    if os.path.exists(ADGUARD_BINARY):
        step("AdGuard Home is already installed, keeping it")
        return
    step("Downloading and installing AdGuard Home (official installer)")
    if dry_run:
        return
    with tempfile.TemporaryDirectory() as tmp:
        script = os.path.join(tmp, "install.sh")
        urllib.request.urlretrieve(ADGUARD_INSTALL_SCRIPT, script)
        result = _run(["sh", script, "-v"], check=False)
        if result.returncode != 0:
            raise RuntimeError("AdGuard Home installer failed:\n" + result.stderr[-2000:])


def _configure(s: dict, password: str, step: Callable[[str], None]) -> None:
    setup = _Api("127.0.0.1", SETUP_PORT)
    api = _Api("127.0.0.1", s["web_port"], s["admin_username"], password)

    if setup.wait("/control/install/get_addresses", seconds=20):
        step(f"Running first-time setup (dashboard on port {s['web_port']}, DNS on port 53)")
        setup.call("POST", "/control/install/configure", {
            "web": {"ip": "0.0.0.0", "port": s["web_port"]},
            "dns": {"ip": "0.0.0.0", "port": 53},
            "username": s["admin_username"],
            "password": password,
        }, timeout=30)
    else:
        step("First-time setup was already done, updating settings")

    if not api.wait("/control/status", seconds=30):
        raise RuntimeError(f"AdGuard Home didn't come up on port {s['web_port']}")
    try:
        api.call("GET", "/control/status")
    except urllib.error.HTTPError as e:
        if e.code in (401, 403):
            raise RuntimeError("Login failed: the admin password doesn't match the existing install") from e
        raise

    step("Setting upstream DNS: " + ", ".join(s["upstream_dns"]))
    api.call("POST", "/control/dns_config", {
        "upstream_dns": list(s["upstream_dns"]),
        "bootstrap_dns": list(s["bootstrap_dns"]),
    })

    filtering = api.call("GET", "/control/filtering/status") or {}
    have = {f.get("url") for f in filtering.get("filters") or []}
    for bl in s["blocklists"] or []:
        if bl["url"] in have:
            continue
        step(f"Adding block list: {bl.get('name') or bl['url']}")
        api.call("POST", "/control/filtering/add_url", {
            "name": bl.get("name") or bl["url"],
            "url": bl["url"],
            "whitelist": False,
        }, timeout=60)

    rules = list(filtering.get("user_rules") or [])
    new = [f"@@||{d.strip().lower()}^" for d in s["allowlist"] or [] if d.strip()]
    missing = [r for r in new if r not in rules]
    if missing:
        step("Allowing: " + ", ".join(s["allowlist"]))
        api.call("POST", "/control/filtering/set_rules", {"rules": rules + missing})


def _set_static_ip(s: dict, step: Callable[[str], None], dry_run: bool) -> None:
    if not shutil.which("nmcli"):
        raise RuntimeError(
            "static_ip_method 'pi' needs NetworkManager (Raspberry Pi OS Bookworm or newer). "
            "Use static_ip_method: router instead."
        )
    step(f"Setting the Pi's {s['interface']} to a fixed address {s['pi_ip']} (takes effect after reboot)")
    if dry_run:
        return
    conn = _run(["nmcli", "-g", "GENERAL.CONNECTION", "device", "show", s["interface"]]).stdout.strip()
    if not conn:
        raise RuntimeError(f"No active network connection on {s['interface']}")
    _run([
        "nmcli", "connection", "modify", conn,
        "ipv4.method", "manual",
        "ipv4.addresses", f"{s['pi_ip']}/{s['network_prefix']}",
        "ipv4.gateway", s["router_ip"],
        "ipv4.dns", "127.0.0.1",
    ])


def _router_instructions(s: dict) -> list[str]:
    steps = []
    if s["static_ip_method"] == "router":
        steps.append(
            f"In your router's admin page (http://{s['router_ip']}), find DHCP reservation "
            f"(sometimes 'Address Reservation' or 'Static Lease') and reserve {s['pi_ip']} for this Pi."
        )
    else:
        steps.append("Reboot the Pi (sudo reboot) so its fixed address takes effect.")
    steps.append(
        f"In the router's DHCP or LAN settings, set the DNS server to {s['pi_ip']} and remove any "
        "second DNS server (otherwise devices can skip the blocker)."
    )
    steps.append("Reconnect a phone to Wi-Fi and visit a site with ads to check it works.")
    steps.append(f"Dashboard: http://{s['pi_ip']}:{s['web_port']}")
    return steps


def prompt_password() -> str:
    """Ask for the admin password on the terminal (used by the CLI)."""
    if not sys.stdin.isatty():
        return ""
    while True:
        p1 = getpass.getpass("Choose a dashboard admin password (8+ characters): ")
        p2 = getpass.getpass("Type it again: ")
        if p1 == p2 and len(p1) >= 8:
            return p1
        print("Passwords didn't match or were too short, try again.")
