"""Dedicated measurement validation layer."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any

from backend.health_vault.clinical_rules import ClinicalRulesEngine
from backend.health_vault.models import METRIC_CATALOG, Measurement, utc_now


@dataclass
class ValidationIssue:
    code: str
    severity: str  # info | warning | error
    message: str
    measurement_id: str | None = None
    metric: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class ValidationResult:
    ok: bool
    confidence: float
    issues: list[ValidationIssue] = field(default_factory=list)
    validated_at: str = field(default_factory=utc_now)
    measurement_count: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "confidence": self.confidence,
            "issues": [i.to_dict() for i in self.issues],
            "validated_at": self.validated_at,
            "measurement_count": self.measurement_count,
        }


class ValidationEngine:
    def __init__(self, rules: ClinicalRulesEngine | None = None) -> None:
        self.rules = rules or ClinicalRulesEngine()

    def validate(
        self,
        measurements: list[Any],
        *,
        document_measured_at: str | None = None,
        existing_fingerprints: set[str] | None = None,
    ) -> ValidationResult:
        issues: list[ValidationIssue] = []
        fingerprints = existing_fingerprints or set()
        seen_local: set[str] = set()
        validish = 0

        for m in measurements:
            mid, metric, value, units, measured_at = self._parts(m)
            if value is None or value == "":
                issues.append(
                    ValidationIssue(
                        code="missing_value",
                        severity="warning",
                        message=f"Missing value for {metric}",
                        measurement_id=mid,
                        metric=metric,
                    )
                )
                continue

            catalog = METRIC_CATALOG.get(metric or "", {})
            expected_units = catalog.get("units")
            if expected_units and units and units != expected_units:
                issues.append(
                    ValidationIssue(
                        code="unit_mismatch",
                        severity="warning",
                        message=f"Unexpected units for {metric}: {units} (expected {expected_units})",
                        measurement_id=mid,
                        metric=metric,
                    )
                )

            try:
                num = float(value)
            except (TypeError, ValueError):
                # Non-numeric allowed for qualitative metrics
                if metric in {"ecg_result", "heart_rhythm", "protein"}:
                    validish += 1
                else:
                    issues.append(
                        ValidationIssue(
                            code="non_numeric",
                            severity="warning",
                            message=f"Non-numeric value for {metric}",
                            measurement_id=mid,
                            metric=metric,
                        )
                    )
                continue

            spec = (self.rules.rules.get("metrics") or {}).get(metric or "")
            if spec:
                if spec.get("impossible_below") is not None and num < float(spec["impossible_below"]):
                    issues.append(
                        ValidationIssue(
                            code="impossible_value",
                            severity="error",
                            message=f"Impossible low value for {metric}: {num}",
                            measurement_id=mid,
                            metric=metric,
                        )
                    )
                    continue
                if spec.get("impossible_above") is not None and num > float(spec["impossible_above"]):
                    issues.append(
                        ValidationIssue(
                            code="impossible_value",
                            severity="error",
                            message=f"Impossible high value for {metric}: {num}",
                            measurement_id=mid,
                            metric=metric,
                        )
                    )
                    continue

            fp = f"{metric}|{value}|{measured_at or document_measured_at or ''}"
            if fp in seen_local or fp in fingerprints:
                issues.append(
                    ValidationIssue(
                        code="duplicate_measurement",
                        severity="warning",
                        message=f"Duplicate measurement fingerprint for {metric}",
                        measurement_id=mid,
                        metric=metric,
                    )
                )
            seen_local.add(fp)

            if document_measured_at and measured_at and measured_at > document_measured_at:
                # Soft check only — clocks may differ
                issues.append(
                    ValidationIssue(
                        code="timestamp_order",
                        severity="info",
                        message="Measurement timestamp after document import/measured_at",
                        measurement_id=mid,
                        metric=metric,
                    )
                )

            validish += 1

        total = max(len(measurements), 1)
        errors = sum(1 for i in issues if i.severity == "error")
        warnings = sum(1 for i in issues if i.severity == "warning")
        confidence = max(0.0, min(1.0, (validish / total) - 0.15 * errors - 0.05 * warnings))
        ok = errors == 0
        return ValidationResult(
            ok=ok,
            confidence=round(confidence, 3),
            issues=issues,
            measurement_count=len(measurements),
        )

    @staticmethod
    def _parts(m: Any) -> tuple[str | None, str | None, Any, str | None, str | None]:
        if isinstance(m, Measurement):
            return m.measurement_id, m.metric, m.value, m.units, m.measured_at
        if isinstance(m, dict):
            return (
                m.get("measurement_id"),
                m.get("metric"),
                m.get("value"),
                m.get("units"),
                m.get("measured_at"),
            )
        return None, None, None, None, None
