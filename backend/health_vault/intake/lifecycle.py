"""HC-312A — Intake lifecycle: states, directory management, atomic claim.

LifecycleState
--------------
Four terminal-or-in-flight states mirror a safe document lifecycle:

    incoming   → file arrived; awaiting claim
    processing → claimed by one runner; being ingested
    completed  → successfully ingested (or canonical duplicate detected)
    quarantine → failed, unsupported, or unsafe; reason code recorded

Atomic claim
------------
``AtomicClaim.claim(source)`` renames the file from incoming/ to processing/.

On Windows (NTFS, same volume) ``Path.rename()`` is effectively atomic for a
single file — only one concurrent caller receives the success return; all
others see FileNotFoundError (source gone) and skip.  No OS-level lock file is
needed.

Reason codes (privacy-safe, never contain clinical values)
----------------------------------------------------------
    unsupported_extension
    file_too_large
    path_traversal
    unreadable_file
    pipeline_exception:<ExcType>
    pipeline_failed
    unsafe_state
"""

from __future__ import annotations

import logging
import threading
from enum import Enum
from pathlib import Path
from typing import NamedTuple

from backend.health_vault.intake.intake_config import IntakeConfig

logger = logging.getLogger("hc312a.lifecycle")

_claim_lock = threading.Lock()


class LifecycleState(str, Enum):
    """HC-312A document lifecycle states."""

    INCOMING = "incoming"
    PROCESSING = "processing"
    COMPLETED = "completed"
    QUARANTINE = "quarantine"


class ClaimResult(NamedTuple):
    """Result of an atomic claim attempt."""

    claimed: bool
    processing_path: Path | None
    reason: str  # "" on success, reason code on failure


class LifecycleManager:
    """Create and manage the intake directory structure.

    All path operations resolve to absolute paths and validate that the result
    stays inside ``config.intake_root`` to prevent path-traversal attacks.
    """

    def __init__(self, config: IntakeConfig) -> None:
        self.config = config

    # ------------------------------------------------------------------
    # Directory initialisation
    # ------------------------------------------------------------------

    def ensure_dirs(self) -> None:
        """Create all lifecycle directories (idempotent)."""
        for d in self.config.all_dirs():
            d.mkdir(parents=True, exist_ok=True)

    # ------------------------------------------------------------------
    # Path safety
    # ------------------------------------------------------------------

    def _safe_basename(self, path: Path) -> str:
        """Return the basename only; reject if it would escape intake_root."""
        name = path.name
        # Reject any name that contains path separators or dots that could
        # escape the directory (e.g. "../secret").
        if "/" in name or "\\" in name or name.startswith(".."):
            raise ValueError(f"path_traversal: {name!r}")
        return name

    def _resolve_and_guard(self, candidate: Path) -> Path:
        """Resolve candidate and verify it is inside intake_root."""
        root = self.config.intake_root.resolve()
        resolved = candidate.resolve()
        try:
            resolved.relative_to(root)
        except ValueError:
            raise ValueError(f"path_traversal: {candidate!r} escapes intake root")
        return resolved

    # ------------------------------------------------------------------
    # Atomic claim: incoming → processing
    # ------------------------------------------------------------------

    def claim(self, incoming_path: Path) -> ClaimResult:
        """Atomically move *incoming_path* to the processing/ directory.

        Returns
        -------
        ClaimResult
            ``claimed=True`` with the new path on success.
            ``claimed=False`` with a reason code when the file was already
            claimed by another runner or does not exist.
        """
        # Resolve the full path first to catch traversal via ".." segments
        # regardless of what the leaf basename looks like.
        try:
            resolved = incoming_path.resolve()
            incoming_root = self.config.incoming_dir.resolve()
            resolved.relative_to(incoming_root)
        except ValueError:
            logger.warning("claim_rejected_path_traversal path=%s", incoming_path)
            return ClaimResult(claimed=False, processing_path=None, reason="path_traversal")

        try:
            name = self._safe_basename(incoming_path)
        except ValueError:
            return ClaimResult(claimed=False, processing_path=None, reason="path_traversal")

        processing_path = self.config.processing_dir / name

        try:
            # On Windows/NTFS within the same volume this rename is atomic.
            with _claim_lock:
                incoming_path.rename(processing_path)
        except FileNotFoundError:
            # Another concurrent runner already claimed this file — skip.
            logger.debug("claim_skipped_already_claimed filename=%s", name)
            return ClaimResult(claimed=False, processing_path=None, reason="already_claimed")
        except OSError as exc:
            logger.warning("claim_failed filename=%s error=%s", name, type(exc).__name__)
            return ClaimResult(claimed=False, processing_path=None, reason=f"claim_os_error:{type(exc).__name__}")

        logger.info("claimed filename=%s", name)
        return ClaimResult(claimed=True, processing_path=processing_path, reason="")

    # ------------------------------------------------------------------
    # Terminal transitions: processing → completed | quarantine
    # ------------------------------------------------------------------

    def move_to_completed(self, processing_path: Path) -> Path:
        """Move a successfully processed file to completed/."""
        name = self._safe_basename(processing_path)
        dest = self._unique_dest(self.config.completed_dir, name)
        processing_path.rename(dest)
        logger.info("completed filename=%s", name)
        return dest

    def move_to_quarantine(self, processing_path: Path, reason: str) -> Path:
        """Move a failed file to quarantine/ and log the reason code."""
        name = self._safe_basename(processing_path)
        dest = self._unique_dest(self.config.quarantine_dir, name)
        try:
            processing_path.rename(dest)
        except FileNotFoundError:
            # File disappeared between claim and quarantine (extremely rare).
            logger.warning("quarantine_source_gone filename=%s reason=%s", name, reason)
            return dest
        # Write a sidecar reason file for auditability (privacy-safe only).
        reason_file = dest.with_suffix(dest.suffix + ".reason")
        try:
            reason_file.write_text(reason, encoding="utf-8")
        except OSError:
            pass  # reason file is advisory — non-fatal
        logger.warning("quarantined filename=%s reason=%s", name, reason)
        return dest

    # ------------------------------------------------------------------
    # Restart / stale-processing recovery
    # ------------------------------------------------------------------

    def recover_stale_processing(self) -> list[str]:
        """Move any files stranded in processing/ back to incoming/.

        Returns list of recovered basenames for the operational summary.
        This is called once at runner startup before scanning begins.
        A prior run that crashed mid-claim will have left files in processing/.
        """
        recovered: list[str] = []
        for candidate in list(self.config.processing_dir.iterdir()):
            if candidate.is_file():
                name = candidate.name
                dest = self._unique_dest(self.config.incoming_dir, name)
                try:
                    candidate.rename(dest)
                    recovered.append(name)
                    logger.info("stale_recovered filename=%s", name)
                except OSError as exc:
                    logger.warning("stale_recovery_failed filename=%s error=%s", name, type(exc).__name__)
        return recovered

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _unique_dest(directory: Path, name: str) -> Path:
        """Return a non-colliding path inside *directory* for *name*.

        If ``completed/report.pdf`` already exists, produce
        ``completed/report__1.pdf``, ``completed/report__2.pdf``, etc.
        """
        dest = directory / name
        if not dest.exists():
            return dest
        stem = Path(name).stem
        suffix = Path(name).suffix
        counter = 1
        while True:
            candidate = directory / f"{stem}__{counter}{suffix}"
            if not candidate.exists():
                return candidate
            counter += 1
