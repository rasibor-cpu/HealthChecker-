"""
HC-202 — AI Health Bridge and ChatGPT Connector V1 tests.

Fictional fixtures only. No private health data.
"""

from __future__ import annotations

import json
import tempfile
from pathlib import Path

import pytest

from backend.ai_health.bridge import AIHealthBridge, DISCLAIMER
from backend.ai_health.connectors.base import get_connector, list_connectors, resolve_connector
from backend.ai_health.connectors.chatgpt import ChatGPTConnector
from backend.health_vault.api import (
    ai_health_import_confirm_handler,
    ai_health_import_history_handler,
    ai_health_import_preview_handler,
    create_health_vault_app,
)
from backend.health_vault.import_service import ImportService
from backend.health_vault.parser_registry import ParserRegistry
from backend.health_vault.parsers import register_builtin_parsers
from backend.health_vault.vault_store import VaultStore


def _store(tmp_path: Path) -> VaultStore:
    return VaultStore(root=tmp_path / "vault")


def _bridge(store: VaultStore) -> AIHealthBridge:
    reg = ParserRegistry()
    register_builtin_parsers(reg)
    pipeline_store = store
    from backend.health_vault.import_pipeline import ImportPipeline

    pipeline = ImportPipeline(store=pipeline_store, registry=reg)
    return AIHealthBridge(store=store, pipeline=pipeline)


def _sample_payload(**overrides) -> dict:
    base = {
        "provider_id": "chatgpt",
        "conversation": {
            "conversation_id": "conv-fictional-001",
            "message_timestamp": "2026-07-19T12:00:00Z",
            "model": "gpt-test",
            "conversation_text": "User: fictional chat body must not persist",
        },
        "records": [
            {
                "filename": "fictional_glucose.json",
                "measured_at": "2026-07-19T10:00:00Z",
                "document_type": "ai_assisted_import",
                "extracted_measurements": [
                    {
                        "metric": "glucose",
                        "value": 112,
                        "units": "mg/dL",
                        "category": "diabetes_glucose",
                    }
                ],
                "interpretation": "Observational glucose reading",
                "confidence": 0.88,
                "provenance": "imported_json",
            },
            {
                "filename": "fictional_bp.json",
                "measured_at": "2026-07-18T08:00:00Z",
                "extracted_measurements": [
                    {"metric": "systolic_bp", "value": 118, "units": "mmHg"},
                    {"metric": "diastolic_bp", "value": 76, "units": "mmHg"},
                ],
                "confidence": 0.9,
            },
        ],
    }
    base.update(overrides)
    return base


def test_connectors_register_chatgpt():
    providers = list_connectors()
    ids = {p["provider_id"] for p in providers}
    assert "chatgpt" in ids
    assert get_connector("chatgpt") is not None


def test_chatgpt_normalizes_records():
    conn = ChatGPTConnector()
    out = conn.normalize_payload(_sample_payload())
    assert out["provider_id"] == "chatgpt"
    assert out["record_count"] == 2
    assert out["records"][0]["acquisition_method"] == "external_ai"
    assert out["records"][0]["linkage"]["provider_id"] == "chatgpt"


def test_conversation_text_not_stored_by_default(tmp_path: Path):
    store = _store(tmp_path)
    bridge = _bridge(store)
    preview = bridge.preview(_sample_payload())
    conv = preview.get("conversation") or {}
    assert "conversation_text" not in conv
    confirm = bridge.confirm({"preview_id": preview["preview_id"], "confirmed": True})
    assert confirm["ok"]
    audits = store.list_ai_import_audits()
    assert len(audits) == 1
    assert "conversation_text" not in (audits[0].get("conversation") or {})


def test_preview_does_not_import_documents(tmp_path: Path):
    store = _store(tmp_path)
    bridge = _bridge(store)
    before = len(store.list_documents())
    preview = bridge.preview(_sample_payload())
    assert preview["ok"]
    assert preview["record_count"] == 2
    assert len(store.list_documents()) == before
    assert preview.get("requires_confirmation") is True


def test_preview_includes_categories_and_date_range(tmp_path: Path):
    bridge = _bridge(_store(tmp_path))
    preview = bridge.preview(_sample_payload())
    assert preview["categories"]
    assert preview["date_range"]["earliest"] <= preview["date_range"]["latest"]
    assert "Import into HealthChecker+" in preview["message"]


def test_confirm_rejects_without_explicit_confirmation(tmp_path: Path):
    bridge = _bridge(_store(tmp_path))
    preview = bridge.preview(_sample_payload())
    result = bridge.confirm({"preview_id": preview["preview_id"], "confirmed": False})
    assert result["ok"] is False
    assert "explicit_user_confirmation_required" in result["errors"][0]


def test_confirm_imports_through_pipeline(tmp_path: Path):
    store = _store(tmp_path)
    bridge = _bridge(store)
    preview = bridge.preview(_sample_payload())
    result = bridge.confirm({"preview_id": preview["preview_id"], "confirmed": True})
    assert result["ok"]
    assert result["imported"] == 2
    assert result["failed"] == 0
    assert result["dashboard_refreshed"] is True
    assert result["doctor_visit_updated"] is True
    assert len(store.list_documents()) == 2


def test_confirm_records_audit_metadata(tmp_path: Path):
    store = _store(tmp_path)
    bridge = _bridge(store)
    preview = bridge.preview(_sample_payload())
    bridge.confirm({"preview_id": preview["preview_id"], "confirmed": True})
    audits = store.list_ai_import_audits()
    assert len(audits) == 1
    entry = audits[0]
    assert entry["ai_provider"] == "chatgpt"
    assert entry["user_confirmation"] is True
    assert entry["imported_count"] == 2
    assert entry.get("confidence") is not None


def test_import_history_endpoint_handler(tmp_path: Path):
    store = _store(tmp_path)
    bridge = _bridge(store)
    preview = bridge.preview(_sample_payload())
    bridge.confirm({"preview_id": preview["preview_id"], "confirmed": True})
    hist = ai_health_import_history_handler(store=store)
    assert hist["ok"]
    assert len(hist["entries"]) >= 1
    assert hist["disclaimer"] == DISCLAIMER


def test_preview_ticket_consumed(tmp_path: Path):
    store = _store(tmp_path)
    bridge = _bridge(store)
    preview = bridge.preview(_sample_payload())
    pid = preview["preview_id"]
    bridge.confirm({"preview_id": pid, "confirmed": True})
    ticket = store.get_ai_import_preview(pid)
    assert ticket["status"] == "consumed"
    second = bridge.confirm({"preview_id": pid, "confirmed": True})
    assert second["ok"] is False


def test_duplicate_estimate_and_skip_on_reimport(tmp_path: Path):
    store = _store(tmp_path)
    bridge = _bridge(store)
    first = bridge.confirm({**_sample_payload(), "confirmed": True})
    assert first["imported"] == 2
    preview2 = bridge.preview(_sample_payload())
    assert preview2["duplicate_estimate"] >= 1
    second = bridge.confirm({"preview_id": preview2["preview_id"], "confirmed": True})
    assert second["duplicates"] >= 1


def test_linkage_on_import_results(tmp_path: Path):
    bridge = _bridge(_store(tmp_path))
    preview = bridge.preview(_sample_payload())
    result = bridge.confirm({"preview_id": preview["preview_id"], "confirmed": True})
    for row in result["results"]:
        if row["status"] == "imported":
            link = row.get("linkage") or {}
            assert link.get("executive_dashboard") is True
            assert link.get("doctor_visit") is True
            assert link.get("document_id")


def test_handlers_preview_and_confirm(tmp_path: Path):
    store = _store(tmp_path)
    preview = ai_health_import_preview_handler(_sample_payload(), store=store)
    assert preview["ok"]
    confirm = ai_health_import_confirm_handler(
        {"preview_id": preview["preview_id"], "confirmed": True},
        store=store,
    )
    assert confirm["imported"] == 2


def test_api_routes_exist(tmp_path: Path):
    pytest.importorskip("fastapi")
    from fastapi.testclient import TestClient

    store = _store(tmp_path)
    app = create_health_vault_app(store=store)
    assert app is not None
    client = TestClient(app)
    prev = client.post("/api/ai-health/import-preview", json=_sample_payload())
    assert prev.status_code == 200
    body = prev.json()
    conf = client.post(
        "/api/ai-health/import-confirm",
        json={"preview_id": body["preview_id"], "confirmed": True},
    )
    assert conf.status_code == 200
    hist = client.get("/api/ai-health/import-history")
    assert hist.status_code == 200
    assert len(hist.json().get("entries") or []) >= 1


def test_api_redacts_absolute_paths(tmp_path: Path):
    pytest.importorskip("fastapi")
    from fastapi.testclient import TestClient

    store = _store(tmp_path)
    app = create_health_vault_app(store=store)
    client = TestClient(app)
    payload = _sample_payload()
    payload["records"][0]["local_path"] = r"C:\Users\fictional\secret.pdf"
    resp = client.post("/api/ai-health/import-preview", json=payload)
    blob = json.dumps(resp.json())
    assert r"C:\Users" not in blob


def test_resolve_connector_defaults_to_chatgpt():
    conn = resolve_connector({})
    assert conn.provider_id == "chatgpt"


def test_disclaimer_on_preview_and_confirm(tmp_path: Path):
    bridge = _bridge(_store(tmp_path))
    preview = bridge.preview(_sample_payload())
    assert "observational" in preview["disclaimer"].lower()
    result = bridge.confirm({"preview_id": preview["preview_id"], "confirmed": True})
    assert result["disclaimer"] == DISCLAIMER


def test_timeline_updated_after_ai_import(tmp_path: Path):
    store = _store(tmp_path)
    bridge = _bridge(store)
    preview = bridge.preview(_sample_payload())
    result = bridge.confirm({"preview_id": preview["preview_id"], "confirmed": True})
    assert result["timeline_entries"] >= 1
    assert result["trend_metrics"] >= 0


def test_connector_does_not_write_to_store(tmp_path: Path):
    store = _store(tmp_path)
    conn = ChatGPTConnector()
    conn.normalize_payload(_sample_payload())
    assert len(store.list_documents()) == 0
    assert len(store.list_ai_import_audits()) == 0


def test_one_shot_confirm_with_full_payload(tmp_path: Path):
    store = _store(tmp_path)
    bridge = _bridge(store)
    result = bridge.confirm({**_sample_payload(), "confirmed": True})
    assert result["imported"] == 2


def test_preview_record_review_flags(tmp_path: Path):
    bridge = _bridge(_store(tmp_path))
    payload = _sample_payload()
    payload["records"][1]["extracted_measurements"] = []
    preview = bridge.preview(payload)
    empty = [r for r in preview["records"] if r["measurement_count"] == 0]
    assert empty
    assert "no_measurements" in (empty[0].get("review_flags") or [])


def test_import_service_still_canonical_for_ai_fields(tmp_path: Path):
    """Regression: direct pipeline path still accepts external_ai extracted measurements."""
    store = _store(tmp_path)
    reg = ParserRegistry()
    register_builtin_parsers(reg)
    svc = ImportService(store=store, registry=reg)
    out = svc.import_health_record(
        {
            "filename": "ai.json",
            "content": b'{"extracted_measurements":[{"metric":"glucose","value":100,"units":"mg/dL"}]}',
            "mime_type": "application/json",
            "acquisition_method": "external_ai",
            "extracted_measurements": [{"metric": "glucose", "value": 100, "units": "mg/dL"}],
            "confidence": 0.9,
        }
    )
    assert out["ok"]
    assert not out.get("duplicate")
