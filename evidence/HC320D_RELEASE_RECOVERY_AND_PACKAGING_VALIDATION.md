# HC-320D Release, Recovery and Packaging Validation

Date: 2026-08-17
Branch: `hc311-encrypted-vault-at-rest`
Baseline: `fa465ffdf772878a57f79ede680a21f4126409f7`

## Outcome

`ENGINEERING_COMPLETE=YES`

`EXTERNAL_ACTIVATION_REQUIRED=YES`

HC-320D engineering is complete. HealthChecker is not declared production
ready. Production Android signing identity activation remains external, and the
separate HC-320C production TLS activation remains pending.

## Change ownership

The following existing uncommitted files remain classified
`HC320C_PENDING_TLS` and are not part of HC-320D:

- `config/healthchecker.production.example.json`
- `scripts/start_healthchecker_production.ps1`
- `tests/test_hc320c_production_runtime_connectivity.py`
- `evidence/HC320C_PRODUCTION_RUNTIME_AND_CONNECTIVITY_VALIDATION.md`

HC-313/314 state, completed intake files, logs, scratch files, HC-317 evidence,
and the untracked HC-320A audit are also excluded.

## Backup and recovery

- Versioned `hc.backup.v1` archive.
- The archive is encrypted and authenticated with an externally protected
  recovery key.
- ZIP assembly/decryption occurs in memory; plaintext PHI is never staged on
  disk.
- Every encrypted source file is hashed and checked before restoration.
- Vault index, encrypted authentication registry, user profiles/preferences,
  records/provenance/intelligence, and companion metadata are captured from the
  authoritative vault root.
- Vault keys, TLS private keys, Android signing material, logs, PID state, and
  incomplete temporary files are excluded.
- Creation uses temporary-file plus atomic replace.
- Restore validates the entire archive before creating an isolated staged tree,
  authenticates that tree with the vault key, then atomically promotes it.
- Existing target state is retained and restored if promotion/post-validation
  fails.
- Tests preserve Robert `00000`, a secondary user, their separate profiles and
  preferences, and the encrypted account registry.
- Wrong recovery key, wrong vault key, truncated/corrupt backup, incomplete
  archive, and unsafe archive paths fail closed.

## Upgrade, migration and rollback

- Authoritative release metadata: `hc.release.v1`, version `0.320.0`.
- Authoritative vault schema: `hc.health_vault.v1`.
- Unknown or newer schemas are rejected rather than silently opened.
- Migration paths are explicit source-to-target contracts.
- A checkpoint is mandatory before mutation.
- Migration occurs on an in-memory copy and uses the vault's encrypted atomic
  index write.
- Contract/post-check failure restores the original encrypted index.
- Downgrade is blocked unless release and schema compatibility are explicitly
  established.

## Desktop packaging

- Allowlisted, deterministic source selection excludes tests, evidence,
  runtime state, vaults, intake files, logs, caches, and secrets.
- Package contains version metadata and a SHA-256 file manifest.
- A final temporary package was generated outside the repository: 151 files; no
  test, cache, key, or log artifacts were found.
- Installer verifies every manifest entry before installation.
- Managed Python runtime presence is required.
- Program Files application content and ProgramData data/config/secrets/logs
  are separated.
- Upgrade uses next/previous directories and restores the prior application on
  promotion failure.
- Common desktop shortcut launches HealthChecker without requiring the consumer
  to open PowerShell.
- User health data is never silently removed by install/uninstall behavior.

## Android packaging and signing

- Application ID remains `com.healthchecker.companion`.
- Version advanced monotonically to code `320`, name `0.320.0`.
- min SDK 28, target SDK 35, Health Connect declarations, WorkManager, backup
  prohibition, and release cleartext prohibition remain intact.
- `testDebugUnitTest`, `lintDebug`, and `bundleRelease`: BUILD SUCCESSFUL.
- Release AAB was produced with shrinking. With no signing environment present,
  `jarsigner -verify` reported `jar is unsigned`; there is no debug-signing
  fallback.
- Production signing consumes only four external environment values. No
  keystore, password, private key, or secret property is committed.

Production signing activation requires an organization-controlled Android
keystore, alias, and passwords supplied by the protected build environment:

- `HC_ANDROID_KEYSTORE_FILE`
- `HC_ANDROID_KEYSTORE_PASSWORD`
- `HC_ANDROID_KEY_ALIAS`
- `HC_ANDROID_KEY_PASSWORD`

## Validation results

- HC-320D focused: **7 passed**.
- HC-311 through HC-320 focused: **354 passed**, 878 deselected, 5 subtests
  passed.
- Full regression: **1229 passed, 3 skipped, 5 subtests passed** in 432.00s.
- Android unit/lint/release AAB: **BUILD SUCCESSFUL**.
- Desktop package generation and manifest creation: PASS.
- `git diff --check`: PASS; exact HC-320D scope verified separately from
  HC-320C and runtime artifacts.

## Gates

`ENCRYPTED_BACKUP=PASS`

`RESTORE=PASS`

`DISASTER_RECOVERY=PASS`

`UPGRADE_MIGRATION=PASS`

`ROLLBACK_RECOVERY=PASS`

`DESKTOP_PACKAGING=PASS`

`ANDROID_RELEASE_BUILD=PASS`

`ANDROID_PRODUCTION_SIGNING=BLOCKED`

`REGRESSION=PASS`

External dependencies:

1. Protected production Android signing identity and governed key custody.
2. HC-320C approved routable HTTPS host identity and protected TLS material.
3. Final installed-system clean-install/upgrade and signed-artifact acceptance
   after those identities are provisioned.

`RESULT=HC320D_RELEASE_RECOVERY_PACKAGING_PASS`
