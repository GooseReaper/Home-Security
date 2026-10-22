"""Standalone CLI for the ad blocker, for running directly on the Pi.

    sudo python3 -m homecontrol.modules.adblock install --config config/home.yaml
    python3 -m homecontrol.modules.adblock status --config config/home.yaml
    python3 -m homecontrol.modules.adblock check --config config/home.yaml
"""

from __future__ import annotations

import argparse
import json
import os
import sys

from . import PASSWORD_ENV, install, prompt_password, status, validate


def _load(path: str) -> tuple[dict, dict]:
    try:
        import yaml
    except ImportError:
        sys.exit("PyYAML is missing. Install it with: sudo apt install python3-yaml")
    try:
        with open(path, encoding="utf-8") as f:
            cfg = yaml.safe_load(f) or {}
    except FileNotFoundError:
        sys.exit(f"No config at {path}. Copy config/home.example.yaml to {path} and fill it in.")
    settings = (cfg.get("modules") or {}).get("adblock")
    if settings is None:
        sys.exit(f"{path} has no modules.adblock section (see adblock.example.yaml)")
    return settings, cfg.get("home") or {}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="adblock", description="Home network ad blocker")
    parser.add_argument("command", choices=["install", "status", "check"])
    parser.add_argument("--config", default="config/home.yaml", help="path to home.yaml")
    parser.add_argument("--dry-run", action="store_true", help="show what install would do")
    parser.add_argument("--json", action="store_true", help="print the raw result")
    args = parser.parse_args(argv)

    settings, home = _load(args.config)

    if args.command == "check":
        errors = validate(settings)
        for e in errors:
            print("x " + e)
        print("Config looks good." if not errors else f"{len(errors)} problem(s) to fix.")
        return 1 if errors else 0

    if args.command == "status":
        result = status(settings, home)
    else:
        password = None
        if not args.dry_run and not settings.get("admin_password") and not os.environ.get(PASSWORD_ENV):
            password = prompt_password() or None
        result = install(settings, home, password=password or ("dry-run-pw" if args.dry_run else None),
                         dry_run=args.dry_run)

    if args.json:
        print(json.dumps(result, indent=2))
    else:
        print(("OK  " if result["ok"] else "ERR ") + result["summary"])
        for e in result.get("errors", []):
            print("  x " + e)
        for n in result.get("next_steps", []):
            print("  -> " + n)
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
