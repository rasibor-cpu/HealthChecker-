# HC-302 — Continuous Health Monitoring Foundation

**Repository:** HealthChecker+
**Phase:** HC-302
**Date:** 2026-07-26
**Starting HEAD:** `bd8e4e7e2fd7c1b5a47d09fb29b6c1ddb0c68d03`

---

## Purpose

Add a production-quality **foundation** for continuous, background health-data monitoring:

- Canonical observation model with acquisition-mode honesty (`LIVE`, `DELAYED`, `MANUAL`, `IMPORTED`, `SIMULATED_TEST_ONLY`, `STALE`, `UNAVAILABLE`)
- Device connector interface (Health Connect / Samsung boundary + Libre boundary)
- Ingestion coordinator (normalize, dedupe, persist, cursors, sync health)
- Monitoring engine (freshness, configurable thresholds, short-term trends)
- Status API + mobile-friendly UI panel
- Scheduler foundation with bounded backoff (no busy loops)

**Observational decision support only.** Not a diagnosis. Not a medical device claim.
Does not replace manufacturer alarms, clinician care, or emergency services.

---

## Architecture

```
Device connectors (fetch/normalize only — never write vault)
  → ContinuousMonitoringBridge.sync_*()
  → IngestionCoordinator (normalize + fingerprint dedupe + VaultStore)
  → MonitoringEngine (freshness + thresholds + trends)
       → HC-301 AlertEngine (dedupe / ack / resolve)
  → optional HealthGuardian.evaluate(trigger=hc302_*)
  → monitoring_status snapshot + EventBus
  → API / Guardian-tab Monitoring panel / SW MONITORING_SYNC nudge
```

### Modules

| Layer | Path |
|-------|------|
| Observation model | `backend/health_vault/monitoring/observation.py` |
| Privacy-safe logs | `backend/health_vault/monitoring/privacy.py` |
| Connector base | `backend/health_vault/monitoring/connectors/base.py` |
| Health Connect foundation | `backend/health_vault/monitoring/connectors/health_connect.py` |
| Libre foundation | `backend/health_vault/monitoring/connectors/libre.py` |
| Simulated test-only | `backend/health_vault/monitoring/connectors/simulated.py` |
| Ingestion | `backend/health_vault/monitoring/ingestion.py` |
| Monitoring engine | `backend/health_vault/monitoring/monitoring_engine.py` |
| Scheduler | `backend/health_vault/monitoring/scheduler.py` |
| Bridge | `backend/health_vault/monitoring/bridge.py` |
| Config | `backend/health_vault/config/monitoring_config.json` |
| Thresholds | `backend/health_vault/config/monitoring_thresholds.json` |
| Browser UI mirror | `js/health_vault/continuous_monitoring.js` |

Vault additive index fields: `observations`, `connector_cursors`, `connector_sync_health`, `monitoring_status`, `monitoring_audits`.

Observations also create lightweight `continuous_monitoring_observation` documents + measurements so HC-201 timeline / HC-301 Guardian can consume them without a parallel clinical database.

---

## Implemented capabilities

1. **Canonical observation model** — ID, metric, value/text, unit, measured_at (timezone-aware UTC Z), received_at, source, source_record_id, acquisition mode, freshness, confidence/quality, provenance, device metadata, fingerprint.
2. **Connector interface** — readiness, supported metrics, incremental cursor, fetch-new-observations, explicit unavailable / permission states.
3. **Health Connect / Samsung adapter foundation** — capability discovery; BP/ECG marked non-continuous; live reads require injectable `platform_bridge` (Android companion). Without bridge → `UNAVAILABLE`.
4. **Libre adapter foundation** — live path `IMPORT_REQUIRED` / `UNAVAILABLE` until authorized client configured; file-import rows accepted only as `IMPORTED` (never silently labeled `LIVE`).
5. **Ingestion coordinator** — unit/timestamp normalization, idempotent fingerprint dedupe, vault persistence, cursor + sync-health records, privacy-safe event payloads.
6. **Monitoring engine** — freshness windows, configurable absolute thresholds, short-term trend deltas, AlertEngine dedupe/suppression, informational→emergency-routing severities.
7. **Status API/UI** — `/api/monitoring/status|connectors|sync|evaluate|scheduler/tick`; Guardian tab Continuous Monitoring panel with live-vs-imported labeling.
8. **Scheduler foundation** — due/backoff planner; explicit `tick` / `run_due`; documents that continuous execution is **not** guaranteed in browser/PWA.

---

## Live versus imported / manual / simulated

| Mode | Meaning |
|------|---------|
| `LIVE` | Authorized live connector reading (requires real bridge/client) |
| `DELAYED` | Device/platform sync that is not real-time streaming |
| `MANUAL` | User-entered |
| `IMPORTED` | File/export/upload parser path |
| `SIMULATED_TEST_ONLY` | Test doubles only; production sync refuses without explicit allow |
| `STALE` / `UNAVAILABLE` | Freshness or connector capability states |

**Production code never silently falls back to simulated readings.**

---

## Permission requirements

- **Android Health Connect / Samsung Health:** OS Health Connect permissions + future companion bridge; not grantable from the Python vault process alone.
- **Libre live:** Authorized Abbott/Libre integration credentials/client (not present in HC-302). Until then: import exports.
- **Browser notifications:** optional `Notification.permission`; local only.
- **Periodic Background Sync:** best-effort; often unavailable on iOS.

---

## Security / privacy protections

- Local-first vault persistence; `vault_storage/` remains gitignored.
- Sync health / EventBus payloads pass through `redact_for_log()` — clinical values and source record IDs redacted.
- API continues to sanitize absolute filesystem paths.
- Service worker still blocks caching of `/api/` and `vault_storage`.
- Simulated connector excluded from production connector listings.
- Public monitoring sync API does not enable simulated unless `connector_id=simulated` **and** `allow_simulated=true` (test harness only).

---

## Testing evidence

Focused suite: `tests/test_hc302_continuous_monitoring.py`

Covers: model validation, timezone handling, unit normalization, dedupe/idempotency, cursors, unavailable/permission-denied, stale detection, thresholds, trends, duplicate alert suppression, live/imported/manual classification, simulated isolation, privacy-safe logging, API contracts, scheduler backoff.

Synthetic fixtures only — no personal health records.

---

## Genuine live-integration limitations

- No Android Health Connect SDK/companion is shipped in this phase.
- No authorized live Libre API client is configured.
- Galaxy Watch does **not** measure glucose.
- BP and ECG are **not** claimed as continuous — they generally require explicit supported measurements.
- PWA/service worker only **nudges** open clients; it does not guarantee continuous sync or evaluation while suspended.
- No off-device caregiver SMS/email/push in HC-302.

---

## HC-302R certification remediations

Independent review (HC-302R) remediated:

- Simulated observations are observation-index only (no clinical measurements) and never feed Guardian
- Cursor advances only after durable batch success
- Scheduler state persisted with overlap lease; unavailable sync is degraded, not false success
- `last_attempt_at` vs `last_successful_sync` distinguished
- Stale readings emit freshness alerts only (not absolute thresholds as current)
- Latest metric selection by `measured_at`; fingerprints include `patient_id`
- Overlapping absolute vitals reuse HC-301 AlertEngine `rule_id`s (no duplicate glucose alerts)
- Threshold schema validation; incompatible units rejected
- Material worsening reactivates acknowledged urgent/critical alerts
- Public API rejects simulated connectors
- Frontend escapes HTML, fetches `/api/monitoring/status`, labels freshness/live-vs-imported
- Adversarial synthetic tests expanded in `tests/test_hc302_continuous_monitoring.py`


1. Android companion app / Health Connect permission UX and bridge implementing `readiness()` + `fetch_new_observations()`.
2. Authorized Libre live connectivity (legal/API agreement + secure credential handling).
3. Native background work (WorkManager / equivalent) for reliable periodic sync beyond PWA limits.
4. Optional encrypted-at-rest hardening beyond current filesystem vault controls.
5. Caregiver notification product decisions (explicitly out of HC-302).

---

## API surface

| Method | Path |
|--------|------|
| GET | `/api/monitoring/status` |
| GET | `/api/monitoring/connectors` |
| POST | `/api/monitoring/sync` |
| POST | `/api/monitoring/evaluate` |
| POST | `/api/monitoring/scheduler/tick` |

Framework-agnostic handlers in `backend/health_vault/api.py` support pytest without FastAPI.
