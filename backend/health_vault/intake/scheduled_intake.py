"""HC-312B — Scheduled intake policy constants and task configuration.

Mirrors the HC-306E-R2 pattern (scheduled_host.py) for the automatic
medical-record intake task:

- Windows Task Scheduler only (NOT NSSM / WinSW / Windows services)
- Inert until HC_312B_ALLOW_INTAKE_TASK=I_UNDERSTAND
- IgnoreNew concurrency policy (single instance)
- Bounded restart, no reboot-on-failure
- No secrets in task XML or arguments

This module is POLICY AND CONFIGURATION ONLY — it does not register,
start, stop, or modify any scheduled task.  Activation is a separate
operator step performed outside this module.
"""

from __future__ import annotations

from pathlib import Path

# ---------------------------------------------------------------------------
# Approved always-on mechanism (same as HC-306E-R2)
# ---------------------------------------------------------------------------
ACTIVE_ALWAYS_ON_MECHANISM = "windows_task_scheduler"
REJECTED_SERVICE_WRAPPERS: tuple[str, ...] = ("NSSM", "WinSW")

# ---------------------------------------------------------------------------
# Exact task name — extend the existing HC task family
# ---------------------------------------------------------------------------
TASK_INTAKE_NAME = "HealthCheckerIntake"

# The task runs the HC-312A one-shot runner on a repeating schedule.
# Entry point: python -m backend.health_vault.intake.runner
TASK_ENTRY_MODULE = "backend.health_vault.intake.runner"

# ---------------------------------------------------------------------------
# Activation / approval gate (inert until set)
# ---------------------------------------------------------------------------
APPROVAL_ENV_VAR = "HC_312B_ALLOW_INTAKE_TASK"
APPROVAL_VALUE = "I_UNDERSTAND"

# ---------------------------------------------------------------------------
# Scheduling defaults
# ---------------------------------------------------------------------------
# Default polling interval for the Windows Task Scheduler repetition.
TASK_REPEAT_INTERVAL_MINUTES: int = 5
TASK_REPEAT_INTERVAL_SECONDS: int = TASK_REPEAT_INTERVAL_MINUTES * 60

# MonitoringScheduler-compatible interval settings (used by IntakeWatcher)
DEFAULT_INTERVAL_SECONDS: int = TASK_REPEAT_INTERVAL_SECONDS   # 5 min
MIN_INTERVAL_SECONDS: int = 60                                   # 1 min floor
MAX_INTERVAL_SECONDS: int = 3600                                 # 1 hr cap
MAX_BACKOFF_SECONDS: int = 1800                                  # 30 min max backoff
# Lease duration: how long a run is considered "live" before stale recovery
LEASE_DURATION_SECONDS: int = 900                                # 15 min

# ---------------------------------------------------------------------------
# Task Scheduler settings contract (matches HC-306E policy)
# ---------------------------------------------------------------------------
MULTIPLE_INSTANCES_POLICY = "IgnoreNew"   # Prevents concurrent task instances
RESTART_INTERVAL_MINUTES: int = 2
RESTART_COUNT: int = 3
REBOOT_ON_FAILURE: bool = False
EXECUTION_TIME_LIMIT_SECONDS: int = 600   # 10 min hard cap per run

# ---------------------------------------------------------------------------
# Fixed host paths — must NOT be inside the Git working tree
# ---------------------------------------------------------------------------
PROGRAMDATA_ROOT = Path(r"C:\ProgramData\HealthChecker")
INTAKE_LOG_DIR = PROGRAMDATA_ROOT / "logs" / "intake"

# Scheduler state persisted under the vault (reuses VaultStore JSON index)
SCHEDULER_STATE_KEY = "hc312b_intake_scheduler"

# ---------------------------------------------------------------------------
# Security constraints
# ---------------------------------------------------------------------------
SERVE_FUNNEL_FORBIDDEN: bool = True
FIREWALL_CHANGES_ALLOWED: bool = False
SECRETS_IN_TASK_XML: bool = False  # must remain False

# ---------------------------------------------------------------------------
# Reason codes (privacy-safe, no clinical content)
# ---------------------------------------------------------------------------
REASON_CODES: frozenset[str] = frozenset(
    {
        "ok",
        "approval_required",
        "elevation_required",
        "already_running",
        "not_due",
        "lease_expired_recovered",
        "intake_run_error",
        "nssm_winsw_rejected",
        "git_tree_execution_forbidden",
        "task_name_forbidden",
        "task_not_installed",
    }
)


def scheduled_task_settings_contract() -> dict:
    """Return the Task Scheduler settings this task requires.

    Used by tests to verify policy without activating anything.
    Mirrors HC-306E scheduled_task_settings_contract() pattern.
    """
    return {
        "task_name": TASK_INTAKE_NAME,
        "entry_module": TASK_ENTRY_MODULE,
        "multiple_instances": MULTIPLE_INSTANCES_POLICY,
        "restart_count": RESTART_COUNT,
        "restart_interval_minutes": RESTART_INTERVAL_MINUTES,
        "reboot_on_failure": REBOOT_ON_FAILURE,
        "repeat_interval_minutes": TASK_REPEAT_INTERVAL_MINUTES,
        "execution_time_limit_seconds": EXECUTION_TIME_LIMIT_SECONDS,
        "approval_env_var": APPROVAL_ENV_VAR,
        "approval_value": APPROVAL_VALUE,
        "secrets_in_task_xml": SECRETS_IN_TASK_XML,
        "active_mechanism": ACTIVE_ALWAYS_ON_MECHANISM,
        "rejected_wrappers": list(REJECTED_SERVICE_WRAPPERS),
    }


def assert_approval_set() -> None:
    """Raise ScheduledIntakeError unless the approval env var is set.

    This is the same inert-template pattern used by HC-306E.
    HC-312B code that would actually register a task must call this first.
    """
    import os
    val = os.environ.get(APPROVAL_ENV_VAR, "")
    if val != APPROVAL_VALUE:
        raise ScheduledIntakeError(
            "approval_required",
            f"Set {APPROVAL_ENV_VAR}={APPROVAL_VALUE} to enable task installation",
        )


class ScheduledIntakeError(ValueError):
    """Fail-closed error for HC-312B scheduled-task operations."""

    def __init__(self, code: str, message: str = "") -> None:
        self.code = code if code in REASON_CODES else "intake_run_error"
        super().__init__(message or self.code)
