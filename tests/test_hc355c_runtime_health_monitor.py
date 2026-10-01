"""HC355-C — governed runtime health verification hardening.

Covers scripts/Test-HealthCheckerRuntimeHealth.ps1's
Test-HealthCheckerRuntimeHealth function against isolated fake
supervisor/child processes and state files. Never touches the real
scheduled task, port 8766, or production state.

HTTP 200 alone must not be sufficient for a HEALTHY verdict: an orphaned
child process serving /healthz must be classified UNHEALTHY whenever the
supervisor is dead, the heartbeat is stale, or the child's parent does not
match the live supervisor.
"""

from __future__ import annotations

import json
import socket
import subprocess
import sys
import time
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from threading import Thread

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "Test-HealthCheckerRuntimeHealth.ps1"
FORBIDDEN_PORTS = {8765, 8766}


def _free_loopback_port() -> int:
    for _ in range(40):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            sock.bind(("127.0.0.1", 0))
            port = int(sock.getsockname()[1])
        if port not in FORBIDDEN_PORTS:
            return port
    raise RuntimeError("no isolated loopback port available")


class _HealthzHandler(BaseHTTPRequestHandler):
    def do_GET(self):  # noqa: N802 - stdlib handler name
        if self.path.split("?", 1)[0] == "/healthz":
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(b'{"status":"ok"}')
        else:
            self.send_response(404)
            self.end_headers()

    def log_message(self, *_args):
        return


def _start_fake_child_server(port: int) -> HTTPServer:
    httpd = HTTPServer(("127.0.0.1", port), _HealthzHandler)
    thread = Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    return httpd


def _start_background_process() -> subprocess.Popen:
    """A real, long-lived process usable as a fake supervisor or child PID."""
    return subprocess.Popen(
        [sys.executable, "-c", "import time; time.sleep(120)"],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )


def _write_heartbeat(
    path: Path,
    *,
    state: str = "running",
    child_pid: int = 0,
    age_seconds: float = 0.0,
) -> None:
    at = datetime.now(timezone.utc) - timedelta(seconds=age_seconds)
    payload = {
        "service": "healthchecker.consumer.api",
        "state": state,
        "at_utc": at.strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z",
    }
    if child_pid:
        payload["child_pid"] = child_pid
    path.write_text(json.dumps(payload), encoding="utf-8")


def _run_health_check(
    tmp_path: Path,
    *,
    bind_address: str = "127.0.0.1",
    port: int,
    heartbeat_staleness_seconds: int = 30,
) -> dict:
    cmd = (
        f". '{SCRIPT}'; "
        "$r = Test-HealthCheckerRuntimeHealth "
        f"-RuntimeStateDir '{tmp_path}' "
        f"-BindAddress '{bind_address}' "
        f"-Port {port} "
        f"-HeartbeatStalenessSeconds {heartbeat_staleness_seconds}; "
        "$r | ConvertTo-Json -Depth 5"
    )
    completed = subprocess.run(
        ["powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", cmd],
        capture_output=True,
        text=True,
        check=False,
        timeout=30,
    )
    assert completed.returncode in (0, 1), (
        f"health check script failed unexpectedly: rc={completed.returncode} "
        f"stdout={completed.stdout!r} stderr={completed.stderr!r}"
    )
    return json.loads(completed.stdout)


def test_scenario_a_healthy_chain_via_powershell_parent(tmp_path: Path):
    """Scenario A using a real PowerShell-spawned parent/child pair.

    PowerShell launches a child python process so Win32_Process.ParentProcessId
    genuinely equals the "supervisor" PID, matching production topology.
    """
    port = _free_loopback_port()
    # Launch a long-lived powershell.exe ("supervisor") that itself starts a
    # python child process via .NET Process.Start, so Win32_Process
    # .ParentProcessId of the child genuinely equals the supervisor's own PID,
    # matching production topology (the real script starts uvicorn the same
    # way via System.Diagnostics.Process). The child itself serves /healthz so
    # it genuinely owns the listening socket (not the test process).
    child_pid_file = tmp_path / "child_pid.txt"
    child_script_path = tmp_path / "fake_child_server.py"
    child_script_path.write_text(
        "import sys\n"
        "from http.server import BaseHTTPRequestHandler, HTTPServer\n"
        "\n"
        "class Handler(BaseHTTPRequestHandler):\n"
        "    def do_GET(self):\n"
        "        self.send_response(200)\n"
        "        self.send_header('Content-Type', 'application/json')\n"
        "        self.end_headers()\n"
        "        self.wfile.write(b'{\"status\":\"ok\"}')\n"
        "    def log_message(self, *args):\n"
        "        return\n"
        "\n"
        f"server = HTTPServer(('127.0.0.1', {port}), Handler)\n"
        "server.serve_forever()\n",
        encoding="utf-8",
    )
    supervisor_script = (
        "$psi = New-Object System.Diagnostics.ProcessStartInfo; "
        f"$psi.FileName = '{sys.executable}'; "
        f"$psi.Arguments = '\"{child_script_path}\"'; "
        "$psi.UseShellExecute = $false; "
        "$psi.CreateNoWindow = $true; "
        "$proc = [System.Diagnostics.Process]::Start($psi); "
        f"Set-Content -LiteralPath '{child_pid_file}' -Value $proc.Id -NoNewline; "
        "Start-Sleep -Seconds 90"
    )
    supervisor_proc = subprocess.Popen(
        ["powershell.exe", "-NoProfile", "-Command", supervisor_script],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    supervisor_pid = supervisor_proc.pid
    try:
        deadline = time.time() + 10
        while time.time() < deadline and not child_pid_file.exists():
            time.sleep(0.1)
        assert child_pid_file.exists(), "supervisor failed to report child pid"
        child_pid = int(child_pid_file.read_text(encoding="ascii").strip())

        # Give the child a moment to actually bind the listening socket.
        deadline = time.time() + 10
        bound = False
        while time.time() < deadline:
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
                probe.settimeout(0.3)
                try:
                    probe.connect(("127.0.0.1", port))
                    bound = True
                    break
                except OSError:
                    time.sleep(0.2)
        assert bound, "fake child server never started listening"

        (tmp_path / "healthchecker-consumer-api.pid").write_text(
            str(supervisor_pid), encoding="ascii"
        )
        _write_heartbeat(
            tmp_path / "healthchecker-consumer-api.heartbeat.json",
            state="running",
            child_pid=child_pid,
            age_seconds=1.0,
        )
        result = _run_health_check(tmp_path, port=port)
        assert result["Healthy"] is True, result
        assert result["SupervisorAlive"] is True
        assert result["ChildAlive"] is True
        assert result["ChildParentMatches"] is True
        assert result["HeartbeatFresh"] is True
        assert result["ListenerOwnedByChild"] is True
        assert result["LocalHealthz"] is True
    finally:
        subprocess.run(
            ["taskkill", "/PID", str(supervisor_pid), "/T", "/F"],
            capture_output=True,
            text=True,
            check=False,
        )


def test_scenario_b_orphan_child_http_200_is_unhealthy(tmp_path: Path):
    """Scenario B: supervisor dead + orphan child + HTTP 200 => UNHEALTHY."""
    port = _free_loopback_port()
    dead_supervisor = _start_background_process()
    dead_pid = dead_supervisor.pid
    dead_supervisor.terminate()
    dead_supervisor.wait(timeout=5)

    orphan_child = subprocess.Popen(
        [sys.executable, "-c", "import time; time.sleep(60)"],
    )
    try:
        httpd = _start_fake_child_server(port)
        try:
            (tmp_path / "healthchecker-consumer-api.pid").write_text(
                str(dead_pid), encoding="ascii"
            )
            _write_heartbeat(
                tmp_path / "healthchecker-consumer-api.heartbeat.json",
                state="running",
                child_pid=orphan_child.pid,
                age_seconds=1.0,
            )
            result = _run_health_check(tmp_path, port=port)
            assert result["Healthy"] is False, result
            assert result["SupervisorAlive"] is False
            assert result["LocalHealthz"] is True, "child must still be serving 200 for this scenario"
            assert "supervisor_not_alive" in result["Reasons"]
        finally:
            httpd.shutdown()
    finally:
        orphan_child.terminate()
        orphan_child.wait(timeout=5)


def test_scenario_c_stale_heartbeat_http_200_is_unhealthy(tmp_path: Path):
    """Scenario C: stale heartbeat + HTTP 200 => UNHEALTHY."""
    port = _free_loopback_port()
    supervisor = _start_background_process()
    child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"])
    try:
        httpd = _start_fake_child_server(port)
        try:
            (tmp_path / "healthchecker-consumer-api.pid").write_text(
                str(supervisor.pid), encoding="ascii"
            )
            _write_heartbeat(
                tmp_path / "healthchecker-consumer-api.heartbeat.json",
                state="running",
                child_pid=child.pid,
                age_seconds=300.0,
            )
            result = _run_health_check(tmp_path, port=port, heartbeat_staleness_seconds=30)
            assert result["Healthy"] is False, result
            assert result["LocalHealthz"] is True
            assert result["HeartbeatFresh"] is False
            assert any(r.startswith("heartbeat_stale") for r in result["Reasons"])
        finally:
            httpd.shutdown()
    finally:
        supervisor.terminate()
        supervisor.wait(timeout=5)
        child.terminate()
        child.wait(timeout=5)


def test_scenario_d_child_parent_mismatch_is_unhealthy(tmp_path: Path):
    """Scenario D: child parent mismatch => UNHEALTHY."""
    port = _free_loopback_port()
    supervisor = _start_background_process()
    # Unrelated process (not a child of supervisor) masquerading as the child.
    unrelated_child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"])
    try:
        httpd = _start_fake_child_server(port)
        try:
            (tmp_path / "healthchecker-consumer-api.pid").write_text(
                str(supervisor.pid), encoding="ascii"
            )
            _write_heartbeat(
                tmp_path / "healthchecker-consumer-api.heartbeat.json",
                state="running",
                child_pid=unrelated_child.pid,
                age_seconds=1.0,
            )
            result = _run_health_check(tmp_path, port=port)
            assert result["Healthy"] is False, result
            assert result["ChildAlive"] is True
            assert result["ChildParentMatches"] is False
            assert "child_parent_mismatch" in result["Reasons"]
        finally:
            httpd.shutdown()
    finally:
        supervisor.terminate()
        supervisor.wait(timeout=5)
        unrelated_child.terminate()
        unrelated_child.wait(timeout=5)


def test_scenario_e_listener_absent_is_unhealthy(tmp_path: Path):
    """Scenario E: expected listener absent => UNHEALTHY."""
    port = _free_loopback_port()
    supervisor = _start_background_process()
    child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"])
    try:
        # Intentionally do NOT start a server on `port` - no listener exists.
        (tmp_path / "healthchecker-consumer-api.pid").write_text(
            str(supervisor.pid), encoding="ascii"
        )
        _write_heartbeat(
            tmp_path / "healthchecker-consumer-api.heartbeat.json",
            state="running",
            child_pid=child.pid,
            age_seconds=1.0,
        )
        result = _run_health_check(tmp_path, port=port)
        assert result["Healthy"] is False, result
        assert result["LocalHealthz"] is False
        assert "listener_absent" in result["Reasons"]
    finally:
        supervisor.terminate()
        supervisor.wait(timeout=5)
        child.terminate()
        child.wait(timeout=5)


def test_missing_pid_and_heartbeat_files_is_unhealthy(tmp_path: Path):
    """Edge case: completely empty state dir (never started) => UNHEALTHY."""
    port = _free_loopback_port()
    result = _run_health_check(tmp_path, port=port)
    assert result["Healthy"] is False, result
    assert "pid_file_missing" in result["Reasons"]
    assert "heartbeat_missing" in result["Reasons"]
