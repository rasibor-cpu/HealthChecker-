# HC329 T56 — Engineering Closure

Date: 2026-09-13

## Closure decision

**HC329_ENGINEERING_CLOSURE = PASS**

This closes the application-engineering and repository-integration phase of HC329.

It does **not** certify a production release. Production certification remains gated by
the production-signed vc329 Android artifact and authorized S24 device UAT tracked in
Issue #36.

## Current release line

- Android versionCode: 329
- Android versionName: 0.329.0
- Main baseline before T56: `b21abbe7d794aa599a99be1d40ba4b93b138f64c`

## Completed HC329 increments

### T51 — Startup wrapper isolation
- Restoring only the repository startup wrapper returned the governed runtime to service.
- Port 8766 listener restored under python.
- /healthz, /mobile, HC329 assets, and recovery catalog returned HTTP 200.
- HC329 application files remained unchanged.
- Root-cause indication isolated to the locally modified startup wrapper.

### T52 — Startup regression hardening
- Added startup-contract regression coverage.
- Preserved the known-good wrapper rather than rewriting it.
- Later normalized the test to the supervised wrapper design, allowing owned-child
  cleanup while continuing to forbid broad process termination, CSS-port handling,
  system network reconfiguration, ACL mutation, and tunnel lifecycle mutation.

### T53 — Consumer UX promotion
- Hospital Blue consumer appearance promoted to main.
- Dark appearance retained.
- Password-change and forgotten-password recovery UX polished.
- Android source version promoted to vc329 / 0.329.0.
- Regression coverage added.

### T54 — SAF/import and release engineering
- Android SAF record-import bridge merged.
- Native import reads bounded and scoped to the selected URI.
- Browser/input-file fallback preserved.
- Upload failure handling normalized.
- Patient-scoping fixes merged.
- Clinical-rules parity guard added.
- Android/release regression tests normalized.
- Debug-only UI tests moved out of release unit scope where appropriate.

### T55 — Cloud validation gate
- Added non-deploying GitHub Actions validation.
- Focused HC329 Python regression: PASS.
- Android debug unit tests: PASS.
- Android release unit tests: PASS.
- Linux CI prerequisites and platform-specific test handling normalized.

## Repository hygiene

- Legacy HC321/HC322/HC323/HC324/HC325 draft PRs reviewed.
- PRs already absorbed by current main were retired.
- Divergent legacy variants were inspected for unique release value before closure.
- Equivalent/newer Health Snapshot, screenshot-policy, timeline/records and deployment
  gating capabilities were confirmed on current main.
- Open legacy pull requests after cleanup: **0**.

## Production-impact statement

No T52–T56 repository work required:
- FINANCE host runtime manipulation;
- scheduled-task stop/restart;
- production-vault mutation;
- phone pairing reset;
- Sync Now;
- Cloudflare lifecycle action;
- ACL modification;
- CSS port 8765 action.

## Remaining release-certification gate

Tracked exclusively in **Issue #36 — HC329 final release certification: production
signing + device UAT**.

Required before `HEALTHCHECKER_FINAL_GATE = PASS`:
1. Produce production-signed vc329 APK with governed credentials.
2. Verify signer and Android signature schemes.
3. Record final APK SHA256.
4. Authorized in-place S24 upgrade without clearing app data.
5. Verify production origin, Hospital Blue/Dark, password/recovery flows.
6. Retest SAF import with representative PDF/JSON/image.
7. Verify record continuity and patient scoping.
8. Confirm screenshot policy remains correct.
9. Confirm no CSS port 8765, pairing-reset, Cloudflare lifecycle, or unrelated vault action.

## Final status

`HC329_ENGINEERING_CLOSURE=PASS`

`HEALTHCHECKER_FINAL_GATE=PENDING_OPERATOR_RELEASE_CERTIFICATION`

No further application-engineering work is planned unless Issue #36 device UAT discovers
a defect.
