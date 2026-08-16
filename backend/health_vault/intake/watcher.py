"""HC-312B — IntakeWatcher: periodic intake using existing MonitoringScheduler.

IntakeWatcher wraps the existing HC-302 MonitoringScheduler infrastructure
around the HC-312A IntakeRunner to provide:

- Interval-based scheduling (no busy loop)
- Lease-based concurrent-run exclusion (one runner at a time)
- Exponential backoff on failure
- Persistent scheduler state via VaultStore
- Stale-lease recovery on restart
- Privacy-safe operational summaries

This does NOT create or register Windows Scheduled Tasks.
It provides the Python-layer scheduling logic that the one-shot runner
(backend.health_vault.intake.runner) wraps when called by a Scheduled Task.

The Windows Task Scheduler provides restart-persistence after reboot.
The MonitoringScheduler provides single-instance concurrency control and
backoff logic across invocations within a run.

Intended usage
--------------
The Windows Scheduled Task calls:

    python -m backend.health_vault.intake.runner

Each invocation creates an IntakeWatcher and calls run_if_due().
The MonitoringScheduler's state, persisted in VaultStore, ensures that:
  - if a prior invocation is still "running" (lease not expired), this
    invocation skips and exits immediately (concurrent-run exclusion)
  - if the lease has expired (prior run crashed), the state is recovered
    before proceeding (restart safety)
  - if the interval has not elapsed, this invocation skips (not_due)
"""

from __future__ import annotations

import logging
from typing import Any

from backend.health_vault.intake.intake_config import IntakeConfig, get_default_intake_config
from backend.health_vault.intake.runner import IntakeRunner
from backend.health_vault.intake.scheduled_intake import (
    DEFAULT_INTERVAL_SECONDS,
    LEASE_DURATION_SECONDS,
    MAX_BACKOFF_SECONDS,
    MAX_INTERVAL_SECONDS,
    MIN_INTERVAL_SECONDS,
    SCHEDULER_STATE_KEY,
    ScheduledIntakeError,
)
from backend.health_vault.models import utc_now
from backend.health_vault.monitoring.scheduler import MonitoringScheduler
from backend.health_vault.vault_store import VaultStore

logger = logging.getLogger("hc312b.watcher")


class IntakeWatcher:
    """Periodic intake controller wrapping MonitoringScheduler + IntakeRunner.

    Parameters
    ----------
    config:
        IntakeConfig for the intake directories and limits.
    store:
        VaultStore used to persist scheduler state.  When None, uses the
        default VaultStore (same vault as the ingestion pipeline).
    import_service:
        Optional ImportService stub — forwarded to IntakeRunner.
    interval_seconds:
        Override for the default polling interval.
    force:
        When True, run even if the scheduler says it is not yet due.
        Used by tests and operator one-off invocations.
    """

    def __init__(
        self,
        config: IntakeConfig | None = None,
        store: VaultStore | None = None,
        import_service: Any | None = None,
        *,
        interval_seconds: int = DEFAULT_INTERVAL_SECONDS,
        force: bool = False,
    ) -> None:
        self.config = config or get_default_intake_config()
        self.store = store  # may be None for stateless runs
        self.import_service = import_service
        self.interval_seconds = max(MIN_INTERVAL_SECONDS, min(MAX_INTERVAL_SECONDS, interval_seconds))
        self.force = force

        # Build a MonitoringScheduler-compatible config dict.
        scheduler_config = {
            "scheduler": {
                "default_interval_seconds": self.interval_seconds,
                "min_interval_seconds": MIN_INTERVAL_SECONDS,
                "max_interval_seconds": MAX_INTERVAL_SECONDS,
                "max_backoff_seconds": MAX_BACKOFF_SECONDS,
            }
        }

        # Reuse MonitoringScheduler exactly — same lease/backoff/state model
        # used by HC-302 continuous monitoring.
        self._scheduler = MonitoringScheduler(
            store=self.store,
            config=scheduler_config,
            patient_id=SCHEDULER_STATE_KEY,
        )

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def run_if_due(self) -> dict[str, Any]:
        """Run one intake pass if the scheduler says it is due.

        Returns a privacy-safe result dict::

            {
                "ran": True/False,
                "reason": "...",   # "not_due" | "already_running" | ""
                "scheduler": {...},
                "intake_summary": {...} | None,
            }

        Concurrent-run exclusion: MonitoringScheduler.run_due() sets
        running=True with a lease expiry before calling the sync function.
        A second concurrent call sees running=True and returns immediately
        with ran=False, reason="already_running".
        """
        runner = self._make_runner()

        def _sync_fn() -> dict[str, Any]:
            summary = runner.run()
            # Map intake summary to MonitoringScheduler ok/success signal.
            # A run is "ok" if it completed without an unhandled exception.
            # Individual file failures are expected and do not make the run fail.
            ok = True  # runner.run() always returns (never raises)
            return {
                "ok": ok,
                "success": ok,
                "intake_summary": summary,
                # Counts are safe to surface; no clinical content.
                "completed": summary.get("completed", 0),
                "quarantine": summary.get("quarantine", 0),
                "duplicate": summary.get("duplicate", 0),
                "pre_rejected": summary.get("pre_rejected", 0),
                "stale_recovered": summary.get("stale_recovered", 0),
            }

        result = self._scheduler.run_due(
            _sync_fn,
            force=self.force,
        )

        ran = bool(result.get("ran"))
        scheduler_state = result.get("scheduler") or {}
        inner = result.get("result") or {}

        log_level = logging.INFO if ran else logging.DEBUG
        logger.log(
            log_level,
            "hc312b_watcher ran=%s reason=%s completed=%s quarantine=%s",
            ran,
            result.get("reason", ""),
            inner.get("completed", 0) if ran else 0,
            inner.get("quarantine", 0) if ran else 0,
        )

        return {
            "ran": ran,
            "reason": result.get("reason", ""),
            "scheduler": scheduler_state,
            "intake_summary": inner.get("intake_summary") if ran else None,
        }

    def scheduler_status(self) -> dict[str, Any]:
        """Return current scheduler state (privacy-safe — no clinical content)."""
        return self._scheduler.status()

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _make_runner(self) -> IntakeRunner:
        return IntakeRunner(
            config=self.config,
            import_service=self.import_service,
        )
