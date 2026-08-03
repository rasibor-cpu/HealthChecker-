# HC-307 — Trusted Operator Evidence Generation

## Purpose

HC-307 provides the trusted Administrator tooling that generates HC-306B-R1
compliant evidence bundles. It is the operator-facing counterpart to the
evidence validation engine.

**HC-307 generates evidence only. It NEVER activates the companion host.**

HC-309 offline policy evaluation is documented in
`docs/governance/HC-309_PROTECTED_RUNTIME_CERTIFICATION.md`; it is not live
provenance certification. Privileged HC-309 collection remains BLOCKED until an
independently trusted immutable collector is installed through the governed
release process and integrated with this authenticated HC-307 architecture.
Mutable repository code must never be elevated. Neither result authorizes
runtime activation.

HC-309 currently has no trusted live collector or trusted-envelope validator.
Its offline evaluator cannot authenticate evidence and cannot produce PASS;
PASS is reserved for a future separately reviewed integration. The privileged
HC-309 wrapper always returns BLOCKED with exit code 20. The protected
executable digest must remain absent until independently established through
that future governed path. Offline evaluation is not live, clinical, or
medical-device certification.

## Depends on

- HC-306B-R1 (external privileged evidence architecture)

## Prerequisites

1. Windows machine with Administrator access.
2. Elevated PowerShell session (`Administrator = True`).
3. Clean Git working tree at the expected HEAD.
4. Tailscale installed and running.
5. Signing key configured via environment:
   - `HC_EVIDENCE_SIGNER_ID` — signer identity
   - `HC_EVIDENCE_SIGNER_KEY` — signing key (`hex:...`, `base64:...`, or raw ≥32 chars)

## Administrator requirement

The generator script **immediately fails** if it is not running from an
elevated Administrator PowerShell. This is enforced at the process level
before any data collection begins.

## Execution steps

1. Open an **elevated** Windows PowerShell (Run as Administrator).
2. Set required environment variables:
   ```powershell
   $env:HC_EVIDENCE_SIGNER_ID = 'ops-elevated-1'
   $env:HC_EVIDENCE_SIGNER_KEY = 'hex:<64-hex-chars-minimum>'
   ```
3. Run the generator:
   ```powershell
   cd C:\rasib\source\healthchecker
   .\scripts\operator\Generate-PrivilegedEvidence.ps1
   ```
4. Observe PASS/FAIL output for each check.
5. If all checks pass, the evidence bundle is stored automatically.
6. **STOP.** Do not proceed to runtime activation without separate approval.

## Generated artifacts

Output directory: `%ProgramData%\HealthChecker\RuntimeEvidence\`

Per evidence generation:

- `<timestamp>_<uuid>.json` — full evidence bundle
- `<timestamp>_<uuid>.sha256` — integrity hash file

Files are created with exclusive atomic writes and are never overwritten.

## Validation workflow

To validate a previously generated evidence bundle:

```powershell
.\scripts\operator\Validate-PrivilegedEvidence.ps1 -EvidencePath <path-to-evidence.json>
```

The validator invokes the HC-306B-R1 validation engine and reports PASS or
FAIL with a specific reason code.

## Failure scenarios

| Scenario | Result |
|----------|--------|
| Not elevated | FAIL — Administrator privileges required |
| Dirty working tree | Evidence generated but validation rejects |
| BitLocker off | Evidence generated but validation rejects |
| Ports occupied | Evidence generated but validation rejects |
| Companion host running | Evidence generated but validation rejects |
| Caddy running | Evidence generated but validation rejects |
| Missing signer config | FAIL — signer environment not set |
| Tailscale offline | Evidence generated with empty node identity |
| Duplicate evidence | FAIL — append-only record exists |

## STOP point

After evidence generation completes:

- Review the evidence bundle.
- Validate using the validation script.
- Do NOT proceed to runtime activation.
- Do NOT configure Caddy or Tailscale Serve.
- Do NOT install services.
- Do NOT create production secrets.

Runtime activation requires separate approval and is governed by HC-304/HC-306.

## Security notes

- Signing keys must never appear in command-line arguments visible to other
  processes. Use environment variables only.
- Evidence bundles contain no secrets (tokens, keys, passwords).
- Each bundle has a unique attestation UUID and monotonically increasing
  sequence number to prevent replay.
- The signature authenticates the complete payload via HMAC-SHA256 with a
  trusted signer key.
