"""Generic medical document + measurement models (FHIR-ready naming)."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any
from uuid import uuid4


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


@dataclass
class MedicalDocument:
    """Generic MedicalDocument — not Samsung-specific. Maps toward DocumentReference."""

    id: str = field(default_factory=lambda: str(uuid4()))
    patient_id: str = "default-patient"
    document_type: str = "unknown"
    source_system: str = "unknown"
    acquisition_method: str = "manual_upload"
    original_filename: str | None = None
    storage_uri: str | None = None
    sha256: str | None = None
    imported_at: str = field(default_factory=utc_now)
    measured_at: str | None = None
    parser_version: str | None = None
    parser_confidence: float | None = None
    status: str = "imported"
    tags: list[str] = field(default_factory=list)
    fhir_resource: str = "DocumentReference"
    interpretation: str | None = None
    mime_type: str | None = None
    size_bytes: int | None = None
    duplicate_of: str | None = None
    # Provenance (HC-201F): original_document_verified | user_reported |
    # historical_summary | wearable_screenshot | wearable_pdf
    provenance: str | None = None
    # HC-201G batch / multi-image grouping (non-destructive)
    batch_id: str | None = None
    group_id: str | None = None
    sequence_number: int | None = None
    page_number: int | None = None
    group_title: str | None = None
    # HC-201H classification + dating
    primary_category: str | None = None
    secondary_categories: list[str] = field(default_factory=list)
    classification_confidence: float | None = None
    classification_method: str | None = None
    classification_version: str | None = None
    requires_review: bool = False
    report_date: str | None = None
    file_capture_date: str | None = None
    date_confidence: float | None = None
    date_source: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class Measurement:
    """Universal Measurement entity — maps toward FHIR Observation."""

    measurement_id: str = field(default_factory=lambda: str(uuid4()))
    document_id: str | None = None
    category: str = "Uncategorized"
    metric: str = "unknown"
    value: Any = None
    units: str | None = None
    reference_range: str | None = None
    abnormal_flag: str | None = None
    confidence: float | None = None
    measured_at: str | None = None
    fhir_resource: str = "Observation"
    original_metric: str | None = None
    original_value: Any = None
    original_units: str | None = None
    original_analyte_name: str | None = None
    observation_class: str | None = None
    specimen: str | None = None
    context: str | None = None
    source_facility: str | None = None
    canonical_reference_range: str | None = None
    conversion_flag: str | None = None
    unit_compatible: bool = True
    normalization_version: str | None = None
    semantics_version: str | None = None
    provenance: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


# Extensible metric catalog (future measurements register here)
METRIC_CATALOG: dict[str, dict[str, Any]] = {
    "ecg_result": {"category": "ECG", "units": None},
    "heart_rhythm": {"category": "Heart Rhythm", "units": None},
    "heart_rate": {"category": "Heart Rate", "units": "bpm"},
    "average_hr": {"category": "Heart Rate", "units": "bpm"},
    "resting_hr": {"category": "Resting HR", "units": "bpm"},
    "hrv": {"category": "HRV", "units": "ms"},
    "sleep_score": {"category": "Sleep Score", "units": "score"},
    "sleep_duration": {"category": "Sleep Duration", "units": "h"},
    "deep_sleep": {"category": "Deep Sleep", "units": "h"},
    "rem_sleep": {"category": "REM", "units": "h"},
    "respiratory_rate": {"category": "Respiratory Rate", "units": "/min"},
    "skin_temperature": {"category": "Skin Temperature", "units": "C"},
    "energy_score": {"category": "Energy Score", "units": "score"},
    "creatinine": {"category": "Creatinine", "units": "umol/L"},
    "egfr": {"category": "eGFR", "units": "mL/min/1.73m2"},
    "protein": {"category": "Protein", "units": None},
    "uacr": {"category": "UACR", "units": "mg/mmol"},
    "potassium": {"category": "Potassium", "units": "mmol/L"},
    "glucose": {"category": "Glucose", "units": "mg/dL"},
    "hba1c": {"category": "HbA1c", "units": "%"},
    "cgm_average": {"category": "CGM", "units": "mg/dL"},
    "cgm_time_in_range": {"category": "CGM", "units": "%"},
    "cgm_gmi": {"category": "CGM", "units": "%"},
    "systolic": {"category": "Systolic", "units": "mmHg"},
    "diastolic": {"category": "Diastolic", "units": "mmHg"},
    "weight": {"category": "Weight", "units": "kg"},
    "bmi": {"category": "BMI", "units": "kg/m2"},
    "sleep_latency": {"category": "Sleep Latency", "units": "h"},
    "urea": {"category": "Urea", "units": "mmol/L"},
    "skin_temperature_deviation": {"category": "Skin Temperature", "units": "C"},
    "medication": {"category": "Medication", "units": None},
    "diagnosis": {"category": "Diagnosis", "units": None},
    "oxygen_saturation": {"category": "Respiratory", "units": "%"},
    "pulse": {"category": "Heart Rate", "units": "bpm"},
    "steps": {"category": "Activity", "units": "count"},
    "activity_minutes": {"category": "Activity", "units": "min"},
    "exercise_minutes": {"category": "Activity", "units": "min"},
    "spo2": {"category": "Respiratory", "units": "%"},
    "ldl": {"category": "Lipids", "units": "mg/dL"},
    "ldl_c": {"category": "Lipids", "units": "mg/dL"},
    "hdl": {"category": "Lipids", "units": "mg/dL"},
    "triglycerides": {"category": "Lipids", "units": "mg/dL"},
}

PROVENANCE_VALUES = (
    "original_document_verified",
    "user_reported",
    "historical_summary",
    "wearable_screenshot",
    "wearable_pdf",
    "health_connect_sync",
    "libre_authorized_live",
    "continuous_monitoring",
    "simulated_test_only",
)

PROVENANCE_CONFIDENCE = {
    "original_document_verified": 0.95,
    "wearable_pdf": 0.9,
    "wearable_screenshot": 0.85,
    "historical_summary": 0.7,
    "user_reported": 0.65,
}


def create_measurement(**kwargs: Any) -> Measurement:
    metric = str(kwargs.get("metric") or "unknown")
    meta = METRIC_CATALOG.get(metric, {})
    return Measurement(
        measurement_id=kwargs.get("measurement_id") or str(uuid4()),
        document_id=kwargs.get("document_id"),
        category=kwargs.get("category") or meta.get("category") or "Uncategorized",
        metric=metric,
        value=kwargs.get("value"),
        units=kwargs["units"] if "units" in kwargs else meta.get("units"),
        reference_range=kwargs.get("reference_range"),
        abnormal_flag=kwargs.get("abnormal_flag"),
        confidence=kwargs.get("confidence"),
        measured_at=kwargs.get("measured_at") or kwargs.get("collection_timestamp"),
        fhir_resource=kwargs.get("fhir_resource") or "Observation",
        original_metric=kwargs.get("original_metric"),
        original_value=kwargs.get("original_value"),
        original_units=kwargs.get("original_units"),
        original_analyte_name=kwargs.get("original_analyte_name") or kwargs.get("analyte") or kwargs.get("name"),
        observation_class=kwargs.get("observation_class"),
        specimen=kwargs.get("specimen"),
        context=kwargs.get("context") or kwargs.get("collection_context"),
        source_facility=kwargs.get("source_facility"),
        canonical_reference_range=kwargs.get("canonical_reference_range"),
        conversion_flag=kwargs.get("conversion_flag"),
        unit_compatible=bool(kwargs["unit_compatible"]) if "unit_compatible" in kwargs else True,
        normalization_version=kwargs.get("normalization_version"),
        semantics_version=kwargs.get("semantics_version"),
        provenance=kwargs.get("provenance"),
    )


def register_metric(metric: str, **meta: Any) -> None:
    METRIC_CATALOG[metric] = {**METRIC_CATALOG.get(metric, {}), **meta}


DOCUMENT_TYPES = (
    "samsung_health_ecg",
    "samsung_health_sleep",
    "samsung_health_energy_score",
    "galaxy_watch_report",
    "blood_pressure_screenshot",
    "blood_glucose",
    "libre_cgm_report",
    "laboratory_pdf",
    "hospital_report",
    "medication_report",
    "imaging_report",
    "ai_assisted_import",
    "json_measurements",
    "continuous_monitoring_observation",
    "unknown",
)


def classify_document_type(filename: str | None, mime: str | None, hint: str | None = None) -> str:
    if hint and hint in DOCUMENT_TYPES:
        return hint
    name = (filename or "").lower()
    type_ = (mime or "").lower()
    if "ecg" in name or "ekg" in name:
        return "samsung_health_ecg"
    if "sleep" in name:
        return "samsung_health_sleep"
    if "energy" in name:
        return "samsung_health_energy_score"
    if "galaxy" in name or "watch" in name:
        return "galaxy_watch_report"
    if "libre" in name or "cgm" in name:
        return "libre_cgm_report"
    if "glucose" in name or name.endswith("bg"):
        return "blood_glucose"
    if "medication" in name or "rx" in name:
        return "medication_report"
    if any(x in name for x in ("imaging", "xray", "mri", "ct")):
        return "imaging_report"
    if "bp" in name or "blood_pressure" in name or "pressure" in name:
        return "blood_pressure_screenshot"
    if "pdf" in type_ or name.endswith(".pdf"):
        if any(x in name for x in ("lab", "lifelabs", "blood")):
            return "laboratory_pdf"
        return "hospital_report"
    if "json" in type_ or name.endswith(".json"):
        return "json_measurements"
    return "unknown"


@dataclass(frozen=True)
class EvidenceReference:
    """Explicit trace to original source documents or measurements in the vault."""

    source_type: str        # 'document' | 'measurement' | 'wearable_sync' | 'external_ai'
    document_id: str | None = None
    measurement_id: str | None = None
    sha256: str | None = None  # Original file integrity verification

    def to_dict(self) -> dict[str, Any]:
        return {k: v for k, v in asdict(self).items() if v is not None}


@dataclass(frozen=True)
class ConfidenceScore:
    """Standardized confidence scoring across parsing and evaluation methods."""

    value: float            # 0.0 to 1.0
    method: str             # 'rule_based' | 'statistical_model' | 'llm_extraction' | 'user_reported'
    version: str            # Parser or algorithm version

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class HealthMetric:
    """Computed physiological trends and summary indicators over time."""

    patient_id: str
    metric: str             # e.g., 'egfr_slope', 'hba1c_tracker', 'blood_pressure_variability'
    value: Any              # Scalar or structure (e.g. {'slope': -0.15, 'interval_days': 90})
    units: str | None
    measured_at: str        # ISO8601 UTC timestamp
    confidence: ConfidenceScore
    evidence: list[EvidenceReference] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "patient_id": self.patient_id,
            "metric": self.metric,
            "value": self.value,
            "units": self.units,
            "measured_at": self.measured_at,
            "confidence": self.confidence.to_dict(),
            "evidence": [e.to_dict() for e in self.evidence],
        }


@dataclass
class HealthEvent:
    """A distinct milestone on the chronological health timeline (maps toward FHIR Procedure/Condition)."""

    patient_id: str
    event_id: str           # UUID
    event_type: str         # 'medication_change' | 'abnormal_lab' | 'wearable_milestone' | 'procedure'
    summary: str
    measured_at: str        # ISO8601 UTC timestamp
    severity: str           # 'normal' | 'borderline' | 'abnormal' | 'critical'
    provenance: str         # Origin reference (e.g. 'libre_live', 'galaxy_watch', 'lifelabs')
    payload: dict[str, Any] = field(default_factory=dict)  # Extensible context
    evidence: list[EvidenceReference] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "patient_id": self.patient_id,
            "event_id": self.event_id,
            "event_type": self.event_type,
            "summary": self.summary,
            "measured_at": self.measured_at,
            "severity": self.severity,
            "provenance": self.provenance,
            "payload": self.payload,
            "evidence": [e.to_dict() for e in self.evidence],
        }


@dataclass
class HealthObservation:
    """Derived medical observation (maps toward FHIR ClinicalImpression)."""

    patient_id: str
    observation_id: str     # UUID
    category: str           # 'renal' | 'glycemic' | 'cardiovascular' | 'sleep' | 'general'
    metric: str | None      # Associated metric (e.g., 'egfr', 'hba1c')
    fact: str               # Direct evidence-based statement (e.g., "eGFR decreased from 92 to 84 mL/min/1.73m2")
    interpretation: str     # Clinical contextualization (e.g., "Filtration rate shows worsening pattern")
    measured_at: str        # ISO8601 UTC timestamp
    confidence: ConfidenceScore
    evidence: list[EvidenceReference] = field(default_factory=list)
    safety_boundary_disclaimer: str = "Observational findings only — not a medical diagnosis. Consult a doctor."
    explanation: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "patient_id": self.patient_id,
            "observation_id": self.observation_id,
            "category": self.category,
            "metric": self.metric,
            "fact": self.fact,
            "interpretation": self.interpretation,
            "measured_at": self.measured_at,
            "confidence": self.confidence.to_dict(),
            "evidence": [e.to_dict() for e in self.evidence],
            "safety_boundary_disclaimer": self.safety_boundary_disclaimer,
            "explanation": self.explanation,
            "observation": f"{self.fact} {self.interpretation} (observational)",
            "kind": "observational",
            "diagnostic": False,
        }


@dataclass
class UserAccount:
    """HC-318B immutable identity and password-lifecycle state."""

    user_id: str
    name: str
    email_identifier: str
    password_hash: str
    password_changed_at: str | None
    password_expiry_date: str | None
    must_change_password: bool
    account_status: str
    role: str
    password_version: int = 1

    def to_dict(self, *, include_secret: bool = False) -> dict[str, Any]:
        data = asdict(self)
        if not include_secret:
            data.pop("password_hash", None)
        return data

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "UserAccount":
        return cls(
            user_id=str(data["user_id"]),
            name=str(data.get("name") or ""),
            email_identifier=str(data.get("email_identifier") or data["user_id"]),
            password_hash=str(data.get("password_hash") or ""),
            password_changed_at=data.get("password_changed_at"),
            password_expiry_date=data.get("password_expiry_date"),
            must_change_password=bool(data.get("must_change_password")),
            account_status=str(data.get("account_status") or "disabled"),
            role=str(data.get("role") or "user"),
            password_version=int(data.get("password_version", 1)),
        )


@dataclass
class UserDashboardPreferences:
    """Configurable user preferences for theme, widget layout order and priorities."""

    theme: str = "light"  # 'light' | 'dark'
    widget_order: list[str] = field(default_factory=lambda: [
        "status_summary", "key_observations", "trends_widget", "timeline_widget", "import_wizard"
    ])
    visible_widgets: list[str] = field(default_factory=lambda: [
        "status_summary", "key_observations", "trends_widget", "timeline_widget", "import_wizard"
    ])
    priority_metric: str | None = None
    reporting_region: str | None = None
    unit_overrides: dict[str, str] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> UserDashboardPreferences:
        return cls(
            theme=data.get("theme", "light"),
            widget_order=list(data.get("widget_order") or [
                "status_summary", "key_observations", "trends_widget", "timeline_widget", "import_wizard"
            ]),
            visible_widgets=list(data.get("visible_widgets") or [
                "status_summary", "key_observations", "trends_widget", "timeline_widget", "import_wizard"
            ]),
            priority_metric=data.get("priority_metric"),
            reporting_region=(str(data["reporting_region"]).upper() if data.get("reporting_region") else None),
            unit_overrides=dict(data.get("unit_overrides") or {}),
        )


@dataclass
class DashboardWidget:
    """Individual serialized dashboard widget payload."""

    widget_id: str
    title: str
    widget_type: str
    priority: int
    payload: dict[str, Any]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class DashboardSummary:
    """Aggregated user landing summary payload."""

    patient_id: str
    overall_status: str
    active_warnings_count: int
    widgets: list[DashboardWidget] = field(default_factory=list)
    display_name: str | None = None

    def to_dict(self) -> dict[str, Any]:
        payload = {
            "patient_id": self.patient_id,
            "overall_status": self.overall_status,
            "active_warnings_count": self.active_warnings_count,
            "widgets": [w.to_dict() for w in self.widgets],
        }
        # Consumer presentation only — never invent a name; omit when unset.
        if self.display_name:
            payload["display_name"] = self.display_name
        return payload


from enum import Enum

class RecordStatus(str, Enum):
    INCOMING = "incoming"
    PROCESSING = "processing"
    IMPORTED = "imported"
    REQUIRES_REVIEW = "requires_review"
    QUARANTINED = "quarantined"
    DUPLICATE = "duplicate"
    FAILED = "failed"


class RecordCategory(str, Enum):
    BLOOD_PRESSURE = "blood_pressure"
    SLEEP = "sleep"
    ECG = "ecg_cardiology"
    CARDIOVASCULAR = "cardiovascular"
    ACTIVITY = "activity_fitness"
    RESPIRATORY = "respiratory_oxygen"
    GLUCOSE = "glucose_diabetes"
    KIDNEY = "kidney_renal"
    LABS = "laboratory_report"
    WEIGHT = "weight_body_metrics"
    MEDICATION = "medication"
    IMAGING = "imaging"
    CLINICAL_DOCUMENT = "hospital_clinical_report"
    OTHER = "other"


@dataclass
class RecordProcessingEvent:
    event_id: str
    document_id: str
    status: RecordStatus
    timestamp: str
    event_type: str
    source: str
    details: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "event_id": self.event_id,
            "document_id": self.document_id,
            "status": self.status.value if isinstance(self.status, RecordStatus) else self.status,
            "timestamp": self.timestamp,
            "event_type": self.event_type,
            "source": self.source,
            "details": dict(self.details),
        }


@dataclass
class RecordLinkage:
    document_id: str
    extracted_measurements: list[dict[str, Any]] = field(default_factory=list)
    timeline_events: list[dict[str, Any]] = field(default_factory=list)
    trend_references: list[dict[str, Any]] = field(default_factory=list)
    ai_observations: list[dict[str, Any]] = field(default_factory=list)
    evidence_references: list[dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class HealthRecord:
    document_id: str
    patient_id: str
    original_filename: str
    primary_category: RecordCategory
    status: RecordStatus
    imported_at: str
    measured_at: str | None = None
    size_bytes: int | None = None
    metrics_count: int = 0
    metadata: dict[str, Any] = field(default_factory=dict)
    source_provenance: dict[str, Any] = field(default_factory=dict)
    linkage: RecordLinkage | None = None
    lifecycle: list[RecordProcessingEvent] = field(default_factory=list)

    def to_summary_dict(self) -> dict[str, Any]:
        meta = self.metadata or {}
        return {
            "document_id": self.document_id,
            "original_filename": self.original_filename,
            "display_title": meta.get("display_title") or self.original_filename,
            "consumer_category": meta.get("consumer_category"),
            "consumer_category_label": meta.get("consumer_category_label"),
            "technical_filename": meta.get("technical_filename") or self.original_filename,
            "primary_category": self.primary_category.value if isinstance(self.primary_category, RecordCategory) else self.primary_category,
            "status": self.status.value if isinstance(self.status, RecordStatus) else self.status,
            "measured_at": self.measured_at,
            "imported_at": self.imported_at,
            "size_bytes": self.size_bytes,
            "metrics_count": self.metrics_count,
            "source_system": self.source_provenance.get("source_system"),
            "provenance": self.source_provenance.get("provenance"),
            "document_type": meta.get("document_type"),
        }

    def to_detail_dict(self) -> dict[str, Any]:
        linkage = self.linkage or RecordLinkage(document_id=self.document_id)
        return {
            **self.to_summary_dict(),
            "metadata": dict(self.metadata),
            "source_provenance": dict(self.source_provenance),
            "extracted_measurements": list(linkage.extracted_measurements),
            "timeline_events": list(linkage.timeline_events),
            "trend_references": list(linkage.trend_references),
            "ai_observations": list(linkage.ai_observations),
            "evidence_references": list(linkage.evidence_references),
            "lifecycle": [event.to_dict() for event in self.lifecycle],
        }

    def to_dict(self) -> dict[str, Any]:
        """Explicit full-record serialization retained for service callers."""
        return self.to_detail_dict()
