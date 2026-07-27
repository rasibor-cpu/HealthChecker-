"""HC-302 Libre continuous-glucose adapter foundation."""

from __future__ import annotations

from typing import Any

from backend.health_vault.monitoring.connectors.base import DeviceConnector, register_device_connector
from backend.health_vault.monitoring.observation import build_observation

_SUPPORTED = [
    "glucose",
    "cgm_average",
    "cgm_time_in_range",
    "cgm_gmi",
]


class LibreConnector(DeviceConnector):
    """
    Libre CGM connector foundation.

    Live access is UNAVAILABLE until an authorized Abbott/Libre integration path
    is configured. Existing file-import / upload parsers remain supported and are
    classified as IMPORTED — never LIVE.
    """

    connector_id = "libre"
    display_name = "FreeStyle Libre"
    version = "hc302.libre.v1"
    supports_live = False
    production_allowed = True

    def readiness(self, context: dict[str, Any] | None = None) -> dict[str, Any]:
        ctx = dict(context or {})
        authorized = bool(ctx.get("authorized_live_client"))
        if not authorized:
            return {
                "state": "import_required",
                "acquisition_mode": "UNAVAILABLE",
                "permission_required": True,
                "permission_granted": False,
                "live_available": False,
                "file_import_supported": True,
                "action_required": "Use Libre file/export import, or configure an authorized live Libre client.",
                "capabilities": {
                    "glucose": {"live": False, "import": True},
                    "trend_direction": {"live": False, "import": True},
                    "freshness": {"live": False, "import": True},
                },
                "errors": [],
            }
        try:
            return dict(ctx["authorized_live_client"].readiness())
        except Exception as exc:  # pragma: no cover
            return {
                "state": "error",
                "acquisition_mode": "UNAVAILABLE",
                "live_available": False,
                "file_import_supported": True,
                "errors": [f"libre_client_error:{type(exc).__name__}"],
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
        client = ctx.get("authorized_live_client")
        if client is None:
            # Explicit import path: allow caller to pass already-normalized import rows
            # labeled IMPORTED (never silently promoted to LIVE).
            import_rows = list(ctx.get("imported_observations") or [])
            if not import_rows:
                return {
                    "status": "IMPORT_REQUIRED",
                    "observations": [],
                    "next_cursor": cursor or {},
                    "errors": ["live_libre_unavailable"],
                    "unavailable_reason": (
                        "Authorized live Libre connectivity is not configured. "
                        "File/export import remains available and is classified as IMPORTED."
                    ),
                }
            observations: list[dict[str, Any]] = []
            errors: list[str] = []
            for item in import_rows:
                try:
                    row = dict(item)
                    row["acquisition_mode"] = "IMPORTED"
                    row["connector_id"] = self.connector_id
                    row.setdefault("source", "libre_import")
                    row.setdefault("provenance", "wearable_pdf")
                    obs = build_observation(row, default_tz=ctx.get("default_tz"))
                    observations.append(obs.to_dict())
                except ValueError as exc:
                    errors.append(str(exc))
            return {
                "status": "ok" if observations and not errors else ("partial" if observations else "error"),
                "observations": observations,
                "next_cursor": cursor or {},
                "errors": errors,
            }

        try:
            raw_batch = client.fetch_new_observations(cursor=cursor or {}, context=ctx)
        except Exception as exc:
            return {
                "status": "error",
                "observations": [],
                "next_cursor": cursor or {},
                "errors": [f"libre_fetch_failed:{type(exc).__name__}"],
            }

        observations = []
        errors = []
        for item in list((raw_batch or {}).get("observations") or []):
            try:
                row = dict(item)
                mode = str(row.get("acquisition_mode") or "LIVE").upper()
                if mode not in {"LIVE", "DELAYED"}:
                    mode = "DELAYED"
                row["acquisition_mode"] = mode
                row["connector_id"] = self.connector_id
                row.setdefault("source", "libre_live")
                row.setdefault("provenance", "libre_authorized_live")
                observations.append(build_observation(row, default_tz=ctx.get("default_tz")).to_dict())
            except ValueError as exc:
                errors.append(str(exc))
        return {
            "status": "ok" if not errors else "partial",
            "observations": observations,
            "next_cursor": (raw_batch or {}).get("next_cursor") or cursor or {},
            "errors": errors,
        }


register_device_connector(LibreConnector())
