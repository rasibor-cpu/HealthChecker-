"""HC-313A — Acquisition state store (idempotency ledger).

Tracks every Gmail attachment that has been evaluated to prevent repeated
acquisition of the same content.

Idempotency is based on a combination of:
    1. message_id + attachment_id  — Gmail structural identity
    2. SHA-256 of attachment content — content identity

Both axes are checked independently:
    - Same message + attachment → ALREADY_ACQUIRED (even if content changed, which
      is unusual but worth guarding against after editing a sent attachment).
    - Same SHA-256 from a DIFFERENT message → content deduplicated.

The ledger is persisted as a JSON file that survives process restarts.
All writes are atomic (write-to-temp, os.replace) to prevent corruption.

No medical-record content is stored in this ledger.
"""

from __future__ import annotations

import json
import logging
import os
import threading
from pathlib import Path
from typing import Any

from backend.health_vault.models import utc_now


logger = logging.getLogger("hc313a.acquisition_state")


class AcquisitionStateStore:
    """Persistent idempotency ledger for Gmail attachment acquisitions.

    Thread-safe for in-process concurrent access; uses an in-memory lock
    around reads/writes.  File-level atomicity is provided by os.replace().

    Parameters
    ----------
    state_path:
        Path to the JSON ledger file.  Parent directory is created if absent.
    """

    def __init__(self, state_path: Path) -> None:
        self._path = Path(state_path)
        self._lock = threading.RLock()
        self._state: dict[str, Any] = self._load()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def is_already_acquired(
        self,
        *,
        message_id: str,
        attachment_id: str,
        sha256: str,
    ) -> bool:
        """Return True if this attachment has already been acquired.

        Checks both structural identity (message_id + attachment_id) and
        content identity (sha256).
        """
        with self._lock:
            attachments: dict[str, Any] = self._state.get("attachments", {})
            key = self._key(message_id, attachment_id)
            if key in attachments:
                return True
            # Content deduplication
            seen_sha256s: set[str] = set(self._state.get("sha256s", []))
            if sha256 and sha256 in seen_sha256s:
                return True
            return False

    def mark_acquired(
        self,
        *,
        message_id: str,
        attachment_id: str,
        sha256: str,
        final_decision: str,
        original_filename: str,
        acquired_at: str | None = None,
    ) -> None:
        """Record an acquisition decision in the ledger and persist to disk.

        Called for ACCEPT, REVIEW, and REJECT decisions so that every
        attachment is processed exactly once regardless of outcome.
        """
        with self._lock:
            key = self._key(message_id, attachment_id)
            entry: dict[str, Any] = {
                "message_id": message_id,
                "attachment_id": attachment_id,
                "sha256": sha256,
                "original_filename": original_filename,
                "final_decision": final_decision,
                "acquired_at": acquired_at or utc_now(),
            }
            self._state.setdefault("attachments", {})[key] = entry
            if sha256:
                sha256s: list[str] = self._state.setdefault("sha256s", [])
                if sha256 not in sha256s:
                    sha256s.append(sha256)
            self._persist()

    def list_records(self) -> list[dict[str, Any]]:
        """Return a copy of all acquisition records (no content — metadata only)."""
        with self._lock:
            return list(self._state.get("attachments", {}).values())

    def count(self) -> int:
        """Return total number of recorded acquisition entries."""
        with self._lock:
            return len(self._state.get("attachments", {}))

    def get_monitoring_scheduler_state(self, patient_id: str) -> dict[str, Any]:
        """Bridge for MonitoringScheduler. (patient_id is ignored since this is a global task)."""
        with self._lock:
            return dict(self._state.get("scheduler", {}))

    def save_monitoring_scheduler_state(self, patient_id: str, state: dict[str, Any]) -> None:
        """Bridge for MonitoringScheduler. (patient_id is ignored)."""
        with self._lock:
            self._state["scheduler"] = dict(state)
            self._persist()

    def companion_lock(self) -> threading.RLock:
        """Bridge for MonitoringScheduler."""
        return self._lock

    def update_telemetry(self, summary: dict[str, Any]) -> None:
        """Aggregate cumulative metrics into the state."""
        with self._lock:
            t = self._state.get("telemetry", {})
            t["total_accept_count"] = t.get("total_accept_count", 0) + summary.get("accept_count", 0)
            t["total_review_count"] = t.get("total_review_count", 0) + summary.get("review_count", 0)
            t["total_reject_count"] = t.get("total_reject_count", 0) + summary.get("reject_count", 0)
            t["total_already_acquired_count"] = t.get("total_already_acquired_count", 0) + summary.get("already_acquired_count", 0)
            if summary.get("error"):
                t["total_failure_count"] = t.get("total_failure_count", 0) + 1
            self._state["telemetry"] = t
            self._persist()

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _key(message_id: str, attachment_id: str) -> str:
        return f"{message_id}::{attachment_id}"

    def _load(self) -> dict[str, Any]:
        if not self._path.is_file():
            return {"schema": "hc313a.acquisition_state.v1", "attachments": {}, "sha256s": []}
        try:
            raw = self._path.read_text(encoding="utf-8")
            data = json.loads(raw)
            if not isinstance(data, dict):
                raise ValueError("state file is not a JSON object")
            return data
        except Exception as exc:
            logger.warning("hc313a_state_load_failed path=%s error=%s", self._path, exc)
            return {"schema": "hc313a.acquisition_state.v1", "attachments": {}, "sha256s": [], "telemetry": {}}

    def _persist(self) -> None:
        """Atomically persist the in-memory state to disk."""
        logger.info("hc314a_persist_called path=%s", self._path)
        self._path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self._path.with_name(f"{self._path.name}.tmp.{os.getpid()}")
        try:
            payload = json.dumps(self._state, indent=2, ensure_ascii=False)
            tmp.write_text(payload, encoding="utf-8")
            os.replace(tmp, self._path)
            import os as _os
            mtime = _os.stat(self._path).st_mtime
            running = self._state.get("scheduler", {}).get("running")
            logger.info("hc314a_state_persist_success path=%s mtime=%s running=%s", self._path, mtime, running)
        except Exception as exc:
            logger.error("hc313a_state_persist_failed path=%s error=%s", self._path, exc)
            try:
                tmp.unlink(missing_ok=True)
            except Exception:
                pass


__all__ = ["AcquisitionStateStore"]
