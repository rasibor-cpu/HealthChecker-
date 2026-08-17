# HC-319A Consumer Runtime Validation

## Implementation summary

HealthChecker now launches as one loopback-only consumer web application. The
existing FastAPI factory serves the dashboard at `/`, the explicitly approved
root assets, and the existing `js/` and `css/` frontend directories. All existing
`/api/*` routes remain on the same origin.

The supported launcher is `scripts/start_healthchecker.ps1`. It uses the managed
Python 3.12.10 runtime at
`C:\ProgramData\HealthChecker\tools\python\3.12.10\python.exe`, runs
`backend.health_vault.api:create_health_vault_app --factory`, binds only to
`127.0.0.1`, defaults to port 8000, and reports a clear error when the configured
port is already listening or the managed runtime is unavailable.

The PWA manifest was updated from the legacy `/HealthChecker-/` hosted subpath to
the single local application's `/` start URL and scope.

## Public-content boundary

The repository root is not mounted. Public content is restricted to:

- `/`, `/index.html`, `/style.css`, `/app.js`
- `/manifest.webmanifest`, `/service-worker.js`
- the four PWA icons and Apple touch icon
- files beneath the explicit `/js` and `/css` static mounts

There are no routes exposing `vault_storage`, `evidence`, `scratch`,
`hc313a_state`, `hc_intake`, credentials, configuration, backend source, Git
metadata, or arbitrary repository files. Static traversal attempts are rejected.

## API and authentication validation

- Existing API routes remain registered and reachable under `/api/*`.
- An unauthenticated `/api/auth/session` request is rejected with HTTP 401.
- Same-origin `POST /api/auth/login` authenticates the bootstrap account.
- The returned bearer token is accepted by same-origin `/api/auth/session`.
- First-login password-change enforcement remains intact.

## Tests executed

### HC-319A focused tests

Command: `python -m pytest tests/test_hc319a_consumer_runtime.py -q`

Result: **5 passed**.

Coverage includes index delivery, CSS and JavaScript delivery, existing API
availability, same-origin login/session handling, sensitive-path denial, and
static-directory traversal denial.

### HC-316 through HC-319 compatibility tests

Command: `python -m pytest tests -q -k "hc316 or hc317 or hc318 or hc319"`

Result: **33 passed, 1146 deselected**.

### Full regression suite

Command: `python -m pytest -q`

Result: **1176 passed, 3 skipped, 5 subtests passed** in 419.48 seconds.
Two dependency deprecation warnings were reported; no test failed.

## Quality checks

- The PowerShell launcher parses without syntax errors.
- `git diff --check` passed before staging.
- Existing HC-313/HC-314 runtime artifacts and unrelated generated/scratch files
  are excluded from the HC-319A commit scope.

## Result

HC-319A meets the single-app runtime, loopback binding, same-origin API,
allowlisted-static-content, and regression requirements.
