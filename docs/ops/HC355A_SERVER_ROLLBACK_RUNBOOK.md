# HC-355A — Server-Side Rollback Runbook (Consumer Runtime)

Status: DOCUMENTED_AND_ENGINEERING_VALIDATED (see "Validation performed" below).
Physical destructive rollback: NOT_PERFORMED (not required to validate this
procedure; see rationale).

## 1. Scope

This runbook covers rollback of the **server-side consumer runtime** only:

- the `HealthCheckerConsumerRuntime` scheduled task
- `scripts/start_healthchecker_production.ps1` (the governed supervisor)
- the uvicorn child process it owns (`backend.health_vault.api`)
- the Cloudflare tunnel that exposes `https://health.capitalstratasystems.com`

It does **not** cover:

- the Android application (APK rollback is a separate, already-documented
  `adb install -r` procedure using a preserved prior-VC artifact —
  see HC-355 Gate 10 evidence)
- health-data schema/content
- signing credentials
- Cloudflare DNS/account configuration

## 2. Architecture facts this runbook depends on

Verified directly against the running system as of
`HEAD=f649036f9fae38367a32ce32984b0f2a0a01a481`:

- The scheduled task action is:
  `powershell.exe -NoProfile -ExecutionPolicy Bypass -File
  "C:\rasib\source\HealthChecker-\scripts\start_healthchecker_production.ps1"
  -ConfigPath "C:\ProgramData\HealthChecker\config\production.json"`
- `Resolve-HealthCheckerInstallRoot.ps1` resolves the install root as the
  **parent directory of the scripts folder** — i.e. the repository working
  tree itself (`C:\rasib\source\HealthChecker-`). There is no separate
  "deployed" copy; **the git working tree IS the running install**.
- The runtime child is started via
  `python -m uvicorn backend.health_vault.api:create_health_vault_app --factory`
  with `WorkingDirectory = $installRoot`, so the exact Python source on disk
  at rollback time is what serves traffic after a restart.
- Runtime state lives entirely outside the repo, under
  `C:\ProgramData\HealthChecker\` (config, PID, heartbeat, logs) and is
  **not affected by a git checkout** of the repo.
- Consequence: **the smallest reliable server rollback mechanism is a `git
  checkout <prior-good-commit>` of the working tree, followed by a governed
  restart of the scheduled task.** No file copies, no reinstall, no separate
  deployment pipeline exists or is required.

## 3. Rollback targets available today

| Tag / commit | Description |
|---|---|
| `f649036` (current, `main` HEAD) | HC-354 runtime supervisor fix — current production |
| `31c4023` | HC-353 — auth recovery screenshot protection + mobile asset cache fix |
| `5bfab5e` | HC-352 — consumer UI polish |
| `d9199a0` | HC final closure — password recovery screenshot protection (pre-HC-352 baseline) |
| `05100d1` | HC-351 — supervisor PID identity hardening |

Any of these commits builds/runs the same `backend.health_vault` API used by
the current production runtime; none of them touch schema or on-disk health
data. All are already pushed to `origin/main` history, so they can be
recovered even if the local working tree were lost.

## 4. Pre-rollback evidence capture

Before touching anything, capture and retain (do not overwrite prior
evidence files):

```powershell
git rev-parse HEAD
git status --short
git log -1 --oneline
Get-ScheduledTask -TaskName HealthCheckerConsumerRuntime | Select-Object TaskName, State
Get-Content "C:\ProgramData\HealthChecker\runtime\healthchecker\healthchecker-consumer-api.heartbeat.json"
Get-Content "C:\ProgramData\HealthChecker\runtime\healthchecker\healthchecker-consumer-api.pid"
Invoke-WebRequest http://127.0.0.1:8766/healthz -UseBasicParsing
Invoke-WebRequest https://health.capitalstratasystems.com/healthz -UseBasicParsing
Invoke-WebRequest https://health.capitalstratasystems.com/mobile -UseBasicParsing
```

Save this output to a new `evidence/HC355A_PRE_ROLLBACK_<date>.txt` file.

## 5. Preservation requirements (must hold throughout)

- `C:\ProgramData\HealthChecker\*` (config, runtime state, logs, secrets) is
  **never** deleted or modified by this procedure.
- Only the two processes owned by the current supervisor/child pair are
  stopped — identified positively by PID **and** command line (matching the
  same identity check the supervisor itself uses in
  `Test-HcOrphanedUvicornChild`), never by name-based killing.
- No other scheduled task, Cloudflare tunnel process serving unrelated
  traffic, or unrelated `python.exe`/`powershell.exe` process is touched.
- The Cloudflare tunnel process/config itself is not restarted or modified
  unless evidence proves it is the cause of a rollback-triggering incident.

## 6. Rollback target selection

Choose the most recent commit **known to be good** that predates the
regression. For a runtime-only rollback (this scope), a prior commit on
`main` is sufficient — no branch/tag creation is required, since git history
already retains every candidate.

```powershell
git log --oneline -10   # confirm target commit is present and reachable
```

## 7. Rollback execution (governed, non-destructive to data)

```powershell
# 1. Stop only the governed task (this also lets Windows send the
#    supervisor's cleanup path a chance to run; the supervisor's own
#    orphan-reclaim logic will heal a bypassed cleanup on next start).
Stop-ScheduledTask -TaskName HealthCheckerConsumerRuntime

# 2. Confirm no unintended process remains before touching source.
Get-NetTCPConnection -LocalPort 8766 -State Listen -ErrorAction SilentlyContinue

# 3. Move the working tree to the rollback target. Use a plain checkout;
#    do NOT reset --hard unless the tree is confirmed clean, and do NOT
#    touch anything under C:\ProgramData\HealthChecker (it is outside the
#    repo and unaffected by this).
git -C C:\rasib\source\HealthChecker- checkout <target-commit-sha>

# 4. Restart the governed task. The supervisor's startup port-check will
#    positively identify and reclaim any leftover child from step 1 if
#    Stop-ScheduledTask bypassed cleanup (same mechanism validated in
#    HC-354), then launch a fresh child from the checked-out source.
Start-ScheduledTask -TaskName HealthCheckerConsumerRuntime
```

## 8. Post-rollback verification (all must pass before declaring rollback complete)

```powershell
Get-ScheduledTask -TaskName HealthCheckerConsumerRuntime | Select-Object State   # expect Running
# Supervisor/child verification:
Get-Content "...\healthchecker-consumer-api.pid"
Get-NetTCPConnection -LocalPort 8766 -State Listen   # single owning PID, matches PID file
# Heartbeat verification:
Get-Content "...\healthchecker-consumer-api.heartbeat.json"   # at_utc within a few seconds of now
# Local/public health:
Invoke-WebRequest http://127.0.0.1:8766/healthz -UseBasicParsing            # 200
Invoke-WebRequest https://health.capitalstratasystems.com/healthz -UseBasicParsing  # 200
Invoke-WebRequest https://health.capitalstratasystems.com/mobile -UseBasicParsing   # 200
# Authentication smoke test (no valid credentials required to prove enforcement):
Invoke-WebRequest https://health.capitalstratasystems.com/api/... -Method POST -Body '{}' # expect 401 JSON, not 500/200
```

## 9. Failure / recovery path

If any post-rollback check fails:

1. Re-run `Start-ScheduledTask` once (the supervisor has its own bounded
   restart/backoff logic; a single transient failure is expected to
   self-heal per HC-354).
2. If still failing, check
   `C:\ProgramData\HealthChecker\logs\healthchecker\healthchecker-runtime.log`
   for the `event=` reason (e.g. `port_already_occupied`,
   `managed_runtime_assert_missing`) — every fail-closed path already logs
   an explicit, positively-identified reason.
3. If the rollback target itself is defective, **roll forward** back to the
   previously-running commit (identical procedure, target = the commit that
   was running before this rollback began — captured in step 4's pre-
   rollback evidence).

## 10. Abort criteria

Abort and roll forward immediately if any of the following occur:

- the working tree cannot be cleanly checked out (uncommitted changes would
  be lost) — resolve/stash first, never force-discard unknown changes;
- `Test-HcOrphanedUvicornChild` cannot positively identify a leftover
  listener on port 8766 (i.e., an unrelated process is bound to that port) —
  this is a fail-closed signal and must not be overridden manually;
- health-data files under `C:\ProgramData\HealthChecker\` show any
  unexpected modification time change during the procedure;
- authentication smoke test returns anything other than the expected 401
  for a negative-credential probe.

## 11. Forward-restoration procedure

To return to the current production commit after a rollback:

```powershell
Stop-ScheduledTask -TaskName HealthCheckerConsumerRuntime
git -C C:\rasib\source\HealthChecker- checkout main   # or the specific prior HEAD sha
Start-ScheduledTask -TaskName HealthCheckerConsumerRuntime
```

Then repeat the Section 8 verification checklist.

## 12. Validation performed for this runbook (HC-355A)

The procedure above was engineering-validated **without executing a
destructive rollback against the live production runtime**, because:

- the exact governed stop/start/reclaim mechanics (steps 7.1, 7.3-4, and the
  orphan-reclaim fallback) are the same mechanics already **physically
  proven** during HC-354's controlled UAT: a real `Stop-ScheduledTask` /
  `Start-ScheduledTask` cycle was performed against this exact task,
  confirmed to leave an orphaned child (expected OS behavior), and confirmed
  the next `Start-ScheduledTask` positively reclaimed it
  (`event=runtime_orphan_child_reclaimed`) with no manual intervention and
  no lingering orphan — this is the same recovery path a real rollback
  restart would use;
- `git checkout <sha>` of this working tree is a standard, low-risk git
  operation that does not touch `C:\ProgramData\HealthChecker\` (verified in
  Section 2) and was confirmed via `git log`/`git status` that all listed
  rollback-target commits are present and reachable in this repository;
- performing an actual rollback purely to prove this document would
  introduce real production risk (a brief consumer-facing outage window)
  with no corresponding new information, which HC-355A explicitly
  prohibits ("Do NOT perform a destructive production rollback merely to
  prove the document").

Therefore this runbook is classified:

**SERVER_ROLLBACK_PROCEDURE = DOCUMENTED_AND_ENGINEERING_VALIDATED**
**PHYSICALLY_EXECUTED = NO**

If owner wants a fully physically-executed rollback rehearsal (checkout to
`31c4023`, verify, then roll forward back to `main`), that is a safe,
non-destructive drill that can be scheduled as a short, separate,
explicitly-authorized exercise — it was not performed here because it was
not requested and was not necessary to close this gap.
