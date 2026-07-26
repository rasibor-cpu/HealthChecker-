"""HC-301 CGM Continuity Guardian — sensor registry, inventory, data gaps."""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from uuid import uuid4

from backend.health_vault.models import utc_now

_DEFAULT_PATH = Path(__file__).resolve().parents[1] / "config" / "cgm_continuity.json"

SENSOR_STATUSES = (
    "planned",
    "active",
    "expiring",
    "expired",
    "failed",
    "replaced",
    "unknown",
)

CONTINUITY_STATES = (
    "SAFE",
    "WATCH",
    "REORDER_REQUIRED",
    "CRITICAL_SHORTAGE",
    "SENSOR_EXPIRING",
    "SENSOR_EXPIRED",
    "SIGNAL_LOSS",
    "DATA_PIPELINE_FAILURE",
    "INVENTORY_UNKNOWN",
)


def _parse_ts(ts: str | None) -> datetime | None:
    if not ts:
        return None
    try:
        return datetime.fromisoformat(str(ts).replace("Z", "+00:00"))
    except Exception:
        return None


def _iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


@dataclass
class CGMSensor:
    sensor_id: str = field(default_factory=lambda: str(uuid4()))
    patient_id: str = "default-patient"
    manufacturer: str = "Abbott"
    model: str = "FreeStyle Libre"
    serial_or_reference: str | None = None
    activation_timestamp: str | None = None
    expected_expiry_timestamp: str | None = None
    actual_expiry_timestamp: str | None = None
    expected_wear_days: int = 14
    status: str = "planned"
    failure_reason: str | None = None
    source: str = "manual"
    notes: str | None = None
    created_at: str = field(default_factory=utc_now)
    updated_at: str = field(default_factory=utc_now)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class CGMInventory:
    patient_id: str = "default-patient"
    unused_sensor_count: int = 0
    protected_reserve_count: int = 0
    minimum_protected_reserve: int = 1
    last_confirmed_at: str | None = None
    expected_wear_days: int = 14
    projected_coverage_days: float = 0.0
    travel_buffer_days: int = 7
    reorder_lead_days: int = 5
    reorder_deadline: str | None = None
    supply_location: str | None = None
    supplier_notes: str | None = None
    confidence: str = "unknown"
    status: str = "INVENTORY_UNKNOWN"

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class CGMContinuityGuardian:
    """Sensor + inventory + gap tracking. Does not pretend live Libre feeds exist."""

    def __init__(self, store: Any, config: dict[str, Any] | None = None, bus: Any | None = None) -> None:
        if config is not None:
            self.config = config
        else:
            p = _DEFAULT_PATH
            self.config = json.loads(p.read_text(encoding="utf-8")) if p.exists() else {}
        self.store = store
        self.bus = bus

    def list_sensors(self, patient_id: str = "default-patient") -> list[dict[str, Any]]:
        return [s for s in self.store.list_cgm_sensors() if s.get("patient_id") == patient_id]

    def register_sensor(self, payload: dict[str, Any]) -> dict[str, Any]:
        wear = int(payload.get("expected_wear_days") or self.config.get("default_expected_wear_days") or 14)
        sensor = CGMSensor(
            patient_id=str(payload.get("patient_id") or "default-patient"),
            manufacturer=str(payload.get("manufacturer") or self.config.get("default_manufacturer") or "Abbott"),
            model=str(payload.get("model") or self.config.get("default_model") or "FreeStyle Libre"),
            serial_or_reference=payload.get("serial_or_reference"),
            expected_wear_days=wear,
            status=str(payload.get("status") or "planned"),
            source=str(payload.get("source") or "manual"),
            notes=payload.get("notes"),
        )
        if payload.get("sensor_id"):
            sensor.sensor_id = str(payload["sensor_id"])
        saved = self.store.upsert_cgm_sensor(sensor.to_dict())
        self._emit("CGMSensorRegistered", saved)
        self._timeline_event("cgm_sensor_registered", saved)
        return saved

    def activate_sensor(
        self,
        sensor_id: str,
        *,
        activation_timestamp: str | None = None,
        reduce_inventory: bool = True,
    ) -> dict[str, Any]:
        sensor = self._get_sensor(sensor_id)
        if not sensor:
            return {"ok": False, "errors": ["sensor_not_found"]}
        # Idempotent: already activated (active/expiring/expired) does not re-decrement
        if sensor.get("activation_timestamp") and sensor.get("status") in (
            "active",
            "expiring",
            "expired",
        ):
            continuity = self.evaluate_continuity(patient_id=sensor.get("patient_id") or "default-patient")
            return {
                "ok": True,
                "sensor": sensor,
                "inventory": self.get_inventory(sensor.get("patient_id") or "default-patient"),
                "continuity": continuity,
                "idempotent": True,
            }
        now = activation_timestamp or utc_now()
        wear = int(sensor.get("expected_wear_days") or self.config.get("default_expected_wear_days") or 14)
        act = _parse_ts(now) or datetime.now(timezone.utc)
        expiry = act + timedelta(days=wear)
        # Mark previous active as replaced
        for s in self.list_sensors(sensor.get("patient_id") or "default-patient"):
            if s.get("status") == "active" and s.get("sensor_id") != sensor_id:
                s["status"] = "replaced"
                s["updated_at"] = utc_now()
                self.store.upsert_cgm_sensor(s)
                self._timeline_event("cgm_sensor_replaced", s)
        sensor["status"] = "active"
        sensor["activation_timestamp"] = _iso(act)
        sensor["expected_expiry_timestamp"] = _iso(expiry)
        sensor["updated_at"] = utc_now()
        saved = self.store.upsert_cgm_sensor(sensor)
        inv_result = None
        if reduce_inventory:
            inv_result = self._decrement_inventory(sensor.get("patient_id") or "default-patient")
        self._emit("CGMSensorActivated", saved)
        self._timeline_event("cgm_sensor_activated", saved)
        continuity = self.evaluate_continuity(patient_id=sensor.get("patient_id") or "default-patient")
        return {"ok": True, "sensor": saved, "inventory": inv_result, "continuity": continuity}

    def fail_sensor(self, sensor_id: str, *, reason: str | None = None) -> dict[str, Any]:
        sensor = self._get_sensor(sensor_id)
        if not sensor:
            return {"ok": False, "errors": ["sensor_not_found"]}
        sensor["status"] = "failed"
        sensor["failure_reason"] = reason or "unspecified"
        sensor["actual_expiry_timestamp"] = utc_now()
        sensor["updated_at"] = utc_now()
        saved = self.store.upsert_cgm_sensor(sensor)
        self._emit("CGMSensorFailed", saved)
        self._timeline_event("cgm_sensor_failed", saved)
        return {"ok": True, "sensor": saved, "continuity": self.evaluate_continuity(patient_id=sensor.get("patient_id") or "default-patient")}

    def replace_sensor(
        self,
        old_sensor_id: str,
        new_payload: dict[str, Any],
    ) -> dict[str, Any]:
        old = self._get_sensor(old_sensor_id)
        if old:
            old["status"] = "replaced"
            old["updated_at"] = utc_now()
            self.store.upsert_cgm_sensor(old)
            self._timeline_event("cgm_sensor_replaced", old)
        registered = self.register_sensor(new_payload)
        return self.activate_sensor(registered["sensor_id"], activation_timestamp=new_payload.get("activation_timestamp"))

    def get_inventory(self, patient_id: str = "default-patient") -> dict[str, Any]:
        inv = self.store.get_cgm_inventory(patient_id)
        if inv:
            return inv
        # Unknown inventory is a warning state — never invent counts
        default = CGMInventory(
            patient_id=patient_id,
            minimum_protected_reserve=int(self.config.get("minimum_protected_reserve") or 1),
            expected_wear_days=int(self.config.get("default_expected_wear_days") or 14),
            travel_buffer_days=int(self.config.get("travel_buffer_days") or 7),
            reorder_lead_days=int(self.config.get("reorder_lead_days") or 5),
            confidence="unknown",
            status="INVENTORY_UNKNOWN",
        ).to_dict()
        return default

    def update_inventory(self, payload: dict[str, Any]) -> dict[str, Any]:
        patient_id = str(payload.get("patient_id") or "default-patient")
        current = dict(self.get_inventory(patient_id))
        unused = int(payload.get("unused_sensor_count", current.get("unused_sensor_count") or 0))
        if unused < 0:
            unused = 0
        current["unused_sensor_count"] = unused
        current["protected_reserve_count"] = max(0, int(payload.get("protected_reserve_count", current.get("protected_reserve_count") or 0)))
        current["minimum_protected_reserve"] = int(
            payload.get("minimum_protected_reserve", current.get("minimum_protected_reserve") or self.config.get("minimum_protected_reserve") or 1)
        )
        current["expected_wear_days"] = int(
            payload.get("expected_wear_days", current.get("expected_wear_days") or self.config.get("default_expected_wear_days") or 14)
        )
        current["travel_buffer_days"] = int(
            payload.get("travel_buffer_days", current.get("travel_buffer_days") or self.config.get("travel_buffer_days") or 7)
        )
        current["reorder_lead_days"] = int(
            payload.get("reorder_lead_days", current.get("reorder_lead_days") or self.config.get("reorder_lead_days") or 5)
        )
        current["supply_location"] = payload.get("supply_location", current.get("supply_location"))
        current["supplier_notes"] = payload.get("supplier_notes", current.get("supplier_notes"))
        current["last_confirmed_at"] = utc_now()
        current["confidence"] = str(payload.get("confidence") or "confirmed")
        current["patient_id"] = patient_id
        current = self._recompute_inventory_fields(current, patient_id)
        saved = self.store.save_cgm_inventory(current)
        self._emit("CGMInventoryUpdated", saved)
        self._timeline_event("cgm_inventory_updated", saved)
        return saved

    def record_data_gap(self, payload: dict[str, Any]) -> dict[str, Any]:
        gap = {
            "gap_id": str(payload.get("gap_id") or uuid4()),
            "patient_id": str(payload.get("patient_id") or "default-patient"),
            "source": str(payload.get("source") or "cgm_or_meter"),
            "provider": str(payload.get("provider") or "upload_parser"),
            "expected_reading_cadence_minutes": int(
                payload.get("expected_reading_cadence_minutes")
                or self.config.get("expected_reading_cadence_minutes")
                or 15
            ),
            "most_recent_reading_timestamp": payload.get("most_recent_reading_timestamp"),
            "gap_start_timestamp": payload.get("gap_start_timestamp") or utc_now(),
            "missing_duration_minutes": payload.get("missing_duration_minutes"),
            "reason_classification": payload.get("reason_classification") or "unknown",
            "retry_state": payload.get("retry_state") or "pending",
            "acknowledgement": payload.get("acknowledgement") or "none",
            "escalation_status": payload.get("escalation_status") or "none",
            "resolution_timestamp": payload.get("resolution_timestamp"),
            "created_at": utc_now(),
            "updated_at": utc_now(),
            "note": "No data is never interpreted as a normal measurement.",
        }
        # Compute missing duration if possible
        recent = _parse_ts(gap.get("most_recent_reading_timestamp"))
        start = _parse_ts(gap.get("gap_start_timestamp"))
        if recent and start and gap.get("missing_duration_minutes") is None:
            gap["missing_duration_minutes"] = max(0, int((start - recent).total_seconds() / 60))
        saved = self.store.upsert_data_gap(gap)
        self._emit("DataGapDetected", saved)
        self._timeline_event("data_gap", saved)
        return saved

    def list_data_gaps(self, patient_id: str = "default-patient") -> list[dict[str, Any]]:
        return [g for g in self.store.list_data_gaps() if g.get("patient_id") == patient_id]

    def detect_glucose_gap(
        self,
        *,
        patient_id: str = "default-patient",
        now: str | None = None,
    ) -> dict[str, Any] | None:
        """Detect missing glucose data from vault measurements. Upload-based — not live Libre."""
        now_ts = _parse_ts(now or utc_now()) or datetime.now(timezone.utc)
        latest = None
        for m in self.store.list_measurements():
            if m.get("metric") != "glucose":
                continue
            ts = _parse_ts(m.get("measured_at"))
            if ts is None:
                continue
            if latest is None or ts > latest:
                latest = ts
        if latest is None:
            # No data at all — record unknown gap, never treat as normal
            return self.record_data_gap(
                {
                    "patient_id": patient_id,
                    "most_recent_reading_timestamp": None,
                    "gap_start_timestamp": _iso(now_ts),
                    "missing_duration_minutes": None,
                    "reason_classification": "no_glucose_measurements",
                    "escalation_status": "watch",
                }
            )
        minutes = int((now_ts - latest).total_seconds() / 60)
        watch = int(self.config.get("data_gap_watch_minutes") or 45)
        if minutes < watch:
            return None
        urgent = int(self.config.get("data_gap_urgent_minutes") or 180)
        critical = int(self.config.get("data_gap_critical_minutes") or 360)
        escalation = "watch"
        if minutes >= critical:
            escalation = "critical"
        elif minutes >= urgent:
            escalation = "urgent"
        return self.record_data_gap(
            {
                "patient_id": patient_id,
                "most_recent_reading_timestamp": _iso(latest),
                "gap_start_timestamp": _iso(now_ts),
                "missing_duration_minutes": minutes,
                "reason_classification": "stale_glucose_feed",
                "escalation_status": escalation,
            }
        )

    def evaluate_continuity(self, patient_id: str = "default-patient", now: str | None = None) -> dict[str, Any]:
        now_dt = _parse_ts(now or utc_now()) or datetime.now(timezone.utc)
        sensors = self.list_sensors(patient_id)
        active = next((s for s in sensors if s.get("status") in ("active", "expiring")), None)
        if not active:
            # Keep tracking last worn sensor until replaced/failed (status may already be expired)
            expired = [
                s
                for s in sensors
                if s.get("status") == "expired" and s.get("activation_timestamp")
            ]
            if expired:
                active = max(expired, key=lambda s: str(s.get("activation_timestamp") or ""))
        inv = self._recompute_inventory_fields(dict(self.get_inventory(patient_id)), patient_id, now=now_dt)
        self.store.save_cgm_inventory(inv)

        states: list[str] = []
        reasons: list[str] = []
        hours_remaining = None
        if inv.get("confidence") == "unknown":
            states.append("INVENTORY_UNKNOWN")
            reasons.append("Sensor inventory has not been confirmed.")
        if active:
            exp = _parse_ts(active.get("expected_expiry_timestamp"))
            if exp:
                hours_remaining = (exp - now_dt).total_seconds() / 3600.0
                warn_h = float(self.config.get("expiring_warning_hours") or 24)
                if hours_remaining <= 0:
                    active["status"] = "expired"
                    active["updated_at"] = utc_now()
                    self.store.upsert_cgm_sensor(active)
                    states.append("SENSOR_EXPIRED")
                    reasons.append("Active CGM sensor is past expected expiry.")
                elif hours_remaining <= warn_h:
                    active["status"] = "expiring"
                    active["updated_at"] = utc_now()
                    self.store.upsert_cgm_sensor(active)
                    states.append("SENSOR_EXPIRING")
                    reasons.append(f"Active sensor expiring in ~{hours_remaining:.1f} hours.")
                elif active.get("status") in ("expired", "expiring"):
                    # Reconcile status when evaluating against an earlier now
                    active["status"] = "active"
                    active["updated_at"] = utc_now()
                    self.store.upsert_cgm_sensor(active)
        else:
            reasons.append("No active CGM sensor registered (upload/manual registry only).")

        unused = int(inv.get("unused_sensor_count") or 0)
        min_res = int(inv.get("minimum_protected_reserve") or 1)
        if unused < min_res and inv.get("confidence") != "unknown":
            states.append("REORDER_REQUIRED")
            reasons.append("Unused sensors below protected reserve.")
        projected = float(inv.get("projected_coverage_days") or 0)
        need = float(inv.get("travel_buffer_days") or 0) + float(inv.get("reorder_lead_days") or 0)
        if inv.get("confidence") != "unknown" and projected < need:
            states.append("CRITICAL_SHORTAGE" if projected < float(inv.get("reorder_lead_days") or 0) else "REORDER_REQUIRED")
            reasons.append("Projected CGM coverage is below configured buffer needs.")

        gaps = [g for g in self.list_data_gaps(patient_id) if not g.get("resolution_timestamp")]
        for g in gaps:
            esc = g.get("escalation_status")
            if esc in ("urgent", "critical"):
                states.append("SIGNAL_LOSS")
                reasons.append("Glucose data gap detected; no data is not normal.")

        # Priority pick
        priority = [
            "DATA_PIPELINE_FAILURE",
            "CRITICAL_SHORTAGE",
            "SENSOR_EXPIRED",
            "SIGNAL_LOSS",
            "SENSOR_EXPIRING",
            "REORDER_REQUIRED",
            "INVENTORY_UNKNOWN",
            "WATCH",
            "SAFE",
        ]
        if not states:
            states = ["SAFE"]
            reasons.append("No continuity warnings under current configuration.")
        overall = next((p for p in priority if p in states), states[0])
        result = {
            "patient_id": patient_id,
            "state": overall,
            "states": states,
            "reasons": reasons,
            "active_sensor": active,
            "hours_remaining": hours_remaining,
            "inventory": inv,
            "open_data_gaps": gaps,
            "live_libre_api": False,
            "disclaimer": self.config.get("disclaimer"),
            "evaluated_at": _iso(now_dt),
        }
        self.store.save_cgm_continuity(result)
        return result

    def _decrement_inventory(self, patient_id: str) -> dict[str, Any]:
        inv = dict(self.get_inventory(patient_id))
        if inv.get("confidence") == "unknown":
            # Do not invent counts; note activation without auto-decrement certainty
            inv["supplier_notes"] = (inv.get("supplier_notes") or "") + " | activation without confirmed inventory"
            return self.store.save_cgm_inventory(inv)
        unused = int(inv.get("unused_sensor_count") or 0)
        unused = max(0, unused - 1)
        inv["unused_sensor_count"] = unused
        inv["last_confirmed_at"] = utc_now()
        inv = self._recompute_inventory_fields(inv, patient_id)
        return self.store.save_cgm_inventory(inv)

    def _recompute_inventory_fields(
        self,
        inv: dict[str, Any],
        patient_id: str,
        now: datetime | None = None,
    ) -> dict[str, Any]:
        now = now or datetime.now(timezone.utc)
        wear = float(inv.get("expected_wear_days") or self.config.get("default_expected_wear_days") or 14)
        unused = max(0, int(inv.get("unused_sensor_count") or 0))
        # Active sensor residual days
        residual = 0.0
        for s in self.list_sensors(patient_id):
            if s.get("status") in ("active", "expiring"):
                exp = _parse_ts(s.get("expected_expiry_timestamp"))
                if exp:
                    residual = max(0.0, (exp - now).total_seconds() / 86400.0)
        projected = residual + unused * wear
        inv["projected_coverage_days"] = round(projected, 2)
        lead = float(inv.get("reorder_lead_days") or self.config.get("reorder_lead_days") or 5)
        travel = float(inv.get("travel_buffer_days") or self.config.get("travel_buffer_days") or 7)
        # Reorder when projected coverage approaches lead + travel buffer
        days_until_deadline = projected - lead - travel
        deadline = now + timedelta(days=max(0.0, days_until_deadline))
        inv["reorder_deadline"] = _iso(deadline)
        min_res = int(inv.get("minimum_protected_reserve") or 1)
        if inv.get("confidence") == "unknown":
            inv["status"] = "INVENTORY_UNKNOWN"
        elif unused < min_res:
            inv["status"] = "REORDER_REQUIRED"
        elif projected < lead + travel:
            inv["status"] = "CRITICAL_SHORTAGE" if projected < lead else "REORDER_REQUIRED"
        else:
            inv["status"] = "SAFE"
        inv["protected_reserve_count"] = min(unused, max(int(inv.get("protected_reserve_count") or 0), 0))
        return inv

    def _get_sensor(self, sensor_id: str) -> dict[str, Any] | None:
        for s in self.store.list_cgm_sensors():
            if s.get("sensor_id") == sensor_id:
                return dict(s)
        return None

    def _timeline_event(self, kind: str, payload: dict[str, Any]) -> None:
        event = {
            "event_id": str(uuid4()),
            "kind": kind,
            "category": "cgm_continuity",
            "measured_at": payload.get("activation_timestamp")
            or payload.get("updated_at")
            or payload.get("gap_start_timestamp")
            or utc_now(),
            "imported_at": utc_now(),
            "provenance": payload.get("source") or "manual",
            "severity": None,
            "summary": kind.replace("_", " "),
            "payload": {
                k: payload.get(k)
                for k in (
                    "sensor_id",
                    "status",
                    "unused_sensor_count",
                    "projected_coverage_days",
                    "gap_id",
                    "missing_duration_minutes",
                    "escalation_status",
                )
                if k in payload
            },
            "dedupe_key": f"{kind}|{payload.get('sensor_id') or payload.get('gap_id') or payload.get('patient_id')}|{payload.get('updated_at') or payload.get('created_at')}",
        }
        self.store.append_timeline_event(event)

    def _emit(self, name: str, payload: dict[str, Any]) -> None:
        if self.bus is not None:
            try:
                self.bus.publish(name, payload if isinstance(payload, dict) else {})
            except Exception:
                pass
