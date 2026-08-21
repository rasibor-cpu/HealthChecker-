"""
HC-321 — Health Snapshot dashboard: latest-valid observations, consumer status,
HealthMetricCard contract, screenshot policy, and UI markers.

Fictional fixtures only. Observational — no diagnostic claims.
"""

from __future__ import annotations

import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from backend.health_vault.clinical_rules import ClinicalRulesEngine
from backend.health_vault.health_snapshot import (
    COLOR_AMBER,
    COLOR_GREEN,
    COLOR_GREY,
    COLOR_RED,
    STATUS_ATTENTION,
    STATUS_CAUTION,
    STATUS_NORMAL,
    STATUS_UNKNOWN,
    HealthSnapshotEngine,
    accessibility_label,
    apply_layout,
    compute_freshness,
    consumer_status_from_flag,
    evaluate_consumer_status,
    is_valid_observation,
    load_health_snapshot_config,
    select_latest_valid,
    status_color,
    trend_from_values,
)
from backend.health_vault.models import Measurement, MedicalDocument
from backend.health_vault.vault_store import VaultStore

ROOT = Path(__file__).resolve().parents[1]
AS_OF = datetime(2026, 8, 21, 18, 0, tzinfo=timezone.utc)


def _iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _obs(metric: str, value, measured_at, **extra):
    row = {
        "metric": metric,
        "value": value,
        "units": extra.pop("units", None),
        "measured_at": measured_at if isinstance(measured_at, str) else _iso(measured_at),
        "provenance": extra.pop("provenance", "test"),
        "source": extra.pop("source", "fixture"),
    }
    row.update(extra)
    if row.get("units") is None:
        row.pop("units", None)
    return row


def test_latest_valid_observation_selected():
    older = _obs("glucose", 90, AS_OF - timedelta(hours=2), units="mg/dL")
    newer = _obs("glucose", 110, AS_OF - timedelta(minutes=5), units="mg/dL")
    chosen = select_latest_valid([older, newer], metric="glucose")
    assert chosen is not None
    assert chosen["value"] == 110


def test_invalid_newer_observation_skipped():
    valid = _obs("glucose", 95, AS_OF - timedelta(hours=1), units="mg/dL")
    invalid_newer = _obs("glucose", None, AS_OF - timedelta(minutes=1), units="mg/dL")
    incompatible = _obs(
        "glucose",
        999,
        AS_OF - timedelta(seconds=30),
        units="mg/dL",
        unit_compatible=False,
    )
    chosen = select_latest_valid([valid, invalid_newer, incompatible], metric="glucose")
    assert chosen["value"] == 95


def test_missing_observation_returns_none():
    assert select_latest_valid([], metric="glucose") is None
    assert select_latest_valid([_obs("heart_rate", 70, AS_OF, units="bpm")], metric="glucose") is None


def test_impossible_value_is_invalid():
    rules = ClinicalRulesEngine()
    impossible = _obs("glucose", 5, AS_OF, units="mg/dL")
    assert not is_valid_observation(impossible, rules=rules)
    assert select_latest_valid([impossible], metric="glucose", rules=rules) is None


def test_ordering_prefers_timestamp_not_list_order():
    a = _obs("resting_hr", 60, AS_OF - timedelta(days=2), units="bpm")
    b = _obs("resting_hr", 72, AS_OF - timedelta(hours=1), units="bpm")
    assert select_latest_valid([b, a], metric="resting_hr")["value"] == 72
    assert select_latest_valid([a, b], metric="resting_hr")["value"] == 72


def test_freshness_current_aging_stale_missing():
    windows = {"glucose": 60, "default": 60}
    fresh = compute_freshness(
        metric="glucose",
        measured_at=_iso(AS_OF - timedelta(minutes=10)),
        now=AS_OF,
        windows=windows,
    )
    assert fresh["freshness_status"] == "fresh"
    assert fresh["currentness"] == "current"
    assert "minute" in fresh["label"]

    aging = compute_freshness(
        metric="glucose",
        measured_at=_iso(AS_OF - timedelta(minutes=90)),
        now=AS_OF,
        windows=windows,
        stale_multiplier=3,
    )
    assert aging["freshness_status"] == "aging"
    assert aging["currentness"] == "current"

    stale = compute_freshness(
        metric="glucose",
        measured_at=_iso(AS_OF - timedelta(days=10)),
        now=AS_OF,
        windows=windows,
        stale_multiplier=3,
    )
    assert stale["freshness_status"] == "stale"
    assert stale["currentness"] == "stale"
    assert "not current" in stale["label"]

    missing = compute_freshness(metric="glucose", measured_at=None, now=AS_OF, windows=windows)
    assert missing["currentness"] == "missing"
    assert missing["age_seconds"] is None


def test_status_colour_mapping():
    assert status_color(STATUS_NORMAL) == COLOR_GREEN
    assert status_color(STATUS_CAUTION) == COLOR_AMBER
    assert status_color(STATUS_ATTENTION) == COLOR_RED
    assert status_color(STATUS_UNKNOWN) == COLOR_GREY
    assert consumer_status_from_flag("Normal") == STATUS_NORMAL
    assert consumer_status_from_flag("Borderline") == STATUS_CAUTION
    assert consumer_status_from_flag("Abnormal") == STATUS_ATTENTION
    assert consumer_status_from_flag("Critical") == STATUS_ATTENTION
    assert consumer_status_from_flag("Unknown") == STATUS_UNKNOWN


def test_bp_normal_caution_attention():
    normal = evaluate_consumer_status(metric="systolic_bp", value=118, units="mmHg")
    caution = evaluate_consumer_status(metric="systolic_bp", value=132, units="mmHg")
    attention = evaluate_consumer_status(metric="systolic_bp", value=150, units="mmHg")
    assert (normal["status"], normal["status_color"]) == (STATUS_NORMAL, COLOR_GREEN)
    assert (caution["status"], caution["status_color"]) == (STATUS_CAUTION, COLOR_AMBER)
    assert (attention["status"], attention["status_color"]) == (STATUS_ATTENTION, COLOR_RED)


def test_glucose_unknown_context_prefers_caution_for_mid_range():
    mid = evaluate_consumer_status(metric="glucose", value=110, units="mg/dL")
    assert mid["status"] == STATUS_CAUTION
    fasting_ok = evaluate_consumer_status(
        metric="glucose", value=90, units="mg/dL", context="fasting"
    )
    assert fasting_ok["status"] == STATUS_NORMAL
    post_meal = evaluate_consumer_status(
        metric="glucose", value=110, units="mg/dL", context="post_meal"
    )
    assert post_meal["status"] == STATUS_NORMAL


def test_spo2_and_egfr_and_ldl_use_clinical_rules():
    spo2_ok = evaluate_consumer_status(metric="oxygen_saturation", value=97, units="%")
    spo2_low = evaluate_consumer_status(metric="spo2", value=91, units="%")
    egfr_ok = evaluate_consumer_status(metric="egfr", value=95, units="mL/min/1.73m2")
    egfr_ckd = evaluate_consumer_status(metric="egfr", value=45, units="mL/min/1.73m2")
    ldl = evaluate_consumer_status(metric="ldl", value=160, units="mg/dL")
    assert spo2_ok["status"] == STATUS_NORMAL
    assert spo2_low["status"] == STATUS_ATTENTION
    assert egfr_ok["status"] == STATUS_NORMAL
    assert egfr_ckd["status"] == STATUS_ATTENTION
    assert ldl["status"] == STATUS_ATTENTION


def test_stale_and_missing_are_unknown_grey():
    stale = evaluate_consumer_status(
        metric="glucose", value=90, units="mg/dL", currentness="stale"
    )
    missing = evaluate_consumer_status(metric="glucose", value=None, currentness="missing")
    invalid = evaluate_consumer_status(metric="glucose", value=90, currentness="invalid")
    for row in (stale, missing, invalid):
        assert row["status"] == STATUS_UNKNOWN
        assert row["status_color"] == COLOR_GREY


def test_weight_and_steps_are_informational_unknown():
    weight = evaluate_consumer_status(metric="weight", value=82, units="kg", informational=True)
    steps = evaluate_consumer_status(metric="steps", value=6400, informational=True)
    assert weight["status"] == STATUS_UNKNOWN
    assert steps["status"] == STATUS_UNKNOWN


def test_activity_heart_rate_not_assumed_resting():
    active = evaluate_consumer_status(
        metric="heart_rate", value=118, units="bpm", context="exercise"
    )
    assert active["status"] == STATUS_UNKNOWN
    extreme = evaluate_consumer_status(
        metric="heart_rate", value=30, units="bpm", context="workout"
    )
    assert extreme["status"] == STATUS_ATTENTION


def test_sleep_duration_single_night_bands():
    ok = evaluate_consumer_status(metric="sleep_duration", value=8, units="h", sample_count=1)
    short = evaluate_consumer_status(metric="sleep_duration", value=4, units="h", sample_count=1)
    assert ok["status"] == STATUS_NORMAL
    assert "single_night" in ok["reason"]
    assert short["status"] == STATUS_ATTENTION


def test_engine_builds_clickable_cards_from_latest_valid_only():
    observations = [
        _obs("systolic_bp", 118, AS_OF - timedelta(minutes=18), units="mmHg"),
        _obs("diastolic_bp", 76, AS_OF - timedelta(minutes=18), units="mmHg"),
        _obs("glucose", None, AS_OF - timedelta(minutes=1), units="mg/dL"),
        _obs("glucose", 96, AS_OF - timedelta(minutes=40), units="mg/dL"),
        _obs("heart_rate", 72, AS_OF - timedelta(minutes=10), units="bpm"),
        _obs("oxygen_saturation", 97, AS_OF - timedelta(minutes=8), units="%"),
        _obs("weight", 81.4, AS_OF - timedelta(days=2), units="kg"),
        _obs("bmi", 24.1, AS_OF - timedelta(days=2), units="kg/m2"),
        _obs("egfr", 88, AS_OF - timedelta(days=20), units="mL/min/1.73m2"),
        _obs("ldl", 118, AS_OF - timedelta(days=30), units="mg/dL"),
        _obs("sleep_duration", 7.5, AS_OF - timedelta(hours=10), units="h"),
        _obs("steps", 8120, AS_OF - timedelta(hours=2)),
        _obs("activity_minutes", 35, AS_OF - timedelta(hours=2), units="min"),
        _obs("creatinine", 90, AS_OF - timedelta(days=20), units="umol/L"),
    ]
    snap = HealthSnapshotEngine().generate(observations=observations, as_of=AS_OF)
    ids = [c["metric_id"] for c in snap["cards"]]
    assert "blood_pressure" in ids
    assert "glucose" in ids
    bp = next(c for c in snap["cards"] if c["metric_id"] == "blood_pressure")
    assert bp["display_value"] == "118/76"
    assert bp["unit"] == "mmHg"
    assert bp["status_text"]
    assert bp["status_color"] == COLOR_GREEN
    assert bp["status"] == STATUS_NORMAL
    glucose = next(c for c in snap["cards"] if c["metric_id"] == "glucose")
    assert glucose["display_value"] == "96"
    assert glucose["unit"] == "mg/dL"
    egfr = next(c for c in snap["cards"] if c["metric_id"] == "egfr")
    assert egfr["status"] == STATUS_CAUTION
    for card in snap["cards"]:
        assert card["accessibility_label"]
        assert card["status_text"]
        assert card["detail_tab"] == "vault"
        assert card["status"] in {STATUS_NORMAL, STATUS_CAUTION, STATUS_ATTENTION, STATUS_UNKNOWN}


def test_stale_card_not_represented_as_current():
    observations = [_obs("glucose", 90, AS_OF - timedelta(days=20), units="mg/dL")]
    snap = HealthSnapshotEngine().generate(observations=observations, as_of=AS_OF)
    card = snap["cards"][0]
    assert card["currentness"] == "stale"
    assert card["status"] == STATUS_UNKNOWN
    assert card["status_color"] == COLOR_GREY
    assert "not current" in card["freshness_label"]


def test_missing_metric_is_omitted_not_fabricated():
    observations = [_obs("steps", 1000, AS_OF - timedelta(hours=1))]
    snap = HealthSnapshotEngine().generate(observations=observations, as_of=AS_OF)
    ids = [c["metric_id"] for c in snap["cards"]]
    assert "steps" in ids
    assert "blood_pressure" not in ids
    assert "glucose" not in ids


def test_trend_shown_or_omitted():
    enough = trend_from_values("glucose", [140, 120, 100])
    assert enough["direction"] == "improving"
    assert enough["indicator"]
    missing = trend_from_values("glucose", [100])
    assert missing["direction"] is None
    assert missing["reason"] == "insufficient_points"


def test_card_optional_fields_not_required():
    observations = [_obs("bmi", 22, AS_OF - timedelta(days=1), units="kg/m2")]
    card = HealthSnapshotEngine().build_cards(observations, as_of=AS_OF)[0]
    card.pop("provenance", None)
    card.pop("trend_label", None)
    card.pop("source", None)
    label = accessibility_label(card)
    assert "BMI" in label


def test_layout_reorder_and_hide():
    cards = [
        {"metric_id": "glucose"},
        {"metric_id": "blood_pressure"},
        {"metric_id": "steps"},
    ]
    visible = apply_layout(
        cards,
        {"order": ["blood_pressure", "glucose", "steps"], "hidden": ["steps"]},
        ["glucose", "blood_pressure", "steps"],
    )
    assert [c["metric_id"] for c in visible] == ["blood_pressure", "glucose"]


def test_accessibility_spoken_bp():
    label = accessibility_label(
        {
            "metric_id": "blood_pressure",
            "title": "Blood Pressure",
            "display_value": "124/78",
            "unit": "mmHg",
            "status_text": "Normal",
            "freshness_label": "Updated 18 minutes ago",
        }
    )
    assert "124 over 78" in label
    assert "millimetres of mercury" in label
    assert "status Normal" in label


def test_snapshot_from_vault_store():
    with tempfile.TemporaryDirectory() as td:
        store = VaultStore(root=Path(td))
        doc = MedicalDocument(
            patient_id="default-patient",
            document_type="bp_screenshot",
            source_system="home_monitor",
            original_filename="bp.png",
            measured_at=_iso(AS_OF - timedelta(hours=1)),
            primary_category="blood_pressure",
            classification_confidence=0.9,
            provenance="wearable_screenshot",
            sha256="e" * 64,
        )
        store.store(
            document=doc,
            measurements=[
                Measurement(
                    metric="systolic_bp",
                    value=118,
                    units="mmHg",
                    measured_at=_iso(AS_OF - timedelta(hours=1)),
                    confidence=0.9,
                ),
                Measurement(
                    metric="diastolic_bp",
                    value=76,
                    units="mmHg",
                    measured_at=_iso(AS_OF - timedelta(hours=1)),
                    confidence=0.9,
                ),
            ],
            content=b"bp",
        )
        snap = HealthSnapshotEngine(store).generate(as_of=AS_OF)
        assert any(c["metric_id"] == "blood_pressure" for c in snap["cards"])
        assert snap["observational_only"] is True


def test_api_health_snapshot_endpoint():
    pytest.importorskip("fastapi")
    from fastapi.testclient import TestClient

    from backend.health_vault.api import create_health_vault_app

    with tempfile.TemporaryDirectory() as td:
        store = VaultStore(root=Path(td))
        app = create_health_vault_app(store=store)
        client = TestClient(app)
        res = client.get("/api/health-vault/health-snapshot")
        assert res.status_code == 200
        body = res.json()
        assert body["observational_only"] is True
        assert "cards" in body


def test_ui_does_not_embed_clinical_thresholds():
    js = (ROOT / "js" / "health_vault" / "health_snapshot.js").read_text(encoding="utf-8")
    assert "renderHealthMetricCard" in js
    assert "HCHealthSnapshot" in js
    assert "NORMAL" in js and "CAUTION" in js and "ATTENTION" in js
    assert "GREEN" in js and "AMBER" in js and "RED" in js and "GREY" in js
    html = (ROOT / "index.html").read_text(encoding="utf-8")
    assert 'id="hc_health_snapshot"' in html
    assert "health_snapshot.js" in html
    assert 'id="hc_theme_toggle"' in html
    assert "hc-metric-card" in html
    exec_js = (ROOT / "js" / "health_vault" / "executive_dashboard.js").read_text(encoding="utf-8")
    assert "HCExecutiveDashboard" in exec_js
    ui = (ROOT / "js" / "health_vault" / "ui.js").read_text(encoding="utf-8")
    assert "openMetricDetail" in ui


def test_card_renderer_has_no_numeric_medical_bands():
    js = (ROOT / "js" / "health_vault" / "health_snapshot.js").read_text(encoding="utf-8")
    start = js.index("function renderHealthMetricCard")
    end = js.index("function openMetricDetail")
    renderer = js[start:end]
    assert "120" not in renderer
    assert "140" not in renderer
    assert "classify(" not in renderer


def test_config_loads_and_documents_thresholds_outside_ui():
    cfg = load_health_snapshot_config()
    assert cfg["schema_version"].startswith("hc.health_snapshot")
    assert "blood_pressure" in cfg["metrics"]


def test_no_unintended_global_flag_secure():
    android_main = ROOT / "android" / "app" / "src" / "main"
    offenders = []
    allowed_clear = 0
    for path in android_main.rglob("*"):
        if path.suffix.lower() not in {".kt", ".java", ".xml"}:
            continue
        text = path.read_text(encoding="utf-8", errors="ignore")
        if "FLAG_SECURE" not in text:
            continue
        if path.name == "ScreenshotPolicy.kt":
            assert "clearFlags" in text
            assert "addFlags" not in text
            allowed_clear += 1
            continue
        offenders.append(str(path.relative_to(ROOT)))
    assert allowed_clear == 1
    assert offenders == []


def test_sw_precaches_snapshot_module():
    sw = (ROOT / "service-worker.js").read_text(encoding="utf-8")
    assert "health_snapshot.js" in sw


def test_light_and_dark_status_tokens_are_distinct():
    html = (ROOT / "index.html").read_text(encoding="utf-8")
    dark = html.split('html[data-theme="light"]', 1)[0]
    light = html.split('html[data-theme="light"]', 1)[1].split("*{", 1)[0]
    for token in ("--status-green", "--status-amber", "--status-red", "--status-grey"):
        assert token in dark
        assert token in light
    assert "#2dd4bf" in dark and "#0f7a5c" in light
    assert "#fbbf24" in dark and "#9a6700" in light
    assert "#fb7185" in dark and "#b42318" in light
    for cls in ("hc-status-green", "hc-status-amber", "hc-status-red", "hc-status-grey"):
        assert cls in html
    assert "hc-metric-status" in html
    assert 'id="hc_theme_toggle"' in html


def test_card_navigation_reuses_vault_detail():
    snap = (ROOT / "js" / "health_vault" / "health_snapshot.js").read_text(encoding="utf-8")
    ui = (ROOT / "js" / "health_vault" / "ui.js").read_text(encoding="utf-8")
    html = (ROOT / "index.html").read_text(encoding="utf-8")
    assert "HCVaultUI.openMetricDetail" in snap
    assert "data-category=" in snap
    assert "data-detail-metric=" in snap
    assert "function openMetricDetail" in ui
    assert '.tab[data="vault"]' in ui
    assert "setCategoryFilter" in ui
    assert 'id="vault_trends"' in html
    assert 'id="vault_timeline"' in html
    assert 'data="dash"' in html and 'data="rep"' in html


def test_no_flag_secure_set_anywhere_in_android_sources():
    """Companion must not *set* FLAG_SECURE. Tests may mention the flag."""
    android = ROOT / "android"
    setters = []
    for path in android.rglob("*"):
        if path.suffix.lower() not in {".kt", ".java", ".xml"}:
            continue
        text = path.read_text(encoding="utf-8", errors="ignore")
        if "addFlags" in text and "FLAG_SECURE" in text and "Test" not in path.name:
            setters.append(str(path.relative_to(ROOT)))
        if "setFlags" in text and "FLAG_SECURE" in text:
            setters.append(str(path.relative_to(ROOT)))
    assert setters == []
    manifest = (ROOT / "android/app/src/main/AndroidManifest.xml").read_text(encoding="utf-8")
    assert "FLAG_SECURE" not in manifest
    assert 'secure="true"' not in manifest.lower()


def _node_snapshot_eval(script: str) -> str:
    import subprocess

    prelude = f"""
const fs = require('fs');
const vm = require('vm');
const ctx = {{ console, globalThis: {{}} }};
ctx.globalThis = ctx;
ctx.window = ctx;
vm.createContext(ctx);
vm.runInContext(fs.readFileSync({str(ROOT / "js/health_vault/clinical_rules.js")!r}, 'utf8'), ctx);
vm.runInContext(fs.readFileSync({str(ROOT / "js/health_vault/trend_engine.js")!r}, 'utf8'), ctx);
vm.runInContext(fs.readFileSync({str(ROOT / "js/health_vault/health_snapshot.js")!r}, 'utf8'), ctx);
const HS = ctx.HCHealthSnapshot;
{script}
"""
    proc = subprocess.run(["node", "-e", prelude], capture_output=True, text=True, check=False)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    return proc.stdout.strip()


def test_js_latest_valid_and_status_colours():
    out = _node_snapshot_eval(
        """
const rows = [
  {metric:'glucose', value:90, units:'mg/dL', measured_at:'2026-08-21T16:00:00Z'},
  {metric:'glucose', value:null, units:'mg/dL', measured_at:'2026-08-21T17:50:00Z'},
  {metric:'glucose', value:101, units:'mg/dL', measured_at:'2026-08-21T17:00:00Z'},
];
const latest = HS.selectLatestValid(rows, 'glucose');
const stN = HS.evaluateConsumerStatus({metric:'systolic_bp', value:118, units:'mmHg'});
const stC = HS.evaluateConsumerStatus({metric:'systolic_bp', value:132, units:'mmHg'});
const stA = HS.evaluateConsumerStatus({metric:'systolic_bp', value:150, units:'mmHg'});
const stU = HS.evaluateConsumerStatus({metric:'glucose', value:90, currentness:'stale'});
console.log(JSON.stringify({
  latest: latest && latest.value,
  n: [stN.status, stN.status_color],
  c: [stC.status, stC.status_color],
  a: [stA.status, stA.status_color],
  u: [stU.status, stU.status_color],
}));
"""
    )
    payload = __import__("json").loads(out)
    assert payload["latest"] == 101
    assert payload["n"] == ["NORMAL", "GREEN"]
    assert payload["c"] == ["CAUTION", "AMBER"]
    assert payload["a"] == ["ATTENTION", "RED"]
    assert payload["u"] == ["UNKNOWN", "GREY"]


def test_js_card_render_includes_value_unit_status_and_is_tappable():
    out = _node_snapshot_eval(
        """
const html = HS.renderHealthMetricCard({
  metric_id: 'blood_pressure',
  title: 'Blood Pressure',
  display_value: '118/76',
  unit: 'mmHg',
  status: 'NORMAL',
  status_text: 'Normal',
  status_color: 'GREEN',
  freshness_label: 'Updated 18 minutes ago',
  trend_label: 'Improving',
  trend_indicator: '\\u2193',
  provenance: 'wearable_screenshot',
  source: 'home_monitor',
  detail_category: 'blood_pressure',
  detail_metric: 'systolic_bp',
  accessibility_label: 'Blood pressure, 118 over 76 millimetres of mercury, status Normal, Updated 18 minutes ago.',
});
const missingTrend = HS.renderHealthMetricCard({
  metric_id: 'steps',
  title: 'Steps',
  display_value: '1000',
  unit: 'steps',
  status: 'UNKNOWN',
  status_text: 'Unknown',
  status_color: 'GREY',
  detail_category: 'other',
  detail_metric: 'steps',
});
console.log(JSON.stringify({html, missingTrend}));
"""
    )
    payload = __import__("json").loads(out)
    html = payload["html"]
    assert html.startswith('<button type="button"')
    assert "hc-metric-card" in html
    assert "hc-status-green" in html
    assert "118/76" in html
    assert "mmHg" in html
    assert "Normal" in html
    assert 'data-status="NORMAL"' in html
    assert "Updated 18 minutes ago" in html
    assert "Improving" in html
    assert "home_monitor" in html
    assert 'data-metric="blood_pressure"' in html
    assert 'data-category="blood_pressure"' in html
    assert "aria-label=" in html
    assert "118 over 76" in html
    assert "Trend unavailable" in payload["missingTrend"]
    assert "hc-status-grey" in payload["missingTrend"]


def test_js_layout_and_theme_helpers():
    out = _node_snapshot_eval(
        """
const cards = [{metric_id:'glucose'},{metric_id:'blood_pressure'},{metric_id:'steps'}];
const vis = HS.applyLayout(cards, {order:['blood_pressure','glucose','steps'], hidden:['steps']}, HS.DEFAULT_ORDER);
console.log(JSON.stringify({ids: vis.map(c => c.metric_id), hasTheme: typeof HS.applyTheme === 'function'}));
"""
    )
    payload = __import__("json").loads(out)
    assert payload["ids"] == ["blood_pressure", "glucose"]
    assert payload["hasTheme"] is True

