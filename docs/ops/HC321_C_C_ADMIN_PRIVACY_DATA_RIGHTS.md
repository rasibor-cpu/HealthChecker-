# HC321-C-C Admin / Privacy / Consent / Data Rights

## Scope

Product controls for account governance and patient data rights. **Not** a
HIPAA, SOC2, ISO, or PIPEDA certification. Legal/business policy owners fill
notice text, retention, and lawful-basis decisions (`LEGAL_POLICY_OWNER_PLACEHOLDER`).

## Admin lifecycle

Privileged roles: `owner`, `admin`. Least-privilege defaults: new users are `user`.

| Operation | Who | Notes |
|---|---|---|
| `GET /api/admin/users` | owner/admin | Safe account rows (no password hashes) |
| `POST /api/admin/users` | owner/admin | Admin cannot create admin; nobody assigns `owner` via API |
| `POST /api/admin/users/{id}/status` | owner/admin | `active`/`disabled`; disable revokes sessions |
| `POST /api/admin/users/{id}/role` | owner only | No self-role change; owner role immutable |
| `POST /api/admin/users/{id}/sessions/revoke` | self or privileged | Audited |

Password recovery remains the existing local privileged offline path
(`auth_recovery.py`) — consistent with HC-321A; not a silent remote reset.

Privilege changes append `privilege_change` audit events.

## Consent / privacy notice

- Notice version: `hc.privacy_notice.v1.PLACEHOLDER`
- Purposes: `product_use`, `health_connect_sync`, `local_analytics_quality`
- Grant / withdraw with timestamp + provenance
- No fabricated legal claims (`certification_claims: []`)

## Data rights

| Endpoint | Behavior |
|---|---|
| `GET /api/privacy/export` | Patient-scoped package only |
| `POST /api/privacy/amend` | Allowlisted profile keys |
| `POST /api/privacy/deletion/request` | Requires `confirmation=DELETE`; returns token |
| `POST /api/privacy/deletion/confirm` | Requires token + `DELETE`; removes patient docs/measurements |

Deletion is fail-closed without deliberate confirmation. Audit records counts /
actions, not recreated clinical payloads.
