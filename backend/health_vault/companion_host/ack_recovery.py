"""Bounded recovery for abandoned in-progress companion batch acknowledgements."""

from __future__ import annotations

from typing import Any

from backend.health_vault.models import utc_now
from backend.health_vault.vault_store import VaultStore

# After this age, in_progress reservations may be marked abandoned so retries can proceed.
DEFAULT_ABANDON_AFTER_SECONDS = 900  # 15 minutes


def _parse_iso_to_epoch(value: str | None) -> float | None:
    if not value:
        return None
    text = str(value).strip()
    if not text:
        return None
    try:
        from datetime import datetime

        if text.endswith("Z"):
            text = text[:-1] + "+00:00"
        return datetime.fromisoformat(text).timestamp()
    except ValueError:
        return None


def recover_abandoned_in_progress_acks(
    store: VaultStore,
    *,
    now_epoch: float | None = None,
    abandon_after_seconds: int = DEFAULT_ABANDON_AFTER_SECONDS,
) -> dict[str, Any]:
    """
    Mark stale in_progress batch acks as abandoned (failed) so identical retries can reserve again.
    Fail-closed on lock/IO errors by returning ok=False without partial silent success claims.
    """
    import time

    now = time.time() if now_epoch is None else now_epoch
    recovered = 0
    scanned = 0
    try:
        with store.companion_lock():
            data = store._read_index()
            acks = dict(data.get("companion_batch_acks") or {})
            changed = False
            for bid, row in list(acks.items()):
                if not isinstance(row, dict):
                    continue
                scanned += 1
                if row.get("status") != "in_progress":
                    continue
                reserved_at = _parse_iso_to_epoch(row.get("reserved_at"))
                if reserved_at is None:
                    # Unparseable timestamp — treat as abandoned to unblock.
                    age_ok = True
                else:
                    age_ok = (now - reserved_at) >= abandon_after_seconds
                if not age_ok:
                    continue
                row = dict(row)
                row["ok"] = False
                row["status"] = "abandoned"
                row["abandoned_at"] = utc_now()
                row["error"] = "in_progress_abandoned_after_timeout"
                acks[str(bid)] = row
                recovered += 1
                changed = True
            if changed:
                data["companion_batch_acks"] = acks
                store._audit(
                    data,
                    "companion_batch_acks_abandoned",
                    {"recovered": recovered},
                )
                store._write_index(data)
        return {"ok": True, "scanned": scanned, "recovered": recovered}
    except OSError:
        return {"ok": False, "scanned": scanned, "recovered": recovered, "error": "ack_recovery_io_error"}
