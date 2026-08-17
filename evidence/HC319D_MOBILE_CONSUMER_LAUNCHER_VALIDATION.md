# HC-319D Mobile Consumer Launcher Validation

## Gate status

HC-319D implementation, automated validation, and real-device acceptance are
complete. The account owner completed the authenticated journey on the authorized
phone without exposing credentials or PHI to captured evidence. Sanitized ADB
verification confirmed the launcher returned to the HC-318 sign-in boundary
after logout.

`REAL_DEVICE_VALIDATION=PASS`

## Baseline

- Repository: `C:\rasib\source\HealthChecker-HC310E`
- Branch: `hc311-encrypted-vault-at-rest`
- Baseline/local HEAD: `1c0343a`
- Remote branch was verified at the same `1c0343a` commit.
- Existing runtime and HC-313/314 artifacts were preserved.

## Architecture implemented

The existing Android application and package ID
`com.healthchecker.companion` are reused. No second application, account
registry, clinical store, or intelligence engine was created.

`ConsumerLauncherActivity` is now the only exported `MAIN`/`LAUNCHER` activity.
The installed HealthChecker icon opens this native activity, which loads the
HC-319C-approved `/mobile` API-only consumer document. The existing
`CompanionStatusActivity` remains an internal native Settings destination for
pairing, Health Connect permissions, manual synchronization, and WorkManager
status.

The visible product label is `HealthChecker`; existing adaptive launcher icons,
colors, DayNight theme, application ID, Health Connect grants, encrypted
preferences, and WorkManager identity are retained.

## Launch and authentication behavior

The launcher obtains the trusted consumer origin from its dedicated encrypted
consumer-origin preference. Debug builds may use the isolated
`http://127.0.0.1:8766` loopback tunnel. It never falls back to the companion
delivery host, preventing CSS or another application's endpoint configuration
from becoming the consumer origin. Release builds require an explicitly paired
HTTPS origin. The launcher then
loads `<trusted-origin>/mobile`. The page checks its HC-318 session:

- no/invalid session -> Login;
- first-login or expired-password session -> password-change gate;
- full session -> Dashboard by default.

The page uses HC-318 login/session/password-change/logout endpoints and the same
user namespace as desktop. No password, bearer credential, Robert name, or
`00000` identifier is hard-coded in Android production logic.

Mobile logout requests revocation of all companion devices owned by the current
authenticated account, revokes the HC-318 session, then navigates to one exact
same-origin completion path. The native launcher intercepts that path, clears
the encrypted device token, Health Connect cursor/scope, pending batch, and sync
state, and reloads Login. The non-secret consumer host origin is retained so a
new account can sign in and pair afresh without inheriting the prior account's
device identity or queued observations.

## Consumer experience

The API-only document exposes responsive destinations for:

- Dashboard / Health Overview
- Records
- Trends
- Observations
- Import/Add Records
- Settings
- Logout

Dashboard and presentation preferences remain HC-316 server-backed. Records and
upload use HC-317. Trends and observations are rendered from dashboard/
intelligence API results. No Android-side intelligence calculation is added.

## Hybrid security boundary

The native allowlist accepts only the exact configured HealthChecker origin and:

- `/mobile` or `/mobile.html`
- `/style.css`
- `/js/health_vault/mobile_consumer.js`
- `/api/*`
- the exact native logout completion path

It rejects the legacy `/` and `/index.html`, external origins, userinfo attacks,
file URLs, JavaScript URLs, arbitrary paths, and sensitive repository paths.

WebView hardening includes:

- file and content access disabled;
- file-origin and universal file-origin access disabled;
- mixed content forbidden;
- cache mode `LOAD_NO_CACHE` and cache cleared at setup/logout;
- third-party and general WebView cookies disabled;
- database storage disabled;
- no JavaScript/native bridge;
- WebView debugging disabled;
- popups/multiple windows disabled;
- TLS errors cancelled, never bypassed;
- screenshots/recents capture blocked with `FLAG_SECURE`;
- downloads blocked;
- upload restricted to Android's document picker and PDF/JSON/PNG/JPEG MIME
  types;
- navigation outside the trusted origin/path set blocked.

DOM storage is enabled only because HC-318 uses session-scoped
`sessionStorage`. The loaded mobile document contains no localStorage,
IndexedDB, CacheStorage, browser clinical vault, or PHI persistence.

## Native services preserved

Android compilation and the complete unit suite verify that the existing Health
Connect capability/permission/read pipeline, encrypted preferences, pairing,
bounded pending batches, cursor rules, synchronization mutex, and unique
WorkManager background job remain present and compatible. The consumer launcher
does not duplicate those responsibilities.

## Connectivity model

### Supported local development

`scripts/start_healthchecker_android_debug.ps1` requires exactly one authorized
Android device, creates `adb reverse tcp:8766 tcp:8766`, and invokes the HC-319A
loopback-only FastAPI launcher. Android then uses
`http://127.0.0.1:8766` through the authorized USB/wireless-debug tunnel.
The mapping is removed when the launcher exits. No LAN listener or `0.0.0.0`
binding is created.

### Production target

Production uses an explicitly configured HTTPS origin with a system-trusted
certificate and the existing pairing process. Release builds reject cleartext.
Host deployment/TLS exposure is a separate reviewed operational concern; the
Android launcher performs no discovery, LAN scan, or fallback origin selection.

## Automated validation

### HC-319C/HC-319D focused tests

Result: **15 passed**.

Coverage includes existing package/launcher reuse, manifest identity, WebView
hardening, exact origin/path policy, mobile logout/revocation, required consumer
destinations/APIs, absence of browser-local clinical persistence, and safe ADB
reverse connectivity.

### Android unit tests

`android\gradlew.bat :app:testDebugUnitTest --no-daemon`

Result: **BUILD SUCCESSFUL**. This includes the new origin-policy tests and all
existing Health Connect, WorkManager, sync, secure-store, privacy, and UI tests.

### Android lint

`android\gradlew.bat :app:lintDebug --no-daemon`

Result: **BUILD SUCCESSFUL**.

### APK build

`android\gradlew.bat :app:assembleDebug --no-daemon`

Result: **BUILD SUCCESSFUL**.

- APK: `android/app/build/outputs/apk/debug/app-debug.apk`
- Size: 23,113,236 bytes
- SHA-256: `EE18D0ECF954ECFE518293735EF9C42813A23E7606B2E12C0915FBFDFD4AE7B6`

### HC-316 through HC-319 compatibility

Result: **48 passed, 1146 deselected**.

### Full Python regression

Result: **1191 passed, 3 skipped, 5 subtests passed** in 421.23 seconds.
Two third-party deprecation warnings were reported; no test failed.

### Quality/security checks

- `git diff --check`: PASS
- production mobile hard-coded Robert/`00000`/temporary password scan: PASS
- unrestricted WebView/JS bridge/file access scan: PASS
- forbidden mobile local clinical persistence scan: PASS
- unrestricted `0.0.0.0` launcher scan: PASS

## Real-device validation

Managed ADB used:

`C:\ProgramData\HealthChecker\tools\android\platform-tools\37.0.1\adb.exe`

Results:

- Authorized device: serial `R3CX305A7DV`, Samsung `SM-S928W`, ADB state `device`.
- Existing package `com.healthchecker.companion` was upgraded in place with
  `adb install -r`; the preserved original signing certificate was used.
- First-install time remained `2026-07-27`; update time advanced. No uninstall
  or application-data clear occurred.
- The app-icon resolver selects `.ui.ConsumerLauncherActivity`, and an icon-style
  launcher intent started that activity successfully.
- Health Connect remained installed and all previously granted supported read
  permissions, including background read, remained granted.
- Android JobScheduler retained the package's WorkManager SystemJobService job.
- HealthChecker `/mobile` returned HTTP 200 on dedicated loopback port 8766.
- A conflicting service on port 8000 returned a different response. The defect
  that allowed legacy-host fallback was corrected; the final ADB reverse list
  contains only HealthChecker `tcp:8766`.
- WebView source/unit validation confirms exact-origin `/mobile` and API-only
  navigation, blocked external/file navigation, no JS/native bridge, and no
  browser-local clinical persistence.
- The account owner interactively completed login/the applicable password gate,
  Dashboard, Records, Trends, Observations, Import, Settings, account isolation,
  and Logout on the live phone. No credentials or PHI were captured.
- Post-journey ADB verification found `.ui.ConsumerLauncherActivity` foregrounded
  at the HealthChecker HC-318 Sign in/User ID/Password boundary, confirming the
  authenticated consumer session was no longer presented after logout.

## Release decision

The connected-device, consumer-journey, security-boundary, service-preservation,
and CSS/HealthChecker isolation gates pass. HC-319D is approved for exact-file
staging, commit, and push. Runtime state, vault content, credentials, tokens,
scratch files, and HC-313/314 artifacts remain excluded.

Current gate summary:

- `EXISTING_ANDROID_APP_REUSED=YES`
- `MOBILE_LAUNCHER=PASS` (automated/build)
- `AUTHENTICATION=PASS` (automated)
- `USER_ISOLATION=PASS` (automated)
- `CONSUMER_DASHBOARD=PASS` (API/contract automated)
- `RECORDS=PASS` (API/contract automated)
- `TRENDS_OBSERVATIONS=PASS` (API/contract automated)
- `HEALTH_CONNECT=PASS` (unit/regression)
- `WORKMANAGER=PASS` (unit/regression)
- `WEBVIEW_SECURITY=PASS` (unit/static/lint)
- `APK_BUILD=PASS`
- `REAL_DEVICE_VALIDATION=PASS`
- `REGRESSION=PASS`
- `REMOTE_SYNC=CONFIRMED`
- `CSS_HEALTHCHECKER_CONCURRENT_ISOLATION=PASS`
