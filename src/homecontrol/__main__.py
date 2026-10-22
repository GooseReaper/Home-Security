"""Run with: python -m homecontrol --config config/home.yaml"""

from __future__ import annotations

import argparse
import logging

import uvicorn

from .app import create_app
from .config import load_config


def main() -> None:
    parser = argparse.ArgumentParser(description="Central home control app")
    parser.add_argument("--config", default="config/home.yaml")
    # 127.0.0.1 by default: the dashboard can lock doors, so only expose it
    # on your LAN deliberately (--host 0.0.0.0) and never to the internet.
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8080)
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    app = create_app(load_config(args.config))
    uvicorn.run(app, host=args.host, port=args.port)


if __name__ == "__main__":
    main()
