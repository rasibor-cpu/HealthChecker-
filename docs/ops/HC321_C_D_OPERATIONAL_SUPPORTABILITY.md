# HC321-C-D Operational Monitoring & Supportability

## Readiness

`GET /api/ops/readiness` (authenticated) returns a privacy-safe status:

- loopback API assumption for installed consumer
- vault encryption + schema gate
- companion pairing presence
- Health Connect last-sync timestamp when known
- public origin: `unknown|up|down` (operator/probe supplied when available)
- actionable `recovery_guidance` for common failure states
- **no secrets / no PHI**

## Support bundle

`POST /api/ops/support-bundle` with `{ "confirm_export": true }` (owner/admin):

- Returns a redacted ZIP download
- Explicit user/operator action required
- Header `X-HC-Auto-Transmit: never` — HealthChecker never auto-sends externally
- Bundle includes platform/release/readiness metadata only

## Onboarding / recovery (existing Settings surface)

Settings shows readiness + first-run / pairing / offline hints without redesigning
the dashboard. Common guidance covers:

- runtime unavailable (`127.0.0.1:8766`, not CSS `:8765`)
- pairing unavailable
- sync stale
- public origin unavailable
- configuration missing
- offline/degraded mode
