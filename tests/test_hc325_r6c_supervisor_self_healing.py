"""HC325-R6C — production supervisor self-healing.

Behavioral coverage of the PowerShell supervisor against an isolated fake
Uvicorn child. Does not talk to live :8766, restart the production host,
touch CSS :8765, or mutate production vault/auth data.
"""

from __future__ import annotations

import json
import os
import socket
import subprocess
import sys
import time
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
LAUNCHER = ROOT / "scripts" / "start_healthchecker_production.ps1"
EXAMPLE = json.loads(
    (ROOT / "config" / "healthchecker.production.example.json").read_text(encoding="utf-8")
)
FORBIDDEN_PORTS = {8765, 8766}

FAKE_UVICORN_MAIN = r'''
import argparse
import os
import socket
import sys
import time
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

CONTROL = Path(os.environ["HC325_R6C_FAKE_CONTROL"])
CONTROL.mkdir(parents=True, exist_ok=True)


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.send_header("Content-Type", "text/plain; charset=utf-8")
        self.end_headers()
        self.wfile.write(b"HealthChecker")

    def log_message(self, *_args):
        return


def _consume(name):
    path = CONTROL / name
    if not path.exists():
        return False
    try:
        path.unlink()
    except OSError:
        pass
    return True


def main():
    args = argparse.ArgumentParser()
    args.add_argument("app", nargs="?")
    args.add_argument("--factory", action="store_true")
    args.add_argument("--host", default="127.0.0.1")
    args.add_argument("--port", type=int, required=True)
    args.add_argument("--no-access-log", action="store_true")
    ns = args.parse_args()
    (CONTROL / "child.pid").write_text(str(os.getpid()), encoding="ascii")
    with (CONTROL / "starts.log").open("a", encoding="ascii") as handle:
        handle.write("%s %s\n" % (os.getpid(), time.time()))
    auto_exit = float(os.environ.get("HC325_R6C_FAKE_AUTO_EXIT_SEC", "0") or "0")
    auto_drop = float(os.environ.get("HC325_R6C_FAKE_AUTO_DROP_SEC", "0") or "0")
    httpd = HTTPServer((ns.host, ns.port), Handler)
    httpd.timeout = 0.2
    serving = True
    started = time.time()
    while True:
        if _consume("exit"):
            sys.exit(1)
        if auto_exit and (time.time() - started) >= auto_exit:
            sys.exit(1)
        if serving and (_consume("drop_listen") or (auto_drop and (time.time() - started) >= auto_drop)):
            try:
                httpd.socket.shutdown(socket.SHUT_RDWR)
            except Exception:
                pass
            try:
                httpd.socket.close()
            except Exception:
                pass
            try:
                httpd.server_close()
            except Exception:
                pass
            serving = False
        if serving:
            httpd.handle_request()
        else:
            time.sleep(0.2)


if __name__ == "__main__":
    main()
'''


def _free_loopback_port() -> int:
    for _ in range(40):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            sock.bind(("127.0.0.1", 0))
            port = int(sock.getsockname()[1])
        if port not in FORBIDDEN_PORTS:
            return port
    raise RuntimeError("no isolated loopback port available")


def _write_fake_uvicorn(root: Path) -> Path:
    pkg = root / "uvicorn"
    pkg.mkdir(parents=True, exist_ok=True)
    (pkg / "__init__.py").write_text("", encoding="utf-8")
    (pkg / "__main__.py").write_text(FAKE_UVICORN_MAIN, encoding="utf-8")
    return root


def _isolated_config(tmp_path: Path, *, port: int, restart_limit: int, backoff: int) -> Path:
    assert port not in FORBIDDEN_PORTS
    state_dir = tmp_path / "state"
    log_dir = tmp_path / "logs"
    state_dir.mkdir()
    log_dir.mkdir()
    payload = dict(EXAMPLE)
    payload.update(
        {
            "bind_address": "127.0.0.1",
            "port": port,
            "runtime_state_dir": str(state_dir),
            "log_dir": str(log_dir),
            "restart_limit": restart_limit,
            "restart_backoff_seconds": backoff,
        }
    )
    path = tmp_path / "production.json"
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    return path


def _read_text_retry(path: Path, attempts: int = 25) -> str | None:
    if not path.is_file():
        return None
    for _ in range(attempts):
        try:
            return path.read_text(encoding="utf-8-sig")
        except (PermissionError, OSError):
            time.sleep(0.05)
    return None


def _read_json(path: Path):
    text = _read_text_retry(path)
    if text is None:
        return None
    text = text.strip()
    if not text:
        return None
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return None


def _wait_until(predicate, timeout: float, interval: float = 0.1):
    deadline = time.time() + timeout
    last = None
    while time.time() < deadline:
        last = predicate()
        if last:
            return last
        time.sleep(interval)
    return last


def _pid_path(config: dict) -> Path:
    return Path(config["runtime_state_dir"]) / "healthchecker-consumer-api.pid"


def _heartbeat_path(config: dict) -> Path:
    return Path(config["runtime_state_dir"]) / "healthchecker-consumer-api.heartbeat.json"


def _log_path(config: dict) -> Path:
    return Path(config["log_dir"]) / "healthchecker-runtime.log"


def _control_dir(tmp_path: Path) -> Path:
    path = tmp_path / "fake-control"
    path.mkdir(exist_ok=True)
    return path


def _starts(control: Path) -> list[int]:
    log = control / "starts.log"
    text = _read_text_retry(log)
    if not text:
        return []
    pids = []
    for line in text.splitlines():
        if line.strip():
            pids.append(int(line.split()[0]))
    return pids


def _child_pids(parent_pid: int) -> list[int]:
    cmd = (
        "Get-CimInstance Win32_Process -ErrorAction SilentlyContinue | "
        f"Where-Object {{ $_.ParentProcessId -eq {int(parent_pid)} -and $_.Name -match 'python' }} | "
        "Select-Object -ExpandProperty ProcessId"
    )
    completed = subprocess.run(
        ["powershell.exe", "-NoProfile", "-Command", cmd],
        capture_output=True,
        text=True,
        check=False,
    )
    pids = []
    for line in completed.stdout.splitlines():
        line = line.strip()
        if line.isdigit():
            pids.append(int(line))
    return pids


def _alive(pid: int) -> bool:
    if pid <= 0:
        return False
    completed = subprocess.run(
        ["tasklist", "/FI", f"PID eq {pid}"],
        capture_output=True,
        text=True,
        check=False,
    )
    return str(pid) in completed.stdout and "No tasks" not in completed.stdout


def _kill_tree(pid: int) -> None:
    if pid <= 0:
        return
    subprocess.run(
        ["taskkill", "/PID", str(pid), "/T", "/F"],
        capture_output=True,
        text=True,
        check=False,
    )


def _start_supervisor(config_path: Path, tmp_path: Path, extra_env: dict | None = None):
    fake_path = _write_fake_uvicorn(tmp_path / "fake-pythonpath")
    control = _control_dir(tmp_path)
    env = os.environ.copy()
    env["HEALTHCHECKER_MANAGED_PYTHON"] = sys.executable
    existing = env.get("PYTHONPATH", "")
    env["PYTHONPATH"] = str(fake_path) + ((";" + existing) if existing else "")
    env["HC325_R6C_FAKE_CONTROL"] = str(control)
    if extra_env:
        env.update(extra_env)
    proc = subprocess.Popen(
        [
            "powershell.exe",
            "-NoProfile",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            str(LAUNCHER),
            "-ConfigPath",
            str(config_path),
        ],
        cwd=str(ROOT),
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    return proc, json.loads(config_path.read_text(encoding="utf-8")), control


def _wait_state(config: dict, wanted: str, timeout: float = 20.0):
    def _pred():
        body = _read_json(_heartbeat_path(config))
        if body and body.get("state") == wanted:
            return body
        return None

    found = _wait_until(_pred, timeout)
    assert found is not None, f"heartbeat never reached {wanted}: {_read_json(_heartbeat_path(config))}"
    return found


def _wait_child_count(control: Path, count: int, timeout: float = 20.0) -> list[int]:
    found = _wait_until(lambda: _starts(control) if len(_starts(control)) >= count else None, timeout)
    assert found is not None, f"expected {count} child starts, got {_starts(control)}"
    return found


# --- source contracts (no live runtime) ---


def test_supervisor_remains_singleton_owner_and_keeps_safety_gates():
    text = LAUNCHER.read_text(encoding="utf-8")
    assert "instance_already_running" in text
    assert "$PID | Set-Content -LiteralPath $pidPath" in text
    assert "port_already_occupied" in text
    assert "css_port_collision_forbidden" in text
    assert "approved_https_origin_required" in text
    assert "loopback_bind_required" in text
    assert "restart_limit_exceeded" in text
    assert "Remove-Item -LiteralPath $pidPath" in text
    assert "healthchecker-consumer-api.heartbeat.json" in text
    assert "Start-HcUvicornChild" in text
    assert "Test-HcLoopbackService" in text
    assert 'state   = "failed"' in text or 'State "failed"' in text
    assert "& $python -m uvicorn" not in text
    assert "--ssl-certfile" not in text
    assert "backend.health_vault.api:create_health_vault_app" in text
    assert "--no-access-log" in text
    log_lines = [line for line in text.splitlines() if "Add-Content" in line]
    joined = "\n".join(log_lines).lower()
    for forbidden in ("publicorigin", "credentials", "token", "password", "patient"):
        assert forbidden not in joined


def test_isolated_harness_never_targets_production_or_css_ports():
    port = _free_loopback_port()
    assert port not in FORBIDDEN_PORTS
    assert EXAMPLE["port"] == 8766
    assert EXAMPLE["bind_address"] == "127.0.0.1"


def test_normal_child_remains_healthy(tmp_path: Path):
    port = _free_loopback_port()
    config_path = _isolated_config(tmp_path, port=port, restart_limit=5, backoff=1)
    proc, config, control = _start_supervisor(config_path, tmp_path)
    try:
        running = _wait_state(config, "running")
        starts = _wait_child_count(control, 1)
        supervisor_pid = int(_read_text_retry(_pid_path(config)) or "0")
        assert supervisor_pid == proc.pid
        assert running["state"] == "running"
        assert _alive(starts[0])
        time.sleep(2.2)
        still = _read_json(_heartbeat_path(config))
        assert still["state"] == "running"
        assert len(_starts(control)) == 1
        children = _child_pids(proc.pid)
        assert len(children) == 1
        assert proc.poll() is None
    finally:
        _kill_tree(proc.pid)


def test_child_exit_restarts(tmp_path: Path):
    port = _free_loopback_port()
    config_path = _isolated_config(tmp_path, port=port, restart_limit=5, backoff=1)
    proc, config, control = _start_supervisor(config_path, tmp_path)
    try:
        _wait_state(config, "running")
        first = _wait_child_count(control, 1)[0]
        (control / "exit").write_text("1", encoding="ascii")
        _wait_until(lambda: len(_starts(control)) >= 2, 15)
        second = _starts(control)[1]
        _wait_state(config, "running", timeout=15)
        assert second != first
        assert not _alive(first)
        assert _alive(second)
        states = _read_json(_heartbeat_path(config))
        assert states["state"] == "running"
        assert states["attempt"] == 2
    finally:
        _kill_tree(proc.pid)


def test_child_alive_but_port_disappears_is_reaped_and_restarted(tmp_path: Path):
    port = _free_loopback_port()
    config_path = _isolated_config(tmp_path, port=port, restart_limit=5, backoff=1)
    proc, config, control = _start_supervisor(config_path, tmp_path)
    try:
        _wait_state(config, "running")
        first = _wait_child_count(control, 1)[0]
        assert _alive(first)
        (control / "drop_listen").write_text("1", encoding="ascii")
        restarted = _wait_until(lambda: len(_starts(control)) >= 2, 20)
        assert restarted, (
            "child was not restarted after listen drop; "
            f"starts={_starts(control)} heartbeat={_read_json(_heartbeat_path(config))} "
            f"log={_read_text_retry(_log_path(config)) or ''}"
        )
        second = _starts(control)[1]
        _wait_state(config, "running", timeout=15)
        assert second != first
        assert not _alive(first)
        assert _alive(second)
        log = _read_text_retry(_log_path(config)) or ""
        assert "reason=service_unavailable" in log
        children = _child_pids(proc.pid)
        assert len(children) == 1
    finally:
        _kill_tree(proc.pid)


def test_restart_backoff_is_applied(tmp_path: Path):
    port = _free_loopback_port()
    config_path = _isolated_config(tmp_path, port=port, restart_limit=5, backoff=2)
    extra = {"HC325_R6C_FAKE_AUTO_EXIT_SEC": "0.05"}
    proc, config, control = _start_supervisor(config_path, tmp_path, extra_env=extra)
    try:
        _wait_until(lambda: len(_starts(control)) >= 2, 20)
        log = _wait_until(
            lambda: _log_path(config).is_file()
            and _read_text_retry(_log_path(config))
            and "event=runtime_backoff seconds=2 attempt=1" in (_read_text_retry(_log_path(config)) or ""),
            15,
        )
        assert log
        text = _read_text_retry(_log_path(config)) or ""
        assert "event=runtime_backoff seconds=2 attempt=1" in text
        starts_log = (_read_text_retry(control / "starts.log") or "").splitlines()
        t0 = float(starts_log[0].split()[1])
        t1 = float(starts_log[1].split()[1])
        assert (t1 - t0) >= 1.8
    finally:
        _kill_tree(proc.pid)


def test_restart_limit_exhaustion_failed_state_and_pid_cleanup(tmp_path: Path):
    port = _free_loopback_port()
    config_path = _isolated_config(tmp_path, port=port, restart_limit=1, backoff=1)
    extra = {"HC325_R6C_FAKE_AUTO_EXIT_SEC": "0.05"}
    proc, config, control = _start_supervisor(config_path, tmp_path, extra_env=extra)
    try:
        _wait_state(config, "failed", timeout=25)
        proc.wait(timeout=15)
        assert proc.returncode not in (0, None)
        assert not _pid_path(config).exists()
        body = _read_json(_heartbeat_path(config))
        assert body["state"] == "failed"
        assert body.get("reason") == "restart_limit_exceeded"
        assert len(_starts(control)) >= 2
        for pid in _starts(control):
            assert not _alive(pid)
        combined = (proc.stderr.read() or "") + (proc.stdout.read() or "")
        assert "restart_limit_exceeded" in combined
    finally:
        if proc.poll() is None:
            _kill_tree(proc.pid)


def test_failed_supervisor_does_not_block_later_governed_start(tmp_path: Path):
    port = _free_loopback_port()
    config_path = _isolated_config(tmp_path, port=port, restart_limit=0, backoff=1)
    extra = {"HC325_R6C_FAKE_AUTO_EXIT_SEC": "0.05"}
    first, config, control = _start_supervisor(config_path, tmp_path, extra_env=extra)
    try:
        _wait_state(config, "failed", timeout=20)
        first.wait(timeout=10)
        assert not _pid_path(config).exists()
    finally:
        if first.poll() is None:
            _kill_tree(first.pid)

    healthy_env = {"HC325_R6C_FAKE_AUTO_EXIT_SEC": "0"}
    second, config, control = _start_supervisor(config_path, tmp_path, extra_env=healthy_env)
    try:
        running = _wait_state(config, "running", timeout=20)
        assert running["state"] == "running"
        assert _pid_path(config).exists()
        assert int(_read_text_retry(_pid_path(config)) or "0") == second.pid
    finally:
        _kill_tree(second.pid)


def test_singleton_protection_instance_already_running(tmp_path: Path):
    port = _free_loopback_port()
    config_path = _isolated_config(tmp_path, port=port, restart_limit=5, backoff=1)
    first, config, _control = _start_supervisor(config_path, tmp_path)
    second = None
    try:
        _wait_state(config, "running")
        second, _, _ = _start_supervisor(config_path, tmp_path)
        second.wait(timeout=20)
        combined = (second.stderr.read() or "") + (second.stdout.read() or "")
        assert second.returncode not in (0, None)
        assert "instance_already_running" in combined
        assert first.poll() is None
        assert int(_read_text_retry(_pid_path(config)) or "0") == first.pid
    finally:
        if second and second.poll() is None:
            _kill_tree(second.pid)
        _kill_tree(first.pid)


def test_no_duplicate_child_during_healthy_and_restart(tmp_path: Path):
    port = _free_loopback_port()
    config_path = _isolated_config(tmp_path, port=port, restart_limit=5, backoff=1)
    proc, config, control = _start_supervisor(config_path, tmp_path)
    try:
        _wait_state(config, "running")
        _wait_child_count(control, 1)
        samples = []
        for _ in range(6):
            samples.append(len(_child_pids(proc.pid)))
            time.sleep(0.25)
        assert max(samples) == 1
        (control / "exit").write_text("1", encoding="ascii")
        _wait_child_count(control, 2, timeout=15)
        _wait_state(config, "running", timeout=15)
        children = _child_pids(proc.pid)
        assert len(children) == 1
        assert max(len(_child_pids(proc.pid)) for _ in range(4)) == 1
    finally:
        _kill_tree(proc.pid)


def test_heartbeat_state_transitions_on_child_exit(tmp_path: Path):
    port = _free_loopback_port()
    config_path = _isolated_config(tmp_path, port=port, restart_limit=5, backoff=1)
    proc, config, control = _start_supervisor(config_path, tmp_path)
    try:
        first = _wait_state(config, "running")
        assert first["attempt"] == 1
        (control / "exit").write_text("1", encoding="ascii")
        _wait_until(
            lambda: (_read_json(_heartbeat_path(config)) or {}).get("state") == "restarting",
            10,
        )
        restarted = _wait_state(config, "running", timeout=15)
        assert restarted["attempt"] == 2
        assert restarted["state"] == "running"
        log = _read_text_retry(_log_path(config)) or ""
        assert "event=runtime_start attempt=1" in log
        assert "event=runtime_healthy attempt=1" in log
        assert "event=runtime_unhealthy reason=child_exit" in log
        assert "event=runtime_start attempt=2" in log
        assert "event=runtime_healthy attempt=2" in log
    finally:
        _kill_tree(proc.pid)
