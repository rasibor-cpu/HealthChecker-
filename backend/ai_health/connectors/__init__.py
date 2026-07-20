"""AI health connector package."""

from backend.ai_health.connectors.base import (
    AIConnector,
    get_connector,
    list_connectors,
    register_connector,
    resolve_connector,
)
from backend.ai_health.connectors import chatgpt as _chatgpt  # noqa: F401 — auto-register

__all__ = [
    "AIConnector",
    "get_connector",
    "list_connectors",
    "register_connector",
    "resolve_connector",
]
