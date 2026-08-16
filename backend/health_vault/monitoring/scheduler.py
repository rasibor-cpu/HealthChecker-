"""HC-302 background scheduling foundation — bounded backoff, no busy loops."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any, Callable

from backend.health_vault.models import utc_now
from backend.health_vault.monitoring.ingestion import load_monitoring_config
from backend.health_vault.vault_store import VaultStore


class MonitoringScheduler:
    """
    Platform-appropriate periodic synchronization planner.

    State is persisted in VaultStore so API ticks share due/backoff/running across requests.
    Does NOT spin a busy loop. Overlapping runs are rejected while status == running.
    """

    def __init__(
        self,
        store: VaultStore | None = None,
        config: dict[str, Any] | None = None,
        patient_id: str = "default-patient",
    ) -> None:
        self.store = store
        self.patient_id = patient_id
        self.config = config or load_monitoring_config()
        sched = self.config.get("scheduler") or {}
        self.default_interval = int(sched.get("default_interval_seconds") or 900)
        self.min_interval = int(sched.get("min_interval_seconds") or 300)
        self.max_interval = int(sched.get("max_interval_seconds") or 86400)
        self.max_backoff = int(sched.get("max_backoff_seconds") or 3600)
        self._state = self._load_or_default()

    def _default_state(self) -> dict[str, Any]:
        return {
            "schema_version": "hc.monitoring_scheduler.v1",
            "patient_id": self.patient_id,
            "status": "idle",
            "consecutive_failures": 0,
            "current_interval_seconds": self.default_interval,
            "last_attempt_at": None,
            "last_success_at": None,
            "next_due_at": None,
            "last_error": None,
            "continuous_guaranteed": False,
            "busy_loop": False,
            "running": False,
            "lease_expires_at": None,
        }

    def _load_or_default(self) -> dict[str, Any]:
        if self.store is None:
            return self._default_state()
        saved = self.store.get_monitoring_scheduler_state(self.patient_id)
        if not saved:
            return self._default_state()
        state = self._default_state()
        state.update(dict(saved))
        # Recover from crashed run only if the running lease expired
        if state.get("running"):
            expires = state.get("lease_expires_at") or state.get("last_attempt_at")
            stale = True
            if expires:
                try:
                    text = expires[:-1] + "+00:00" if str(expires).endswith("Z") else str(expires)
                    stale = datetime.now(timezone.utc) > datetime.fromisoformat(text)
                except Exception:
                    stale = True
            if stale:
                state["running"] = False
                state["lease_expires_at"] = None
                if state.get("status") == "running":
                    state["status"] = "idle"
        return state

    def _persist(self) -> None:
        if self.store is None:
            return
        self.store.save_monitoring_scheduler_state(self.patient_id, dict(self._state))

    def status(self) -> dict[str, Any]:
        return dict(self._state)

    def plan_next(
        self,
        *,
        success: bool,
        now: str | None = None,
        error: str | None = None,
        degraded: bool = False,
    ) -> dict[str, Any]:
        now_ts = now or utc_now()
        self._state["last_attempt_at"] = now_ts
        self._state["running"] = False
        self._state["lease_expires_at"] = None
        if success:
            self._state["consecutive_failures"] = 0
            self._state["current_interval_seconds"] = self.default_interval
            self._state["last_success_at"] = now_ts
            self._state["last_error"] = None
            self._state["status"] = "ok" if not degraded else "degraded_ok"
        else:
            failures = int(self._state.get("consecutive_failures") or 0) + 1
            self._state["consecutive_failures"] = failures
            backoff = min(self.max_backoff, self.default_interval * (2 ** max(failures - 1, 0)))
            backoff = max(self.min_interval, min(self.max_interval, backoff))
            self._state["current_interval_seconds"] = backoff
            self._state["last_error"] = error or "sync_failed"
            self._state["status"] = "retry_scheduled"
            # Do NOT set last_success_at on failure or mere unavailable

        def _parse(s: str) -> datetime:
            text = s[:-1] + "+00:00" if s.endswith("Z") else s
            return datetime.fromisoformat(text)

        nxt = _parse(now_ts) + timedelta(seconds=int(self._state["current_interval_seconds"]))
        self._state["next_due_at"] = (
            nxt.astimezone(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")
        )
        self._persist()
        return self.status()

    def is_due(self, now: str | None = None) -> bool:
        now_ts = now or utc_now()
        due = self._state.get("next_due_at")
        if not due:
            return True
        return str(now_ts) >= str(due)

    def run_due(
        self,
        sync_fn: Callable[[], dict[str, Any]],
        *,
        now: str | None = None,
        force: bool = False,
    ) -> dict[str, Any]:
        """Execute sync_fn when due. Never loops. Rejects overlapping runs."""
        now_ts = now or utc_now()

        lock = self.store.companion_lock() if self.store else None
        if lock:
            lock.acquire()
        try:
            # Re-read state under lock to prevent TOC/TOU race condition
            self._state = self._load_or_default()

            if self._state.get("running"):
                return {
                    "ran": False,
                    "reason": "already_running",
                    "scheduler": self.status(),
                }
            if not force and not self.is_due(now=now_ts):
                return {
                    "ran": False,
                    "reason": "not_due",
                    "scheduler": self.status(),
                }
            self._state["status"] = "running"
            self._state["running"] = True
            self._state["last_attempt_at"] = now_ts
            # Explicit lease so overlap detection does not depend on wall-clock vs fixture dates
            try:
                text = now_ts[:-1] + "+00:00" if now_ts.endswith("Z") else now_ts
                lease_end = datetime.fromisoformat(text) + timedelta(seconds=900)
                self._state["lease_expires_at"] = (
                    lease_end.astimezone(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")
                )
            except Exception:
                self._state["lease_expires_at"] = None
            self._persist()
        finally:
            if lock:
                lock.release()

        try:
            result = sync_fn() or {}
            ok = bool(result.get("ok", result.get("success", False)))
            degraded = bool(result.get("degraded"))
            self.plan_next(
                success=ok,
                now=now_ts,
                error=None if ok else str(result.get("error") or "sync_failed"),
                degraded=degraded,
            )
            return {"ran": True, "result": result, "scheduler": self.status()}
        except Exception as exc:
            self.plan_next(success=False, now=now_ts, error=f"{type(exc).__name__}")
            return {
                "ran": True,
                "result": {"ok": False, "error": type(exc).__name__},
                "scheduler": self.status(),
            }
