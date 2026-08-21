# HC321-B1 Cloudflare Tunnel Lifecycle Operations Runbook

Status: production ops (P0-02 / HC321-B1)  
Approved public origin: `https://health.capitalstratasystems.com`  
Approved TLS termination: Cloudflare Tunnel only  
Local origin: `http://127.0.0.1:8766` (loopback only; never CSS port 8765)

## Principle

Tunnel failure must not corrupt or unnecessarily stop the local HealthChecker
vault/runtime. Recover tunnel independently when the loopback API is healthy.

Owner for external production handoff:
`RELEASE/INFRASTRUCTURE OWNER — ASSIGN BEFORE EXTERNAL PRODUCTION HANDOFF`

## Credential and config locations

| Item | Expected location |
| --- | --- |
| Production config | `C:\ProgramData\HealthChecker\config\production.json` |
| Tunnel YAML | `C:\ProgramData\HealthChecker\config\cloudflared-healthchecker.yml` |
| Tunnel credentials JSON | `C:\ProgramData\HealthChecker\secrets\cloudflare\*.json` |
| cloudflared binary | `C:\ProgramData\HealthChecker\tools\cloudflared\cloudflared.exe` |
| API PID / heartbeat | `C:\ProgramData\HealthChecker\runtime\healthchecker\` |
| Tunnel PID | `...\runtime\healthchecker\healthchecker-cloudflare-tunnel.pid` |

**Prohibition:** never commit tunnel credentials, tokens, or private keys to git.
Configurator fail-closed requires credentials already under ProgramData.

## Startup

1. Confirm install root resolves without git (Program Files or source-tree):
   `scripts\Resolve-HealthCheckerInstallRoot.ps1`
2. Start consumer API (loopback 8766):
   `scripts\start_healthchecker_production.ps1`
3. Verify loopback before enabling tunnel:
   `Invoke-WebRequest http://127.0.0.1:8766/ -UseBasicParsing`
4. Start tunnel only after loopback is healthy:
   `scripts\start_healthchecker_cloudflare_tunnel.ps1`
5. Verify public HTTPS:
   `Invoke-WebRequest https://health.capitalstratasystems.com/ -UseBasicParsing`
   Expect HTTP 200; a Cloudflare 524 means origin timeout — see recovery below.

## Shutdown

1. Stop tunnel first when removing public reachability (do not stop the vault API
   unless a full host shutdown is intended).
2. Locate tunnel PID from `healthchecker-cloudflare-tunnel.pid` and stop that
   process only.
3. Leave `healthchecker-consumer-api` running unless maintenance requires a full
   stop; vault data under ProgramData must remain untouched.

## Restart

1. Prefer tunnel-only restart when public HTTPS fails but loopback answers 200.
2. Do not restart a healthy `:8766` API solely to prove tunnel recovery.
3. Full API restart only when loopback itself is unhealthy or config changes
   require it; preserve vault/secrets under ProgramData.

## Verify loopback 8766

- Listener must be `127.0.0.1:8766` only.
- Port `8765` is reserved for CSS and must never be used by HealthChecker.
- Healthy response from `http://127.0.0.1:8766/` is required before blaming tunnel.

## Verify public HTTPS

- Origin must be exactly `https://health.capitalstratasystems.com`.
- Success: HTTP 200 (or expected authenticated app response), not 524/5xx from edge.
- Do not destructive-test by tearing down a healthy production tunnel during
  unrelated work.

## Recover from 524 / origin timeout

Sequencing when public HTTPS returns 524 but operators can reach the host:

1. Probe loopback `http://127.0.0.1:8766/` first.
2. **API healthy / tunnel unhealthy:** restart only the Cloudflare tunnel process
   (`start_healthchecker_cloudflare_tunnel.ps1`). Do not stop the vault API.
3. **API unhealthy:** restore loopback API first; only then re-enable tunnel.
4. Re-check public HTTPS after tunnel is up.
5. If 524 persists with healthy loopback: verify tunnel YAML ingress points to
   `http://127.0.0.1:8766`, hostname `health.capitalstratasystems.com`, and
   credentials path under ProgramData secrets.

## Re-enable after maintenance

1. Confirm loopback healthy.
2. Confirm tunnel config present and fail-closed path constraints still hold.
3. Start tunnel launcher.
4. Verify public HTTPS 200 without 524.
5. Record evidence (timestamp, loopback status, public status) for the incident.

## DNS / tunnel identity verification

- Public hostname: `health.capitalstratasystems.com` only.
- Tunnel service id in production config: `healthchecker.cloudflare.tunnel`.
- Consumer API service id: `healthchecker.consumer.api`.
- Tunnel UUID and credentials are external; configurator never invents them.
- DNS for the hostname must remain on the Cloudflare zone that owns the tunnel
  route. Changes require the assigned release/infrastructure owner.

## Rollback / escalation

1. Rollback public exposure by stopping tunnel only; keep local vault running.
2. Do not introduce Caddy, Tailscale, direct public ports, or local public TLS
   private keys as alternate architectures.
3. Escalate unresolved edge/DNS/tunnel identity failures to:
   `RELEASE/INFRASTRUCTURE OWNER — ASSIGN BEFORE EXTERNAL PRODUCTION HANDOFF`
4. Capture non-secret evidence: timestamps, HTTP status codes, PID presence,
   config path existence (not credential contents).
