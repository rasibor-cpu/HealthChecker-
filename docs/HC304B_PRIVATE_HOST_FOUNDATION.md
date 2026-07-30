# HC-304B — Private Permanent Host Foundation

**Status:** Foundation implemented — **not deployed**. HC-304BR1 corrects proxy topology.
**Architecture:** Option A — local-first Windows host on loopback; Tailscale Serve for private HTTPS via a **trusted local reverse proxy**.
**HEAD baseline:** `e441ef1` (HC-303D) + uncommitted HC-304B / HC-304BR1 foundation.

This phase builds and certifies a **fail-closed companion-only host**. It does **not**:

- install Tailscale, Caddy, or Windows scheduled tasks;
- create a real monitoring vault on disk outside tests;
- grant phone permissions, Sync, or WorkManager;
- connect to production `vault_storage`;
- commit/push (until separately approved).

See also: `docs/HC304A_PERMANENT_HOST_READINESS.md`.

### Always-on mechanism (HC-306E-R2)

**Active:** Microsoft Windows **Task Scheduler** scheduled startup tasks (not Windows services):

1. `HealthCheckerCompanionHost` — AtStartup; IgnoreNew; bounded restart
2. `HealthCheckerCompanionProxy` — AtStartup + bounded delay; waits for Companion `/healthz`; IgnoreNew; bounded restart

**Rejected:** NSSM, WinSW, and any third-party service wrapper. Historical NSSM sketches in `install_service.ps1.template` are marked rejected only.

Privileged tasks must run from an immutable release copy under `C:\ProgramData\HealthChecker\releases\<commit>\` with SHA-256 manifest verification. Python and Caddy for SYSTEM tasks must be staged under versioned Admin/SYSTEM-owned paths from `config/companion_runtime.json`:

- `C:\ProgramData\HealthChecker\tools\python\3.12.10\python.exe`
- `C:\ProgramData\HealthChecker\tools\caddy\2.11.4\caddy.exe`

Production dependencies are pinned with hashes in `requirements/production.txt` (`pip install --require-hashes --only-binary=:all:`). User-profile interpreters are forbidden for SYSTEM tasks.

---

## Corrected topology (HC-304BR1)

```
Phone over private tailnet
  → Tailscale Serve HTTPS (private; never Funnel)
    → local trusted reverse proxy on 127.0.0.1 (Caddy)
      → Companion Host on a different 127.0.0.1 port
```

**Proven gap:** Companion Host requires `X-HC-Proxy-Token`, but [Tailscale Serve](https://tailscale.com/kb/1242/tailscale-serve) does **not** currently document custom secret-header injection. The path **Tailscale Serve → Companion Host (direct)** is therefore **not deployable** as designed.

The local proxy must **overwrite** (not preserve) trusted forwarding headers and inject `X-HC-Proxy-Token` from protected process environment configuration. Do **not** use [Funnel](https://tailscale.com/kb/1223/funnel).

### Caddy directive ordering (HC-305F-R1)

Certified **Caddy v2.11.4** proved that `reverse_proxy` `header_up -Name` followed by `header_up Name …` for the **same** trusted header can drop the inject (`proxy_token_invalid` at Companion Host). Correct ordering:

1. **Edge** `request_header` strip of `Forwarded`, `X-Forwarded-*`, `X-HC-Proxy-Token` (and wildcards)
2. **`reverse_proxy`** canonical `header_up` **set only** for `X-Forwarded-Proto`, `X-Forwarded-Host`, `X-HC-Proxy-Token` (no matching deletes)
3. Companion Host evaluates proxy token + HTTPS origin

Direct **Serve → Companion Host** remains **invalid**. Gate F stays blocked pending independent review/commit of this remediation. This document does **not** claim live clinical activation.

### Caddy site label vs bind (HC-306D-R1)

Certified **Caddy v2.11.4** with Tailscale Serve proved that a site label of `http://127.0.0.1:<proxy_port>` matches only a loopback `Host` header. Serve preserves the private MagicDNS `Host`, so requests reach Caddy but miss the site block (empty HTTP 200). Required contract:

1. Site label: **`http://:<proxy_port>`** (host-agnostic)
2. Explicit **`bind 127.0.0.1`** (loopback-only listen)
3. Canonical **`X-Forwarded-Host`** from `{env.HC_EXTERNAL_HTTPS_HOST}` — never client `Host`
4. No Caddy TLS; no Funnel; Authorization preserved

### Port placeholders (operator may change; code rejects reserved / colliding)

| Role | Env | Default placeholder |
|------|-----|---------------------|
| Companion Host loopback | `HC_BIND_HOST` / `HC_BIND_PORT` | `127.0.0.1:8743` |
| Local proxy listener | `HC_PROXY_LISTEN_HOST` / `HC_PROXY_LISTEN_PORT` | `127.0.0.1:8744` |
| Tailscale Serve target | `HC_TAILSCALE_SERVE_TARGET_PORT` | `8744` (must equal proxy) |

**Rejected:** equal proxy/backend ports; CSS `8765`; HC-303D `8877`; privileged (&lt;1024) or malformed ports; non-loopback listeners. Hostname is **not** invented here.

### Startup / shutdown

1. Companion Host
2. Local trusted proxy
3. Tailscale Serve

Shutdown in reverse. Health checks: `/healthz` on companion and proxy loopback URLs; `tailscale serve status --json` (no Funnel; no secrets in bodies).

---

## What was built

| Component | Path |
|-----------|------|
| Activation / fail-closed config | `backend/health_vault/companion_host/activation.py` |
| Topology / port separation | `backend/health_vault/companion_host/topology.py` |
| Caddy render + structural validate | `backend/health_vault/companion_host/caddy_config.py` |
| Monitoring vault boundary | `backend/health_vault/companion_host/vault_boundary.py` |
| Trusted proxy / origin / CORS deny | `backend/health_vault/companion_host/proxy_trust.py` |
| Shared proxy token (`X-HC-Proxy-Token`) | Required in `tailscale_https` mode; injected by **local proxy**, not Serve |
| Rate limits | `backend/health_vault/companion_host/rate_limit.py` |
| Abandoned ack recovery | `backend/health_vault/companion_host/ack_recovery.py` |
| Companion-only FastAPI app | `backend/health_vault/companion_host/app.py` |
| Process entry (`python -m …`) | `backend/health_vault/companion_host/__main__.py` |
| Env example (no secrets) | `scripts/companion_host/env.example` |
| Caddyfile template (inert) | `scripts/companion_host/Caddyfile.template` |
| Local proxy start gate (inert) | `scripts/companion_host/start_local_proxy.ps1.template` |
| Tailscale Serve template (inert) | `scripts/companion_host/configure_tailscale_serve.ps1.template` |
| Serve status Funnel parse | `backend/health_vault/companion_host/serve_status.py` |
| Topology control template | `scripts/companion_host/topology_control.ps1.template` |
| Scheduled-host policy + packaging | `backend/health_vault/companion_host/scheduled_host.py` |
| Production runtime contract | `config/companion_runtime.json`, `requirements/production.in`, `requirements/production.txt`, `runtime_contract.py` |
| Task Scheduler templates (inert) | `scripts/companion_host/install_scheduled_tasks.ps1.template`, `control_scheduled_tasks.ps1.template`, `package_verified_release.ps1.template`, bootstraps |
| NSSM install template (REJECTED) | `scripts/companion_host/install_service.ps1.template` (historical; exits rejected) |
| Adversarial tests | `tests/test_hc304b_private_host_foundation.py`, `tests/test_hc304br1_proxy_topology.py`, `tests/test_hc306e_scheduled_host_foundation.py` |

---

### Deployment assumption (pilot)

**Single-user / single-session Windows host.** Loopback Caddy injects the proxy token for any local process that can reach `127.0.0.1:HC_PROXY_LISTEN_PORT`. Tailscale ACL does not apply on that path. Shared multi-user machines need extra local hardening before production use.

---

## Route inventory (companion-only)

| Method | Path | Auth |
|--------|------|------|
| GET | `/healthz` | Loopback / trusted peer; no secrets |
| GET | `/readyz` | Loopback / trusted peer; config public summary only |
| POST | `/api/companion/pair/start` | **Admin token required** |
| POST | `/api/companion/pair/confirm` | Pair code (+ rate limit) |
| GET | `/api/companion/devices` | **Admin token required** |
| DELETE | `/api/companion/devices/{device_id}` | **Admin token required** |
| POST | `/api/companion/observations` | Device Bearer |
| GET | `/api/companion/status` | Optional Bearer (paired metadata) |

**Not exposed:** Guardian, import, clinical browse, AI, monitoring administration, OpenAPI/docs.

---

## Plain-language setup (later — do not run now)

1. Create a dedicated folder **outside the Git repo** for the monitoring vault (not `vault_storage`, not `private_imports`).
2. Create `%ProgramData%\HealthChecker\companion_host\host.env` from `scripts/companion_host/env.example` with real secrets (24+ char admin token + pepper + proxy token).
3. Set `HC_EXTERNAL_HTTPS_ORIGIN` (and `HC_EXTERNAL_HTTPS_HOST`) to your future Tailscale HTTPS origin (exact; do not invent here).
4. Set `HC_PROXY_SHARED_TOKEN` (24+ chars). Configure the **local Caddy proxy** to inject matching `X-HC-Proxy-Token` from env (see `Caddyfile.template`).
5. Set `HC_TRUSTED_PROXY_MODE=tailscale_https`, loopback binds, distinct `HC_BIND_PORT` / `HC_PROXY_LISTEN_PORT`.
6. Set `HC_HOST_ACTIVATION=enabled` only when ready.
7. Install Tailscale (manual), enroll laptop + phone, restrict ACL to those nodes; **no Funnel/public exposure**.
8. Install Caddy (manual — **not** by this repo). Stage Admin-owned copies under `%ProgramData%\HealthChecker\tools\` for SYSTEM tasks. Render/review Caddyfile; package verified release; install scheduled tasks only after privilege audit.
9. Configure Tailscale **Serve** only to `http://127.0.0.1:<HC_PROXY_LISTEN_PORT>` (template: `configure_tailscale_serve.ps1.template`). Tasks never auto-configure Serve/Funnel.
10. Pair the phone to the **HTTPS** origin with a **new** pair code.

### Windows ACL guidance

- Scheduled-task identity (SYSTEM pilot): read/execute on verified release + tools; Modify on monitoring vault + log directory only.
- Env/secret file: Readable only by that account / Administrators.
- Release directory + manifest: writable only by SYSTEM and Administrators.
- Logs: Outside the vault (e.g. `%ProgramData%\HealthChecker\logs\`).
- Never put secrets in process command-line arguments or task XML.
- Do not execute privileged tasks from the mutable Git working tree.

---

## Rate limiting (honest limitation)

Process-local sliding windows with capped key sets are **acceptable for a single-worker pilot**.
Multi-worker / multi-process deployment can bypass intended limits unless a shared limiter is added — keep one uvicorn worker for HC-304B pilot.

Rules (tested as documentation contract + HC-303D phone behavior):

1. The **temporary pair remains active** until permanent pairing **succeeds**.
2. Permanent pair uses a **new pair code** and the permanent **HTTPS** origin (draft field only until success).
3. Verify the permanent host (healthz/readyz + successful pair) **before** you **revoke temporary** pairing.
4. No token or pairing-code reuse.
5. On failure, **rollback** retains the temporary pair (active host/token unchanged).
6. No automatic host rewrite — only atomic successful `commitPairedSession` promotes active host.

---

## Rollback

1. `tailscale serve reset` (confirm no Funnel / no public listener).
2. Stop local trusted proxy.
3. Stop companion-host / proxy scheduled tasks (when installed).
4. Leave phone on prior active host if permanent pair never succeeded.
5. If permanent pair succeeded but must roll back: re-pair to a known-good host with a new code; revoke the unwanted device on the abandoned host.
6. Do not delete monitoring vault until backup confirmed.

---

## Backup

- Back up the **monitoring vault directory** and the **env/secret file** separately.
- Prefer BitLocker on the system volume.
- Restore drill: stop host → restore vault copy → start → `/readyz` → list devices with admin token.

---

## Incident revocation

1. `DELETE /api/companion/devices/{device_id}` with admin token on the permanent host.
2. Rotate `HC_COMPANION_ADMIN_TOKEN` and `HC_COMPANION_PEPPER` (re-pair devices after pepper rotation).
3. Rotate `HC_PROXY_SHARED_TOKEN` and restart the local proxy (Serve unchanged).
4. Revoke Tailscale node access for a lost phone.
5. Review privacy-safe host logs (no tokens/codes/bodies).

---

## Tailscale prerequisites (Robert — manual later)

- Tailscale account approval / sign-in
- Laptop node enrollment
- S24 Ultra enrollment
- MagicDNS / HTTPS hostname (do not invent here)
- Tailnet ACL: Robert’s phone + host only
- **Serve** → local proxy loopback port (not Companion Host direct)
- Connectivity + certificate verification
- Device removal / revocation procedure
- Serve rollback: `tailscale serve reset`

**Do not invent a hostname or create an account in this phase.**

---

## Installation-time checks (remaining — not automated here)

- Caddy binary present and started with env containing `HC_PROXY_SHARED_TOKEN` / `HC_EXTERNAL_HTTPS_HOST` (no secrets on CLI).
- Live prove: client-supplied `X-HC-Proxy-Token` is stripped and overwritten by proxy.
- Live prove: Serve status JSON has no Funnel; HTTPS origin matches phone draft host.
- Boundary healthz on companion + proxy without secret leakage.

---

## Key rotation procedure

1. Generate new admin token + pepper offline.
2. Update env file; restart host.
3. Pepper change invalidates existing device token hashes → **re-pair** all devices with new codes.
4. Revoke old device entries after successful re-pair.

---

## GO / NO-GO

| Item | Verdict |
|------|---------|
| Foundation code + tests | Ready for independent review |
| HC-305F-R1 Caddy header remediation | Ready for independent review / commit (Gate F still blocked until then) |
| Install scheduled tasks / Tailscale Serve / create real vault | **NO-GO** until approved + privilege audit |
| Phone permissions / Sync / WorkManager / live clinical | **NO-GO** |
