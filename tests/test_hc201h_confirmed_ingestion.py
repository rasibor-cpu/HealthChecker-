"""
HC-201H — Confirmed chronological / categorized ingestion tests (fictional only).
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from backend.health_vault.batch_import import BatchImportService
from backend.health_vault.category_classifier import classify_health_record
from backend.health_vault.date_extraction import extract_measured_date, timeline_sort_key
from backend.health_vault.event_bus import EventBus
from backend.health_vault.import_pipeline import ImportPipeline
from backend.health_vault.metric_normalization import (
    canonicalize_metric,
    normalize_measurement,
)
from backend.health_vault.parser_registry import ParserRegistry
from backend.health_vault.parsers import register_builtin_parsers
from backend.health_vault.timeline import build_timeline
from backend.health_vault.trend_engine import TrendEngine
from backend.health_vault.vault_store import VaultStore


@pytest.fixture()
def store(tmp_path: Path) -> VaultStore:
    return VaultStore(root=tmp_path / "vault")


@pytest.fixture()
def pipeline(store: VaultStore) -> ImportPipeline:
    reg = ParserRegistry()
    register_builtin_parsers(reg)
    return ImportPipeline(store=store, registry=reg, bus=EventBus())


@pytest.fixture()
def batch(store: VaultStore, pipeline: ImportPipeline) -> BatchImportService:
    return BatchImportService(store=store, pipeline=pipeline)


def _item(name: str, metrics: dict, **extra):
    body = json.dumps({"note": name, **metrics}).encode("utf-8")
    return {
        "content": body,
        "filename": name,
        "mime_type": "application/json",
        "size_bytes": len(body),
        "extracted_measurements": [{"metric": k, "value": v} for k, v in metrics.items()],
        **extra,
    }


def test_category_classification_examples():
    bp = classify_health_record(
        document_type="blood_pressure_screenshot",
        filename="bp.png",
        measurements=[{"metric": "systolic", "value": 120}, {"metric": "diastolic", "value": 80}],
    )
    assert bp["primary_category"] == "blood_pressure"

    sleep = classify_health_record(
        document_type="samsung_health_sleep",
        filename="sleep_page1.png",
        measurements=[{"metric": "sleep_score", "value": 70}],
    )
    assert sleep["primary_category"] == "sleep"

    kidney = classify_health_record(
        document_type="laboratory_pdf",
        filename="egfr_lab.json",
        measurements=[{"metric": "egfr", "value": 40}],
    )
    assert kidney["primary_category"] == "kidney_renal"
    assert "laboratory_report" in kidney["secondary_categories"]


def test_measured_date_priority():
    info = extract_measured_date(
        explicit_measured_at="2026-07-19T11:48:00-04:00",
        filename="Samsung_0719.png",
        imported_at="2026-07-19T20:00:00Z",
    )
    assert info["date_source"] == "explicit_measured_at"
    assert info["date_confidence"] >= 0.9
    assert "2026-07-19" in info["measured_at"]

    fallback = extract_measured_date(imported_at="2026-07-19T20:00:00Z")
    assert fallback["date_source"] == "imported_at_fallback"
    assert fallback["requires_review"] is True


def test_normalize_metric_names_and_units():
    m = normalize_measurement({"metric": "systolic", "value": 120, "units": "mmHg"})
    assert m["metric"] == "systolic_bp"
    assert m["original_metric"] == "systolic"
    assert m["unit_compatible"] is True

    sleep = normalize_measurement({"metric": "sleep_duration", "value": 3.5, "units": "h"})
    assert sleep["metric"] == "sleep_duration"
    assert sleep["units"] == "min"
    assert sleep["value"] == 210.0

    bad = normalize_measurement({"metric": "glucose", "value": 6.0, "units": "stones"})
    assert bad["unit_compatible"] is False


def test_pipeline_classifies_and_dates(pipeline: ImportPipeline, store: VaultStore):
    result = pipeline.run(
        {
            "content": b'{"x":1}',
            "filename": "bp_shot.png",
            "mime_type": "image/png",
            "document_type": "blood_pressure_screenshot",
            "measured_at": "2025-08-16T12:00:00Z",
            "extracted_measurements": [
                {"metric": "systolic", "value": 122},
                {"metric": "diastolic", "value": 82},
            ],
        }
    )
    assert result["ok"]
    doc = result["document"]
    assert doc["primary_category"] == "blood_pressure"
    assert doc["measured_at"].startswith("2025-08-16")
    assert any(m["metric"] == "systolic_bp" for m in result["measurements"])


def test_chronological_sorting(pipeline: ImportPipeline, store: VaultStore):
    pipeline.run(
        {
            "content": b"a",
            "filename": "old.json",
            "mime_type": "application/json",
            "measured_at": "2023-01-15T12:00:00Z",
            "extracted_measurements": [{"metric": "egfr", "value": 35}],
        }
    )
    pipeline.run(
        {
            "content": b"b",
            "filename": "new.json",
            "mime_type": "application/json",
            "measured_at": "2026-03-24T12:00:00Z",
            "extracted_measurements": [{"metric": "egfr", "value": 25}],
        }
    )
    tl = build_timeline(store, newest_first=True)
    assert timeline_sort_key(tl[0]["document"]) >= timeline_sort_key(tl[1]["document"])


def test_grouped_page_order(batch: BatchImportService, store: VaultStore):
    items = [
        {
            "content": b"p3",
            "filename": "sleep_page3.png",
            "mime_type": "image/png",
            "document_type": "samsung_health_sleep",
            "extracted_measurements": [{"metric": "sleep_score", "value": 30}],
            "measured_at": "2026-07-19T07:00:00Z",
        },
        {
            "content": b"p1",
            "filename": "sleep_page1.png",
            "mime_type": "image/png",
            "document_type": "samsung_health_sleep",
            "extracted_measurements": [{"metric": "sleep_score", "value": 32}],
            "measured_at": "2026-07-19T07:00:00Z",
        },
        {
            "content": b"p2",
            "filename": "sleep_page2.png",
            "mime_type": "image/png",
            "document_type": "samsung_health_sleep",
            "extracted_measurements": [{"metric": "sleep_score", "value": 31}],
            "measured_at": "2026-07-19T07:00:00Z",
        },
    ]
    report = batch.import_batch(items, confirmed_by_user=True, confirmation_timestamp="2026-07-19T21:00:00Z")
    assert report["imported"] == 3
    assert report["category_counts"].get("sleep") == 3
    seqs = sorted(r["sequence_number"] for r in report["results"])
    assert seqs == [1, 2, 3]
    audits = store.list_batch_audits()
    assert audits
    assert audits[-1]["confirmed_by_user"] is True
    assert audits[-1]["selected_count"] == 3


def test_batch_response_category_and_dates(batch: BatchImportService):
    report = batch.import_batch(
        [
            _item("bp.json", {"systolic": 120, "diastolic": 80}, measured_at="2026-01-10T12:00:00Z"),
            _item("glu.json", {"glucose": 110}, measured_at="2026-02-01T12:00:00Z"),
        ],
        confirmed_by_user=True,
    )
    assert report["selected"] == 2
    assert "category_counts" in report
    assert report["earliest_measured_at"] <= report["latest_measured_at"]
    for r in report["results"]:
        assert "primary_category" in r or "category" in r
        assert "measured_at" in r


def test_trend_excludes_low_confidence_and_duplicates(store: VaultStore, pipeline: ImportPipeline):
    # Three eligible glucose points for a trend
    for i, val in enumerate([100, 110, 120]):
        pipeline.run(
            {
                "content": f"g{i}".encode(),
                "filename": f"g{i}.json",
                "mime_type": "application/json",
                "measured_at": f"2026-0{i+1}-01T12:00:00Z",
                "extracted_measurements": [{"metric": "glucose", "value": val}],
            }
        )
    # Duplicate of first content
    pipeline.run(
        {
            "content": b"g0",
            "filename": "g0_dup.json",
            "mime_type": "application/json",
            "measured_at": "2026-04-01T12:00:00Z",
            "extracted_measurements": [{"metric": "glucose", "value": 999}],
        }
    )
    trends = TrendEngine(store).recompute()
    assert "glucose" in trends
    assert trends["glucose"]["sample_count"] == 3
    assert trends["glucose"]["latest"] == 120.0


def test_category_filter_timeline(store: VaultStore, pipeline: ImportPipeline):
    pipeline.run(
        {
            "content": b"bp",
            "filename": "bp.json",
            "mime_type": "application/json",
            "document_type": "blood_pressure_screenshot",
            "measured_at": "2026-01-01T00:00:00Z",
            "extracted_measurements": [
                {"metric": "systolic", "value": 120},
                {"metric": "diastolic", "value": 80},
            ],
        }
    )
    pipeline.run(
        {
            "content": b"sl",
            "filename": "sleep.json",
            "mime_type": "application/json",
            "document_type": "samsung_health_sleep",
            "measured_at": "2026-01-02T00:00:00Z",
            "extracted_measurements": [{"metric": "sleep_score", "value": 40}],
        }
    )
    only_bp = build_timeline(store, category="blood_pressure")
    assert only_bp
    assert all(e["document"]["primary_category"] == "blood_pressure" for e in only_bp)


def test_ui_confirm_assets_exist():
    html = (ROOT / "index.html").read_text(encoding="utf-8")
    assert "vault_recent" in html
    assert "vault_category_filters" in html
    assert "import_confirm.js" in html
    assert (ROOT / "js/health_vault/import_confirm.js").exists()
    assert (ROOT / "docs/HC201H_CONFIRMED_CATEGORIZED_INGESTION.md").exists()


def test_canonicalize_aliases():
    assert canonicalize_metric("systolic") == "systolic_bp"
    assert canonicalize_metric("hrv") == "hrv_rmssd"


def test_no_private_pii_in_docs():
    text = (ROOT / "docs/HC201H_CONFIRMED_CATEGORIZED_INGESTION.md").read_text(encoding="utf-8").lower()
    assert "1968-03-13" not in text
    assert "enaholo" not in text
