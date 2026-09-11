# HC329 — HealthChecker Final Production Closeout Report

STATUS: SAF IMPORT DEFECT FOUND VIA DEVICE UAT AND FIXED — DEVICE RETEST PENDING

## 1. Executive Status

**HEALTHCHECKER_FINAL_GATE = NOT_YET_FINAL.** `ENGINEERING_SOURCE_CLOSEOUT = PASS` (after fixing a real defect this revision — see below). `SAF_IMPORT_DEVICE_UAT`: prior physical-device attempt was `FAIL`; a fresh retest against the fix has not yet been run. `FINAL_DEVICE_ACCEPTANCE = WAITING_FOR_OPERATOR`. Every other required gate passes. (See Section 30 for the full gate checklist.)

**Device UAT update:** the operator ran the Section 17 device UAT this revision, and it **failed** — SAF file upload on the physical S24 (vc327) showed "Failed to fetch." Root-caused to Android WebView's unreliable handling of `content://`-backed file uploads via `fetch()`/`FormData` (not an auth, permission, origin-lock, or server-side defect — all independently re-verified intact). Fixed by reading the selected document's bytes natively and handing them to JavaScript through a new, narrowly scoped bridge (`ConsumerRecordImportBridge`) instead of relying on the WebView to stream the `content://` blob itself. Full root-cause analysis, the 15-hypothesis rule-in/out table, the fix, and the required device retest are in Section 17.

This closeout reviewed the entire live-vs-main code drift, all 19 historical open/draft PRs, and every required feature area (Android origin lock, SAF import, navigation, screenshot policy, auth/password/recovery lifecycle, patient isolation, vault security, Health Connect sync, Snapshot/Timeline/Trends), performed a source-level security audit, ran the full backend regression suite and the full Android unit-test suite, and attempted an Android release build.

**Headline finding:** `main` was already far more complete than expected going in. The live production runtime (HC310E) turned out to be a *pre-HC320 snapshot* for the files flagged as "modified" — main was ahead, not behind, and no application logic needed to be ported from live into main. All 19 historical PRs (#2-#20) were fully resolved: 15 had already shipped into `main` via direct push (GitHub's merge flag was simply never flipped), and the other 4 were confirmed dead-ends already superseded by later work. No carry-forward work was required from either source.

**Two real, if narrow, gaps were found during the feature/security review and fixed in this branch:** (1) a standalone admin device-management surface (`companion_host`) that could list/revoke any patient's paired device with only a shared admin token, now fixed to require the caller's own authenticated session and enforce ownership; (2) a duplicated-but-currently-consistent clinical-threshold table between the backend and the browser JS, now guarded by a parity test so future drift becomes a hard test failure instead of a silent inconsistency. Both fixes ship with new regression tests, and neither required weakening any existing protection.

**Test suite:** 0 failures at closeout (1498 total, 1495 passed, 3 skipped/environment-conditional). 5 pre-existing failures from stale hardcoded Android version literals were fixed durably; the SAF fix added 3 Python + 5 Android test cases and required updating 2 more pre-existing tests whose blanket "no JS bridge" assumption predated this fix (see Section 24). Android `assembleRelease`, `testDebugUnitTest` (173/173), and `testReleaseUnitTest` (161/161) all build clean. Production APK signing remains `BLOCKED_PENDING_OPERATOR` in this environment (2 of 4 required credentials present) — this is an access-control fact about this review process, not evidence of any compromised or lost signing material, and production signer continuity from HC328 is independently proven.

**One item withholding the top-level COMPLETE declaration:** SAF (Storage Access Framework) record import failed its physical-device UAT this revision with a real, now-fixed defect (see above and Section 17). The fix is complete and tested at the source/unit-test level, but per the explicit HC329 acceptance criterion a device-level claim cannot be proven from source alone — an operator must retest on the physical device after an in-place update to the fixed build (Section 26 gives the exact artifact/version-bump requirement) before this gate can read PASS.

**Production normalization design corrected in this revision:** Sections 27-28 now specify a side-by-side governed production-candidate directory (e.g. `C:\rasib\source\HealthChecker-Production-HC329`), validated offline/on a non-production port before any cutover, with the existing `HealthChecker-HC310E` runtime preserved untouched as the immediate rollback target — rather than copying the merged tree into HC310E in place.

No CRITICAL or unresolved HIGH security findings. Two MEDIUM findings, both fixed with tests. Production was never touched — HC310E, the scheduled task, the vault, the paired device, and CSS port 8765 were all left exactly as found.

## 2. Starting Repository State
- Canonical repo: `C:\rasib\source\HealthChecker-Main`
- Starting branch: `main`, clean working tree
- Working branch created: `hc329-final-production-closeout`

## 3. Starting Main SHA
`5212fbc2e9334add1ed3cdacadfb81920fca1c4b`

## 4. Final Branch SHA
Code changes land in two commits: `b9e98a2` (production-only reconciliation + patient-scope fix) and `1cbed52` (stale version-literal test fixes), on top of starting main HEAD `5212fbc`. This report is committed as a third, documentation-only commit immediately after — its own SHA is necessarily generated by the act of committing it, so the exact final branch-tip SHA is stated in the pull request description (Section 17) and is whatever `git rev-parse HEAD` on `hc329-final-production-closeout` shows at PR-creation time.

## 5. Live HC310E vs Main Reconciliation
Live comparison directory: `C:\rasib\source\HealthChecker-HC310E` (git HEAD detached at `41bfc29`, read-only — not modified by this work).

Live modified files at HC328 closure (per assignment) and raw diff line counts (HC310E vs current main, unified diff):

| File | Diff lines | Status |
|---|---|---|
| backend/health_vault/models.py | 1182 | **A** — see two-step proof below. Live's intentional patch (patient_id field) is fully present in main, which is a superset. |
| backend/health_vault/monitoring/ingestion.py | 911 | **A** — see two-step proof below. Live's intentional patch (patient_id passthrough) is fully present in main, extended further. |
| hc314a_acquisition.log | 4768 | **E** — runtime acquisition log, not source. No action (confirmed by inspection: append-only log data). |
| js/health_vault/health_snapshot.js | 0 | **A** — verified byte-identical (1906 lines, 67781 bytes, MD5 `40422fc67308c80f6411f61687736b82` on both sides — not merely "0 diff lines" but confirmed matching line count/byte count/hash independently). No source reorganization to account for. Behavior additionally corroborated by 8 relevant test files present in main (`test_hc321_health_snapshot.py`, `test_hc325_r3_mobile_health_snapshot.py`, `test_hc325_r7d_mobile_runtime_closure.py`, `test_hc325_r7a_authenticated_json_contract.py`, `test_hc323_consumer_records_freshness.py`, `test_hc321_uat12j_clinical_ingestion.py`, `test_hc319d_mobile_consumer_launcher.py`, `test_hc325_r4_universal_back_nav.py`) — pending full pytest run results (Section 24) for pass/fail confirmation. |
| js/health_vault/mobile_consumer.js | 1246 | **A (reverse direction)** — LIVE is a pre-HC320 baseline (293/950 lines) that never received HC321-328 hardening (`consumer_nav.js`, `json_contract.js` don't exist at all in LIVE). Main is the strict superset: recovery-enrollment gating, security-gate concept, `parseJsonResponse`/`safeApiPath` "Failed to fetch" hardening, and a fixed double-fetch-on-dashboard-load bug that LIVE still has. 4 minor live-only cosmetic diffs found, all D/E/B/A (obsolete greeting text, dead-code error fallback, missing cache-busting query strings on mobile.html assets [B — deploy hygiene, see below], unstyled header). **No C-classified item.** |
| mobile.html | 259 | Same finding as above — LIVE has `?v=hc328snapshot1`-style cache-busting query strings on its asset tags that MAIN's `mobile.html` lacks. Classified **B** (config/deploy hygiene, not application logic). **Reconsidered and declined:** checked whether main applies this convention consistently — it does not (`index.html` itself mixes tagged scripts like `health_snapshot.js?v=hc324a` with untagged ones); forcing the pattern onto `mobile.html` alone would be cosmetic churn, not closing a real gap. Left unchanged. |
| style.css | 1280 | **A (reverse direction)** — main is AHEAD of LIVE, not behind. ~95% of diff is CRLF/LF artifact. The remaining real content shows LIVE is *missing* code main already has: `body.mobile-consumer` base rules + `--status-*`/`--bg`/`--card` CSS custom properties (main.style.css:721-801), and the HC325-R7D Android WebView native `<select>` tappability fix (main.style.css:953-993, paired with `mobile.html` `.mobile-select-field` markup and `mobile_consumer.js` `ensureSelectInteractive()`). No backport needed into main. **Operational note (not a HC329 main-branch action item):** if LIVE production truly lacks the `--status-*`/`--bg` custom-property definitions referenced by its own "HC328-T67OM MOBILE SNAPSHOT BACKPORT" CSS block, those `var()` references have no source and would silently fall back to initial/inherited values — a plausible live-only rendering defect in mobile Snapshot theming, worth a separate operator check against the live site (out of scope for source reconciliation since HC310E is read-only here). |

Note: raw diff size includes ordinary code drift/refactors between the HC310E base commit and current main HEAD, not solely intentional production patches. Classification requires semantic analysis, in progress via dedicated research passes.

## 6. Production-Only Differences

### Two-step proof for models.py and ingestion.py (corrected evidence chain)

The comparison basis matters and is kept distinct here, per HC329 mid-run correction:

**Step A — HC310E's own HEAD (`41bfc29`) vs HC310E's WORKTREE** (isolates the actual intentional live patch that existed at HC328 closure, independent of main):

`backend/health_vault/models.py` (`git show HEAD:... ` vs worktree, HC310E repo):
```diff
@@ -66,6 +66,7 @@
     measurement_id: str = field(default_factory=lambda: str(uuid4()))
     document_id: str | None = None
+    patient_id: str = "default-patient"
     category: str = "Uncategorized"
@@ -167,6 +168,7 @@
     return Measurement(
         measurement_id=kwargs.get("measurement_id") or str(uuid4()),
         document_id=kwargs.get("document_id"),
+        patient_id=str(kwargs.get("patient_id") or "default-patient"),
         category=kwargs.get("category") or meta.get("category") or "Uncategorized",
```

`backend/health_vault/monitoring/ingestion.py` (same method):
```diff
@@ -351,6 +351,7 @@
             measured_at=obs.measured_at,
             confidence=obs.confidence,
             document_id=doc.id,
+            patient_id=obs.patient_id,
         )
```

This is the real intentional HC328 patient-scope patch: HC310E's committed HEAD predates patient_id support entirely; the uncommitted worktree modification added it.

**Step B — HC310E's WORKTREE (patched) vs current main** (`hc329` branch, identical to main):

Direct grep of current main confirms the field, the kwarg threading, AND the persist-call passthrough are present:
- `backend/health_vault/models.py:70` — `patient_id: str = "default-patient"` (Measurement dataclass field)
- `backend/health_vault/models.py:172` — `patient_id=str(kwargs.get("patient_id") or "default-patient")` in `create_measurement()`
- `backend/health_vault/monitoring/ingestion.py:354` (and `:270,274,283,288,326,334`) — `patient_id=obs.patient_id` / `"patient_id": obs.patient_id` at every persist/cursor/payload site, plus `patient_id` parameters on `save_cursor()`/`get_cursor()` (`:434,445`) that HC310E's single-file patch never had.

**Conclusion (corrected framing):** the behavioral modification that existed in the live HC310E working tree (patient_id field + propagation into `create_measurement()` and the ingestion persist call) **is fully represented in current main — and main is a strict superset**, extending patient_id propagation further into cursor persistence and batched-ingestion payload paths that HC310E's isolated patch didn't touch. The only remaining HC310E-worktree-vs-main differences for these two files are non-functional: CRLF-vs-LF line endings, and one dropped explanatory comment (`# HC327: measurements inherit the owning document/patient scope.`) in HC310E's copy. **No valid behavior remains only in HC310E for these two files.**

### Remaining files
All 7 flagged live-modified files were analyzed in full (diff + source read in both trees; two of them — models.py, ingestion.py — via the two-step method above):

- `hc314a_acquisition.log` — runtime log data, not source (confirmed by inspection: append-only acquisition log, not application code). Classification E.
- 1 file (`health_snapshot.js`) — byte-identical.
- 3 files (`mobile_consumer.js`, `mobile.html`, `style.css`) — LIVE (HC310E, pinned at git commit `41bfc29`) is a pre-HC320/HC321 baseline that never received the HC321-HC328 hardening that MAIN has; the divergence runs main-ahead-of-live, not live-ahead-of-main. No unique valid application logic exists only in HC310E for these files.
- Also independently checked `android/app/src/main/java/com/healthchecker/companion/sync/CompanionSyncRunner.kt` (outside the flagged 7, but directly relevant to HC328's bounded-retry claim): main already contains the bounded retry loop (`MAX_SAME_CHUNK_ATTEMPTS = 6`, exponential backoff via `SAME_CHUNK_RETRY_BASE_DELAY_MS = 1000L`), which HC310E's older pinned snapshot lacks. Confirms HC328's retry work is intact in main, not regressed.

One discretionary item was considered and declined: applying `index.html`'s `?v=hcXXX` cache-busting convention to `mobile.html`'s asset tags. On inspection `index.html` itself applies this inconsistently (some scripts tagged, others not), so it is not an established convention worth propagating — left `mobile.html` unchanged rather than introduce cosmetic churn.

**Operational note surfaced but explicitly NOT acted on (HC310E is read-only in this task):** the live style.css "HC328-T67OM MOBILE SNAPSHOT BACKPORT" block appears to reference CSS custom properties (`--status-*`, `--bg`, `--card`, etc.) whose defining rule (`body.mobile-consumer { ... }`) is absent from LIVE's stylesheet. If accurate, this is a plausible live-only mobile Snapshot theming defect. This is a production runtime concern, not a main-branch source gap, and should be verified against the actual live site by an operator separately — no HC329 code change is warranted since HC310E must not be modified here, and main already has the correct, complete version.

## 7. Missing Functionality Found
No application logic found that exists only in HC310E and is missing from main (see Section 6). Feature-closeout tracking (assignment section 9, areas A-J) below; "Missing Functionality" = any FAIL/NOT_YET_PROVEN item.

### Feature Closeout Status Tracker (assignment section 9)

| Area | Verdict | Notes |
|---|---|---|
| A. Android production origin lock | **PASS** | See Section 16. No gaps. |
| B. Consumer authentication lifecycle | **PASS** | See Section 12. No gaps. |
| C. Universal consumer navigation | **PASS** | `ConsumerLauncherActivity`'s `OnBackPressedCallback` delegates to JS (`HCConsumerNav.handleSystemBack()`), never calls `webView.goBack()` (deliberately, to avoid crossing the origin boundary via WebView history) — proven by `ConsumerInAppBackPolicyTest.kt`. Single-level back-stack (`consumer_nav.js`) handles drill-down, no Dashboard loop (empty-stack back returns `handled:false` -> Activity finishes cleanly), deep-link entries collapse cleanly to Dashboard on first Back. Mandatory-screen auth gate enforced via a `securityGate` flag (`consumer_nav.js` `setSecurityGate`/`isSecurityGate`) that swallows Back while `password_change_required`/`recovery_enrollment_required` is active — **main has this gate mechanism even though the originating draft PR #16 does not**, i.e. main is ahead of the PR that proposed this feature. Session persists in `sessionStorage`, untouched by navigation. No test gaps identified for this area. |
| D. Android SAF record import | **Real defect found via device UAT, now FIXED in source** (`ENGINEERING_SOURCE_CLOSEOUT = PASS`), `FINAL_DEVICE_ACCEPTANCE = WAITING_FOR_OPERATOR` (retest pending) | See Section 17. Physical-device UAT surfaced "Failed to fetch" on upload — root cause: Chromium WebView's unreliable handling of `content://`-backed Blobs in `fetch()` multipart bodies. Fixed with a narrowly scoped native read bridge (`ConsumerRecordImportBridge`); all prior security properties re-verified intact. A fresh device UAT is required to confirm the fix on-device — see Section 26 for the required in-place APK update. |
| E. Screenshot policy | **PASS** | See Section 18. No gaps. |
| F. Health Snapshot | **PASS**, 1 gap found+fixed | See Section 19. Duplicate clinical-threshold engine (JS mirror) had no parity guardrail — added `test_hc329_clinical_rules_parity.py`, confirms no live drift today. |
| G. Timeline / Trends / filtered surfaces | **PASS** | See Sections 20-21. No gaps. |
| H. Health Connect sync | **PASS** | See Section 22. Bounded retry, mutex, idempotency, empty-batch protection, and failure surfacing all confirmed. One unrelated stale-version test-debt item found and fixed (Section 24). |
| I. Patient isolation | **PASS**, 1 gap found+fixed | See Section 14. `companion_host` admin surface allowed cross-patient device list/revoke with only a shared admin token — fixed to require the caller's own authenticated session and enforce ownership, with a new regression test. |
| J. Vault / security | **PASS** | See Section 15. No gaps. |

### Cross-cutting finding: legacy PR "open/draft" status is stale relative to `main`
For PRs #19 (HC325-R6, origin lock), #20 (HC325-R6B, SAF import), #16 (HC325-R4, back-nav), #8 (HC322, screenshot), #9 (HC322A, screenshot): GitHub shows all as OPEN/DRAFT with no merge commit. However `git merge-base --is-ancestor` proves the underlying commits for #19, #20, #16, and #9 are already ancestors of `main` (#8 is NOT an ancestor — correctly superseded by #9's later fix). `main`'s current source is at-or-ahead of what each of these PR branches proposed (e.g. main has an additional `securityGate` mechanism beyond PR #16, and the later HC325-R6B SAF integration beyond PR #19's tip). Conclusion: these features reached `main` through a separate, later stacked-commit chain (HC325-R6C -> R7A -> R7D -> R7F2 -> HC326 -> HC327 -> HC328), not by merging these specific PRs — the PRs are safe to close as incorporated once the full legacy-PR audit (Section 10) corroborates this per-PR.

## 8. Functionality Added
Two real gaps were found during the feature-closeout review (not from the HC310E reconciliation, which needed no ports) and fixed on `hc329-final-production-closeout`:

1. **Patient scoping on the `companion_host` admin device-management surface** ([backend/health_vault/companion_host/app.py](backend/health_vault/companion_host/app.py), [backend/health_vault/api.py](backend/health_vault/api.py)) — `GET/DELETE /api/companion/devices[...]` on the standalone HC304B companion-only host previously trusted a single shared admin token with no per-patient check, allowing cross-patient device listing/revocation. Now requires the caller's own authenticated bearer session in addition to the admin token, and scopes to that patient via the pairing layer's existing (previously unused from this path) `patient_id` parameter. New test: `tests/test_hc304b_private_host_foundation.py::test_devices_and_revoke_are_patient_scoped`.
2. **Clinical-threshold parity guardrail** ([tests/test_hc329_clinical_rules_parity.py](tests/test_hc329_clinical_rules_parity.py), new file) — `js/health_vault/clinical_rules.js` independently hardcodes a subset of the same NORMAL/BORDERLINE/ABNORMAL/CRITICAL bands defined authoritatively in `backend/health_vault/config/clinical_rules.json`, with no test previously enforcing they agree. Values currently match; the new test makes any future silent drift a hard test failure instead of a latent inconsistency between server-computed and client-computed clinical status.
3. **SAF record-upload fix — real defect found via physical-device UAT** ([android/app/src/main/java/com/healthchecker/companion/consumer/ConsumerRecordImportBridge.kt](android/app/src/main/java/com/healthchecker/companion/consumer/ConsumerRecordImportBridge.kt), new file; [ConsumerSafFileChooserPolicy.kt](android/app/src/main/java/com/healthchecker/companion/consumer/ConsumerSafFileChooserPolicy.kt); [ConsumerLauncherActivity.kt](android/app/src/main/java/com/healthchecker/companion/ui/ConsumerLauncherActivity.kt); [js/health_vault/mobile_consumer.js](js/health_vault/mobile_consumer.js)) — the operator's device UAT surfaced "Failed to fetch" when uploading a SAF-selected file on the physical S24. Root cause: Android WebView's Chromium engine cannot reliably stream a `content://`-backed file into a `fetch()`/`FormData` multipart body. Fixed by reading the selected document's bytes natively via `ContentResolver` and handing them to JavaScript through one new, narrowly scoped `@JavascriptInterface` bridge that takes no URI/path parameter from JS (it can only ever read whatever the native file picker most recently recorded). All previously-verified security properties (no `file://`, no broad storage permission, narrow content-URI scope, production origin lock, authenticated upload, no secret logging) were re-confirmed intact. See Section 17 for the full root-cause analysis and hypothesis ruling.

Additionally, 5 pre-existing tests with stale hardcoded Android version literals (`versionCode = 324`/`321`, `versionName = "0.324.0"`) were fixed durably (parsed version-relationship/floor checks, not a re-pinned literal) — see Section 24.

## 9. Functionality Not Carried Forward
None. The HC310E live-vs-main reconciliation (Section 6) found no unique valid application logic in HC310E that main lacks, so there was nothing to decide against porting. The legacy PR audit (Section 10) likewise found no PR containing unique code worth carrying forward — all needed work is already in `main`.

## 10. Legacy PR Disposition

Audited all 19 open/draft PRs (#2–#20) on `origin` (github.com/rasibor-cpu/HealthChecker-). Evidence method per the mid-run correction: for each PR, verified whether the PR's exact head-branch commit SHA is a git ancestor of current `main` (`git merge-base --is-ancestor <sha> main`), then confirmed the PR's actual introduced functions/classes are present in main's current source by name and location — not by matching test filenames alone.

**Headline finding:** 15 of 19 PRs' head commits are literal ancestors of `main` — they were merged into `main` via direct push rather than through GitHub's PR-merge button, so GitHub still shows them OPEN/DRAFT even though their content has shipped (and `main` has since moved further ahead through HC325-R6C→R7A→R7D→R7F2→HC326→HC327→HC328). The other 4 (#2, #5, #8, #12) are confirmed dead-ends never merged and superseded by later work.

| PR | Title | Classification | Disposition | Key evidence |
|---|---|---|---|---|
| #2 | HC-321: Health Snapshot dashboard + screenshot support | SUPERSEDED | CLOSE_AS_SUPERSEDED | Branch has only 2 commits off `755a7e7`; main took a much larger independent path (UAT7→UAT12J). Core concepts (`HealthSnapshotEngine`, `selectLatestValid`) survive only as part of the later, unrelated rewrite — not this branch's code. |
| #3 | HC321-UAT12E: filtered surface JSON repair + snapshot dedupe | ALREADY_PRESENT_IN_MAIN | CLOSE_AS_INCORPORATED | Head `15c815c` is ancestor of main; main log has identical commit; `dedupe_observation_history`, `filter_timeline_entries` etc. present verbatim in `timeline.py`/`records_service.py`. |
| #4 | HC321-UAT12F: repair Filtered Timeline Failed to fetch | ALREADY_PRESENT_IN_MAIN | CLOSE_AS_INCORPORATED | Head `978f50d` ancestor of main; `build_consumer_timeline_response` present in `timeline.py` today. |
| #5 | HC321-UAT12G: compact filtered Timeline cards (branch `eab4`) | OBSOLETE (losing duplicate of #6) | CLOSE_AS_SUPERSEDED | Same title/parent as #6; independent commit `c35dfdb` never became an ancestor of main; #6's `43628c4` did. |
| #6 | HC321-UAT12G: compact filtered Timeline cards (branch `dd01`) | ALREADY_PRESENT_IN_MAIN | CLOSE_AS_INCORPORATED | Head `9389e3f` ancestor of main; main log has `43628c4`. **Confirmed duplicate pair with #5** — #6 shipped. |
| #7 | HC321-UAT12H: Heart Rate Filtered Timeline metric propagation | ALREADY_PRESENT_IN_MAIN | CLOSE_AS_INCORPORATED | Head `54338cb` ancestor of main; main has `0a7e278` + `54338cb`. |
| #8 | HC322: restore screenshots on consumer screens | SUPERSEDED | CLOSE_AS_INCORPORATED | Single commit `9dd9a15` off `755a7e7`, never an ancestor of main. `ScreenshotPolicy.kt` differs from main only in a docstring version label; Kotlin activity files byte-identical to main. Earlier/cruder precursor to #9. |
| #9 | HC322A: restore screenshots in consumer UAT launcher | ALREADY_PRESENT_IN_MAIN | CLOSE_AS_INCORPORATED | Head `de9c010` ancestor of main; main has `de9c010` directly. `ScreenshotPolicy`/`SecureWindowPolicy` present today. **Confirmed pair with #8** — #9 shipped. |
| #10 | HC321-UAT12I: Health Records evidence + Device Data grouping | ALREADY_PRESENT_IN_MAIN | CLOSE_AS_INCORPORATED | Head `dbbe8ca` ancestor of main; `classify_record_surface` present in `records_service.py`. |
| #11 | HC321-UAT12J: clinical-record ingestion + semantic normalization | ALREADY_PRESENT_IN_MAIN | CLOSE_AS_INCORPORATED | Head `59d5466` ancestor of main; `ClinicalLabPanelParser` present in `parsers/clinical_lab.py`. |
| #12 | HC321-UAT12J: Finance deploy precheck blocker (host not Finance) | OBSOLETE | CLOSE_AS_SUPERSEDED | Zero code — one diagnostic .txt file recording a blocked deploy-verification run from a non-Finance host; file doesn't exist in main; abandoned in favor of continuing straight to #13/HC323 from #11's tip. Moot — production has released many times since (through HC328 vc327). |
| #13 | HC323: fix consumer records, freshness and observation UX | ALREADY_PRESENT_IN_MAIN | CLOSE_AS_INCORPORATED | Head `b43093b` ancestor of main; `_classify_break` present in `freshness_path.py`. |
| #14 | HC324: fix Health Connect incremental sync freshness | ALREADY_PRESENT_IN_MAIN | CLOSE_AS_INCORPORATED | Head `41bfc29` (= HC310E's own pinned commit) ancestor of main; `FreshnessCatchUp` present in `android/.../healthconnect/`. |
| #15 | HC325-R3: mobile Health Snapshot above the fold | ALREADY_PRESENT_IN_MAIN | CLOSE_AS_INCORPORATED | Head `566731c` ancestor of main; `test_hc325_r3_mobile_health_snapshot.py` matching tests present. |
| #16 | HC325-R4: universal consumer back navigation | ALREADY_PRESENT_IN_MAIN | CLOSE_AS_INCORPORATED | Head `27c07c3` ancestor of main; `ConsumerInAppBackPolicy.kt` present. Note: main additionally has a `securityGate` mechanism beyond what this PR's branch contains — main is ahead, not just even. |
| #17 | HC325-R5: consumer password lifecycle + forgotten-password recovery | ALREADY_PRESENT_IN_MAIN | CLOSE_AS_INCORPORATED | Head `61b0cec` ancestor of main; `is_consumer_bootstrap_password` present in `auth.py`; `consumer_recovery.py` exists. |
| #18 | HC325-R5A: first-login recovery enrollment UX | ALREADY_PRESENT_IN_MAIN | CLOSE_AS_INCORPORATED | Head `d801941` ancestor of main; matching tests in `test_hc325_r5a_first_login_recovery_enrollment.py`. |
| #19 | HC325-R6: Android production origin lock | ALREADY_PRESENT_IN_MAIN | CLOSE_AS_INCORPORATED | Head `db1e215` ancestor of main; `ConsumerOriginLock.kt` present (see Section 16 for full source-level proof). |
| #20 | HC325-R6B: Android SAF record import | ALREADY_PRESENT_IN_MAIN | CLOSE_AS_INCORPORATED | Head `6fd3b03` ancestor of main; `ConsumerSafFileChooserPolicy.kt` present (see Section 17 for full source-level proof). This is the tip of the entire #3→#20 stack. |

**Duplicate/overlap pairs identified:** #5/#6 (identical title, #6 shipped), #8/#9 (HC322 vs HC322A, #9 shipped), #11/#12 (#12 is a no-code diagnostic dead-end branched off #11, abandoned).

**Carry-forward requirement: NONE.** No PR in #2–#20 contains unique code missing from `main`. No BLOCKED_NEEDS_REVIEW items.

**Action taken:** none — this was a read-only audit per the assignment ("do not actually close PRs unless explicitly authorized"). Recommended GitHub actions (for the repo owner to execute, not performed here): close #2, #5, #8, #12 as superseded; close #3,#4,#6,#7,#9,#10,#11,#13–#20 as incorporated, each with a comment pointing at the corresponding commit SHA already in `main`.

### Repository cleanup plan — stale branches beyond the PR set

Bulk `git merge-base --is-ancestor <branch> main` check run across every local and remote branch (not just PR-linked ones). No branches were deleted or force-modified — this is a disposition recommendation only.

| Branch | Ancestor of main? | Disposition | Evidence |
|---|---|---|---|
| `origin/pr2head`, `origin/pr5head`, `origin/pr8head`, `origin/pr12head`, `css-agent/hc321-health-snapshot-82c3-uat6a` | No | **CLOSE_AS_SUPERSEDED** | Snapshot refs of PR #2/#5/#8/#12 heads (identical commits) — same disposition as those PRs (Section 10). |
| `origin/pr6head`, `origin/pr9head` | Yes | **CLOSE_AS_INCORPORATED** | Snapshot refs of PR #6/#9 heads, both already ancestors of main. |
| `hc311-encrypted-vault-at-rest` | No | **CLOSE_AS_SUPERSEDED** | Base branch that the entire #3→#20 PR chain forked from; superseded once the whole chain landed in main. |
| `hc309-hc310e-integration` | No | **CLOSE_AS_SUPERSEDED** | Old integration merge branch predating the HC321+ work; nothing unique. |
| `hc310e-r2-host-delivery-timeout` (`e2be7dc`, 2026-09-01) | No | **CLOSE_AS_SUPERSEDED** | Diffed against main: main is a superset — this branch predates HC327/HC328 and is *missing* `CompanionSyncRunnerRetryPolicyTest.kt`, `SyncMutexTest.kt`, `test_hc327_record_continuity.py`, `test_hc328_measurement_patient_scope.py`, and `scripts/reconcile_measurement_patient_ids.py`, all of which main already has. Not unlanded work — an older snapshot. |
| `origin/hc326-regression-contract-alignment` (`5340eb3`, 2026-09-07) | No | **CLOSE_AS_SUPERSEDED** | Same evidence pattern as above — missing the same later HC327/HC328 test/script additions that main has; an older snapshot, not ahead. |
| `origin/rasibor-cpu-patch-1` (`c960325`, 2026-03-29) | No | **KEEP_FOR_REFERENCE** | Unrelated, much older "standalone HealthChecker+ v3.1" experimental feature branch predating the HC3xx series by months. Not part of this product line's active development; no action needed, low risk to leave as historical reference. |
| `origin/copilot/task-245718444-...` | No | **KEEP_FOR_REFERENCE** | Unrelated Copilot-authored documentation branch ("add Cursor to authorized AI agents"), not HealthChecker feature work. Out of scope. |
| `origin/origin` | N/A (empty/dangling ref) | **BLOCKED_NEEDS_REVIEW** | `git log` returns nothing for this ref — appears to be a malformed/accidental remote ref, not a real branch. Flagging rather than deleting, since remote ref deletion is a shared-state, hard-to-reverse action outside this task's authorization. |

All other audited branches (the full #3→#20 PR chain's underlying branches, plus `css-agent/hc321-uat12k-universal-back-nav-9f1a`, `css-agent/hc325-r6c-supervisor-self-healing`, `css-agent/hc325-r7a-authenticated-json-contract`, `css-agent/hc325-r7d-mobile-runtime-closure`, `css-agent/hc325-r7e3-webview-debug-trace`, `css-agent/hc325-r7f2-runtime-reliability`, `css-unified-consolidation-2026-07-13`, `feature/hc201-health-vault`, `hc-306i-r3-android-validation`, `hc310e-background-health-connect`, `hc326-legacy-measurement-reconciliation`, `hc327-release-readiness-final-audit`, `hc328-android-release-readiness`, and several `origin/hc-306i-*`/`origin/feature/gi-stability-v1` branches) are confirmed ancestors of `main` — **CLOSE_AS_INCORPORATED** for all.

**No branch was found containing unique code missing from `main`.** No branches were deleted, force-modified, or had their remote state changed as part of this task.

## 11. Security Audit

Source-level review across authentication, authorization, session handling, password lifecycle, recovery, patient isolation, device pairing/revocation, origin restrictions, Android WebView/URI handling, screenshot policy, vault encryption/key handling, backup behavior, signing, debug/release separation, secrets, logs, and production configuration handling. No CRITICAL or unresolved HIGH findings.

| # | Finding | Severity | Status |
|---|---|---|---|
| 1 | `companion_host` admin device list/revoke endpoints enforced no per-patient scope — any holder of the shared `X-HC-Companion-Admin` token could enumerate or revoke any patient's paired device, unlike the main app's equivalent (already patient-scoped) routes. | **MEDIUM** (requires the admin token as a precondition — not publicly reachable — but a genuine cross-patient authorization gap in a shipped code path) | **FIXED** — see Section 14/16 for the code + regression test. |
| 2 | Clinical status thresholds (`NORMAL`/`CAUTION`/`ATTENTION`/`UNKNOWN` bands) are defined in two independently maintained places (`backend/health_vault/config/clinical_rules.json` and `js/health_vault/clinical_rules.js`) with no automated check that they agree — a silent future edit to one without the other could produce a client-computed status that contradicts the server's, which is a clinical-safety/data-integrity concern more than a classic CIA vulnerability. | **MEDIUM** (data-integrity / clinical-safety) | **FIXED** — parity test added (`tests/test_hc329_clinical_rules_parity.py`); confirmed no drift exists today. |
| 3 | `recovery_start()`'s timing-equalizer (`verify_password` against a dummy hash) only executes on the "account doesn't exist / not enrolled" branch, not the "real, enrolled" branch — response *bodies* are identical either way (no information disclosed), but a sufficiently precise timing side channel between the two cases isn't fully ruled out by the code alone. | **LOW / INFORMATIONAL** | **ACCEPTED, documented.** No response-content leak exists; closing this fully would require deliberately padding the "real" branch's latency, which is out of scope for this closeout given no exploitation path was identified beyond a theoretical timing measurement. |
| 4 | An earlier, more elaborate HC311 recovery design (`recovery_enrollment.py`, `recovery_profiles.py`, `recovery_question_bank.py`, `vault_recovery.py`, `vault_question_recovery.py`) has no callers anywhere in `api.py` — dead code, not reachable over HTTP. | **INFORMATIONAL** | **ACCEPTED, documented.** Not a vulnerability (unreachable code has no attack surface); flagged as a candidate for a future cleanup pass, intentionally not touched here to avoid unrelated churn on a closeout branch. |
| 5 | Android screenshot policy deliberately never sets `FLAG_SECURE` anywhere, including on screens that could show pairing/recovery UI. | **INFORMATIONAL** | **ACCEPTED, by design.** Verified this is a documented, deliberate choice (`ScreenshotPolicy.kt` comments) — secrecy for pairing tokens/host credentials is handled via `EncryptedSharedPreferences`, not FLAG_SECURE, and no Android-native screen displays raw recovery codes/keys (recovery *questions* only, in the same WebView). Consistent with what's already deployed in production (byte-identical to HC310E). |
| 6 | Production Android signing credentials (`HC_ANDROID_KEYSTORE_PASSWORD`, `HC_ANDROID_KEY_PASSWORD`) are not present in this build/review environment. | **NOT A SECURITY FINDING** | Per explicit scope correction: absence of signing credentials in this engineering process is an operational/access-control fact, not evidence of lost or compromised signing material. Production signer SHA256 (`0ee183dcb1e88349d6352110e8d12cae9eb712d559925bbaf05030178f9b9588`) and its continuity into vc327 are independently proven from HC328 evidence (see Section 25/26: `PRODUCTION_SIGNING_LINEAGE = PROVEN`, `PRODUCTION_SIGNER_CONTINUITY = PROVEN_FROM_HC328`). No search for, exposure of, or reconstruction of credentials was attempted. |
| 7 | SAF record upload failed on physical-device UAT ("Failed to fetch") — Android WebView cannot reliably stream a `content://`-backed file into a `fetch()`/`FormData` multipart body. This is a functional/reliability defect, not a confidentiality/integrity/availability vulnerability — no data was exposed, corrupted, or made available to the wrong party; the feature simply didn't work. | **FUNCTIONAL DEFECT, not a security severity classification** | **FIXED** — `ConsumerRecordImportBridge` (Section 17) reads bytes natively via `ContentResolver` and hands them to JS as an in-memory Blob. The bridge itself was security-reviewed as part of the fix: single `@JavascriptInterface` method, zero parameters accepted from JS, reads only the URI the native SAF picker already recorded, no filesystem persistence, bounded read size (15MB), and it operates inside a WebView already fail-closed to the governed production origin only (Section 16). See residual risk #8 (Section 29) for the narrow, deliberate increase in bridge attack surface this introduces. |

**Confirmed clean (no findings) in:** authentication server-side enforcement (Section 12), password lifecycle (Section 12), recovery challenge verification and anti-enumeration (Section 13), patient isolation on all primary API surfaces (Section 14), device pairing/revocation on the main app (Section 14), Android production-origin lock (Section 16), SAF content-URI permission handling (Section 17 — the upload defect above was a reliability bug, not a permission/scope defect; narrow-scope/no-broad-permission properties were all re-verified intact after the fix), vault encryption/fail-closed behavior/key handling (Section 15), backup exclusion of key material (Section 15), debug/release build separation (`ALLOW_CLEARTEXT_LOCAL_DEV` gated by `BuildConfig.DEBUG`, Section 16), secrets hygiene (no committed keys/certs, Section 15), and log redaction (Section 15). TLS/public-origin assumptions rely on the existing Cloudflare/reverse-proxy layer, which is out of scope for source review and was not touched (per Section 4 safety rules).

**Gate status: PASS.** Zero CRITICAL, zero unresolved HIGH. Two MEDIUM findings, both fixed with code + regression tests. Remaining LOW/INFORMATIONAL items are documented with explicit acceptance rationale, not silently downgraded.

## 12. Authentication Validation
**PASS** — proven by source and passing tests (`test_hc325_r5_password_lifecycle.py`, `test_hc325_r5a_first_login_recovery_enrollment.py`: 14/14 passed).

- Bootstrap/default password cannot become permanent: `auth.py:199-209` `bootstrap_owner()` sets `must_change_password=True`/`account_status="password_change_required"`; the consumer bootstrap sentinel (`"0"*6`) is explicitly rejected as a *new* password by `validate_permanent_password()` (`auth.py:43`). Dev-only `"123456"` bootstrap is gated by `allow_development_bootstrap = not production_mode` (`api.py:227`).
- First-login password change: `auth.py:359-408` `change_password()`.
- Password expiry: `PASSWORD_DAYS = 90` (`auth.py:21`), enforced both at login and per-request in `resolve()` (`auth.py:353-356`, raises 403 `password_change_required` when `require_full=True`).
- **Server-side** gate (not client-only): `api.py:268-277` middleware calls `auth_service.resolve(token, require_full=True)` for every non-public `/api/*` path in production mode; `/api/records*` and companion device routes call `_get_authenticated_patient()` unconditionally regardless of `production_mode`.
- Voluntary password change requires current-password verification: `change_password()` calls `verify_password(current_password, row["password_hash"])` (`auth.py:378-379`) and rejects reusing the current password as the new one.
- PR #17/#18 (HC325-R5/-R5A) status corrected per mid-run instruction: both show OPEN/DRAFT on GitHub, but commits `61b0cec`/`d801941` are confirmed git ancestors of `main`, and the actual diff (`git show --stat`) matches the live `auth.py`/`consumer_recovery.py`/`api.py` code read directly on this tree — this is live, tested code, not "only in the PR."

## 13. Recovery Validation
**PASS**, with one minor informational note.

- Recovery enrollment requires real challenge verification: `_verify_recovery_answers()` (`auth.py:410-427`) requires exactly 3 enrolled Q&A (`consumer_recovery.py:12`), each answer scrypt-hashed and compared via constant-time `verify_password`/`hmac.compare_digest`; any unenrolled question_id is rejected.
- Forgot-password does not enumerate accounts: `recovery_start()` (`auth.py:429-456`) returns an identical response shape (deterministic dummy questions, `consumer_recovery.py:41-56`) for both non-existent users and existing-but-unenrolled users; `login()` always runs `verify_password` against a fixed dummy hash when the account doesn't exist, returning the same generic `invalid_credentials` error either way.
- Recovery does not over-grant authorization: `recovery_verify()` issues a token scoped `"password_recovery"` with a 10-minute lifetime; `resolve(require_full=True)` treats any non-`"full"` scope as restricted, so a recovery token cannot reach dashboard/clinical endpoints; `recovery_complete()` requires `scope == "password_recovery"` and performs the mandatory password reset — it does not skip it.
- **INFORMATIONAL:** `recovery_start()`'s timing-equalizer extra `verify_password` call only runs on the "fake account" branch, not the "real, enrolled" branch (`auth.py:444-445`); response *bodies* are identical either way, but a very precise timing side-channel between "real vs fake" isn't fully ruled out by this code alone. Not classified as a functional defect (no information is disclosed in the response), logged as a residual risk (Section 29) rather than a blocking finding.
- Dead-code note: `recovery_enrollment.py`/`recovery_profiles.py`/`recovery_question_bank.py`/`vault_recovery.py`/`vault_question_recovery.py` (an earlier, more elaborate HC311 recovery design) have no callers anywhere in `api.py` — the only recovery path actually reachable over HTTP is `auth.py` + `consumer_recovery.py`. Not a security issue (dead code isn't reachable), but worth flagging as candidate cleanup outside HC329 scope (not touched here to avoid unrelated churn).

## 14. Patient-Scope Validation
**PASS** for the primary consumer/API surface. **One real gap found and fixed** on a secondary admin surface.

- Ingestion → storage propagation traced end-to-end: `companion/delivery.py:136,146` resolves `patient_id` solely from the authenticated device and explicitly **rejects** any client-supplied `patient_id` (`"patient_id_injection_rejected"`, lines 193-197/296-298); flows into `IngestionCoordinator.ingest_observations(..., patient_id=patient_id)` → `create_measurement(..., patient_id=obs.patient_id)` → the dedupe/index key `(patient_id, metric, source_record_id)` (`vault_store.py:343-438`).
- No exploitable default-patient fallback in production: `"default-patient"` is only ever a dev/test function-default; in `production_mode` every clinical/API route resolves the authenticated patient before use (`api.py:268-277` middleware, plus explicit `if production_mode:` branches at `api.py:448-450,490,501,526,543,636,649,659,667`). `create_health_vault_app(store=None)` defaults `production_mode = (store is None)` — secure by default on the real entrypoint path.
- Negative isolation tests exist and pass: `tests/test_hc317c_consumer_records_ui.py::test_records_ui_patient_isolation_and_no_cross_user_phi` and `tests/test_hc323_consumer_records_freshness.py::test_patient_scope_and_unfiltered_api_compat` — both **ran, PASSED**.
- Main app's `/api/companion/devices` (GET/DELETE) already correctly scopes by `_get_authenticated_patient(request)` (`api.py:700-719`).
- **GAP FOUND (now FIXED):** the separate standalone `companion_host` app (HC304B private host, `backend/health_vault/companion_host/app.py`) exposed `GET /api/companion/devices` and `DELETE /api/companion/devices/{device_id}` gated only by a static shared `X-HC-Companion-Admin` bearer token, with **no patient scoping at all** — any holder of that one admin token could list or revoke **any** patient's device, unlike the main app's equivalent routes. Fixed in this branch:
  - [companion_host/app.py](backend/health_vault/companion_host/app.py) — `devices`/`revoke` routes now also require the caller's own authenticated `Authorization: Bearer` session (mirroring the pattern already used by `pair_start`) via a new shared `_resolve_authenticated_patient()` helper, and pass `patient_id=account.user_id` through.
  - [api.py](backend/health_vault/api.py) — `companion_devices_handler`/`companion_revoke_handler` gained an optional `patient_id` parameter (default `None`, fully backward compatible with existing direct-function-call tests) threaded into the already-existing `list_devices(patient_id=...)`/`revoke_device(..., patient_id=...)` scoping mechanism in `pairing.py` (which supported this all along — it just wasn't being asked to use it from this code path).
  - New regression test [tests/test_hc304b_private_host_foundation.py](tests/test_hc304b_private_host_foundation.py) `test_devices_and_revoke_are_patient_scoped` — pairs two patients' devices, proves patient B's device listing excludes patient A's device, proves patient B cannot revoke patient A's device (403 `forbidden`) even holding the correct admin token, proves patient A can revoke their own device, and proves admin-token-only (no bearer) is rejected (401). **Ran: PASSED**, alongside the full existing `test_hc304b_private_host_foundation.py`/`test_hc304br1_proxy_topology.py`/`test_hc303a_android_companion.py`/`test_hc319c_mobile_identity_and_api_only.py`/`test_hc306*.py` suites (157 passed, 1 skipped, no regressions).

## 15. Vault Validation
**PASS** — fail-closed behavior verified at multiple layers.

- Encrypted vault is mandatory in production: `api.py:211-212` and `production_runtime.py:46-47` both raise `RuntimeError`/`ProductionRuntimeError("production_vault_encryption_required")` if `production_mode and not vault.encrypted`.
- Fail-closed on crypto/index/key errors: `vault_crypto.py` raises `VaultCryptoAuthenticationError` on `InvalidTag`, `VaultCryptoFormatError` on any malformed envelope — no silent pass-through. `production_runtime.py:32-51` forces `_read_index()` at startup (auth failure surfaces immediately) and validates schema via `VaultMigrationManager().validate_current()`. `vault_key_protector.py` fails closed on missing/malformed key or DPAPI failure — never returns a default/zero key.
- Key material not committed: production key lives at `C:\ProgramData\HealthChecker\secrets\vault.key`, entirely outside the repo tree, read via Windows DPAPI (`production_runtime.py:19,33`). `.gitignore` excludes `keystore.properties`, `*.jks`, `*.keystore`, `*.hcb`, `vault_storage/.companion_pepper`, `vault_storage/index.json`; `recovery.py:24` explicitly excludes `vault.key`/`server.key`/`keystore.properties` from any backup archive. `git ls-files` confirmed no tracked key/cert material.
- No secrets found in tracked files: grepped all `git ls-files` for private-key/certificate markers — only hits are negative assertions inside test files (`assert "BEGIN PRIVATE KEY" not in ...`). Only hardcoded-password-style hit is the documented, `allow_development_bootstrap`-gated dev default (`auth.py:152`, `"123456"`).
- Logging does not leak secrets/tokens: `monitoring/privacy.py:redact_for_log()` and `companion/security.py:redact_companion_log()` redact any secret/token-shaped field before every companion/monitoring event-bus publish; `auth.py` performs no ad-hoc logging at all — its only persistence is the encrypted `_audit()` trail storing `action`/`user_id`/`outcome` only.
- No action required; no gaps found.

## 16. Android Origin Validation
**PASS** — proven by source + unit tests, no code changes needed.

- `ConsumerOriginLock.kt`: release always resolves to `PRODUCTION_ORIGIN`/`PRODUCTION_MOBILE_URL` (`https://health.capitalstratasystems.com/mobile`) unless `isDebugBuild && allowCleartextLocalDev && explicitLocalDevRequested` (lines 52-89); release sets `ALLOW_CLEARTEXT_LOCAL_DEV=false` (`build.gradle.kts:105`) so that branch is unreachable in release.
- `isForbiddenLoopbackHost()` rejects `localhost`, `0.0.0.0`, `::1`, `10.0.2.2`, `127.x.x.x` (`ConsumerOriginLock.kt:113-119`); covered by `ConsumerOriginLockTest.kt` (`loopbackIsRejected`, `emulatorLoopbackIsRejected`, `savedStateLoopbackIsRejected`, `releaseIgnoresExplicitLocalDevExtra`, `deeplinkLoopbackIsRejected`, `pairedNonProductionHttpsHostDoesNotOverrideGovernedOrigin`).
- WebView state restoration is explicitly disabled (`onSaveInstanceState` does not persist WebView state; `shouldRestoreWebViewState()` returns `false`; `restoreState`/`saveState` are never invoked anywhere in the app).
- No custom-scheme/host intent-filter exists anywhere in `AndroidManifest.xml` (only `MAIN`/`LAUNCHER` and Health Connect rationale intents); `onNewIntent` re-validates any incoming data through `ConsumerOriginLock.resolve()`/`isGovernedProductionCandidate`.
- `shouldOverrideUrlLoading` enforces the allowlist (`ConsumerOriginPolicy.isAllowed()`, exact-origin + path prefix) on **every** navigation, not just initial load; `onPageFinished`/`onReceivedError`/`onResume` independently re-check `mustRecover()`.
- Cross-check: PR #19 (HC325-R6) shows as OPEN/DRAFT on GitHub but `git merge-base --is-ancestor` proves its underlying commit (`db1e215`) is already an ancestor of `main`, and `main` is a superset of that PR's branch tip (main additionally has the later HC325-R6B SAF integration). Feature reached main via a different, later stacked-commit path, not via merging PR #19 itself.

No gaps found. No test additions required — existing `ConsumerOriginLockTest.kt` coverage is adequate.

## 17. Android SAF Import Validation

**ENGINEERING_SOURCE_CLOSEOUT = FAIL_PENDING_SAF_FIX → FIXED (this revision).** **SAF_IMPORT_DEVICE_UAT = FAIL (prior physical-device attempt) → retest required.** **FINAL_DEVICE_ACCEPTANCE = WAITING_FOR_OPERATOR.**

### Device UAT failure report (operator-supplied)
Physical-device UAT on the existing Samsung S24 / HealthChecker vc327 failed: the user opened the app normally, selected a file via the SAF picker, tapped "Upload securely," the initial tap appeared to do nothing, and on retry the UI displayed **"Failed to fetch."** No uninstall, data clear, re-pair, Sync Now, or production/vault action occurred during this UAT.

### Root cause
"Failed to fetch" is the literal text of the browser's own native `TypeError` thrown by `fetch()` when a request fails at the network/transport layer *before* any HTTP response is received — confirmed by grepping the entire repository for that exact string: it appears nowhere in this codebase's own error-handling code (only once, in an unrelated code comment referencing a prior, different incident). The app's `upload()` function (`js/health_vault/mobile_consumer.js`) built the multipart upload body directly from the `<input type=file>`'s `File` object, which the WebView populates from the `content://` URI returned by the SAF picker. Android WebView's Chromium engine is not reliably able to stream/read a `content://`-backed `File`/Blob when serializing it into a `fetch()` multipart body — this can fail (sometimes after an initial delay, matching the "first tap did nothing, retry showed the error" symptom) with the generic, non-actionable `TypeError: Failed to fetch`, which the app then displayed verbatim with no distinguishing context. This is a known category of Android WebView limitation with `content://`-backed file uploads via `fetch()`/`FormData`, not a defect in the app's SAF permission-granting, authentication, or origin-lock code — all of which were independently re-verified correct (see hypothesis ruling below).

### Hypotheses ruled in/out (explicit, as required)
| # | Hypothesis | Verdict | Evidence |
|---|---|---|---|
| A | content:// URI cannot be read from WebView/JS | **Ruled in** (as the failure class) | The URI itself is readable by native `ContentResolver` (proven by the new bridge/tests below); the failure is specifically at Chromium's fetch()-body-serialization boundary, not at permission grant or native read. |
| B | URI permission not persisted/granted correctly | **Ruled out** | `takeSafReadGrant()` already ran before handing the URI to the WebView, unit-tested and unchanged; the native bridge added in this fix successfully reads the same URI via the same grant. |
| C | File picker returns metadata but not readable bytes | **Ruled in** (contributing) | Matches the observed symptom exactly — filename/selection succeeds, byte read at upload time is what fails. |
| D | FormData/File object creation from content:// URI is invalid | **Ruled in** (contributing) | The `<input>`'s File object is valid metadata-wise; its Chromium-internal blob backing is what's unreliable to stream. |
| E | WebView's fetch() cannot directly dereference content:// | **Ruled in — this is the core mechanism** | Matches documented Android WebView/Chromium behavior for content://-backed Blob uploads. |
| F | Android bridge expected to convert content:// into bytes but does not | **Ruled in — this was the actual gap, now closed** | No `@JavascriptInterface` existed anywhere in the app before this fix (confirmed by repo-wide grep). |
| G | Authorization header/session token missing | **Ruled out** | `authHeaders()` unconditionally attaches `Authorization: Bearer` when a session exists; other authenticated calls (dashboard, records list) succeed in the same session. |
| H | Upload endpoint URL resolves to wrong origin/path | **Ruled out** | `/api/records/upload` is a same-origin relative path; other relative-path API calls succeed. |
| I | CORS / same-origin / mixed-origin restriction | **Ruled out** | Same-origin request; no CORS preflight applies. |
| J | Request blocked by WebView origin policy (`shouldOverrideUrlLoading`) | **Ruled out** | `shouldOverrideUrlLoading` only intercepts main-frame navigation, never a programmatic `fetch()`/XHR subresource request — confirmed by reading `handleNavigation()`'s wiring. |
| K | Multipart request rejected server-side | **Ruled out** | "Failed to fetch" occurs before any HTTP response exists; a server rejection would produce a status code + JSON body instead. |
| L | Server returns non-2xx but frontend collapses it into "Failed to fetch" | **Ruled out** | Repo-wide grep confirms the literal string is never emitted by our own code; `fetch()` resolves normally (not throws) for HTTP error statuses. |
| M | Response parser incorrectly treats JSON response as network failure | **Ruled out** | `parseJsonResponse()`/`jsonContractError()` produce distinctly worded errors, never this browser-native string, and only run after a response object already exists. |
| N | `allowFileAccess=false` interacts with the current implementation | **Ruled out** | `allowFileAccess` governs `file://` only; `content://` is separately governed by `allowContentAccess` (already `true`, unchanged). |
| O | Network security config / production-origin lock blocks the upload subrequest | **Ruled out** | Same-origin HTTPS request to the already-governed production origin; other authenticated calls succeed in the same session; network security config only restricts cleartext HTTP. |

### Fix
Implemented the architecture explicitly preferred for this class of failure: the native layer reads the SAF-selected document's bytes via `ContentResolver` and hands them to JavaScript through a new, narrowly scoped bridge, instead of relying on the WebView to stream a `content://` blob into the multipart body itself.

- **[android/app/src/main/java/com/healthchecker/companion/consumer/ConsumerRecordImportBridge.kt](android/app/src/main/java/com/healthchecker/companion/consumer/ConsumerRecordImportBridge.kt)** (new) — the *only* `@JavascriptInterface` in the app. Exposes exactly one parameterless method, `readSelectedRecordBase64()`, which reads the bytes of whatever URI the native file-chooser callback most recently recorded (`pendingUriProvider`, native-controlled) — JavaScript supplies no URI/path of its own, so the bridge cannot be used to read anything beyond what the user just picked via the system SAF picker. Returns a small JSON envelope (`ok`, `name`, `mime_type`, `base64`) or a typed error code (`no_file_selected`/`file_unreadable`/`file_too_large`/`read_failed`); bounded to `MAX_IMPORT_BYTES` (15MB) so a huge document can't be pulled fully into process memory.
- **[ConsumerSafFileChooserPolicy.kt](android/app/src/main/java/com/healthchecker/companion/consumer/ConsumerSafFileChooserPolicy.kt)** — added `MAX_IMPORT_BYTES` and a pure, Android-framework-free `classifyImportRead()` classifier (testable without Robolectric) that the bridge delegates to.
- **[ConsumerLauncherActivity.kt](android/app/src/main/java/com/healthchecker/companion/ui/ConsumerLauncherActivity.kt)** — tracks `pendingImportUri` (set alongside the existing `takeSafReadGrant()` call, cleared on `onDestroy()`); installs the bridge as `"HCNativeImport"` in `configureWebView()`. Class doc comment updated to explain why this one bridge exists and why it's safe (no JS-supplied URI, no clinical data persisted natively, bytes only ever live transiently in memory).
- **[js/health_vault/mobile_consumer.js](js/health_vault/mobile_consumer.js)** `upload()` — now prefers `window.HCNativeImport.readSelectedRecordBase64()` when present (decodes the returned base64 into a plain in-memory `Blob`, which `fetch()` can serialize without touching `content://` at all), falling back to the original `<input>` `File` object when no bridge is installed (e.g. desktop/browser development) so behavior outside the governed WebView is unchanged. Added `describeUploadError()` to categorize failures instead of showing raw error text: file access/read failure, authentication failure (existing "Session expired"/"Password change required" messages, unchanged), upload HTTP error (existing `userFacingAuthError` mapping, unchanged), network/public-origin failure (new — a raw `TypeError` is now shown as "Upload could not reach the server. Check your connection and try again." instead of "Failed to fetch" verbatim), and invalid response (the existing technical `jsonContractError` string is now logged via `console.warn` rather than shown to the user directly, replaced with a friendly message).

### Preserved security properties (explicitly re-verified, unchanged by this fix)
`allowFileAccess` remains `false`; `file://` imports remain rejected (`isSafContentUri()` unchanged); no broad storage permission added (`AndroidManifest.xml` unchanged — still only `INTERNET` + Health Connect); SAF `content://` access remains narrowly scoped to exactly the user-selected URI (the bridge takes no parameter from JS at all); persistable permission still only taken when the provider grants it (unchanged `try/catch(SecurityException)`); production origin lock untouched (`ConsumerOriginLock.kt`/`ConsumerOriginPolicy.kt` not modified by this fix); no cleartext/localhost fallback in the release path (unchanged); authenticated upload still required (`Authorization: Bearer` still attached, server-side `_get_authenticated_patient()` unchanged); bearer tokens/recovery secrets/signing data/file contents are never logged (the new `console.warn` calls log only error codes and jsonContractError's existing path/status/content-type string, never the file bytes or the bridge's base64 payload); no plaintext temp file is ever written to disk (bytes exist only in process memory — native heap briefly during the bridge read, then JS heap as a `Blob` — never persisted).

### Regression tests added
- **[ConsumerRecordImportBridgeTest.kt](android/app/src/test/java/com/healthchecker/companion/consumer/ConsumerRecordImportBridgeTest.kt)** (new, Robolectric) — no-selection error, successful read returns exact byte-for-byte base64 of the selected content, unreadable URI reports an error without throwing, oversized document is rejected before encoding, and the pure `classifyImportRead()` classifier is exercised directly across every branch.
- **[tests/test_hc325_r6b_android_saf_record_import.py](tests/test_hc325_r6b_android_saf_record_import.py)** — updated the pre-existing "no JS bridge at all" assertion (written before this fix existed) to instead assert *exactly one*, narrowly-scoped bridge with no JS-supplied parameter; added tests confirming the native-bridge-preferred/`<input>`-fallback JS structure and that categorized error messages (not raw "Failed to fetch") are produced.
- Two other pre-existing tests with the same "no JS bridge at all" assumption were updated the same way, for the same reason: `tests/test_hc319d_mobile_consumer_launcher.py::test_launcher_is_hardened_and_has_no_javascript_bridge` and `tests/test_hc325_r4_universal_back_nav.py::test_android_system_back_uses_in_app_hierarchy_not_webview_history`. Neither test's actual purpose (WebView hardening, back-navigation not using WebView history) is weakened — both still assert everything they always asserted, plus the corrected, narrower bridge-count check.
- `tests/test_hc319c_mobile_identity_and_api_only.py::test_mobile_page_has_no_external_navigation_file_access_or_native_bridge` required **no change** — it checks the served `/mobile` HTML/JS content for the literal string `"addJavascriptInterface"`, which is a Kotlin/Android API call that never appears in JS/HTML regardless of this fix.

### Required final device UAT (operator-run retest, on the existing vc327 S24 installation once updated — see Section 26 for the exact artifact/versioning requirement)

Still a small, tightly bounded acceptance check — not a new install pattern beyond the one in-place APK update needed to get the fix onto the device. **Do not** clear app data, re-pair the device, or trigger Health Connect "Sync Now" as part of this UAT.

| Step | Action | Proves |
|---|---|---|
| A | Open the HealthChecker app normally after the in-place update (Section 26). | App still launches after `install -r`. |
| B | Sign in normally with the existing account (session/pairing expected to survive an in-place update). | Auth path and pairing state unaffected. |
| C | Open Records / Import. | Existing navigation to the import surface works. |
| D | Select a benign PDF, JSON, or image through the Android system file picker (SAF). | `ACTION_OPEN_DOCUMENT` / `content://` picker launches and returns a selection. |
| E | Tap "Upload securely" and confirm it succeeds — no "Failed to fetch." | **This is the specific defect this fix targets.** |
| F | Confirm the resulting record appears in Records. | Upload actually persisted and is visible, not just accepted. |
| G | Exercise Cancel once, then confirm a later selection still works. | Cancel doesn't leave stale picker/callback/`pendingImportUri` state. |
| H | If a harmless failed-import condition can be safely reproduced (e.g. an oversized or corrupt file), retry with a valid selection afterward and confirm the retry succeeds. | Confirms the originally-flagged "retry after failure" behavior, now that a real upload attempt can complete at all. |

**Constraints restated:** no clinical records may be deleted; no app data may be cleared; no pairing change may occur; no Sync Now may occur. An in-place `-r` APK update (Section 26) is required this time (unlike the original bounded UAT) because the fix is a code change that must actually be on the device to retest.

Until this retest passes and is recorded here, `SAF_IMPORT_DEVICE_UAT` stays open (prior attempt was `FAIL`; a fresh attempt after the fix has not yet been run) and `FINAL_DEVICE_ACCEPTANCE = WAITING_FOR_OPERATOR`. `HEALTHCHECKER_FINAL_GATE = NOT_YET_FINAL` until then (see Section 30).

## 18. Screenshot Policy Validation
**PASS** — proven by source + Robolectric-style unit tests, matches deployed production behavior.

- `ScreenshotPolicy.applyConsumerScreenshotPolicy()` only ever **clears** `FLAG_SECURE`, and is re-asserted at every lifecycle/navigation point (`onCreate`, `onResume`, `onPageFinished`, back-handling, `showConnectionError`) — nothing can leave the flag set.
- `ScreenshotPolicy.HAS_PROTECTED_SCREENS = false`; `SecureWindowPolicy.shouldSecureWindow()` unconditionally returns `false` — no global/Application-level FLAG_SECURE regression.
- No Android-native screen displays raw recovery codes/secrets; recovery *questions* (not codes/keys) run in the same WebView the design intentionally leaves unsecured. Pairing tokens/host credentials are protected via `EncryptedSharedPreferences`, not FLAG_SECURE — a documented, deliberate design choice, not an oversight.
- `ScreenshotPolicyTest.kt` asserts blocking is disabled globally, that `applyConsumerScreenshotPolicy` actively clears a pre-set flag, and that neither `ToolbarHarnessActivity` nor `PermissionsRationaleActivity` ever sets it.
- Confirmed byte-identical to the versions already present in the live HC310E comparison tree — this is deployed, proven behavior, not new/untested code.
- Cross-check: PR #8 (HC322) is superseded (its commit is NOT an ancestor of main) by PR #9 (HC322A), whose commit (`de9c010`) IS an ancestor of main — consistent with #8 being an earlier, replaced attempt.

No gaps found. No test additions required.

## 19. Health Snapshot Validation
**PASS**, with **one real gap found and fixed** (guardrail, not a live inconsistency).

- Mounted on authenticated mobile dashboard, cards clickable, drill-down/history, NORMAL/CAUTION/ATTENTION/UNKNOWN semantics — all confirmed present via `health_snapshot.py`/`health_snapshot.js` and corroborated by passing tests (`test_hc321_health_snapshot.py`, `test_hc325_r3_mobile_health_snapshot.py`, `test_hc325_r7d_mobile_runtime_closure.py`).
- **GAP FOUND (now FIXED): duplicate clinical threshold engine.** The canonical status thresholds live in `backend/health_vault/config/clinical_rules.json` (consumed by `clinical_rules.py`/`health_snapshot.py`), but `js/health_vault/clinical_rules.js` independently **hardcodes its own copy** of the same numeric bands (self-documented as "mirrors backend config subset"), with **no test previously enforcing agreement between the two**. Manual diff at review time showed all overlapping thresholds currently matched — but with no guardrail, either side could silently drift on a future edit. Fixed by adding [tests/test_hc329_clinical_rules_parity.py](tests/test_hc329_clinical_rules_parity.py): parses the JS `RULES` object and the backend JSON, asserts every metric present in the JS mirror has bit-for-bit matching bands in the JSON, and that the JS mirror never defines a metric unknown to the JSON. **Ran: 2/2 passed** against current values — confirms no live drift exists today, and will fail loudly if either file changes without the other.
- Provenance remains visible where intended: not independently re-verified beyond what the existing HC321/HC325 test suites already assert (test names above); no gap found.

## 20. Timeline Validation
**PASS.**

- Authenticated JSON API, not HTML-shell-able: `timeline()` (`api.py:439-472`) returns `JSONResponse` on both success and `AuthenticationError`; independently enforced globally by `api_never_html_shell` middleware (`api.py:285-295`), which converts any accidental `text/html`/`*+html` response on any `/api/*` route into a `502 api_html_forbidden` JSON error — a structural guarantee, not per-handler discipline.
- No O(n) full-vault-decrypt-per-record regression: `timeline.py:161-167` contains an explicit fix (with a comment referencing the prior HC321-UAT12F perf regression) that calls `store.list_measurements()` once and buckets by document (`_measurements_by_document`) before the per-document loop, rather than re-decrypting the whole vault index once per document.
- Filtered metric propagation, dedupe-without-deleting-stored-observations: consistent with passing `test_hc321_uat12h_timeline_filter_dd01`-lineage tests already in the suite (see Section 10, PR #7).
- No gaps found.

## 21. Trends Validation
**PASS** (no separate gap found beyond the Section 19 clinical-rules duplication, which also affects any Trends surface that reuses the same threshold tables — already remediated by the new parity test). No dedicated Trends-only defect identified during this review.

## 22. Health Connect Validation
**PASS** — all required invariants confirmed with exact values, live in `main`.

- `MAX_SAME_CHUNK_ATTEMPTS = 6` — `android/app/src/main/java/com/healthchecker/companion/sync/CompanionSyncRunner.kt:228`.
- `SAME_CHUNK_RETRY_BASE_DELAY_MS = 1000L` — `CompanionSyncRunner.kt:229`.
- Bounded exponential-backoff retry loop: `CompanionSyncRunner.kt:160-187`, hard stop at 6 attempts (confirmed already ahead of HC310E's older pinned snapshot — see Section 6).
- Manual/worker sync mutex: `SyncMutex.kt` — in-process `synchronized` gate plus a persisted `SharedPreferences` lease with a 15-minute staleness window for crash recovery; wired at both `MonitoringSyncWorker.kt` (worker) and `CompanionStatusActivity.kt` (manual) call sites via the same `prefs.syncMutex` instance.
- `source_record_id` idempotency: `vault_store.py:343-352` dedupe index keyed `(patient_id, canonical_metric, source_record_id)`; consulted in `ingestion.py:_persist_one` before any write, short-circuiting duplicates.
- Empty sync batches cannot erase prior measurement state: no delete/clear code path exists for empty input in `ingest_observations`; `companion/delivery.py:merge_iso_latest_maps()` is explicitly documented and tested ("Empty maps must not erase prior values") — covered by `tests/test_hc324_live_sync_freshness.py::test_empty_batch_does_not_erase_prior_latest_timestamps` (ran, PASSED).
- Failures surfaced, not swallowed: no bare `except: pass` found in `monitoring/`/`companion/`; ingestion loop appends structured errors to the returned summary and publishes `MONITORING_SYNC_FAILED`.
- One unrelated test-debt item found and fixed: `test_hc324_live_sync_freshness.py::test_catch_up_contract_in_companion_and_ui_sources` (plus 4 sibling tests in other files) asserted a stale literal `"versionCode = 324"`/`"0.324.0"` against `android/app/build.gradle.kts`, which correctly advanced to 327 through legitimate HC325-HC328 releases. Not a functional/security regression — fixed durably (parsed version + floor/relationship checks, not a re-pinned "327" literal) so a future legitimate vc328+ bump won't recreate the same failure; see Section 24.
- No gaps found beyond the test-debt item above, which is now fixed.

## 23. Device Pairing Validation
**PASS** (after the Section 14 fix).

- Pairing flow: `start_pairing()` requires an authenticated, non-default `patient_id` (`pairing.py:59-61`, rejects empty or `"default-patient"`); one-time pair code, hashed at rest, TTL-bounded (`PAIR_CODE_TTL_SECONDS`), throttled against brute force (`PAIR_CODE_MAX_ATTEMPTS`, constant-time code comparison).
- Device identity is always host-generated on confirm — never caller-selected (`pairing.py:145-146`).
- Device list/revoke scoping: main app (`api.py:700-719`) was already correctly patient-scoped; the standalone `companion_host` admin surface was not (Section 14 finding) and is now fixed and regression-tested.
- Current production state per assignment context (not independently re-verified against the live host, which is out of scope/read-only): active companion identity `hc3a_24692f58ff02fec8`, 1 active paired device, old `vc324` identity `hc3a_77d134db3200fc8c` recorded `revoked=True`/`revoked_at=None`. Per HC328 residual notes, this is a metadata inconsistency (revoked flag set without a timestamp) rather than a functional defect — revocation itself (`revoke_device()`, `pairing.py:198-213`) always sets both fields together in the code path used by both the main app and the now-fixed `companion_host` path, so this stale row predates or bypassed that code path; not reproducible from current `main` logic. No source change made against this historical row since HC310E/production vault data is read-only in this task.

## 24. Test Matrix

### Python backend suite (`pytest`, full repo, on `hc329-final-production-closeout`)

**Baseline run (before HC329 fixes):**

| TOTAL | PASSED | FAILED | SKIPPED | XFAILED | ERRORS | ENVIRONMENT_BLOCKED |
|---|---|---|---|---|---|---|
| 1492 | 1484 | 5 | 3 | 0 | 0 | 0 |

5 failures, all stale hardcoded Android version literals (`versionCode = 324`/`321`, `versionName = "0.324.0"`) left unbumped through legitimate HC325-HC328 releases that advanced the real value to 327 — not functional/security regressions:
- `tests/test_hc320d_release_recovery_packaging.py::test_packaging_and_signing_configuration_contains_no_secrets`
- `tests/test_hc321_b2_desktop_installer_closure.py::test_desktop_release_version_advanced_to_0_321_0`
- `tests/test_hc321_b3_android_signed_release.py::test_android_version_advanced_monotonically_to_321`
- `tests/test_hc321_b3_android_signed_release.py::test_provenance_script_parser_ok_and_emits_files`
- `tests/test_hc324_live_sync_freshness.py::test_catch_up_contract_in_companion_and_ui_sources`

**Interim run (after first HC329 fix pass — the 5 failures fixed by re-pinning to "327", 3 new tests added):**

| TOTAL | PASSED | FAILED | SKIPPED | XFAILED | ERRORS | ENVIRONMENT_BLOCKED |
|---|---|---|---|---|---|---|
| 1495 | 1492 | **0** | 3 | 0 | 0 | 0 |

**This interim fix was itself flagged on final review as a maintenance trap:** re-pinning the 5 failing assertions to the literal `327` makes them pass today but would recreate the identical failure the moment Android legitimately advances to vc328. Corrected in a follow-up pass (this revision) — see "Durable version-contract fix" below. The final PASSED/FAILED counts are unchanged (24 tests across the 4 affected files still pass, now durably), only the *implementation* of the fix changed.

**Durable version-contract fix (this revision):** replaced every hardcoded current-version literal in the 4 affected files with one of:
- a parsed-from-`build.gradle.kts` relationship check (`versionName == f"0.{versionCode}.0"`), which holds for every past and future release and needs no update ever;
- a fixed, intentionally-historical floor check (`versionCode > 320`, or `>= 321`/`>= 324` depending on which release each test's *actual* purpose is tied to) that never needs to move forward, since `versionCode` only increases;
- intentionally-historical literals left untouched and now explicitly documented as such in-code (`DESKTOP_VERSION = "0.321.0"` — the desktop release track is a separate, frozen lineage; `PRIOR_ANDROID_VERSION_CODE = 320` — a fixed governed monotonicity floor from HC321-B3; the README's own historical "321"/"320" documentation-line checks, which the code's own pre-existing comment already explained were intentional).

**Durability proof:** `android/app/build.gradle.kts` was temporarily edited to `versionCode = 328` / `versionName = "0.328.0"` (simulating the next legitimate release) and all 24 tests across the 4 affected files were re-run — all still passed, with zero edits to the tests themselves. The simulated edit was then reverted (`git diff` on the gradle file is empty, confirmed clean).

3 skipped, unchanged throughout, all environment-conditional (not hidden failures) — confirmed via static source inspection of the `pytest.skip(...)` call sites, cross-checked against this environment:
- `tests/test_hc304br1_proxy_topology.py` — certified Caddy v2.11.4 binary not installed on this host (`pytest.skip("certified Caddy v2.11.4 not installed")` / hash-mismatch variant).
- `tests/test_hc309r4d_synthetic_collector.py` — requires either an unsigned-script exception under this host's PowerShell Restricted execution policy, or actual elevated (admin) test identity — both explicitly gated, not available in this non-elevated dev shell.
- `tests/test_hc321_b2_desktop_installer_closure.py` — requires a governed managed Python installation not present on this host.

None of the 3 skips are `ENVIRONMENT_BLOCKED` in the sense of "should have run but couldn't due to an unexpected problem" — each is an explicit, source-documented `pytest.skip()`/`skipif` gate for infrastructure (Caddy binary, elevated Windows identity, managed Python distribution) that is legitimately absent from this engineering/review shell and would be present on a properly provisioned host. No test was deleted or weakened to make it pass.

**Final run (after the SAF import fix — Section 17 — added 3 more tests, and 2 more pre-existing tests needed the same "no JS bridge at all" → "exactly one narrowly-scoped bridge" correction as `test_hc325_r6b_android_saf_record_import.py` above):**

| TOTAL | PASSED | FAILED | SKIPPED | XFAILED | ERRORS | ENVIRONMENT_BLOCKED |
|---|---|---|---|---|---|---|
| 1498 | 1495 | **0** | 3 | 0 | 0 | 0 |

Two pre-existing tests initially failed after `ConsumerRecordImportBridge` was wired in, for the identical reason as the SAF test file — they asserted a blanket `"addJavascriptInterface" not in source`, written before any bridge existed. Fixed the same way (assert exactly one, narrowly-scoped bridge, not a blanket absence), preserving each test's actual purpose unchanged:
- `tests/test_hc319d_mobile_consumer_launcher.py::test_launcher_is_hardened_and_has_no_javascript_bridge` — still asserts every other hardening property (no FLAG_SECURE, no debug web-contents-debugging, no origin bypass, etc.), unchanged.
- `tests/test_hc325_r4_universal_back_nav.py::test_android_system_back_uses_in_app_hierarchy_not_webview_history` — still asserts back-navigation never touches WebView history (`webView.goBack`/`canGoBack` absent), unchanged; the bridge is unrelated to navigation.

`tests/test_hc319c_mobile_identity_and_api_only.py::test_mobile_page_has_no_external_navigation_file_access_or_native_bridge` required no change — it checks the served `/mobile` HTML/JS text for the literal string, which never appears there regardless (it's a Kotlin API call, not JS-visible).

3 new tests added in `tests/test_hc325_r6b_android_saf_record_import.py`: bridge-is-exactly-one-narrowly-scoped-reader, native-bridge-preferred-with-input-fallback, and upload-errors-are-categorized-not-raw-fetch-failures.

Reconciliation: 1492 (prior final) + 3 new SAF tests = 1495 passed; 3 skipped unchanged; total 1498.

### Android suite (Gradle/JUnit, `./gradlew testDebugUnitTest testReleaseUnitTest`)

**Before the SAF fix:**

| Variant | Tests | Failures | Errors | Skipped |
|---|---|---|---|---|
| Debug | 168 | 0 | 0 | 0 |
| Release | 156 | 0 | 0 | 0 |

(156 vs 168 is expected and correct — 3 test files depending on the debug-only `ToolbarHarnessActivity` test harness were moved to a `testDebug`-only source set as part of this closeout; see Section 25.)

**After the SAF fix (current):**

| Variant | Tests | Failures | Errors | Skipped |
|---|---|---|---|---|
| Debug | 173 | 0 | 0 | 0 |
| Release | 161 | 0 | 0 | 0 |

(+5 in both variants: the new `ConsumerRecordImportBridgeTest.kt`, which lives in the shared `src/test/` source set — 5 cases: no-selection, successful read, unreadable URI, oversized document, and the pure `classifyImportRead()` classifier across every branch. The debug/release gap stays at 12, unrelated to this fix.)

### Coverage against the assignment's required regression areas
Health Connect sync/retry (Section 22) ✓, SyncMutex (Section 22) ✓, patient-scope (Section 14, negative tests ran+passed) ✓, ingestion (Section 6/22) ✓, companion pairing/device tests (Section 23, new regression test added) ✓, device revoke tests (Section 14/23, new regression test added) ✓, authentication lifecycle (Section 12) ✓, password lifecycle (Section 12, 14/14 ran+passed) ✓, recovery (Section 13) ✓, navigation (Section 7 tracker, `ConsumerInAppBackPolicyTest.kt`) ✓, screenshot-policy (Section 18, `ScreenshotPolicyTest.kt`) ✓, Health Snapshot (Section 19, new parity test added) ✓, Timeline (Section 20) ✓, Trends (Section 21) ✓, record upload/import (Section 17 — real defect found and fixed, new bridge tests added) ✓ engineering-side, device retest pending, production-origin (Section 16, `ConsumerOriginLockTest.kt`) ✓, vault integrity/security (Section 15) ✓, public/mobile API contract (`test_hc325_r7a_authenticated_json_contract.py`, part of the passing suite) ✓.

## 25. Android Build Result

Using the existing approved Gradle build process (`android/app/build.gradle.kts`), no invented/replacement signing material, no debug-keystore fallback.

| Status | Value |
|---|---|
| `HC329_RELEASE_BUILD_COMPILE` | **PASS** — `./gradlew assembleRelease testReleaseUnitTest testDebugUnitTest` → `BUILD SUCCESSFUL in 1m 24s`, 85 actionable tasks. |
| `HC329_PRODUCTION_SIGNING_EXECUTION` | **BLOCKED_PENDING_OPERATOR** — this build environment has only 2 of the 4 required signing env vars set (`HC_ANDROID_KEYSTORE_FILE`, `HC_ANDROID_KEY_ALIAS`; `HC_ANDROID_KEYSTORE_PASSWORD` and `HC_ANDROID_KEY_PASSWORD` are absent). The build script itself fails closed on a partial signing environment (`hc_android_signing_env_incomplete`) rather than silently falling back to debug signing — this is by design. Per governance, no attempt was made to search for, print, expose, reconstruct, or guess the missing credentials, and no replacement key was created. |
| `PRODUCTION_SIGNING_LINEAGE` | **PROVEN** — established HC328 evidence: production signer SHA256 `0ee183dcb1e88349d6352110e8d12cae9eb712d559925bbaf05030178f9b9588`; vc327 production APK SHA256 `1645E3C868E1CEEFEFB27A40A2ADA83A5B8A330BB59C0FEB36FDBA40AA4A8BDC`, production-v2 signed and successfully installed in-place. |
| `PRODUCTION_SIGNER_CONTINUITY` | **PROVEN_FROM_HC328** — no evidence of lost or compromised signing material was found anywhere in this review; absence of two passwords in this dev/review shell is an access-control fact about this process, not a finding about the signer itself. |

**One pre-existing, unrelated Android build defect found and fixed during this attempt:** `compileReleaseUnitTestKotlin` failed on the first build attempt with `Unresolved reference: ToolbarHarnessActivity` in `ScreenshotPolicyTest.kt`, `StatusScreenNavigationUiTest.kt`, `WindowInsetApplierTest.kt` — these three unit tests live under the shared `src/test/` source set but reference `ToolbarHarnessActivity`, which is defined only under the debug-only `src/debug/` source set (confirmed via `grep -rl "class ToolbarHarnessActivity" app/src/`), so they never compiled against the `release` variant. Pre-existing on `main` (confirmed via `git status` showing no prior local changes to these files before this task touched them). Fixed by moving all three files to a new `src/testDebug/` source set (the standard Android Gradle convention for debug-variant-only unit tests) — zero change to any shipped/`main`-sourceSet code, only test compilation scoping. First build attempt: `FAILURE` on `compileReleaseUnitTestKotlin`. Second build attempt (after the fix): `BUILD SUCCESSFUL`.

## 26. Release Artifact Details

**This is an UNSIGNED RELEASE BUILD FOR SOURCE/BUILD VALIDATION ONLY. It is not a production release artifact, has not been installed anywhere, and its signer state (absent) is not a regression against the signed production APK — it is the expected shape of an unsigned validation build.**

### Post-SAF-fix validation build (current)

| Field | Value |
|---|---|
| versionCode | **327 (unchanged in this build — see version-bump recommendation below).** |
| versionName | "0.327.0" |
| APK path | `android/app/build/outputs/apk/release/app-release-unsigned.apk` (local build output only, not distributed) |
| APK size | 3,984,155 bytes |
| APK SHA256 (this unsigned validation build, includes the SAF fix) | `a61a83d85a9af783149226cc2f912a34a8a2bd868edd0f447bfcf140253c9abc` |
| Signer identity | **None — unsigned.** Not comparable to, and not a regression against, the production signer (`0ee183dcb1e88349d6352110e8d12cae9eb712d559925bbaf05030178f9b9588`) or the running production APK SHA256 (`1645E3C868E1CEEFEFB27A40A2ADA83A5B8A330BB59C0FEB36FDBA40AA4A8BDC`) — those remain the authoritative, currently-installed production artifact, untouched by this task. |
| Signing verification result | N/A (unsigned by design for this validation build) |
| Build test result | `testDebugUnitTest`: **173 tests, 0 failures, 0 errors, 0 skipped** (168 + 5 new `ConsumerRecordImportBridgeTest` cases). `testReleaseUnitTest`: **161 tests, 0 failures, 0 errors, 0 skipped** (156 + the same 5 new cases; the release/debug gap stays at 12, unrelated debug-only-harness tests). |
| Installed anywhere? | **No.** Not installed on any device, emulator, or production host. |

### Version-bump recommendation for the device retest (not applied to source — operator decision)

The device already has vc327 (`versionCode=327`) installed. This validation build was produced *without* bumping the version, since bumping a production version number is a release-governance action this task should recommend, not silently take. **For the in-place update needed to actually retest on the S24 (Section 17), the next governed release should be `versionCode = 328`, `versionName = "0.328.0"`** — following the same `versionName == "0.<versionCode>.0"` contract now durably enforced by `tests/test_hc321_b3_android_signed_release.py` (Section 24). This recommendation is not applied to `android/app/build.gradle.kts` in this branch; the operator's governed release process should apply it (alongside the signed build, since an unsigned APK cannot `install -r` over the currently-installed signed vc327 build — see `PRODUCTION_SIGNING_EXECUTION` above).

### Prior validation build (pre-SAF-fix, superseded)
For continuity: the build produced earlier in this closeout (before the SAF defect was found) had APK SHA256 `4022f75f170955dd20c103826584a7459379c0c99c99c6cd7c24f60aa1b8c929`, `testDebugUnitTest` 168/168, `testReleaseUnitTest` 156/156. That artifact does **not** contain the SAF fix and must not be used for the device retest.

## 27. Production Normalization Plan

**Status: WAITING_FOR_OPERATOR_AUTHORIZATION.** Nothing in this section has been executed. This is a plan for a human operator to run later, on the FINANCE host, using governed credentials this task never had or sought.

**Design correction from the prior revision of this report:** the previous plan proposed copying the merged `main` tree directly into `C:\rasib\source\HealthChecker-HC310E`, in place. That was rejected on final review — `HealthChecker-HC310E` is the known-good, proven runtime and the natural rollback target; overwriting it in place removes the one thing that made rollback trivial. This revision instead builds a **side-by-side governed production candidate** at a separate path (suggested: `C:\rasib\source\HealthChecker-Production-HC329`), validates it offline/on a non-production port, and only then cuts the scheduled task over to it — with `HealthChecker-HC310E` left completely untouched throughout, so rollback is "point the task back," not "reconstruct from backup."

### PHASE A — PRECHECK
1. Current HC310E HEAD/worktree state: `git -C C:\rasib\source\HealthChecker-HC310E status` and `rev-parse HEAD` — expect detached HEAD at `41bfc29` plus the 7 known worktree modifications enumerated in Section 6 (all now confirmed reconciled into `main`, none unique). Record verbatim; do not modify.
2. Current scheduled-task command: `Get-ScheduledTask -TaskName HealthCheckerConsumerRuntime | Get-ScheduledTaskInfo`, plus the task's Action/Arguments (must currently read `...\HealthChecker-HC310E\scripts\start_healthchecker_production.ps1 -ConfigPath ...\production.json`). Record verbatim as the rollback target string.
3. Current 8766 PID/listener: identify the process bound to `127.0.0.1:8766` (e.g. `Get-NetTCPConnection -LocalPort 8766 | Select OwningProcess`) and record its PID and start time.
4. Current local/public health: `http://127.0.0.1:8766/healthz` and `https://health.capitalstratasystems.com/healthz` (or equivalent) both return 200 before any change.
5. Current active companion identity: confirm still `hc3a_24692f58ff02fec8`.
6. Paired-device count: confirm still `1`.
7. Current vault integrity: confirm `backend.health_vault.production_runtime.create_production_vault()` currently opens cleanly against `C:\ProgramData\HealthChecker\data\vault` with `C:\ProgramData\HealthChecker\secrets\vault.key` — **read-only check, do not write**.
8. Current production config hash: compute and record a SHA256 of `C:\ProgramData\HealthChecker\config\production.json` as it stands today (untouched baseline for later comparison).
9. Current source hashes for critical live files: SHA256 of the 7 files enumerated in Section 6 as they currently stand in `HealthChecker-HC310E`'s worktree, so any later drift is independently detectable.

### PHASE B — CREATE CANDIDATE
1. Create a clean checkout or git worktree at the exact merged HC329 `main` commit, at a new, clearly-named governed path — e.g. `C:\rasib\source\HealthChecker-Production-HC329` (or `git worktree add C:\rasib\source\HealthChecker-Production-HC329 <merge-commit-sha>` from a bare/managed clone). **Do not** derive this from a copy of `HealthChecker-HC310E`'s (dirty) working tree — check out the actual merged commit fresh.
2. **Do not modify `HealthChecker-HC310E`** — no files copied into it, no branch switch, no checkout, no clean, in either direction.
3. Production configuration (`C:\ProgramData\HealthChecker\config\production.json`) and the vault (`C:\ProgramData\HealthChecker\data\vault`, `C:\ProgramData\HealthChecker\secrets\vault.key`) remain external, under `ProgramData`, exactly as today — the candidate checkout references them by the same config path convention as HC310E does; nothing about this phase moves or duplicates production config/vault state into the source tree.

### PHASE C — OFFLINE/CANARY VALIDATION (before any cutover, without touching live production)
1. Make a **copy** of the production vault (`C:\ProgramData\HealthChecker\data\vault` → a separate validation-only path) — the candidate must never open the live vault directly during this phase. Validate the copy opens read-only via `create_production_vault()` pointed at the copy before using it for anything else.
2. Use a copied/temporary configuration file (a duplicate of `production.json` with `HC_VAULT_ROOT` repointed at the copied vault) — never the live `production.json` in this phase.
3. Bind the candidate to a **non-production port**, e.g. `8776` — **do not bind to or touch 8765** (CSS) or rebind the live `8766` listener.
4. With the candidate running against the copied vault/config on port 8776:
   - Confirm the process starts cleanly (no fail-closed exceptions from vault/config validation).
   - Confirm `http://127.0.0.1:8776/healthz` (or equivalent) returns 200.
   - Confirm `/mobile` and other static/mobile asset routes load.
   - Confirm the auth route behaves correctly (login succeeds with a known-good credential against the copied vault; invalid credentials are rejected).
   - Confirm Snapshot/Timeline/Trends routes return authenticated JSON as expected against the copied vault's data.
   - Confirm patient scoping: a second patient (if the copied vault has one, or one created in the copy for this purpose) cannot see the first patient's data through these routes.
   - Confirm the candidate can authenticate against and open the copied encrypted vault using the governed read/test configuration, without printing, logging, or otherwise exposing the vault key or any secret material during validation.
5. **Do not proceed to Phase D unless every check in this phase passes.** If any candidate validation step cannot be safely performed with the tools/access available at execution time (e.g., no safe way to copy the vault without an operator-supervised step), state that precisely as a blocker in this report rather than skipping or improvising around it.

### PHASE D — CUTOVER (only after Phase C passes AND an operator explicitly authorizes production normalization)
1. Stop only the `HealthCheckerConsumerRuntime` scheduled task (`Stop-ScheduledTask -TaskName HealthCheckerConsumerRuntime`). No other process, service, or task is touched.
2. `HealthChecker-HC310E` is left exactly as it was at PRECHECK — untouched, not deleted, not modified.
3. Change the `HealthCheckerConsumerRuntime` scheduled task's Action/Arguments to point at the governed HC329 production-candidate path (e.g. `...\HealthChecker-Production-HC329\scripts\start_healthchecker_production.ps1 -ConfigPath ...\production.json`, the same live config path as before — config itself is not duplicated, only the source-tree pointer changes) — or use an equally atomic, reversible source-selection mechanism if the task supports one (e.g. an environment-variable-driven source root the launch script resolves, so cutover is a one-line, instantly-revertible change). **Never point the scheduled task at a mutable developer working tree** (i.e., never at `HealthChecker-Main` itself) — always at a dedicated, governed, checked-out production path.
4. Start `HealthCheckerConsumerRuntime` (`Start-ScheduledTask`).
5. Verify `127.0.0.1:8766` is healthy again (new PID, 200 on `/healthz`).

### PHASE E — POST-CUTOVER SMOKE
1. Local `http://127.0.0.1:8766/healthz` → 200.
2. Public `https://health.capitalstratasystems.com/healthz` (or equivalent) → 200 through Cloudflare.
3. `/mobile` loads.
4. Authenticated login succeeds with a known-good credential.
5. Snapshot returns expected NORMAL/CAUTION/ATTENTION/UNKNOWN data for the known active patient.
6. Snapshot drill-down/history loads.
7. Timeline returns authenticated JSON (never an HTML shell).
8. Trends returns authenticated JSON with expected aggregation.
9. Patient isolation: no cross-patient data visible through any of the above.
10. Live paired-device count remains `1`.
11. Active device remains `hc3a_24692f58ff02fec8`.
12. No unexpected clinical record/measurement count change versus the PRECHECK baseline.
13. CSS/port 8765 untouched (still the same PID/process as PRECHECK).
14. Cloudflare configuration unchanged (no config action taken by this plan at all).
15. No Android action of any kind (no install/uninstall, no data clear, no re-pair).
16. No Health Connect "Sync Now" action of any kind.

### PHASE F — ROLLBACK (fast and deterministic — see Section 28 for full detail)
1. Stop only `HealthCheckerConsumerRuntime`.
2. Restore the scheduled task's Action/Arguments to the exact PRECHECK string recorded in Phase A step 2 — pointing back at the untouched `HealthChecker-HC310E` runtime.
3. Start `HealthCheckerConsumerRuntime`.
4. Verify local/public endpoints, pairing identity/count, and vault integrity all match PRECHECK.

Because `HealthChecker-HC310E` was never modified during Phase B-E, rollback does **not** require reconstructing it from any backup — restoring the scheduled-task pointer is sufficient, unless the untouched runtime itself is independently found to be damaged (which this plan gives no reason to expect, since it was never written to).

## 28. Rollback Plan

1. **Exact rollback source:** the untouched `C:\rasib\source\HealthChecker-HC310E` runtime — the same one that was serving production immediately before cutover, left byte-for-byte as-is throughout Phases B-E. No backup restore is needed for the source tree itself.
2. **Exact scheduled-task restore:** reset `HealthCheckerConsumerRuntime`'s Action/Arguments to the exact command string recorded in Phase A precheck step 2 (`...\HealthChecker-HC310E\scripts\start_healthchecker_production.ps1 -ConfigPath ...\production.json`).
3. **Exact restart procedure:** `Stop-ScheduledTask -TaskName HealthCheckerConsumerRuntime` → restore the task Action/Arguments → `Start-ScheduledTask -TaskName HealthCheckerConsumerRuntime`.
4. **Expected recovery checks:** re-run the full Phase E smoke list against the rolled-back (HC310E) runtime; additionally confirm the running APK SHA256 and active device identity are unchanged throughout (rollback must never touch the phone/pairing state — it is a source-selection change only).
5. If vault integrity is ever in doubt after a failed cutover attempt, validate a **copy** of the vault (never the live path) via `create_production_vault()` before considering any vault-level recovery action — never attempt vault recovery live against the production path without that validation first. Since Phase B-E never wrote to the live vault (only a copy was used for Phase C, and the live vault is opened read/write only by whichever runtime is actually serving requests), a cutover failure should not, by construction, ever put the live vault in a suspect state — this step exists for defense in depth, not because the plan expects to need it.

### ABORT CONDITIONS (cutover in Phase D must not proceed, or must be immediately rolled back per Phase F, if any of these occur)
- Vault integrity failure (`create_production_vault()` raises, or schema/auth check fails, against the live vault post-cutover).
- Auth failure (a known-good credential is rejected post-cutover).
- Public endpoint failure (Cloudflare/public origin unreachable or non-200 post-cutover).
- Patient-scope failure (any smoke check shows data crossing patients).
- Device identity mismatch (active identity changes from `hc3a_24692f58ff02fec8` without operator action).
- Pairing count unexpected (count changes from `1` without operator action).
- APK signer mismatch (this plan takes no Android action at all; if any future plan ever installs an APK, the signer SHA256 must still be `0ee183dcb1e88349d6352110e8d12cae9eb712d559925bbaf05030178f9b9588`).
- CSS/port 8765 impacted in any way.
- Cloudflare configuration altered in any way.
- Unexpected clinical-data mutation (any smoke check shows different measurement/document counts than PRECHECK recorded).

**This plan is not executed as part of HC329. Deployment status: WAITING_FOR_OPERATOR_AUTHORIZATION. HC310E remains the untouched, proven rollback runtime throughout.**

## 29. Residual Risks

1. **SAF import device retest pending — this is the sole remaining blocker on the final gate** (Section 17, Section 30). The physical-device UAT found and this revision fixed a real defect (Chromium WebView cannot reliably upload `content://`-backed files via `fetch()`; fixed with a narrowly scoped native read bridge). The fix is complete and fully unit-tested, but per the explicit HC329 acceptance criterion this cannot be rounded up to PASS without a fresh operator-run physical-device retest against an in-place update containing the fix (Section 17, Section 26). `SAF_IMPORT_DEVICE_UAT`: prior attempt `FAIL`; retest not yet performed.
2. **Recovery timing-symmetry nit** (Section 13, Section 11 finding #3): the timing-equalizer in `recovery_start()` only runs on the "fake account" branch. Response bodies are identical either way (no information disclosed), so this is accepted as a low-severity theoretical residual, not fixed in this closeout.
3. **Dead recovery-code modules** (Section 11 finding #4): an unreachable, superseded HC311 recovery design remains in the tree (`recovery_enrollment.py` and siblings). No attack surface (unreachable), but a candidate for a future cleanup pass outside HC329 scope.
4. **Operational note on live HC310E CSS theming** (Section 6): if the live production stylesheet is missing the `body.mobile-consumer` base rule that its own "backport" block depends on via CSS custom properties, that's a plausible live-only Snapshot theming defect. This is a runtime/production concern, not a main-branch source gap (main already has the correct, complete version) — recommend an operator visually check the live `/mobile` Snapshot page before/after any future normalization, since this task could not modify or fully verify the read-only HC310E runtime.
5. **`origin/origin` dangling/malformed git ref** (Section 10 cleanup plan): flagged for review, not deleted, since ref deletion is a shared-state action outside this task's authorization.
6. **Companion device revocation metadata inconsistency** (Section 23, carried from HC328): the old `vc324` identity (`hc3a_77d134db3200fc8c`) shows `revoked=True`/`revoked_at=None` in production. Current `main` code always sets both fields together on revoke, so this stale row predates or bypassed that path; it is a data artifact in the live vault, not a code defect, and this task did not (and could not, per Section 3/4 rules) mutate the production vault to backfill it.
7. **Android debug-keystore/lint-vital and deprecation warnings**: `assembleRelease` build emits Kotlin deprecation warnings (`databaseEnabled`, `allowFileAccessFromFileURLs`, `allowUniversalAccessFromFileURLs` setters) — informational, not build-blocking, tracked as ordinary tech debt.
8. **New JavaScript bridge is a (narrow, deliberate) increase in WebView attack surface** (Section 17): `ConsumerRecordImportBridge` is the first `@JavascriptInterface` this app has ever installed. Risk is judged low because (a) the WebView is already fail-closed to the governed production origin only (Section 16 — untrusted content can never load here to begin with), (b) the bridge takes zero parameters from JS and can only read the one URI the native picker already recorded, and (c) it performs no writes and persists nothing. Flagged here for visibility, not because a specific exploitation path was found.

## 30. Final Gate

| Gate | Status | Evidence |
|---|---|---|
| Current HC329 source contains all valid production behavior | **PASS** | Section 6 |
| No valid application logic remains only in HC310E | **PASS** | Section 6 |
| No unresolved CRITICAL security findings | **PASS** | Section 11 |
| No unresolved HIGH security findings | **PASS** | Section 11 |
| Final regression suite passes | **PASS** | Section 24 — 1498 total, 1495 passed, 0 failed, 3 skipped (all environment-conditional, documented), 0 errors |
| Patient isolation passes | **PASS** | Section 14 (1 gap found and fixed, regression test added) |
| Vault integrity/security passes | **PASS** | Section 15 |
| Authentication lifecycle passes | **PASS** | Section 12 |
| Recovery lifecycle passes | **PASS** | Section 13 |
| Production-origin lock passes | **PASS** | Section 16 |
| Health Connect bounded retry passes | **PASS** | Section 22 |
| Idempotency/duplicate prevention passes | **PASS** | Section 22 |
| Companion pairing/revoke authorization passes | **PASS** (after fix) | Section 14, 23 |
| Snapshot passes | **PASS** (1 gap found and fixed) | Section 19 |
| Timeline passes | **PASS** | Section 20 |
| Trends passes | **PASS** | Section 21 |
| Screenshot policy passes | **PASS** | Section 18 |
| SAF import passes OR is explicitly/legitimately removed from scope | **NOT YET FINAL** — `ENGINEERING_SOURCE_CLOSEOUT = PASS` (defect found via device UAT, fixed this revision), `SAF_IMPORT_DEVICE_UAT` prior attempt `FAIL`, retest pending, `FINAL_DEVICE_ACCEPTANCE = WAITING_FOR_OPERATOR` | Section 17 — not removed from scope; the physical-device UAT found and this revision fixed a real defect (Chromium WebView content:// upload failure); a fresh device retest against the fix is required before this gate can read PASS |
| Android release build succeeds, or only production signing is operator-blocked | **PASS** (build succeeds; signing execution is BLOCKED_PENDING_OPERATOR, not the build itself) | Section 25 |
| Final HC329 worktree is clean after commits | **PASS** | `git status` clean after the commits in Section 16 |
| Final PR is prepared and mergeable | **PASS**, held as DRAFT pending this gate | PR #29, `mergeable: MERGEABLE`, `isDraft: true`; not merged |
| Production normalization plan complete | **PASS** | Sections 27-28 |
| Rollback plan complete | **PASS** | Section 28 |
| Legacy PR disposition complete | **PASS** | Section 10 |

**One outstanding item, now a fixed-but-unretested defect rather than a pure inference gap:** the physical-device UAT this revision found a real defect — SAF file upload failed with "Failed to fetch" on the S24, root-caused to Android WebView's unreliable handling of `content://`-backed uploads via `fetch()`/`FormData` (Section 17). It has been fixed in source (`ConsumerRecordImportBridge`), fully unit-tested (Android + Python), and every previously-verified security property was re-confirmed intact. What remains is confirming the fix actually resolves the symptom on the physical device — which requires an in-place APK update (Section 26) and a retest, not further source review. This is the only thing separating this closeout from the full COMPLETE gate.

`ENGINEERING_SOURCE_CLOSEOUT = PASS` (fixed this revision)
`SAF_IMPORT_DEVICE_UAT`: prior attempt = `FAIL`; retest not yet performed
`FINAL_DEVICE_ACCEPTANCE = WAITING_FOR_OPERATOR`

# HEALTHCHECKER_FINAL_GATE = NOT_YET_FINAL

Once the Section 17 device retest is performed (after the in-place update specified in Section 26) and its result recorded in this report (PASS with evidence, or FAIL with the specific broken step), this line should be updated: if PASS, to `HEALTHCHECKER_COMPLETE_READY_FOR_FINAL_PRODUCTION_NORMALIZATION`; if FAIL, to `HEALTHCHECKER_NOT_COMPLETE` with the specific remaining defect as the blocker. This is not `HEALTHCHECKER_NOT_COMPLETE` today — the engineering/source closeout, including the SAF fix, is complete — it is withheld from the top-level COMPLETE declaration solely pending the one physical-device retest above.
