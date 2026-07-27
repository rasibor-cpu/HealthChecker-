"""HC-302 simulated connector — TEST ONLY. Never selected by production sync."""

from __future__ import annotations

from typing import Any

from backend.health_vault.monitoring.connectors.base import DeviceConnector, register_device_connector
from backend.health_vault.monitoring.observation import build_observation


class SimulatedTestConnector(DeviceConnector):
    """
    Isolated test double. production_allowed=False.

    ContinuousMonitoringBridge refuses to sync this connector unless
    allow_simulated=True is explicitly passed (tests only).
    """

    connector_id = "simulated"
    display_name = "Simulated Test Connector"
    version = "hc302.simulated.v1"
    supports_live = False
    production_allowed = False

    def readiness(self, context: dict[str, Any] | None = None) -> dict[str, Any]:
        return {
            "state": "simulated_test_only",
            "acquisition_mode": "SIMULATED_TEST_ONLY",
            "permission_required": False,
            "permission_granted": True,
            "live_available": False,
            "production_forbidden": True,
            "errors": [],
        }

    def supported_metrics(self) -> list[str]:
        return ["heart_rate", "glucose", "oxygen_saturation"]

    def fetch_new_observations(
        self,
        *,
        cursor: dict[str, Any] | None = None,
        context: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        ctx = dict(context or {})
        rows = list(ctx.get("simulated_observations") or [])
        observations: list[dict[str, Any]] = []
        errors: list[str] = []
        for item in rows:
            try:
                row = dict(item)
                row["acquisition_mode"] = "SIMULATED_TEST_ONLY"
                row["allow_simulated"] = True
                row["connector_id"] = self.connector_id
                row.setdefault("source", "simulated_test")
                row["provenance"] = "simulated_test_only"
                observations.append(build_observation(row, default_tz=ctx.get("default_tz")).to_dict())
            except ValueError as exc:
                errors.append(str(exc))
        next_cursor = dict(cursor or {})
        next_cursor["last_index"] = int(next_cursor.get("last_index") or 0) + len(observations)
        return {
            "status": "ok",
            "observations": observations,
            "next_cursor": next_cursor,
            "errors": errors,
        }


# Registered but excluded from production list_device_connectors(include_simulated=False)
register_device_connector(SimulatedTestConnector())
