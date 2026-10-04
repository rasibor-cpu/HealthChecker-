# HealthChecker Android companion

Application ID: `com.healthchecker.companion`

## Versions

| Line | versionCode | versionName |
|------|-------------|-------------|
| Prior governed baseline (HC320D) | 320 | 0.320.0 |
| Prior signing baseline (HC321-B3) | 321 | 0.321.0 |
| Installed S24 UAT candidate (HC334) | 334 | 0.334.0 |
| Corrected consumer UX candidate (HC352 remediation) | 344 | 0.344.0 |
| Secure lifecycle restoration and optional 2FA | 345 | 0.345.0 |

The authoritative current Android version is declared in `app/build.gradle.kts`.
Desktop release metadata (`config/healthchecker.release.json`) is independent and already at `0.321.0`.

## Secure consumer return and optional 2FA

The mobile consumer can save only an allowlisted section and an opaque record ID in Android encrypted preferences; it does not restore WebView history or persist clinical measurements. Fast return is opt-in, requires an Android system biometric or device-credential prompt, and uses a server-revocable credential that rotates on use and expires after 30 days. Password sign-in remains available.

Authenticator-app TOTP is optional and is enrolled from consumer Settings after current-password confirmation. Ten single-use recovery codes are shown once. Enabling, disabling, or replacing factors revokes existing sessions and trusted-device credentials. Ordinary consumer screens remain screenshot-capable; password/recovery and 2FA secret screens remain protected by the route-specific screenshot policy.

## Debug (local only)

See `scripts/start_healthchecker_android_debug.ps1`. Debug builds must never be distributed as production.

## Production release signing

Governed runbook (env injection, fail-closed rules, verification, device upgrade, key loss):

[`docs/ops/HC321_B3_ANDROID_PRODUCTION_SIGNING_RUNBOOK.md`](../docs/ops/HC321_B3_ANDROID_PRODUCTION_SIGNING_RUNBOOK.md)

Approved env interface only:

- `HC_ANDROID_KEYSTORE_FILE`
- `HC_ANDROID_KEYSTORE_PASSWORD`
- `HC_ANDROID_KEY_ALIAS`
- `HC_ANDROID_KEY_PASSWORD`
- optional: `HC_ANDROID_REQUIRE_PRODUCTION_SIGNING=1`

Never commit keystores or passwords. Never use the debug keystore for release.

## Provenance

```powershell
..\scripts\Write-HealthCheckerAndroidReleaseProvenance.ps1
```

Writes non-secret JSON + text under an operator-chosen evidence directory (default outside source tree when possible).

## Current version

Android versionCode 346 / versionName 0.346.0 (JavaBridge thread-safety fix; remembered User ID).
