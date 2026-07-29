# HC-306B-R1 — External Privileged Evidence Architecture

## Rationale

HC-306B evolves HC-306A by decoupling privileged verification from automation
process identity while preserving all security guarantees through trusted
operator evidence and independent runtime fail-closed enforcement.

Legacy HC-306A-style same-process elevation checks remain supported for
backward compatibility.

## Trust Model

- A trusted elevated operator context performs privileged host checks.
- The automation path validates an authenticated and hashed evidence bundle (Mode B).
- Runtime activation remains independent and fail-closed.
- HC-306B PASS never auto-authorizes activation.

R1 introduces explicit authenticity verification for evidence signatures using a
trusted signer registry. Windows certificate-backed signing is preferred in
production; development fallback uses a dedicated protected signer key.

## Threat Model

Threats considered:

- forged evidence payloads
- replay of old evidence
- stale host-state attestations
- cross-machine replay
- workspace mismatch drift
- privileged/non-privileged process confusion

Mitigations:

- strict required schema validation
- SHA-256 evidence integrity verification
- machine binding checks
- freshness windows per control domain
- replay detection via append-only evidence history
- independent runtime activation checks

## Evidence Schema

Schema version: `hc306b.external_evidence.v1.r1`

Required fields:

- `schema_version`
- `timestamp_utc`
- `check_timestamps_utc` (required object)
- `hostname`
- `machine_identifier`
- `windows_boot_time`
- `repository_path`
- `branch`
- `head_commit`
- `origin_head`
- `worktree_clean`
- `ahead_behind`
- `elevation_verified`
- `bitlocker_status`
- `filesystem`
- `tailscale_node_id`
- `tailscale_dns_name`
- `tailscale_ipv4`
- `companion_service_present`
- `caddy_running`
- `companion_process_running`
- `required_ports`
- `vault_paths`
- `attestation_uuid`
- `attestation_sequence`
- `signer_id`
- `signature_timestamp_utc`
- `evidence_signature`
- `evidence_sha256`

`check_timestamps_utc` must include:

- `elevation_verified`
- `bitlocker_status`
- `workspace`
- `ports`
- `runtime_inactive`

The `evidence_sha256` is computed over canonical JSON excluding the
`evidence_sha256` and `evidence_signature` keys.

`evidence_signature` authenticates the canonical payload (excluding only
`evidence_signature`) and must verify against a trusted signer.

## Operator Workflow (Mode B)

1. Run privileged checks from trusted elevated operator context.
2. Build evidence bundle with required fields.
3. Assign `attestation_uuid` and increment `attestation_sequence` for signer.
4. Compute SHA-256 digest and set `evidence_sha256`.
5. Sign the evidence payload and set `evidence_signature`.
6. Submit evidence for validation in automation preflight.
7. Persist accepted evidence as append-only audit artifacts.
8. Stop before activation unless separately approved.

## Runtime Workflow

Runtime activation is unchanged and independently fail-closed. Immediately
before activation, runtime must verify:

- activation flag
- required secrets
- loopback-only binding
- monitoring vault boundary
- trusted proxy requirements
- topology constraints
- Serve/Funnel safety constraints

Failure in any control blocks activation.

## Freshness Policy

Default maximum age:

- Elevation: 10 minutes
- BitLocker: 30 minutes
- Workspace: 5 minutes
- Ports: 2 minutes
- Runtime inactive: 2 minutes
- Signature: 10 minutes

Expired or future-dated evidence fails preflight.

Each domain uses explicit per-check timestamps; global timestamp fallback is not
allowed for those controls.

## Policy Validation

HC-306B-R1 validates policy state, not only data types:

- `elevation_verified` must be `true`
- `worktree_clean` must be `true`
- `companion_service_present` must be `false`
- `caddy_running` must be `false`
- `companion_process_running` must be `false`
- all `required_ports` must report `FREE`
- repository path / branch / head / origin head must match expected context
- `ahead_behind` must be zero
- `filesystem` must be `NTFS`
- BitLocker protection status must report `On`

## Compatibility

HC-306B supports both preflight modes:

- Mode A: `legacy_same_process` (existing behavior)
- Mode B: `external_privileged_evidence` (recommended)

Mode A is retained to avoid breaking existing operational paths.

## Migration Strategy

1. Introduce Mode B as additive.
2. Keep Mode A operational during transition.
3. Update runbooks to default to Mode B.
4. Continue requiring independent runtime fail-closed checks.
5. Review operational metrics and then consider deprecation timeline for
   Mode A (if desired).

## Failure Scenarios

- Missing required field -> reject evidence.
- Invalid/mismatched hash -> reject evidence.
- Invalid/mismatched signature or untrusted signer -> reject evidence.
- Host/machine/repo/head mismatch -> reject evidence.
- Stale timestamps -> reject evidence.
- Replay hash, attestation UUID, or sequence -> reject evidence.
- Runtime prerequisites missing -> activation remains blocked.

## Audit Trail

Use append-only records under `runtime_evidence/` with exclusive creation:

- `<timestamp>.json`
- `<timestamp>.sha256`

Existing records are never overwritten.
