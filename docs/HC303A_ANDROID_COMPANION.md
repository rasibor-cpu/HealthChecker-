# HC-303A — Android Companion Readiness and Secure Bridge

**Repository:** HealthChecker+
**Phase:** HC-303A
**Date:** 2026-07-27
**Starting HEAD:** `40e7b9e41792b73609990b7c79888a246b5b9bc3`

---

## Purpose

Add an Android companion **foundation** that can:

1. Discover Health Connect availability and permissions honestly.
2. Read authorized Health Connect records incrementally.
3. Pair securely with the HealthChecker+ host.
4. Deliver normalized observations into the certified HC-302 `IngestionCoordinator`.
5. Schedule best-effort background sync with WorkManager.

**This phase does not claim production-live monitoring** until the APK is built, installed, permission-tested, and validated on Robert’s actual Android phone.

Observational decision support only. Not a diagnosis. Not medication advice.

---

## Architecture and trust boundary

```
Samsung Health / other apps  →  Android Health Connect
                                      ↓
                         HC Companion (Kotlin)
                         - capability + permissions
                         - incremental reader
                         - EncryptedSharedPreferences token
                         - WorkManager unique periodic work
                                      ↓  HTTPS (+ local-dev exception in debug only)
                         Host /api/companion/*
                         - pair start/confirm/revoke
                         - Bearer token (hash stored only)
                         - schema / replay / size validation
                                      ↓
                         HC-302 IngestionCoordinator
                         - fingerprint dedupe
                         - MonitoringEngine / AlertEngine
```

The host **cannot** read Health Connect. The companion **pushes** observations. The in-process HC-302 `platform_bridge` remains for tests; production delivery uses authenticated companion endpoints.

---

## Prerequisite audit (laptop)

| Prerequisite | Class |
|--------------|--------|
| HC-302 connector/ingestion contracts | READY |
| Existing Android project (before HC-303A) | MISSING → foundation added under `android/` |
| JDK / Android SDK / Gradle on laptop | MISSING (do not auto-install) |
| API auth / pairing (before HC-303A) | MISSING → host pairing added |
| Encryption at rest for vault blobs | MISSING (companion uses Keystore-backed prefs) |
| Live phone / Health Connect validation | EXTERNAL_DEVICE_VALIDATION_REQUIRED |

---

## Android / Health Connect requirements

- `minSdk 28`, `compileSdk/targetSdk 35`
- Health Connect client dependency
- WorkManager 2.x
- EncryptedSharedPreferences / MasterKey
- Internet permission
- Health Connect read permissions for supported record types

Samsung Health must **write** the requested record types into Health Connect for them to be readable. If Samsung does not publish a type to Health Connect on the device/region, the companion will report missing data — not fabricate it.

---

## Permissions and metric matrix

| Metric | Permission / record | Continuous? | HC-303A |
|--------|---------------------|-------------|---------|
| Heart rate | READ_HEART_RATE | Often yes | Supported |
| Resting HR | READ_RESTING_HEART_RATE | Derived | Supported |
| SpO₂ | READ_OXYGEN_SATURATION | Session/spot | Supported |
| Blood pressure | READ_BLOOD_PRESSURE | **No** (explicit) | Supported as DELAYED explicit measurement |
| Sleep | READ_SLEEP | Session | Supported |
| Steps | READ_STEPS | Aggregate | Supported |
| Exercise | READ_EXERCISE | Session | Supported as exercise_minutes |
| Weight | READ_WEIGHT | Spot | Supported |
| ECG | — | **No** | **Unsupported** in HC-303A |

---

## Pairing procedure

1. On host: `POST /api/companion/pair/start` → one-time code (10 minutes).
2. In companion: enter host base URL + code → `POST /api/companion/pair/confirm`.
3. Companion stores `device_id` + `device_token` in Keystore-backed prefs.
4. Host stores **token hash only**.
5. Revoke anytime: `DELETE /api/companion/devices/{device_id}`.

---

## Development vs production transport

| Mode | Rule |
|------|------|
| Production / release | HTTPS required; cleartext disabled |
| Debug local-dev | Cleartext to private LAN allowed only when `ALLOW_CLEARTEXT_LOCAL_DEV=true` and `X-HC-Local-Dev: true` |

Do not hard-code IPs, credentials, or personal identifiers.

---

## Build and installation (when tooling available)

```text
cd android
# Requires JDK 17+, Android SDK, Gradle wrapper or system Gradle
./gradlew :app:testDebugUnitTest
./gradlew :app:lintDebug
./gradlew :app:assembleDebug
adb install -r app/build/outputs/apk/debug/app-debug.apk
```

On this laptop at implementation time, JDK/SDK/Gradle were **not** installed — Android Gradle tasks are **blocked** until tooling is provisioned manually.

---

## Background-execution limitations

- WorkManager unique periodic work name: `hc303a_monitoring_sync`
- Minimum interval: **15 minutes** (Android platform)
- Constraints: network connected, battery not low
- Exponential backoff; overlapping runs prevented via unique work KEEP policy
- Exact timing and uninterrupted execution are **NOT guaranteed**
- Native WorkManager is still subject to OEM battery savers

---

## Host API surface

| Method | Path |
|--------|------|
| POST | `/api/companion/pair/start` |
| POST | `/api/companion/pair/confirm` |
| GET | `/api/companion/devices` |
| DELETE | `/api/companion/devices/{device_id}` |
| POST | `/api/companion/observations` |
| GET | `/api/companion/status` |

Accepted observations flow through HC-302 `IngestionCoordinator` with `connector_id=health_connect`. Simulated data is rejected. Cursor advances only after durable acknowledgement without rejected rows.

---

## Privacy / security controls

- Token hash only on host; Keystore-backed token on device
- Replay resistance via `batch_id`+`nonce` acks and seen observation keys
- Payload size / batch count limits
- Log redaction (`redact_companion_log` / `PrivacyRedactor`)
- No health values on companion status UI
- `allowBackup=false`
- Release minify enabled; cleartext disabled

---

## Device-validation checklist (Robert’s phone)

1. Install debug APK on Android 9+ with Health Connect.
2. Confirm Health Connect availability state is honest (unsupported/update/ready).
3. Grant only required permissions; deny one and confirm sync does not claim success.
4. Pair with host over HTTPS (or documented local-dev HTTP).
5. Confirm Samsung Health (or other) data appears in Health Connect for HR/SpO₂/steps/etc.
6. Trigger Sync now; verify host vault receives DELAYED observations with provenance.
7. Revoke device; confirm further delivery returns unauthorized/revoked.
8. Enable WorkManager; observe last attempt vs last success separately.
9. Confirm BP readings are labeled as explicit measurements, not continuous.
10. Confirm ECG is not presented as supported continuous data.

---

## Remaining work before live activation

- Phone validation checklist above (not started in HC-303B)
- Confirm Samsung→Health Connect record coverage on Robert’s device/region
- Production TLS termination and LAN bind policy; set `HC_COMPANION_ADMIN_TOKEN` + `HC_COMPANION_PEPPER`
- Optional Libre live path remains **out of scope**
- Full clinical deletion reconciliation remains limited to tombstones (no host clinical history delete)

---

## HC-303B toolchain and build evidence

| Item | Value |
|------|--------|
| JDK | Microsoft OpenJDK 17.0.10 (`...\jdk-17.0.10.7-hotspot`) |
| Android SDK | `%LOCALAPPDATA%\Android\Sdk` |
| cmdline-tools | 22.0 |
| build-tools | 35.0.0 |
| platform | android-35 |
| platform-tools / adb | 37.0.0 / 1.0.41 |
| Gradle wrapper | 8.7 (`distributionUrl` HTTPS official) |
| AGP / Kotlin | 8.5.2 / 1.9.24 |
| minSdk / compileSdk / targetSdk | 28 / 35 / 35 |

### Reproducible session commands

```powershell
$env:JAVA_HOME = "C:\Users\Larry\AppData\Local\Programs\Microsoft\jdk-17.0.10.7-hotspot"
$env:ANDROID_HOME = "C:\Users\Larry\AppData\Local\Android\Sdk"
$env:ANDROID_SDK_ROOT = $env:ANDROID_HOME
$env:Path = "$env:JAVA_HOME\bin;$env:ANDROID_HOME\platform-tools;$env:ANDROID_HOME\cmdline-tools\latest\bin;$env:Path"
# android/local.properties -> sdk.dir=C:/Users/Larry/AppData/Local/Android/Sdk  (gitignored)
cd android
.\gradlew.bat --version --no-daemon
.\gradlew.bat testDebugUnitTest --no-daemon
.\gradlew.bat lintDebug --no-daemon
.\gradlew.bat assembleDebug --no-daemon
```

### HC-303B code outcomes

1. **Stable retry identity** — `PendingBatch` persists batch_id/nonce/payload; cleared only after durable ack (or permanent auth failure).
2. **Manual/worker mutex** — `SyncMutex` lease shared by manual sync and WorkManager; surfaces `sync_already_running`.
3. **Deletions** — Health Connect deletion IDs delivered as host tombstones; clinical history is **not** deleted.
4. **Production gate** — `ProductionConfigGate` fails closed for missing host/token and release cleartext.

### Build defects fixed during HC-303B

- `HeartRateRecord.Sample.zoneOffset` unresolved → use record `startZoneOffset` / `zoneOffset`
- Privacy redactor left Bearer token fragments → strengthened patterns
- Windows resource merge file lock → cleared locked merge intermediates once
- AGP compileSdk 35 warning → `android.suppressUnsupportedCompileSdk=35`

LIVE ACTIVATION REMAINS NO-GO.

---

## Testing evidence (host)

| Suite | Result |
|-------|--------|
| Focused HC-303A/B host + static Android contracts | See latest validation run |
| Android unit / lint / assembleDebug | Executed on provisioned laptop toolchain (see HC-303B report) |

### HC-303AR security remediations (foundation)

- Pair codes stored as HMAC hashes only; generic reject errors; attempt throttle
- Device tokens HMAC-verified with vault/env pepper; raw token once; strict Bearer
- Batch reservation, nonce/payload conflict detection, clock-skew window
- Patient ID injection rejected; admin token gate for pairing lifecycle when configured
- Release cleartext disabled; debug cleartext isolated to debug source set
- WorkManager permanent auth failures return `Result.failure()` (no infinite retry)

---

## HC-303C — Permission UI reopen + navigation (pre-live)

**Phase:** HC-303C device validation (pre-commit)
**Base HEAD:** `0f48b39478eb72addd13e0ddc08b9142c5ed8c58`
**Device:** Samsung S24 Ultra · Android 16
**Debug APK SHA-256:** `61C44F54CFCAA028E57E701EBE71545C712F6CF73BDDB57AAC077D0431D8D50A`

### Defects discovered and remediated

1. **Permission UI would not reopen** after cancel / partial Steps grant when re-requesting the full permission set on Android 16 → request **only missing** permissions; silent/no-result detection; visible **Manage Health Connect permissions** (user-initiated only; no automatic settings redirect).
2. **Missing Back navigation** → Material toolbar Back + system Back via `StatusScreenNavigator` (finish only; no sync/pairing side effects).
3. **Generic launcher icon** → adaptive HealthChecker+ brand mark (navy / blue / teal).
4. **WorkManager button under Samsung nav bar** → window insets + scroll bottom clearance.

### Android 16 platform limitations (documented, not defects)

- Repeated REQUEST after a partial grant may no-op unless missing-only launch is used (remediated).
- **Manage** may open the main Health Connect **app list** rather than the package-specific page; user selects **HealthChecker+ Companion**, then adjusts permissions.

### Confirmed device results (Samsung S24 Ultra / Android 16)

| Check | Result |
|-------|--------|
| ADB authorization | PASS |
| Debug APK install/update | PASS |
| Launch / no crash | PASS |
| Health Connect READY | PASS |
| Initial permission denial | PASS — 0 granted / 8 missing |
| Steps-only grant | PASS — 1 granted / 7 missing |
| Android 16 repeated-request limitation | Documented |
| Manage Health Connect settings fallback | PASS |
| Steps revocation | PASS — 0 granted / 8 missing |
| Status refresh after reopening Companion | PASS |
| Toolbar Back | PASS |
| State preserved after Back/reopen | PASS |
| Branded adaptive icon | PASS |
| System-bar insets / bottom-button visibility | PASS |
| No pairing | Confirmed |
| No sync attempt | Confirmed |
| No queued observations | Confirmed |
| No WorkManager activation | Confirmed |
| No real health observation displayed, transmitted, or persisted | Confirmed |

### Safety boundary for HC-303C

HC-303C validates install, capability, permission UX, navigation, and UI chrome only.
It does **not** authorize pairing, host delivery, WorkManager clinical sync, or live monitoring.

LIVE ACTIVATION REMAINS NO-GO.
