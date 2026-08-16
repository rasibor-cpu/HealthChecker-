"""HC-312A — One-shot intake runner.

Entry point suitable for a Windows Scheduled Task (not activated here).

Workflow
--------
1. Ensure intake directory structure exists.
2. Recover any files stranded in processing/ from a prior crashed run
   (restart safety).
3. Scan incoming/ for eligible files and pre-rejected files.
4. Quarantine pre-rejected files immediately (no claim needed — they never
   passed validation).
5. For each eligible candidate: claim atomically → ingest → complete/quarantine.
6. Emit a privacy-safe summary (counts only, no clinical values).
7. Exit.

Usage (one-shot, for later Scheduled Task wiring)
-------------------------------------------------
    python -m backend.health_vault.intake.runner

Or in code::

    from backend.health_vault.intake.runner import IntakeRunner
    from backend.health_vault.intake.intake_config import get_default_intake_config

    runner = IntakeRunner(config=get_default_intake_config(intake_root=...))
    summary = runner.run()
    print(summary)

Privacy note
------------
The summary and all log messages contain only: counts, filenames (basenames),
status codes, reason codes, sha256 hashes, timestamps.  No clinical content,
no vault keys, no OCR text.
"""

from __future__ import annotations

import logging
import sys
from pathlib import Path
from typing import Any

from backend.health_vault.intake.file_processor import FileProcessor
from backend.health_vault.intake.file_scanner import ScanRejection, scan_incoming
from backend.health_vault.intake.intake_config import IntakeConfig, get_default_intake_config
from backend.health_vault.intake.lifecycle import LifecycleManager
from backend.health_vault.models import utc_now

logger = logging.getLogger("hc312a.runner")


class IntakeRunner:
    """One-shot automatic medical-record intake runner.

    Parameters
    ----------
    config:
        ``IntakeConfig`` with intake root, limits, and allowed types.
        Defaults to ``get_default_intake_config()`` when not supplied.
    import_service:
        ``ImportService`` instance.  When not supplied, an ``ImportService``
        backed by the default ``VaultStore`` is created.  Tests inject a stub.
    """

    def __init__(
        self,
        config: IntakeConfig | None = None,
        import_service: Any | None = None,
    ) -> None:
        self.config = config or get_default_intake_config()
        self.lifecycle = LifecycleManager(self.config)

        if import_service is not None:
            self._svc = import_service
        else:
            # Lazy import to avoid loading the full vault stack during module
            # import (important for fast test collection).
            from backend.health_vault.import_service import ImportService
            self._svc = ImportService()

        self._processor = FileProcessor(
            import_service=self._svc,
            lifecycle=self.lifecycle,
            config=self.config,
        )

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def run(self) -> dict[str, Any]:
        """Execute one complete intake pass.

        Returns
        -------
        dict
            Privacy-safe operational summary::

                {
                    "started_at": "...",
                    "finished_at": "...",
                    "stale_recovered": 0,
                    "scanned": 0,
                    "pre_rejected": 0,
                    "claimed": 0,
                    "completed": 0,
                    "duplicate": 0,
                    "quarantine": 0,
                    "skipped": 0,
                    "results": [ ... per-file privacy-safe dicts ... ],
                }
        """
        started_at = utc_now()
        logger.info("hc312a_intake_run_start started_at=%s", started_at)

        # ----------------------------------------------------------------
        # 1. Ensure directory structure exists.
        # ----------------------------------------------------------------
        self.lifecycle.ensure_dirs()

        # ----------------------------------------------------------------
        # 2. Restart safety: recover stale processing/ files.
        # ----------------------------------------------------------------
        stale_recovered: list[str] = []
        if self.config.stale_recovery:
            stale_recovered = self.lifecycle.recover_stale_processing()
            if stale_recovered:
                logger.info("stale_recovered count=%d", len(stale_recovered))

        # ----------------------------------------------------------------
        # 3. Scan incoming/.
        # ----------------------------------------------------------------
        scan = scan_incoming(self.config)
        results: list[dict[str, Any]] = []

        # ----------------------------------------------------------------
        # 4. Quarantine pre-rejected files (no claim needed).
        # ----------------------------------------------------------------
        for rejection in scan.rejections:
            quarantine_result = self._quarantine_rejection(rejection)
            results.append(quarantine_result)

        # ----------------------------------------------------------------
        # 5. Process eligible candidates (claim → ingest → terminal state).
        # ----------------------------------------------------------------
        for candidate in scan.candidates:
            try:
                file_result = self._processor.process(candidate)
            except Exception as exc:
                # Belt-and-suspenders: FileProcessor.process() is supposed to
                # never raise, but we catch here as a final safety net.
                logger.error(
                    "runner_unexpected_exception filename=%s error=%s",
                    candidate.name, type(exc).__name__,
                )
                file_result = {
                    "filename": candidate.name,
                    "ok": False,
                    "status": "quarantine",
                    "reason_code": f"runner_exception:{type(exc).__name__}",
                    "sha256": None,
                    "document_id": None,
                    "duplicate": False,
                    "processed_at": utc_now(),
                }
            results.append(file_result)

        # ----------------------------------------------------------------
        # 6. Build privacy-safe summary counts.
        # ----------------------------------------------------------------
        finished_at = utc_now()
        summary = _build_summary(
            started_at=started_at,
            finished_at=finished_at,
            stale_recovered=stale_recovered,
            results=results,
        )

        logger.info(
            "hc312a_intake_run_end completed=%d quarantine=%d duplicate=%d "
            "pre_rejected=%d stale_recovered=%d",
            summary["completed"],
            summary["quarantine"],
            summary["duplicate"],
            summary["pre_rejected"],
            summary["stale_recovered"],
        )
        return summary

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _quarantine_rejection(self, rejection: ScanRejection) -> dict[str, Any]:
        """Move a pre-rejected file from incoming/ to quarantine/."""
        from backend.health_vault.intake.file_processor import _make_result
        from backend.health_vault.intake.lifecycle import ClaimResult

        # Pre-rejected files are still in incoming/ — move directly to quarantine.
        dest = self.lifecycle._unique_dest(
            self.config.quarantine_dir, rejection.name
        )
        try:
            rejection.path.rename(dest)
            reason_file = dest.with_suffix(dest.suffix + ".reason")
            reason_file.write_text(rejection.reason, encoding="utf-8")
        except FileNotFoundError:
            pass  # already gone (concurrent runner or OS)
        except OSError:
            pass  # non-fatal — file may still be there; reason sidecar is advisory

        logger.warning(
            "pre_rejected filename=%s reason=%s", rejection.name, rejection.reason
        )
        return _make_result(
            name=rejection.name,
            ok=False,
            status="quarantine",
            reason_code=rejection.reason,
        )


# ---------------------------------------------------------------------------
# Summary builder (module-level for testability)
# ---------------------------------------------------------------------------

def _build_summary(
    *,
    started_at: str,
    finished_at: str,
    stale_recovered: list[str],
    results: list[dict[str, Any]],
) -> dict[str, Any]:
    """Aggregate per-file results into a privacy-safe run summary."""
    counts: dict[str, int] = {
        "completed": 0,
        "duplicate": 0,
        "quarantine": 0,
        "skipped": 0,
        "pre_rejected": 0,
    }
    for r in results:
        status = r.get("status", "")
        if status == "completed":
            counts["completed"] += 1
        elif status == "duplicate":
            counts["duplicate"] += 1
        elif status == "quarantine":
            # Pre-rejected are also quarantine — distinguish via reason_code prefix
            # so that "pre_rejected" count reflects scanner-level rejections.
            reason = r.get("reason_code", "")
            if reason in ("unsupported_extension", "path_traversal", "file_too_large",
                          "unsupported_mime", "unreadable_file"):
                counts["pre_rejected"] += 1
            else:
                counts["quarantine"] += 1
        elif status == "skipped":
            counts["skipped"] += 1
        else:
            counts["quarantine"] += 1

    return {
        "started_at": started_at,
        "finished_at": finished_at,
        "stale_recovered": len(stale_recovered),
        "stale_recovered_names": stale_recovered,  # basenames — safe per repo policy
        "scanned": len(results),
        "pre_rejected": counts["pre_rejected"],
        "claimed": counts["completed"] + counts["duplicate"] + counts["quarantine"] + counts["skipped"],
        "completed": counts["completed"],
        "duplicate": counts["duplicate"],
        "quarantine": counts["quarantine"],
        "skipped": counts["skipped"],
        "results": results,
    }


# ---------------------------------------------------------------------------
# __main__ entry point for Scheduled Task use
# ---------------------------------------------------------------------------

def _configure_logging() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
        stream=sys.stdout,
    )


def main() -> int:
    """One-shot runner entry point.  Returns exit code: 0 success, 1 error."""
    _configure_logging()
    try:
        runner = IntakeRunner()
        summary = runner.run()
        # Print privacy-safe summary counts only.
        print(
            f"HC-312A intake run complete: "
            f"completed={summary['completed']} "
            f"duplicate={summary['duplicate']} "
            f"quarantine={summary['quarantine']} "
            f"pre_rejected={summary['pre_rejected']} "
            f"stale_recovered={summary['stale_recovered']}"
        )
        return 0
    except Exception as exc:
        logger.error("hc312a_runner_fatal error=%s", type(exc).__name__)
        return 1


if __name__ == "__main__":
    sys.exit(main())
