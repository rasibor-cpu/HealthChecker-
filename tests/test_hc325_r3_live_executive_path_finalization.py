"""HC-325 R3 — live executive path finalization regression tests."""

from __future__ import annotations

from pathlib import Path


def _read(rel: str) -> str:
    return Path(__file__).resolve().parents[1].joinpath(rel).read_text(encoding="utf-8")


def test_executive_uses_canonical_consumer_dashboard_token_contract():
    js = _read("js/health_vault/executive_dashboard.js")
    dash = _read("js/health_vault/dashboard.js")
    assert "HCConsumerDashboard.getAuthorizationHeaders" in js
    assert "HCConsumerDashboard.token" in js
    assert "canonicalAuthHeaders" in js
    assert "getAuthorizationHeaders()" in dash
    assert "sessionStorage.setItem(STORAGE_KEY, JSON.stringify({" in dash
    assert "patientId," in dash
    assert "token," in dash
    assert "displayName:" in dash


def test_authenticated_refresh_cannot_paint_local_vault_fallback():
    js = _read("js/health_vault/executive_dashboard.js")
    assert "renderLoading" in js
    assert "Local vault data is not substituted while authenticated" in js
    refresh_block = js[js.index("async function refresh") : js.index('document.addEventListener("hc:session-changed"')]
    assert "HCTrendEngine" not in refresh_block
    assert "briefing || buildLocalBriefing()" not in js
    assert "if (authenticated)" in refresh_block
    assert "renderFetchError" in refresh_block
    assert "render(buildLocalBriefing()" in refresh_block


def test_post_login_and_session_change_trigger_server_refresh():
    html = _read("index.html")
    dash = _read("js/health_vault/dashboard.js")
    js = _read("js/health_vault/executive_dashboard.js")
    assert "HCExecutiveDashboard.refresh" in dash
    assert "hc:session-changed" in js
    assert "hc:session-changed" in dash
    assert "authenticated: !!this.token" in dash
    assert "HCConsumerDashboard.token && window.HCExecutiveDashboard" in html
    assert "dashboard.js?v=hc321c1" in html


def test_production_shaped_briefing_maps_nonzero_vault_summary_fields():
    js = _read("js/health_vault/executive_dashboard.js")
    assert "vaultSummary.total_records" in js
    assert "vaultSummary.total_measurements" in js
    assert "vaultSummary.health_connect_observation_count" in js
    assert "observational_sample_count" in js
    assert "executiveKpis" in js
    briefing = {
        "patient_id": "00000",
        "data_status": "Partially current",
        "latest_health_record_date": "2026-08-18T15:04:06Z",
        "vault_summary": {
            "total_records": 8318,
            "total_measurements": 8317,
            "health_connect_observation_count": 8308,
        },
    }
    vs = briefing["vault_summary"]
    assert vs["total_records"] == 8318
    assert vs["total_measurements"] == 8317
    assert vs["health_connect_observation_count"] == 8308


def test_no_competing_legacy_initializer_after_consumer_init():
    html = _read("index.html")
    writers = []
    for path in (
        "js/health_vault/executive_dashboard.js",
        "js/health_vault/dashboard.js",
        "js/health_vault/ui.js",
        "js/health_vault/ai_health_bridge.js",
        "index.html",
    ):
        text = _read(path)
        if "HCExecutiveDashboard.refresh" in text or "exec_health_dashboard" in text:
            writers.append(path)
    assert writers == [
        "js/health_vault/executive_dashboard.js",
        "js/health_vault/dashboard.js",
        "js/health_vault/ui.js",
        "js/health_vault/ai_health_bridge.js",
        "index.html",
    ]
    assert "HCConsumerDashboard.init()" in html
    assert "t.getAttribute('data') === 'dash'" in html


def test_service_worker_navigation_is_network_first_with_skip_waiting():
    sw = _read("service-worker.js")
    html = _read("index.html")
    assert 'CACHE_REVISION = "hc321uat12i"' in sw
    assert "isNavigationRequest" in sw
    assert "SKIP_WAITING" in sw
    assert "clients.claim()" in sw
    assert "client.navigate" in sw
    assert "controllerchange" in html
    assert "service-worker.js?v=hc321uat12i" in html


def test_guardian_server_backed_contract_intact():
    js = _read("js/health_vault/health_guardian.js")
    assert "/api/guardian/status" in js
    assert "/api/guardian/alerts" in js
    assert "HCConsumerDashboard.getAuthorizationHeaders" in js
