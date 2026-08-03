"""Deprecated HC-309 compatibility shim that always returns BLOCKED.

No collector, evaluator, evidence input, or successful certification route remains.
"""

from __future__ import annotations

import json
import sys

SCHEMA = "hc.protected_runtime_certification.v1"
EXIT_BLOCKED = 20


def main(argv: list[str] | None = None) -> int:
    """Return a deterministic BLOCKED result; no live collector exists."""
    result = {
        "schema_version": SCHEMA,
        "overall": "BLOCKED",
        "exit_code": EXIT_BLOCKED,
        "checks": [],
        "error": "deprecated_trusted_collector_unavailable",
    }
    print(json.dumps(result, sort_keys=True, separators=(",", ":")))
    return EXIT_BLOCKED


if __name__ == "__main__":
    raise SystemExit(main())
