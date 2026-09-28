"""
Isolated monitoring-vault path boundary for HC-304B.

Does not create directories until prepare_monitoring_vault() is called after
activation validation succeeds.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Callable

from backend.health_vault.companion_host.activation import ActivationError
from backend.health_vault.models import utc_now
from backend.health_vault.vault_key_protector import read_protected_key
from backend.health_vault.vault_store import VaultStore

MONITORING_SCHEMA = "hc.monitoring_vault.v1"
MARKER_NAME = ".hc_monitoring_vault"
META_NAME = "host_meta.json"


def assert_safe_monitoring_vault_path(raw: str | Path, *, repo_root: Path) -> Path:
    """
    Resolve and reject unsafe monitoring-vault targets.
    Does not create the directory.
    """
    try:
        path = Path(raw).expanduser().resolve(strict=False)
    except OSError as exc:
        raise ActivationError("monitoring_vault_path_invalid") from exc

    repo = repo_root.resolve()
    prod_vault = (repo / "vault_storage").resolve()
    private_imports = (repo / "private_imports").resolve()

    # Reject repo root, production vault, private_imports, and anything under the repo.
    if path == repo or path == prod_vault or path == private_imports:
        raise ActivationError("monitoring_vault_path_forbidden")
    try:
        path.relative_to(repo)
        raise ActivationError("monitoring_vault_path_inside_repo_forbidden")
    except ValueError:
        pass  # not inside repo — good

    # Reject TEMP HC-303D session trees.
    temp = Path(os.environ.get("TEMP") or os.environ.get("TMP") or "/tmp").resolve()
    hc303d = (temp / "hc303d_session").resolve()
    if path == hc303d or _is_relative_to(path, hc303d):
        raise ActivationError("monitoring_vault_temp_session_forbidden")

    # Reject obviously broad / system roots.
    if path.anchor and path == Path(path.anchor):
        raise ActivationError("monitoring_vault_path_too_broad")
    parts_lower = {p.lower() for p in path.parts}
    if parts_lower & {"windows", "system32", "program files", "program files (x86)"}:
        raise ActivationError("monitoring_vault_path_forbidden")

    return path


def _is_relative_to(path: Path, parent: Path) -> bool:
    try:
        path.relative_to(parent)
        return True
    except ValueError:
        return False


def prepare_monitoring_vault(
    root: Path,
    key_file: Path,
    *,
    key_reader: Callable[[Path], bytes] = read_protected_key,
) -> VaultStore:
    """
    Create/open an encrypted isolated monitoring vault after activation validation.

    The protected key must already exist. Existing plaintext pilot storage is
    rejected rather than being rewritten in place without a verified migration.
    """
    root = Path(root).resolve()
    key_file = Path(key_file).resolve()
    try:
        key = key_reader(key_file)
        store = VaultStore(root=root, encryption_key=key)
        # Authenticate an existing index immediately; wrong/corrupt keys and
        # legacy plaintext state must fail before the host can serve requests.
        store._read_index()
    except ActivationError:
        raise
    except Exception as exc:
        raise ActivationError("monitoring_vault_activation_failed") from exc
    if not store.encrypted:
        raise ActivationError("monitoring_vault_encryption_required")

    marker = root / MARKER_NAME
    if not marker.exists():
        marker.write_text("hc304b_monitoring_vault\n", encoding="utf-8")
        try:
            os.chmod(marker, 0o600)
        except OSError:
            pass

    meta_path = root / META_NAME
    meta: dict[str, Any] = {
        "schema_version": MONITORING_SCHEMA,
        "activation": "enabled",
        "purpose": "companion_monitoring_pilot",
        "encrypted_at_rest": True,
        "merges_into_production_vault": False,
        "simulated_clinical_observations": False,
        "updated_at": utc_now(),
    }
    if meta_path.exists():
        try:
            prior = json.loads(meta_path.read_text(encoding="utf-8"))
            if isinstance(prior, dict):
                meta["created_at"] = prior.get("created_at") or utc_now()
            else:
                meta["created_at"] = utc_now()
        except (OSError, json.JSONDecodeError):
            meta["created_at"] = utc_now()
    else:
        meta["created_at"] = utc_now()
    meta_path.write_text(json.dumps(meta, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    try:
        os.chmod(meta_path, 0o600)
    except OSError:
        pass

    data = store._read_index()
    data["monitoring_schema_version"] = MONITORING_SCHEMA
    data["monitoring_activation"] = "enabled"
    store._write_index(data)
    return store


def windows_acl_guidance(vault_root: Path) -> list[str]:
    """Plain-language ACL guidance — not executed."""
    return [
        f"Grant Modify only to the companion-host service account on: {vault_root}",
        "Remove Inherited permissions; do not grant Users/Everyone write access.",
        "Keep secret env files outside the vault and outside Git (e.g. %ProgramData%\\HealthChecker\\companion_host\\).",
        "Do not store the vault under the Git repository, vault_storage, or private_imports.",
    ]
