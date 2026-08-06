from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

from backend.health_vault.companion_host import r4f_preparation
from scripts import check_hc309_pending_pen_readiness as wrapper


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts/check_hc309_pending_pen_readiness.py"


def test_wrapper_evaluates_only_the_fixed_record(capsys):
    assert wrapper.main([]) == r4f_preparation.EXIT_BLOCKED
    result = json.loads(capsys.readouterr().out)
    assert result["certification_status"] == "BLOCKED"
    assert result["authorization"] == "readiness_only"
    assert result["live_execution_status"] == "BLOCKED"
    assert result["exit_code"] == r4f_preparation.EXIT_BLOCKED
    assert "PASS" not in json.dumps(result)


def test_wrapper_rejects_all_arguments_without_reading_record(monkeypatch, capsys):
    monkeypatch.setattr(
        Path,
        "read_bytes",
        lambda _path: (_ for _ in ()).throw(AssertionError("record must not be read")),
    )
    assert wrapper.main(["--record", "other.json"]) == r4f_preparation.EXIT_INVOCATION
    result = json.loads(capsys.readouterr().out)
    assert result["certification_status"] == "FAIL"
    assert result["error"] == "readiness_configuration_invalid"
    assert result["live_execution_status"] == "BLOCKED"


def test_wrapper_fails_closed_when_fixed_record_cannot_be_read(monkeypatch, capsys):
    monkeypatch.setattr(
        Path,
        "read_bytes",
        lambda _path: (_ for _ in ()).throw(OSError("private detail")),
    )
    assert wrapper.main([]) == r4f_preparation.EXIT_INVOCATION
    result = json.loads(capsys.readouterr().out)
    assert result["error"] == "readiness_configuration_invalid"
    assert "private detail" not in capsys.readouterr().out


def test_script_is_runnable_from_outside_repository():
    completed = subprocess.run(
        [sys.executable, str(SCRIPT)],
        cwd=ROOT / "docs",
        check=False,
        capture_output=True,
        text=True,
        timeout=10,
    )
    assert completed.returncode == r4f_preparation.EXIT_BLOCKED
    assert completed.stderr == ""
    result = json.loads(completed.stdout)
    assert result["certification_status"] == "BLOCKED"
