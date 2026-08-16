"""HC-312A/B — Automatic Medical Record Intake package.

Provides a narrowly-scoped, unattended intake layer that watches a controlled
HealthChecker-owned directory, claims files atomically, delegates each file to
the canonical ImportService/ImportPipeline, and moves each file to a terminal
lifecycle state (completed or quarantine).

HC-312B extends this with periodic scheduling via the existing MonitoringScheduler
(HC-302) and a Windows Task Scheduler inert-template (same pattern as HC-306E).

Public API
----------
IntakeConfig       — configuration dataclass
IntakeRunner       — one-shot runner (scan → claim → process → summarise)
IntakeWatcher      — periodic wrapper using MonitoringScheduler (HC-312B)
LifecycleState     — intake lifecycle states
scheduled_intake   — HC-312B policy constants and task settings contract
"""

from __future__ import annotations

from backend.health_vault.intake.intake_config import IntakeConfig, get_default_intake_config
from backend.health_vault.intake.lifecycle import LifecycleState
from backend.health_vault.intake.runner import IntakeRunner
from backend.health_vault.intake.watcher import IntakeWatcher

__all__ = [
    "IntakeConfig",
    "IntakeRunner",
    "IntakeWatcher",
    "LifecycleState",
    "get_default_intake_config",
]
