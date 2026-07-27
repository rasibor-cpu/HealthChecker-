"""HC-302 device connector provider abstraction."""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any, Callable

CONNECTOR_STATES = (
    "ready",
    "permission_required",
    "permission_denied",
    "unavailable",
    "import_required",
    "error",
    "disabled",
    "simulated_test_only",
)


class DeviceConnector(ABC):
    """
    Base class for continuous-monitoring device connectors.

    Connectors fetch/normalize observations only. They never write to the vault.
    Persistence always goes through IngestionCoordinator / ContinuousMonitoringBridge.
    """

    connector_id: str = "base"
    display_name: str = "Device Connector"
    version: str = "0.0.0"
    supports_live: bool = False
    production_allowed: bool = True

    @abstractmethod
    def readiness(self, context: dict[str, Any] | None = None) -> dict[str, Any]:
        """Return permission/readiness state and capability metadata."""

    @abstractmethod
    def supported_metrics(self) -> list[str]:
        """Metric types this connector can supply when available."""

    @abstractmethod
    def fetch_new_observations(
        self,
        *,
        cursor: dict[str, Any] | None = None,
        context: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """
        Fetch incremental observations.

        Returns:
          {
            status, observations: [raw dicts], next_cursor, errors, unavailable_reason?
          }
        """


_REGISTRY: dict[str, DeviceConnector] = {}
_FACTORIES: list[Callable[[], DeviceConnector]] = []


def register_device_connector(connector: DeviceConnector) -> DeviceConnector:
    if not connector or not getattr(connector, "connector_id", None):
        raise ValueError("connector must define connector_id")
    _REGISTRY[connector.connector_id] = connector
    return connector


def register_device_connector_factory(factory: Callable[[], DeviceConnector]) -> None:
    _FACTORIES.append(factory)


def clear_device_registry_for_tests() -> None:
    _REGISTRY.clear()


def _ensure_builtins() -> None:
    if _REGISTRY:
        return
    for factory in list(_FACTORIES):
        try:
            register_device_connector(factory())
        except Exception:
            continue
    if not _REGISTRY:
        # Import side-effects register connectors
        from backend.health_vault.monitoring.connectors import health_connect as _hc  # noqa: F401
        from backend.health_vault.monitoring.connectors import libre as _libre  # noqa: F401
        from backend.health_vault.monitoring.connectors import simulated as _sim  # noqa: F401


def get_device_connector(connector_id: str | None) -> DeviceConnector | None:
    _ensure_builtins()
    if not connector_id:
        return None
    key = str(connector_id).strip().lower()
    if key in _REGISTRY:
        return _REGISTRY[key]
    for cid, conn in _REGISTRY.items():
        if cid.lower() == key or cid.lower().replace("_", "") == key.replace("_", ""):
            return conn
    return None


def resolve_device_connector(connector_id: str) -> DeviceConnector:
    found = get_device_connector(connector_id)
    if not found:
        raise ValueError(f"unknown_device_connector:{connector_id}")
    return found


def list_device_connectors(*, include_simulated: bool = False) -> list[dict[str, Any]]:
    _ensure_builtins()
    rows: list[dict[str, Any]] = []
    for c in sorted(_REGISTRY.values(), key=lambda x: x.connector_id):
        if not include_simulated and not c.production_allowed:
            continue
        ready = c.readiness()
        rows.append(
            {
                "connector_id": c.connector_id,
                "display_name": c.display_name,
                "version": c.version,
                "supports_live": c.supports_live,
                "production_allowed": c.production_allowed,
                "supported_metrics": c.supported_metrics(),
                "readiness": ready,
            }
        )
    return rows
