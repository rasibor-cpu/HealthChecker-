"""HC-314A — AcquisitionWatcher: periodic Gmail acquisition using MonitoringScheduler.

AcquisitionWatcher wraps the existing HC-302 MonitoringScheduler infrastructure
around the HC-313 GmailAcquirer to provide:

- Interval-based scheduling (default 5 minutes, no busy loop)
- Lease-based concurrent-run exclusion
- Exponential backoff on transient Gmail/network failures
- Persistent scheduler state via AcquisitionStateStore
- Privacy-safe operational telemetry

This provides the Python-layer scheduling logic invoked by the Windows Scheduled Task.
"""

from __future__ import annotations

import logging
from typing import Any
import dataclasses

from backend.health_vault.acquisition.acquisition_state import AcquisitionStateStore
from backend.health_vault.acquisition.gmail_acquirer import GmailAcquirer
from backend.health_vault.acquisition.gmail_api_connector import GmailApiConnector
from backend.health_vault.acquisition.patient_identity import PatientIdentityVerifier
from backend.health_vault.acquisition.gmail_config import (
    DEFAULT_INTERVAL_SECONDS,
    MAX_BACKOFF_SECONDS,
    MAX_INTERVAL_SECONDS,
    MIN_INTERVAL_SECONDS,
    GmailAcquisitionConfig,
    get_default_config,
)
from backend.health_vault.monitoring.scheduler import MonitoringScheduler
from backend.health_vault.vault_store import VaultStore

logger = logging.getLogger("hc314a.watcher")


class AcquisitionWatcher:
    """Periodic Gmail acquisition controller wrapping MonitoringScheduler + GmailAcquirer.

    Parameters
    ----------
    config:
        GmailAcquisitionConfig for the acquisition limits and paths.
    store:
        AcquisitionStateStore used to persist scheduler state and idempotency.
    interval_seconds:
        Override for the default polling interval.
    force:
        When True, run even if the scheduler says it is not yet due.
    """

    def __init__(
        self,
        config: GmailAcquisitionConfig | None = None,
        store: AcquisitionStateStore | None = None,
        vault_store: VaultStore | None = None,
        *,
        interval_seconds: int = DEFAULT_INTERVAL_SECONDS,
        force: bool = False,
    ) -> None:
        self.config = config or get_default_config()
        # Default store path from config if not provided
        self.store = store or AcquisitionStateStore(self.config.acquisition_state_path)
        self.vault_store = vault_store or VaultStore()
        self.interval_seconds = max(MIN_INTERVAL_SECONDS, min(MAX_INTERVAL_SECONDS, interval_seconds))
        self.force = force

        scheduler_config = {
            "scheduler": {
                "default_interval_seconds": self.interval_seconds,
                "min_interval_seconds": MIN_INTERVAL_SECONDS,
                "max_interval_seconds": MAX_INTERVAL_SECONDS,
                "max_backoff_seconds": MAX_BACKOFF_SECONDS,
            }
        }

        # Reuse MonitoringScheduler. State is passed to AcquisitionStateStore.
        self._scheduler = MonitoringScheduler(
            store=self.store,  # Duck-types VaultStore
            config=scheduler_config,
            patient_id="gmail_acquisition_scheduler",
        )

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def run_if_due(self) -> dict[str, Any]:
        """Run one acquisition pass if the scheduler says it is due.

        Returns a privacy-safe result dict.
        Concurrent-run exclusion prevents overlapping executions.
        """
        def _sync_fn() -> dict[str, Any]:
            connector = GmailApiConnector(config=self.config)
            verifier = PatientIdentityVerifier.from_store(self.vault_store)
            acquirer = GmailAcquirer(connector=connector, config=self.config, verifier=verifier)
            
            # The acquirer might raise transient HTTP/Auth errors.
            # Catching these ensures the scheduler backs off.
            try:
                summary_obj = acquirer.run_scan()
                summary_obj.gmail_auth_success = True  # Assuming success if we reach here
                summary = dataclasses.asdict(summary_obj)
                ok = True
                error_msg = None
            except Exception as exc:
                logger.error("hc314a_acquisition_failed error=%s", exc)
                summary = {"error": type(exc).__name__}
                ok = False
                error_msg = str(exc)

            if hasattr(self.store, "update_telemetry"):
                self.store.update_telemetry(summary)

            return {
                "ok": ok,
                "success": ok,
                "error": error_msg,
                "acquisition_summary": summary,
                "gmail_auth_success": summary.get("gmail_auth_success", False),
                "handoff_success": summary.get("handoff_success", False),
                "messages_discovered": summary.get("messages_scanned", 0),
                "attachments_discovered": summary.get("attachments_evaluated", 0),
                "accept_count": summary.get("accepted", 0),
                "review_count": summary.get("sent_to_review", 0),
                "reject_count": summary.get("rejected_format", 0) + summary.get("rejected_classification", 0) + summary.get("rejected_identity", 0),
                "already_acquired_count": summary.get("already_acquired", 0),
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
            "hc314a_watcher ran=%s reason=%s accept=%s review=%s reject=%s",
            ran,
            result.get("reason", ""),
            inner.get("accept_count", 0) if ran else 0,
            inner.get("review_count", 0) if ran else 0,
            inner.get("reject_count", 0) if ran else 0,
        )

        return {
            "ran": ran,
            "reason": result.get("reason", ""),
            "scheduler": scheduler_state,
            "acquisition_summary": inner.get("acquisition_summary") if ran else None,
            "error": inner.get("error"),
        }

    def scheduler_status(self) -> dict[str, Any]:
        """Return current scheduler state (privacy-safe)."""
        return self._scheduler.status()
