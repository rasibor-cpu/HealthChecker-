import json
from pathlib import Path

from backend.health_vault.date_extraction import extract_measured_date
from backend.health_vault.event_bus import EventBus
from backend.health_vault.import_pipeline import ImportPipeline
from backend.health_vault.parser_registry import ParserRegistry
from backend.health_vault.parsers import GenericJsonParser
from backend.health_vault.parsers import HealthCheckerVaultImportParser
from backend.health_vault.parsers import register_builtin_parsers
from backend.health_vault.trend_engine import TrendEngine
from backend.health_vault.vault_store import VaultStore
from backend.health_vault.consumer_records import classify_consumer_record


def test_compact_vault_rows_keep_metric_units_flags_and_dates():
    data = {
        "schema": "healthchecker.vault_import.v1",
        "generated_at": "2026-09-18T00:00:00Z",
        "measurements": [
            {
                "measured_at": "2022-04-21",
                "code": "creatinine",
                "value": 190,
                "unit": "umol/L",
                "flag": "high",
            },
            {
                "measured_at": "2023-11-29",
                "code": "egfr",
                "value": 24,
                "unit": "mL/min/1.73m2",
                "flag": "low",
            },
        ],
    }
    result = GenericJsonParser().parse({"json": data, "document_id": "doc-1"})
    rows = result["measurements"]
    assert [(row.metric, row.units, row.abnormal_flag) for row in rows] == [
        ("creatinine", "umol/L", "high"),
        ("egfr", "mL/min/1.73m2", "low"),
    ]
    assert [row.measured_at for row in rows] == ["2022-04-21", "2023-11-29"]
    assert result["report_date"] == "2026-09-18T00:00:00Z"
    assert "measurement_coverage:2022-04-21..2023-11-29" in result["notes"]


def test_versioned_vault_package_uses_dedicated_parser():
    registry = ParserRegistry()
    register_builtin_parsers(registry)
    parser = registry.resolve(
        {
            "json": {
                "schema": "healthchecker.vault_import.v1",
                "generated_at": "2026-09-18T00:00:00Z",
                "measurements": [],
            },
            "filename": "Robert_HealthChecker_Vault_Import.json",
            "mime_type": "application/json",
            "document_type": "json_measurements",
        }
    )
    assert isinstance(parser, HealthCheckerVaultImportParser)


def test_multi_date_package_uses_package_date_not_first_historical_row():
    info = extract_measured_date(
        parser_date="2026-09-18T00:00:00Z",
        imported_at="2026-09-21T23:00:00Z",
        measurement_dates=["2022-09-12", "2022-04-21", "2023-11-29"],
    )
    assert info["measured_at"] == "2026-09-18T00:00:00Z"
    assert info["date_source"] == "parser_date"


def test_one_or_two_values_are_not_described_as_stable():
    for values in ([24.0], [24.0, 24.0]):
        result = TrendEngine.classify("egfr", values)
        assert result["direction"] == "insufficient_data"
        assert result["label"] == "Insufficient data"
        assert result["reason"] == "insufficient_points"


def test_verified_multidomain_vault_category_is_not_replaced_by_first_metric():
    result = classify_consumer_record(
        filename="Robert_HealthChecker_Vault_Reprocess.json",
        document_type="json_measurements",
        source_system="healthchecker_plus",
        stored_category="laboratory_report",
        metrics=["glucose_random", "creatinine", "left_ventricular_ejection_fraction"],
        measured_at="2026-09-18T00:00:00Z",
    )
    assert result["consumer_category"] == "laboratory_report"
    assert result["consumer_category_label"] == "Laboratory"
    assert result["record_category"].value == "laboratory_report"


def test_records_ui_preserves_utc_clinical_calendar_date():
    source = (Path(__file__).parents[1] / "js" / "health_vault" / "records.js").read_text(encoding="utf-8")
    assert 'timeZone: "UTC"' in source


def test_pipeline_preserves_package_date_patient_scope_and_builds_real_trends(tmp_path):
    data = {
        "schema": "healthchecker.vault_import.v1",
        "generated_at": "2026-09-18T00:00:00Z",
        "measurements": [
            {"measured_at": "2022-04-21", "code": "egfr", "value": 34,
             "unit": "mL/min/1.73m2", "flag": "low"},
            {"measured_at": "2023-03-30", "code": "egfr", "value": 27,
             "unit": "mL/min/1.73m2", "flag": "low"},
            {"measured_at": "2023-11-29", "code": "egfr", "value": 24,
             "unit": "mL/min/1.73m2", "flag": "low"},
        ],
    }
    store = VaultStore(root=tmp_path / "vault", encryption_key=b"V" * 32)
    registry = ParserRegistry()
    register_builtin_parsers(registry)
    result = ImportPipeline(store=store, registry=registry, bus=EventBus()).run(
        {
            "patient_id": "robert",
            "content": json.dumps(data).encode(),
            "filename": "Robert_HealthChecker_Vault_Import.json",
            "mime_type": "application/json",
            "source_system": "healthchecker_plus",
        }
    )
    assert result["ok"] is True
    assert result["document"]["measured_at"] == "2026-09-18T00:00:00Z"
    assert result["document"]["date_source"] == "parser_date"
    assert {row["patient_id"] for row in result["measurements"]} == {"robert"}
    assert result["trends"]["egfr"]["sample_count"] == 3
    assert result["trends"]["egfr"]["label"] == "Worsening"
