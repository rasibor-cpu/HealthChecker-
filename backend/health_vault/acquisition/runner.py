"""HC-314A — One-shot unattended Gmail acquisition runner.

Entry point for the Windows Scheduled Task.

Workflow
--------
1. Instantiate AcquisitionWatcher.
2. Check scheduler due state and concurrent leases.
3. If due, scan Gmail for attachments, classify, verify identity, and hand off.
4. Update scheduler persistence (backoff, next due).
5. Print privacy-safe telemetry summary.
6. Exit cleanly.

Usage
-----
    python -m backend.health_vault.acquisition.runner
"""

from __future__ import annotations

import logging
import sys

from backend.health_vault.acquisition.watcher import AcquisitionWatcher


logger = logging.getLogger("hc314a.runner")


def _configure_logging() -> None:
    import os
    log_path = os.environ.get("HC_ACQUISITION_LOG_PATH", "C:\\rasib\\source\\HealthChecker-HC310E\\hc314a_acquisition.log")
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
        filename=log_path,
        force=True,
    )

def main() -> int:
    """One-shot runner entry point for scheduled execution."""
    _configure_logging()
    try:
        watcher = AcquisitionWatcher()
        result = watcher.run_if_due()
        
        if result.get("ran"):
            summary = result.get("acquisition_summary") or {}
            logger.info(
                f"HC-314A acquisition run complete: "
                f"messages={summary.get('messages_discovered', 0)} "
                f"attachments={summary.get('attachments_discovered', 0)} "
                f"accept={summary.get('accept_count', 0)} "
                f"review={summary.get('review_count', 0)} "
                f"reject={summary.get('reject_count', 0)} "
                f"already_acquired={summary.get('already_acquired_count', 0)} "
                f"error={result.get('error') or 'None'}"
            )
        else:
            logger.info(f"HC-314A acquisition skipped: {result.get('reason')}")
            
        return 0
    except Exception as exc:
        logger.error("hc314a_runner_fatal error=%s", type(exc).__name__)
        return 1


if __name__ == "__main__":
    sys.exit(main())
