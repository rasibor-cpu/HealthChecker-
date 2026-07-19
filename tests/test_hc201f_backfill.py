"""
HC-201F — Private backfill framework tests (fictional fixtures only — no real PII).
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from backend.health_vault.backfill import (
    SCHEMA_VERSION,
    BackfillValidationError,
    run_backfill,
    validate_backfill_payload,
)
from backend.health_vault.doctor_visit import DoctorVisitMode
from backend.health_vault.timeline import build_timeline
from backend.health_vault.vault_store import VaultStore

FIXTURE = {
    "schema_version": SCHEMA_VERSION,
    "patient": {
        "patient_id": "fixture-patient",
        "display_name": "Alex Example",
        "date_of_birth": "1970-01-01",
    },
    "profile": {
        "diagnoses": ["Example condition"],
        "medications": [
            {
                "name": "ExampleMed",
                "dose": "10 mg",
                "status": "uncertain",
                "notes": "Status not confirmed",
            }
        ],
    },
    "records": [
        {
            "record_id": "fix-samsung-ecg",
            "document_type": "samsung_health_ecg",
            "provenance": "wearable_pdf",
            "source_system": "Example Wearable",
            "device": "Example Watch",
            "measured_at": "2026-07-19T11:48:00-04:00",
            "original_filename": "Example_ECG.pdf",
            "interpretation": "Example sinus rhythm",
            "measurements": [
                {"metric": "heart_rhythm", "value": "sinus rhythm"},
                {"metric": "average_hr", "value": 60, "units": "bpm"},
            ],
            "tags": ["example", "ecg"],
        },
        {
            "record_id": "fix-sleep",
            "document_type": "samsung_health_sleep",
            "provenance": "wearable_screenshot",
            "source_system": "Example Wearable",
            "measured_at": "2026-07-19T07:00:00-04:00",
            "original_filename": "example_sleep.json",
            "context_note": "Short sleep due to late bedtime — not a diagnosis.",
            "measurements": [
                {"metric": "sleep_score", "value": 32},
                {"metric": "sleep_duration", "value": 3.6, "units": "h"},
                {"metric": "energy_score", "value": 68},
            ],
            "tags": ["example", "sleep"],
        },
        {
            "record_id": "fix-egfr",
            "document_type": "laboratory_pdf",
            "provenance": "historical_summary",
            "source_system": "historical_summary",
            "measured_at": "2025-06-19T12:00:00Z",
            "original_filename": "example_egfr.json",
            "measurements": [{"metric": "egfr", "value": 27, "units": "mL/min/1.73m2"}],
            "tags": ["kidney"],
        },
        {
            "record_id": "fix-hba1c",
            "document_type": "laboratory_pdf",
            "provenance": "historical_summary",
            "source_system": "historical_summary",
            "measured_at": "2025-12-15T12:00:00Z",
            "original_filename": "example_hba1c.json",
            "measurements": [{"metric": "hba1c", "value": 8.2, "units": "%"}],
            "tags": ["diabetes"],
        },
        {
            "record_id": "fix-bp",
            "document_type": "blood_pressure_screenshot",
            "provenance": "user_reported",
            "source_system": "user_reported",
            "measured_at": "2025-08-16T12:00:00Z",
            "original_filename": "example_bp.json",
            "measurements": [
                {"metric": "systolic", "value": 211, "units": "mmHg"},
                {"metric": "diastolic", "value": 125, "units": "mmHg"},
            ],
            "tags": ["bp"],
        },
        {
            "record_id": "fix-weight",
            "document_type": "json_measurements",
            "provenance": "historical_summary",
            "source_system": "historical_summary",
            "measured_at": "2025-07-31T12:00:00Z",
            "original_filename": "example_weight.json",
            "measurements": [{"metric": "weight", "value": 97, "units": "kg"}],
            "tags": ["weight"],
        },
    ],
}


@pytest.fixture()
def fixture_path(tmp_path: Path) -> Path:
    path = tmp_path / "fixture_backfill.json"
    path.write_text(json.dumps(FIXTURE), encoding="utf-8")
    return path


@pytest.fixture()
def store(tmp_path: Path) -> VaultStore:
    return VaultStore(root=tmp_path / "vault")


def test_validate_rejects_bad_provenance():
    bad = json.loads(json.dumps(FIXTURE))
    bad["records"][0]["provenance"] = "lab_verified_fake"
    errs = validate_backfill_payload(bad)
    assert errs


def test_validate_template_example():
    template = ROOT / "docs" / "examples" / "health_backfill_template.json"
    payload = json.loads(template.read_text(encoding="utf-8"))
    assert validate_backfill_payload(payload) == []
    blob = template.read_text(encoding="utf-8").lower()
    assert "robert" not in blob
    assert "asibor" not in blob


def test_idempotent_backfill(fixture_path: Path, store: VaultStore):
    first = run_backfill(fixture_path, store=store)
    assert first["ok"] is True
    assert first["imported"] == len(FIXTURE["records"])
    assert first["duplicates"] == 0
    assert first["final_document_count"] == len(FIXTURE["records"])

    second = run_backfill(fixture_path, store=store)
    assert second["ok"] is True
    assert second["imported"] == 0
    assert second["duplicates"] == len(FIXTURE["records"])
    assert second["final_document_count"] == len(FIXTURE["records"])
    assert second["final_measurement_count"] == first["final_measurement_count"]


def test_provenance_labels_persisted(fixture_path: Path, store: VaultStore):
    run_backfill(fixture_path, store=store)
    docs = store.list_documents()
    provenances = {d.get("provenance") for d in docs}
    assert "wearable_pdf" in provenances
    assert "user_reported" in provenances
    assert "historical_summary" in provenances
    for d in docs:
        assert any(str(t).startswith("provenance:") for t in (d.get("tags") or []))


def test_uncertain_medication_status(fixture_path: Path, store: VaultStore):
    run_backfill(fixture_path, store=store)
    meds = store.get_profile().get("medications") or []
    assert any("[status:uncertain]" in m for m in meds)


def test_chronological_ordering(fixture_path: Path, store: VaultStore):
    run_backfill(fixture_path, store=store)
    timeline = build_timeline(store)
    dates = [str(e.get("date") or "") for e in timeline]
    assert dates == sorted(dates, reverse=True)


def test_samsung_and_historical_mapping(fixture_path: Path, store: VaultStore):
    run_backfill(fixture_path, store=store)
    metrics = {m["metric"]: m["value"] for m in store.list_measurements()}
    assert metrics.get("average_hr") == 60
    assert metrics.get("sleep_score") == 32
    assert metrics.get("egfr") == 27
    assert metrics.get("hba1c") == 8.2
    assert metrics.get("systolic_bp") == 211 or metrics.get("systolic") == 211
    assert metrics.get("weight") == 97


def test_doctor_visit_includes_records(fixture_path: Path, store: VaultStore):
    run_backfill(fixture_path, store=store)
    report = DoctorVisitMode(store).generate(patient_id="fixture-patient")
    assert report["current_diagnoses"]
    assert any("[status:uncertain]" in m for m in report["current_medications"])
    assert report["recent_ecg"]
    assert "egfr" in report["kidney_trend"].lower() or "latest" in report["kidney_trend"].lower()
    assert report["health_timeline"]


def test_private_path_gitignored():
    gi = (ROOT / ".gitignore").read_text(encoding="utf-8")
    assert "private_imports/**" in gi
    # Real private file must not be tracked
    tracked = subprocess.check_output(
        ["git", "ls-files", "private_imports/robert_health_backfill.json"],
        cwd=str(ROOT),
        text=True,
    ).strip()
    assert tracked == ""


def test_no_pii_in_committed_fixtures():
    paths = [
        ROOT / "docs" / "examples" / "health_backfill_template.json",
        ROOT / "docs" / "HC201F_HEALTH_RECORD_BACKFILL.md",
    ]
    # Avoid matching this test module's own assertion strings.
    banned = ("1968-03-13", "enaholo", "galaxy watch5")
    for path in paths:
        text = path.read_text(encoding="utf-8").lower()
        for token in banned:
            assert token not in text, f"{token} found in {path}"
    # Template must stay fictional
    template = (ROOT / "docs" / "examples" / "health_backfill_template.json").read_text(
        encoding="utf-8"
    ).lower()
    assert "alex example" in template
    assert "examplemed" in template.replace(" ", "")

def test_cli_module_dry_run(fixture_path: Path, tmp_path: Path):
    vault = tmp_path / "cli-vault"
    proc = subprocess.run(
        [
            sys.executable,
            "-m",
            "backend.health_vault.backfill",
            "--input",
            str(fixture_path),
            "--vault-root",
            str(vault),
            "--dry-run",
        ],
        cwd=str(ROOT),
        capture_output=True,
        text=True,
        check=False,
    )
    assert proc.returncode == 0
    assert "dry_run" in proc.stdout


def test_invalid_payload_raises(tmp_path: Path, store: VaultStore):
    path = tmp_path / "bad.json"
    path.write_text(json.dumps({"schema_version": SCHEMA_VERSION, "records": []}), encoding="utf-8")
    # empty records is schema-valid; force bad provenance
    path.write_text(
        json.dumps(
            {
                "schema_version": SCHEMA_VERSION,
                "records": [
                    {
                        "record_id": "x",
                        "provenance": "nope",
                        "measurements": [{"metric": "egfr", "value": 1}],
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    with pytest.raises(BackfillValidationError):
        run_backfill(path, store=store)
