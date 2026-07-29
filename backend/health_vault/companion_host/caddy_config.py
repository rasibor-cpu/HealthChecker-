"""
Render and structurally validate an inert Caddy reverse-proxy configuration.

Secrets are referenced as environment placeholders only — never inlined.
Does not install or download Caddy.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass

from backend.health_vault.companion_host.activation import ActivationError
from backend.health_vault.companion_host.topology import HostTopology, load_host_topology

# Headers that must be stripped from the inbound client before the proxy sets trusted values.
# Exact names plus wildcard forms so case/variant duplicates cannot survive into upstream.
STRIP_HEADERS = (
    "X-HC-Proxy-Token",
    "Forwarded",
    "X-Forwarded-For",
    "X-Forwarded-Host",
    "X-Forwarded-Proto",
    "X-Forwarded-Port",
)

# Additional wildcard deletes (Caddy request_header supports prefix/suffix *).
STRIP_HEADER_WILDCARDS = (
    "X-Forwarded-*",
    "X-HC-Proxy-*",
)

# Canonical trusted headers set once inside reverse_proxy (never also header_up-deleted).
# HC-305F Gate E: Caddy v2.11.4 applies reverse_proxy header deletes in a way that
# drops a subsequent header_up set of the same name (proxy_token_invalid upstream).
CANONICAL_SET_HEADERS = (
    "X-Forwarded-Proto",
    "X-Forwarded-Host",
    "X-HC-Proxy-Token",
)

# Never strip Authorization — Companion device Bearer delivery depends on it.
PRESERVE_HEADERS = ("Authorization",)

FORBIDDEN_LITERALS = (
    "0.0.0.0",
    ":80 {",
    ":443 {",
)


@dataclass(frozen=True)
class CaddyRenderResult:
    caddyfile: str
    topology: HostTopology


def _require_proxy_env(environ: dict[str, str]) -> tuple[str, str]:
    from backend.health_vault.companion_host.activation import (
        validate_external_https_origin,
        validate_secret_value,
    )

    token = validate_secret_value(
        environ.get("HC_PROXY_SHARED_TOKEN", ""), field="proxy_shared_token"
    )
    origin = validate_external_https_origin(environ.get("HC_EXTERNAL_HTTPS_ORIGIN", ""))
    # Host-only for X-Forwarded-Host (no scheme).
    host = origin.split("://", 1)[1]
    if not host or "/" in host or "@" in host or "?" in host or "#" in host:
        raise ActivationError("external_https_origin_invalid")
    # If operator set HC_EXTERNAL_HTTPS_HOST, it must match the origin host exactly.
    configured_host = environ.get("HC_EXTERNAL_HTTPS_HOST", "").strip().lower()
    if configured_host and configured_host != host.lower():
        raise ActivationError("external_https_host_origin_mismatch")
    return token, host


def render_caddyfile(
    *,
    environ: dict[str, str] | None = None,
    topology: HostTopology | None = None,
) -> CaddyRenderResult:
    """
    Render a Caddyfile that:
    - binds proxy to 127.0.0.1 only
    - reverse_proxies to Companion Host on a different loopback port
    - strips inbound untrusted headers at the edge (exact + wildcard request_header)
    - sets canonical trusted headers once inside reverse_proxy (no matching header_up delete)
    - injects X-HC-Proxy-Token via {env.HC_PROXY_SHARED_TOKEN} (never a literal secret)
    - preserves Authorization by never listing it in strip directives
    - discards access logs by default (startup/config errors still go to stderr)
    """
    env = {k: str(v) for k, v in (environ if environ is not None else os.environ).items()}
    topo = topology or load_host_topology(env)
    _require_proxy_env(env)

    strip_lines = "\n".join(
        [f"\trequest_header -{name}" for name in STRIP_HEADERS]
        + [f"\trequest_header -{name}" for name in STRIP_HEADER_WILDCARDS]
    )
    # Edge strip only — do not header_up-delete names that are also set (Caddy v2.11.4).
    text = f"""# HC-304BR2 trusted local reverse proxy (Caddy)
# Topology: Tailscale Serve → this proxy → Companion Host
# Ordering: edge request_header strip → reverse_proxy canonical header_up set → Companion Host
# DO NOT put secrets in this file. Use process environment:
#   HC_PROXY_SHARED_TOKEN, HC_EXTERNAL_HTTPS_HOST (hostname from HC_EXTERNAL_HTTPS_ORIGIN)
# Bind: loopback only. No Funnel. No public/LAN listen.
# Authorization is preserved (not listed in strip directives).
# Access log discarded; Caddy startup/config failures still print to stderr (no secrets).
#
# Generated structurally — review before use. Caddy is NOT installed by this repo.

{{
\tauto_https off
\tadmin off
}}

http://{topo.proxy_listen_host}:{topo.proxy_listen_port} {{
\tbind {topo.proxy_listen_host}
\trequest_body {{
\t\tmax_size 512KB
\t}}
{strip_lines}
\treverse_proxy {topo.companion_bind_host}:{topo.companion_bind_port} {{
\t\theader_up X-Forwarded-Proto https
\t\theader_up X-Forwarded-Host {{env.HC_EXTERNAL_HTTPS_HOST}}
\t\theader_up X-HC-Proxy-Token {{env.HC_PROXY_SHARED_TOKEN}}
\t\ttransport http {{
\t\t\tdial_timeout 5s
\t\t\tresponse_header_timeout 60s
\t\t}}
\t}}
\tlog {{
\t\toutput discard
\t}}
}}
"""
    validate_rendered_caddyfile(text, topology=topo)
    return CaddyRenderResult(caddyfile=text, topology=topo)


def _caddy_active_text(text: str) -> str:
    """Strip #-comments so advisory prose (e.g. 'no Funnel') is not treated as config."""
    lines: list[str] = []
    for line in text.splitlines():
        if "#" in line:
            line = line.split("#", 1)[0]
        lines.append(line)
    return "\n".join(lines)


def validate_rendered_caddyfile(text: str, *, topology: HostTopology) -> None:
    active = _caddy_active_text(text)
    low = active.lower()
    for bad in FORBIDDEN_LITERALS:
        if bad in low:
            raise ActivationError("caddyfile_public_or_funnel_forbidden")
    # Funnel must not appear as an active directive (comments already stripped).
    if re.search(r"\bfunnel\b", low):
        raise ActivationError("caddyfile_funnel_forbidden")
    if f"bind {topology.proxy_listen_host}" not in text:
        raise ActivationError("caddyfile_bind_missing")
    if f"reverse_proxy {topology.companion_bind_host}:{topology.companion_bind_port}" not in text:
        raise ActivationError("caddyfile_backend_missing")
    if topology.proxy_listen_port == topology.companion_bind_port:
        raise ActivationError("proxy_backend_ports_must_differ")
    for name in STRIP_HEADERS:
        if f"request_header -{name}" not in text:
            raise ActivationError("caddyfile_strip_incomplete")
    for name in STRIP_HEADER_WILDCARDS:
        if f"request_header -{name}" not in text:
            raise ActivationError("caddyfile_wildcard_strip_incomplete")
    # Edge strip must appear before reverse_proxy in active config (ignore comments).
    strip_pos = active.find("request_header -X-HC-Proxy-Token")
    rp_idx = active.find("reverse_proxy")
    if rp_idx < 0:
        raise ActivationError("caddyfile_backend_missing")
    if strip_pos < 0 or strip_pos > rp_idx:
        raise ActivationError("caddyfile_edge_strip_before_proxy_required")
    block = active[rp_idx:]
    # Authorization must not be stripped.
    for name in PRESERVE_HEADERS:
        if re.search(rf"(?i)request_header\s+-{re.escape(name)}\b", text):
            raise ActivationError("caddyfile_authorization_must_be_preserved")
        if re.search(rf"(?i)header_up\s+-{re.escape(name)}\b", text):
            raise ActivationError("caddyfile_authorization_must_be_preserved")
    # Token must come from env placeholder — never a literal secret; exactly one set.
    token_assigns = re.findall(r"header_up\s+X-HC-Proxy-Token\s+(\S+)", text)
    if len(token_assigns) != 1:
        raise ActivationError("caddyfile_proxy_token_env_required")
    if token_assigns[0] != "{env.HC_PROXY_SHARED_TOKEN}":
        raise ActivationError("caddyfile_literal_secret_forbidden")
    proto_assigns = re.findall(r"header_up\s+X-Forwarded-Proto\s+(\S+)", text)
    if proto_assigns != ["https"]:
        raise ActivationError("caddyfile_forwarded_proto_required")
    host_assigns = re.findall(r"header_up\s+X-Forwarded-Host\s+(\S+)", text)
    if host_assigns != ["{env.HC_EXTERNAL_HTTPS_HOST}"]:
        raise ActivationError("caddyfile_forwarded_host_env_required")
    # No reverse_proxy delete for any header that is also canonically set (HC-305F-R1).
    for name in CANONICAL_SET_HEADERS:
        if re.search(rf"(?im)^\s*header_up\s+-{re.escape(name)}\b", block):
            raise ActivationError("caddyfile_header_up_delete_set_conflict")
    if "output discard" not in text and "log {\n\t\toff" not in text:
        raise ActivationError("caddyfile_access_log_must_be_privacy_safe")
    if "request_body" not in text or "max_size" not in text:
        raise ActivationError("caddyfile_body_limit_required")
    if "dial_timeout" not in text or "response_header_timeout" not in text:
        raise ActivationError("caddyfile_time_limits_required")


def assert_no_literal_secrets(text: str, secrets: list[str]) -> None:
    for secret in secrets:
        if secret and secret in text:
            raise ActivationError("caddyfile_literal_secret_forbidden")


def assert_proxy_env_ready_for_start(environ: dict[str, str] | None = None) -> dict[str, str]:
    """
    Fail closed before launching Caddy: require proxy token + HTTPS origin/host.
    Returns a non-secret public summary only.
    """
    env = {k: str(v) for k, v in (environ if environ is not None else os.environ).items()}
    token, host = _require_proxy_env(env)
    topo = load_host_topology(env)
    # Touch token length only — never return the secret.
    return {
        "proxy_token_configured": len(token) >= 24,
        "external_https_host": host,
        "proxy_listen": f"{topo.proxy_listen_host}:{topo.proxy_listen_port}",
        "companion_bind": f"{topo.companion_bind_host}:{topo.companion_bind_port}",
    }
