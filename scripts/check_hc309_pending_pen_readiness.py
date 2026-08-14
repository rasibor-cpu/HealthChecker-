"""Evaluate the repository's HC-309 pending-PEN readiness record.

This wrapper is intentionally read-only. It accepts no arguments, reads only the
fixed repository record, and can never authorize live execution or return PASS.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from backend.health_vault.companion_host.r4f_preparation import (
    EXIT_INVOCATION,
    PENDING_PEN_RESULT_SCHEMA,
    evaluate_pending_pen_readiness,
)


READINESS_RECORD = ROOT / "config/hc309_r4f_exec_pending_pen_readiness.json"


def _fixed_error() -> dict[str, object]:
    return {
        "authorization": "readiness_only",
        "certification_status": "FAIL",
        "environment": "pilot",
        "error": "readiness_configuration_invalid",
        "exit_code": EXIT_INVOCATION,
        "live_execution_status": "BLOCKED",
        "schema_version": PENDING_PEN_RESULT_SCHEMA,
    }


def main(argv: list[str] | None = None) -> int:
    args = sys.argv[1:] if argv is None else argv
    if args:
        result = _fixed_error()
    else:
        try:
            raw = READINESS_RECORD.read_bytes()
        except (OSError, ValueError):
            result = _fixed_error()
        else:
            result = evaluate_pending_pen_readiness(raw)
    sys.stdout.write(json.dumps(result, sort_keys=True, separators=(",", ":")) + "\n")
    return int(result["exit_code"])


if __name__ == "__main__":
    raise SystemExit(main())
