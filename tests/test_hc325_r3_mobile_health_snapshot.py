"""HC325-R3 — authenticated /mobile Health Snapshot above-the-fold contract.

Reuses HC321 health_snapshot.js / snapshot API. Fictional fixtures only.
"""

from __future__ import annotations

import json
import subprocess
import tempfile
from datetime import datetime, timezone
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


def _read(rel: str) -> str:
    return (ROOT / rel).read_text(encoding="utf-8")


def _node_eval(script: str) -> str:
    prelude = f"""
const fs = require('fs');
const vm = require('vm');
const ctx = {{ console }};
ctx.globalThis = ctx;
ctx.window = ctx;
ctx.document = {{
  body: {{ classList: {{ contains: (name) => name === 'mobile-consumer' }} }},
  getElementById: () => null,
  addEventListener: () => {{}},
  querySelector: () => null,
  querySelectorAll: () => [],
}};
ctx.sessionStorage = {{
  _data: {{}},
  getItem(key) {{ return this._data[key] || null; }},
  setItem(key, value) {{ this._data[key] = String(value); }},
  removeItem(key) {{ delete this._data[key]; }},
}};
vm.createContext(ctx);
vm.runInContext(fs.readFileSync({str(ROOT / "js/health_vault/health_snapshot.js")!r}, 'utf8'), ctx);
const HS = ctx.HCHealthSnapshot;
{script}
"""
    proc = subprocess.run(["node", "-e", prelude], capture_output=True, text=True, check=False)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    return proc.stdout.strip()


def test_mobile_dashboard_mounts_health_snapshot_before_secondary_content():
    html = _read("mobile.html")
    assert 'id="hc_health_snapshot"' in html
    assert 'aria-label="Health Snapshot"' in html
    assert "/js/health_vault/health_snapshot.js" in html
    assert "/js/health_vault/mobile_consumer.js" in html
    dash = html.split('id="mobile_dashboard"', 1)[1].split('id="mobile_records"', 1)[0]
    assert 'id="hc_health_snapshot"' in dash
    assert html.index('id="hc_health_snapshot"') < html.index('data-mobile-content')
    assert html.index("/js/health_vault/health_snapshot.js") < html.index(
        "/js/health_vault/mobile_consumer.js"
    )
    assert "vault_store.js" not in html
    assert "service-worker.js" not in html


def test_mobile_dashboard_calls_existing_snapshot_refresh_not_local_thresholds():
    js = _read("js/health_vault/mobile_consumer.js")
    snap = _read("js/health_vault/health_snapshot.js")
    assert "HCHealthSnapshot.refresh" in js
    assert "hc_health_snapshot" in js
    assert "hc:session-changed" in js
    assert "glucoseStatus" not in js
    assert "heartRateStatus" not in js
    assert "classifyFlag" not in js
    assert "HCClinicalRules" not in js
    assert "evaluateConsumerStatus" not in js
    assert "function classify(" not in js
    renderer = snap[
        snap.index("function renderHealthMetricCard") : snap.index("function openMetricDetail")
    ]
    assert "120" not in renderer
    assert "140" not in renderer
    assert "classify(" not in renderer
    assert "openMetricDetail" in snap
    assert "bindCardClicks" in snap
    assert 'class="hc-metric-card' in snap
    assert "data-metric" in snap


def test_mobile_landing_order_prioritizes_required_cards():
    out = _node_eval(
        """
const order = HS.MOBILE_LANDING_ORDER;
const desktop = HS.DEFAULT_ORDER;
const cards = HS.applyLayout([
  {metric_id:'steps', title:'Steps'},
  {metric_id:'egfr', title:'Kidney function (eGFR)'},
  {metric_id:'glucose', title:'Glucose'},
  {metric_id:'heart_rate', title:'Heart Rate'},
  {metric_id:'blood_pressure', title:'Blood Pressure'},
  {metric_id:'oxygen_saturation', title:'Oxygen Saturation'},
  {metric_id:'sleep_duration', title:'Sleep'},
  {metric_id:'weight', title:'Weight'},
], {order: order, hidden: []}, desktop);
console.log(JSON.stringify({
  order: order,
  desktopFirst: desktop[0],
  ids: cards.map(c => c.metric_id),
}));
"""
    )
    payload = json.loads(out)
    assert payload["desktopFirst"] == "blood_pressure"
    for required in (
        "heart_rate",
        "blood_pressure",
        "glucose",
        "oxygen_saturation",
        "steps",
        "sleep_duration",
        "egfr",
    ):
        assert required in payload["order"]
    assert payload["ids"][:7] == [
        "heart_rate",
        "blood_pressure",
        "glucose",
        "oxygen_saturation",
        "steps",
        "sleep_duration",
        "egfr",
    ]


def test_cards_remain_buttons_with_stale_unknown_semantics():
    out = _node_eval(
        """
const html = HS.renderHealthMetricCard({
  metric_id: 'heart_rate',
  title: 'Heart Rate',
  display_value: '72',
  unit: 'bpm',
  status: 'NORMAL',
  status_text: 'Normal',
  status_color: 'GREEN',
  freshness_label: 'Updated 5 min ago',
  accessibility_label: 'Heart Rate 72 bpm Normal',
  detail_category: 'ecg_cardiology',
  detail_metric: 'heart_rate',
});
const stale = HS.normalizeSnapshotCard({
  metric_id: 'sleep_duration',
  title: 'Sleep',
  display_value: '7.5',
  unit: 'h',
  status: 'ATTENTION',
  status_text: 'Attention',
  status_color: 'RED',
  measured_at: '2020-01-01T00:00:00Z',
}, Date.parse('2026-08-21T12:00:00Z'));
console.log(JSON.stringify({
  html: html,
  stale_status: stale.status,
  stale_color: stale.status_color,
  stale_label: stale.freshness_label,
}));
"""
    )
    payload = json.loads(out)
    assert 'type="button"' in payload["html"]
    assert 'class="hc-metric-card' in payload["html"]
    assert 'data-metric="heart_rate"' in payload["html"]
    assert payload["stale_status"] == "UNKNOWN"
    assert payload["stale_color"] == "GREY"
    assert "not current" in (payload["stale_label"] or "").lower() or payload["stale_status"] == "UNKNOWN"


def test_snapshot_auth_reads_mobile_session_and_style_is_compact():
    snap = _read("js/health_vault/health_snapshot.js")
    css = _read("style.css")
    policy = _read("android/app/src/main/java/com/healthchecker/companion/consumer/ConsumerOriginPolicy.kt")
    assert "hc_mobile_auth_session" in snap
    assert "hc_auth_session" in snap
    assert "body.mobile-consumer .hc-metric-grid" in css
    assert "grid-template-columns: 1fr 1fr" in css
    assert "min-height: 44px" in css
    assert "path == \"/js/health_vault/health_snapshot.js\"" in policy
    assert 'path == "/"' not in policy
    assert 'path == "/index.html"' not in policy


def test_authenticated_mobile_page_and_snapshot_api_are_patient_scoped():
    pytest.importorskip("fastapi")
    from fastapi.testclient import TestClient

    from backend.health_vault.api import create_health_vault_app
    from backend.health_vault.vault_store import VaultStore

    with tempfile.TemporaryDirectory() as td:
        store = VaultStore(root=Path(td), encryption_key=b"R" * 32)
        app = create_health_vault_app(
            store=store,
            production=True,
            bootstrap_password="Boot-Pass-HC325R3xx",
        )
        client = TestClient(app)
        page = client.get("/mobile")
        assert page.status_code == 200
        assert 'id="hc_health_snapshot"' in page.text
        assert "/js/health_vault/health_snapshot.js" in page.text
        script = client.get("/js/health_vault/health_snapshot.js")
        assert script.status_code == 200
        assert "HCHealthSnapshot" in script.text

        now = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
        store.upsert_observation(
            {
                "patient_id": "00000",
                "metric_type": "heart_rate",
                "value": 72,
                "unit": "bpm",
                "measured_at": now,
                "acquisition_mode": "HEALTH_CONNECT",
                "fingerprint": "hc325-r3-owner-hr",
            }
        )
        store.upsert_observation(
            {
                "patient_id": "other-user",
                "metric_type": "heart_rate",
                "value": 99,
                "unit": "bpm",
                "measured_at": now,
                "acquisition_mode": "HEALTH_CONNECT",
                "fingerprint": "hc325-r3-other-hr",
            }
        )
        denied = client.get("/api/health-vault/health-snapshot")
        assert denied.status_code in (401, 403)
        login = client.post(
            "/api/auth/login",
            json={"patient_id": "00000", "password": "Boot-Pass-HC325R3xx"},
        )
        assert login.status_code == 200
        token = login.json()["token"]
        if login.json().get("must_change_password"):
            changed = client.post(
                "/api/auth/password/change",
                headers={"Authorization": f"Bearer {token}"},
                json={
                    "current_password": "Boot-Pass-HC325R3xx",
                    "new_password": "Owner-HC325R3-Password1",
                },
            )
            assert changed.status_code == 200
            token = changed.json()["token"]
        headers = {"Authorization": f"Bearer {token}"}
        snap = client.get("/api/health-vault/health-snapshot", headers=headers)
        assert snap.status_code == 200
        cards = snap.json().get("cards") or []
        hr = next((c for c in cards if c.get("metric_id") == "heart_rate"), None)
        assert hr is not None
        assert str(hr.get("display_value")) in ("72", "72.0")
        assert "99" not in str(hr.get("display_value"))
        detail = client.get(
            "/api/health-vault/health-snapshot?metric=heart_rate", headers=headers
        )
        assert detail.status_code == 200
        body = detail.json()
        assert body.get("card") or body.get("found") is not False


def test_screenshot_allowance_on_consumer_screens_is_preserved():
    launcher = _read(
        "android/app/src/main/java/com/healthchecker/companion/ui/ConsumerLauncherActivity.kt"
    )
    policy = _read("android/app/src/main/java/com/healthchecker/companion/ui/ScreenshotPolicy.kt")
    assert "addFlags(WindowManager.LayoutParams.FLAG_SECURE)" not in launcher
    assert "ScreenshotPolicy.applyConsumerScreenshotPolicy" in launcher
    assert "clearFlags(WindowManager.LayoutParams.FLAG_SECURE)" in policy
    assert "addFlags(WindowManager.LayoutParams.FLAG_SECURE)" not in policy
