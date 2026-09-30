# HC-355 / HC-355A — Governed Production Release Evidence

## Owner authorization

- Owner explicitly authorized release: `RELEASE_AUTHORIZED=YES`
- Authorization timestamp (owner instruction received): 2026-09-30T00:16:32-04:00
- Authorized HEAD at time of owner authorization:
  `ae0441aa525c2bcf99d7d84bb1410763961a2657`

## Qualified code identity

```
QUALIFIED_CODE_HEAD=ae0441aa525c2bcf99d7d84bb1410763961a2657
```

Verified independently at release time via:
```
git rev-parse HEAD        -> ae0441aa525c2bcf99d7d84bb1410763961a2657
git cat-file -t HEAD       -> commit
git status --short         -> only pre-disclosed HC-355 local evidence
                                artifacts untracked; no unexplained
                                tracked source modifications
```

## Released artifact

```
APK_PATH=android\app\build\outputs\apk\release\app-release.apk
PACKAGE=com.healthchecker.companion
VERSION_NAME=0.343.0
VERSION_CODE=343
APK_SHA256=3F00366EED59153269A0809DB6E7385EA829D3FE4476915AE5563FA69AA2C522
SIGNER_SHA256=0ee183dcb1e88349d6352110e8d12cae9eb712d559925bbaf05030178f9b9588
```

Re-verified directly from the on-disk artifact at release time without
rebuilding or resigning:
- `Get-FileHash -Algorithm SHA256` on the existing `app-release.apk` →
  exact match to the expected SHA256 above.
- `aapt2 dump badging` on the existing artifact → confirms
  `package='com.healthchecker.companion' versionCode='343'
  versionName='0.343.0'`.
- `apksigner verify --print-certs` on the existing artifact → confirms
  signer SHA-256 digest exact match to the governed production-v2 signer.

No rebuild, no resign, no new artifact was produced or substituted.

## Pre-release runtime state

```
TASK_STATE=Running
SUPERVISOR=live (task action unchanged, per docs)
CHILD_PID=27872 (python.exe, start time 2026-09-29 22:29:58, Responding=True)
HEARTBEAT=fresh (at_utc within ~1s of check time)
ORPHAN_CONDITION=none (single listener on 127.0.0.1:8766, matches recorded child)
LOCAL_HEALTH=200
PUBLIC_HEALTH=200 (https://health.capitalstratasystems.com/healthz)
PUBLIC_MOBILE=200 (https://health.capitalstratasystems.com/mobile)
```

## Release action performed

The qualified artifact (`app-release.apk`, SHA256
`3F00366E...9B9588`) was **already** the artifact:
- installed and running on the owner's physical S24
  (`versionCode=343`, `versionName=0.343.0`, `lastUpdateTime=2026-09-29
  16:08:19`, confirmed via read-only `adb shell dumpsys package` — no
  reinstall performed), and
- exposed through the established production web/mobile distribution path
  (`https://health.capitalstratasystems.com/healthz` and `/mobile`, both
  responding 200 from the same governed runtime this whole session).

No new deployment, install, uninstall, rebuild, or resigning action was
required or performed. This release evidence formally establishes and
records that this exact, already-qualified artifact is the released
production candidate, per owner instruction to avoid manufacturing
unnecessary deployment activity when the qualified artifact is already the
one in production use.

```
DIRECT_APK_RELEASE=NOT_REQUIRED (already installed and UAT-verified on S24)
WEB_RELEASE_STATE=ALREADY_LIVE (public endpoint serving this runtime)
MOBILE_RELEASE_STATE=ALREADY_LIVE (public /mobile endpoint responding 200)
PLAY_STORE_RELEASE=NOT_AUTHORIZED (not performed, not requested)
```

## Post-release verification

Re-checked after formalizing this release record (no runtime restart
performed — a healthy runtime was not restarted merely to prove it can
restart, per instruction):

```
TASK_STATE=Running
SUPERVISOR=LIVE
CHILD=LIVE (PID 27872, unchanged throughout this session)
HEARTBEAT=FRESH
ORPHAN_CONDITION=NONE
LOCAL_HEALTH=200
PUBLIC_HEALTH=200
PUBLIC_MOBILE=200
AUTHENTICATION_PATH=AVAILABLE (production auth enforcement unchanged this
  session; verified previously under HC-355 Gate 6, no code touched since)
HEALTH_DATA=NO_EVIDENCE_OF_LOSS (ProgramData runtime/config/state
  directories untouched; S24 app data not cleared/uninstalled at any point)
REPOSITORY_TRACKED_CHANGES=NONE_UNEXPECTED (only this evidence-record
  commit, documentation-only)
```

## Rollback readiness

```
ROLLBACK_RUNBOOK=docs/ops/HC355A_SERVER_ROLLBACK_RUNBOOK.md (present, usable)
KNOWN_GOOD_ROLLBACK_TARGETS=
  f649036 (HC-354 — prior HEAD, runtime supervisor fix)
  31c4023 (HC-353 — auth recovery screenshot protection)
  5bfab5e (HC-352 — consumer UI polish)
  d9199a0 (HC final closure — password recovery screenshot protection baseline)
```

Rollback was not executed — no release failure occurred.

## Deviations

None. The release consisted entirely of verification and formal
recording; no code, configuration, signing identity, health data, or
Android installation state was changed.

## Final release disposition

```
PRODUCTION_RELEASE_STATUS=RELEASED
```

All mandatory pre-release and post-release checks (artifact identity,
signer identity, runtime health, endpoint health, data integrity, rollback
readiness) passed without deviation.

---

Distinguish:

```
QUALIFIED_CODE_HEAD=ae0441aa525c2bcf99d7d84bb1410763961a2657
FINAL_EVIDENCE_HEAD=<the commit that adds this file, docs-only>
```

The `FINAL_EVIDENCE_HEAD` commit is documentation-only and does not change,
supersede, rebuild, or resign the qualified application artifact identified
by `QUALIFIED_CODE_HEAD`.
