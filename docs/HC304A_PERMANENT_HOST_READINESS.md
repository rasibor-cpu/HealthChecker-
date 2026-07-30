# HC-304A — Permanent Host and Live-Readiness Audit

**Status:** Architecture audit complete; foundation work continues in **HC-304B** (`docs/HC304B_PRIVATE_HOST_FOUNDATION.md`).
**HEAD at audit:** `e441ef1` (`HC-303D: secure companion pairing and host validation`)
**Not executed in HC-304A:** Health Connect permissions, Sync Now, WorkManager enablement, public deploy, real-record access, commit/push of follow-on work unless separately approved.

This document captures the HC-304A readiness audit. Permanent-host **implementation** is described in HC-304B and remains undeployed until Robert approves Tailscale enrollment and service install.

---

## Section 1 — Current host audit

### 1.1 How the backend is started

| Mechanism | Detail |
|-----------|--------|
| In-repo entry | `create_health_vault_app(store=None)` in `backend/health_vault/api.py` — FastAPI factory only |
| Default store | `store or VaultStore()` → opens `<repo>/vault_storage` immediately (`api.py` ~108–117) |
| Production runner | **None committed** — no `__main__`, no `uvicorn` script, no README start command |
| HC-303D ephemeral | `%TEMP%\hc303d_session\run_host.py` (not in git): `uvicorn.run(app, host="127.0.0.1", port=…)` |
| Temp vault gate | Temp runner **refuses** if vault root is production `vault_storage` |

### 1.2 Bind address and port

| Mode | Bind | Port |
|------|------|------|
| HC-303D temp | `127.0.0.1` only | `HC303D_PORT` (default 8765; phone session used **8877**) |
| Phone pairing today | `http://127.0.0.1:8877` via USB/ADB | Cleartext; not production |
| Committed LAN/`0.0.0.0` policy | **Absent** | Documented as remaining work in `docs/HC303A_ANDROID_COMPANION.md` |

### 1.3 Windows always-on suitability

**HC-306E-R2 foundation (repo):** inert Task Scheduler templates + release packaging — **not installed** on hosts until a later approved gate.

**Rejected for this pilot:** NSSM and WinSW (third-party service wrappers). Do not treat historical NSSM sketches as an active path.

**Active design:** Microsoft Windows Task Scheduler scheduled startup tasks (`HealthCheckerCompanionHost`, `HealthCheckerCompanionProxy`) running from an immutable ProgramData release copy — accurately described as scheduled tasks, **not** Windows services.

### 1.4 CORS, auth, rate limits, body limits, TLS

| Control | Status | Notes |
|---------|--------|-------|
| CORS | **Missing** | No `CORSMiddleware` |
| Companion admin | Optional | `HC_COMPANION_ADMIN_TOKEN` unset → **allow** (LAN-trust) — `companion/security.py` ~225–236 |
| Device Bearer | Present | Strict Bearer; token **hash** on host; query tokens rejected |
| Pair confirm | Code-gated | No admin header (by design); throttled attempts |
| Rate limiting | Partial | Pair-confirm throttle only; no general API rate limit |
| Body limits | Present | `MAX_PAYLOAD_BYTES` (~512 KB) + max observations/batch; Content-Length → 413 |
| TLS | Soft | `X-Forwarded-Proto` / `X-HC-Local-Dev`; spoofable without trusted reverse proxy |
| Non-companion APIs | **Open** | Import, guardian, monitoring, AI routes lack companion-style auth |

### 1.5 Where data and secrets live

| Asset | Location |
|-------|----------|
| Vault / clinical blobs | `<repo>/vault_storage` (gitignored documents/index patterns) |
| Pairing / devices / batch acks | `vault_storage/index.json` companion_* keys |
| Pepper | `HC_COMPANION_PEPPER` env or `vault_storage/.companion_pepper` (gitignored) |
| Admin token | `HC_COMPANION_ADMIN_TOKEN` env only (not in Git) |
| Device tokens (host) | HMAC hashes only |
| Device tokens (phone) | EncryptedSharedPreferences (`hc_companion_secure`) |

### 1.6 Temporary HC-303D vault vs production

- Temp session under `%TEMP%\hc303d_session\` with isolated `test_vault` remains a **lab** configuration.
- It must **not** be treated as the permanent host.
- Production `vault_storage` is reachable by any default `VaultStore()` / app factory call — **no explicit activation flag**.

### 1.7 Backup, encryption-at-rest, recovery

| Control | Status |
|---------|--------|
| Server encryption-at-rest | **Not implemented** (planned in HC-201/HC-302 docs) |
| Certified backup/restore | **Missing** |
| Index durability | Atomic replace retries in `VaultStore` |
| Phone backup | `allowBackup=false`; Keystore-backed prefs |
| Stuck batch `in_progress` | Durable across restart; **no TTL sweeper** found |

### 1.8 Logging exposure risk

- Redactors exist: companion (`redact_companion_log`), monitoring (`redact_for_log`), Android (`PrivacyRedactor` / `SafeLog`).
- Residual risk if host is network-exposed: unauthenticated clinical APIs; `/api/companion/status` exposes paired-device count; EventBus/access logs need permanent-host audit.

### 1.9 Restart / crash recovery

- Pair sessions, devices, token index, batch acks: durable in `index.json`.
- Android pending batch: encrypted; fail closed if corrupt; Sync uses **active** host only (HC-303D).
- Host crash mid-ack can leave `in_progress` → clients get retry-later until resolved.

---

## Section 2 — Connection options (not installed)

| Criterion | A. Local + private VPN | B. Local + HTTPS tunnel | C. LAN HTTPS + trusted cert | D. Cloud backend |
|-----------|------------------------|-------------------------|-----------------------------|------------------|
| Privacy / residency | **Best** — data on laptop | Strong if outbound-only | Good on home LAN | Weakest |
| Security | Tailscale/WG ACLs + local HTTPS | Strong; URL-leak risk | Private CA; Wi‑Fi surface | Full cloud hardening |
| Reliability | High when laptop+VPN up | Tunnel vendor dependent | High at home | Vendor SLA |
| Phone away from home | **Yes** | **Yes** | **No** | **Yes** |
| Cert management | Tailnet HTTPS or local TLS | Often vendor TLS | mDNS / private CA | Managed |
| Recurring cost | Free–low | Free→paid | Low | Ongoing $ |
| Ops complexity | Medium | Medium–high | Medium | High |
| Laptop off | Offline | Offline | Offline | Survives |

**Recommendation: Option A** (local-first Windows host + private authenticated network, HTTPS on loopback or tailnet-only bind).

- Option C: home-only phase acceptable.
- Option B: interim only with explicit approval.
- Option D: only if cloud residency is explicitly accepted.
- **Hard rule:** no public exposure without authentication, TLS, and Robert’s approval.

### Option A topology correction (HC-304BR1)

Do **not** deploy **Tailscale Serve → Companion Host** directly. Companion Host requires `X-HC-Proxy-Token`, and [Tailscale Serve](https://tailscale.com/kb/1242/tailscale-serve) does **not** currently document custom secret-header injection.

**Required path:**

```
Phone (private tailnet) → Tailscale Serve HTTPS → local trusted reverse proxy (127.0.0.1)
  → Companion Host (different 127.0.0.1 port)
```

Use **Serve only** (never [Funnel](https://tailscale.com/kb/1223/funnel)). Details: `docs/HC304B_PRIVATE_HOST_FOUNDATION.md`.

---

## Section 3 — Production security gates (fail-closed)

| Gate | Today | Required before permanent live |
|------|-------|--------------------------------|
| HTTPS-only Companion traffic | Release APK yes; host soft | TLS at proxy; reject cleartext on production bind |
| `HC_COMPANION_ADMIN_TOKEN` | Optional | **Mandatory** if bind ≠ loopback |
| `HC_COMPANION_PEPPER` | Env or auto file | **Mandatory** env / sealed store; rotation documented |
| Secrets outside Git/APK | Largely yes | Enforce; never BuildConfig |
| Token-hash storage | Yes | Keep; never log plaintext tokens |
| Revocation / re-pair | API exists | Runbook + phone checklist |
| Strict CORS/origin | Missing | Deny-by-default |
| Endpoint authentication | Companion yes; others open | Companion-only bind **or** auth all clinical APIs |
| Replay/idempotency | Batch acks durable | Stuck `in_progress` recovery policy |
| Size / rate limits | Size yes; rate partial | Add observation/pair rate limits |
| Windows FS ACLs | OS-default | Restrict vault to service account |
| Encryption-at-rest | Missing | BitLocker (+ optional vault crypto) before Gate 10 |
| Privacy-safe logs | Helpers exist | Permanent-host log audit |
| Backups / restore test | Missing | Restore drill before Gate 10 |
| Startup health check | Partial status | Refuse start if secrets/TLS/vault activation missing |
| No simulated fallback | Companion rejects | Keep `allow_simulated=False` |
| No default personal vault | Default opens vault | Explicit vault root + activation flag |

---

## Section 4 — Host identity migration

**Current temp origin:** `http://127.0.0.1:8877` (USB/ADB-dependent).

### Permanent URL format (placeholders — no real domain invented)

- VPN / tailnet: `https://<device-name>.<tailnet>.ts.net`
- LAN private CA: `https://healthchecker.home.arpa:8443`

### Safe transition (do not execute)

1. Stand up permanent HTTPS host with **explicit** vault root + activation.
2. Require admin token + pepper; health check green.
3. Phone: enter permanent origin as **draft only**; do not Sync.
4. New pair code on permanent host only; successful confirm → atomic `commitPairedSession`.
5. Verify Paired yes; active host = permanent HTTPS; no integrity warning.
6. **Then** revoke temporary device on old temp host/vault.
7. Rollback: failed pair must leave prior active host/token unchanged (HC-303D).
8. Never reuse old codes; never log raw tokens; draft never drives delivery.

### Phone verification checklist

- [ ] No debug pairing extras
- [ ] Host shows permanent `https://…` after success
- [ ] Paired: yes; no repair required
- [ ] Permissions only as intentionally granted (0/8 until Gate 4)
- [ ] Last attempt/success unchanged until supervised Sync
- [ ] WorkManager not scheduled until Gate 8

---

## Section 5 — Supervised activation plan (do not execute)

| Gate | Action | Stop if |
|------|--------|---------|
| 1 | Persistent HTTPS host (service + TLS + bind) | Health/secrets fail |
| 2 | Production secrets + host auth mandatory | Admin/pepper unset |
| 3 | Permanent phone pairing (new code) | Pair fails — keep old active; no revoke yet |
| 4 | Grant **Steps** permission only | User declines |
| 5 | One supervised manual Steps Sync Now | Wrong host / delivery error |
| 6 | Verify source, timestamps, units, dedupe, vault location | Any mismatch |
| 7 | Add remaining permissions individually | Skip ECG; BP DELAYED; Libre out of scope |
| 8 | Enable WorkManager only after manual OK | Gate 5–6 failure |
| 9 | Alert + recovery validation | Noise / missed path |
| 10 | Production-use certification | Incomplete gates |

**Clinical constraints:** BP = DELAYED / not continuous; ECG unsupported; Glucose/Libre out of scope.

---

## Section 6 — Tests and documentation

| Item | Action this phase |
|------|-------------------|
| New deployment code | **Not implemented** (awaiting architecture approval) |
| Readiness documentation | This file: `docs/HC304A_PERMANENT_HOST_READINESS.md` |
| New readiness unit tests | **Deferred** until permanent runner design is approved (no false green on missing host) |
| Existing HC-303D certification | Remains valid at `e441ef1` (74 Android / 32 HC-303 / 266 full host) |
| `git diff --check` | Clean for this doc |

---

## Prerequisites requiring Robert’s decision

1. Connection option A (recommended) / B / C / D.
2. Real permanent hostname.
3. Live vault: production `vault_storage` vs dedicated monitoring vault.
4. Always-on mechanism (service vs login task).
5. VPN/tunnel account if A/B.
6. Certificate approach.
7. BitLocker + backup destination before Gate 10.
8. Approval to implement permanent host (HC-304B+).
9. Cleanup of `%TEMP%\hc303d_session` when finished.

---

## GO / NO-GO

| Decision | Verdict |
|----------|---------|
| Architecture audit | **GO** |
| Implement permanent host now | **NO-GO** until Tailscale + service install explicitly approved (see HC-304B) |
| Permissions / Sync / WorkManager / live clinical | **NO-GO** |
| Commit/push | **NO-GO** this phase (doc may stay uncommitted) |

---

## References

- `backend/health_vault/companion/security.py`
- `backend/health_vault/api.py`
- `backend/health_vault/vault_store.py`
- `LocalCleartextHostPolicy.kt`, `ProductionConfigGate.kt`, `CompanionHostStore.kt`
- `docs/HC303A_ANDROID_COMPANION.md`
- HC-303D commit `e441ef1`
