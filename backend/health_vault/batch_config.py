"""HC-201G — configurable batch import safety limits."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any


@dataclass(frozen=True)
class BatchImportConfig:
    """Central limits for multi-file Health Vault ingestion."""

    max_files_per_batch: int = 25
    max_file_bytes: int = 20 * 1024 * 1024  # 20 MB
    max_batch_bytes: int = 150 * 1024 * 1024  # 150 MB
    allowed_extensions: tuple[str, ...] = (".pdf", ".png", ".jpg", ".jpeg", ".json")
    allowed_mime_prefixes: tuple[str, ...] = (
        "application/pdf",
        "image/png",
        "image/jpeg",
        "image/jpg",
        "application/json",
        "text/json",
        "application/octet-stream",  # allowed only with known extension
    )

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


DEFAULT_BATCH_CONFIG = BatchImportConfig()


def get_batch_config(**overrides: Any) -> BatchImportConfig:
    """Return config with optional field overrides (tests / deployment)."""
    base = DEFAULT_BATCH_CONFIG.to_dict()
    base.update({k: v for k, v in overrides.items() if k in base})
    # tuples may arrive as lists from callers
    if isinstance(base.get("allowed_extensions"), list):
        base["allowed_extensions"] = tuple(base["allowed_extensions"])
    if isinstance(base.get("allowed_mime_prefixes"), list):
        base["allowed_mime_prefixes"] = tuple(base["allowed_mime_prefixes"])
    return BatchImportConfig(**base)
