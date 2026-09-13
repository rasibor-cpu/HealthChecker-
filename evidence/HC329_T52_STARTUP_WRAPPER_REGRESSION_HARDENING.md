# HC329-T52 — Startup Wrapper Regression Hardening

## Scope

This increment is repository-only and was created from baseline commit:

`41bfc294368e6eab4b5022576cc4047580cffddf`

It does not modify the production startup wrapper and does not touch the FINANCE host.

## T51 evidence carried forward

HC329-T51 established all of the following after restoring only
`scripts/start_healthchecker_production.ps1` to repository HEAD:

- governed scheduled task entered Running state;
- 127.0.0.1:8766 obtained a python listener;
- /healthz and /mobile returned HTTP 200;
- HC329 JavaScript assets returned HTTP 200;
- recovery catalog returned HTTP 200;
- HC329 application files were unchanged;
- no scheduled-task stop, PID manipulation, ACL modification, configuration
  mutation, production-vault-content change, phone action, pairing action,
  Cloudflare action, or CSS port 8765 action was performed.

The local modified wrapper was therefore isolated as the startup fault domain.

## T52 control

T52 leaves the proven-good wrapper unchanged and adds static regression coverage
for the startup contract. The new tests require:

- managed runtime assertion;
- install-root resolution;
- HealthChecker consumer service identity;
- loopback-only binding;
- CSS port collision protection;
- stale/running-instance protection;
- PID and heartbeat lifecycle controls;
- uvicorn factory launch of the Health Vault API;
- fail-closed runtime, configuration, transport, identity, and public-origin
  validation.

The tests also reject introduction of obvious unsafe startup-side process/network
operations such as Stop-Process, taskkill, explicit listener operations against
CSS port 8765, netsh, ACL mutation, and Cloudflare tunnel lifecycle mutation.

## Deliberate non-goals

This increment does not:

- reconstruct or overwrite uncommitted HC329 application changes on FINANCE;
- read or alter the T51 backup in the FINANCE temp directory;
- restart or stop the governed Windows task;
- touch port 8765;
- perform pairing, Sync Now, phone, Cloudflare, ACL, or production-vault actions;
- claim live-device or Windows-host validation from GitHub alone.

## Closure criterion

Repository-side startup hardening is complete when the new static tests are
committed and CI, if configured for the branch/PR, passes. Live-host closure
remains the T51 runtime evidence already obtained on FINANCE.
