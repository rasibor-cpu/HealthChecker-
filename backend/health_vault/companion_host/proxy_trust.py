"""Trusted-proxy and external HTTPS origin enforcement for HC-304B."""

from __future__ import annotations

from dataclasses import dataclass
from urllib.parse import urlparse

from backend.health_vault.companion_host.activation import (
    TRUSTED_PROXY_LOOPBACK_DIRECT,
    TRUSTED_PROXY_TAILSCALE,
    HostActivationConfig,
    hmac_equal,
)


@dataclass(frozen=True)
class ProxyTrustResult:
    ok: bool
    error: str | None
    tls_enabled: bool
    effective_origin: str | None


def _client_is_loopback(client_host: str | None) -> bool:
    import os

    h = (client_host or "").strip().lower().strip("[]")
    if h in {"127.0.0.1", "::1", "localhost"}:
        return True
    if h == "testclient" and os.environ.get("HC_HOST_ALLOW_TESTCLIENT_PEER", "").strip() == "1":
        return True
    return False


def _normalize_forwarded_host(raw: str) -> str | None:
    """
    Take a single forwarded host value (already de-duplicated).
    Reject userinfo, paths, queries, fragments, and multi-hop leftovers.
    """
    value = (raw or "").strip().lower()
    if not value or "," in value:
        return None
    # Disallow credentials / path / query / fragment confusion.
    if any(ch in value for ch in ("@", "/", "?", "#", "\\", " ")):
        return None
    # Structured parse via URL form.
    parsed = urlparse("https://" + value)
    if parsed.username or parsed.password:
        return None
    if parsed.path not in ("", "/") or parsed.query or parsed.fragment:
        return None
    host = parsed.hostname
    if not host:
        return None
    port = parsed.port
    if port and port != 443:
        return f"{host}:{port}"
    return host


def evaluate_proxy_trust(
    *,
    config: HostActivationConfig,
    client_host: str | None,
    forwarded_proto: str | None,
    forwarded_host: str | None,
    host_header: str | None,
    path: str,
    proxy_token_header: str | None = None,
    duplicate_forwarded: bool = False,
) -> ProxyTrustResult:
    """
    Trust forwarded scheme/host only from the verified local proxy path (loopback peer)
    AND a shared proxy token in tailscale_https mode.
    """
    path = path or "/"
    is_health = path.rstrip("/") in {"/healthz", "/readyz"}

    if duplicate_forwarded:
        return ProxyTrustResult(False, "duplicate_forwarded_header", False, None)

    if config.trusted_proxy_mode == TRUSTED_PROXY_LOOPBACK_DIRECT:
        if not _client_is_loopback(client_host):
            return ProxyTrustResult(False, "direct_mode_requires_loopback_client", False, None)
        if is_health:
            return ProxyTrustResult(True, None, False, f"http://{config.bind_host}:{config.bind_port}")
        return ProxyTrustResult(False, "companion_requires_tailscale_https_mode", False, None)

    # tailscale_https
    if not _client_is_loopback(client_host):
        return ProxyTrustResult(False, "proxy_peer_not_loopback", False, None)

    if is_health and not (forwarded_proto or forwarded_host):
        # Direct loopback health without forwarded headers — allowed, no secrets.
        return ProxyTrustResult(True, None, False, f"http://{config.bind_host}:{config.bind_port}")

    # Any companion (or health-with-forwarded) path requires proxy shared token.
    expected_proxy = (config.proxy_shared_token or "").strip()
    if not expected_proxy:
        return ProxyTrustResult(False, "proxy_shared_token_required", False, None)
    provided = str(proxy_token_header or "")
    # Length-safe compare — never raise on mismatched token lengths (avoids 500).
    if not hmac_equal(provided, expected_proxy):
        return ProxyTrustResult(False, "proxy_token_invalid", False, None)

    proto = (forwarded_proto or "").strip().lower()
    if proto != "https":
        return ProxyTrustResult(False, "forwarded_proto_must_be_https", False, None)

    host = _normalize_forwarded_host(forwarded_host or "")
    if not host:
        return ProxyTrustResult(False, "forwarded_host_invalid", False, None)

    # Ignore Host header for origin construction when proxy mode is on.
    effective = f"https://{host}"
    expected = config.external_https_origin.rstrip("/").lower()
    if _origin_key(effective) != _origin_key(expected):
        return ProxyTrustResult(False, "external_origin_mismatch", True, effective)

    return ProxyTrustResult(True, None, True, expected)


def _origin_key(origin: str) -> str:
    p = urlparse(origin)
    host = (p.hostname or "").lower()
    port = p.port
    if port in (None, 443) and p.scheme == "https":
        return f"https://{host}"
    if port in (None, 80) and p.scheme == "http":
        return f"http://{host}"
    return f"{p.scheme}://{host}:{port}"


def cors_deny_headers() -> dict[str, str]:
    """Deny-by-default: never reflect caller Origin."""
    return {
        "Cache-Control": "no-store",
        "X-Content-Type-Options": "nosniff",
    }


def reject_browser_cors(origin_header: str | None) -> str | None:
    """If Origin is present, reject — companion host is not a browser CORS API."""
    if origin_header and origin_header.strip():
        return "cors_origin_denied"
    return None
