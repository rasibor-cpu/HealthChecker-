# HC-309-R4F-PREP — Preparation and Readiness Findings

Status: repository preparation only; current certification remains BLOCKED

## Authority and boundaries

This phase consolidates the R4B architecture, R4C decisions and R4D synthetic
collector into preparation artifacts. It creates no certificate, key, trust
store entry, signed artifact, installer, package, ProgramData content,
executable hash, authenticated evidence, task, service, ACL or activation.

The current runtime remains uncertified and inactive. Its executable digest
remains absent and must not be inferred. Certification PASS remains
unreachable. R4F-EXEC and R4G require separate approval and independent review.
This is software/runtime assurance preparation, not clinical certification,
medical-device approval, deployment approval, or commercialization readiness.

## Redacted host capability audit

Audit date: 2026-08-03. Inspection was non-elevated and non-mutating.

| Capability | Result | Evidence/limitation |
|---|---|---|
| Windows platform | AVAILABLE | Windows Home/Core, display 25H2, build 26200 |
| Windows PowerShell | AVAILABLE | Desktop 5.1; fixed System32 executable present |
| TPM readiness | BLOCKED | Readiness cmdlet requires Administrator; no elevation attempted |
| Microsoft Platform Crypto Provider | AVAILABLE WITH LIMITATIONS | Provider registered, but device reported not ready |
| Software/Smart-card KSPs | AVAILABLE | Provider names registered; no key/container enumeration performed |
| TPM RSA-3072/ECDSA-P256 | BLOCKED | Algorithms are design-compatible, but device readiness and key operations are unproven |
| LocalMachine personal store read-only open | AVAILABLE | Store opened read-only; subjects and keys were not enumerated |
| Authenticode verification | AVAILABLE | Fixed PowerShell cmdlet and WinVerifyTrust present |
| X.509 chain/revocation APIs | AVAILABLE | X509Chain, Crypt32 and WinVerifyTrust present; policy operation still requires tests |
| RFC 3161 verification | AVAILABLE WITH LIMITATIONS | Authenticode APIs can assess timestamps; approved TSA/policy is unresolved |
| Windows SDK SignTool | ABSENT | No registered Windows 10 SDK root or known SignTool installation found |
| Approved signing identity | NOT ASSESSED | Repository defines no exact identity; account enumeration was prohibited |
| Immutable collector root | ABSENT | Fixed collector root does not exist; no ProgramData content was enumerated |

No hardware identifiers, certificate subjects, private-key containers, ACL
identities, account names, or unrelated system contents were collected.

## Preparation tooling

`backend.health_vault.companion_host.r4f_preparation` reads one bounded
synthetic fixture from stdin and validates exact PKI, immutable package and
reinstall-plan schemas. It emits one deterministic privacy-safe JSON record:

- `environment: synthetic`;
- `authorization: preparation_only`;
- `certification_status: BLOCKED`;
- exit 20 for a structurally acceptable preparation plan;
- fixed redacted exit 22 for malformed or forbidden input.

The complete stdin operation has one fixed, non-configurable 10-second total
deadline. One daemon reader performs only the existing 131,073-byte bounded
read. The deadline is not reset by partial reads. On expiry the process flushes
one fixed redacted record and terminates with exit 22 through the narrowly
allowlisted `os._exit`; the daemon cannot keep the process alive. No environment
or fixture field can alter the deadline.

Before policy validation, strict parsing enforces at most 131,072 encoded bytes,
depth 12, 64 total containers, 32 members per object, 32 elements per array,
320 total scalar values including object keys, 512 decoded characters per
string, 19 integer digits and signed 64-bit range. Floats, non-finite values,
ambiguous bool-as-integer fields, duplicate keys and invalid Unicode fail with
the same fixed record.

All command-line arguments are forbidden, including live/apply/install/sign
forms. The module imports no process, certificate-store, filesystem, network,
installer, signing, ProgramData, service, task, ACL or package-manager API. It
cannot generate live manifests, hash an executable, sign evidence, install an
asset, or produce certification PASS.

Synthetic validation covers distinct non-exportable key profiles, algorithms,
EKUs, policy identifiers, TPM status, certificate verdict classification,
exact package assets and hashes, signer/minimum-version rules, immutable target
classes, installer identity and SHA-256 provenance, review ordering, rollback,
current-runtime retention, and forbidden activation/digest adoption.

Both exact synthetic leaf profiles require `is_ca=false`, present and critical
BasicConstraints, and exact Boolean KeyUsage fields. Only
`digital_signature` is enabled; certificate/CRL signing, key/data encipherment,
key agreement, and content commitment are explicitly disabled. No usage is
inferred from an omitted field.

Package and external trust are distinct synthetic objects. The manifest lists
only three payload assets and never hashes itself or its detached signature.
Its exact RFC 8785 bytes are hashed once and are the exact future CMS/PKCS#7
detached-signature input. The separately governed future
`HC_COLLECTOR_TRUST_POLICY.json` exists outside the candidate package and must
be authenticated before package parsing. PREP defines no live policy values.

Root, issuing, code-signing and evidence-key placement are exact topology
constraints. Only the evidence key may reside on the runtime host. Revocation
requires both an offline-root CRL for issuing-CA status and an issuing-CA CRL
for leaf status; missing or stale status is BLOCKED and invalid/revoked status
is FAIL.

## Unresolved prerequisites

P1 records reviewed pending-PEN and toolchain-readiness decisions in
`config/hc309_r4f_exec_pending_pen_readiness.json`. The IANA request is confirmed,
but no PEN or OIDs are assigned and no mutation is authorized.

1. Independently verify the assigned PEN when issued, then establish and review
   the enterprise policy arc in a separate repository change.
2. Prove TPM readiness and approved algorithm/provider behavior in a separately
   authorized non-production ceremony.
3. Complete the protected offline identity and two-custodian ceremony record.
4. Independently verify the approved timestamp chain and revocation operation.
5. Complete the offline CA and CRL ceremony and transfer-readiness review.
6. Independently verify the operator-supplied fixed SignTool evidence before any
   signing operation.
7. Independently review frozen certificate templates, package schema and later
   mutation tooling before R4F-EXEC mutation.

P2 prepares a bounded validator for the future assigned-PEN transition proposal.
It establishes no authoritative identifier, performs no IANA lookup, and leaves
all live and mutation gates BLOCKED. See
`HC-309-R4F-EXEC-P2_ASSIGNED_PEN_TRANSITION_VALIDATOR.md`.

Related designs:

- `HC-309-R4D_SYNTHETIC_COLLECTOR_IMPLEMENTATION.md`
- `HC-309-R4F_PILOT_PKI_PROFILE.md`
- `HC-309-R4F_IMMUTABLE_COLLECTOR_PACKAGE.md`
- `HC-309-R4G_CONTROLLED_CPYTHON_REINSTALL_RUNBOOK.md`
