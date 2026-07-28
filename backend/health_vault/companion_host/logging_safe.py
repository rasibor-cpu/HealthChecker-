"""Privacy-safe structured logging helpers for the companion-only host."""

from __future__ import annotations

import json
import logging
from typing import Any

from backend.health_vault.companion.security import redact_companion_log

_LOG = logging.getLogger("healthchecker.companion_host")


def configure_host_logging(level: int = logging.INFO) -> None:
    if not _LOG.handlers:
        handler = logging.StreamHandler()
        handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
        _LOG.addHandler(handler)
    _LOG.setLevel(level)
    _LOG.propagate = False


def log_event(event: str, **fields: Any) -> None:
    safe = redact_companion_log(dict(fields))
    # Never allow common secret keys through even if redactor missed a nesting.
    for banned in ("admin_token", "pepper", "device_token", "pair_code", "authorization", "token"):
        if banned in safe:
            safe[banned] = "[redacted]"
    _LOG.info("%s %s", event, json.dumps(safe, default=str, sort_keys=True))
