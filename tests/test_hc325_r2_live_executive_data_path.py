"""HC-325 R2 — live executive dashboard data-path regression tests."""

from __future__ import annotations

import tempfile
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from backend.health_vault.api import create_health_vault_app
from backend.health_vault.dashboard_service import DashboardService
from backend.health_vault.executive_briefing import ExecutiveHealthBriefingEngine
from backend.health_vault.models import create_measurement
from backend.health_vault.records_service import RecordsService
from backend.health_vault.vault_store import VaultStore


@pytest.fixture
def store():
    with tempfile.TemporaryDirectory() as td:
        yield VaultStore(root=Path(td))


def _seed_health_connect_patient(store: VaultStore, patient_id: str, count: int) -> None:
    data = store._read_index()
    for index in range(count):
        doc_id = f"hc-doc-{index}"
        metric = "heart_rate" if index % 3 == 0 else ("sleep_duration" if index % 3 == 1 else "oxygen_saturation")
        data["documents"].append(
            {
                "id": doc_id,
                "patient_id": patient_id,
                "document_type": "continuous_monitoring_observation",
                "source_system": "health_connect_companion",
                "provenance": "continuous_monitoring",
                "measured_at": f"2026-08-19T{index % 24:02d}:00:00Z",
                "imported_at": f"2026-08-19T{index % 24:02d}:00:00Z",
                "status": "imported",
                "tags": ["hc302", "continuous_monitoring"],
            }
        )
        data["measurements"].append(
            create_measurement(
                document_id=doc_id,
                metric=metric,
                value=70.0 + index,
                measured_at=f"2026-08-19T{index % 24:02d}:00:00Z",
            ).to_dict()
        )
        data["observations"].append(
            {
                "patient_id": patient_id,
                "metric_type": metric,
                "value": 70.0 + index,
                "measured_at": f"2026-08-19T{index % 24:02d}:00:00Z",
                "source": "health_connect_companion",
                "connector_id": "health_connect",
            }
        )
    store._write_index(data)


def _login(client: TestClient, user_id: str, password: str) -> str:
    response = client.post("/api/auth/login", json={"user_id": user_id, "password": password})
    assert response.status_code == 200, response.text
    return response.json()["token"]


def test_executive_endpoint_matches_classic_dashboard_counts(store):
    _seed_health_connect_patient(store, "00000", 5)
    records = RecordsService(store).list_records("00000")
    summary = DashboardService(store).get_summary("00000")
    brief = ExecutiveHealthBriefingEngine(store).generate(patient_id="00000")
    widgets = {widget.widget_id: widget for widget in summary.widgets}
    status = widgets["status_summary"].payload
    import_w = widgets["import_wizard"].payload
    assert brief["vault_summary"]["total_records"] == import_w["records_count"] == len(records) == 5
    assert brief["vault_summary"]["total_measurements"] == status["measurements_count"] == 5
    assert brief["vault_summary"]["health_connect_observation_count"] == status["health_connect_observation_count"] == 5


def test_executive_endpoint_exposes_health_connect_observational_domains(store):
    _seed_health_connect_patient(store, "00000", 6)
    domains = ExecutiveHealthBriefingEngine(store).generate(patient_id="00000")["domain_summaries"]
    assert domains["heart"]["observational_sample_count"] >= 2
    assert domains["sleep"]["observational_sample_count"] >= 2
    assert domains["respiratory"]["observational_sample_count"] >= 1


def test_authenticated_api_route_returns_executive_briefing(store):
    app = create_health_vault_app(store=store, production=False, test_users={"patient-1": "correct"})
    assert app is not None
    client = TestClient(app)
    _seed_health_connect_patient(store, "patient-1", 4)
    token = _login(client, "patient-1", "correct")
    response = client.get(
        "/api/health-vault/executive-briefing?patient_id=patient-1",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert response.status_code == 200
    payload = response.json()
    assert payload["patient_id"] == "patient-1"
    assert payload["vault_summary"]["total_records"] == 4
    assert payload["domain_summaries"]["heart"]["observational_sample_count"] >= 1


def test_executive_js_uses_authenticated_endpoint_and_session_listener():
    js = Path(__file__).resolve().parents[1].joinpath("js", "health_vault", "executive_dashboard.js").read_text(
        encoding="utf-8"
    )
    assert "fetchServerBriefing" in js
    assert '/api/health-vault/executive-briefing' in js
    assert "hc:session-changed" in js
    assert "hc_auth_session" in js
    assert "getLastBriefingSource" in js
    assert "renderFetchError" in js
    assert "buildLocalBriefing(), { source: \"local\" }" in js or 'buildLocalBriefing(), { source: "local" }' in js


def test_executive_js_does_not_substitute_local_storage_when_authenticated():
    js = Path(__file__).resolve().parents[1].joinpath("js", "health_vault", "executive_dashboard.js").read_text(
        encoding="utf-8"
    )
    assert "if (authenticated)" in js
    assert "Local vault data is not substituted while authenticated" in js
    refresh_block = js[js.index("async function refresh") : js.index("document.addEventListener(\"hc:session-changed\"")]
    assert "buildLocalBriefing()" in refresh_block
    assert "briefing || buildLocalBriefing()" not in refresh_block


def test_dashboard_js_refreshes_executive_after_summary_load():
    js = Path(__file__).resolve().parents[1].joinpath("js", "health_vault", "dashboard.js").read_text(encoding="utf-8")
    assert "HCExecutiveDashboard.refresh" in js
    assert "renderDashboard();" in js


def test_service_worker_network_first_for_executive_js():
    sw = Path(__file__).resolve().parents[1].joinpath("service-worker.js").read_text(encoding="utf-8")
    assert "hc321c1" in sw
    assert "NETWORK_FIRST_JS" in sw
    assert "executive_dashboard" in sw
    assert "isNavigationRequest" in sw
    assert "SKIP_WAITING" in sw


def test_index_html_cache_busts_executive_script():
    html = Path(__file__).resolve().parents[1].joinpath("index.html").read_text(encoding="utf-8")
    assert "executive_dashboard.js?v=hc321c1" in html


def test_guardian_js_still_prefers_server_when_authenticated():
    js = Path(__file__).resolve().parents[1].joinpath("js", "health_vault", "health_guardian.js").read_text(
        encoding="utf-8"
    )
    assert "/api/guardian/status" in js
    assert "/api/guardian/alerts" in js
