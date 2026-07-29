"""
HC-304BR1 — Tailscale Serve → local trusted proxy → Companion Host topology tests.

Structural / adversarial validation. Does not install Caddy or Tailscale.
"""

from __future__ import annotations

import json
import os
import re
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from backend.health_vault.companion_host import (  # noqa: E402
    ActivationError,
    load_and_validate_activation,
)
from backend.health_vault.companion_host.caddy_config import (  # noqa: E402
    CANONICAL_SET_HEADERS,
    STRIP_HEADERS,
    assert_no_literal_secrets,
    render_caddyfile,
    validate_rendered_caddyfile,
)
from backend.health_vault.companion_host.proxy_trust import evaluate_proxy_trust  # noqa: E402
from backend.health_vault.companion_host.topology import (  # noqa: E402
    RESERVED_PORTS,
    load_host_topology,
)

SCRIPTS = ROOT / "scripts" / "companion_host"
DOCS_B = ROOT / "docs" / "HC304B_PRIVATE_HOST_FOUNDATION.md"
DOCS_A = ROOT / "docs" / "HC304A_PERMANENT_HOST_READINESS.md"

_COMPANION_ENV_KEYS = (
    "HC_HOST_ACTIVATION",
    "HC_COMPANION_ADMIN_TOKEN",
    "HC_COMPANION_PEPPER",
    "HC_PROXY_SHARED_TOKEN",
    "HC_MONITORING_VAULT_ROOT",
    "HC_TRUSTED_PROXY_MODE",
    "HC_EXTERNAL_HTTPS_ORIGIN",
    "HC_EXTERNAL_HTTPS_HOST",
    "HC_BIND_HOST",
    "HC_BIND_PORT",
    "HC_PROXY_LISTEN_HOST",
    "HC_PROXY_LISTEN_PORT",
    "HC_TAILSCALE_SERVE_TARGET_PORT",
    "HC_HOST_ALLOW_TESTCLIENT_PEER",
)


@pytest.fixture(autouse=True)
def _restore_companion_process_env():
    before = {k: os.environ.get(k) for k in _COMPANION_ENV_KEYS}
    yield
    for key, value in before.items():
        if value is None:
            os.environ.pop(key, None)
        else:
            os.environ[key] = value


def _base_env(vault: Path) -> dict[str, str]:
    return {
        "HC_HOST_ACTIVATION": "enabled",
        "HC_COMPANION_ADMIN_TOKEN": "test-admin-token-24chars-min!!",
        "HC_COMPANION_PEPPER": "test-pepper-value-24chars-min!!",
        "HC_PROXY_SHARED_TOKEN": "test-proxy-shared-token-24min!!",
        "HC_MONITORING_VAULT_ROOT": str(vault),
        "HC_TRUSTED_PROXY_MODE": "tailscale_https",
        "HC_EXTERNAL_HTTPS_ORIGIN": "https://phone-host.example.ts.net",
        "HC_BIND_HOST": "127.0.0.1",
        "HC_BIND_PORT": "8743",
        "HC_PROXY_LISTEN_HOST": "127.0.0.1",
        "HC_PROXY_LISTEN_PORT": "8744",
        "HC_TAILSCALE_SERVE_TARGET_PORT": "8744",
        "HC_HOST_ALLOW_TESTCLIENT_PEER": "1",
    }


@pytest.fixture()
def monitoring_vault(tmp_path: Path) -> Path:
    return tmp_path / "monitoring_vault"


def test_topology_defaults_and_separation(monitoring_vault: Path):
    topo = load_host_topology(_base_env(monitoring_vault))
    assert topo.companion_bind_port == 8743
    assert topo.proxy_listen_port == 8744
    assert topo.tailscale_serve_target_port == 8744
    assert topo.companion_bind_port != topo.proxy_listen_port
    assert topo.companion_bind_host == "127.0.0.1"
    assert topo.proxy_listen_host == "127.0.0.1"


def test_proxy_backend_port_collision_rejected(monitoring_vault: Path):
    env = _base_env(monitoring_vault)
    env["HC_PROXY_LISTEN_PORT"] = "8743"
    env["HC_TAILSCALE_SERVE_TARGET_PORT"] = "8743"
    with pytest.raises(ActivationError) as ei:
        load_host_topology(env)
    assert ei.value.code == "proxy_backend_ports_must_differ"


def test_serve_must_target_proxy_port(monitoring_vault: Path):
    env = _base_env(monitoring_vault)
    env["HC_TAILSCALE_SERVE_TARGET_PORT"] = "8999"
    with pytest.raises(ActivationError) as ei:
        load_host_topology(env)
    assert ei.value.code == "tailscale_serve_must_target_proxy_port"


@pytest.mark.parametrize("port", sorted(RESERVED_PORTS))
def test_reserved_ports_rejected(monitoring_vault: Path, port: int):
    env = _base_env(monitoring_vault)
    env["HC_BIND_PORT"] = str(port)
    with pytest.raises(ActivationError) as ei:
        load_and_validate_activation(environ=env, repo_root=ROOT)
    assert "reserved" in ei.value.code

    env = _base_env(monitoring_vault)
    env["HC_PROXY_LISTEN_PORT"] = str(port)
    env["HC_TAILSCALE_SERVE_TARGET_PORT"] = str(port)
    with pytest.raises(ActivationError) as ei:
        load_host_topology(env)
    assert "reserved" in ei.value.code


def test_privileged_and_malformed_ports_rejected(monitoring_vault: Path):
    env = _base_env(monitoring_vault)
    env["HC_BIND_PORT"] = "443"
    with pytest.raises(ActivationError) as ei:
        load_and_validate_activation(environ=env, repo_root=ROOT)
    assert ei.value.code == "bind_port_privileged_forbidden"

    env = _base_env(monitoring_vault)
    env["HC_PROXY_LISTEN_PORT"] = "not-a-port"
    with pytest.raises(ActivationError) as ei:
        load_host_topology(env)
    assert ei.value.code == "proxy_listen_port_invalid"


def test_non_loopback_proxy_rejected(monitoring_vault: Path):
    env = _base_env(monitoring_vault)
    env["HC_PROXY_LISTEN_HOST"] = "0.0.0.0"
    with pytest.raises(ActivationError) as ei:
        load_host_topology(env)
    assert ei.value.code == "proxy_listen_host_non_loopback_forbidden"


def test_direct_tailscale_style_headers_without_token_fail(monitoring_vault: Path):
    """Simulate Serve-style forwarded headers reaching the host without proxy token."""
    env = _base_env(monitoring_vault)
    cfg = load_and_validate_activation(environ=env, repo_root=ROOT)
    result = evaluate_proxy_trust(
        config=cfg,
        client_host="127.0.0.1",
        forwarded_proto="https",
        forwarded_host="phone-host.example.ts.net",
        host_header="phone-host.example.ts.net",
        path="/api/companion/status",
        proxy_token_header=None,
    )
    assert result.ok is False
    assert result.error == "proxy_token_invalid"


def test_wrong_token_fails(monitoring_vault: Path):
    env = _base_env(monitoring_vault)
    cfg = load_and_validate_activation(environ=env, repo_root=ROOT)
    result = evaluate_proxy_trust(
        config=cfg,
        client_host="127.0.0.1",
        forwarded_proto="https",
        forwarded_host="phone-host.example.ts.net",
        host_header="phone-host.example.ts.net",
        path="/api/companion/status",
        proxy_token_header="wrong-token-value-not-matching!!",
    )
    assert result.ok is False
    assert result.error == "proxy_token_invalid"


def test_direct_backend_cannot_spoof_https_without_token(monitoring_vault: Path):
    env = _base_env(monitoring_vault)
    cfg = load_and_validate_activation(environ=env, repo_root=ROOT)
    # Attacker on loopback sets HTTPS forwarded headers but omits/forges token.
    for token in (None, "", "forged-client-token-24chars!!"):
        result = evaluate_proxy_trust(
            config=cfg,
            client_host="127.0.0.1",
            forwarded_proto="https",
            forwarded_host="phone-host.example.ts.net",
            host_header="phone-host.example.ts.net",
            path="/api/companion/observations",
            proxy_token_header=token,
        )
        assert result.ok is False


def test_caddy_render_overwrites_headers_and_uses_env_token(monitoring_vault: Path):
    env = _base_env(monitoring_vault)
    rendered = render_caddyfile(environ=env)
    text = rendered.caddyfile
    # HC-306D-R1 host-agnostic site label + loopback bind
    assert "http://:8744 {" in text or "http://:8744{" in text.replace(" ", "")
    assert re.search(r"(?m)^\s*http://:8744\s*\{", text)
    assert "http://127.0.0.1:8744" not in _caddy_active_for_assert(text)
    assert re.search(r"(?m)^\s*bind\s+127\.0\.0\.1\b", text)
    # Strip before set = overwrite design
    strip_pos = min(text.index(f"request_header -{h}") for h in STRIP_HEADERS)
    set_token_pos = text.index("header_up X-HC-Proxy-Token {env.HC_PROXY_SHARED_TOKEN}")
    set_proto_pos = text.index("header_up X-Forwarded-Proto https")
    assert strip_pos < set_token_pos
    assert strip_pos < set_proto_pos
    # Canonical trusted headers present
    assert "header_up X-Forwarded-Host {env.HC_EXTERNAL_HTTPS_HOST}" in text
    # Client-supplied token cannot survive: strip + env overwrite
    assert "request_header -X-HC-Proxy-Token" in text
    assert_no_literal_secrets(text, [env["HC_PROXY_SHARED_TOKEN"], env["HC_COMPANION_ADMIN_TOKEN"]])
    validate_rendered_caddyfile(text, topology=rendered.topology)


def test_caddy_missing_proxy_token_refuses_render(monitoring_vault: Path):
    env = _base_env(monitoring_vault)
    env["HC_PROXY_SHARED_TOKEN"] = ""
    with pytest.raises(ActivationError) as ei:
        render_caddyfile(environ=env)
    assert ei.value.code == "proxy_shared_token_required"


def test_caddy_missing_origin_refuses_render(monitoring_vault: Path):
    env = _base_env(monitoring_vault)
    env["HC_EXTERNAL_HTTPS_ORIGIN"] = ""
    with pytest.raises(ActivationError) as ei:
        render_caddyfile(environ=env)
    assert ei.value.code == "external_https_origin_required"


def test_caddy_literal_secret_assignment_rejected(monitoring_vault: Path):
    env = _base_env(monitoring_vault)
    rendered = render_caddyfile(environ=env)
    evil = rendered.caddyfile.replace(
        "header_up X-HC-Proxy-Token {env.HC_PROXY_SHARED_TOKEN}",
        "header_up X-HC-Proxy-Token literal-secret-value-here-24",
    )
    with pytest.raises(ActivationError) as ei:
        validate_rendered_caddyfile(evil, topology=rendered.topology)
    assert ei.value.code == "caddyfile_literal_secret_forbidden"


def test_templates_no_funnel_no_literal_secrets_and_rollback():
    secretish = (
        "test-proxy-shared-token-24min!!",
        "sk_live_",
        "BEGIN PRIVATE KEY",
    )
    files = list(SCRIPTS.glob("*"))
    assert files
    for path in files:
        text = path.read_text(encoding="utf-8")
        for s in secretish:
            assert s not in text
        for line in text.splitlines():
            code = line.split("#", 1)[0].strip()
            if not code:
                continue
            low = code.lower()
            # Must never invoke Funnel as a Tailscale command.
            assert "tailscale funnel" not in low
            assert not re.search(r"&\s*tailscale\s+funnel\b", low)

    serve_tpl = (SCRIPTS / "configure_tailscale_serve.ps1.template").read_text(encoding="utf-8")
    assert "tailscale serve" in serve_tpl.lower()
    assert "serve reset" in serve_tpl.lower()
    assert "serve status --json" in serve_tpl.lower()
    assert "HC_304_ALLOW_TAILSCALE_SERVE" in serve_tpl
    assert "funnel" in serve_tpl.lower()  # mentioned as forbidden
    assert "127.0.0.1" in serve_tpl
    assert "I_UNDERSTAND" in serve_tpl

    caddy_tpl = (SCRIPTS / "Caddyfile.template").read_text(encoding="utf-8")
    assert "http://:8744" in caddy_tpl
    assert "http://127.0.0.1:8744" not in _caddy_active_for_assert(caddy_tpl)
    assert "bind 127.0.0.1" in caddy_tpl
    assert "request_header -X-HC-Proxy-Token" in caddy_tpl
    assert "{env.HC_PROXY_SHARED_TOKEN}" in caddy_tpl
    assert "output discard" in caddy_tpl
    for name in STRIP_HEADERS:
        assert f"request_header -{name}" in caddy_tpl


def _caddy_active_for_assert(text: str) -> str:
    lines = []
    for line in text.splitlines():
        if "#" in line:
            line = line.split("#", 1)[0]
        lines.append(line)
    return "\n".join(lines)


def test_docs_corrected_topology_no_direct_serve_to_host():
    doc_b = DOCS_B.read_text(encoding="utf-8")
    doc_a = DOCS_A.read_text(encoding="utf-8")
    for doc in (doc_a, doc_b):
        assert "local trusted reverse proxy" in doc.lower() or "trusted local reverse proxy" in doc.lower() or "local trusted proxy" in doc.lower()
        assert "tailscale.com/kb/1242/tailscale-serve" in doc
        assert "secret-header" in doc.lower() or "custom secret-header" in doc.lower()
        assert "funnel" in doc.lower()
    # Must not claim direct Serve → Companion Host as deployable
    assert "not deployable" in doc_b.lower() or "not currently document" in doc_b.lower()
    assert "Serve → Companion Host" in doc_a or "Serve → Companion Host" in doc_b or "direct" in doc_b.lower()
    # Startup order documented
    assert "Companion Host" in doc_b
    assert "local trusted proxy" in doc_b.lower() or "Local trusted proxy" in doc_b


def test_activation_loads_with_topology_ports(monitoring_vault: Path):
    env = _base_env(monitoring_vault)
    cfg = load_and_validate_activation(environ=env, repo_root=ROOT)
    assert cfg.bind_port == 8743
    assert cfg.proxy_shared_token


def test_static_caddyfile_template_structurally_valid():
    """When executable proxy integration is unavailable, validate template structure."""
    text = (SCRIPTS / "Caddyfile.template").read_text(encoding="utf-8")
    from backend.health_vault.companion_host.topology import HostTopology

    topo = HostTopology(
        companion_bind_host="127.0.0.1",
        companion_bind_port=8743,
        proxy_listen_host="127.0.0.1",
        proxy_listen_port=8744,
        tailscale_serve_target_port=8744,
    )
    validate_rendered_caddyfile(text, topology=topo)


def test_allowfunnel_false_is_not_exposure():
    from backend.health_vault.companion_host.serve_status import (
        assert_no_funnel_exposure,
        funnel_exposure_from_serve_status,
    )

    # Key name contains "Funnel" but value is false — must NOT count as exposure.
    status = {
        "TCP": {"443": {"HTTPS": True}},
        "Web": {
            "node.example.ts.net:443": {
                "Handlers": {"/": {"Proxy": "http://127.0.0.1:8744"}}
            }
        },
        "AllowFunnel": {"node.example.ts.net:443": False},
    }
    assert funnel_exposure_from_serve_status(status) == []
    assert_no_funnel_exposure(status)
    assert funnel_exposure_from_serve_status(json.dumps(status)) == []


def test_allowfunnel_true_is_exposure():
    from backend.health_vault.companion_host.serve_status import funnel_exposure_from_serve_status

    status = {"AllowFunnel": {"node.example.ts.net:443": True}}
    assert funnel_exposure_from_serve_status(status) == ["node.example.ts.net:443"]


def test_wrong_length_proxy_token_does_not_raise(monitoring_vault: Path):
    env = _base_env(monitoring_vault)
    cfg = load_and_validate_activation(environ=env, repo_root=ROOT)
    result = evaluate_proxy_trust(
        config=cfg,
        client_host="127.0.0.1",
        forwarded_proto="https",
        forwarded_host="phone-host.example.ts.net",
        host_header="x",
        path="/api/companion/status",
        proxy_token_header="short",
    )
    assert result.ok is False
    assert result.error == "proxy_token_invalid"


def test_caddy_preserves_authorization_and_overwrites_inside_reverse_proxy(monitoring_vault: Path):
    env = _base_env(monitoring_vault)
    text = render_caddyfile(environ=env).caddyfile
    assert "request_header -Authorization" not in text
    assert "header_up -Authorization" not in text
    assert "request_header -X-HC-Proxy-Token" in text
    assert "request_header -X-Forwarded-*" in text
    # reverse_proxy must set canonical headers without conflicting deletes (HC-305F-R1)
    block = text.split("reverse_proxy", 1)[1]
    assert "header_up X-Forwarded-Proto https" in block
    assert "header_up X-Forwarded-Host {env.HC_EXTERNAL_HTTPS_HOST}" in block
    assert "header_up X-HC-Proxy-Token {env.HC_PROXY_SHARED_TOKEN}" in block
    for name in CANONICAL_SET_HEADERS:
        assert f"header_up -{name}" not in block


def test_caddy_rejects_reverse_proxy_delete_set_conflict(monitoring_vault: Path):
    env = _base_env(monitoring_vault)
    rendered = render_caddyfile(environ=env)
    # Re-introduce the Gate E defective pattern: delete then set same header.
    evil = rendered.caddyfile.replace(
        "\treverse_proxy 127.0.0.1:8743 {\n",
        "\treverse_proxy 127.0.0.1:8743 {\n\t\theader_up -X-HC-Proxy-Token\n",
    )
    assert "header_up -X-HC-Proxy-Token" in evil
    with pytest.raises(ActivationError) as ei:
        validate_rendered_caddyfile(evil, topology=rendered.topology)
    assert ei.value.code == "caddyfile_header_up_delete_set_conflict"


def test_caddy_canonical_sets_exactly_once(monitoring_vault: Path):
    env = _base_env(monitoring_vault)
    text = render_caddyfile(environ=env).caddyfile
    assert text.count("header_up X-HC-Proxy-Token {env.HC_PROXY_SHARED_TOKEN}") == 1
    assert text.count("header_up X-Forwarded-Proto https") == 1
    assert text.count("header_up X-Forwarded-Host {env.HC_EXTERNAL_HTTPS_HOST}") == 1
    # Edge strips present for all required names
    for name in STRIP_HEADERS:
        assert f"request_header -{name}" in text
    assert "request_header -X-Forwarded-*" in text
    assert "request_header -X-HC-Proxy-*" in text


def test_live_certified_caddy_injects_configured_token_not_forged(monitoring_vault: Path, tmp_path: Path):
    """
    TEMP-only integration against certified Caddy v2.11.4.
    Proves forged client token is stripped and configured env token arrives upstream.
    Never prints secret values.
    """
    import hashlib
    import json
    import os
    import socket
    import subprocess
    import threading
    import time
    import urllib.error
    import urllib.request
    from http.server import BaseHTTPRequestHandler, HTTPServer

    localapp = os.environ.get("LOCALAPPDATA") or ""
    caddy_bin = Path(localapp) / "HealthChecker" / "tools" / "caddy" / "2.11.4" / "caddy.exe"
    if not caddy_bin.is_file():
        pytest.skip("certified Caddy v2.11.4 not installed")
    expected_sha = "5CB9AB71E5756CE72840B8234177A2F40C8B4AB47A806B8E841E2B784E9DF62B"
    digest = hashlib.sha256(caddy_bin.read_bytes()).hexdigest().upper()
    if digest != expected_sha:
        pytest.skip("Caddy binary hash does not match certified install")

    def _free_port() -> int:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.bind(("127.0.0.1", 0))
            return int(s.getsockname()[1])

    upstream_port = _free_port()
    proxy_port = _free_port()
    assert upstream_port != proxy_port

    env = _base_env(monitoring_vault)
    env["HC_BIND_PORT"] = str(upstream_port)
    env["HC_PROXY_LISTEN_PORT"] = str(proxy_port)
    env["HC_TAILSCALE_SERVE_TARGET_PORT"] = str(proxy_port)
    env["HC_EXTERNAL_HTTPS_HOST"] = "phone-host.example.ts.net"
    rendered = render_caddyfile(environ=env)
    caddyfile_path = tmp_path / "Caddyfile"
    caddyfile_path.write_text(rendered.caddyfile, encoding="utf-8")
    assert_no_literal_secrets(
        rendered.caddyfile,
        [env["HC_PROXY_SHARED_TOKEN"], env["HC_COMPANION_ADMIN_TOKEN"], env["HC_COMPANION_PEPPER"]],
    )

    result: dict[str, object] = {}
    forged = "forged-client-token-24chars!!"
    auth_value = "Bearer test-device-token-value-ok"

    class Upstream(BaseHTTPRequestHandler):
        def log_message(self, *_args) -> None:
            return

        def do_GET(self) -> None:  # noqa: N802
            tok = self.headers.get("X-HC-Proxy-Token") or ""
            result.update(
                {
                    "token_eq_configured": tok == env["HC_PROXY_SHARED_TOKEN"],
                    "token_eq_forged": tok == forged,
                    "token_len": len(tok),
                    "proto": self.headers.get("X-Forwarded-Proto") or "",
                    "host_eq": (self.headers.get("X-Forwarded-Host") or "")
                    == env["HC_EXTERNAL_HTTPS_HOST"],
                    "auth_eq": (self.headers.get("Authorization") or "") == auth_value,
                }
            )
            body = b'{"ok":true}'
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

    upstream = HTTPServer(("127.0.0.1", upstream_port), Upstream)
    thread = threading.Thread(target=upstream.serve_forever, daemon=True)
    thread.start()

    caddy_env = os.environ.copy()
    caddy_env["HC_PROXY_SHARED_TOKEN"] = env["HC_PROXY_SHARED_TOKEN"]
    caddy_env["HC_EXTERNAL_HTTPS_HOST"] = env["HC_EXTERNAL_HTTPS_HOST"]
    # Fail closed if validate fails.
    validate = subprocess.run(
        [str(caddy_bin), "validate", "--config", str(caddyfile_path), "--adapter", "caddyfile"],
        capture_output=True,
        text=True,
        env=caddy_env,
        check=False,
    )
    assert validate.returncode == 0, "caddy validate failed"

    proc = subprocess.Popen(
        [str(caddy_bin), "run", "--config", str(caddyfile_path), "--adapter", "caddyfile"],
        env=caddy_env,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    try:
        deadline = time.time() + 8
        last_err = None
        while time.time() < deadline:
            try:
                req = urllib.request.Request(
                    f"http://127.0.0.1:{proxy_port}/probe",
                    headers={
                        # Synthetic non-private MagicDNS-like Host (Serve preserves this).
                        "Host": "synthetic-desktop.example.ts.net",
                        "X-HC-Proxy-Token": forged,
                        "X-Forwarded-Proto": "http",
                        "X-Forwarded-Host": "evil.example",
                        "Forwarded": "for=1.2.3.4",
                        "Authorization": auth_value,
                    },
                )
                with urllib.request.urlopen(req, timeout=2) as resp:
                    assert resp.status == 200
                    body = resp.read()
                    assert body == b'{"ok":true}', "upstream body required (not empty 200)"
                break
            except Exception as exc:  # noqa: BLE001 — wait for listener
                last_err = exc
                time.sleep(0.2)
        else:
            raise AssertionError(f"proxy did not become ready: {type(last_err).__name__}")

        # Also prove loopback Host still works after host-agnostic site label.
        req_loop = urllib.request.Request(
            f"http://127.0.0.1:{proxy_port}/probe",
            headers={
                "Host": "127.0.0.1",
                "Authorization": auth_value,
                "X-HC-Proxy-Token": forged,
                "X-Forwarded-Host": "evil.example",
            },
        )
        with urllib.request.urlopen(req_loop, timeout=2) as resp_loop:
            assert resp_loop.status == 200
            assert resp_loop.read() == b'{"ok":true}'

        assert result.get("token_eq_configured") is True
        assert result.get("token_eq_forged") is False
        assert int(result.get("token_len") or 0) >= 24
        assert result.get("proto") == "https"
        assert result.get("host_eq") is True
        assert result.get("auth_eq") is True
        # Privacy: result blob must not embed secrets if serialized in assert messages.
        blob = json.dumps(result)
        assert env["HC_PROXY_SHARED_TOKEN"] not in blob
        assert env["HC_COMPANION_ADMIN_TOKEN"] not in blob
        assert env["HC_COMPANION_PEPPER"] not in blob
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()
        upstream.shutdown()


def test_caddy_validator_rejects_unsafe_site_bind_variants(monitoring_vault: Path):
    env = _base_env(monitoring_vault)
    rendered = render_caddyfile(environ=env)
    topo = rendered.topology
    good = rendered.caddyfile

    # Missing bind
    missing_bind = re.sub(r"(?m)^\s*bind\s+127\.0\.0\.1\s*$", "", good)
    with pytest.raises(ActivationError) as ei:
        validate_rendered_caddyfile(missing_bind, topology=topo)
    assert ei.value.code == "caddyfile_bind_missing"

    # Public / non-loopback bind
    public_bind = good.replace("bind 127.0.0.1", "bind 192.0.2.10")
    with pytest.raises(ActivationError) as ei:
        validate_rendered_caddyfile(public_bind, topology=topo)
    assert ei.value.code == "caddyfile_public_bind_forbidden"

    # HTTPS site label
    https_label = good.replace("http://:8744", "https://:8744")
    with pytest.raises(ActivationError) as ei:
        validate_rendered_caddyfile(https_label, topology=topo)
    assert ei.value.code in {
        "caddyfile_https_site_label_forbidden",
        "caddyfile_site_label_host_agnostic_required",
    }

    # Wrong port on site label
    wrong_port = good.replace("http://:8744", "http://:8755")
    with pytest.raises(ActivationError) as ei:
        validate_rendered_caddyfile(wrong_port, topology=topo)
    assert ei.value.code == "caddyfile_site_label_wrong_port"

    # Old loopback-host site label forbidden
    loop_label = good.replace("http://:8744", "http://127.0.0.1:8744")
    with pytest.raises(ActivationError) as ei:
        validate_rendered_caddyfile(loop_label, topology=topo)
    assert ei.value.code == "caddyfile_site_label_loopback_host_forbidden"

    # Non-loopback upstream
    evil_up = good.replace("reverse_proxy 127.0.0.1:8743", "reverse_proxy 203.0.113.9:8743")
    with pytest.raises(ActivationError) as ei:
        validate_rendered_caddyfile(evil_up, topology=topo)
    assert ei.value.code in {
        "caddyfile_backend_missing",
        "caddyfile_upstream_non_loopback_forbidden",
    }

    # Client Host trust forbidden
    client_host = good.replace(
        "header_up X-Forwarded-Host {env.HC_EXTERNAL_HTTPS_HOST}",
        "header_up X-Forwarded-Host {http.request.host}",
    )
    with pytest.raises(ActivationError) as ei:
        validate_rendered_caddyfile(client_host, topology=topo)
    assert ei.value.code in {
        "caddyfile_forwarded_host_env_required",
        "caddyfile_client_host_trust_forbidden",
    }

    # Funnel directive forbidden
    with_funnel = good + "\nfunnel 443 {\n}\n"
    with pytest.raises(ActivationError) as ei:
        validate_rendered_caddyfile(with_funnel, topology=topo)
    assert ei.value.code == "caddyfile_funnel_forbidden"


def test_reserved_ports_8765_8877_untouched(monitoring_vault: Path):
    from backend.health_vault.companion_host.topology import RESERVED_PORTS

    assert 8765 in RESERVED_PORTS
    assert 8877 in RESERVED_PORTS
    env = _base_env(monitoring_vault)
    for field, port in (("HC_BIND_PORT", "8765"), ("HC_PROXY_LISTEN_PORT", "8877")):
        bad = dict(env)
        bad[field] = port
        if field == "HC_PROXY_LISTEN_PORT":
            bad["HC_TAILSCALE_SERVE_TARGET_PORT"] = port
        with pytest.raises(ActivationError) as ei:
            render_caddyfile(environ=bad)
        assert "reserved_forbidden" in ei.value.code


def test_authorization_not_stripped_in_render(monitoring_vault: Path):
    text = render_caddyfile(environ=_base_env(monitoring_vault)).caddyfile
    assert "request_header -Authorization" not in text
    assert "header_up -Authorization" not in text


def test_proxy_token_injected_exactly_once(monitoring_vault: Path):
    text = render_caddyfile(environ=_base_env(monitoring_vault)).caddyfile
    assert len(re.findall(r"header_up\s+X-HC-Proxy-Token\s+", text)) == 1
    assert "header_up X-HC-Proxy-Token {env.HC_PROXY_SHARED_TOKEN}" in text
    assert_no_literal_secrets(
        text,
        [
            _base_env(monitoring_vault)["HC_PROXY_SHARED_TOKEN"],
            _base_env(monitoring_vault)["HC_COMPANION_ADMIN_TOKEN"],
        ],
    )


def test_external_https_host_mismatch_refuses(monitoring_vault: Path):
    env = _base_env(monitoring_vault)
    env["HC_EXTERNAL_HTTPS_HOST"] = "evil.example.ts.net"
    with pytest.raises(ActivationError) as ei:
        render_caddyfile(environ=env)
    assert ei.value.code == "external_https_host_origin_mismatch"


def test_proxy_start_gate_summary_has_no_secrets(monitoring_vault: Path):
    from backend.health_vault.companion_host.caddy_config import assert_proxy_env_ready_for_start

    env = _base_env(monitoring_vault)
    env["HC_EXTERNAL_HTTPS_HOST"] = "phone-host.example.ts.net"
    summary = assert_proxy_env_ready_for_start(env)
    assert summary["proxy_token_configured"] is True
    blob = json.dumps(summary)
    assert env["HC_PROXY_SHARED_TOKEN"] not in blob
    assert env["HC_COMPANION_ADMIN_TOKEN"] not in blob


def test_serve_template_parses_allowfunnel_not_substring():
    serve_tpl = (SCRIPTS / "configure_tailscale_serve.ps1.template").read_text(encoding="utf-8")
    assert "ConvertFrom-Json" in serve_tpl
    assert "AllowFunnel" in serve_tpl
    assert "-eq $true" in serve_tpl
    # Must not use naive substring match alone as the sole Funnel gate.
    assert "ConvertFrom-Json" in serve_tpl
    assert "[Environment]::GetEnvironmentVariable" in serve_tpl
    assert "HC_PROXY_LISTEN_PORT" in serve_tpl
    assert "HC_BIND_PORT" in serve_tpl
    proxy_tpl = (SCRIPTS / "start_local_proxy.ps1.template").read_text(encoding="utf-8")
    assert "HC_304_ALLOW_LOCAL_PROXY" in proxy_tpl
    assert "caddy validate" in proxy_tpl.lower() or "validate --config" in proxy_tpl
    assert "value not shown" in proxy_tpl.lower() or "not shown" in proxy_tpl


def test_healthz_readyz_no_sensitive_config(monitoring_vault: Path):
    pytest.importorskip("fastapi")
    from fastapi.testclient import TestClient

    from backend.health_vault.companion_host import build_activated_app

    env = _base_env(monitoring_vault)
    app, config, _store = build_activated_app(environ=env, repo_root=ROOT)
    os.environ["HC_HOST_ALLOW_TESTCLIENT_PEER"] = "1"
    client = TestClient(app)
    for path in ("/healthz", "/readyz"):
        r = client.get(path)
        assert r.status_code == 200
        body = r.text
        assert config.proxy_shared_token not in body
        assert config.admin_token not in body
        assert config.pepper not in body
        assert "phone-host.example.ts.net" not in body
