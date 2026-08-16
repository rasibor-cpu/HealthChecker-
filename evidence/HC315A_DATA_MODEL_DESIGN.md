# HC-315A: Health Intelligence Data Model Design

This document details the data model design for the Health Intelligence Engine. The models are designed to map toward standard clinical schemas (FHIR-ready) while fully preserving HealthChecker's architectural invariants:
1. **Multi-User Isolation**: Storing `patient_id` on all top-level entities.
2. **Encrypted Source of Truth**: All models serialize directly into the `VaultStore` index structure. No duplicate databases or unencrypted storage will be introduced.
3. **Traceable Evidence & Provenance**: Strict reference structures back to original measurements and documents in the vault.

---

## 1. Data Model Specifications

### 1.1 EvidenceReference
Represents a traceable cryptographic link back to the source data in the vault.
```python
@dataclass(frozen=True)
class EvidenceReference:
    """Explicit trace to original source documents or measurements in the vault."""
    source_type: str        # 'document' | 'measurement' | 'wearable_sync' | 'external_ai'
    document_id: str | None = None
    measurement_id: str | None = None
    sha256: str | None = None  # Original file integrity verification
```

### 1.2 ConfidenceScore
Encapsulates standard metrics for data and classification reliability.
```python
@dataclass(frozen=True)
class ConfidenceScore:
    """Standardized confidence scoring across parsing and evaluation methods."""
    value: float            # 0.0 to 1.0
    method: str             # 'rule_based' | 'statistical_model' | 'llm_extraction' | 'user_reported'
    version: str            # Parser or algorithm version
```

### 1.3 HealthMetric
Represents derived physiological values calculated across data points over time.
```python
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
```

### 1.4 HealthEvent
Represents a chronological milestone in the patient's longitudinal timeline.
```python
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
```

### 1.5 HealthObservation
Represents explainable insights derived from metrics and history, strictly separating facts from interpretation.
```python
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
```

---

## 2. Vault Index Integration

To maintain the architectural principle that the encrypted vault is the sole source of truth, these entities will serialize directly into the index managed by `VaultStore`:

```json
{
  "schema_version": "hc.health_vault.v1",
  "documents": [],
  "measurements": [],
  "timeline_events": [
    {
      "patient_id": "default-patient",
      "event_id": "uuid-1234",
      "event_type": "abnormal_lab",
      "summary": "Creatinine level flagged Critical (120 umol/L)",
      "measured_at": "2026-08-16T19:00:00Z",
      "severity": "critical",
      "provenance": "lifelabs",
      "payload": {
        "metric": "creatinine",
        "value": 120.0,
        "reference_range": "45-97"
      },
      "evidence": [
        {
          "source_type": "measurement",
          "document_id": "doc-uuid-5678",
          "measurement_id": "meas-uuid-9999",
          "sha256": "HASH123..."
        }
      ]
    }
  ],
  "health_intelligence": {
    "observations": [
      {
        "patient_id": "default-patient",
        "observation_id": "uuid-abcd",
        "category": "renal",
        "metric": "egfr",
        "fact": "eGFR decreased from 92 to 84 mL/min/1.73m2",
        "interpretation": "Filtration rate shows worsening pattern",
        "measured_at": "2026-08-16T19:00:00Z",
        "confidence": {
          "value": 0.9,
          "method": "rule_based",
          "version": "1.0.0"
        },
        "evidence": [
          {
            "source_type": "measurement",
            "document_id": "doc-uuid-5678",
            "measurement_id": "meas-uuid-8888"
          }
        ],
        "safety_boundary_disclaimer": "Observational findings only — not a medical diagnosis. Consult a doctor."
      }
    ],
    "disclaimer": "Observational intelligence only — not a medical diagnosis."
  }
}
```

---

## 3. Testing Plan Draft

Before implementing these models in python, the following tests will be added:
1. **Model Serialization & Deserialization Tests**: Check that all dataclasses export to JSON-safe dictionary structures and parse back cleanly without dropping fields or losing type info.
2. **Multi-User Partition Tests**: Verify that methods retrieving events and observations strictly filter based on `patient_id` and raise exceptions or fail closed on mismatched context.
3. **Traceability Verification**: Assert that observations cannot be created without at least one valid `EvidenceReference` entry.
4. **Safety Disclaimer Check**: Test that `HealthObservation` initializes with the mandatory medical safety disclaimer.
