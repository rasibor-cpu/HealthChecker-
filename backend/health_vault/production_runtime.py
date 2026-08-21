"""Fail-closed HC-311 production vault construction boundary."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Callable, Mapping

from backend.health_vault.recovery import CURRENT_SCHEMA, RecoveryError, VaultMigrationManager
from backend.health_vault.vault_key_protector import read_protected_key
from backend.health_vault.vault_store import VaultStore


class ProductionRuntimeError(RuntimeError):
    """Privacy-safe production startup failure."""


DEFAULT_VAULT_ROOT = Path(r"C:\ProgramData\HealthChecker\data\vault")
DEFAULT_KEY_PATH = Path(r"C:\ProgramData\HealthChecker\secrets\vault.key")


def create_production_vault(
    *,
    environ: Mapping[str, str] | None = None,
    key_reader: Callable[[Path], bytes] = read_protected_key,
) -> VaultStore:
    """Open the authoritative encrypted vault; never create/load plaintext state."""

    env = os.environ if environ is None else environ
    root = Path(env.get("HC_VAULT_ROOT") or DEFAULT_VAULT_ROOT)
    key_path = Path(env.get("HC_VAULT_KEY_FILE") or DEFAULT_KEY_PATH)
    try:
        key = key_reader(key_path)
        store = VaultStore(root=root, encryption_key=key)
        # Force authentication of an existing index during startup. A wrong or
        # corrupt key must not be deferred until the first user request.
        store._read_index()
        # Schema compatibility gate: unsupported/future schemas never become active.
        VaultMigrationManager().validate_current(store)
    except ProductionRuntimeError:
        raise
    except RecoveryError as exc:
        raise ProductionRuntimeError(f"production_vault_schema_incompatible:{exc}") from exc
    except Exception as exc:
        raise ProductionRuntimeError("production_vault_activation_failed") from exc
    if not store.encrypted:
        raise ProductionRuntimeError("production_vault_encryption_required")
    schema = store._read_index().get("schema_version")
    if schema != CURRENT_SCHEMA:
        raise ProductionRuntimeError("production_vault_schema_incompatible")
    return store


__all__ = ["ProductionRuntimeError", "create_production_vault", "CURRENT_SCHEMA"]
