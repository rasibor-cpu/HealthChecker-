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

__all__ = [
    "ActivationError",
    "HostActivationConfig",
    "load_and_validate_activation",
    "create_companion_only_app",
    "build_activated_app",
    "COMPANION_ONLY_ROUTES",
]
