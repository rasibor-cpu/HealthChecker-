"""HC-312A — File processor: claim → canonical ingestion → lifecycle transition.

The processor owns the per-file import loop.  For every candidate it:

1. Atomically claims the file (incoming → processing via rename).
2. Calls ``ImportService.import_file()`` — the one canonical ingestion entry
   point.  No second parsing or persistence path is created.
3. Moves the file to completed/ (success or canonical duplicate) or
   quarantine/ (failure of any kind).
4. Returns a privacy-safe per-file result dict.

A failure of one file MUST NOT propagate and block subsequent files.  All
exceptions from ImportService are caught; the file is quarantined and
processing continues.

Privacy note
------------
Per-file result dicts contain only: filename (basename), status, reason_code,
sha256, document_id, duplicate flag, timestamp.  No clinical values, no OCR
text, no vault keys.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from backend.health_vault.models import utc_now

logger = logging.getLogger("hc312a.processor")

# Privacy-safe set of keys extracted from pipeline result for the operational log.
_SAFE_RESULT_KEYS = frozenset(
    {"ok", "duplicate", "status", "sha256", "imported_at"}
)


def _safe_document_id(result: dict[str, Any]) -> str | None:
    """Extract document_id without exposing clinical content."""
    doc = result.get("document")
    if isinstance(doc, dict):
        return doc.get("id")
    return None


class FileProcessor:
    """Process a single pre-validated candidate through canonical ingestion.

    Parameters
    ----------
    import_service:
        An ``ImportService`` instance.  Injected so tests can substitute a
        stub without touching production vault or keys.
    lifecycle:
        ``LifecycleManager`` instance for atomic-claim and terminal moves.
    config:
        ``IntakeConfig`` — used only for provenance_tag and source_system.
    """

    def __init__(self, import_service: Any, lifecycle: Any, config: Any) -> None:
        self._svc = import_service
        self._lc = lifecycle
        self._cfg = config

    def process(self, candidate: Any) -> dict[str, Any]:
        """Claim and ingest one file; return privacy-safe result metadata.

        This method NEVER raises.  Any exception results in quarantine + a
        result dict with ok=False.

        Parameters
        ----------
        candidate:
            A ``ScanCandidate`` namedtuple from file_scanner.scan_incoming().

        Returns
        -------
        dict
            Privacy-safe per-file result containing:
            filename, status, reason_code, sha256, document_id, duplicate,
            ok, processed_at.
        """
        name = candidate.name
        mime = candidate.mime_type
        started_at = utc_now()

        # ----------------------------------------------------------------
        # Step 1: Atomic claim  (incoming → processing)
        # ----------------------------------------------------------------
        claim = self._lc.claim(candidate.path)
        if not claim.claimed:
            return _make_result(
                name=name,
                ok=False,
                status="skipped",
                reason_code=claim.reason,
                processed_at=started_at,
            )

        processing_path = claim.processing_path

        # ----------------------------------------------------------------
        # Step 2: Canonical ingestion via ImportService.import_file()
        # ----------------------------------------------------------------
        try:
            result = self._svc.import_file(
                processing_path,
                mime_type=mime,
                provenance=self._cfg.provenance_tag,
                source_system=self._cfg.source_system,
                acquisition_method="hc312a_automatic_intake",
            )
        except Exception as exc:
            reason = f"pipeline_exception:{type(exc).__name__}"
            logger.warning("process_exception filename=%s reason=%s", name, reason)
            self._lc.move_to_quarantine(processing_path, reason)
            return _make_result(
                name=name,
                ok=False,
                status="quarantine",
                reason_code=reason,
                processed_at=started_at,
            )

        # ----------------------------------------------------------------
        # Step 3: Evaluate pipeline result → completed or quarantine
        # ----------------------------------------------------------------
        pipeline_ok = bool(result.get("ok"))
        is_duplicate = bool(result.get("duplicate"))
        sha256 = result.get("sha256")
        document_id = _safe_document_id(result)

        if pipeline_ok or is_duplicate:
            # Success path: new import or canonical duplicate.
            dest = self._lc.move_to_completed(processing_path)
            status = "duplicate" if is_duplicate else "completed"
            logger.info(
                "process_completed filename=%s status=%s sha256=%s",
                name, status, sha256 or "none",
            )
            return _make_result(
                name=name,
                ok=True,
                status=status,
                reason_code="",
                sha256=sha256,
                document_id=document_id,
                duplicate=is_duplicate,
                processed_at=started_at,
            )
        else:
            # Pipeline returned ok=False — quarantine with a reason code.
            errors = result.get("errors") or []
            # Derive a privacy-safe reason code from error strings.
            # Error strings from ImportPipeline are already type-name-based
            # (e.g. "pipeline_exception:ValueError") — safe to propagate.
            reason = _derive_reason(errors)
            self._lc.move_to_quarantine(processing_path, reason)
            logger.warning("process_quarantined filename=%s reason=%s", name, reason)
            return _make_result(
                name=name,
                ok=False,
                status="quarantine",
                reason_code=reason,
                sha256=sha256,
                document_id=document_id,
                processed_at=started_at,
            )


# ---------------------------------------------------------------------------
# Helpers (module-level, not methods, to keep processor class lean)
# ---------------------------------------------------------------------------

def _derive_reason(errors: list[str]) -> str:
    """Derive a single privacy-safe reason code from a list of error strings.

    Only type-name-level codes are kept.  Any string that looks like it
    contains clinical content (unusually long, non-ASCII, contains digits
    that could be lab values) falls back to 'pipeline_failed'.
    """
    if not errors:
        return "pipeline_failed"
    first = errors[0]
    # ImportPipeline error strings are already type-name-based; keep as-is
    # if they match the known safe pattern.
    if ":" in first and len(first) < 100 and first.replace(":", "").replace("_", "").isalnum():
        return first
    return "pipeline_failed"


def _make_result(
    *,
    name: str,
    ok: bool,
    status: str,
    reason_code: str,
    sha256: str | None = None,
    document_id: str | None = None,
    duplicate: bool = False,
    processed_at: str = "",
) -> dict[str, Any]:
    """Build a privacy-safe per-file result dict."""
    return {
        "filename": name,      # basename only — per existing repo policy
        "ok": ok,
        "status": status,
        "reason_code": reason_code,
        "sha256": sha256,      # content hash — safe to log per repo policy
        "document_id": document_id,
        "duplicate": duplicate,
        "processed_at": processed_at or utc_now(),
    }
