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
    dedupe_observation_history,
    evaluate_consumer_status,
    fallback_observation_identity,
    is_valid_observation,
    load_health_snapshot_config,
    observation_display_identity,
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
    # Beyond the configured current-data window: not a CURRENT clinical picture.
    assert aging["currentness"] == "stale"
    assert "not current" in aging["label"]

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
    assert ('id="hc_theme_toggle"' in html) or ('id="theme_toggle_btn"' in html) or ("Switch Theme" in html)
    assert 'id="hc_health_snapshot"' in html
    assert "hc-metric-card" in html or "hc-metric-card" in (ROOT / "js" / "health_vault" / "health_snapshot.js").read_text(encoding="utf-8")
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
    """Do not globally set FLAG_SECURE. Production SecureWindowPolicy may apply it selectively."""
    android_main = ROOT / "android" / "app" / "src" / "main"
    if not android_main.exists():
        return
    allowed_names = {
        "ScreenshotPolicy.kt",  # PR clear-only helper (optional; signing deferred)
        "SecureWindowPolicy.kt",  # production selective sensitive-surface policy
        "ConsumerLauncherActivity.kt",  # applies SecureWindowPolicy only when needed
    }
    offenders = []
    for path in android_main.rglob("*"):
        if path.suffix.lower() not in {".kt", ".java", ".xml"}:
            continue
        text = path.read_text(encoding="utf-8", errors="ignore")
        if "FLAG_SECURE" not in text:
            continue
        if path.name in allowed_names:
            continue
        offenders.append(str(path.relative_to(ROOT)))
    assert not offenders
    # Must not blanket FLAG_SECURE in Application or manifest
    for app_path in android_main.rglob("*Application*.kt"):
        text = app_path.read_text(encoding="utf-8", errors="ignore")
        assert "FLAG_SECURE" not in text


def test_sw_precaches_snapshot_module():
    sw = (ROOT / "service-worker.js").read_text(encoding="utf-8")
    assert "health_snapshot.js" in sw


def test_light_and_dark_status_tokens_are_distinct():
    html = (ROOT / "index.html").read_text(encoding="utf-8")
    # Production uses :root (dark defaults) + body.light-theme overrides (HC-316 consumer theme).
    if 'html[data-theme="light"]' in html:
        dark = html.split('html[data-theme="light"]', 1)[0]
        light = html.split('html[data-theme="light"]', 1)[1].split("*{", 1)[0]
    else:
        assert "body.light-theme" in html
        dark = html.split("body.light-theme", 1)[0]
        light = html.split("body.light-theme{", 1)[1].split("}", 1)[0]
    for token in ("--status-green", "--status-amber", "--status-red", "--status-grey"):
        assert token in dark
        assert token in light
    assert "#2dd4bf" in dark and "#0f7a5c" in light
    assert "#fbbf24" in dark and "#9a6700" in light
    assert "#fb7185" in dark and "#b42318" in light
    for cls in ("hc-status-green", "hc-status-amber", "hc-status-red", "hc-status-grey"):
        assert cls in html
    assert "hc-metric-status" in html
    assert ('id="hc_theme_toggle"' in html) or ('id="theme_toggle_btn"' in html)


def test_card_navigation_reuses_vault_detail():
    snap = (ROOT / "js" / "health_vault" / "health_snapshot.js").read_text(encoding="utf-8")
    ui = (ROOT / "js" / "health_vault" / "ui.js").read_text(encoding="utf-8")
    surfaces = (ROOT / "js" / "health_vault" / "consumer_surfaces.js").read_text(encoding="utf-8")
    html = (ROOT / "index.html").read_text(encoding="utf-8")
    assert "function openMetricDetail" in snap
    assert "closeDrillDown" in snap
    assert "hc-drill-back" in snap
    assert "data-open-filtered" in snap
    assert "data-category=" in snap
    assert "data-detail-metric=" in snap
    assert "function openMetricDetail" in ui
    assert "setMetricFilter" in ui
    assert "openFiltered" in surfaces
    assert ('.tab[data="vault"]' in ui) or ("health_records_screen" in ui) or ("health_records_screen" in snap)
    assert "setCategoryFilter" in ui
    assert ('id="vault_trends"' in html) or ('id="consumer_trends_screen"' in html)
    assert ('id="vault_timeline"' in html) or ('id="consumer_timeline_screen"' in html)
    assert 'data="dash"' in html
    assert ('data="rep"' in html) or ('data="health_records_screen"' in html)


def test_no_flag_secure_set_anywhere_in_android_sources():
    """Companion must not globally set FLAG_SECURE; selective SecureWindowPolicy is allowed."""
    android = ROOT / "android"
    if not android.exists():
        return
    allowed = {
        "SecureWindowPolicy.kt",
        "ConsumerLauncherActivity.kt",
        "ScreenshotPolicy.kt",
    }
    setters = []
    for path in android.rglob("*"):
        if path.suffix.lower() not in {".kt", ".java", ".xml"}:
            continue
        if path.name in allowed:
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


def test_uat10_snapshot_mount_near_top_of_authenticated_dashboard():
    """HC321-UAT10: Health Snapshot mounts inside the authenticated Welcome container near the top."""
    html = (ROOT / "index.html").read_text(encoding="utf-8")
    assert 'id="consumer_dashboard_container"' in html
    assert 'id="hc_health_snapshot"' in html
    consumer = html.split('id="consumer_dashboard_container"', 1)[1]
    # Mount must live inside the authenticated container, before widget target and executive.
    assert 'id="hc_health_snapshot"' in consumer.split('id="dashboard_widgets_target"', 1)[0]
    assert html.index('id="hc_health_snapshot"') < html.index('id="dashboard_widgets_target"')
    assert html.index('id="hc_health_snapshot"') < html.index('id="exec_health_dashboard"')
    assert "health_snapshot.js?v=hc321uat12" in html
    sw = (ROOT / "service-worker.js").read_text(encoding="utf-8")
    assert 'CACHE_REVISION = "hc321uat12g"' in sw


def test_uat10_authenticated_dashboard_snapshot_render_path():
    """Authenticated Dashboard load → snapshot API → non-empty cards → HealthMetricCard markup."""
    pytest.importorskip("fastapi")
    from datetime import datetime, timedelta, timezone

    from fastapi.testclient import TestClient

    from backend.health_vault.api import create_health_vault_app

    with tempfile.TemporaryDirectory() as td:
        store = VaultStore(root=Path(td), encryption_key=b"U" * 32)
        app = create_health_vault_app(
            store=store,
            production=True,
            bootstrap_password="Boot-Pass-UAT10xx",
        )
        client = TestClient(app)
        login = client.post(
            "/api/auth/login",
            json={"patient_id": "00000", "password": "Boot-Pass-UAT10xx"},
        )
        assert login.status_code == 200
        token = login.json()["token"]
        if login.json().get("must_change_password"):
            changed = client.post(
                "/api/auth/password/change",
                headers={"Authorization": f"Bearer {token}"},
                json={
                    "current_password": "Boot-Pass-UAT10xx",
                    "new_password": "Owner-UAT10-Password1",
                },
            )
            assert changed.status_code == 200
            token = changed.json()["token"]
        headers = {"Authorization": f"Bearer {token}"}

        now = datetime.now(timezone.utc)
        for i, (metric, value, unit) in enumerate(
            [
                ("heart_rate", 72, "bpm"),
                ("oxygen_saturation", 97, "%"),
                ("steps", 5400, "count"),
                ("sleep_duration", 420, "min"),
                ("activity_minutes", 35, "min"),
            ]
        ):
            store.upsert_observation(
                {
                    "patient_id": "00000",
                    "metric_type": metric,
                    "value": value,
                    "unit": unit,
                    "measured_at": (now - timedelta(minutes=i))
                    .isoformat()
                    .replace("+00:00", "Z"),
                    "acquisition_mode": "HEALTH_CONNECT",
                    "fingerprint": f"uat10-live-{metric}",
                }
            )

        summary = client.get("/api/dashboard/summary", headers=headers)
        assert summary.status_code == 200
        snap = client.get("/api/health-vault/health-snapshot", headers=headers)
        assert snap.status_code == 200
        body = snap.json()
        assert body["card_count"] > 0
        metric_ids = [c["metric_id"] for c in body["cards"]]
        # Capture metric names only — no PHI values asserted beyond presence.
        assert "heart_rate" in metric_ids
        assert "oxygen_saturation" in metric_ids
        assert "steps" in metric_ids

        slim = []
        for c in body["cards"]:
            slim.append(
                {
                    k: c.get(k)
                    for k in (
                        "metric_id",
                        "title",
                        "display_value",
                        "unit",
                        "status",
                        "status_text",
                        "status_color",
                        "freshness_label",
                        "detail_category",
                        "detail_metric",
                        "accessibility_label",
                    )
                }
            )
        cards_json = __import__("json").dumps(slim)
        out = _node_snapshot_eval(
            f"""
const cards = {cards_json};
const root = {{ innerHTML: '', querySelectorAll() {{ return []; }}, addEventListener() {{}} }};
HS.renderInto(root, cards);
console.log(JSON.stringify({{
  has_section: root.innerHTML.includes('Health Snapshot'),
  rendered_cards: (root.innerHTML.match(/hc-metric-card/g) || []).length,
  api_cards: cards.length,
}}));
"""
        )
        payload = __import__("json").loads(out)
        assert payload["has_section"] is True
        assert payload["api_cards"] > 0
        assert payload["rendered_cards"] == payload["api_cards"]


def test_uat10_js_refresh_renders_health_metric_cards():
    cards = [
        {
            "metric_id": "heart_rate",
            "title": "Heart Rate",
            "display_value": "72",
            "unit": "bpm",
            "status": "NORMAL",
            "status_text": "Normal",
            "status_color": "GREEN",
            "freshness_label": "Updated 5 minutes ago",
            "detail_category": "ecg_cardiology",
            "detail_metric": "heart_rate",
            "accessibility_label": "Heart Rate, 72 bpm, status Normal.",
        },
        {
            "metric_id": "steps",
            "title": "Steps",
            "display_value": "5400",
            "unit": "steps",
            "status": "UNKNOWN",
            "status_text": "Unknown",
            "status_color": "GREY",
            "freshness_label": "Updated today",
            "detail_category": "other",
            "detail_metric": "steps",
            "accessibility_label": "Steps, 5400, status Unknown.",
        },
    ]
    out = _node_snapshot_eval(
        f"""
const cards = {__import__("json").dumps(cards)};
const root = {{ innerHTML: '', querySelectorAll() {{ return []; }}, addEventListener() {{}} }};
HS.renderInto(root, cards);
console.log(JSON.stringify({{
  has_section: root.innerHTML.includes('Health Snapshot'),
  rendered_cards: (root.innerHTML.match(/hc-metric-card/g) || []).length,
}}));
"""
    )
    payload = __import__("json").loads(out)
    assert payload["has_section"] is True
    assert payload["rendered_cards"] == 2


def test_uat11_activity_exercise_minutes_deduped():
    observations = [
        _obs("activity_minutes", 34, AS_OF - timedelta(hours=2), units="min"),
        _obs("exercise_minutes", 34, AS_OF - timedelta(hours=2), units="min"),
    ]
    snap = HealthSnapshotEngine().generate(observations=observations, as_of=AS_OF)
    ids = [c["metric_id"] for c in snap["cards"]]
    assert ids.count("activity_minutes") == 1
    assert "exercise_minutes" not in ids
    card = next(c for c in snap["cards"] if c["metric_id"] == "activity_minutes")
    assert card["title"] == "Activity"


def test_uat11_stale_sleep_unknown_fresh_abnormal_attention():
    stale = HealthSnapshotEngine().generate(
        observations=[_obs("sleep_duration", 1.2, AS_OF - timedelta(days=3), units="h")],
        as_of=AS_OF,
    )
    stale_card = stale["cards"][0]
    assert stale_card["currentness"] == "stale"
    assert stale_card["status"] == STATUS_UNKNOWN
    assert stale_card["status_color"] == COLOR_GREY
    assert "not current" in stale_card["freshness_label"]
    assert stale_card["historical_status"] == STATUS_ATTENTION
    assert stale_card["historical_status_color"] == COLOR_RED

    fresh = HealthSnapshotEngine().generate(
        observations=[_obs("sleep_duration", 1.2, AS_OF - timedelta(hours=8), units="h")],
        as_of=AS_OF,
    )
    fresh_card = fresh["cards"][0]
    assert fresh_card["currentness"] == "current"
    assert fresh_card["status"] == STATUS_ATTENTION
    assert fresh_card["status_color"] == COLOR_RED


def test_uat11_metric_detail_history_and_filter_identity():
    observations = [
        _obs("heart_rate", 80, AS_OF - timedelta(hours=2), units="bpm"),
        _obs("heart_rate", 72, AS_OF - timedelta(minutes=20), units="bpm"),
        _obs("heart_rate", 76, AS_OF - timedelta(minutes=5), units="bpm"),
        _obs("steps", 1000, AS_OF - timedelta(hours=1)),
    ]
    detail = HealthSnapshotEngine().metric_detail(
        "heart_rate", observations=observations, as_of=AS_OF
    )
    assert detail["found"] is True
    assert detail["card"]["metric_id"] == "heart_rate"
    assert len(detail["history"]) >= 3
    assert detail["history"][0]["value"] == 76  # newest first
    assert detail["stats"]["sample_count"] >= 3
    assert "heart_rate" in detail["filter_metrics"]


def test_uat11_compact_mobile_css_and_status_text():
    html = (ROOT / "index.html").read_text(encoding="utf-8")
    assert "@media (max-width:719px)" in html
    assert ".hc-metric-card{padding:8px 10px" in html.replace(" ", "") or "padding:8px 10px" in html
    assert "hc-metric-status" in html
    dash = (ROOT / "js" / "health_vault" / "dashboard.js").read_text(encoding="utf-8")
    assert "Data freshness" in dash
    assert "Health Connect connection" in dash
    assert "SYNCED" not in dash or "Data freshness" in dash
    snap = (ROOT / "js" / "health_vault" / "health_snapshot.js").read_text(encoding="utf-8")
    assert "hc-drill-back" in snap
    assert "metric=" in snap
    assert "closeDrillDown" in snap


def test_uat11_js_aging_is_not_current_and_dedupe_layout():
    out = _node_snapshot_eval(
        """
const aging = HS.computeFreshness('sleep_duration', '2020-01-01T00:00:00Z', Date.parse('2020-01-04T00:00:00Z'));
const cards = HS.applyLayout([
  {metric_id:'exercise_minutes', title:'Exercise Minutes', display_value:'34'},
  {metric_id:'activity_minutes', title:'Activity', display_value:'34'},
], {order:['activity_minutes','exercise_minutes'], hidden:[]}, HS.DEFAULT_ORDER);
console.log(JSON.stringify({
  aging_currentness: aging.currentness,
  aging_label: aging.label,
  ids: cards.map(c => c.metric_id),
  title: cards[0] && cards[0].title,
}));
"""
    )
    payload = __import__("json").loads(out)
    assert payload["aging_currentness"] == "stale"
    assert "not current" in payload["aging_label"]
    assert payload["ids"] == ["activity_minutes"]
    assert payload["title"] == "Activity"


def test_uat12_client_normalizes_stale_sleep_attention_to_unknown():
    """Defense-in-depth: even if API returns Attention for stale Sleep, client paints UNKNOWN/GREY."""
    out = _node_snapshot_eval(
        """
const asOf = Date.parse('2026-08-21T12:00:00Z');
const card = HS.normalizeSnapshotCard({
  metric_id: 'sleep_duration',
  title: 'Sleep',
  display_value: '1.2',
  unit: 'h',
  status: 'ATTENTION',
  status_text: 'Attention',
  status_color: 'RED',
  measured_at: '2026-08-18T12:00:00Z',
  currentness: 'current',
  freshness_label: 'Updated 3 days ago',
}, asOf);
console.log(JSON.stringify({
  status: card.status,
  color: card.status_color,
  currentness: card.currentness,
  label: card.freshness_label,
  historical: card.historical_status,
}));
"""
    )
    payload = __import__("json").loads(out)
    assert payload["status"] == "UNKNOWN"
    assert payload["color"] == "GREY"
    assert payload["currentness"] == "stale"
    assert "not current" in payload["label"]
    assert payload["label"].startswith("Last recorded")
    assert payload["historical"] == "ATTENTION"


def test_uat12_dashboard_trends_and_timeline_consumer_polish():
    dash = (ROOT / "js" / "health_vault" / "dashboard.js").read_text(encoding="utf-8")
    assert "Not current" in dash
    assert "consumerMetricTitle" in dash or 'return "Activity"' in dash
    assert "exercise_minutes" in dash and "Activity" in dash
    assert "similar updates" in dash or "Health Connect observations" in dash
    assert "Category: ${" not in dash or "if (cat)" in dash
    assert "data-open-full-timeline" in dash
    assert "Data freshness" in dash
    html = (ROOT / "index.html").read_text(encoding="utf-8")
    assert "exec-nav-bar.vault-sticky-actions" in html
    assert "position:static" in html.replace(" ", "") or "position:static" in html
    snap = (ROOT / "js" / "health_vault" / "health_snapshot.js").read_text(encoding="utf-8")
    assert "normalizeSnapshotCard" in snap
    assert "keydown" in snap
    assert "hc-drill-back" in snap
    assert 'CACHE_REVISION = "hc321uat12g"' in (ROOT / "service-worker.js").read_text(encoding="utf-8")


def _assert_json_not_html(response):
    ctype = str(response.headers.get("content-type") or "").lower()
    assert "json" in ctype
    assert "html" not in ctype
    text = response.text.lstrip()
    assert text
    assert not text.startswith("<")
    assert "<html" not in text[:240].lower()
    return response.json()


def _uat12e_login(client, password="Boot-Pass-UAT12Exx"):
    login = client.post("/api/auth/login", json={"user_id": "00000", "password": password})
    assert login.status_code == 200, login.text
    token = login.json()["token"]
    if login.json().get("must_change_password"):
        changed = client.post(
            "/api/auth/password/change",
            headers={"Authorization": f"Bearer {token}"},
            json={"current_password": password, "new_password": "Owner-UAT12E-Password1"},
        )
        assert changed.status_code == 200, changed.text
        token = changed.json()["token"]
    return {"Authorization": f"Bearer {token}"}


def test_uat12e_history_dedupes_duplicate_hr_samples_without_deleting_store():
    t_76 = "2026-08-21T14:50:45Z"
    t_65 = "2026-08-21T14:40:45Z"
    t_72 = "2026-08-21T14:30:45Z"
    observations = [
        _obs("heart_rate", 76, t_76, units="bpm", source="health_connect"),
        _obs("heart_rate", 76, "2026-08-21T14:50:45.000Z", units="bpm", source="health_connect_companion"),
        _obs("heart_rate", 65, t_65, units="bpm", fingerprint="same-fp-65"),
        _obs("heart_rate", 65, t_65, units="bpm", fingerprint="same-fp-65"),
        _obs("heart_rate", 72, t_72, units="bpm", observation_id="distinct-72"),
        _obs("heart_rate", 74, "2026-08-21T14:20:45Z", units="bpm", observation_id="distinct-74"),
    ]
    with tempfile.TemporaryDirectory() as td:
        store = VaultStore(root=Path(td), encryption_key=b"U" * 32)
        for i, row in enumerate(observations):
            store.upsert_observation(
                {
                    "patient_id": "default-patient",
                    "metric_type": row["metric"],
                    "value": row["value"],
                    "unit": row.get("units"),
                    "measured_at": row["measured_at"],
                    "source": row.get("source") or "fixture",
                    "fingerprint": f"stored-{i}",
                    "observation_id": f"stored-obs-{i}",
                }
            )
        before = len(store.list_observations() or [])
        assert before == 6
        assert observation_display_identity(observations[2]).startswith("fp:")
        assert fallback_observation_identity(observations[0]).startswith("fallback:")
        assert len(dedupe_observation_history(observations)) == 4
        detail = HealthSnapshotEngine(store).metric_detail(
            "heart_rate", observations=observations, as_of=AS_OF
        )
        values = [row["value"] for row in detail["history"]]
        assert values.count(76) == 1
        assert values.count(65) == 1
        assert 72 in values
        assert 74 in values
        assert detail["stats"]["sample_count"] == 4
        assert detail["stats"]["maximum"] == 76
        assert detail["stats"]["minimum"] == 65
        assert len(store.list_observations() or []) == before


def test_uat12e_js_history_dedupes_duplicate_hr_samples():
    out = _node_snapshot_eval(
        """
const rows = [
  {metric:'heart_rate', value:76, units:'bpm', measured_at:'2026-08-21T14:50:45Z'},
  {metric:'heart_rate', value:76, units:'bpm', measured_at:'2026-08-21T14:50:45.000Z'},
  {metric:'heart_rate', value:65, units:'bpm', measured_at:'2026-08-21T14:40:45Z', fingerprint:'same-fp-65'},
  {metric:'heart_rate', value:65, units:'bpm', measured_at:'2026-08-21T14:40:45Z', fingerprint:'same-fp-65'},
  {metric:'heart_rate', value:72, units:'bpm', measured_at:'2026-08-21T14:30:45Z', observation_id:'distinct-72'},
  {metric:'heart_rate', value:74, units:'bpm', measured_at:'2026-08-21T14:20:45Z', observation_id:'distinct-74'},
];
const summary = HS.summarizeHistory(rows, 'heart_rate');
console.log(JSON.stringify({
  values: summary.history.map(r => r.value),
  samples: summary.stats.sample_count,
  min: summary.stats.minimum,
  max: summary.stats.maximum,
}));
"""
    )
    payload = __import__("json").loads(out)
    assert payload["values"].count(76) == 1
    assert payload["values"].count(65) == 1
    assert 72 in payload["values"]
    assert 74 in payload["values"]
    assert payload["samples"] == 4
    assert payload["min"] == 65
    assert payload["max"] == 76


def test_uat12e_filtered_surfaces_authenticated_json_never_html():
    pytest.importorskip("fastapi")
    from fastapi.testclient import TestClient

    from backend.health_vault.api import create_health_vault_app

    with tempfile.TemporaryDirectory() as td:
        store = VaultStore(root=Path(td), encryption_key=b"U" * 32)
        app = create_health_vault_app(
            store=store,
            production=True,
            bootstrap_password="Boot-Pass-UAT12Exx",
        )
        client = TestClient(app)
        html_root = client.get("/")
        assert html_root.status_code == 200
        assert "html" in str(html_root.headers.get("content-type") or "").lower()

        for path in (
            "/api/health-vault/timeline?unified=true&metric=heart_rate",
            "/api/records?metric=heart_rate",
            "/api/health-vault/trends?metric=heart_rate",
        ):
            unauth = client.get(path)
            assert unauth.status_code in (401, 403)
            _assert_json_not_html(unauth)

        headers = _uat12e_login(client)
        hr_doc = MedicalDocument(
            id="doc-hr",
            patient_id="00000",
            document_type="continuous_monitoring_observation",
            source_system="health_connect_companion",
            original_filename="heart-rate.json",
            measured_at="2026-08-21T14:50:45Z",
            primary_category="other",
            sha256="a" * 64,
        )
        steps_doc = MedicalDocument(
            id="doc-steps",
            patient_id="00000",
            document_type="continuous_monitoring_observation",
            source_system="health_connect_companion",
            original_filename="steps.json",
            measured_at="2026-08-21T14:40:45Z",
            primary_category="other",
            sha256="b" * 64,
        )
        store.store(
            document=hr_doc,
            measurements=[
                Measurement(
                    metric="heart_rate",
                    value=76,
                    units="bpm",
                    measured_at="2026-08-21T14:50:45Z",
                )
            ],
            content=b"hr",
        )
        store.store(
            document=steps_doc,
            measurements=[
                Measurement(
                    metric="steps",
                    value=5400,
                    units="count",
                    measured_at="2026-08-21T14:40:45Z",
                )
            ],
            content=b"steps",
        )
        for i, (metric, value) in enumerate(
            (("heart_rate", 70), ("heart_rate", 72), ("heart_rate", 76), ("steps", 1000), ("steps", 2000), ("steps", 5400))
        ):
            store.upsert_observation(
                {
                    "patient_id": "00000",
                    "metric_type": metric,
                    "value": value,
                    "unit": "bpm" if metric == "heart_rate" else "count",
                    "measured_at": f"2026-08-21T14:{30 + i:02d}:45Z",
                    "source": "health_connect_companion",
                    "connector_id": "health_connect",
                    "fingerprint": f"uat12e-{metric}-{i}",
                    "observation_id": f"uat12e-obs-{metric}-{i}",
                }
            )

        timeline = client.get(
            "/api/health-vault/timeline?unified=true&metric=heart_rate",
            headers=headers,
        )
        timeline_body = _assert_json_not_html(timeline)
        assert timeline.status_code == 200
        assert isinstance(timeline_body.get("entries"), list)
        assert timeline_body.get("filter_metric") == "heart_rate"
        blob = __import__("json").dumps(timeline_body).lower()
        assert "heart_rate" in blob
        assert all(
            "steps" not in __import__("json").dumps(entry).lower()
            or "heart_rate" in __import__("json").dumps(entry).lower()
            for entry in timeline_body["entries"]
        )

        records = client.get("/api/records?metric=heart_rate", headers=headers)
        records_body = _assert_json_not_html(records)
        assert records.status_code == 200
        names = [row.get("original_filename") for row in records_body["records"]]
        assert "heart-rate.json" in names
        assert "steps.json" not in names

        trends = client.get("/api/health-vault/trends?metric=heart_rate", headers=headers)
        trends_body = _assert_json_not_html(trends)
        assert trends.status_code == 200
        assert set(trends_body.get("trends") or {}) == {"heart_rate"}
        assert "steps" not in (trends_body.get("trends") or {})

        asset = client.get("/js/health_vault/trends.js")
        assert asset.status_code == 200
        asset_ct = str(asset.headers.get("content-type") or "").lower()
        assert "javascript" in asset_ct or "ecmascript" in asset_ct or asset_ct.startswith("text/plain")
        assert "html" not in asset_ct
        assert not asset.text.lstrip().startswith("<")
        assert "HCConsumerTrends" in asset.text
        assert "/api/health-vault/trends" in asset.text

        html = (ROOT / "index.html").read_text(encoding="utf-8")
        surfaces = (ROOT / "js" / "health_vault" / "consumer_surfaces.js").read_text(encoding="utf-8")
        records_js = (ROOT / "js" / "health_vault" / "records.js").read_text(encoding="utf-8")
        sw = (ROOT / "service-worker.js").read_text(encoding="utf-8")
        assert "js/health_vault/trends.js" in html
        assert "Accept: \"application/json\"" in surfaces or "Accept: 'application/json'" in surfaces or 'Accept: "application/json"' in surfaces
        assert "parseJsonResponse" in surfaces
        assert "setMetricFilter" in records_js
        assert "HCRecordsUI.setMetricFilter" in surfaces
        assert "./js/health_vault/trends.js" in sw
        assert "isNavigationRequest(req)" in sw
        assert "network_failed" in sw


def test_uat12f_s24_filtered_timeline_fetch_contract_matches_snapshot_auth():
    """Reproduce the exact S24 Heart Rate → Filtered Timeline browser request.

    Snapshot (working) uses GET /api/health-vault/health-snapshot?metric=heart_rate
    with Authorization + cache:no-store. Timeline (failing) used the same Bearer
    token plus Accept: application/json against
    GET /api/health-vault/timeline?unified=true&metric=heart_rate.
    The UI error was fetch()'s TypeError message "Failed to fetch" — a dropped
    connection — not the UAT12E HTML-shell parse error and not a 401 JSON body.
    """
    surfaces = (ROOT / "js" / "health_vault" / "consumer_surfaces.js").read_text(encoding="utf-8")
    snap = (ROOT / "js" / "health_vault" / "health_snapshot.js").read_text(encoding="utf-8")
    dash = (ROOT / "js" / "health_vault" / "dashboard.js").read_text(encoding="utf-8")
    sw = (ROOT / "service-worker.js").read_text(encoding="utf-8")
    html = (ROOT / "index.html").read_text(encoding="utf-8")
    timeline_js = (ROOT / "js" / "health_vault" / "timeline.js").read_text(encoding="utf-8")

    assert 'data-open-filtered="timeline"' in snap
    assert "openFilteredSurface" in snap
    assert "HCConsumerSurfaces.openFiltered" in snap
    assert "/api/health-vault/timeline?" in surfaces
    assert 'unified: "true"' in surfaces or "unified: 'true'" in surfaces
    assert 'params.set("metric"' in surfaces
    assert 'Accept: "application/json"' in surfaces
    assert 'cache: "no-store"' in surfaces
    assert 'credentials: "same-origin"' in surfaces
    assert "getAuthorizationHeaders" in surfaces
    assert 'Authorization": `Bearer ${this.token}`' in dash or "Authorization" in dash
    assert "/api/health-vault/health-snapshot?metric=" in snap
    assert 'cache: "no-store"' in snap
    assert "/api/" not in timeline_js
    assert "isForbiddenCacheUrl(req.url)) return" in sw.replace("\n", "") or "isForbiddenCacheUrl(req.url)) return" in sw
    assert "if (isForbiddenCacheUrl(req.url)) return;" in sw
    fetch_handler = sw.split('self.addEventListener("fetch"', 1)[1]
    api_return = fetch_handler.find("if (isForbiddenCacheUrl(req.url)) return;")
    first_respond = fetch_handler.find("event.respondWith")
    assert api_return >= 0
    assert first_respond > api_return
    assert lastNav_before_click_prevents_double_fetch(surfaces)
    assert 'CACHE_REVISION = "hc321uat12g"' in sw
    assert "consumer_surfaces.js?v=hc321uat12g" in html
    assert "service-worker.js?v=hc321uat12g" in html


def lastNav_before_click_prevents_double_fetch(surfaces: str) -> bool:
    open_fn = surfaces.split("function openFiltered", 1)[1]
    last_nav = open_fn.find("lastNavScreen = screenId")
    click = open_fn.find("tab.click()")
    second_load = open_fn.find("loadTimeline(metricFilter)")
    return 0 <= last_nav < click < second_load


def test_uat12f_consumer_projection_cannot_be_parsed_as_html_shell():
    import json

    from backend.health_vault.timeline import project_consumer_timeline_entry

    fat = {
        "date": "2026-08-21T14:50:45Z",
        "summary": "Heart rate 76 bpm",
        "primary_category": "other",
        "trend_impact": "heart_rate: stable",
        "document": {
            "id": "doc-hr",
            "original_filename": "heart-rate.json",
            "extracted_text": "<html><body>app shell</body></html>" * 40,
            "ocr_text": "x" * 8000,
        },
        "measurements": [{"metric": "heart_rate", "value": 76, "units": "bpm"}],
        "fhir_resources": {"document": "DocumentReference"},
        "payload": {"metric": "heart_rate", "raw_blob": "<!doctype html>"},
    }
    slim = project_consumer_timeline_entry(fat)
    blob = json.dumps(slim).lower()
    assert "extracted_text" not in blob
    assert "ocr_text" not in blob
    assert "<html" not in blob
    assert "<!doctype" not in blob
    assert "heart_rate" in blob
    assert slim["document"]["original_filename"] == "heart-rate.json"


def test_uat12f_s24_authenticated_filtered_timeline_avoids_n_plus_one_and_failed_to_fetch():
    """The S24 blocker: authenticated GET timeline?unified=true&metric=heart_rate.

    Before UAT12F, build_timeline called list_measurements(document_id=...) once
    per vault document. Each call decrypts the whole encrypted index, so a live
    Health Connect vault (thousands of rows) stalled past the Cloudflare/S24
    fetch deadline and the browser surfaced TypeError: Failed to fetch.
    Snapshot/Records/Trends stayed small and succeeded with the same Bearer token.
    """
    pytest.importorskip("fastapi")
    from fastapi.testclient import TestClient

    from backend.health_vault.api import create_health_vault_app
    from backend.health_vault.timeline import build_unified_timeline

    with tempfile.TemporaryDirectory() as td:
        store = VaultStore(root=Path(td), encryption_key=b"U" * 32)
        app = create_health_vault_app(
            store=store,
            production=True,
            bootstrap_password="Boot-Pass-UAT12Exx",
        )
        n_docs = 48
        for i in range(n_docs):
            metric = "heart_rate" if i % 3 == 0 else "steps"
            store.store(
                document=MedicalDocument(
                    id=f"uat12f-doc-{i}",
                    patient_id="00000",
                    document_type="continuous_monitoring_observation",
                    source_system="health_connect_companion",
                    original_filename=f"{metric}-{i}.json",
                    measured_at=f"2026-08-21T14:{i % 60:02d}:{i % 60:02d}Z",
                    primary_category="other",
                    sha256=f"{i:064d}"[:64],
                ),
                measurements=[
                    Measurement(
                        metric=metric,
                        value=60 + i,
                        units="bpm" if metric == "heart_rate" else "count",
                        measured_at=f"2026-08-21T14:{i % 60:02d}:{i % 60:02d}Z",
                    )
                ],
                content=("extracted-text-shell <html>not-json</html> " + str(i)).encode("utf-8"),
            )
        for i, (metric, value) in enumerate(
            (("heart_rate", 70), ("heart_rate", 72), ("heart_rate", 76), ("steps", 1000), ("steps", 2000), ("steps", 5400))
        ):
            store.upsert_observation(
                {
                    "patient_id": "00000",
                    "metric_type": metric,
                    "value": value,
                    "unit": "bpm" if metric == "heart_rate" else "count",
                    "measured_at": f"2026-08-21T13:{30 + i:02d}:45Z",
                    "source": "health_connect_companion",
                    "connector_id": "health_connect",
                    "fingerprint": f"uat12f-{metric}-{i}",
                    "observation_id": f"uat12f-obs-{metric}-{i}",
                }
            )

        measurement_calls: list[dict] = []
        original_list = store.list_measurements

        def _counting_list_measurements(**filters):
            measurement_calls.append(dict(filters))
            return original_list(**filters)

        store.list_measurements = _counting_list_measurements  # type: ignore[method-assign]

        client = TestClient(app)
        browser_headers = {
            **_uat12e_login(client),
            "Accept": "application/json",
        }

        measurement_calls.clear()
        unfiltered = client.get(
            "/api/health-vault/timeline?unified=true",
            headers=browser_headers,
        )
        unfiltered_body = _assert_json_not_html(unfiltered)
        assert unfiltered.status_code == 200
        assert unfiltered.headers.get("content-type", "").lower().startswith("application/json")
        assert isinstance(unfiltered_body.get("entries"), list)
        assert unfiltered_body.get("entries")
        assert len(measurement_calls) == 1
        assert "document_id" not in measurement_calls[0]

        measurement_calls.clear()
        filtered = client.get(
            "/api/health-vault/timeline?unified=true&metric=heart_rate",
            headers=browser_headers,
        )
        body = _assert_json_not_html(filtered)
        assert filtered.status_code == 200
        assert filtered.headers.get("content-type", "").lower().startswith("application/json")
        assert body.get("filter_metric") == "heart_rate"
        assert len(measurement_calls) == 1
        assert "document_id" not in measurement_calls[0]
        assert body.get("entries")
        blob = __import__("json").dumps(body).lower()
        assert "heart_rate" in blob
        assert "<html" not in blob
        assert "extracted-text-shell" not in blob
        assert "failed to fetch" not in blob
        for entry in body["entries"]:
            entry_blob = __import__("json").dumps(entry).lower()
            assert "heart_rate" in entry_blob
            assert "extracted_text" not in entry_blob
            doc = entry.get("document") or {}
            assert "extracted_text" not in doc
            assert "ocr_text" not in doc

        snapshot = client.get(
            "/api/health-vault/health-snapshot?metric=heart_rate",
            headers={"Authorization": browser_headers["Authorization"]},
        )
        snap_body = _assert_json_not_html(snapshot)
        assert snapshot.status_code == 200
        assert snap_body.get("metric_id") == "heart_rate"
        assert isinstance(snap_body.get("history"), list)

        records = client.get("/api/records?metric=heart_rate", headers=browser_headers)
        records_body = _assert_json_not_html(records)
        assert records.status_code == 200
        names = [row.get("original_filename") for row in records_body["records"]]
        assert any(str(name).startswith("heart_rate-") for name in names)
        assert not any(str(name).startswith("steps-") for name in names)

        trends = client.get("/api/health-vault/trends?metric=heart_rate", headers=browser_headers)
        trends_body = _assert_json_not_html(trends)
        assert trends.status_code == 200
        assert "heart_rate" in (trends_body.get("trends") or {})
        assert "steps" not in (trends_body.get("trends") or {})

        stored_before = len(store.list_documents())
        _ = build_unified_timeline(store, patient_id="00000")
        assert len(store.list_documents()) == stored_before
        assert len(store.list_measurements()) >= n_docs


def _node_surfaces_eval(script: str) -> str:
    import subprocess

    prelude = f"""
const fs = require('fs');
const vm = require('vm');
const documentStub = {{
  readyState: 'complete',
  getElementById: function () {{ return null; }},
  querySelectorAll: function () {{ return []; }},
  querySelector: function () {{ return null; }},
  addEventListener: function () {{}},
  createElement: function (name) {{
    return {{
      tagName: String(name || '').toUpperCase(),
      className: '',
      textContent: '',
      childNodes: [],
      setAttribute: function () {{}},
      appendChild: function (node) {{ this.childNodes.push(node); return node; }},
    }};
  }},
}};
const ctx = {{
  console: console,
  document: documentStub,
  window: {{}},
  fetch: function () {{ return Promise.reject(new Error('fetch_unused')); }},
}};
ctx.window = ctx;
ctx.globalThis = ctx;
vm.createContext(ctx);
vm.runInContext(fs.readFileSync({str(ROOT / "js/health_vault/consumer_surfaces.js")!r}, 'utf8'), ctx);
const CS = ctx.HCConsumerSurfaces;
{script}
"""
    proc = subprocess.run(["node", "-e", prelude], capture_output=True, text=True, check=False)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    return proc.stdout.strip()


def test_uat12g_display_compacts_same_day_heart_rate_health_connect_rows():
    out = _node_surfaces_eval(
        """
const day = '2026-08-18';
const values = [56,58,60,62,64,65,66,67,68,69,70,71,72,73,74,75,76,76,77,78];
const events = values.map((value, i) => ({
  date: day + 'T12:' + String(i).padStart(2,'0') + ':00Z',
  measured_at: day + 'T12:' + String(i).padStart(2,'0') + ':00Z',
  primary_category: 'other',
  entry_kind: 'document',
  provenance: 'health_connect_companion',
  document: {
    document_type: 'continuous_monitoring_observation',
    source_system: 'health_connect_companion',
    primary_category: 'other',
  },
  measurements: [{metric:'heart_rate', value, units:'bpm', measured_at: day + 'T12:' + String(i).padStart(2,'0') + ':00Z'}],
}));
events.push({
  date: day + 'T18:00:00Z',
  measured_at: day + 'T18:00:00Z',
  primary_category: 'other',
  entry_kind: 'document',
  provenance: 'health_connect_companion',
  document: { document_type: 'continuous_monitoring_observation', source_system: 'health_connect_companion' },
  measurements: [{metric:'steps', value:5400, units:'count', measured_at: day + 'T18:00:00Z'}],
});
const groups = CS.compactTimelineEntries(events);
const hr = groups.find(g => g.kind === 'group' && g.metric === 'heart_rate');
const steps = groups.find(g => g.kind === 'group' && g.metric === 'steps');
const lines = CS.groupedCardLines(hr);
const cat = CS.categoryLine(events[0]);
const eventLines = CS.eventCardLines(events[0]);
console.log(JSON.stringify({
  groupCount: groups.length,
  hrCount: hr && hr.underlyingCount,
  hrLatest: hr && hr.latest && hr.latest.value,
  hrMin: hr && hr.min,
  hrMax: hr && hr.max,
  hrDay: hr && hr.day,
  stepsCount: steps && steps.underlyingCount,
  label: CS.consumerMetricLabel('heart_rate'),
  sleep: CS.consumerMetricLabel('sleep_duration'),
  activity: CS.consumerMetricLabel('exercise_minutes'),
  spo2: CS.consumerMetricLabel('oxygen_saturation'),
  lines: lines,
  categoryLine: cat,
  eventLines: eventLines,
  storedEvents: events.length,
}));
"""
    )
    payload = __import__("json").loads(out)
    assert payload["groupCount"] == 2
    assert payload["hrCount"] == 20
    assert payload["hrLatest"] == 78
    assert payload["hrMin"] == 56
    assert payload["hrMax"] == 78
    assert payload["hrDay"] == "2026-08-18"
    assert payload["stepsCount"] == 1
    assert payload["label"] == "Heart Rate"
    assert payload["sleep"] == "Sleep"
    assert payload["activity"] == "Activity"
    assert payload["spo2"] == "Oxygen Saturation"
    assert payload["lines"][0] == "Heart Rate"
    assert "20 observations" in payload["lines"]
    assert "Latest: 78 bpm" in payload["lines"]
    assert any(line.startswith("Range: 56–78") for line in payload["lines"])
    assert any(line.startswith("Source:") for line in payload["lines"])
    assert payload["categoryLine"] == ""
    assert "Category: Not available" not in payload["eventLines"]
    assert "Not available" not in payload["eventLines"]
    assert payload["storedEvents"] == 21


def test_uat12g_never_renders_category_not_available():
    surfaces = (ROOT / "js" / "health_vault" / "consumer_surfaces.js").read_text(encoding="utf-8")
    assert "Category: ${" not in surfaces
    assert 'Category: Not available' not in surfaces
    assert "compactTimelineEntries" in surfaces
    assert "Show observations" in surfaces
    assert "1 observation" in surfaces
    out = _node_surfaces_eval(
        """
const missing = {date:'2026-08-18', primary_category:'', summary:'Imported note'};
const other = {date:'2026-08-18', primary_category:'other', summary:'HC row'};
const lab = {date:'2026-08-18', primary_category:'laboratory_report', summary:'Lab PDF'};
console.log(JSON.stringify({
  missing: CS.categoryLine(missing),
  other: CS.categoryLine(other),
  lab: CS.categoryLine(lab),
  missingLines: CS.eventCardLines(missing),
}));
"""
    )
    payload = __import__("json").loads(out)
    assert payload["missing"] == ""
    assert payload["other"] == ""
    assert payload["lab"] == "Category: laboratory report"
    assert "Category: Not available" not in payload["missingLines"]
    blob = " ".join(payload["missingLines"])
    assert "Not available" not in blob


def test_uat12g_uat12f_batched_list_measurements_still_present():
    source = (ROOT / "backend" / "health_vault" / "timeline.py").read_text(encoding="utf-8")
    assert "measurements_by_document = _measurements_by_document(store.list_measurements())" in source
    assert 'store.list_measurements(document_id=doc["id"])' not in source
    assert 'store.list_measurements(document_id=doc[\'id\'])' not in source
    sw = (ROOT / "service-worker.js").read_text(encoding="utf-8")
    html = (ROOT / "index.html").read_text(encoding="utf-8")
    assert 'CACHE_REVISION = "hc321uat12g"' in sw
    assert "if (isForbiddenCacheUrl(req.url)) return;" in sw
    fetch_handler = sw.split('self.addEventListener("fetch"', 1)[1]
    assert fetch_handler.find("if (isForbiddenCacheUrl(req.url)) return;") < fetch_handler.find("event.respondWith")
    assert "consumer_surfaces.js?v=hc321uat12g" in html
    assert "service-worker.js?v=hc321uat12g" in html


def test_uat12g_authenticated_filtered_heart_rate_json_and_compaction_inputs():
    pytest.importorskip("fastapi")
    from fastapi.testclient import TestClient

    from backend.health_vault.api import create_health_vault_app

    with tempfile.TemporaryDirectory() as td:
        store = VaultStore(root=Path(td), encryption_key=b"U" * 32)
        app = create_health_vault_app(
            store=store,
            production=True,
            bootstrap_password="Boot-Pass-UAT12Exx",
        )
        values = [56, 60, 64, 70, 76, 78]
        for i, value in enumerate(values):
            store.store(
                document=MedicalDocument(
                    id=f"uat12g-hr-{i}",
                    patient_id="00000",
                    document_type="continuous_monitoring_observation",
                    source_system="health_connect_companion",
                    original_filename=f"heart-rate-{i}.json",
                    measured_at=f"2026-08-18T12:{i:02d}:00Z",
                    primary_category="other",
                    sha256=f"{i+10:064d}"[:64],
                ),
                measurements=[
                    Measurement(
                        metric="heart_rate",
                        value=value,
                        units="bpm",
                        measured_at=f"2026-08-18T12:{i:02d}:00Z",
                    )
                ],
                content=b"hr",
            )
        store.store(
            document=MedicalDocument(
                id="uat12g-steps",
                patient_id="00000",
                document_type="continuous_monitoring_observation",
                source_system="health_connect_companion",
                original_filename="steps.json",
                measured_at="2026-08-18T18:00:00Z",
                primary_category="other",
                sha256="c" * 64,
            ),
            measurements=[
                Measurement(metric="steps", value=5400, units="count", measured_at="2026-08-18T18:00:00Z")
            ],
            content=b"steps",
        )
        for i, value in enumerate((70, 72, 76)):
            store.upsert_observation(
                {
                    "patient_id": "00000",
                    "metric_type": "heart_rate",
                    "value": value,
                    "unit": "bpm",
                    "measured_at": f"2026-08-18T11:{i:02d}:00Z",
                    "source": "health_connect_companion",
                    "connector_id": "health_connect",
                    "fingerprint": f"uat12g-hr-{i}",
                    "observation_id": f"uat12g-obs-hr-{i}",
                }
            )

        stored_docs = len(store.list_documents())
        stored_obs = len(store.list_observations() or [])
        measurement_calls: list[dict] = []
        original_list = store.list_measurements

        def _counting_list_measurements(**filters):
            measurement_calls.append(dict(filters))
            return original_list(**filters)

        store.list_measurements = _counting_list_measurements  # type: ignore[method-assign]
        client = TestClient(app)
        headers = {**_uat12e_login(client), "Accept": "application/json"}

        measurement_calls.clear()
        filtered = client.get(
            "/api/health-vault/timeline?unified=true&metric=heart_rate",
            headers=headers,
        )
        body = _assert_json_not_html(filtered)
        assert filtered.status_code == 200
        assert str(filtered.headers.get("content-type") or "").lower().startswith("application/json")
        assert body.get("filter_metric") == "heart_rate"
        assert len(measurement_calls) == 1
        assert "document_id" not in measurement_calls[0]
        entries = body["entries"]
        assert len(entries) == 6
        assert all("heart_rate" in __import__("json").dumps(entry).lower() for entry in entries)
        assert all(
            "steps" not in __import__("json").dumps(entry).lower()
            or "heart_rate" in __import__("json").dumps(entry).lower()
            for entry in entries
        )
        compact = _node_surfaces_eval(
            "const events = " + __import__("json").dumps(entries) + """;
const groups = CS.compactTimelineEntries(events);
const hr = groups.filter(g => g.kind === 'group' && g.metric === 'heart_rate');
console.log(JSON.stringify({
  cards: groups.length,
  hrCards: hr.length,
  underlying: hr.reduce((n, g) => n + g.underlyingCount, 0),
  latest: hr[0] && hr[0].latest && hr[0].latest.value,
  min: hr[0] && hr[0].min,
  max: hr[0] && hr[0].max,
  lines: hr[0] ? CS.groupedCardLines(hr[0]) : [],
}));
"""
        )
        grouped = __import__("json").loads(compact)
        assert grouped["cards"] == 1
        assert grouped["hrCards"] == 1
        assert grouped["underlying"] == 6
        assert grouped["latest"] == 78
        assert grouped["min"] == 56
        assert grouped["max"] == 78
        assert grouped["lines"][0] == "Heart Rate"
        assert "6 observations" in grouped["lines"]
        assert "Latest: 78 bpm" in grouped["lines"]
        assert any("Range: 56–78" in line for line in grouped["lines"])
        assert "Category: Not available" not in grouped["lines"]

        snapshot = client.get("/api/health-vault/health-snapshot?metric=heart_rate", headers=headers)
        snap_body = _assert_json_not_html(snapshot)
        assert snapshot.status_code == 200
        assert snap_body.get("metric_id") == "heart_rate"
        history = snap_body.get("history") or []
        assert isinstance(history, list)

        records = client.get("/api/records?metric=heart_rate", headers=headers)
        records_body = _assert_json_not_html(records)
        assert records.status_code == 200
        names = [row.get("original_filename") for row in records_body["records"]]
        assert any(str(name).startswith("heart-rate-") for name in names)
        assert "steps.json" not in names

        trends = client.get("/api/health-vault/trends?metric=heart_rate", headers=headers)
        trends_body = _assert_json_not_html(trends)
        assert trends.status_code == 200
        assert "heart_rate" in (trends_body.get("trends") or {})
        assert "steps" not in (trends_body.get("trends") or {})

        assert len(store.list_documents()) == stored_docs
        assert len(store.list_observations() or []) == stored_obs


