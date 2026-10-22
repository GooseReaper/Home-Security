"""Run with: python -m homecontrol --config config/home.yaml

Make a dashboard password hash with: python -m homecontrol hash-password
"""

from __future__ import annotations

import argparse
import getpass
import logging
import sys

import uvicorn

from .app import create_app
from .auth import hash_password
from .config import load_config


def _hash_password() -> None:
    password = getpass.getpass("Dashboard password (12+ characters): ")
    if len(password) < 12:
        sys.exit("Too short: use at least 12 characters.")
    if getpass.getpass("Again: ") != password:
        sys.exit("The passwords don't match.")
    print("Put this in your home config under dashboard.password_hash:")
    print(hash_password(password))


def main() -> None:
    if sys.argv[1:2] == ["hash-password"]:
        _hash_password()
        return

    parser = argparse.ArgumentParser(description="Central home control app")
    parser.add_argument("--config", default="config/home.yaml")
    # 127.0.0.1 by default: the dashboard can lock doors, so only expose it
    # on your LAN deliberately (--host 0.0.0.0) and never to the internet.
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8080)
    # Serve over HTTPS so the login and session cookie can't be sniffed on
    # the LAN. Tailscale (`tailscale cert`) or mkcert can make these files.
    parser.add_argument("--ssl-certfile")
    parser.add_argument("--ssl-keyfile")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    config = load_config(args.config)
    if args.host not in {"0.0.0.0", "::"} and args.host not in config.dashboard.allowed_hosts:
        config.dashboard.allowed_hosts.append(args.host)
    try:
        app = create_app(config)
    except ValueError as exc:
        sys.exit(str(exc))
    if args.host not in {"127.0.0.1", "localhost", "::1"} and not args.ssl_certfile:
        logging.warning("Dashboard is open to the network over plain HTTP. Use --ssl-certfile/--ssl-keyfile.")
    uvicorn.run(app, host=args.host, port=args.port,
                ssl_certfile=args.ssl_certfile, ssl_keyfile=args.ssl_keyfile)


if __name__ == "__main__":
    main()
