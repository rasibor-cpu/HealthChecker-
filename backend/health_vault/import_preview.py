"""Server-enforced parse -> preview -> confirm -> commit protocol (HC-358).

Preview sessions hold the staged upload in memory only (never on disk) and are
referenced by an opaque random token. The token carries no medical content.
Sessions are bound to the authenticated user and to the sha256 of the staged
bytes, expire after a configurable TTL, and can be confirmed exactly once.
Confirming again returns the stored result (idempotent retry).
"""

from __future__ import annotations

import hashlib
import os
import secrets
import threading
import time
from dataclasses import dataclass, field
from typing import Any, Callable

PREVIEWED = "PREVIEWED"
CONFIRMED = "CONFIRMED"
CANCELLED = "CANCELLED"
EXPIRED = "EXPIRED"

DEFAULT_TTL_SECONDS = 900
MAX_ACTIVE_PER_USER = 10
MAX_STAGED_BYTES = 30 * 1024 * 1024
MAX_ACTIVE_BYTES_PER_USER = 64 * 1024 * 1024
MAX_ACTIVE_BYTES_TOTAL = 256 * 1024 * 1024
MAX_SESSION_RECORDS = 1000
MAX_CONCURRENT_PREVIEW_ANALYSES = 2


def configured_ttl_seconds() -> int:
    try:
        value = int(os.environ.get("HC_IMPORT_PREVIEW_TTL_SECONDS", DEFAULT_TTL_SECONDS))
    except ValueError:
        return DEFAULT_TTL_SECONDS
    return min(max(value, 30), 3600)


class PreviewError(Exception):
    def __init__(self, code: str, status_code: int) -> None:
        super().__init__(code)
        self.code = code
        self.status_code = status_code


@dataclass
class _Session:
    token: str
    user_id: str
    filename: str
    mime_type: str
    sha256: str
    parser_identity: str | None
    summary: dict[str, Any]
    created_at: float
    expires_at: float
    status: str = PREVIEWED
    content: bytes | None = None
    result: dict[str, Any] | None = None
    lock: threading.Lock = field(default_factory=threading.Lock)


class ImportPreviewService:
    def __init__(
        self,
        pipeline_dry_run: Callable[[str, bytes, str, str], dict[str, Any]],
        commit: Callable[[str, bytes, str, str], dict[str, Any]],
        *,
        ttl_seconds: int | None = None,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self._dry_run = pipeline_dry_run
        self._commit = commit
        self._ttl = ttl_seconds if ttl_seconds is not None else configured_ttl_seconds()
        self._clock = clock
        self._sessions: dict[str, _Session] = {}
        self._guard = threading.Lock()
        # OCR and parser dry-runs are the expensive pre-commit phase. Bound
        # them independently from staged-session storage so valid large scans
        # cannot exhaust the worker pool before admission controls run.
        self._analysis_slots = threading.BoundedSemaphore(
            MAX_CONCURRENT_PREVIEW_ANALYSES
        )
        # Serialises the final Vault commit so concurrent confirmations of
        # overlapping content cannot interleave duplicate checks and writes.
        self._commit_lock = threading.Lock()

    @property
    def ttl_seconds(self) -> int:
        return self._ttl

    def _purge(self) -> None:
        now = self._clock()
        for token in list(self._sessions):
            session = self._sessions[token]
            if session.status == PREVIEWED and session.expires_at <= now:
                session.status = EXPIRED
                session.content = None
            # Keep terminal records briefly so retries get a clear answer.
            if session.expires_at + self._ttl <= now:
                self._sessions.pop(token, None)

    def _prune_terminal_for_capacity(self) -> None:
        """Bound retained preview metadata without evicting active sessions."""
        if len(self._sessions) < MAX_SESSION_RECORDS:
            return
        terminal = sorted(
            (
                session for session in self._sessions.values()
                if session.status != PREVIEWED
            ),
            key=lambda session: session.created_at,
        )
        for session in terminal:
            if len(self._sessions) < MAX_SESSION_RECORDS:
                break
            self._sessions.pop(session.token, None)
        if len(self._sessions) >= MAX_SESSION_RECORDS:
            raise PreviewError("preview_capacity_exceeded", 503)

    def create(self, user_id: str, content: bytes, filename: str, mime_type: str) -> dict[str, Any]:
        if not content:
            raise PreviewError("empty_file", 400)
        if len(content) > MAX_STAGED_BYTES:
            raise PreviewError("file_too_large", 413)
        digest = hashlib.sha256(content).hexdigest()
        if not self._analysis_slots.acquire(blocking=False):
            raise PreviewError("preview_busy", 503)
        try:
            parsed = self._dry_run(user_id, content, filename, mime_type)
        finally:
            self._analysis_slots.release()
        with self._guard:
            self._purge()
            now = self._clock()
            token = secrets.token_urlsafe(32)
            summary = self._summarise(parsed, filename)
            if summary["eligible"]:
                active = [
                    session for session in self._sessions.values()
                    if session.user_id == user_id and session.status == PREVIEWED
                ]
                user_bytes = sum(
                    len(session.content) for session in active
                    if session.content is not None
                )
                # Preserve the existing oldest-preview eviction policy, but
                # apply it to both session count and retained bytes.
                while active and (
                    len(active) >= MAX_ACTIVE_PER_USER
                    or user_bytes + len(content) > MAX_ACTIVE_BYTES_PER_USER
                ):
                    oldest = min(active, key=lambda session: session.created_at)
                    if oldest.content is not None:
                        user_bytes -= len(oldest.content)
                    oldest.status = CANCELLED
                    oldest.content = None
                    active.remove(oldest)

                total_bytes = sum(
                    len(session.content) for session in self._sessions.values()
                    if session.status == PREVIEWED and session.content is not None
                )
                if total_bytes + len(content) > MAX_ACTIVE_BYTES_TOTAL:
                    # Never evict another user's pending clinical preview.
                    raise PreviewError("preview_capacity_exceeded", 503)

            # Terminal entries are pruned before admitting new metadata. If the
            # cap is still full, every retained entry is active and protected.
            self._prune_terminal_for_capacity()
            session = _Session(
                token=token,
                user_id=user_id,
                filename=filename,
                mime_type=mime_type,
                sha256=digest,
                parser_identity=summary.get("parser"),
                summary=summary,
                created_at=now,
                expires_at=now + self._ttl,
                content=content if summary["eligible"] else None,
            )
            if not summary["eligible"]:
                session.status = CANCELLED
            self._sessions[token] = session
        return {
            "ok": True,
            "preview_token": token if summary["eligible"] else None,
            "expires_in_seconds": self._ttl,
            "fingerprint": digest[:12],
            "filename": filename,
            **summary,
        }

    @staticmethod
    def _summarise(parsed: dict[str, Any], filename: str) -> dict[str, Any]:
        measurements = list(parsed.get("measurements") or [])
        metrics = sorted({str(m.get("metric")) for m in measurements if m.get("metric")})
        dates = sorted(str(m.get("measured_at")) for m in measurements if m.get("measured_at"))
        if not dates and parsed.get("measured_at"):
            dates = [str(parsed["measured_at"])]
        errors = [str(e) for e in (parsed.get("errors") or [])]
        duplicate = bool(parsed.get("duplicate"))
        parser = parsed.get("parser") or {}
        parser_id = (
            f"{parser.get('id')}@{parser.get('version')}" if isinstance(parser, dict) and parser else None
        )
        categories = [c for c in [parsed.get("primary_category"), *(parsed.get("secondary_categories") or [])] if c]
        ok = bool(parsed.get("ok"))
        ocr = parsed.get("ocr") if isinstance(parsed.get("ocr"), dict) else {}
        ocr_meta = ocr.get("meta") if isinstance(ocr.get("meta"), dict) else {}
        confirmable = bool(parsed.get("confirmable", True))
        return {
            "document_type": parsed.get("document_type"),
            "source": parsed.get("source_system"),
            "parser": parser_id,
            "ocr": {
                "provider": ocr.get("provider"),
                "confidence": ocr.get("confidence"),
                "status": ocr_meta.get("reason"),
                "local_only": bool(ocr_meta.get("local_only")),
            },
            "clinical_data_detected": bool(parsed.get("clinical_data_detected", measurements)),
            "record_count": 0 if duplicate else 1,
            "observation_count": len(measurements),
            "metrics": metrics[:50],
            "measurement_preview": [
                {
                    "metric": m.get("metric"),
                    "value": m.get("value"),
                    "units": m.get("units") or m.get("unit"),
                    "measured_at": m.get("measured_at"),
                    "flag": m.get("flag"),
                }
                for m in measurements[:50]
            ],
            "categories": categories,
            "date_range": {"from": dates[0], "to": dates[-1]} if dates else None,
            "duplicate": duplicate,
            "duplicate_count": 1 if duplicate else 0,
            "requires_review": bool(parsed.get("requires_review")),
            "warnings": [str(w) for w in (parsed.get("warnings") or [])][:20],
            "errors": errors[:20],
            "eligible": ok and not errors and not duplicate and confirmable,
        }

    def _owned(self, token: str, user_id: str) -> _Session:
        with self._guard:
            self._purge()
            session = self._sessions.get(str(token or ""))
        # Same error for unknown and foreign tokens so ownership cannot be probed.
        if session is None or session.user_id != user_id:
            raise PreviewError("preview_token_invalid", 404)
        return session

    def confirm(self, user_id: str, token: str) -> dict[str, Any]:
        session = self._owned(token, user_id)
        with session.lock:
            if session.status == CONFIRMED and session.result is not None:
                return {**session.result, "already_confirmed": True}
            if session.status == CANCELLED:
                raise PreviewError("preview_cancelled", 409)
            if session.status == EXPIRED or session.expires_at <= self._clock():
                session.status = EXPIRED
                session.content = None
                raise PreviewError("preview_expired", 410)
            content = session.content
            if content is None or hashlib.sha256(content).hexdigest() != session.sha256:
                session.status = CANCELLED
                session.content = None
                raise PreviewError("preview_content_mismatch", 409)
            with self._commit_lock:
                result = self._commit(user_id, content, session.filename, session.mime_type)
            if not result.get("ok"):
                # Nothing was committed; the user may retry the same preview.
                raise PreviewError("import_failed", 502)
            session.result = result
            session.status = CONFIRMED
            session.content = None
            return {**result, "already_confirmed": False}

    def cancel(self, user_id: str, token: str) -> dict[str, Any]:
        session = self._owned(token, user_id)
        with session.lock:
            if session.status == CONFIRMED:
                raise PreviewError("preview_already_confirmed", 409)
            session.status = CANCELLED
            session.content = None
        return {"ok": True, "status": CANCELLED}

    def status_of(self, token: str) -> str | None:
        session = self._sessions.get(token)
        return session.status if session else None
