# HealthChecker Android Production Release Signing Runbook

Status: production release operations (originated at P0-04 / HC321-B3)  
Current Android candidate: `versionCode=341`, `versionName=0.341.0`  
Installed S24 UAT baseline: `versionCode=334`, `versionName=0.334.0`  
Owner role: `RELEASE/SIGNING OWNER — ASSIGN BEFORE EXTERNAL PRODUCTION HANDOFF`

## Governing rules

- Production signing identity is **organization-controlled** (org-controlled), not developer-local.
- Keystore, private key, and passwords live **outside Git** under org key custody.
- Approved injection interface (only):
  - `HC_ANDROID_KEYSTORE_FILE` — absolute path to governed keystore (outside repo)
  - `HC_ANDROID_KEYSTORE_PASSWORD`
  - `HC_ANDROID_KEY_ALIAS`
  - `HC_ANDROID_KEY_PASSWORD`
  - `HC_ANDROID_EXPECTED_CERT_SHA256` — approved SHA-256 certificate fingerprint
    for the signer already installed on the authorized S24 (hex, separators optional)
- Optional fail-closed gate for distribution builds:
  - `HC_ANDROID_REQUIRE_PRODUCTION_SIGNING=1` — Gradle refuses release when material is missing
  - Enabled values are `1`, `true`, `yes`, or `on` (case-insensitive); `0`, `false`,
    `no`, `off`, an empty value, or an unset variable are disabled consistently in
    Gradle and provenance.
- **Never** use the Android debug keystore as a release fallback.
- **Never** commit keystore files, private keys, passwords, or signing secrets.
- **Never** generate an ad-hoc production keystore merely to make a gate green.
- Do not print passwords, key material, or secret values in logs or evidence.

## Build a signed production AAB

From `android/` with governed env injected into the protected build shell:

```powershell
$env:HC_ANDROID_REQUIRE_PRODUCTION_SIGNING = "1"
# Also set HC_ANDROID_KEYSTORE_FILE / _PASSWORD / _KEY_ALIAS / _KEY_PASSWORD
# and HC_ANDROID_EXPECTED_CERT_SHA256 from the approved external trust record.
.\gradlew.bat :app:bundleRelease
```

Expected output (typical):

`android/app/build/outputs/bundle/release/app-release.aab`

## Verify signing (non-secret)

```powershell
jarsigner -verify -verbose -certs android\app\build\outputs\bundle\release\app-release.aab
# and/or apksigner / bundletool as available on the build host
```

Record only: verify status, certificate subject/fingerprint (SHA-256), artifact SHA-256.
Never record passwords or private key bytes. The provenance step normalizes the
`apksigner` SHA-256 digest and compares it with
`HC_ANDROID_EXPECTED_CERT_SHA256`. With production signing required, a missing,
malformed, unavailable, or mismatched fingerprint fails closed.

AAB verification and APK verification are separate evidence. Use `jarsigner` for the AAB.
For the installable APK, `apksigner verify --verbose --print-certs` is mandatory so Android
signature schemes are checked; an AAB's `jarsigner` result must never be used as proof
that the APK is signed.

## Release APK for device acceptance

When a device install/upgrade drill is required:

```powershell
.\gradlew.bat :app:assembleRelease
```

Install/upgrade on the acceptance device **without uninstalling** and **without deleting
user app data** merely to obtain a clean result. Confirm the package upgrades under the
same signing identity (`com.healthchecker.companion`) and that local user data remains.

## VersionCode monotonicity

- Governed historical baseline: `320` / `0.320.0`
- Installed S24 UAT baseline: `334` / `0.334.0`
- Current candidate: `341` / `0.341.0`
- Every production release must increase `versionCode` monotonically above the
  installed baseline. Never reuse a prior `versionCode` with a different signing identity.

## Key loss / rotation / recovery

- Lost production keystore or passwords: Play / sideload upgrade path for this signing
  identity is effectively ended for existing installs. Treat as incident.
- Rotation: only via org-approved Play App Signing / dual-signing policy if adopted;
  do not quietly swap to a new local key for the same applicationId.
- Recovery owner: `RELEASE/SIGNING OWNER — ASSIGN BEFORE EXTERNAL PRODUCTION HANDOFF`

## Provenance / evidence

After each governed release build, run:

`scripts/Write-HealthCheckerAndroidReleaseProvenance.ps1`

Retain machine-readable JSON + concise operator text. Fields include HC release version,
Android versionCode/versionName, Git SHA, build timestamp, AAB/APK names + SHA-256,
artifact-specific signing verification status, cert fingerprint when safely obtainable,
Gradle/tool info, whether production signing was available, and whether device upgrade
proof completed.

The provenance script refuses `DeviceUpgradeProof=PASS` unless the APK is present in the
same run, independently reports `SIGNED_VERIFIED` from `apksigner`, and its certificate
SHA-256 matches the approved fingerprint. This guard does not replace physical S24
evidence; it prevents an unsigned, AAB-only, or wrong-signer artifact from being recorded
as the upgraded candidate.

Never include passwords, keystore bytes, or private material in provenance.

## Unsigned / missing-key builds

Without `HC_ANDROID_*` material (and without REQUIRE), Gradle may produce an **unsigned**
release artifact for engineering validation. That is **not** a production-signed release.
Classify: `ANDROID_PRODUCTION_SIGNING=BLOCKED_EXTERNAL_KEY_CUSTODY` until the owner
injects governed credentials and verification + device upgrade proof complete.

## Forbidden

- Debug-key production releases
- Repo-local production keystores
- Hard-coded passwords in Gradle, scripts, docs, or CI logs
- Treating “validly signed” as proof of signer continuity without fingerprint matching
- Committing `.jks` / `.keystore` / password files
