# HC-319B Mobile Consumer Launcher / Existing Android Framework Review

## Decision

Use **option C: a hybrid approach**.

Retain the existing native Android application, Health Connect integration,
encrypted companion credentials, and WorkManager synchronization. Add a consumer
launcher surface that renders only the server-backed HealthChecker consumer UI
and authenticates through HC-318. Keep Health Connect permission, pairing, sync,
and diagnostic controls native under Settings.

A WebView around the current root page without further boundary work is **not
safe**. The page still loads legacy JavaScript modules that store clinical data
in browser `localStorage` and IndexedDB (`HCHealthVault`). Using that page as-is
would create a second mobile health repository and violate VaultStore authority.
The mobile consumer surface must be API-only before it is embedded.

## Existing Android framework inventory

### Application and build

| Item | Finding |
|---|---|
| Gradle project | `android/`, one `:app` application module |
| Namespace / application ID | `com.healthchecker.companion` |
| App name | `HealthChecker+ Companion` |
| Launcher | `.ui.CompanionStatusActivity` |
| SDK levels | minSdk 28, target/compileSdk 35 |
| Toolchain | Android Gradle Plugin 8.5.2, Kotlin 1.9.24, Gradle 8.7, Java 17 |
| Version | versionCode 1, versionName `hc303b.1.0.0` |
| UI framework | Android Views/ViewBinding with Material Components; no Compose |
| Outputs | Ignored local debug APKs exist under `android/app/build/outputs/apk/debug`; no release signing configuration is committed |

The existing package ID should be preserved to retain install identity,
encrypted preferences, granted Health Connect permissions, and WorkManager
state. Renaming it would behave like a separate application and risk a parallel
identity/data path.

### Branding and navigation

The application already has HealthChecker navy/blue/teal color tokens, a
DayNight Material theme, adaptive round/standard launcher icons, and a branded
pulse/HC+ foreground. Branding is aligned, although the visible app label and
status title still say “Companion.”

Navigation is currently a single native, scrollable companion-status screen.
It contains host/pairing inputs, Health Connect permission management, manual
sync, and WorkManager schedule controls. There is no consumer bottom navigation,
navigation graph, dashboard landing activity, or native Records/Trends/
Observations/Import experience.

### Health Connect and background runtime

The existing implementation is substantial and should be reused:

- Health Connect availability and permission capability checks
- readers/mappers for heart rate, resting heart rate, oxygen saturation, blood
  pressure, sleep, steps, exercise, and weight
- partial-permission and background-read policy handling
- incremental changes cursor with token-scope validation
- deletion tombstones and bounded, durable pending batches
- manual and periodic delivery sharing a synchronization mutex
- unique WorkManager job `hc303a_monitoring_sync`, minimum 15-minute cadence,
  connected-network and battery-not-low constraints, and bounded backoff
- durable host acknowledgements before cursor advancement
- privacy-safe logging and redaction

This native subsystem should remain independent of consumer-screen lifecycle.
Opening, closing, or signing out of the dashboard must not corrupt pending sync
batches or silently change Health Connect permissions.

### Connectivity and device pairing

`HostClient` uses OkHttp and the existing host origin for:

- `POST /api/companion/pair/confirm`
- `POST /api/companion/observations`

The device credential is a distinct, limited bearer token with the
`health_connect.observations` scope. Active host, device ID, token, cursor,
pending batches, and sync state are held in Android Keystore-backed
`EncryptedSharedPreferences`. The host stores only token hashes. Draft host
changes do not replace active delivery credentials until pairing succeeds.

Release traffic requires HTTPS and system trust. Debug builds can explicitly
allow private/local cleartext hosts. The HC-319A desktop launcher binds to
`127.0.0.1`; that address is the Android device itself and is not reachable from
a physical phone. A mobile client therefore needs an explicitly configured,
secure, network-reachable HealthChecker host. The desktop launcher must not be
silently broadened to `0.0.0.0`; remote access requires a separately reviewed
TLS/network deployment mode.

## Authentication and isolation findings

### Consumer authentication

The Android app has **no HC-318 user login/session implementation**. Its device
pairing token is not a user-session token and must never be upgraded or reused
for dashboard/records access.

The web consumer UI already uses same-origin HC-318 endpoints:

- `POST /api/auth/login`
- `GET /api/auth/session`
- `POST /api/auth/password/change`
- `POST /api/auth/logout`

It keeps the user token in browser `sessionStorage` and sends it as a bearer
token. Dashboard and records endpoints derive patient scope from the resolved
server session rather than caller-provided patient IDs. This is the appropriate
consumer identity model for mobile as well.

### Blocking identity gap in companion pairing

The current pairing service binds every pairing session and companion device to
the literal patient ID `default-patient`. HC-318 accounts, including owner
`00000`, are user-scoped. Consequently, the existing companion delivery identity
is not yet safely linked to the authenticated consumer account.

Before mobile consumer release, pairing initiation must require a full HC-318
user session and bind the pairing session to that server-resolved user ID.
Confirmation must copy the patient ID from the consumed pairing session, never
from the Android request. Existing injection rejection in delivery should remain.
A migration decision is required for legacy `default-patient` devices; they must
not be automatically assigned to Robert or another user.

### Duplicate-storage gap in the current webpage

Although HC-316/317 consumer dashboard and records functions call server APIs,
the root page also loads legacy vault/import/trend/guardian modules. Those modules
use `localStorage` and IndexedDB for documents, measurements, profiles, alerts,
and other health state. A WebView gives those stores a new device-local instance.

Therefore the mobile shell must load an API-only consumer entry point or the root
consumer page must first be separated from/disable the legacy client vault paths.
WebView cache may contain static app assets, but it must not become clinical
system-of-record storage. VaultStore remains authoritative.

## Architecture options

### A. Fully native consumer UI

This offers the strongest platform integration and precise credential control,
but requires new screens, navigation, API models, upload handling, trends and
observation rendering, accessibility work, and duplicated presentation logic.
It is not the minimum change and creates long-term desktop/mobile UI drift.

### B. WebView around the current consumer UI

This is the smallest visual implementation and naturally reuses HC-318 and the
relative `/api/*` contracts. It is not acceptable unchanged because of the
legacy browser health stores, broad mixed-purpose navigation, and the absence of
a reviewed WebView origin/navigation policy.

### C. Hybrid — recommended

Use a small native launcher/navigation shell with:

1. an API-only consumer WebView for Dashboard, Records, Trends, Observations,
   and Import;
2. the existing native companion status capability as Settings/Health Connect;
3. the existing WorkManager/Health Connect pipeline unchanged.

This reuses the consumer UI and APIs while preserving Android-native permission
and background behavior. It avoids duplicate business rules and storage when
the consumer entry point is constrained to server-backed features.

## Target mobile experience

The launcher should land on **Dashboard** after a valid HC-318 session, or Login
otherwise. Primary destinations should be:

- Dashboard
- Records (including record detail)
- Trends
- Observations
- Import (HC-317B upload, therefore HC-312 intake)
- Settings (account/logout plus native pairing, Health Connect permissions, sync
  state, manual sync, and background schedule)

The current web dashboard already presents record summaries, trends,
observations, preferences, records upload/detail, password change, and logout.
It needs a mobile-specific API-only navigation surface rather than exposure of
legacy Add/Symptoms/Health Vault/Guardian/Reports tabs that still depend on
browser-local stores.

## Minimum safe implementation changes

1. **Server identity binding prerequisite**
   - Protect pairing-start with full HC-318 authentication.
   - Resolve the user ID from the session and persist it on the one-time pairing
     session.
   - Copy that persisted ID into the confirmed companion device.
   - Never accept patient identity from Android payloads.
   - Quarantine or require explicit re-pairing for legacy `default-patient`
     devices; do not auto-migrate them.

2. **API-only mobile consumer entry point**
   - Split or gate the web consumer shell so mobile loads no clinical
     localStorage/IndexedDB vault modules.
   - Reuse HC-316/317 APIs and UI components for Dashboard, Records, Trends,
     Observations, and Import.
   - Keep dashboard as the post-login landing destination.

3. **Native launcher shell**
   - Preserve package ID and replace/redirect the MAIN launcher to a small
     consumer activity.
   - Retain `CompanionStatusActivity` as the native Settings/Health Connect
     destination.
   - Use existing theme and icons; rename visible product labels from Companion
     to HealthChecker while accurately labeling the Health Connect settings area.

4. **Hardened WebView boundary**
   - Permit only the explicitly paired/configured HTTPS HealthChecker origin.
   - Disable file/content access, mixed content, arbitrary redirects, popups,
     unsafe downloads, and debugging in release.
   - Do not add a JavaScript bridge for credentials or clinical data.
   - Let the consumer page perform HC-318 login; keep its user token separate
     from the native device token.
   - Clear consumer WebView session/cookies/site data on logout as defined by the
     session policy, without deleting native pending Health Connect batches.
   - Use Android's document picker only as a narrowly scoped enhancement if
     WebView file selection proves insufficient; upload must still call
     `/api/records/upload`.

5. **Secure host reachability**
   - Add an explicit reviewed configuration flow for a TLS-reachable host.
   - Do not change the HC-319A loopback-only default or enable general cleartext
     release traffic.
   - Provide visible offline/unreachable-host states and never fall back to a
     different origin.

6. **Tests before implementation release**
   - authenticated/expired/first-password-change/logout WebView journeys;
   - forged origin, redirect, TLS, and file-access rejection;
   - Robert/secondary-user record, trend, observation, preference, and companion
     ingestion isolation;
   - pairing session user binding and legacy-device non-migration;
   - absence of mobile clinical data in WebView localStorage/IndexedDB;
   - Health Connect permission and WorkManager regression;
   - upload through HC-317B and HC-312 into the authoritative encrypted vault;
   - configuration-change/process-death and offline behavior;
   - signed release APK validation after release signing is configured.

## Data-boundary preservation

The recommended design does not duplicate authentication or document storage:

- HC-318 owns user credentials and consumer sessions.
- Companion device tokens remain separate, revocable, ingestion-only credentials.
- HC-311 VaultStore remains the clinical source of truth.
- HC-312 remains the document intake path.
- HC-313 provenance remains server-side and record-linked.
- HC-314 remains the autonomous host runtime.
- Android retains only encrypted operational pairing/sync state and bounded
  in-flight Health Connect delivery batches, not a second consumer health vault.

## Review conclusion

The existing Android project is the correct framework to evolve; no new Android
application should be created. The minimum safe direction is a hardened hybrid
launcher, but implementation must first close two blockers: authenticated
user-bound companion pairing and removal/separation of browser-local clinical
storage from the mobile consumer entry point. After those boundaries are fixed,
the existing native Health Connect and background synchronization code can be
preserved almost unchanged.

RESULT=HC319B_MOBILE_REVIEW_COMPLETE
