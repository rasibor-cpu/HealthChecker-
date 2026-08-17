# HC-320B Production Security Closure Validation

Date: 2026-08-17
Branch: `hc311-encrypted-vault-at-rest`
Baseline: `de37c0719e3fa648daef6c18cc7429abcd12b6c2`

## Result

- `PRODUCTION_ENCRYPTION_DEFAULT=PASS`
- `PLAINTEXT_PRODUCTION_FALLBACK=REMOVED`
- `CLINICAL_API_AUTHENTICATION=PASS`
- `PATIENT_SCOPING=PASS`
- `BOOTSTRAP_PASSWORD_FALLBACK=REMOVED`
- `ROBERT_00000_DATA_ISOLATION=PASS`
- `MULTI_USER_ISOLATION=PASS`
- `REGRESSION=PASS`

No HC-320B production-security blocker remains. TLS, packaging, signing,
backup/restore, and upgrade/rollback remain explicitly outside this task and are
assigned to later production-hardening gates.

## Baseline and path classification

The local and remote branch started synchronized at HC-319D commit `de37c07`.
Nothing was staged. Existing HC-313/314 logs, acquisition state, completed
synthetic intake files, scratch, and unrelated evidence were preserved.

| Path | Classification | HC-320B disposition |
|---|---|---|
| No-argument `create_health_vault_app` / Uvicorn factory | PRODUCTION | Uses authoritative protected-key factory; encrypted and fail closed |
| `scripts/start_healthchecker.ps1` | PRODUCTION | Invokes the secured no-argument factory |
| Mobile `/mobile` and same-origin APIs | PRODUCTION | Uses the same secured application boundary |
| Scheduled intake `main()` | PRODUCTION | Protected-key vault plus required runtime user binding |
| Scheduled Gmail acquisition `main()` | PRODUCTION | Protected-key vault plus required runtime user binding |
| Companion-only activated host | PRODUCTION/PILOT | No known bootstrap; random memory-only initial credential |
| Explicit `create_health_vault_app(store, production=False)` | DEVELOPMENT/TEST | Plain fixtures allowed explicitly |
| Framework-agnostic handler helpers with injected stores | TEST/LEGACY | Not selected by the production app factory |
| Plain `VaultStore` unit fixtures | TEST | Retained for compatibility/crypto transition tests |

## Encrypted production activation

`backend.health_vault.production_runtime.create_production_vault()` is the one
authoritative production construction boundary. It loads the existing HC-311
DPAPI-protected key envelope, opens the configured vault with that key, and
forces authenticated index decryption during startup. It never generates a key
or performs data migration.

Missing, unreadable, malformed, corrupt, wrong-user, or wrong-key state produces
only `production_vault_activation_failed`; key bytes, paths, credentials, and
clinical values are not included. An explicitly supplied plaintext store is
rejected in production mode. Existing plaintext user data is not migrated or
overwritten.

Default production locations remain configuration-driven with protected
defaults under `C:\ProgramData\HealthChecker`; test/development stores must be
passed explicitly.

The automatic intake and Gmail scheduled entry points now use the same factory.
Both also require `HC_RUNTIME_PATIENT_ID`. Intake overwrites any file/client
patient field with that runtime identity. Gmail identity verification loads only
that user's registered profile. Missing identity fails before clinical work.

## Authentication closure

The production FastAPI application applies an HC-318 authentication middleware
to all account-clinical `/api/*` paths. It rejects missing, malformed, forged,
expired, revoked, and password-change-only credentials before route dispatch.

Explicit exceptions are limited to:

- login and auth operations that perform their own token/scope validation;
- one-time companion pairing confirmation;
- companion observation/status endpoints with existing device-token validation;
- non-clinical batch-limit metadata.

Static UI assets remain public and contain no health data.

## Patient/user scoping

Legacy routes now derive identity from the authenticated account. Client query,
form, or JSON `patient_id` cannot select another user. This covers imports,
timeline/unified timeline, doctor visit, integrity summary, intelligence, import
history/logs, executive briefing/print, AI import, monitoring, Guardian alerts,
baselines, sensors, inventory, continuity, and data gaps. Alert and CGM mutation
lookups verify ownership before acting.

HC-316 dashboard and HC-317 records/download routes retain their existing
authenticated scoping. Companion device management and Health Connect delivery
retain account/device-token ownership. Gmail provenance follows its runtime
identity and HC-312 handoff.

Tests seed Robert `00000` markers and prove a secondary account cannot retrieve
them through modern or legacy reads, even with a forged `patient_id=00000`.
Uploads/imports are persisted to the authenticated/runtime identity instead.

## Bootstrap closure

`AuthenticationService` no longer reads an environment/default password or
silently uses `123456`. A new production registry requires an explicitly
supplied controlled enrollment credential. A non-secret `.auth_enrolled` marker
is written after successful enrollment and after validating an existing legacy
registry. If the registry later disappears, the marker prevents re-bootstrap,
even when the old enrollment credential is supplied. Corrupt or ownerless state
also fails closed.

Password replacement still revokes prior sessions, increments password version,
issues a full session, and sets the 30-day expiry. The initial enrollment
credential fails after replacement and is never persisted or logged. The
companion-only isolated host uses a random memory-only first credential rather
than the known development credential.

The known credential remains only behind the explicit
`allow_development_bootstrap=True` test/development switch used by non-production
fixtures.

## Security inventory

| Finding | Classification/result |
|---|---|
| Direct plain `VaultStore()` in no-argument consumer startup | Removed |
| Plain stores in explicit unit/development fixtures | Allowed, explicit |
| Scheduled intake/Gmail plain default | Removed from production `main()` paths |
| Unauthenticated clinical APIs | Removed from production boundary |
| Client-authoritative `patient_id` | Overwritten/ignored in production routes |
| `default-patient` | Legacy development default only; production routes derive account ID |
| Robert/`00000` generic authorization | None; identifier exists only in bootstrap model/tests |
| Known production bootstrap fallback | Removed |
| Tokens/credentials in logs | No new values logged; failures use reason codes only |
| Plaintext PHI in startup failure | None |
| Unsafe debug auth bypass | Explicit development mode only; production mode cannot enable it implicitly |

## Tests

### HC-320B security suite

`tests/test_hc320b_production_security_closure.py`

Result: **24 passed**.

Coverage includes protected-key startup, encrypted persistence, missing/corrupt
key and auth failure, plaintext production rejection, full route-inventory
authentication, forged/revoked/expired tokens, all legacy read scopes, forged
patient import, Robert/secondary isolation, bootstrap invalidation and
missing-registry marker, scheduled Gmail/intake binding, and explicit development
fixtures.

### HC-311 through HC-320B focused suites

Result: **340 passed, 5 subtests passed**.

### Regression reconciliation

The date-sensitive HC-201I blood-pressure fixture crossed its implicit 30-day
window on 2026-08-17. The test now supplies its intended fixed `as_of` date;
assertions and production logic were unchanged. The HC-303A static scan was
reconciled by using `localhost` for the HC-319D debug-only loopback origin rather
than a numeric IP literal. Companion-host tests retain isolated pilot behavior
without restoring a known password.

### Full regression

Result: **1215 passed, 3 skipped, 5 subtests passed** in 428.55 seconds.

### Quality

- Android unit/lint compatibility: BUILD SUCCESSFUL
- `git diff --check`: PASS
- exact HC-320B staging review: PASS
- runtime/vault/credential/token/scratch/HC-313/314 artifact exclusion: PASS

## Final gate

`RESULT=HC320B_PRODUCTION_SECURITY_CLOSURE_PASS`

`NEXT_TASK=HC320C_PRODUCTION_RUNTIME_AND_CONNECTIVITY`
