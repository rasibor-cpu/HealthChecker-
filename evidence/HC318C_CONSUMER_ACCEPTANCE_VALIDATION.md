# HC-318C Authenticated Consumer Acceptance Validation

Date: 2026-08-16

Branch: `hc311-encrypted-vault-at-rest`

Validated HEAD: `4d94fa8512b8a9b853f688780fbc70edeeee0886` (`HC-318B: Implement production authentication foundation`)

## Outcome

The authenticated consumer journey passes after one blocking isolation defect discovered by browser acceptance was corrected. Authentication was not redesigned. Validation used a temporary encrypted VaultStore, synthetic health records and an isolated secondary test account; no real user data or production authentication registry was changed.

## Robert bootstrap acceptance

- Logged in as the exact string user ID `00000` with the required temporary bootstrap password.
- Confirmed the initial session could not enter the dashboard and presented the forced password-change form.
- Changed to a compliant password through `POST /api/auth/password/change`.
- Confirmed the restricted session was replaced and the authenticated dashboard opened as `Welcome, 00000`.
- Confirmed the initial dashboard/profile contained no migrated synthetic records.

## End-to-end consumer journey

The following journey was exercised through the rendered consumer UI:

```text
Login
  -> forced first password change
  -> authenticated dashboard
  -> Health Records
  -> authenticated upload
  -> HC-312 batch/intake processing
  -> encrypted VaultStore document and measurements
  -> HC-315 trend and observational intelligence generation
  -> HC-317 record detail and HC-316 dashboard insights
```

An ambiguous two-domain synthetic record correctly entered `Requires Review` and did not generate trusted trend linkage. A focused glucose record entered `Imported` and displayed:

- extracted glucose measurement;
- stable glucose trend reference;
- evidence-linked observational AI explanation;
- linked timeline event;
- manual-upload provenance, batch/group identifiers and authoritative processing history.

This confirms the intake/review gate is preserved rather than bypassed to satisfy the consumer flow.

## Secondary-user acceptance

Created `secondary-user` only inside the temporary acceptance environment. The user had:

- an empty scoped profile;
- zero records;
- zero timeline entries;
- zero trends and AI observations;
- no Robert filenames, facts, measurements or evidence;
- independent dashboard preferences.

After Robert imported a measurement, the secondary dashboard continued to show `Total Measurements: 0` and an empty records widget.

## Blocking defect found and corrected

Initial browser validation showed `Total Measurements: 3` on the secondary user's status widget even though all record, trend, observation and timeline widgets were empty. `DashboardService` used the global measurement count for this single aggregate.

The minimal correction builds the authenticated user's document-ID set and counts only measurements attached to those documents. A structured HC-318B assertion now proves that a new user's measurement count remains zero when Robert has stored measurements. No authentication contract or test assertion was weakened.

Post-fix browser validation confirmed:

```text
Welcome, secondary-user
Total Measurements: 0
No observations available yet.
No metrics available for trend mapping.
Timeline is empty.
0 health records available.
```

## Isolation matrix

| Surface | Robert | Secondary user | Result |
|---|---|---|---|
| Profile | Scoped owner profile | Empty scoped profile | Pass |
| Record list/detail | Robert records visible | Robert records absent/foreign detail hidden | Pass |
| Documents/download | Owner-bound | Foreign document returns not found | Pass |
| Measurements | Owner-linked rows | Count and payload remain zero/absent | Pass after fix |
| Trends | Evidence-linked glucose trend | Empty | Pass |
| Observations | Evidence-linked observational insight | Empty | Pass |
| Timeline | Robert import event | Empty | Pass |
| Dashboard widgets | Robert counts/insights | Empty independent state | Pass |
| Preferences | Robert namespace | Independent secondary namespace | Pass |

## Session and boundary validation

- Missing and forged bearer sessions are rejected.
- First-login and expired-password sessions are limited to password change and receive `403 password_change_required` from clinical APIs.
- Successful password change invalidates the prior session through password-version enforcement.
- Logout revokes the active session; subsequent API use is rejected.
- Unknown users and wrong passwords return generic invalid-credential failures.
- Cross-user record/detail/download attempts fail closed.

## Automated validation

HC-318B focused authentication/isolation suite after the aggregate fix:

```text
7 passed in 2.70s
```

HC-311 through HC-318 regression suite:

```text
296 passed, 2 warnings, 5 subtests passed in 19.69s
```

Full repository regression after the blocking-defect correction:

```text
1171 passed, 3 skipped, 3 warnings, 5 subtests passed in 423.77s
```

Warnings were existing FastAPI/Starlette and HTTPX deprecations plus a local pytest cache permission warning. No test failed.

## Data handling and cleanup

- Browser validation used only synthetic JSON in a temporary encrypted vault.
- Temporary acceptance files were removed after validation.
- The disposable local server was stopped.
- HC-313/HC-314 runtime artifacts already present in the worktree were preserved and not modified for this gate.
- No account, password, token, synthetic record or acceptance vault was committed to the repository.

## Gate conclusion

HC-318C passes. Robert's bootstrap/change/dashboard journey, the complete record-to-insight flow, secondary-user empty state, preference independence, session lifecycle, expired-password handling, unauthorized rejection and patient isolation are validated end to end.
