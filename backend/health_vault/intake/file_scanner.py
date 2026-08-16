"""HC-312A — File scanner: enumerate and validate candidates from incoming/.

The scanner is the first gate before atomic claim.  It:

1. Lists files in incoming/ (non-recursive — controlled directory only).
2. Applies extension and size checks (fast, no I/O beyond stat).
3. Returns two lists: eligible candidates and pre-rejected items.

Pre-rejected items are NOT moved here — the caller (file_processor or runner)
is responsible for quarantining them.  This keeps concern separation clean.

Privacy note
------------
The scanner never reads file content or logs any clinical values.
Only filename basename, size, and status are recorded.
"""

from __future__ import annotations

import logging
import mimetypes
from pathlib import Path
from typing import NamedTuple

from backend.health_vault.intake.intake_config import IntakeConfig
from backend.health_vault.intake.lifecycle import LifecycleManager

logger = logging.getLogger("hc312a.scanner")


class ScanCandidate(NamedTuple):
    """A file in incoming/ that passed fast pre-checks and may be claimed."""
    path: Path
    name: str       # basename only
    size_bytes: int
    mime_type: str


class ScanRejection(NamedTuple):
    """A file in incoming/ that failed a fast pre-check — quarantine without claim."""
    path: Path
    name: str       # basename only
    reason: str     # privacy-safe reason code


class ScanResult(NamedTuple):
    candidates: list[ScanCandidate]
    rejections: list[ScanRejection]


def _guess_mime(name: str, config: IntakeConfig) -> str:
    """Guess MIME type from extension; fall back to octet-stream."""
    guessed, _ = mimetypes.guess_type(name)
    return guessed or "application/octet-stream"


def _extension_allowed(name: str, config: IntakeConfig) -> bool:
    ext = Path(name).suffix.lower()
    return ext in config.allowed_extensions


def _mime_allowed(mime: str, config: IntakeConfig) -> bool:
    m = mime.lower().split(";")[0].strip()
    if not m or m == "application/octet-stream":
        return True  # extension already validated; octet-stream is a pass-through
    return any(
        m == prefix or m.startswith(prefix.rstrip("*"))
        for prefix in config.allowed_mime_prefixes
    )


def _is_safe_name(name: str) -> bool:
    """Reject names containing path separators or parent-dir references."""
    if "/" in name or "\\" in name:
        return False
    parts = Path(name).parts
    return ".." not in parts and len(parts) == 1


def scan_incoming(config: IntakeConfig) -> ScanResult:
    """Enumerate incoming/ and split files into eligible candidates and rejections.

    This is a READ-ONLY operation — no files are moved here.

    Parameters
    ----------
    config:
        Intake configuration (provides paths, limits, allowed types).

    Returns
    -------
    ScanResult
        Two lists: candidates ready for atomic claim, and pre-rejected files
        with privacy-safe reason codes.
    """
    incoming_dir = config.incoming_dir
    candidates: list[ScanCandidate] = []
    rejections: list[ScanRejection] = []

    if not incoming_dir.exists():
        logger.info("incoming_dir_absent path=%s", incoming_dir)
        return ScanResult(candidates=[], rejections=[])

    for entry in incoming_dir.iterdir():
        # Non-recursive: only top-level files.
        if not entry.is_file():
            continue

        name = entry.name

        # 1. Path-safety check.
        if not _is_safe_name(name):
            logger.warning("scan_rejected_path_traversal filename=%s", name)
            rejections.append(ScanRejection(path=entry, name=name, reason="path_traversal"))
            continue

        # 2. Extension whitelist.
        if not _extension_allowed(name, config):
            logger.info("scan_rejected_extension filename=%s", name)
            rejections.append(ScanRejection(path=entry, name=name, reason="unsupported_extension"))
            continue

        # 3. Size check (stat only — no content read).
        try:
            size = entry.stat().st_size
        except OSError as exc:
            logger.warning("scan_stat_failed filename=%s error=%s", name, type(exc).__name__)
            rejections.append(ScanRejection(path=entry, name=name, reason=f"unreadable_file:{type(exc).__name__}"))
            continue

        if size > config.max_file_bytes:
            logger.warning("scan_rejected_size filename=%s size=%d limit=%d", name, size, config.max_file_bytes)
            rejections.append(ScanRejection(path=entry, name=name, reason="file_too_large"))
            continue

        # 4. MIME consistency check (best-effort from extension).
        mime = _guess_mime(name, config)
        if not _mime_allowed(mime, config):
            logger.info("scan_rejected_mime filename=%s mime=%s", name, mime)
            rejections.append(ScanRejection(path=entry, name=name, reason="unsupported_mime"))
            continue

        candidates.append(ScanCandidate(path=entry, name=name, size_bytes=size, mime_type=mime))
        logger.debug("scan_candidate filename=%s size=%d", name, size)

    logger.info("scan_complete candidates=%d rejections=%d", len(candidates), len(rejections))
    return ScanResult(candidates=candidates, rejections=rejections)
