"""HC-302 Health Connect / Samsung adapter foundation."""

from __future__ import annotations

from typing import Any

from backend.health_vault.monitoring.connectors.base import DeviceConnector, register_device_connector
from backend.health_vault.monitoring.observation import build_observation


# Metrics Health Connect / Samsung Health commonly expose when permissions granted.
_SUPPORTED = [
    "heart_rate",
    "resting_hr",
    "oxygen_saturation",
    "systolic_bp",
    "diastolic_bp",
    "ecg_result",
    "sleep_duration",
    "deep_sleep_duration",
    "rem_sleep_duration",
    "steps",
    "activity_minutes",
    "exercise_minutes",
    "weight",
]


class HealthConnectConnector(DeviceConnector):
    """
    Real integration boundary for Android Health Connect / Samsung Health data.

    HC-302 ships the contract + capability discovery. Live reads require an
    Android companion / Health Connect permission grant that this Python vault
    process does not possess. Production path therefore reports UNAVAILABLE /
    permission_required rather than fabricating readings.

    Explicit on-demand measurement notes:
    - Blood pressure is generally user-initiated on supported devices, not continuous.
    - ECG is generally an explicit recorded session, not continuous streaming.
    """

    connector_id = "health_connect"
    display_name = "Android Health Connect / Samsung Health"
    version = "hc302.health_connect.v1"
    supports_live = False  # live path not yet authorized/available in this process
    production_allowed = True

    def readiness(self, context: dict[str, Any] | None = None) -> dict[str, Any]:
        ctx = dict(context or {})
        # Optional injected platform bridge for future Android companion wiring.
        bridge = ctx.get("platform_bridge")
        if bridge is None:
            return {
                "state": "unavailable",
                "acquisition_mode": "UNAVAILABLE",
                "permission_required": True,
                "permission_granted": False,
                "live_available": False,
                "capabilities": {
                    "heart_rate": {"continuous_possible": True, "available": False},
                    "resting_hr": {"continuous_possible": True, "available": False},
                    "oxygen_saturation": {"continuous_possible": True, "available": False},
                    "blood_pressure": {
                        "continuous_possible": False,
                        "available": False,
                        "note": "BP generally requires an explicit supported measurement.",
                    },
                    "ecg": {
                        "continuous_possible": False,
                        "available": False,
                        "note": "ECG generally requires an explicit recorded session.",
                    },
                    "sleep": {"continuous_possible": False, "session_based": True, "available": False},
                    "steps_activity": {"continuous_possible": True, "available": False},
                    "weight": {"continuous_possible": False, "available": False},
                },
                "action_required": "Install/authorize an Android Health Connect companion bridge, then grant read permissions.",
                "errors": [],
            }
        # Future bridge contract: must expose readiness() itself.
        try:
            return dict(bridge.readiness())
        except Exception as exc:  # pragma: no cover - defensive
            return {
                "state": "error",
                "acquisition_mode": "UNAVAILABLE",
                "permission_required": True,
                "permission_granted": False,
                "live_available": False,
                "errors": [f"platform_bridge_error:{type(exc).__name__}"],
            }

    def supported_metrics(self) -> list[str]:
        return list(_SUPPORTED)

    def fetch_new_observations(
        self,
        *,
        cursor: dict[str, Any] | None = None,
        context: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        ctx = dict(context or {})
        bridge = ctx.get("platform_bridge")
        if bridge is None:
            return {
                "status": "UNAVAILABLE",
                "observations": [],
                "next_cursor": cursor or {},
                "errors": ["health_connect_bridge_unavailable"],
                "unavailable_reason": "No Android Health Connect platform bridge is configured in this process.",
            }

        # Permission denied path
        ready = {}
        try:
            ready = dict(bridge.readiness() or {})
        except Exception as exc:
            return {
                "status": "error",
                "observations": [],
                "next_cursor": cursor or {},
                "errors": [f"readiness_failed:{type(exc).__name__}"],
            }
        state = str(ready.get("state") or "")
        if state == "permission_denied":
            return {
                "status": "permission_denied",
                "observations": [],
                "next_cursor": cursor or {},
                "errors": ["permission_denied"],
                "unavailable_reason": "Health Connect permissions were denied.",
            }
        if state == "permission_required":
            return {
                "status": "permission_required",
                "observations": [],
                "next_cursor": cursor or {},
                "errors": ["permission_required"],
                "unavailable_reason": "Health Connect permissions have not been granted.",
            }

        try:
            raw_batch = bridge.fetch_new_observations(cursor=cursor or {}, context=ctx)
        except Exception as exc:
            return {
                "status": "error",
                "observations": [],
                "next_cursor": cursor or {},
                "errors": [f"fetch_failed:{type(exc).__name__}"],
            }

        observations: list[dict[str, Any]] = []
        errors: list[str] = []
        for item in list((raw_batch or {}).get("observations") or []):
            try:
                # Never silently re-label imported/manual as LIVE.
                mode = str(item.get("acquisition_mode") or "DELAYED").upper()
                if mode not in {"LIVE", "DELAYED"}:
                    mode = "DELAYED"
                item = dict(item)
                item["acquisition_mode"] = mode
                item["connector_id"] = self.connector_id
                item.setdefault("source", "health_connect")
                item.setdefault("provenance", "health_connect_sync")
                obs = build_observation(item, default_tz=ctx.get("default_tz"))
                observations.append(obs.to_dict())
            except ValueError as exc:
                errors.append(str(exc))
        return {
            "status": "ok" if not errors else "partial",
            "observations": observations,
            "next_cursor": (raw_batch or {}).get("next_cursor") or cursor or {},
            "errors": errors,
        }


register_device_connector(HealthConnectConnector())
