"""Optional add-on modules (ad blocker, Wi-Fi sensing, ...).

A module is a package in this folder that exposes:

    status(settings: dict, home: dict) -> dict   # must include "ok" and "summary"
    install(settings: dict, home: dict) -> dict  # optional

It is enabled by giving it a section under `modules:` in the home config.
"""

from __future__ import annotations

import asyncio
import importlib
import logging
from types import ModuleType
from typing import Any

from ..config import Config

log = logging.getLogger(__name__)


def load_modules(config: Config) -> dict[str, ModuleType]:
    loaded = {}
    for name in config.modules:
        try:
            loaded[name] = importlib.import_module(f"{__name__}.{name}")
        except ImportError as exc:
            log.error("Module %r is configured but could not be loaded: %s", name, exc)
    return loaded


async def module_status(name: str, module: ModuleType, config: Config) -> dict[str, Any]:
    settings = config.modules.get(name, {})
    home = config.home.model_dump()
    try:
        result = await asyncio.to_thread(module.status, settings, home)
    except Exception as exc:
        return {"ok": False, "summary": f"status check failed: {exc}"}
    return {"ok": bool(result.get("ok")), "summary": str(result.get("summary", "")), **result}
