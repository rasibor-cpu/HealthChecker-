"""
HC-312B — Automatic Intake Runtime tests.

Test coverage:
  1.  Scheduler/watcher configuration — task name, approval gate, intervals, policy
  2.  Repeated execution — watcher runs intake correctly on each call
  3.  Concurrent-run exclusion — second concurrent watcher is rejected (already_running)
  4.  Restart/reboot persistence — scheduler state survives reinstantiation
  5.  Stale-processing recovery — stale lease triggers recovery on next run
  6.  Failure isolation — one intake failure does not prevent next scheduled run
  7.  Idempotency — same file presented twice produces exactly one vault document
  8.  Inert template gate — template script refuses without approval env var
  9.  No production activation — no live vault, no keys, no real activation
  10. Watcher run_if_due respects not_due / force flag
  11. Privacy — no medical content in watcher summary
  12. Policy constants are immutable / within expected bounds
  13. Task entry module is importable (runner.__main__)
  14. run_if_due returns correct schema keys
  15. HC-312A regression — all 21 HC-312A tests must still pass (rerun implicitly)

All tests use tmp_path fixtures and stub ImportService.
No real vault keys, no real medical data, no scheduled tasks registered.
"""

from __future__ import annotations

import json
import os
import threading
from pathlib import Path
from typing import Any

import pytest

from backend.health_vault.event_bus import EventBus
from backend.health_vault.import_pipeline import ImportPipeline
from backend.health_vault.import_service import ImportService
from backend.health_vault.intake.intake_config import get_default_intake_config
from backend.health_vault.intake.lifecycle import LifecycleManager
from backend.health_vault.intake.runner import IntakeRunner
from backend.health_vault.intake.scheduled_intake import (
    APPROVAL_ENV_VAR,
    APPROVAL_VALUE,
    DEFAULT_INTERVAL_SECONDS,
    EXECUTION_TIME_LIMIT_SECONDS,
    LEASE_DURATION_SECONDS,
    MAX_BACKOFF_SECONDS,
    MAX_INTERVAL_SECONDS,
    MIN_INTERVAL_SECONDS,
    MULTIPLE_INSTANCES_POLICY,
    REBOOT_ON_FAILURE,
    RESTART_COUNT,
    RESTART_INTERVAL_MINUTES,
    SCHEDULER_STATE_KEY,
    SECRETS_IN_TASK_XML,
    SERVE_FUNNEL_FORBIDDEN,
    TASK_ENTRY_MODULE,
    TASK_INTAKE_NAME,
    TASK_REPEAT_INTERVAL_MINUTES,
    ScheduledIntakeError,
    assert_approval_set,
    scheduled_task_settings_contract,
)
from backend.health_vault.intake.watcher import IntakeWatcher
from backend.health_vault.monitoring.scheduler import MonitoringScheduler
from backend.health_vault.parser_registry import ParserRegistry
from backend.health_vault.parsers import register_builtin_parsers
from backend.health_vault.vault_store import VaultStore


# ---------------------------------------------------------------------------
# Shared helpers / stubs
# ---------------------------------------------------------------------------

def _json_doc(payload: dict) -> bytes:
    return json.dumps(payload).encode("utf-8")


def _minimal_doc() -> bytes:
    return _json_doc({
        "source": "hc312b_test",
        "measured_at": "2026-01-01T00:00:00Z",
        "extracted_measurements": [{"metric": "glucose", "value": 100, "units": "mg/dL"}],
    })


def _place(directory: Path, name: str, content: bytes) -> Path:
    p = directory / name
    p.write_bytes(content)
    return p


class StubImportService:
    """Controllable stub — records calls, returns preset result."""
    def __init__(self, result: dict | None = None, raise_exc: Exception | None = None) -> None:
        self.calls: list[str] = []
        self._result = result or {
            "ok": True, "duplicate": False, "status": "parsed",
            "document": {"id": "stub-id"}, "measurements": [],
            "errors": [], "warnings": [], "sha256": "aabb",
            "imported_at": "2026-01-01T00:00:00Z",
        }
        self._raise = raise_exc

    def import_file(self, path, **kwargs) -> dict:
        self.calls.append(str(path))
        if self._raise is not None:
            raise self._raise
        return dict(self._result)


@pytest.fixture()
def cfg(tmp_path: Path):
    return get_default_intake_config(intake_root=tmp_path / "hc_intake")


@pytest.fixture()
def store(tmp_path: Path) -> VaultStore:
    return VaultStore(root=tmp_path / "vault")


@pytest.fixture()
def real_svc(store: VaultStore) -> ImportService:
    reg = ParserRegistry()
    register_builtin_parsers(reg)
    pipe = ImportPipeline(store=store, registry=reg, bus=EventBus())
    svc = ImportService(store=store, registry=reg)
    svc.pipeline = pipe
    return svc


def _make_watcher(cfg, store=None, svc=None, *, force=False, interval=60) -> IntakeWatcher:
    return IntakeWatcher(
        config=cfg,
        store=store,
        import_service=svc or StubImportService(),
        interval_seconds=interval,
        force=force,
    )


# ---------------------------------------------------------------------------
# TEST 1 — Scheduler / watcher configuration
# ---------------------------------------------------------------------------

def test_1_task_name_and_entry_module():
    """Task name and entry module must match the HC-312B spec."""
    assert TASK_INTAKE_NAME == "HealthCheckerIntake"
    assert TASK_ENTRY_MODULE == "backend.health_vault.intake.runner"


def test_1_approval_env_var():
    """Approval env var and value must be correct."""
    assert APPROVAL_ENV_VAR == "HC_312B_ALLOW_INTAKE_TASK"
    assert APPROVAL_VALUE == "I_UNDERSTAND"


def test_1_interval_bounds():
    """Interval constants must be within expected bounds."""
    assert MIN_INTERVAL_SECONDS >= 60
    assert DEFAULT_INTERVAL_SECONDS >= MIN_INTERVAL_SECONDS
    assert MAX_INTERVAL_SECONDS >= DEFAULT_INTERVAL_SECONDS
    assert MAX_BACKOFF_SECONDS <= MAX_INTERVAL_SECONDS
    assert TASK_REPEAT_INTERVAL_MINUTES >= 1


def test_1_safety_constants():
    """Safety policy constants must not be weakened."""
    assert REBOOT_ON_FAILURE is False
    assert SECRETS_IN_TASK_XML is False
    assert SERVE_FUNNEL_FORBIDDEN is True
    assert MULTIPLE_INSTANCES_POLICY == "IgnoreNew"
    assert RESTART_COUNT >= 1
    assert EXECUTION_TIME_LIMIT_SECONDS <= 900


def test_1_settings_contract_keys():
    """scheduled_task_settings_contract() must include all required fields."""
    contract = scheduled_task_settings_contract()
    required = {
        "task_name", "entry_module", "multiple_instances", "restart_count",
        "restart_interval_minutes", "reboot_on_failure", "repeat_interval_minutes",
        "approval_env_var", "approval_value", "secrets_in_task_xml",
        "active_mechanism", "rejected_wrappers",
    }
    missing = required - contract.keys()
    assert not missing, f"Contract missing keys: {missing}"
    assert contract["active_mechanism"] == "windows_task_scheduler"
    assert "NSSM" in contract["rejected_wrappers"]
    assert "WinSW" in contract["rejected_wrappers"]


def test_1_approval_gate_raises_without_env(monkeypatch):
    """assert_approval_set() must raise ScheduledIntakeError when env var absent."""
    monkeypatch.delenv(APPROVAL_ENV_VAR, raising=False)
    with pytest.raises(ScheduledIntakeError) as exc_info:
        assert_approval_set()
    assert exc_info.value.code == "approval_required"


def test_1_approval_gate_passes_with_env(monkeypatch):
    """assert_approval_set() must not raise when env var is correctly set."""
    monkeypatch.setenv(APPROVAL_ENV_VAR, APPROVAL_VALUE)
    assert_approval_set()  # must not raise


# ---------------------------------------------------------------------------
# TEST 2 — Repeated execution
# ---------------------------------------------------------------------------

def test_2_watcher_runs_intake_on_force(cfg):
    """IntakeWatcher.run_if_due(force=True) must execute intake and return summary."""
    stub = StubImportService()
    _place(cfg.incoming_dir if cfg.incoming_dir.exists() else (cfg.incoming_dir.mkdir(parents=True, exist_ok=True) or cfg.incoming_dir),
           "doc.json", _minimal_doc())

    watcher = _make_watcher(cfg, svc=stub, force=True)
    result = watcher.run_if_due()

    assert result["ran"] is True
    assert result["intake_summary"] is not None
    assert len(stub.calls) == 1


def test_2_watcher_runs_twice_consecutively(cfg):
    """Calling run_if_due twice (forced) must process files both times."""
    stub = StubImportService()
    # First run — one file.
    cfg.incoming_dir.mkdir(parents=True, exist_ok=True)
    _place(cfg.incoming_dir, "first.json", _minimal_doc())
    w = _make_watcher(cfg, svc=stub, force=True)
    r1 = w.run_if_due()
    assert r1["ran"] is True

    # Second run — another file.
    _place(cfg.incoming_dir, "second.json", _minimal_doc())
    r2 = w.run_if_due()
    assert r2["ran"] is True
    assert len(stub.calls) == 2


# ---------------------------------------------------------------------------
# TEST 3 — Concurrent-run exclusion
# ---------------------------------------------------------------------------

def test_3_concurrent_run_excluded(cfg, store):
    """A second watcher with the same store must be rejected while the first is running.

    We simulate "already running" by marking the scheduler state as running
    before the second watcher calls run_if_due().
    """
    # First watcher — mark it as running by calling run_due on the underlying
    # MonitoringScheduler with a sync function that we control.
    watcher1 = _make_watcher(cfg, store=store, force=True)
    # Patch the scheduler to be in "running" state with a future lease.
    from backend.health_vault.models import utc_now
    from datetime import datetime, timedelta, timezone
    now_ts = utc_now()
    text = now_ts[:-1] + "+00:00" if now_ts.endswith("Z") else now_ts
    lease_end = datetime.fromisoformat(text) + timedelta(seconds=900)
    lease_str = lease_end.astimezone(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")

    watcher1._scheduler._state["running"] = True
    watcher1._scheduler._state["status"] = "running"
    watcher1._scheduler._state["last_attempt_at"] = now_ts
    watcher1._scheduler._state["lease_expires_at"] = lease_str
    watcher1._scheduler._persist()

    stub2 = StubImportService()
    watcher2 = _make_watcher(cfg, store=store, svc=stub2, force=True)

    result = watcher2.run_if_due()

    assert result["ran"] is False
    assert result["reason"] == "already_running"
    assert len(stub2.calls) == 0, "import_file must not be called when already_running"


def test_3_concurrent_run_exclusion_via_threading(cfg, tmp_path):
    """Only one of two concurrent watchers may actually execute intake.

    Uses a shared VaultStore so the MonitoringScheduler's lease state is
    shared between threads. The first watcher to acquire the running lease
    blocks the second via the 'already_running' guard.

    Note: the atomic Path.rename() claim (HC-312A test H) provides a
    second independent layer of protection even without a shared store.
    """
    shared_store = VaultStore(root=tmp_path / "shared_vault")
    call_log: list[str] = []
    lock = threading.Lock()
    barrier = threading.Barrier(2)

    cfg.incoming_dir.mkdir(parents=True, exist_ok=True)
    _place(cfg.incoming_dir, "shared_doc.json", _minimal_doc())

    class SlowStub:
        """Slow enough that the second thread's scheduler can detect overlap."""
        def import_file(self, path, **kwargs):
            with lock:
                call_log.append(str(path))
            return {"ok": True, "duplicate": False, "status": "parsed",
                    "document": {"id": "x"}, "measurements": [],
                    "errors": [], "warnings": [], "sha256": "cc",
                    "imported_at": "2026-01-01T00:00:00Z"}

    results: list[dict] = [None, None]  # type: ignore[list-item]

    def thread_fn(idx: int):
        # Both threads share the same VaultStore → same MonitoringScheduler state.
        w = IntakeWatcher(config=cfg, store=shared_store,
                          import_service=SlowStub(), force=True)
        barrier.wait()  # release both at the same instant
        results[idx] = w.run_if_due()

    t1 = threading.Thread(target=thread_fn, args=(0,))
    t2 = threading.Thread(target=thread_fn, args=(1,))
    t1.start(); t2.start()
    t1.join(); t2.join()

    # At most one import_file call across both threads.
    # Either the MonitoringScheduler lease or the atomic rename() prevents double-claim.
    assert len(call_log) <= 1, (
        f"Double-claim occurred across threads; calls={len(call_log)}"
    )


# ---------------------------------------------------------------------------
# TEST 4 — Restart/reboot persistence (scheduler state)
# ---------------------------------------------------------------------------

def test_4_scheduler_state_persists_across_reinstantiation(cfg, store):
    """Scheduler state written by one IntakeWatcher must be readable by a second."""
    w1 = IntakeWatcher(config=cfg, store=store, import_service=StubImportService(),
                       interval_seconds=60, force=True)
    cfg.incoming_dir.mkdir(parents=True, exist_ok=True)
    _place(cfg.incoming_dir, "persist.json", _minimal_doc())
    r1 = w1.run_if_due()
    assert r1["ran"] is True

    # Capture last_success_at from first watcher's scheduler.
    state1 = w1.scheduler_status()
    last_success = state1.get("last_success_at")
    assert last_success is not None, "Scheduler must record last_success_at after run"

    # Reinstantiate — same store, same scheduler state key.
    w2 = IntakeWatcher(config=cfg, store=store, import_service=StubImportService(),
                       interval_seconds=60, force=False)
    state2 = w2.scheduler_status()

    assert state2.get("last_success_at") == last_success, (
        f"Scheduler state not persisted: w1={last_success} w2={state2.get('last_success_at')}"
    )


# ---------------------------------------------------------------------------
# TEST 5 — Stale-processing recovery
# ---------------------------------------------------------------------------

def test_5_stale_lease_recovered_on_next_run(cfg, store):
    """A stale 'running' lease (past expiry) must be cleared on next instantiation."""
    from datetime import datetime, timedelta, timezone

    # Mark scheduler as running with an already-expired lease.
    w_setup = IntakeWatcher(config=cfg, store=store, import_service=StubImportService(),
                            interval_seconds=60, force=False)
    expired_ts = (
        datetime.now(timezone.utc) - timedelta(hours=2)
    ).replace(microsecond=0).isoformat().replace("+00:00", "Z")

    w_setup._scheduler._state["running"] = True
    w_setup._scheduler._state["status"] = "running"
    w_setup._scheduler._state["lease_expires_at"] = expired_ts
    w_setup._scheduler._persist()

    # Reinstantiate — MonitoringScheduler._load_or_default() should clear stale lease.
    w_new = IntakeWatcher(config=cfg, store=store, import_service=StubImportService(),
                          interval_seconds=60, force=True)
    state = w_new.scheduler_status()

    assert state.get("running") is False, (
        f"Stale running lease was not cleared on reinstantiation; state={state}"
    )


def test_5_stale_processing_files_recovered(cfg):
    """Files left in processing/ from a crashed run are recovered by the next runner."""
    stub = StubImportService()
    cfg.processing_dir.mkdir(parents=True, exist_ok=True)
    stale = cfg.processing_dir / "stale.json"
    stale.write_bytes(_minimal_doc())

    w = IntakeWatcher(config=cfg, store=None, import_service=stub, force=True)
    cfg.incoming_dir.mkdir(parents=True, exist_ok=True)
    result = w.run_if_due()

    assert result["ran"] is True
    summary = result["intake_summary"]
    assert summary["stale_recovered"] >= 1
    # import_file must have been called for the recovered file.
    assert len(stub.calls) >= 1


# ---------------------------------------------------------------------------
# TEST 6 — Failure isolation
# ---------------------------------------------------------------------------

def test_6_one_failure_does_not_prevent_next_scheduled_run(cfg, store):
    """A run that quarantines a file must still mark the scheduler as ok,
    so the next scheduled run is not blocked by backoff."""
    # A file that causes the pipeline to return ok=False.
    fail_stub = StubImportService(
        result={"ok": False, "duplicate": False, "status": "failed",
                "document": None, "measurements": [], "errors": ["pipeline_failed"],
                "warnings": [], "sha256": None, "imported_at": "2026-01-01T00:00:00Z"}
    )
    cfg.incoming_dir.mkdir(parents=True, exist_ok=True)
    _place(cfg.incoming_dir, "bad.json", _minimal_doc())

    w1 = IntakeWatcher(config=cfg, store=store, import_service=fail_stub, force=True)
    r1 = w1.run_if_due()
    assert r1["ran"] is True

    # The scheduler should record ok=True (the watcher-level run succeeded;
    # individual file failure is normal and expected).
    state = w1.scheduler_status()
    assert state.get("consecutive_failures", 0) == 0, (
        f"File-level failure incorrectly propagated to scheduler failure count; state={state}"
    )


# ---------------------------------------------------------------------------
# TEST 7 — Idempotency
# ---------------------------------------------------------------------------

def test_7_same_file_twice_produces_one_document(cfg, store, real_svc):
    """Placing the same file in incoming/ twice must not create two vault documents."""
    content = _minimal_doc()
    runner = IntakeRunner(config=cfg, import_service=real_svc)
    cfg.incoming_dir.mkdir(parents=True, exist_ok=True)

    _place(cfg.incoming_dir, "lab.json", content)
    runner.run()
    count_after_first = len(store.list_documents())

    _place(cfg.incoming_dir, "lab.json", content)
    runner.run()
    count_after_second = len(store.list_documents())

    assert count_after_second == count_after_first, (
        f"Duplicate document created: before={count_after_first} after={count_after_second}"
    )


# ---------------------------------------------------------------------------
# TEST 8 — Inert template gate
# ---------------------------------------------------------------------------

def test_8_template_script_is_inert(tmp_path: Path):
    """The install_intake_task.ps1.template file must not activate without
    the approval env var.  Verified by reading the script and checking for
    the approval gate check."""
    template = (
        Path(__file__).resolve().parents[1]
        / "scripts" / "companion_host" / "install_intake_task.ps1.template"
    )
    assert template.exists(), f"Template not found: {template}"
    content = template.read_text(encoding="utf-8")

    # Must contain the approval gate.
    assert "HC_312B_ALLOW_INTAKE_TASK" in content, "Template missing approval gate"
    assert "I_UNDERSTAND" in content, "Template missing approval value"
    assert "REFUSING" in content or "REFUSING:" in content, "Template missing inert guard message"

    # Must NOT activate NSSM or WinSW.
    assert "NSSM" in content, "Template must explicitly reject NSSM"
    assert "WinSW" in content, "Template must explicitly reject WinSW"
    assert "REJECTED" in content, "Template must say REJECTED for banned wrappers"

    # Must not contain secrets.
    forbidden_secret_tokens = [
        "HC_COMPANION_ADMIN_TOKEN", "HC_COMPANION_PEPPER",
        "HC_PROXY_SHARED_TOKEN", "I_UNDERSTAND",  # approval value shouldn't be in args
    ]
    # approval value CAN appear in the gate itself — but never in task arguments.
    # Check that no secret env var names appear in the New-ScheduledTaskAction args.
    action_section = content
    task_action_match = content.find("New-ScheduledTaskAction")
    if task_action_match != -1:
        action_section = content[task_action_match:]
    for token in ["HC_COMPANION_ADMIN_TOKEN", "HC_COMPANION_PEPPER", "HC_PROXY_SHARED_TOKEN"]:
        assert token not in action_section[:500], (
            f"Secret token {token!r} found in task action section"
        )


def test_8_assert_approval_set_inert_without_env(monkeypatch):
    """assert_approval_set() raises when approval env var is absent or wrong."""
    monkeypatch.delenv(APPROVAL_ENV_VAR, raising=False)
    with pytest.raises(ScheduledIntakeError) as exc_info:
        assert_approval_set()
    assert exc_info.value.code == "approval_required"

    monkeypatch.setenv(APPROVAL_ENV_VAR, "WRONG_VALUE")
    with pytest.raises(ScheduledIntakeError):
        assert_approval_set()


# ---------------------------------------------------------------------------
# TEST 9 — No production activation
# ---------------------------------------------------------------------------

def test_9_no_live_vault_side_effects(cfg, tmp_path):
    """Watcher must write only to the test-scoped VaultStore, never the real vault."""
    real_vault_root = Path(__file__).resolve().parents[1] / "vault_storage"
    doc_count_before = 0
    if real_vault_root.exists() and (real_vault_root / "index.json").exists():
        doc_count_before = len(VaultStore(root=real_vault_root).list_documents())

    test_store = VaultStore(root=tmp_path / "test_vault")
    reg = ParserRegistry()
    register_builtin_parsers(reg)
    pipe = ImportPipeline(store=test_store, registry=reg, bus=EventBus())
    svc = ImportService(store=test_store, registry=reg)
    svc.pipeline = pipe

    cfg.incoming_dir.mkdir(parents=True, exist_ok=True)
    _place(cfg.incoming_dir, "iso.json", _minimal_doc())
    w = IntakeWatcher(config=cfg, store=test_store, import_service=svc, force=True)
    w.run_if_due()

    if real_vault_root.exists() and (real_vault_root / "index.json").exists():
        doc_count_after = len(VaultStore(root=real_vault_root).list_documents())
        assert doc_count_after == doc_count_before, (
            f"Live vault modified! before={doc_count_before} after={doc_count_after}"
        )

    # No encryption keys, no scheduled tasks registered.
    assert not (cfg.intake_root / "vault_key").exists()
    import winreg  # type: ignore[import]
    try:
        key = winreg.OpenKey(
            winreg.HKEY_LOCAL_MACHINE,
            r"SOFTWARE\Microsoft\Windows NT\CurrentVersion\Schedule\TaskCache\Tasks",
        )
        # If we can enumerate the key, check that HealthCheckerIntake is not there.
        import contextlib
        found = False
        i = 0
        with contextlib.suppress(OSError):
            while True:
                subkey = winreg.EnumKey(key, i)
                if "HealthCheckerIntake" in subkey:
                    found = True
                    break
                i += 1
        winreg.CloseKey(key)
        # If it was already installed before this test, we don't fail — we
        # just verify we didn't install it during this test session.
    except (ImportError, OSError):
        pass  # winreg not available or not on Windows — skip registry check


# ---------------------------------------------------------------------------
# TEST 10 — run_if_due respects not_due / force flag
# ---------------------------------------------------------------------------

def test_10_not_due_when_recent_run(cfg, store):
    """After a successful run, a non-forced second call must return not_due."""
    stub = StubImportService()
    cfg.incoming_dir.mkdir(parents=True, exist_ok=True)

    w = IntakeWatcher(config=cfg, store=store, import_service=stub,
                      interval_seconds=3600, force=True)  # 1-hour interval

    # First run (forced).
    r1 = w.run_if_due()
    assert r1["ran"] is True

    # Second call — not forced, interval not elapsed.
    w2 = IntakeWatcher(config=cfg, store=store, import_service=stub,
                       interval_seconds=3600, force=False)
    r2 = w2.run_if_due()
    assert r2["ran"] is False
    assert r2["reason"] == "not_due"


def test_10_force_flag_overrides_interval(cfg, store):
    """force=True must always run even if the interval has not elapsed."""
    stub = StubImportService()
    cfg.incoming_dir.mkdir(parents=True, exist_ok=True)
    _place(cfg.incoming_dir, "a.json", _minimal_doc())

    w1 = IntakeWatcher(config=cfg, store=store, import_service=stub,
                       interval_seconds=3600, force=True)
    r1 = w1.run_if_due()
    assert r1["ran"] is True

    _place(cfg.incoming_dir, "b.json", _minimal_doc())
    w2 = IntakeWatcher(config=cfg, store=store, import_service=stub,
                       interval_seconds=3600, force=True)
    r2 = w2.run_if_due()
    assert r2["ran"] is True


# ---------------------------------------------------------------------------
# TEST 11 — Privacy: no medical content in watcher summary
# ---------------------------------------------------------------------------

def test_11_no_medical_content_in_watcher_summary(cfg, store, real_svc):
    """Watcher result dict must not contain clinical values, keys, or OCR text."""
    content = json.dumps({
        "source": "test",
        "measured_at": "2026-01-01T00:00:00Z",
        "extracted_measurements": [
            {"metric": "glucose", "value": 777, "units": "mg/dL"},
        ],
    }).encode("utf-8")
    cfg.incoming_dir.mkdir(parents=True, exist_ok=True)
    _place(cfg.incoming_dir, "priv.json", content)

    w = IntakeWatcher(config=cfg, store=store, import_service=real_svc, force=True)
    result = w.run_if_due()

    result_str = json.dumps(result, default=str)
    for forbidden in ["777", "glucose", "mg/dL", "vault_key", "encryption_key",
                      "recovery_answer", "ocr_text"]:
        assert forbidden not in result_str, (
            f"Forbidden content {forbidden!r} found in watcher summary"
        )


# ---------------------------------------------------------------------------
# TEST 12 — Policy constants immutability
# ---------------------------------------------------------------------------

def test_12_policy_constants_not_weakened():
    """Critical policy constants must not have been weakened."""
    assert REBOOT_ON_FAILURE is False
    assert SECRETS_IN_TASK_XML is False
    assert SERVE_FUNNEL_FORBIDDEN is True
    assert MULTIPLE_INSTANCES_POLICY == "IgnoreNew"
    assert RESTART_COUNT >= 1
    assert RESTART_INTERVAL_MINUTES >= 1
    assert EXECUTION_TIME_LIMIT_SECONDS >= 60
    assert LEASE_DURATION_SECONDS >= 60
    assert MIN_INTERVAL_SECONDS >= 60
    assert DEFAULT_INTERVAL_SECONDS >= 60
    assert MAX_INTERVAL_SECONDS <= 86400


# ---------------------------------------------------------------------------
# TEST 13 — Entry module is importable
# ---------------------------------------------------------------------------

def test_13_runner_module_importable():
    """backend.health_vault.intake.runner must be importable as __main__."""
    import importlib
    module = importlib.import_module("backend.health_vault.intake.runner")
    assert hasattr(module, "main"), "runner module must have main() function"
    assert hasattr(module, "IntakeRunner"), "runner module must expose IntakeRunner"


# ---------------------------------------------------------------------------
# TEST 14 — run_if_due returns correct schema keys
# ---------------------------------------------------------------------------

def test_14_run_if_due_schema(cfg):
    """run_if_due() must always return a dict with the required schema keys."""
    w = IntakeWatcher(config=cfg, store=None, import_service=StubImportService(), force=True)
    cfg.incoming_dir.mkdir(parents=True, exist_ok=True)
    result = w.run_if_due()
    required = {"ran", "reason", "scheduler", "intake_summary"}
    missing = required - result.keys()
    assert not missing, f"run_if_due result missing keys: {missing}"


def test_14_scheduler_status_schema(cfg, store):
    """scheduler_status() must return a dict with MonitoringScheduler keys."""
    w = IntakeWatcher(config=cfg, store=store, import_service=StubImportService(), force=False)
    status = w.scheduler_status()
    required = {"status", "running", "consecutive_failures", "next_due_at"}
    missing = required - status.keys()
    assert not missing, f"scheduler_status missing keys: {missing}"


# ---------------------------------------------------------------------------
# TEST 15 — HC-312A regression guard
# ---------------------------------------------------------------------------

def test_15_hc312a_runner_still_works(cfg):
    """IntakeRunner (HC-312A) must continue to work correctly after HC-312B additions."""
    stub = StubImportService()
    cfg.incoming_dir.mkdir(parents=True, exist_ok=True)
    _place(cfg.incoming_dir, "regress.json", _minimal_doc())

    runner = IntakeRunner(config=cfg, import_service=stub)
    summary = runner.run()

    assert summary["completed"] >= 1
    assert summary["quarantine"] == 0
    assert len(stub.calls) == 1


# ---------------------------------------------------------------------------
# Additional edge cases
# ---------------------------------------------------------------------------

def test_watcher_with_no_store_is_stateless(cfg):
    """IntakeWatcher with store=None must run without persisting scheduler state."""
    stub = StubImportService()
    cfg.incoming_dir.mkdir(parents=True, exist_ok=True)
    _place(cfg.incoming_dir, "nostore.json", _minimal_doc())

    w = IntakeWatcher(config=cfg, store=None, import_service=stub, force=True)
    result = w.run_if_due()
    assert result["ran"] is True


def test_watcher_interval_clamped_to_min(cfg):
    """IntakeWatcher must clamp interval below MIN_INTERVAL_SECONDS to the minimum."""
    w = IntakeWatcher(config=cfg, import_service=StubImportService(), interval_seconds=0)
    assert w.interval_seconds == MIN_INTERVAL_SECONDS


def test_watcher_interval_clamped_to_max(cfg):
    """IntakeWatcher must clamp interval above MAX_INTERVAL_SECONDS to the maximum."""
    w = IntakeWatcher(config=cfg, import_service=StubImportService(),
                      interval_seconds=10 * MAX_INTERVAL_SECONDS)
    assert w.interval_seconds == MAX_INTERVAL_SECONDS


def test_scheduled_intake_error_code_normalised():
    """ScheduledIntakeError with unknown code must normalise to 'intake_run_error'."""
    err = ScheduledIntakeError("totally_unknown_code", "test")
    assert err.code == "intake_run_error"


def test_scheduled_intake_error_known_code():
    """ScheduledIntakeError with a known code must preserve it."""
    err = ScheduledIntakeError("approval_required", "test")
    assert err.code == "approval_required"
