# HC-319C Mobile Identity and API-Only Consumer Hardening

## Outcome

The two HC-319B blockers are resolved for the mobile consumer path:

- companion pairing is bound to a full HC-318 authenticated account;
- Android observation delivery derives ownership only from that paired device;
- missing and legacy generic ownership fail closed;
- the dedicated `/mobile` consumer document is API-only and loads no legacy
  browser health vault, local clinical database, or service worker;
- logout can atomically revoke the named user-owned companion device before
  revoking the HC-318 session;
- Android has an explicit user-scoped state cleanup operation for account switch.

No second Android application, credential registry, clinical repository, or
Health Intelligence implementation was created.

## Baseline

- Repository: `C:\rasib\source\HealthChecker-HC310E`
- Branch: `hc311-encrypted-vault-at-rest`
- Starting HEAD: `fb648d4 HC-319A: Implement single-app consumer runtime`
- Remote: `origin` -> `https://github.com/rasibor-cpu/HealthChecker-.git`
- HC-319B review was present and used as the architecture input.
- Existing HC-313/314 runtime artifacts and unrelated evidence were preserved.

## `default-patient` audit and classification

An exhaustive scan covered backend, Android, configuration, and tests.

| Classification | Locations | HC-319C disposition |
|---|---|---|
| **UNSAFE_DEFAULT / production mobile** | companion pairing previously wrote `default-patient`; companion confirmation repeated it; delivery fell back to it | Removed. Pairing requires an authenticated user ID, confirmation copies the consumed session owner, and delivery rejects missing or `default-patient` ownership. |
| **PRODUCTION / authenticated mobile** | main FastAPI pairing start, device list, device revocation; companion-only host pairing start | Now requires a full HC-318 session. Body/query patient IDs are not trusted. Device list and revocation are owner-scoped. |
| **LEGACY** | older monitoring, Guardian, timeline, intelligence, import/backfill, and storage APIs/models | Retained for non-mobile backward compatibility and explicitly excluded from the mobile consumer route. These defaults are not reachable through the HC-319C pairing/delivery identity chain. |
| **SAFE_DEFAULT** | legacy single-user domain/service method defaults used only when older callers intentionally omit scope | Not expanded or used by mobile. Follow-up modernization can remove these independently without destabilizing HC-311–315. |
| **TEST_FIXTURE** | HC-201/301/302 and older regression fixtures | Preserved where testing legacy behavior. Companion/cursor fixtures were changed to explicit synthetic user IDs. |
| **Configuration** | no Android production patient ID configuration | No hard-coded Robert/`00000` identity was added. |

`00000` continues to resolve dynamically only through HC-318 login for the
bootstrap owner. Android production code contains neither Robert's name nor his
user ID.

## Authenticated identity architecture

```text
HC-318 full user session
        |
        v
pair/start resolves account.user_id on the server
        |
        v
one-time pair session stores user_id + code hash
        |
        v
pair/confirm copies consumed session user_id to device
        |
        v
device bearer token authenticates ingestion-only device
        |
        v
HC-302 observations and cursor use device.patient_id
```

The Android request cannot select or override patient identity. Pair codes and
device tokens remain hash-only on the host, and the Android device token remains
in Keystore-backed encrypted preferences. The device credential retains its
limited `health_connect.observations` scope and is not a consumer login token.

The companion-only host keeps its proxy and administrator gates and now also
resolves a full HC-318 bearer session before starting pairing.

Legacy devices with absent or `default-patient` ownership cannot deliver. They
must be explicitly revoked/re-paired after an authenticated user initiates a new
pairing. There is no silent migration to Robert or another account.

## Logout, revocation, and account switch

`POST /api/auth/logout` accepts an optional `device_id`. When supplied, the
server verifies that the device belongs to the authenticated account, revokes
the device, then revokes the user session. A cross-user device ID is rejected
without revocation.

Android `SecurePrefs.clearUserScopedState()` clears the paired host/device/token,
Health Connect cursor and scope, pending delivery batch, last sync state,
warnings, and queued count. The future hybrid launcher must call it only after
server-confirmed logout/revocation. A newly signed-in account must pair afresh;
it cannot inherit an old device token, cursor, or queued observations.

## Browser clinical-storage audit

### Legacy root document

The historic `index.html` still loads legacy modules that use:

- `HC_V6` and other `localStorage` JSON health state;
- `HC_HEALTH_VAULT_V1` metadata in `localStorage`;
- `HCHealthVault` IndexedDB document blobs;
- browser-side timeline, trends, alerts, Guardian, and import engines.

That document is classified **LEGACY and not WebView-safe**. HC-319C does not
silently migrate its browser data because ownership cannot be established. It
must not be used as the Android consumer URL.

### API-only mobile consumer

The new `/mobile` entry point loads only:

- `/style.css`
- `/js/health_vault/mobile_consumer.js`

It does not load the legacy vault, import, timeline, trend, intelligence,
Guardian, alert, dashboard, records, root `app.js`, or service-worker modules.
Clinical values are held in JavaScript memory for the active view and discarded
on logout/reload. No record, measurement, trend, observation, timeline,
document, or PHI payload is written to localStorage, IndexedDB, CacheStorage, or
a browser JSON database.

The only browser persistence is a revocable HC-318 user session in
`sessionStorage`, scoped to the active browsing session. Dashboard presentation
preferences remain in the encrypted server-side user profile and are proven
isolated between users.

No legacy data is automatically migrated. Any future recovery tool must require
an authenticated owner decision and write through normal intake with explicit
provenance; it must never infer an owner from the browser profile.

## API-only clinical flow

```text
/mobile consumer UI
        |
        +-- /api/auth/*
        +-- /api/dashboard/summary
        +-- /api/dashboard/preferences
        +-- /api/records
        +-- /api/records/upload
        |
        v
authenticated Dashboard / Records / Intelligence services
        |
        v
HC-312 intake and HC-311 encrypted VaultStore
```

The mobile UI provides Dashboard, Records, Trends, Observations, Import, and
Settings destinations. It does not calculate intelligence locally and does not
bypass HC-312 for uploads.

## Session and security model

- desktop and mobile use the same HC-318 account registry and password policy;
- first-login password change and 30-day expiry behavior are preserved;
- no password, permanent bearer token, Robert identity, or `00000` value is
  embedded in Android/mobile production logic;
- consumer and companion tokens remain separate by purpose and scope;
- logout removes the browser session and can revoke the paired device;
- release companion networking remains HTTPS-only;
- the API-only mobile document is `Cache-Control: no-store`;
- its Content Security Policy permits only same-origin scripts/styles/API
  connections, forbids objects, base-URL changes, and framing;
- it contains no `file://` access, external URL, popup/navigation call, or
  JavaScript/native bridge.

## Android hybrid responsibilities

Existing native responsibilities remain:

- Health Connect permissions, reading, mapping, and deletion handling;
- WorkManager/background synchronization and retry policy;
- encrypted device credential/cursor/pending-batch state;
- pairing, revocation, account-switch cleanup;
- future notifications and platform integrations.

The consumer document owns only authenticated presentation and API actions. The
final WebView/native navigation shell remains intentionally out of HC-319C scope.

## WebView readiness gate

The URL approved for a future shell is `/mobile`, never `/` or `/index.html`.

| Check | Result |
|---|---|
| Arbitrary navigation absent | PASS |
| `file://` clinical access absent | PASS |
| JS/native bridge absent | PASS |
| External origins absent by default | PASS |
| API origin constrained to same origin by code and CSP | PASS |
| Clinical/API responses excluded from persistent browser stores | PASS |
| Mobile HTML is not cached | PASS |
| Sensitive download implementation | Not exposed in the mobile consumer; future shell must use a reviewed native download boundary |

**WEBVIEW_CONSUMER_UI_READY=YES** for the dedicated `/mobile` document.
The legacy root document remains explicitly `NO` and must be denied by the
future WebView allowlist.

## Tests and validation

### Focused HC-319C and companion compatibility

`python -m pytest tests/test_hc319c_mobile_identity_and_api_only.py tests/test_hc303a_android_companion.py tests/test_hc304b_private_host_foundation.py tests/test_hc306i_r11_absent_cursor_contract.py -q`

Result: **70 passed**.

Coverage includes authenticated binding, generic/missing-identity rejection,
forged identity rejection, cross-user device isolation, logout/revocation,
post-revocation delivery denial, account-switch cleanup, API-only storage
invariants, CSP/cache policy, navigation/bridge restrictions, and server-scoped
preferences.

### Android companion

`android\gradlew.bat :app:testDebugUnitTest --no-daemon`

Result: **BUILD SUCCESSFUL**; all Android debug unit tests passed.

### HC-311 through HC-319 milestone suites

`python -m pytest tests -q -k "hc311 or hc312 or hc313 or hc314 or hc315 or hc316 or hc317 or hc318 or hc319"`

Result: **309 passed, 878 deselected, 5 subtests passed**.

### Full regression

`python -m pytest -q`

Result: **1185 passed, 3 skipped, 5 subtests passed** in 421.31 seconds.
Two third-party deprecation warnings were reported; no regression failed.

### Security scans

- production Android/mobile source contains no hard-coded Robert or `00000`;
- no hard-coded password or bearer credential was introduced;
- companion delivery contains no generic ownership fallback;
- `/mobile` contains no localStorage, IndexedDB, browser clinical vault, cache
  write, external origin, file URL, or JS/native bridge;
- remaining `default-patient` occurrences are classified legacy/test/domain
  defaults outside the authenticated mobile execution path;
- `git diff --check` passed.

## Known remaining work

- Implement the final Android launcher/WebView/navigation shell in a later phase,
  using only the `/mobile` URL and a strict paired-origin allowlist.
- Define the separately reviewed TLS/network-reachable host deployment; do not
  broaden HC-319A's loopback default implicitly.
- Retire the legacy root browser clinical stores in a dedicated compatibility
  phase if the legacy desktop features are no longer required. They are not
  loaded or migrated by the mobile consumer.
- Configure and validate release APK signing before consumer distribution.

## Gate result

- `AUTHENTICATED_MOBILE_USER_BINDING=PASS`
- `DEFAULT_PATIENT_PRODUCTION_FALLBACK=REMOVED`
- `CONSUMER_CLINICAL_STORAGE=API_ONLY`
- `MULTI_USER_ISOLATION=PASS`
- `WEBVIEW_CONSUMER_UI_READY=YES`
- `REGRESSION=PASS`
