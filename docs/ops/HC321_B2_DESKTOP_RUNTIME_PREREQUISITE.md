# HC321-B2 Desktop Managed Runtime Prerequisite

## Required runtime

HealthChecker desktop install and production start require a **governed** CPython
runtime at the fixed path:

`C:\ProgramData\HealthChecker\tools\python\3.12.10\python.exe`

- Expected identity: CPython **3.12.x** on Windows amd64
- Override (operator/test harness only): environment variable
  `HEALTHCHECKER_MANAGED_PYTHON` pointing at an equivalent governed executable

## Fail-closed behavior

If the runtime is missing, not executable, or the wrong major/minor line:

- `scripts/Assert-HealthCheckerManagedRuntime.ps1` throws `managed_runtime_missing`
- `scripts/install_healthchecker_desktop.ps1` refuses to activate a package
- `scripts/start_healthchecker.ps1` and `scripts/start_healthchecker_production.ps1`
  refuse to start

There is **no** silent fallback to system Python, Microsoft Store Python, or a
demo/dev server on port 8000.

## Supported bootstrap (governed only)

1. Obtain an organization-approved CPython 3.12.10 win_amd64 layout from the
   internal software depot / golden image (not an ad-hoc internet download).
2. Place it under
   `C:\ProgramData\HealthChecker\tools\python\3.12.10\` so that `python.exe`
   exists at the fixed path above.
3. Install production dependencies into that runtime from the packaged
   `requirements-production.lock` using the governed interpreter only.
4. Re-run install or start; Assert must succeed before activation/startup.

## Explicitly unsupported

- Silent or automatic download of Python from the public internet
- Bundling arbitrary unsigned interpreters inside the desktop package
- Using CSS / Capital Strata Systems tooling paths or port **8765**
- Starting consumer HealthChecker without the managed runtime

## Validation

Package validation and HC321-B2 tests prove a missing runtime cannot partially
or unsafely start the installed consumer application.
