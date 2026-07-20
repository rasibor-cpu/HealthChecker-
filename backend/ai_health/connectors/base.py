"""
AI Connector provider abstraction (HC-202).

Providers register into a global registry. Connectors normalize AI payloads;
they never write to the Health Vault. All persistence goes through ImportPipeline.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any, Callable


class AIConnector(ABC):
    """Base class for AI health-record connectors."""

    provider_id: str = "base"
    display_name: str = "AI Connector"
    version: str = "0.0.0"

    @abstractmethod
    def normalize_payload(self, payload: dict[str, Any]) -> dict[str, Any]:
        """
        Validate and normalize an inbound AI payload.

        Returns a canonical bridge payload:
          {
            provider_id, provider_version, conversation (metadata only),
            records: [ { document metadata, measurements, interpretation,
                         provenance, confidence, attachments, linkage } ]
          }

        Must NOT write to storage.
        """

    def supports(self, payload: dict[str, Any]) -> bool:
        """Return True if this connector can handle the payload."""
        pid = str(
            (payload or {}).get("provider_id")
            or (payload or {}).get("provider")
            or (payload or {}).get("ai_provider")
            or ""
        ).lower()
        return pid in {self.provider_id.lower(), self.provider_id.lower().replace("_", "")}


_REGISTRY: dict[str, AIConnector] = {}
_FACTORIES: list[Callable[[], AIConnector]] = []


def register_connector(connector: AIConnector) -> AIConnector:
    """Register a connector instance (idempotent by provider_id)."""
    if not connector or not getattr(connector, "provider_id", None):
        raise ValueError("connector must define provider_id")
    _REGISTRY[connector.provider_id] = connector
    return connector


def register_connector_factory(factory: Callable[[], AIConnector]) -> None:
    """Register a factory invoked on first resolve / list."""
    _FACTORIES.append(factory)


def _ensure_builtins() -> None:
    if _REGISTRY:
        return
    for factory in list(_FACTORIES):
        try:
            register_connector(factory())
        except Exception:
            continue
    if not _REGISTRY:
        # Lazy import built-ins
        from backend.ai_health.connectors.chatgpt import ChatGPTConnector

        register_connector(ChatGPTConnector())


def get_connector(provider_id: str | None) -> AIConnector | None:
    _ensure_builtins()
    if not provider_id:
        return None
    key = str(provider_id).strip().lower()
    if key in _REGISTRY:
        return _REGISTRY[key]
    for cid, conn in _REGISTRY.items():
        if cid.lower() == key or cid.lower().replace("_", "") == key.replace("_", ""):
            return conn
    return None


def resolve_connector(payload: dict[str, Any]) -> AIConnector:
    """Pick a connector for the payload; default ChatGPT V1 when unspecified."""
    _ensure_builtins()
    pid = (
        (payload or {}).get("provider_id")
        or (payload or {}).get("provider")
        or (payload or {}).get("ai_provider")
    )
    if pid:
        found = get_connector(str(pid))
        if found:
            return found
        raise ValueError(f"unknown_ai_provider:{pid}")
    for conn in _REGISTRY.values():
        if conn.supports(payload or {}):
            return conn
    chatgpt = get_connector("chatgpt")
    if chatgpt:
        return chatgpt
    raise ValueError("no_ai_connector_registered")


def list_connectors() -> list[dict[str, str]]:
    _ensure_builtins()
    return [
        {
            "provider_id": c.provider_id,
            "display_name": c.display_name,
            "version": c.version,
        }
        for c in sorted(_REGISTRY.values(), key=lambda x: x.provider_id)
    ]


def clear_registry_for_tests() -> None:
    """Test helper — clears registered connectors."""
    _REGISTRY.clear()
