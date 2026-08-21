# HC-321 — Consumer Health Snapshot Dashboard and Screenshot Support

Observational decision-support only. Not a diagnosis or prescription.

## Reconnaissance (Phase 0)

### EXISTING
- Consumer landing: `index.html` Dashboard tab (`#dash`) after load (no separate login gate in the PWA).
- Executive Health Dashboard (`js/health_vault/executive_dashboard.js`, `backend/health_vault/executive_briefing.py`).
- Domain cards, vault records, timeline, trends, reports, Doctor Visit.
- Canonical observations (`backend/health_vault/monitoring/observation.py`) and vault `Measurement`.
- ClinicalRulesEngine (`config/clinical_rules.json`) — Normal / Borderline / Abnormal / Critical / Unknown.
- TrendEngine (Improving / Stable / Worsening).
- HC-302 freshness windows (`config/monitoring_config.json`).
- Android companion (Health Connect → host). **No `FLAG_SECURE` was set application-wide.**
- Companion theme: `Theme.MaterialComponents.DayNight.NoActionBar`.

### PARTIAL
- Status colours existed as ok/warn/bad, not NORMAL/CAUTION/ATTENTION/UNKNOWN → GREEN/AMBER/RED/GREY.
- Latest-value sorting existed but did not skip invalid newer rows or mark stale as not-current.
- Domain cards were not full-card tappable HealthMetricCards.
- PWA was dark-only; no dashboard card reorder.

### MISSING (now implemented)
- Domain Health Snapshot engine and reusable HealthMetricCard.
- Normalized consumer statuses consumed by the UI.
- Dashboard customization (show/hide/reorder snapshot cards).
- Light/dark theme toggle on the consumer PWA.
- Explicit Android screenshot policy + tests.

### REUSE PLAN
- Clinical thresholds stay in `ClinicalRulesEngine` / `clinical_rules.json`.
- Snapshot engine selects latest-valid rows and maps flags → consumer status.
- Cards host inside the existing Dashboard; drill-down reuses Vault timeline/trends.
- Screenshot policy **clears** `FLAG_SECURE` on companion activities; it does not add it.

## Clinical status architecture

UI cards consume only:

- `status`: `NORMAL` | `CAUTION` | `ATTENTION` | `UNKNOWN`
- `status_text` (never colour-only)
- `status_color`: `GREEN` | `AMBER` | `RED` | `GREY`

Determination (`backend/health_vault/health_snapshot.py`, mirrored in `js/health_vault/health_snapshot.js`):

1. Select the latest **valid** observation (skip missing values, incompatible units, simulated rows, impossible values).
2. Compute freshness. **Stale** observations stay visible but map to UNKNOWN/GREY (“not current”).
3. Map `ClinicalRulesEngine` flags: Normal→NORMAL, Borderline→CAUTION, Abnormal/Critical→ATTENTION, Unknown→UNKNOWN.
4. Context-aware exceptions (documented, testable, not in the card renderer):
   - Glucose post-meal vs fasting / unknown
   - Unlabelled vs activity heart rate
   - Adult single-night sleep duration (NSF 7–9 h); not chronic deterioration
   - Weight / steps / activity: informational, no invented clinical target

## Screenshot configuration

| Screen | Mechanism | Screenshot | Justification |
| --- | --- | --- | --- |
| Welcome / Health Snapshot (PWA Dashboard) | none | allowed | ordinary health information |
| Metric detail / Vault / Trends / Timeline / Reports | none | allowed | ordinary health information |
| Companion status (`CompanionStatusActivity`) | `ScreenshotPolicy.applyConsumerScreenshotPolicy` clears `FLAG_SECURE` | allowed | pairing tokens are in EncryptedSharedPreferences, not shown as capture-sensitive UI |
| Permissions rationale | same helper | allowed | platform rationale text |
| Application-wide | **none** | allowed | no global `FLAG_SECURE` existed; none was added |

**No screens remain screenshot-protected.** Authentication, encryption, and vault controls were not weakened.

## Manual UAT (Samsung Galaxy S24 Ultra)

1. Install/run a debug Android companion build (`android/`: `./gradlew :app:assembleDebug`) and open the HealthChecker+ PWA (`index.html`) on the device browser or hosted URL.
2. Open the app. Confirm **Dashboard** is the default landing screen (Health Snapshot at the top).
3. With vault/manual readings present, confirm metric cards show latest valid values, units, status **text**, colour treatment, timestamp/freshness, and trend when ≥3 points exist.
4. Confirm GREEN=Normal, AMBER=Caution, RED=Attention, GREY=Unknown/stale/missing interpretation (status text always visible).
5. Tap several cards. Confirm the Vault tab opens, category filter applies, and Back/Dashboard tab returns.
6. Confirm stale rows say “not current” and are not coloured as current Normal.
7. Toggle **Light mode** / **Dark mode**. Confirm all four status treatments remain readable.
8. Open **Customize dashboard**. Hide and reorder cards; reload; confirm persistence.
9. Take Android screenshots of: Welcome/Health Snapshot, a metric/Vault detail, Trends, Observations/Timeline, Reports. Confirm they save to the gallery.
10. Companion status/pairing screen: screenshot should also save (not blocked).
11. Confirm login/pairing, Health Connect, vault import, encryption prefs, and existing tabs still work.

## Tests

```bash
python -m pytest tests/test_hc321_health_snapshot.py tests/test_hc201i_executive_dashboard.py tests/test_hc201c_production_readiness.py tests/test_hc201_health_vault.py -q
cd android && ./gradlew :app:testDebugUnitTest
```
