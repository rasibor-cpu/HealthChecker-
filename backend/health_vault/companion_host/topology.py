"""
HC-304BR1 topology: Tailscale Serve → local trusted proxy → Companion Host.

Port placeholders (operator may change; reserved ports are always rejected):
  HC_BIND_PORT              Companion Host loopback (default 8743)
  HC_PROXY_LISTEN_PORT      Caddy loopback listener (default 8744)
  HC_TAILSCALE_SERVE_TARGET_PORT  Must equal proxy listen (Serve → proxy only)
"""

from __future__ import annotations

from dataclasses import dataclass

from backend.health_vault.companion_host.activation import ActivationError

# Historical / conflicting ports — never use for permanent host topology.
RESERVED_PORTS = frozenset(
    {
        8765,  # CSS / historical local host
        8877,  # HC-303D temp pairing
    }
)

DEFAULT_COMPANION_PORT = 8743
DEFAULT_PROXY_PORT = 8744


@dataclass(frozen=True)
class HostTopology:
    companion_bind_host: str
    companion_bind_port: int
    proxy_listen_host: str
    proxy_listen_port: int
    tailscale_serve_target_port: int

    def public_dict(self) -> dict[str, object]:
        return {
            "companion_bind": f"{self.companion_bind_host}:{self.companion_bind_port}",
            "proxy_listen": f"{self.proxy_listen_host}:{self.proxy_listen_port}",
            "tailscale_serve_target": f"127.0.0.1:{self.tailscale_serve_target_port}",
            "topology": "tailscale_serve -> loopback_proxy -> companion_host",
        }


def validate_topology_port(port_raw: str, *, field: str) -> int:
    if not str(port_raw or "").strip():
        raise ActivationError(f"{field}_required")
    try:
        port = int(str(port_raw).strip())
    except ValueError as exc:
        raise ActivationError(f"{field}_invalid") from exc
    if port < 1 or port > 65535:
        raise ActivationError(f"{field}_invalid")
    if port < 1024:
        raise ActivationError(f"{field}_privileged_forbidden")
    if port in RESERVED_PORTS:
        raise ActivationError(f"{field}_reserved_forbidden")
    return port


def validate_loopback_host(host: str, *, field: str) -> str:
    h = (host or "").strip().lower()
    if h in {"127.0.0.1", "localhost"}:
        return "127.0.0.1"
    if h == "::1":
        return "::1"
    raise ActivationError(f"{field}_non_loopback_forbidden")


def load_host_topology(environ: dict[str, str] | None = None) -> HostTopology:
    import os

    env = {k: str(v) for k, v in (environ if environ is not None else os.environ).items()}
    companion_host = validate_loopback_host(
        env.get("HC_BIND_HOST", "127.0.0.1"), field="bind_host"
    )
    companion_port = validate_topology_port(
        env.get("HC_BIND_PORT", str(DEFAULT_COMPANION_PORT)), field="bind_port"
    )
    proxy_host = validate_loopback_host(
        env.get("HC_PROXY_LISTEN_HOST", "127.0.0.1"), field="proxy_listen_host"
    )
    proxy_port = validate_topology_port(
        env.get("HC_PROXY_LISTEN_PORT", str(DEFAULT_PROXY_PORT)), field="proxy_listen_port"
    )
    serve_port = validate_topology_port(
        env.get("HC_TAILSCALE_SERVE_TARGET_PORT", str(proxy_port)),
        field="tailscale_serve_target_port",
    )

    if companion_port == proxy_port:
        raise ActivationError("proxy_backend_ports_must_differ")
    if serve_port != proxy_port:
        raise ActivationError("tailscale_serve_must_target_proxy_port")

    return HostTopology(
        companion_bind_host=companion_host,
        companion_bind_port=companion_port,
        proxy_listen_host=proxy_host,
        proxy_listen_port=proxy_port,
        tailscale_serve_target_port=serve_port,
    )
