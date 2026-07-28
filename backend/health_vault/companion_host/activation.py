"""
Fail-closed activation configuration for the HC-304B companion-only host.

Secrets must come from the process environment (or an env file loaded by the
service wrapper). Never pass tokens/pepper on the command line.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

# Activation must be the exact token "enabled" (case-sensitive).
ACTIVATION_ENABLED = "enabled"
TRUSTED_PROXY_TAILSCALE = "tailscale_https"
TRUSTED_PROXY_LOOPBACK_DIRECT = "loopback_direct"
ALLOWED_PROXY_MODES = frozenset({TRUSTED_PROXY_TAILSCALE, TRUSTED_PROXY_LOOPBACK_DIRECT})
ALLOWED_BIND_HOSTS = frozenset({"127.0.0.1", "::1", "localhost"})
_PLACEHOLDER_FRAGMENTS = (
    "replace-with",
    "changeme",
    "change-me",
    "example",
    "placeholder",
    "todo",
    "xxxxx",
    "your-",
)


class ActivationError(ValueError):
    """Raised when permanent-host activation fails closed."""

    def __init__(self, code: str, message: str = "") -> None:
        self.code = code
        super().__init__(message or code)


@dataclass(frozen=True)
class HostActivationConfig:
    activation: str
    admin_token: str
    pepper: str
    proxy_shared_token: str
    monitoring_vault_root: Path
    trusted_proxy_mode: str
    external_https_origin: str
    bind_host: str
    bind_port: int
    repo_root: Path

    def public_dict(self) -> dict[str, Any]:
        """Non-secret summary for readiness responses / logs."""
        return {
            "activation": self.activation,
            "trusted_proxy_mode": self.trusted_proxy_mode,
            "external_https_origin_configured": True,
            "bind_host": self.bind_host,
            "bind_port": self.bind_port,
            "monitoring_vault_configured": True,
            "admin_token_configured": True,
            "pepper_configured": True,
            "proxy_shared_token_configured": bool(self.proxy_shared_token),
        }


def validate_secret_value(value: str, *, field: str) -> str:
    raw = (value or "").strip()
    if not raw or len(raw) < 24:
        raise ActivationError(f"{field}_required")
    low = raw.lower()
    if any(frag in low for frag in _PLACEHOLDER_FRAGMENTS):
        raise ActivationError(f"{field}_placeholder_forbidden")
    if len(set(raw)) < 4:
        raise ActivationError(f"{field}_weak_forbidden")
    return raw


def validate_external_https_origin(origin: str) -> str:
    raw = (origin or "").strip().rstrip("/")
    if not raw:
        raise ActivationError("external_https_origin_required")
    parsed = urlparse(raw)
    if parsed.scheme.lower() != "https":
        raise ActivationError("external_https_origin_must_be_https")
    if not parsed.hostname:
        raise ActivationError("external_https_origin_invalid")
    if parsed.username or parsed.password:
        raise ActivationError("external_https_origin_userinfo_forbidden")
    if parsed.path not in ("", "/") or parsed.query or parsed.fragment:
        raise ActivationError("external_https_origin_must_be_origin_only")
    if any(ch.isspace() or ord(ch) < 0x20 for ch in raw):
        raise ActivationError("external_https_origin_invalid")
    host = parsed.hostname.lower()
    port = parsed.port
    if port and port != 443:
        return f"https://{host}:{port}"
    return f"https://{host}"


def validate_bind_host(host: str) -> str:
    h = (host or "").strip().lower()
    if not h:
        raise ActivationError("bind_host_required")
    if h in {"0.0.0.0", "::", "[::]", "*"}:
        raise ActivationError("bind_host_non_loopback_forbidden")
    if re.match(r"^\d+\.\d+\.\d+\.\d+$", h):
        if h != "127.0.0.1":
            raise ActivationError("bind_host_non_loopback_forbidden")
        return "127.0.0.1"
    if h not in ALLOWED_BIND_HOSTS:
        raise ActivationError("bind_host_non_loopback_forbidden")
    if h == "localhost":
        return "127.0.0.1"
    if h == "::1":
        return "::1"
    return "127.0.0.1"


def validate_bind_port(port_raw: str) -> int:
    from backend.health_vault.companion_host.topology import validate_topology_port

    return validate_topology_port(port_raw or "", field="bind_port")


def load_and_validate_activation(
    *,
    environ: dict[str, str] | None = None,
    repo_root: Path | None = None,
) -> HostActivationConfig:
    """
    Validate activation configuration without creating a vault.

    Raises ActivationError on any missing/malformed required value.
    """
    env = {k: str(v) for k, v in (environ if environ is not None else os.environ).items()}
    root = Path(repo_root or Path(__file__).resolve().parents[3]).resolve()

    activation = env.get("HC_HOST_ACTIVATION", "").strip()
    if activation != ACTIVATION_ENABLED:
        raise ActivationError("host_activation_required")

    admin = validate_secret_value(env.get("HC_COMPANION_ADMIN_TOKEN", ""), field="admin_token")
    pepper = validate_secret_value(env.get("HC_COMPANION_PEPPER", ""), field="pepper")
    if hmac_equal(admin, pepper):
        raise ActivationError("admin_pepper_must_differ")

    vault_raw = env.get("HC_MONITORING_VAULT_ROOT", "").strip()
    if not vault_raw:
        raise ActivationError("monitoring_vault_root_required")

    from backend.health_vault.companion_host.vault_boundary import (
        assert_safe_monitoring_vault_path,
    )

    vault_root = assert_safe_monitoring_vault_path(vault_raw, repo_root=root)

    proxy_mode = env.get("HC_TRUSTED_PROXY_MODE", "").strip()
    if proxy_mode not in ALLOWED_PROXY_MODES:
        raise ActivationError("trusted_proxy_mode_invalid")

    proxy_shared = ""
    if proxy_mode == TRUSTED_PROXY_TAILSCALE:
        proxy_shared = validate_secret_value(
            env.get("HC_PROXY_SHARED_TOKEN", ""), field="proxy_shared_token"
        )
        if hmac_equal(proxy_shared, admin) or hmac_equal(proxy_shared, pepper):
            raise ActivationError("proxy_token_must_differ")

    origin = validate_external_https_origin(env.get("HC_EXTERNAL_HTTPS_ORIGIN", ""))
    bind_host = validate_bind_host(env.get("HC_BIND_HOST", "127.0.0.1"))
    bind_port = validate_bind_port(env.get("HC_BIND_PORT", ""))

    # HC-304BR1: Tailscale HTTPS path requires Serve → local proxy → Companion Host
    # port separation (never equal ports; never CSS 8765 / HC-303D 8877).
    if proxy_mode == TRUSTED_PROXY_TAILSCALE:
        from backend.health_vault.companion_host.topology import load_host_topology

        topo_env = dict(env)
        topo_env["HC_BIND_HOST"] = bind_host
        topo_env["HC_BIND_PORT"] = str(bind_port)
        load_host_topology(topo_env)

    return HostActivationConfig(
        activation=activation,
        admin_token=admin,
        pepper=pepper,
        proxy_shared_token=proxy_shared,
        monitoring_vault_root=vault_root,
        trusted_proxy_mode=proxy_mode,
        external_https_origin=origin,
        bind_host=bind_host,
        bind_port=bind_port,
        repo_root=root,
    )


def hmac_equal(a: str, b: str) -> bool:
    import hmac as _hmac

    aa = a.encode("utf-8")
    bb = b.encode("utf-8")
    if len(aa) != len(bb):
        # Length mismatch ⇒ not equal; still touch both buffers to avoid trivial timing branch.
        _hmac.compare_digest(aa, aa)
        _hmac.compare_digest(bb, bb)
        return False
    return _hmac.compare_digest(aa, bb)


def apply_secrets_to_environ(config: HostActivationConfig, environ: dict[str, str] | None = None) -> None:
    """
    Publish validated secrets into the process environment for existing companion helpers.
    Call only after load_and_validate_activation succeeds.
    """
    target = environ if environ is not None else os.environ
    target["HC_COMPANION_ADMIN_TOKEN"] = config.admin_token
    target["HC_COMPANION_PEPPER"] = config.pepper
    target["HC_HOST_ACTIVATION"] = config.activation
    target["HC_MONITORING_VAULT_ROOT"] = str(config.monitoring_vault_root)
    target["HC_TRUSTED_PROXY_MODE"] = config.trusted_proxy_mode
    target["HC_EXTERNAL_HTTPS_ORIGIN"] = config.external_https_origin
    target["HC_BIND_HOST"] = config.bind_host
    target["HC_BIND_PORT"] = str(config.bind_port)
    if config.proxy_shared_token:
        target["HC_PROXY_SHARED_TOKEN"] = config.proxy_shared_token
