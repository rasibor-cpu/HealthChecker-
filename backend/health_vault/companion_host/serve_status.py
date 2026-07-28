"""Parse Tailscale `serve status --json` for unexpected Funnel exposure."""

from __future__ import annotations

import json
from typing import Any


def funnel_exposure_from_serve_status(status: Any) -> list[str]:
    """
    Return host:port entries where Funnel is enabled (AllowFunnel value is true).

    The JSON key name itself is "AllowFunnel"; a naive substring match on "funnel"
    false-positives when Funnel is off. Only truthy map values count as exposure.
    """
    if status is None:
        return []
    if isinstance(status, (bytes, bytearray)):
        status = status.decode("utf-8", errors="replace")
    if isinstance(status, str):
        text = status.strip()
        if not text:
            return []
        try:
            status = json.loads(text)
        except json.JSONDecodeError as exc:
            raise ValueError("serve_status_json_invalid") from exc
    if not isinstance(status, dict):
        raise ValueError("serve_status_json_invalid")

    exposed: list[str] = []
    allow = status.get("AllowFunnel")
    if isinstance(allow, dict):
        for host_port, enabled in allow.items():
            if enabled is True:
                exposed.append(str(host_port))
    # Recurse into Foreground ephemeral configs if present.
    foreground = status.get("Foreground")
    if isinstance(foreground, dict):
        for nested in foreground.values():
            exposed.extend(funnel_exposure_from_serve_status(nested))
    return exposed


def assert_no_funnel_exposure(status: Any) -> None:
    exposed = funnel_exposure_from_serve_status(status)
    if exposed:
        raise ValueError("funnel_public_exposure_detected")
