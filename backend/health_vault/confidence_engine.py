"""Multi-factor confidence engine for Health Vault imports."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any


@dataclass
class ConfidenceBreakdown:
    extraction_confidence: float = 0.0
    validation_confidence: float = 0.0
    clinical_confidence: float = 0.0
    storage_confidence: float = 0.0
    overall_confidence: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class ConfidenceEngine:
    """
    Combines extraction, validation, clinical flag coverage, and storage integrity
    into an overall confidence score in [0, 1].
    """

    def compute(
        self,
        *,
        extraction: float = 0.0,
        validation: float = 0.0,
        clinical: float = 0.0,
        storage: float = 0.0,
        weights: dict[str, float] | None = None,
    ) -> ConfidenceBreakdown:
        w = {
            "extraction": 0.30,
            "validation": 0.30,
            "clinical": 0.20,
            "storage": 0.20,
        }
        if weights:
            w.update(weights)
        e = _clamp(extraction)
        v = _clamp(validation)
        c = _clamp(clinical)
        s = _clamp(storage)
        overall = (
            e * w["extraction"]
            + v * w["validation"]
            + c * w["clinical"]
            + s * w["storage"]
        )
        return ConfidenceBreakdown(
            extraction_confidence=round(e, 3),
            validation_confidence=round(v, 3),
            clinical_confidence=round(c, 3),
            storage_confidence=round(s, 3),
            overall_confidence=round(_clamp(overall), 3),
        )

    def clinical_from_flags(self, flags: list[str | None]) -> float:
        if not flags:
            return 0.4
        known = [f for f in flags if f and f != "Unknown"]
        return round(min(1.0, 0.5 + 0.5 * (len(known) / max(len(flags), 1))), 3)


def _clamp(v: float) -> float:
    try:
        x = float(v)
    except (TypeError, ValueError):
        return 0.0
    return max(0.0, min(1.0, x))
