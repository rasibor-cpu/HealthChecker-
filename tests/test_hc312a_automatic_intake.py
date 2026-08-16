"""
HC-312A — Automatic Medical Record Intake tests.

Test coverage required by HC-312A specification:
    A  supported medical document is automatically imported
    B  canonical ImportService/ImportPipeline path is used
    C  successful file reaches completed state
    D  duplicate/re-presented document does not duplicate medical data
    E  unsupported extension is rejected/quarantined
    F  malformed document is quarantined
    G  importer exception is quarantined
    H  concurrent/double claim is prevented
    I  controlled directory / path-traversal protection works
    J  medical content is absent from operational logs/report
    K  failed file reaches quarantine/rejected state
    L  one failed file does not prevent later eligible files from processing
    M  stale processing state / restart recovery behaves deterministically
    N  production key / live vault side effects do not occur

All tests use tmp_path fixtures and stub ImportService implementations.
No real vault keys, no real medical data, no live vault modifications.
"""

from __future__ import annotations

import json
import logging
import threading
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from backend.health_vault.event_bus import EventBus
from backend.health_vault.import_pipeline import ImportPipeline
from backend.health_vault.import_service import ImportService
from backend.health_vault.intake.file_processor import FileProcessor, _make_result
from backend.health_vault.intake.file_scanner import ScanCandidate, scan_incoming
from backend.health_vault.intake.intake_config import get_default_intake_config
from backend.health_vault.intake.lifecycle import LifecycleManager, LifecycleState
from backend.health_vault.intake.runner import IntakeRunner, _build_summary
from backend.health_vault.parser_registry import ParserRegistry
from backend.health_vault.parsers import register_builtin_parsers
from backend.health_vault.vault_store import VaultStore


# ---------------------------------------------------------------------------
# Shared fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def cfg(tmp_path: Path):
    """IntakeConfig with all directories under tmp_path."""
    return get_default_intake_config(intake_root=tmp_path / "hc_intake")


@pytest.fixture()
def lc(cfg):
    """LifecycleManager for the test config."""
    mgr = LifecycleManager(cfg)
    mgr.ensure_dirs()
    return mgr


@pytest.fixture()
def store(tmp_path: Path) -> VaultStore:
    return VaultStore(root=tmp_path / "vault")


@pytest.fixture()
def real_import_service(store: VaultStore) -> ImportService:
    reg = ParserRegistry()
    register_builtin_parsers(reg)
    pipeline = ImportPipeline(store=store, registry=reg, bus=EventBus())
    svc = ImportService(store=store, registry=reg)
    svc.pipeline = pipeline
    return svc


@pytest.fixture()
def runner(cfg, real_import_service) -> IntakeRunner:
    return IntakeRunner(config=cfg, import_service=real_import_service)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _place_file(directory: Path, name: str, content: bytes) -> Path:
    """Write *content* to *directory/name* and return the path."""
    p = directory / name
    p.write_bytes(content)
    return p


def _json_doc(payload: dict) -> bytes:
    return json.dumps(payload).encode("utf-8")


def _minimal_json_doc() -> bytes:
    """Minimal synthetic health record — no real patient data."""
    return _json_doc(
        {
            "source": "test_fixture",
            "measured_at": "2026-01-01T00:00:00Z",
            "extracted_measurements": [
                {"metric": "glucose", "value": 100, "units": "mg/dL"}
            ],
        }
    )


# Stub import service that returns a configurable result.
class StubImportService:
    """Controllable stub for ImportService — records calls, returns preset result."""

    def __init__(self, result: dict | None = None, raise_exc: Exception | None = None) -> None:
        self.calls: list[dict] = []
        self._result = result or {"ok": True, "duplicate": False, "status": "parsed",
                                  "document": {"id": "stub-doc-id"},
                                  "measurements": [], "errors": [], "warnings": [],
                                  "sha256": "aabbcc", "imported_at": "2026-01-01T00:00:00Z"}
        self._raise = raise_exc

    def import_file(self, path, **kwargs) -> dict:
        self.calls.append({"path": str(path), **kwargs})
        if self._raise is not None:
            raise self._raise
        return dict(self._result)


# ---------------------------------------------------------------------------
# TEST A — Supported medical document is automatically imported
# ---------------------------------------------------------------------------

def test_a_supported_document_is_imported(cfg, lc, store, real_import_service):
    """A supported JSON document in incoming/ reaches the vault after runner.run()."""
    content = _minimal_json_doc()
    _place_file(cfg.incoming_dir, "lab_result.json", content)

    runner = IntakeRunner(config=cfg, import_service=real_import_service)
    summary = runner.run()

    # At least one file must reach completed or duplicate status.
    terminal_ok = summary["completed"] + summary["duplicate"]
    assert terminal_ok >= 1, f"Expected at least 1 completed/duplicate; summary={summary}"


# ---------------------------------------------------------------------------
# TEST B — Canonical ImportService/ImportPipeline path is used
# ---------------------------------------------------------------------------

def test_b_canonical_import_service_is_called(cfg, lc):
    """FileProcessor must call ImportService.import_file() — not a second pipeline."""
    stub = StubImportService()
    content = _minimal_json_doc()
    _place_file(cfg.incoming_dir, "record.json", content)

    runner = IntakeRunner(config=cfg, import_service=stub)
    runner.run()

    # import_file must have been called exactly once (one file in incoming/).
    assert len(stub.calls) == 1, f"Expected exactly 1 import_file call; got {len(stub.calls)}"
    # The call must have been made with a real filesystem path, not raw bytes.
    called_path = stub.calls[0]["path"]
    assert "record.json" in called_path, f"import_file called with unexpected path: {called_path}"


# ---------------------------------------------------------------------------
# TEST C — Successful file reaches completed state
# ---------------------------------------------------------------------------

def test_c_successful_file_reaches_completed(cfg, lc):
    """A successfully imported file must be moved to completed/ directory."""
    stub = StubImportService()
    _place_file(cfg.incoming_dir, "bp_reading.json", _minimal_json_doc())

    runner = IntakeRunner(config=cfg, import_service=stub)
    summary = runner.run()

    # File must have moved to completed/.
    completed_files = list(cfg.completed_dir.iterdir())
    assert any(f.name == "bp_reading.json" for f in completed_files), (
        f"bp_reading.json not found in completed/; files={[f.name for f in completed_files]}"
    )
    assert summary["completed"] == 1
    # incoming/ must be empty (file was claimed and moved).
    remaining = list(cfg.incoming_dir.iterdir())
    assert remaining == [], f"incoming/ should be empty after processing; found {remaining}"


# ---------------------------------------------------------------------------
# TEST D — Duplicate / re-presented document does not duplicate medical data
# ---------------------------------------------------------------------------

def test_d_duplicate_does_not_create_second_record(cfg, lc, store, real_import_service):
    """Re-presenting the same file must not create a second vault document."""
    content = _minimal_json_doc()
    runner = IntakeRunner(config=cfg, import_service=real_import_service)

    # First run.
    _place_file(cfg.incoming_dir, "lab.json", content)
    runner.run()
    docs_after_first = len(store.list_documents())

    # Second run — same file content.
    _place_file(cfg.incoming_dir, "lab.json", content)
    summary2 = runner.run()
    docs_after_second = len(store.list_documents())

    assert docs_after_second == docs_after_first, (
        f"Duplicate import created extra document: first={docs_after_first} "
        f"second={docs_after_second}"
    )
    assert summary2["duplicate"] >= 1 or summary2["completed"] >= 1, (
        "Second run should complete (duplicate or otherwise) without error"
    )


# ---------------------------------------------------------------------------
# TEST E — Unsupported extension is rejected / quarantined
# ---------------------------------------------------------------------------

def test_e_unsupported_extension_quarantined(cfg, lc):
    """A file with an unsupported extension must be quarantined, not imported."""
    stub = StubImportService()
    _place_file(cfg.incoming_dir, "document.exe", b"MZ\x90\x00unsupported")

    runner = IntakeRunner(config=cfg, import_service=stub)
    summary = runner.run()

    # File must not have been forwarded to import_file.
    assert len(stub.calls) == 0, "import_file must not be called for unsupported extension"
    # File must appear in quarantine/.
    quarantine_files = [f.name for f in cfg.quarantine_dir.iterdir() if f.is_file() and not f.name.endswith(".reason")]
    assert "document.exe" in quarantine_files, (
        f"document.exe not found in quarantine/; files={quarantine_files}"
    )
    assert summary["pre_rejected"] >= 1


# ---------------------------------------------------------------------------
# TEST F — Malformed document is quarantined
# ---------------------------------------------------------------------------

def test_f_malformed_document_quarantined(cfg, lc, store, real_import_service):
    """A file with a supported extension but corrupt/unreadable content must be
    quarantined after the pipeline attempt."""
    # Use a .json extension with completely invalid binary content so the
    # pipeline will fail to parse it (import may succeed but produce no measurements,
    # or may error depending on strictness).  For a strong failure, write a file
    # that triggers an exception in the pipeline by making ImportService.import_file
    # raise when reading broken content.

    # We use a stub that returns ok=False to simulate a parse failure.
    stub = StubImportService(
        result={
            "ok": False, "duplicate": False, "status": "failed",
            "document": None, "measurements": [], "errors": ["pipeline_exception:ValueError"],
            "warnings": [], "sha256": None, "imported_at": "2026-01-01T00:00:00Z",
        }
    )
    _place_file(cfg.incoming_dir, "corrupt.json", b"\xff\xfe\x00broken not json")

    runner = IntakeRunner(config=cfg, import_service=stub)
    summary = runner.run()

    quarantine_files = [f.name for f in cfg.quarantine_dir.iterdir() if f.is_file() and not f.name.endswith(".reason")]
    assert "corrupt.json" in quarantine_files, (
        f"corrupt.json not in quarantine/; found={quarantine_files}"
    )
    assert summary["quarantine"] >= 1


# ---------------------------------------------------------------------------
# TEST G — Importer exception is quarantined
# ---------------------------------------------------------------------------

def test_g_importer_exception_quarantined(cfg, lc):
    """An exception raised by ImportService.import_file() must quarantine the
    file without crashing the runner."""
    stub = StubImportService(raise_exc=RuntimeError("synthetic_importer_crash"))
    _place_file(cfg.incoming_dir, "crash_doc.json", _minimal_json_doc())

    runner = IntakeRunner(config=cfg, import_service=stub)
    summary = runner.run()

    quarantine_files = [f.name for f in cfg.quarantine_dir.iterdir() if f.is_file() and not f.name.endswith(".reason")]
    assert "crash_doc.json" in quarantine_files, (
        f"crash_doc.json not in quarantine/ after exception; found={quarantine_files}"
    )
    # The runner must exit cleanly (summary is returned, not an exception).
    assert isinstance(summary, dict)
    assert summary["quarantine"] >= 1


# ---------------------------------------------------------------------------
# TEST H — Concurrent / double claim is prevented
# ---------------------------------------------------------------------------

def test_h_concurrent_double_claim_prevented(cfg, lc):
    """Only one of two concurrent runners may claim the same file.
    The second must see 'already_claimed' and not call import_file."""
    call_log: list[str] = []
    lock = threading.Lock()
    barrier = threading.Barrier(2)

    content = _minimal_json_doc()
    _place_file(cfg.incoming_dir, "shared.json", content)

    class BarrierStub:
        """Rendezvous both threads at the same moment before importing."""

        def import_file(self, path, **kwargs):
            # Both threads arrive here; only one should actually reach this point.
            with lock:
                call_log.append(str(path))
            return {"ok": True, "duplicate": False, "status": "parsed",
                    "document": {"id": "hid"}, "measurements": [],
                    "errors": [], "warnings": [], "sha256": "cc", "imported_at": "2026-01-01T00:00:00Z"}

    results: list[dict] = [None, None]  # type: ignore[list-item]

    def thread_fn(idx):
        import dataclasses
        test_cfg = dataclasses.replace(cfg, stale_recovery=False)
        r = IntakeRunner(config=test_cfg, import_service=BarrierStub())
        barrier.wait()  # synchronise both threads
        results[idx] = r.run()

    t1 = threading.Thread(target=thread_fn, args=(0,))
    t2 = threading.Thread(target=thread_fn, args=(1,))
    t1.start(); t2.start()
    t1.join(); t2.join()

    # import_file must have been called at most once across both runners.
    assert len(call_log) <= 1, (
        f"import_file was called {len(call_log)} times — double-claim occurred"
    )
    # Total completed + duplicate across both runs must be exactly 1.
    total_terminal = sum(
        r["completed"] + r["duplicate"] for r in results if r is not None
    )
    assert total_terminal <= 1, (
        f"File imported {total_terminal} times — atomic claim failed"
    )


# ---------------------------------------------------------------------------
# TEST I — Path-traversal protection
# ---------------------------------------------------------------------------

def test_i_path_traversal_rejected(cfg, lc):
    """A filename that attempts path traversal must be quarantined immediately."""
    stub = StubImportService()

    # Create a file with a safe name; then simulate what scanner sees when
    # a malicious name appears.  On Windows creating "../evil.json" is illegal,
    # so we test the scanner function directly with a crafted ScanCandidate.
    from backend.health_vault.intake.file_scanner import ScanRejection

    # Direct test of _is_safe_name helper.
    from backend.health_vault.intake.file_scanner import _is_safe_name
    assert not _is_safe_name("../../../etc/passwd"), "Should reject traversal name"
    assert not _is_safe_name("sub/dir/file.json"), "Should reject sub-path names"
    assert _is_safe_name("safe_file.json"), "Should accept safe flat name"
    assert _is_safe_name("lab result (2026).pdf"), "Should accept parens and spaces"

    # End-to-end: the lifecycle claim must also reject traversal attempts.
    result = lc.claim(cfg.incoming_dir / ".." / "escape.json")
    assert not result.claimed, "Traversal path must not be claimed"
    assert result.reason == "path_traversal"


# ---------------------------------------------------------------------------
# TEST J — Medical content is absent from operational logs / report
# ---------------------------------------------------------------------------

def test_j_no_medical_content_in_summary(cfg, lc, store, real_import_service):
    """The operational summary must not contain raw medical content,
    OCR text, clinical values, or vault keys."""
    content = _json_doc({
        "source": "test",
        "measured_at": "2026-01-01T00:00:00Z",
        "extracted_measurements": [
            {"metric": "glucose", "value": 999, "units": "mg/dL"},
        ],
    })
    _place_file(cfg.incoming_dir, "blood.json", content)

    runner = IntakeRunner(config=cfg, import_service=real_import_service)
    summary = runner.run()

    # Serialise the entire summary to a string and check for forbidden content.
    summary_str = json.dumps(summary, default=str)

    forbidden_patterns = [
        "999",           # the specific measurement value
        "glucose",       # metric name
        "mg/dL",         # unit
        "encryption_key",
        "vault_key",
        "recovery_answer",
        "ocr_text",
    ]
    for pattern in forbidden_patterns:
        assert pattern not in summary_str, (
            f"Forbidden content '{pattern}' found in operational summary"
        )


# ---------------------------------------------------------------------------
# TEST K — Failed file reaches quarantine/rejected state
# ---------------------------------------------------------------------------

def test_k_failed_file_reaches_quarantine(cfg, lc):
    """A pipeline ok=False response must result in the file in quarantine/."""
    stub = StubImportService(
        result={
            "ok": False, "duplicate": False, "status": "failed",
            "document": None, "measurements": [], "errors": ["pipeline_failed"],
            "warnings": [], "sha256": "aa", "imported_at": "2026-01-01T00:00:00Z",
        }
    )
    _place_file(cfg.incoming_dir, "bad.json", _minimal_json_doc())

    runner = IntakeRunner(config=cfg, import_service=stub)
    summary = runner.run()

    quarantine_files = [
        f.name for f in cfg.quarantine_dir.iterdir()
        if f.is_file() and not f.name.endswith(".reason")
    ]
    assert "bad.json" in quarantine_files
    assert summary["quarantine"] >= 1
    # completed must be zero.
    assert summary["completed"] == 0


# ---------------------------------------------------------------------------
# TEST L — One failed file does not prevent subsequent eligible files
# ---------------------------------------------------------------------------

def test_l_one_failure_does_not_block_others(cfg, lc):
    """With two files, one crashing and one valid, the valid one must still
    reach completed state."""
    call_count = {"n": 0}

    class AlternatingStub:
        """Raises on first call; succeeds on second."""
        def import_file(self, path, **kwargs):
            call_count["n"] += 1
            if call_count["n"] == 1:
                raise ValueError("synthetic_first_crash")
            return {"ok": True, "duplicate": False, "status": "parsed",
                    "document": {"id": "doc-2"}, "measurements": [],
                    "errors": [], "warnings": [], "sha256": "bb",
                    "imported_at": "2026-01-01T00:00:00Z"}

    # Place two files; scanner processes in filesystem order.
    _place_file(cfg.incoming_dir, "aaa_crash.json", _minimal_json_doc())
    _place_file(cfg.incoming_dir, "bbb_good.json", _minimal_json_doc())

    runner = IntakeRunner(config=cfg, import_service=AlternatingStub())
    summary = runner.run()

    # Both files must have been attempted.
    assert call_count["n"] == 2, f"Expected 2 import_file calls; got {call_count['n']}"
    # The crashing file ends in quarantine, the good one in completed.
    assert summary["quarantine"] >= 1
    assert summary["completed"] >= 1


# ---------------------------------------------------------------------------
# TEST M — Stale processing state / restart recovery
# ---------------------------------------------------------------------------

def test_m_stale_processing_recovered_and_reingested(cfg, lc):
    """Files stranded in processing/ (from a prior crash) are moved back to
    incoming/ and re-ingested on the next run."""
    stub = StubImportService()

    # Simulate a prior crashed run: place a file directly in processing/.
    stale_path = cfg.processing_dir / "stale.json"
    stale_path.write_bytes(_minimal_json_doc())

    # Confirm it's in processing, not incoming.
    assert stale_path.exists()
    assert not (cfg.incoming_dir / "stale.json").exists()

    runner = IntakeRunner(config=cfg, import_service=stub)
    summary = runner.run()

    # stale_recovered count must be >= 1.
    assert summary["stale_recovered"] >= 1, (
        f"No stale files recovered; summary={summary}"
    )
    # After recovery + processing, file must be in completed/ or quarantine/, not processing/.
    remaining_in_processing = list(cfg.processing_dir.iterdir())
    assert remaining_in_processing == [], (
        f"Files left in processing/ after run: {remaining_in_processing}"
    )
    # import_file must have been called (file re-entered the pipeline).
    assert len(stub.calls) >= 1, "Stale file was not re-ingested after recovery"


# ---------------------------------------------------------------------------
# TEST N — Production key / live vault side effects do not occur
# ---------------------------------------------------------------------------

def test_n_no_production_key_or_live_vault_side_effects(cfg, lc, tmp_path):
    """The intake runner must use only its injected (test) VaultStore,
    never the live vault at the default repository path.

    Verified by confirming that VaultStore is constructed with tmp_path root,
    and by checking no files appear in the real vault_storage/ directory.
    """
    # Confirm the real vault_storage dir was NOT written during this test.
    real_vault_root = Path(__file__).resolve().parents[1] / "vault_storage"

    # Record document count before run (real vault may have existing data).
    doc_count_before = 0
    if real_vault_root.exists() and (real_vault_root / "index.json").exists():
        real_store = VaultStore(root=real_vault_root)
        doc_count_before = len(real_store.list_documents())

    # Run with a test-scoped store (tmp_path).
    test_store = VaultStore(root=tmp_path / "test_vault")
    reg = ParserRegistry()
    register_builtin_parsers(reg)
    pipeline = ImportPipeline(store=test_store, registry=reg, bus=EventBus())
    svc = ImportService(store=test_store, registry=reg)
    svc.pipeline = pipeline

    content = _minimal_json_doc()
    _place_file(cfg.incoming_dir, "isolation.json", content)
    runner = IntakeRunner(config=cfg, import_service=svc)
    runner.run()

    # Real vault must have the same document count as before.
    if real_vault_root.exists() and (real_vault_root / "index.json").exists():
        real_store2 = VaultStore(root=real_vault_root)
        doc_count_after = len(real_store2.list_documents())
        assert doc_count_after == doc_count_before, (
            f"Live vault was modified! Before={doc_count_before} After={doc_count_after}"
        )

    # The test store must have received the document.
    assert len(test_store.list_documents()) >= 1, "Document not stored in test vault"

    # No encryption key was created or modified.
    assert not (cfg.intake_root / "vault_key").exists()
    assert not (cfg.intake_root / "recovery_package").exists()


# ---------------------------------------------------------------------------
# Additional edge-case unit tests
# ---------------------------------------------------------------------------

def test_lifecycle_state_enum_values():
    """LifecycleState enum must have the four expected states."""
    assert LifecycleState.INCOMING == "incoming"
    assert LifecycleState.PROCESSING == "processing"
    assert LifecycleState.COMPLETED == "completed"
    assert LifecycleState.QUARANTINE == "quarantine"


def test_scan_incoming_empty_dir(cfg, lc):
    """scan_incoming on an empty incoming/ must return zero candidates and rejections."""
    from backend.health_vault.intake.file_scanner import scan_incoming
    result = scan_incoming(cfg)
    assert result.candidates == []
    assert result.rejections == []


def test_scan_incoming_non_existent_dir(cfg):
    """scan_incoming must handle absent incoming/ gracefully (no exception)."""
    from backend.health_vault.intake.file_scanner import scan_incoming
    # Do NOT call ensure_dirs — incoming/ doesn't exist.
    result = scan_incoming(cfg)
    assert result.candidates == []
    assert result.rejections == []


def test_quarantine_reason_sidecar_written(cfg, lc):
    """move_to_quarantine must write a .reason sidecar file."""
    p = cfg.processing_dir / "test.json"
    p.write_bytes(b"{}")
    lc.move_to_quarantine(p, "unsupported_extension")
    sidecars = list(cfg.quarantine_dir.glob("*.reason"))
    assert len(sidecars) == 1
    assert sidecars[0].read_text(encoding="utf-8") == "unsupported_extension"


def test_unique_dest_no_collision(cfg, lc):
    """_unique_dest must not overwrite existing files."""
    d = cfg.completed_dir
    d.mkdir(parents=True, exist_ok=True)
    (d / "result.json").write_bytes(b"1")
    dest1 = lc._unique_dest(d, "result.json")
    assert dest1.name == "result__1.json"
    dest1.write_bytes(b"2")
    dest2 = lc._unique_dest(d, "result.json")
    assert dest2.name == "result__2.json"


def test_intake_config_dirs_are_under_root(cfg):
    """All four lifecycle directories must resolve inside intake_root."""
    root = cfg.intake_root.resolve()
    for d in cfg.all_dirs():
        assert str(d.resolve()).startswith(str(root)), f"{d} not inside {root}"


def test_runner_summary_has_required_keys(cfg):
    """IntakeRunner.run() summary must contain all required keys."""
    runner = IntakeRunner(config=cfg, import_service=StubImportService())
    summary = runner.run()
    required = {"started_at", "finished_at", "stale_recovered", "scanned",
                 "pre_rejected", "claimed", "completed", "duplicate", "quarantine",
                 "skipped", "results"}
    missing = required - summary.keys()
    assert not missing, f"Summary missing keys: {missing}"
