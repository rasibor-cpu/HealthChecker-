"""HC-313A Gmail acquisition configuration.

All tuneable knobs for the Gmail medical-record acquisition pipeline.
Supported attachment formats are deliberately bounded to the existing
HC-312 intake stack — no new parser capabilities are introduced here.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


# ---------------------------------------------------------------------------
# Canonical paths
# ---------------------------------------------------------------------------

_REPO_ROOT = Path(__file__).resolve().parents[3]

# HC-312 intake boundary — the ONLY hand-off point HC-313A writes to.
_DEFAULT_INTAKE_INCOMING = _REPO_ROOT / "hc_intake" / "incoming"

# Acquisition state (idempotency ledger) is kept outside the vault root.
_DEFAULT_STATE_PATH = _REPO_ROOT / "hc313a_state" / "acquisition_state.json"

# ---------------------------------------------------------------------------
# Supported attachment formats (strict subset of HC-312's accepted set)
# ---------------------------------------------------------------------------

_SUPPORTED_EXTENSIONS: tuple[str, ...] = (".pdf", ".png", ".jpg", ".jpeg", ".json")
_SUPPORTED_MIME_PREFIXES: tuple[str, ...] = (
    "application/pdf",
    "image/png",
    "image/jpeg",
    "image/jpg",
    "application/json",
    "text/json",
    # application/octet-stream only valid when extension check passes
    "application/octet-stream",
)

_MAX_ATTACHMENT_BYTES: int = 20 * 1024 * 1024  # 20 MB — mirrors HC-312

# ---------------------------------------------------------------------------
# Scheduler constants
# ---------------------------------------------------------------------------
DEFAULT_INTERVAL_SECONDS = 300  # 5 minutes
MIN_INTERVAL_SECONDS = 60
MAX_INTERVAL_SECONDS = 3600
MAX_BACKOFF_SECONDS = 1800


# ---------------------------------------------------------------------------
# Config dataclass
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class GmailAcquisitionConfig:
    """All tuneable knobs for HC-313A Gmail acquisition.

    Attributes
    ----------
    intake_incoming_dir:
        The HC-312 incoming boundary.  Only ACCEPTED attachments are written here.
        Existing HC-312 infrastructure handles everything downstream.
    acquisition_state_path:
        Path to the persistent idempotency ledger (JSON).
    max_attachment_bytes:
        Maximum attachment size accepted.  Larger attachments are REJECTED.
    allowed_extensions:
        Supported file extensions — subset of HC-312's allowed set.
    allowed_mime_prefixes:
        Accepted MIME-type prefixes.
    medical_confidence_threshold:
        Minimum confidence score for ``MEDICAL_DOCUMENT_CONFIRMED`` classification.
    source_system:
        ``source_system`` value stamped on MedicalDocument records ingested
        via this pipeline.
    acquisition_method:
        ``acquisition_method`` value stamped on MedicalDocument records.
    gmail_label_filter:
        Gmail query label/filter used when listing candidate messages.
        Defaults to no filter (all messages scanned for attachments).
    max_messages_per_scan:
        Hard cap on messages evaluated per acquisition run to bound runtime.
    """

    intake_incoming_dir: Path = field(
        default_factory=lambda: _DEFAULT_INTAKE_INCOMING
    )
    acquisition_state_path: Path = field(
        default_factory=lambda: _DEFAULT_STATE_PATH
    )
    max_attachment_bytes: int = _MAX_ATTACHMENT_BYTES
    allowed_extensions: tuple[str, ...] = _SUPPORTED_EXTENSIONS
    allowed_mime_prefixes: tuple[str, ...] = _SUPPORTED_MIME_PREFIXES
    medical_confidence_threshold: float = 0.60
    source_system: str = "hc313a_gmail"
    acquisition_method: str = "hc313a_gmail_acquisition"
    gmail_label_filter: str = ""
    max_messages_per_scan: int = 200

    def to_dict(self) -> dict[str, Any]:
        return {
            "intake_incoming_dir": str(self.intake_incoming_dir),
            "acquisition_state_path": str(self.acquisition_state_path),
            "max_attachment_bytes": self.max_attachment_bytes,
            "allowed_extensions": list(self.allowed_extensions),
            "medical_confidence_threshold": self.medical_confidence_threshold,
            "source_system": self.source_system,
            "acquisition_method": self.acquisition_method,
            "gmail_label_filter": self.gmail_label_filter,
            "max_messages_per_scan": self.max_messages_per_scan,
        }


def get_default_config(**overrides: Any) -> GmailAcquisitionConfig:
    """Return a ``GmailAcquisitionConfig`` with optional field overrides."""
    base: dict[str, Any] = {
        "intake_incoming_dir": _DEFAULT_INTAKE_INCOMING,
        "acquisition_state_path": _DEFAULT_STATE_PATH,
        "max_attachment_bytes": _MAX_ATTACHMENT_BYTES,
        "allowed_extensions": _SUPPORTED_EXTENSIONS,
        "allowed_mime_prefixes": _SUPPORTED_MIME_PREFIXES,
        "medical_confidence_threshold": 0.60,
        "source_system": "hc313a_gmail",
        "acquisition_method": "hc313a_gmail_acquisition",
        "gmail_label_filter": "",
        "max_messages_per_scan": 200,
    }
    for k, v in overrides.items():
        if k in base:
            base[k] = v
    for key in ("allowed_extensions", "allowed_mime_prefixes"):
        if isinstance(base.get(key), list):
            base[key] = tuple(base[key])
    for key in ("intake_incoming_dir", "acquisition_state_path"):
        if isinstance(base.get(key), str):
            base[key] = Path(base[key])
    return GmailAcquisitionConfig(**base)


__all__ = [
    "GmailAcquisitionConfig",
    "get_default_config",
]
