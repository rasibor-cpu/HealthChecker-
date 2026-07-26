# HC-301 — Always-On Health Guardian

**Repository:** HealthChecker+  
**Phase:** HC-301  
**Date:** 2026-07-26  

---

## Purpose

Add an observational **Always-On Health Guardian** layer on top of the Health Vault:

- Formal alert engine (create / dedupe / acknowledge / resolve / critical persistence)
- Expanded clinical / continuity rules (Python authoritative; browser mirror best-effort)
- Personalized baselines from confirmed measurements
- CGM continuity (sensor registry, inventory, data gaps)
- Thin orchestrator: event → baselines → rules → alerts → timeline → EventBus → status
- PWA service-worker foundation for offline shell + optional local notifications

**Observational safety companion only.** Not a diagnosis. Not a medical device claim.  
No medication or insulin dosing advice.

---

## Architecture

```
Confirmed vault measurements / imports / manual CGM registry
  → BaselineEngine.rebuild()
  → CGMContinuity.detect_glucose_gap() + evaluate_continuity()
  → ExpandedClinicalRulesEngine.evaluate()   # Python
     (browser: HCHealthGuardian + HCClinicalRules.evaluateGuardianRules stub)
  → AlertEngine.ingest_evaluation()          # dedupe / cooldown / escalate
  → timeline_events + EventBus
  → guardian_status snapshot
  → UI Guardian tab / optional SW GUARDIAN_EVAL message
```

### Modules

| Layer | Python | Browser |
|-------|--------|---------|
| Alert engine | `backend/health_vault/guardian/alert_engine.py` | `js/health_vault/alert_engine.js` (`HCAlertEngine`) |
| Baselines | `backend/health_vault/guardian/baseline_engine.py` | `js/health_vault/baseline_engine.js` (`HCBaselineEngine`) |
| CGM continuity | `backend/health_vault/guardian/cgm_continuity.py` | `js/health_vault/cgm_continuity.js` (`HCCGMContinuity`) |
| Rules | `backend/health_vault/guardian/rule_engine.py` + `config/guardian_rules.json` | subset in `clinical_rules.js` + `health_guardian.js` |
| Orchestrator | `backend/health_vault/guardian/health_guardian.py` | `js/health_vault/health_guardian.js` (`HCHealthGuardian`) |
| Storage | `VaultStore` index fields | `HCHealthVault` meta (+ `HC_GUARDIAN_ALERTS_V1` fallback) |

Vault extensions (additive): `alerts`, `baselines`, `cgm_sensors`, `cgm_inventory`, `data_gaps`, `timeline_events`, `guardian_status`.

---

## Alert Engine

- Severities: `informational` → `watch` → `warning` → `urgent` → `critical`
- Deduplication by `patient|rule|metrics` while an alert is open
- Cooldown after resolve (critical bypasses cooldown on re-trigger)
- Critical alerts require acknowledgement before resolve; cannot snooze
- Persistence escalation: repeated same-severity detections can bump severity
- Safety disclaimer attached to every alert

---

## Rules

Configurable pack: `backend/health_vault/config/guardian_rules.json`.

Includes absolute thresholds, multi-metric BP, rate-of-change, rolling averages, consecutive abnormal, baseline deviation, CGM continuity, missing glucose data, and pipeline failure.

**Missing data never evaluates as Normal.**

Thresholds are configuration, not clinical facts — require clinician review before relying on them for personal care planning.

---

## Personalized Baselines

- Confirmed measurements only; minimum sample count (default 5)
- Rolling window (default 90 days)
- Median / mean / stdev / percentile bands
- Unit separation (incompatible units are not mixed)
- Insufficient samples → `ready: false` (never treat as “normal”)

---

## CGM Continuity

- Manual sensor register / activate / fail / replace
- Inventory never below 0; unknown inventory is a warning state
- Continuity states: `SAFE`, `WATCH`, `REORDER_REQUIRED`, `CRITICAL_SHORTAGE`, `SENSOR_EXPIRING`, `SENSOR_EXPIRED`, `SIGNAL_LOSS`, `DATA_PIPELINE_FAILURE`, `INVENTORY_UNKNOWN`
- Data-gap detection from vault glucose timestamps (upload/manual — **not** a live Libre feed)
- Does **not** replace FreeStyle Libre manufacturer alarms

---

## Orchestration & UI

- Guardian tab in `index.html`: status, alerts, CGM inventory/sensors, baselines, timeline filters, critical/urgent banner, safety disclaimer
- Dashboard shows a Guardian snapshot via `HCHealthGuardian`
- Timeline merges documents + `timeline_events` + optional `HC_V6` logs with dedupe; `measured_at` precedence

---

## Service Worker Limitations

File: `service-worker.js` (`CACHE_NAME = hc-guardian-v1`).

- Caches app shell (`index.html`, CSS, JS, `manifest.webmanifest`) for offline shell use
- **Does not** cache `vault_storage` clinical blobs or API secrets
- Message types: `GUARDIAN_EVAL` / `SKIP`
- Attempts `periodicSync` tag `hc-guardian-eval` when supported
- `showNotification` only if `Notification.permission === 'granted'`
- **Never** sends caregiver SMS/email/push off-device in HC-301
- PWAs cannot guarantee unrestricted background execution (especially iOS)

---

## Privacy (local-first)

- Browser Guardian state lives in localStorage / IndexedDB on-device
- Python vault under `vault_storage/` is local filesystem
- No off-device caregiver or emergency notification channel in this phase

---

## Safety Disclaimers

- Observational only — not a diagnosis
- Does not replace manufacturer CGM/device alarms
- Does not replace medical care or emergency services
- No medication or insulin dosing advice
- Samsung BP is user-initiated; ECG is not continuously collected
- Galaxy Watch does not measure glucose
- **Health Connect is not implemented** in HC-301

---

## Explicit Non-Goals (HC-301)

| Item | Status |
|------|--------|
| Health Connect integration | Not implemented |
| Live Libre / Samsung APIs | Not implemented (upload/parser + manual registry) |
| Watch continuous glucose | Not available |
| Continuous BP / ECG streaming | User-initiated / not continuous |
| Caregiver off-device alerts | Not sent |
| Native companion app | Future |

Manufacturer alarms remain authoritative.

---

## Test Evidence

Baseline (pre-HC-301): `128 passed` on HEAD `2477e43`.

Pre-hardening HC-301: `16` focused tests; full suite `144` passed.

Post-audit hardening:

```bash
python -m pytest tests/test_hc301_health_guardian.py -q
# 77 passed

python -m pytest -q
# 205 passed (1 PendingDeprecationWarning: prefer python_multipart)
```

Manual import (script only — never on startup; personal records require explicit approval):

```bash
python scripts/import_recent_hc301_records.py --dry-run
# Dry-run only until approved: imported=0 dry-run=2
```

Browser: open Guardian tab → Evaluate → confirm banner/alerts/inventory/baselines; service worker registers when served over HTTPS/localhost.

Authoritative rule/alert semantics: **Python**. Browser mirrors are best-effort for offline PWA use.

---

## Future

- Native companion for more reliable background checks
- Optional Health Connect / live device bridges (explicit consent)
- Clinician-reviewed personalized threshold packs
- Richer baseline contexts (fasting / post-meal) once sample depth allows

---

## Persistence map

| Concern | Where it lives | Notes |
|---------|----------------|-------|
| Documents, measurements, SHA duplicates | Vault index + `vault_storage/` blobs | Append-only clinical store; Python `VaultStore` |
| Alerts, baselines, CGM sensors/inventory, data gaps, timeline events, guardian status/audits | Vault index (`index.json`) | HC-301 additive fields; never overwrite clinical docs |
| Browser Guardian UI cache / offline shell | Service worker `CACHE_NAME` (`hc-guardian-v1`) | App shell only — **never** `vault_storage` or `/api/` |
| Browser alert/meta fallback | `localStorage` / IndexedDB via `HCHealthVault` (+ `HC_GUARDIAN_ALERTS_V1` fallback) | Best-effort mirror; not authoritative |
| In-flight evaluation / EventBus subscribers | Process memory | Ephemeral; status snapshot persisted after evaluate |

Python vault on disk is the durable source of truth for confirmed imports and Guardian state written by the backend.

---

## Python vs JS parity matrix

| Concept | Python (authoritative) | Browser JS (best-effort) |
|---------|------------------------|---------------------------|
| Severity names | `informational`, `watch`, `warning`, `urgent`, `critical` | Same string set in `HCAlertEngine` |
| Alert statuses | `active`, `acknowledged`, `snoozed`, `resolved`, `expired` | Mirrored subset |
| Overall Guardian states | `NORMAL`, `WATCH`, `WARNING`, `URGENT`, `CRITICAL`, `MONITORING_DEGRADED`, `UNKNOWN` | Status display via `HCHealthGuardian` |
| CGM continuity states | `SAFE`, `WATCH`, `REORDER_REQUIRED`, `CRITICAL_SHORTAGE`, `SENSOR_EXPIRING`, `SENSOR_EXPIRED`, `SIGNAL_LOSS`, `DATA_PIPELINE_FAILURE`, `INVENTORY_UNKNOWN` | Mirrored labels |
| Rule evaluation | `ExpandedClinicalRulesEngine` + `guardian_rules.json` | Subset / stub in `clinical_rules.js` + `health_guardian.js` |
| Authoritative side | **Python** for create/dedupe/ack/resolve/evaluate semantics | Offline PWA convenience only |

If Python and JS disagree, trust Python (and re-sync from vault / API).

---

## Honest evaluation modes

| Mode | When | `fully_evaluated` | Behavior |
|------|------|-------------------|----------|
| **Full** | Manual / API `evaluate`, scheduled full pass | `true` | Rebuild baselines → CGM gap + continuity → rules → alerts → status |
| **Lightweight** | Post-import hook (`evaluate_after_import`) | `false` | Baselines + rules/alerts; **defers** `cgm_gap_detection` and `full_continuity_refresh` via `deferred_steps` |

Lightweight results must not be presented as a complete continuity check. Status may carry `evaluation_mode: "lightweight"` and must not claim NORMAL solely because deferred work was skipped.

---

## Empty vault → UNKNOWN

An empty patient vault (no confirmed measurements and no active alerts) evaluates to **`UNKNOWN`**, never **`NORMAL`**.

Missing glucose / missing inventory is similarly never treated as “all clear.” No data → Unknown / degraded monitoring language, not Normal.

---

## Test evidence (updated counts placeholder)

Baseline (pre-HC-301): `128 passed` on HEAD `2477e43`.

Post-HC-301 expansion (placeholder — re-run locally to confirm):

```bash
python -m pytest tests/test_hc301_health_guardian.py -q
# expected: 55+ passed (alert / rules / baseline / CGM / guardian / timeline / API / import / SW)

python -m pytest -q
# expected: prior suite + HC-301 expansions (fill exact counts after a local run)
```

Manual import (script only — never on startup):

```bash
python scripts/import_recent_hc301_records.py --dry-run
python scripts/import_recent_hc301_records.py
```

Fingerprints for idempotent skip: `patient_id|metric|value|measured_at|source_system`.
