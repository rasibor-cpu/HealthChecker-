"""
HC-302 — Continuous Health Monitoring foundation.

Additive layer for connector-based observation ingestion, freshness monitoring,
and status reporting. Persists through Health Vault. Reuses HC-301 AlertEngine
for alert lifecycle. Never silently falls back to simulated production readings.
"""

from backend.health_vault.monitoring.bridge import ContinuousMonitoringBridge
from backend.health_vault.monitoring.observation import (
    ACQUISITION_MODES,
    FRESHNESS_STATUSES,
    CanonicalObservation,
)

__all__ = [
    "ContinuousMonitoringBridge",
    "CanonicalObservation",
    "ACQUISITION_MODES",
    "FRESHNESS_STATUSES",
]
