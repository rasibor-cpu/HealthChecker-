# HC-318B Authentication Foundation Implementation Validation

Date: 2026-08-16

Branch: `hc311-encrypted-vault-at-rest`

Baseline: `141a6964e65243d698e767e4d28a33d166d58d7e` (`HC-317C: Implement consumer health records UI experience`)

## Implementation summary

- Added the `UserAccount` contract with immutable string user ID, name, email/identifier, password hash, password timestamps/expiry, forced-change flag, account status, role and password version.
- Added an encrypted authentication registry under the HC-311 vault root with a distinct encryption context. It stores account metadata, hash-only opaque sessions and redacted authentication audits; it is not a clinical document repository.
- Added adaptive salted `scrypt` password hashing and timing-safe verification. Unknown accounts perform a dummy hash verification to reduce account-enumeration timing differences.
- Added idempotent bootstrap provisioning for owner `00000`, Robert Asibor, with the required temporary password accepted only through hash verification and `password_change_required` initial state.
- Added restricted first-login sessions, secure password update, 30-day UTC expiry, password-version invalidation, logout/revocation, session expiry and locked/disabled account enforcement.
- Added login, session-status, password-change and logout endpoints. Existing dashboard and records APIs now resolve the user through the authentication service and deny restricted sessions with `403 password_change_required`.
- Integrated the HC-316 dashboard with the forced password-change journey. Browser bearer state moved from persistent local storage to tab/session storage.
- Added user-scoped profiles and dashboard preferences. New accounts receive empty profile namespaces; no legacy, Robert or synthetic record is copied during account creation.
- Added explicit owner-profile selection support for HC-313 identity verification and patient-scoped backfill/profile compatibility.
- Updated the PWA cache revision so installed clients receive the HC-318B login flow while retaining the stable HC-301 cache family contract.

## Bootstrap validation

- Owner ID remains the exact string `00000`.
- Name is `Robert Asibor`; role is `owner`.
- Initial state is `password_change_required` with no password-changed or expiry timestamp.
- The registry stores an encoded salted `scrypt` hash and never the plaintext temporary password.
- Re-running bootstrap refuses to overwrite/reset the existing owner account or password hash.
- Encrypted registry bytes do not expose JSON field names, passwords or hashes.

## Password and session validation

- Initial owner login receives only a ten-minute password-change session.
- Dashboard and records APIs reject restricted sessions.
- New passwords must contain at least eight characters and differ from the current password.
- Successful change records UTC timestamps, sets expiry 30 days later, increments password version, revokes earlier sessions and issues a full session.
- Password expiry produces a password-change-only session and denies clinical APIs.
- Unknown users, wrong passwords, forged tokens, revoked tokens and stale password-version sessions fail closed.
- Session storage persists only token hashes server-side; authentication audits contain no passwords or password hashes.

## Isolation validation

- All consumer record/dashboard authorization derives owner ID from the server-side session, never the request body.
- New users receive empty records and profiles.
- A new user cannot list, detail or download Robert's records and receives no Robert intelligence/dashboard linkage.
- Synthetic fixture ownership is not migrated to Robert or new accounts.
- Profiles and dashboard preferences are keyed by user ID.
- Backfill writes only the declared patient's profile. Legacy no-ID profile compatibility resolves a scoped profile only when exactly one unambiguous profile exists.
- HC-313 can construct identity verification from an explicitly selected user profile.

## Tests executed

HC-318B plus HC-316/317 authentication integration:

```text
25 passed, 2 warnings in 9.62s
```

HC-311 through HC-318 milestone validation:

```text
296 passed, 2 warnings, 5 subtests passed in 18.23s
```

Full repository regression after authentication/profile compatibility changes:

```text
1171 passed, 3 skipped, 3 warnings, 5 subtests passed in 422.57s
```

Final PWA cache compatibility check after the HC-318B cache revision:

```text
2 passed in 0.43s
```

Warnings were limited to existing FastAPI/Starlette and HTTPX deprecations plus an environment-specific pytest cache permission warning. No test failed.

## Runtime-artifact controls

HC-313/HC-314 logs, state, generated synthetic PDFs, intake output, reports and scratch files were preserved but excluded from staging. Account tests used temporary encrypted vault roots. No production `auth_registry.json`, plaintext password, synthetic record migration or real-user health-data mutation was created in the repository.
