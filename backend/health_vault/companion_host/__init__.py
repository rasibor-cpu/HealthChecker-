"""HC-304B companion-only permanent host — fail-closed activation and app factory."""

from backend.health_vault.companion_host.activation import (
    ActivationError,
    HostActivationConfig,
    load_and_validate_activation,
)
from backend.health_vault.companion_host.app import (
    COMPANION_ONLY_ROUTES,
    create_companion_only_app,
    build_activated_app,
)
from backend.health_vault.companion_host.privileged_evidence import (
    EvidenceContext,
    EvidenceValidationError,
    FreshnessPolicy,
    MODE_EXTERNAL_EVIDENCE,
    MODE_LEGACY_SAME_PROCESS,
    SCHEMA_VERSION_V1,
    TrustedSigner,
    append_evidence_record_append_only,
    compute_evidence_sha256,
    compute_evidence_signature,
    validate_preflight_mode,
    validate_privileged_evidence_bundle,
)

__all__ = [
    "ActivationError",
    "HostActivationConfig",
    "load_and_validate_activation",
    "create_companion_only_app",
    "build_activated_app",
    "COMPANION_ONLY_ROUTES",
    "EvidenceContext",
    "EvidenceValidationError",
    "FreshnessPolicy",
    "MODE_EXTERNAL_EVIDENCE",
    "MODE_LEGACY_SAME_PROCESS",
    "SCHEMA_VERSION_V1",
    "TrustedSigner",
    "compute_evidence_sha256",
    "compute_evidence_signature",
    "validate_preflight_mode",
    "validate_privileged_evidence_bundle",
    "append_evidence_record_append_only",
]
