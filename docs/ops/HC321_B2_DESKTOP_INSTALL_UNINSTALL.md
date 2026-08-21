# HC321-B2 Desktop Package Install / Uninstall / Rollback

## Package

```powershell
.\scripts\package_healthchecker_desktop.ps1 -OutputDirectory <dir-outside-source-tree>
```

Produces `HealthChecker-0.321.0\` with `release-manifest.json` (SHA-256 for every
file). Excludes `.git`, tests, caches, vault storage, intake records, secrets,
and signing keys.

## Install (atomic)

```powershell
.\scripts\install_healthchecker_desktop.ps1 -PackageDirectory <staged-package>
```

Order of operations:

1. Assert managed Python runtime (fail-closed)
2. Verify every SHA-256 in `release-manifest.json` (tamper → reject, no swap)
3. Stage to `HealthChecker.next`
4. Move active install to `HealthChecker.previous` (if present)
5. Promote `.next` → active; on failure restore `.previous`
6. Create desktop shortcut → `scripts\start_healthchecker.ps1` (production path)

User data stays under `C:\ProgramData\HealthChecker\` (`data`, `config`,
`secrets`, `logs`) and is **outside** app replacement boundaries.

Isolated harness overrides (acceptance only): `-InstallRoot`, `-DataRoot`,
`-ShortcutDirectory`, `-ManagedPythonPath`, `-SkipShortcut`.

## Production launch

Installed consumer start uses install-root resolution (no git checkout) and
binds **127.0.0.1:8766** via `start_healthchecker_production.ps1`. CSS port
**8765** is forbidden. Missing runtime/config fails closed.

## Uninstall

```powershell
.\scripts\uninstall_healthchecker_desktop.ps1
```

Removes:

- Application files under the install root (default `C:\Program Files\HealthChecker`)
- Desktop shortcut `HealthChecker.lnk`
- Scheduled task `HealthCheckerConsumerRuntime` when present

Does **not** delete:

- Production vault / ProgramData `data`
- Governed `config` or `secrets`
- Managed tools under ProgramData

Optional admin data removal is a **separate deliberate recovery-verified
operation** and is never the default uninstall path.

## Rollback

If activation fails after `.previous` exists, the installer restores the prior
application tree. Interrupted promotion must not leave the machine without the
previous install when a previous tree was available.

## Clean-machine note

Full physical VM clean-machine drills may be deferred
(`CLEAN_MACHINE_PHYSICAL_VM=DEFERRED`). Source-independent isolated-install
acceptance (package → verify → install under a temp root → preflight → update/
rollback → uninstall with data preserved) is required on the engineering host.
