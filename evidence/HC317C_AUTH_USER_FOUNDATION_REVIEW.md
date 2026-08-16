# HC-317C Authentication and User Isolation Foundation Review

Date: 2026-08-16

Branch reviewed: `hc311-encrypted-vault-at-rest`
Reviewed baseline: `a619fffaa957820988f5526d6c8a8d6dcb1b3e0f` plus the uncommitted HC-317C UI work

## Decision

The current HC-316/HC-317 authentication boundary is adequate only as a development scaffold. Patient-scoped dashboard and records queries correctly derive their patient identity from a server-issued bearer token, but there is no authoritative user registry, password hashing, first-login change, password expiry, or durable account lifecycle. The requested Robert Asibor bootstrap account must **not** be added using the current login implementation.

HC-317C should remain uncommitted until the small authentication foundation below is implemented and its security tests pass. Existing clinical documents must not be reassigned as part of that work.

## Current architecture findings

### What already supports the requirement

- `POST /api/auth/login` issues an opaque, cryptographically random bearer token.
- HC-316 dashboard endpoints resolve `patient_id` from that server-side token rather than accepting a patient selector from the client.
- HC-317 list, detail, upload and download endpoints use the authenticated patient identity.
- HC-317 upload ignores identity inside uploaded content and binds intake to the authenticated patient.
- `RecordsService` filters documents and linked measurements, timeline entries, trends and observations by patient/document scope.
- Cross-patient record detail and download return `404`, avoiding confirmation that another user's document exists.
- HC-311 can encrypt the VaultStore index and document payloads at rest.
- The HC-317C browser client reuses the HC-316 identity/session and has no direct VaultStore access.

### Blocking gaps

1. **Credentials are not real accounts.** Login currently accepts any non-empty patient ID when the password is the shared plaintext value `correct`. This permits arbitrary account creation/impersonation and cannot establish that `00000` is Robert Asibor.
2. **No password hash exists.** There is no password record, salt, work factor, constant-time password verification or credential audit trail.
3. **No first-login or expiry state exists.** Neither `password_changed_at` nor `must_change_password`/expiry is represented or enforced.
4. **Sessions are process-memory only and unbounded.** Tokens have no issued/expiry timestamp, revocation state or password-version binding. Restart invalidates sessions, but a password change cannot reliably revoke already-issued sessions.
5. **The profile is global.** `VaultStore.get_profile()` reads one top-level `profile`, which is appropriate for the earlier single-user design but unsafe as a multi-user profile source. HC-313 identity verification therefore cannot safely select among multiple registered users today.
6. **Legacy defaults remain.** Several pre-HC-316 services still use `default-patient`, and some non-dashboard/non-records routes accept caller-provided patient IDs or are not behind the HC-316 authentication helper. They must not be treated as multi-user-safe without an endpoint-by-endpoint boundary review.
7. **Browser token storage is persistent.** HC-316 stores the bearer token in `localStorage`. This is vulnerable to token theft if script injection occurs and is weaker than a secure, HttpOnly, SameSite cookie for production clients.
8. **Tests depend on the scaffold.** HC-316/317 tests log in arbitrary IDs with `correct`; these fixtures must move to an isolated test-only user factory instead of influencing production bootstrap behavior.

## Required account contract

The bootstrap identity is:

| Field | Required value |
|---|---|
| Display name | Robert Asibor |
| User ID | `00000` (always a string; leading zeros are significant) |
| Initial password | `123456`, accepted only for one-time bootstrap login |
| Stored credential | Salted, adaptive password hash only |
| Initial state | `must_change_password = true` |
| First `password_changed_at` | `null` until the forced change succeeds |
| Password lifetime | 30 days from a successful password change |

The plaintext initial password must not be written to the VaultStore, logs, evidence, audit details, browser storage, fixtures or generated configuration. Provisioning should pass it once to an idempotent bootstrap operation, which immediately hashes it and persists only the encoded hash. A production build should not contain a reusable plaintext credential constant.

## Smallest safe implementation plan

### 1. Add an authoritative account registry

Add a small authentication service backed by the encrypted HC-311 store (or an equally protected dedicated authentication index) with one account record per user:

```text
user_id, display_name, password_hash, password_changed_at,
must_change_password, password_expires_at, password_version,
created_at, disabled_at
```

Use an adaptive password KDF with a unique random salt (prefer Argon2id when an approved dependency is available; otherwise use a reviewed `scrypt` configuration). Compare hashes safely. Never store or audit the supplied password.

This registry does not create a second clinical document repository: VaultStore remains authoritative for health records. Authentication metadata and clinical payloads remain distinct domains.

### 2. Provision Robert exactly once

Create an idempotent bootstrap/provisioning path that:

- creates user `00000` only when that ID is absent;
- stores display name `Robert Asibor`;
- receives the initial password through the protected provisioning boundary and stores only its hash;
- sets `must_change_password = true`, `password_changed_at = null` and no active full-access session;
- refuses to overwrite, reset or silently repair an existing `00000` account.

Bootstrap must not scan documents, infer ownership, copy a profile or attach legacy/default-patient data.

### 3. Enforce password lifecycle server-side

Replace the scaffold login check with account lookup and hash verification. Login should return either:

- a normal, expiring session when the password is current; or
- a narrowly scoped password-change session when first change is required or the password is 30 days old.

Add an authenticated password-change endpoint requiring the current password/change token and a conforming new password. On success, set `password_changed_at`, calculate `password_expires_at = password_changed_at + 30 days`, clear `must_change_password`, increment `password_version`, and revoke all earlier sessions. All dashboard, records, upload and download APIs must reject restricted/expired sessions with a consistent `403 password_change_required` response.

Add session expiry, logout/revocation, failed-login throttling and generic credential errors. Prefer secure HttpOnly, SameSite cookies for the browser client; if bearer tokens are temporarily retained, keep them short-lived and avoid persistent browser storage.

### 4. Make profiles explicitly user-scoped

Introduce a patient-keyed profile representation and require authenticated `user_id` for profile reads/writes. Update HC-313 identity verification to receive the intended authenticated/acquisition owner explicitly; it must never read a global profile in a multi-user flow.

Do not automatically migrate the existing top-level profile. Preserve it as legacy/unassigned until an explicit, audited ownership decision is made.

### 5. Preserve Robert's ownership without cloning data

- Records already explicitly marked `patient_id = "00000"` remain Robert's only.
- Do not copy those records, measurements, trends, observations, provenance or dashboard preferences to another ID.
- Do not infer that `default-patient`, `patient-A`, fixture or unscoped records belong to Robert.
- If Robert's legitimate pre-account data exists under another identifier, require a separate reviewed ownership manifest and one-time audited reassignment; it is outside the bootstrap operation.
- Every new account receives a new empty profile/preferences namespace and sees zero clinical records until that account imports its own data.

### 6. Separate synthetic and real data

- Automated tests must create temporary encrypted VaultStores and test-only users such as `test-patient-*`; they must never invoke the production Robert bootstrap record.
- Synthetic documents remain in test fixtures or temporary intake roots and carry an explicit `data_classification = synthetic_test` where persisted.
- Production/user records carry `data_classification = user_health_data` and an immutable owner ID.
- Startup and packaging checks should fail if known synthetic filenames/fixture identities are present in the production vault.
- Existing HC-313/HC-314 generated PDFs, acquisition state, reports and logs in the working tree are runtime/test artifacts; they must remain excluded from the HC-317C commit and must not be imported into user `00000`.

### 7. Close the endpoint boundary consistently

Centralize authentication/authorization in a reusable FastAPI dependency and apply it to consumer-facing dashboard, records, profile, intelligence, timeline, guardian and companion operations. Remove caller-selected/default patient identity from authenticated product routes. Keep any development triggers disabled outside an explicit test/development configuration.

## Required tests before HC-317C commit

- Bootstrap creates exactly `00000`/Robert and persists no plaintext password.
- Re-running bootstrap does not overwrite the account or reset its password.
- `123456` can obtain only a password-change session on first login.
- Dashboard, record list/detail/upload/download are denied until password change.
- Successful change records UTC timestamps, establishes 30-day expiry and revokes the bootstrap session.
- Login at and after expiry again permits password change only.
- Wrong passwords, unknown IDs, malformed/forged/expired tokens and replayed pre-change sessions fail closed.
- User ID is handled as the string `00000`, never coerced to numeric `0`.
- Robert's record/linkage data is visible only to `00000`; a second new user begins empty.
- Upload identity override and cross-user linkage/download attempts remain rejected.
- Synthetic fixtures cannot appear in a production/user vault validation run.
- HC-313 acquisition identity uses the intended owner's scoped profile.
- Desktop and mobile sessions for the same account resolve to the same user ID without sharing client-local storage.

## Desktop and mobile identity

HealthChecker desktop and HealthChecker mobile are different clients of the same branded service and account identity. Robert signs in as user `00000` on either client; the server resolves both sessions to the same immutable account and the same patient-scoped VaultStore records. Each client receives its own revocable session/device credential and maintains its own presentation preferences where appropriate. Neither client creates a second user, clones clinical data, embeds a password, shares encryption keys, or accesses VaultStore files directly.

The account is the identity boundary; desktop/mobile are delivery surfaces. Mobile companion pairing must bind to the authenticated account instead of the current `default-patient` assumption before it is considered multi-user ready.

## Impact on HC-317C

The records UI itself can remain largely unchanged because it already consumes authenticated APIs and does not submit a patient ID. It needs a forced-password-change screen/state, consistent handling for `password_change_required` and session expiry, and migration away from long-lived `localStorage` bearer tokens if cookie sessions are adopted. The backend authentication foundation and patient-scoped profile work must land before HC-317C is committed as consumer-ready.

No implementation code, account, credential, clinical record or data migration was created during this review.
