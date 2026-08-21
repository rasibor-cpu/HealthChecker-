# HC321-B1 Certificate Lifecycle (Cloudflare Edge TLS)

Status: production ops (P0-02 / HC321-B1)  
Approved public origin: `https://health.capitalstratasystems.com`

## TLS model (binding)

- Cloudflare Tunnel is the **only** approved production TLS termination.
- Browsers and mobile clients validate the Cloudflare edge certificate for
  `health.capitalstratasystems.com`.
- The local HealthChecker origin listens on `http://127.0.0.1:8766` and holds
  **no local public TLS private key** under this topology.
- Do not deploy Caddy, Tailscale Serve, or host-local public certificates as a
  competing public TLS path.

## Provider-managed edge certificate renewal

- Edge certificates for the approved hostname are managed by Cloudflare for the
  tunnel/DNS hostname binding.
- Operators do not install or rotate a HealthChecker-hosted public TLS private
  key for this topology.
- Product acceptance still requires operator verification that the public origin
  presents a valid, unexpired certificate after renewals or incidents.

## Operator verification of cert health / expiry / public origin

Perform after renewal windows, tunnel identity changes, DNS changes, or HTTPS
incidents:

1. Confirm public origin responds successfully:
   `https://health.capitalstratasystems.com/` (expect success, not 524).
2. Inspect presented certificate for the hostname (browser or `openssl
   s_client` / equivalent): subject/SAN includes
   `health.capitalstratasystems.com`, chain validates, and notExpired.
3. Confirm loopback API remains healthy independently on
   `http://127.0.0.1:8766/` (tunnel/cert issues must not imply vault corruption).
4. Confirm production config still enforces HTTPS-only approved origin and
   Cloudflare tunnel transport (fail-closed).

## Evidence after renewal or incident

Retain non-secret evidence:

- UTC timestamp of check
- Public HTTPS status code (and absence of 524)
- Certificate not-after / validity observation (no private key material)
- Loopback 8766 status
- Note whether tunnel process was restarted (API left running when healthy)

Never store or commit private keys, tunnel credentials, or raw secret files in
the repository or evidence attachments.

## Escalation

If certificate validity, hostname mismatch, or Cloudflare edge renewal failures
cannot be resolved with tunnel re-enable and DNS/tunnel identity checks, escalate
to:

`RELEASE/INFRASTRUCTURE OWNER — ASSIGN BEFORE EXTERNAL PRODUCTION HANDOFF`

Do not work around by exposing local ports publicly or introducing a second TLS
terminator.
