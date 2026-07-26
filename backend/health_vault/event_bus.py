"""Lightweight in-process publish/subscribe event bus for Health Vault."""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from typing import Any, Callable
from uuid import uuid4

from backend.health_vault.models import utc_now


# Canonical event names
DOCUMENT_RECEIVED = "DocumentReceived"
PARSER_SELECTED = "ParserSelected"
OCR_COMPLETED = "OCRCompleted"
MEASUREMENTS_EXTRACTED = "MeasurementsExtracted"
VALIDATION_COMPLETED = "ValidationCompleted"
DUPLICATE_DETECTED = "DuplicateDetected"
DOCUMENT_STORED = "DocumentStored"
MEASUREMENT_STORED = "MeasurementStored"
TIMELINE_UPDATED = "TimelineUpdated"
TREND_UPDATED = "TrendUpdated"
DOCTOR_REPORT_UPDATED = "DoctorReportUpdated"
PARSER_FAILED = "ParserFailed"
IMPORT_COMPLETED = "ImportCompleted"
IMPORT_FAILED = "ImportFailed"
# HC-301 Guardian events
ALERT_CREATED = "AlertCreated"
ALERT_UPDATED = "AlertUpdated"
ALERT_ESCALATED = "AlertEscalated"
ALERT_ACKNOWLEDGED = "AlertAcknowledged"
ALERT_RESOLVED = "AlertResolved"
ALERT_SNOOZED = "AlertSnoozed"
GUARDIAN_EVALUATED = "GuardianEvaluated"
GUARDIAN_EVALUATION_FAILED = "GuardianEvaluationFailed"
CGM_SENSOR_REGISTERED = "CGMSensorRegistered"
CGM_SENSOR_ACTIVATED = "CGMSensorActivated"
CGM_SENSOR_FAILED = "CGMSensorFailed"
CGM_INVENTORY_UPDATED = "CGMInventoryUpdated"
DATA_GAP_DETECTED = "DataGapDetected"


@dataclass
class VaultEvent:
    name: str
    payload: dict[str, Any] = field(default_factory=dict)
    event_id: str = field(default_factory=lambda: str(uuid4()))
    at: str = field(default_factory=utc_now)

    def to_dict(self) -> dict[str, Any]:
        return {
            "event_id": self.event_id,
            "name": self.name,
            "at": self.at,
            "payload": dict(self.payload),
        }


class EventBus:
    """Simple synchronous pub/sub — replaceable later with async/queue backends."""

    def __init__(self) -> None:
        self._subs: dict[str, list[Callable[[VaultEvent], None]]] = defaultdict(list)
        self.history: list[VaultEvent] = []

    def subscribe(self, name: str, handler: Callable[[VaultEvent], None]) -> None:
        self._subs[name].append(handler)

    def unsubscribe(self, name: str, handler: Callable[[VaultEvent], None]) -> None:
        if handler in self._subs.get(name, []):
            self._subs[name].remove(handler)

    def publish(self, name: str, payload: dict[str, Any] | None = None) -> VaultEvent:
        event = VaultEvent(name=name, payload=dict(payload or {}))
        self.history.append(event)
        for handler in list(self._subs.get(name, [])):
            try:
                handler(event)
            except Exception:
                # Handlers must not break the pipeline.
                continue
        for handler in list(self._subs.get("*", [])):
            try:
                handler(event)
            except Exception:
                continue
        return event

    def clear_history(self) -> None:
        self.history.clear()


_DEFAULT_BUS = EventBus()


def get_event_bus() -> EventBus:
    return _DEFAULT_BUS
