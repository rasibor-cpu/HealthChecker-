"""HC-312A — Intake configuration.

Centralises every safety limit for automatic medical-record intake so that
tests can override individual values without patching global state.

Allowed file types are deliberately bounded to the existing HealthChecker
document-import stack (BatchImportConfig).  HC-312A adds no new parser
capabilities.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any


# ---------------------------------------------------------------------------
# Supported types — must stay a strict subset of BatchImportConfig limits.
# ---------------------------------------------------------------------------
_SUPPORTED_EXTENSIONS: tuple[str, ...] = (".pdf", ".png", ".jpg", ".jpeg", ".json")
_SUPPORTED_MIME_PREFIXES: tuple[str, ...] = (
    "application/pdf",
    "image/png",
    "image/jpeg",
    "image/jpg",
    "application/json",
    "text/json",
    "application/octet-stream",  # only valid when extension is known
)

# Default intake root — siblings of vault_storage so both live under the repo.
_DEFAULT_INTAKE_ROOT = Path(__file__).resolve().parents[3] / "hc_intake"

# Per-file size limit mirrors BatchImportConfig.max_file_bytes (20 MB).
_DEFAULT_MAX_FILE_BYTES: int = 20 * 1024 * 1024


@dataclass(frozen=True)
class IntakeConfig:
    """All tuneable knobs for automatic intake.

    Attributes
    ----------
    intake_root:
        Absolute path to the HealthChecker-owned intake root.  Sub-directories
        (incoming/, processing/, completed/, quarantine/) are created under it.
    max_file_bytes:
        Maximum per-file size accepted from incoming/.  Larger files are moved
        directly to quarantine with reason ``file_too_large``.
    allowed_extensions:
        Set of lower-cased file extensions recognised as supported document
        types.  Must remain a subset of the existing parser/BatchImport stack.
    allowed_mime_prefixes:
        MIME prefix whitelist used alongside the extension check.
    provenance_tag:
        Provenance string stamped on every document imported through this
        intake path.  Ends up as a ``provenance:`` tag in the vault index.
    source_system:
        ``source_system`` value forwarded to ImportService so vault records
        are clearly attributable to the automatic intake path.
    stale_recovery:
        When True (the default), files stranded in processing/ at startup are
        moved back to incoming/ before scanning begins.
    """

    intake_root: Path = field(default_factory=lambda: _DEFAULT_INTAKE_ROOT)
    max_file_bytes: int = _DEFAULT_MAX_FILE_BYTES
    allowed_extensions: tuple[str, ...] = _SUPPORTED_EXTENSIONS
    allowed_mime_prefixes: tuple[str, ...] = _SUPPORTED_MIME_PREFIXES
    provenance_tag: str = "hc312a_automatic_intake"
    source_system: str = "hc312a_intake"
    stale_recovery: bool = True

    # ------------------------------------------------------------------
    # Derived directory accessors (not stored — computed from intake_root)
    # ------------------------------------------------------------------

    @property
    def incoming_dir(self) -> Path:
        return self.intake_root / "incoming"

    @property
    def processing_dir(self) -> Path:
        return self.intake_root / "processing"

    @property
    def completed_dir(self) -> Path:
        return self.intake_root / "completed"

    @property
    def quarantine_dir(self) -> Path:
        return self.intake_root / "quarantine"

    def all_dirs(self) -> tuple[Path, ...]:
        return (
            self.incoming_dir,
            self.processing_dir,
            self.completed_dir,
            self.quarantine_dir,
        )

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        # Path objects are not JSON-serialisable as-is
        d["intake_root"] = str(self.intake_root)
        return d


def get_default_intake_config(**overrides: Any) -> IntakeConfig:
    """Return an IntakeConfig with optional field overrides.

    Typical usage in tests::

        cfg = get_default_intake_config(intake_root=tmp_path / "intake")
    """
    base = {
        "intake_root": _DEFAULT_INTAKE_ROOT,
        "max_file_bytes": _DEFAULT_MAX_FILE_BYTES,
        "allowed_extensions": _SUPPORTED_EXTENSIONS,
        "allowed_mime_prefixes": _SUPPORTED_MIME_PREFIXES,
        "provenance_tag": "hc312a_automatic_intake",
        "source_system": "hc312a_intake",
        "stale_recovery": True,
    }
    for k, v in overrides.items():
        if k in base:
            base[k] = v
    # Coerce list → tuple for frozen fields
    for key in ("allowed_extensions", "allowed_mime_prefixes"):
        if isinstance(base.get(key), list):
            base[key] = tuple(base[key])
    if isinstance(base.get("intake_root"), str):
        base["intake_root"] = Path(base["intake_root"])
    return IntakeConfig(**base)
