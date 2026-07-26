"""HC-301 formal Alert Engine — observational, auditable, non-diagnostic."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any
from uuid import uuid4

from backend.health_vault.models import utc_now

SAFETY_DISCLAIMER = (
    "Observational only — not a diagnosis. HealthChecker+ does not replace "
    "FreeStyle Libre alarms, Samsung Health Monitor, medical care, or emergency services. "
    "No medication or insulin dosing advice is provided."
)

SEVERITIES = ("informational", "watch", "warning", "urgent", "critical")
SEVERITY_RANK = {s: i for i, s in enumerate(SEVERITIES)}
STATUSES = ("active", "acknowledged", "snoozed", "resolved", "expired")


@dataclass
class Alert:
    alert_id: str = field(default_factory=lambda: str(uuid4()))
    patient_id: str = "default-patient"
    rule_id: str = ""
    rule_version: str = "1.0.0"
    title: str = ""
    message: str = ""
    severity: str = "watch"
    category: str = "general"
    metrics: list[str] = field(default_factory=list)
    source_event_ids: list[str] = field(default_factory=list)
    created_at: str = field(default_factory=utc_now)
    updated_at: str = field(default_factory=utc_now)
    first_detected_at: str = field(default_factory=utc_now)
    last_detected_at: str = field(default_factory=utc_now)
    occurrence_count: int = 1
    status: str = "active"
    acknowledgement_state: str = "none"
    acknowledged_at: str | None = None
    resolved_at: str | None = None
    snoozed_until: str | None = None
    cooldown_until: str | None = None
    deduplication_key: str = ""
    evidence: dict[str, Any] = field(default_factory=dict)
    recommended_next_step: str = ""
    safety_disclaimer: str = SAFETY_DISCLAIMER
    audit_history: list[dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Alert":
        known = {f.name for f in cls.__dataclass_fields__.values()}  # type: ignore[attr-defined]
        payload = {k: v for k, v in (data or {}).items() if k in known}
        if "metrics" in payload and payload["metrics"] is None:
            payload["metrics"] = []
        if "source_event_ids" in payload and payload["source_event_ids"] is None:
            payload["source_event_ids"] = []
        if "audit_history" in payload and payload["audit_history"] is None:
            payload["audit_history"] = []
        if "evidence" in payload and payload["evidence"] is None:
            payload["evidence"] = {}
        return cls(**payload)


def _parse_iso(ts: str | None) -> float | None:
    if not ts:
        return None
    try:
        from datetime import datetime

        return datetime.fromisoformat(ts.replace("Z", "+00:00")).timestamp()
    except Exception:
        return None


def _add_minutes(iso_ts: str, minutes: float) -> str:
    from datetime import datetime, timedelta, timezone

    try:
        dt = datetime.fromisoformat(iso_ts.replace("Z", "+00:00"))
    except Exception:
        dt = datetime.now(timezone.utc)
    return (dt + timedelta(minutes=minutes)).replace(microsecond=0).isoformat().replace("+00:00", "Z")


class AlertEngine:
    """Create/update/acknowledge/resolve alerts with dedupe, cooldown, escalation."""

    def __init__(
        self,
        store: Any,
        *,
        cooldowns_minutes: dict[str, int] | None = None,
        bus: Any | None = None,
    ) -> None:
        self.store = store
        self.cooldowns = cooldowns_minutes or {
            "informational": 360,
            "watch": 180,
            "warning": 60,
            "urgent": 30,
            "critical": 15,
        }
        self.bus = bus

    def list_alerts(
        self,
        *,
        patient_id: str | None = None,
        status: str | None = None,
        active_only: bool = False,
    ) -> list[dict[str, Any]]:
        items = list(self.store.list_alerts())
        if patient_id:
            items = [a for a in items if a.get("patient_id") == patient_id]
        if status:
            items = [a for a in items if a.get("status") == status]
        if active_only:
            items = [a for a in items if a.get("status") in ("active", "acknowledged", "snoozed")]
        return items

    def get_alert(self, alert_id: str) -> dict[str, Any] | None:
        for a in self.store.list_alerts():
            if a.get("alert_id") == alert_id:
                return a
        return None

    def ingest_evaluation(
        self,
        evaluation: dict[str, Any],
        *,
        patient_id: str = "default-patient",
        source_event_ids: list[str] | None = None,
        now: str | None = None,
    ) -> dict[str, Any] | None:
        """Upsert an alert from a structured rule evaluation result."""
        if not evaluation or not evaluation.get("triggered"):
            return None
        if not isinstance(evaluation, dict):
            return None
        if not evaluation.get("rule_id"):
            return None
        severity = str(evaluation.get("severity") or "watch")
        if severity not in SEVERITY_RANK:
            return None
        now_ts = now or utc_now()
        patient_id = str(patient_id or "default-patient")
        rule_id = str(evaluation.get("rule_id") or "unknown")
        metrics = list(evaluation.get("metrics") or [])
        if evaluation.get("metric") and evaluation["metric"] not in metrics:
            metrics.insert(0, evaluation["metric"])
        dedupe = str(
            evaluation.get("deduplication_key")
            or f"{patient_id}|{rule_id}|{'+'.join(sorted(str(x) for x in metrics))}"
        )
        # Enforce patient prefix on dedupe keys
        if not dedupe.startswith(f"{patient_id}|"):
            dedupe = f"{patient_id}|{dedupe}"

        existing = self._find_open_by_dedupe(dedupe, patient_id)
        if existing:
            return self._update_existing(existing, evaluation, severity, source_event_ids, now_ts)

        # Cooldown: suppress brand-new alert if a recently resolved one shares dedupe
        if self._in_cooldown(dedupe, patient_id, now_ts) and severity != "critical":
            return None

        alert = Alert(
            patient_id=patient_id,
            rule_id=rule_id,
            rule_version=str(evaluation.get("rule_version") or "1.0.0"),
            title=str(evaluation.get("title") or rule_id),
            message=str(evaluation.get("message") or evaluation.get("title") or rule_id),
            severity=severity,
            category=str(evaluation.get("category") or "general"),
            metrics=metrics,
            source_event_ids=list(source_event_ids or evaluation.get("source_event_ids") or []),
            created_at=now_ts,
            updated_at=now_ts,
            first_detected_at=now_ts,
            last_detected_at=now_ts,
            deduplication_key=dedupe,
            evidence=dict(evaluation.get("evidence") or {}),
            recommended_next_step=str(evaluation.get("recommended_next_step") or ""),
            safety_disclaimer=str(evaluation.get("safety_disclaimer") or SAFETY_DISCLAIMER),
            audit_history=[
                {
                    "at": now_ts,
                    "action": "created",
                    "detail": {"severity": severity, "rule_id": rule_id},
                }
            ],
        )
        saved = self.store.upsert_alert(alert.to_dict())
        self._emit("AlertCreated", saved)
        return saved

    def acknowledge(
        self,
        alert_id: str,
        *,
        note: str | None = None,
        now: str | None = None,
    ) -> dict[str, Any]:
        alert = self.get_alert(alert_id)
        if not alert:
            return {"ok": False, "errors": ["alert_not_found"]}
        now_ts = now or utc_now()
        # Critical alerts stay visible (status acknowledged, not dismissed)
        alert["acknowledgement_state"] = "acknowledged"
        alert["acknowledged_at"] = now_ts
        alert["updated_at"] = now_ts
        if alert.get("status") == "active":
            alert["status"] = "acknowledged"
        self._append_audit(alert, now_ts, "acknowledged", {"note": note})
        saved = self.store.upsert_alert(alert)
        self._emit("AlertAcknowledged", saved)
        return {"ok": True, "alert": saved}

    def resolve(
        self,
        alert_id: str,
        *,
        note: str | None = None,
        force: bool = False,
        now: str | None = None,
    ) -> dict[str, Any]:
        alert = self.get_alert(alert_id)
        if not alert:
            return {"ok": False, "errors": ["alert_not_found"]}
        now_ts = now or utc_now()
        severity = alert.get("severity")
        # Critical cannot be silently dismissed — must be acknowledged first unless force (admin/test)
        if severity == "critical" and alert.get("acknowledgement_state") != "acknowledged" and not force:
            return {
                "ok": False,
                "errors": ["critical_requires_acknowledgement"],
                "alert": alert,
            }
        alert["status"] = "resolved"
        alert["resolved_at"] = now_ts
        alert["updated_at"] = now_ts
        cd = int(self.cooldowns.get(str(severity), 30))
        alert["cooldown_until"] = _add_minutes(now_ts, cd)
        self._append_audit(alert, now_ts, "resolved", {"note": note, "force": force})
        saved = self.store.upsert_alert(alert)
        self._emit("AlertResolved", saved)
        return {"ok": True, "alert": saved}

    def snooze(
        self,
        alert_id: str,
        *,
        minutes: int = 60,
        now: str | None = None,
    ) -> dict[str, Any]:
        alert = self.get_alert(alert_id)
        if not alert:
            return {"ok": False, "errors": ["alert_not_found"]}
        if alert.get("severity") == "critical":
            return {"ok": False, "errors": ["critical_cannot_snooze"], "alert": alert}
        now_ts = now or utc_now()
        alert["status"] = "snoozed"
        alert["snoozed_until"] = _add_minutes(now_ts, minutes)
        alert["updated_at"] = now_ts
        self._append_audit(alert, now_ts, "snoozed", {"minutes": minutes})
        saved = self.store.upsert_alert(alert)
        self._emit("AlertSnoozed", saved)
        return {"ok": True, "alert": saved}

    def active_counts(self, patient_id: str | None = None) -> dict[str, int]:
        counts = {s: 0 for s in SEVERITIES}
        for a in self.list_alerts(patient_id=patient_id, active_only=True):
            sev = a.get("severity")
            if sev in counts:
                counts[sev] += 1
        counts["total"] = sum(counts[s] for s in SEVERITIES)
        return counts

    def _find_open_by_dedupe(self, dedupe: str, patient_id: str) -> dict[str, Any] | None:
        for a in self.list_alerts(patient_id=patient_id, active_only=True):
            if a.get("deduplication_key") == dedupe:
                return a
        return None

    def _in_cooldown(self, dedupe: str, patient_id: str, now_ts: str) -> bool:
        now_epoch = _parse_iso(now_ts) or 0.0
        for a in self.store.list_alerts():
            if a.get("patient_id") != patient_id:
                continue
            if a.get("deduplication_key") != dedupe:
                continue
            until = _parse_iso(a.get("cooldown_until"))
            if until is not None and until > now_epoch:
                return True
        return False

    def _update_existing(
        self,
        existing: dict[str, Any],
        evaluation: dict[str, Any],
        severity: str,
        source_event_ids: list[str] | None,
        now_ts: str,
    ) -> dict[str, Any]:
        alert = dict(existing)
        prev = str(alert.get("severity") or "watch")
        escalated = SEVERITY_RANK.get(severity, 0) > SEVERITY_RANK.get(prev, 0)
        alert["occurrence_count"] = int(alert.get("occurrence_count") or 1) + 1
        alert["last_detected_at"] = now_ts
        alert["updated_at"] = now_ts
        alert["evidence"] = dict(evaluation.get("evidence") or alert.get("evidence") or {})
        if source_event_ids:
            ids = list(alert.get("source_event_ids") or [])
            for sid in source_event_ids:
                if sid not in ids:
                    ids.append(sid)
            alert["source_event_ids"] = ids
        if escalated:
            alert["severity"] = severity
            # Re-open visibility for escalations
            if alert.get("status") in ("acknowledged", "snoozed"):
                alert["status"] = "active"
                alert["acknowledgement_state"] = "none"
            self._append_audit(
                alert,
                now_ts,
                "escalated",
                {"from": prev, "to": severity, "occurrence_count": alert["occurrence_count"]},
            )
            saved = self.store.upsert_alert(alert)
            self._emit("AlertEscalated", saved)
            return saved
        self._append_audit(
            alert,
            now_ts,
            "redetected",
            {"occurrence_count": alert["occurrence_count"], "severity": alert.get("severity")},
        )
        # Persistence escalation: same severity but many repeats → bump one level
        if int(alert["occurrence_count"]) >= 5 and prev in ("watch", "warning", "informational"):
            next_sev = SEVERITIES[min(SEVERITY_RANK[prev] + 1, len(SEVERITIES) - 1)]
            alert["severity"] = next_sev
            alert["status"] = "active"
            self._append_audit(
                alert,
                now_ts,
                "escalated_persistence",
                {"from": prev, "to": next_sev},
            )
            saved = self.store.upsert_alert(alert)
            self._emit("AlertEscalated", saved)
            return saved
        saved = self.store.upsert_alert(alert)
        self._emit("AlertUpdated", saved)
        return saved

    def _append_audit(self, alert: dict[str, Any], at: str, action: str, detail: dict[str, Any]) -> None:
        hist = list(alert.get("audit_history") or [])
        hist.append({"at": at, "action": action, "detail": detail})
        alert["audit_history"] = hist

    def _emit(self, name: str, payload: dict[str, Any]) -> None:
        if self.bus is not None:
            try:
                self.bus.publish(name, {"alert_id": payload.get("alert_id"), "severity": payload.get("severity")})
            except Exception:
                pass
