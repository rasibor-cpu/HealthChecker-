# HC-309-R4C — Operator Signing and Provenance Decision Record

Status: **APPROVED governance decisions**

Approval authority: Robert

Governing specification:
`docs/governance/HC-309-R4B_TRUSTED_COLLECTOR_AND_RUNTIME_PROVENANCE_SPEC.md`

## 1. Scope and effect

Robert approved the complete recommended R4C signing and provenance decision
package. This record resolves the operator choices required by R4B and is
authoritative for planning and review of later HC-309 phases.

This approval records governance only. It does not authorize R4D or any later
phase and does not authorize collector implementation, certificate or key
creation, certificate provisioning, installer acquisition, CPython reinstall,
ProgramData changes, ACL/task/service changes, executable hashing, evidence
collection, digest adoption, runtime activation, or live certification.

The protected-runtime certification state remains **BLOCKED**. HC-309 PASS
remains unreachable.

## 2. Approved decisions

### A. Pilot trust model — APPROVED

- The pilot will use a private HealthChecker pilot PKI.
- Pilot trust is limited to development and pilot operation.
- Pilot roots, intermediates, certificates, keys, policies, and allowlists must
  never silently become production trust.
- Promotion to production requires a separate commercialization-phase decision,
  explicit trust-policy change, independent review, and approval.

### B. Production model — APPROVED

- Production signing is deferred until commercialization.
- Production collector code signing must use a recognized public code-signing
  certificate authority.
- Production evidence-signing trust requires a separate governed decision.
- No pilot certificate, key, issuer, root, allowlist entry, or signed artifact is
  automatically eligible for production.

### C. Key separation — APPROVED

- Collector code-signing and evidence-signing certificates and private keys must
  be separate.
- Each trust purpose must have a distinct certificate policy, authorization,
  access boundary, rotation record, revocation path, and allowlist entry.
- Compromise, revocation, or expiry of one purpose must not automatically expose
  the other private key or authorize the other trust purpose.

### D. Code-signing key — APPROVED

- Algorithm: RSA 3072.
- Digest: SHA-256.
- Required purpose: Code Signing EKU and approved pilot certificate policy.
- Private key: non-exportable.
- Every collector signature requires a trusted RFC 3161 timestamp.
- Collector acceptance also requires the R4B artifact digest, version, placement,
  chain, revocation, signer, and immutable-installation checks.

### E. Evidence-signing key — APPROVED

- Algorithm: ECDSA P-256.
- Digest: SHA-256.
- Purpose: dedicated evidence-signing policy/EKU supported by the pilot PKI
  design and enforced by offline validation.
- Private key: non-exportable.
- The key must not be reused for code signing, TLS, user authentication,
  encryption, or a general-purpose signing operation.

### F. Key storage — APPROVED

- Certificate and key association: Windows LocalMachine certificate context.
- Private-key provider: TPM/CNG-backed storage where supported.
- Private keys must be non-exportable.
- Access must follow least privilege and be restricted to the separately
  approved signing identity or process for that key's single purpose.
- Private keys must never be placed in Git, repository files, plaintext user
  directories, environment variables, command arguments, reports, logs,
  transcripts, or clipboard instructions.

This decision does not select a store sub-location, provider, identity, or ACL;
those implementation details require R4F design evidence and approval.

### G. Root trust — APPROVED

- The pilot root authority remains offline.
- The pilot root private key must never be installed on the HealthChecker
  runtime host.
- Only the minimum required public root/intermediate trust material and issued
  leaf/intermediate certificates may be deployed under a later R4F approval.
- Trust deployment must be versioned, reviewed, and auditable; presence in a
  Windows store alone does not establish authorization.

### H. Revocation and validity — APPROVED

- Validators must build and validate the complete certificate chain under the
  approved pilot policy.
- Revocation checks must follow the documented pilot policy established before
  provisioning.
- Revoked, invalid, wrong-purpose, wrong-policy, or wrong-signer certificates
  produce **FAIL**.
- Indeterminate revocation or certificate status produces **BLOCKED**.
- An expired collector signature may be accepted only when a valid trusted RFC
  3161 timestamp and the applicable chain policy prove that the signature and
  certificate were valid at signing time.
- Evidence remains subject to its short freshness window; timestamping cannot
  make stale evidence current.
- Rotation and emergency-revocation procedures must be approved before any
  certificate or key is provisioned.

### I. Controlled CPython reinstall — APPROVED IN PRINCIPLE

- A later controlled CPython 3.12.10 Windows AMD64 reinstall is approved in
  principle.
- The reinstall may occur only after collector implementation, synthetic
  testing, independent security review, certificate signing, immutable collector
  packaging, and all R4G pre-install provenance gates pass.
- A separate R4G authorization and runbook are mandatory.
- This decision does not authorize installer acquisition, download, execution,
  installation, dependency changes, or host mutation.

### J. Existing protected runtime — APPROVED

- The current protected runtime cannot be certified from available evidence.
- It must be retained inactive as rollback and evidence material.
- Its observed executable hash must never become authoritative provenance.
- It must not be deleted, replaced, selected, or reactivated without a later
  approved runbook.
- Deletion requires separate approval after a successful certified replacement
  and after rollback/evidence retention requirements are satisfied.

### K. Independent review — APPROVED

- Every security-relevant implementation phase requires review by a person or
  isolated review process that did not implement that phase.
- The reviewer must receive the frozen artifact, evidence, tests, and governing
  policy independently of implementation assertions.
- Robert retains final approval authority.
- A production release requires an appropriately qualified independent human
  security review. Pilot automation or an isolated automated review does not
  replace that production requirement.

### L. Evidence retention — APPROVED

- Pilot authenticated evidence retention: 2 years.
- Production authenticated evidence retention: 7 years, subject to later legal
  and privacy review.
- Retained evidence must be privacy-safe, integrity-protected, access-controlled,
  and governed by append-only and replay-detection requirements.
- Expiry, legal hold, archival, and deletion must follow a separately approved
  retention procedure. This decision does not authorize deletion.

## 3. Trust-purpose matrix

| Purpose | Pilot decision | Production decision |
|---|---|---|
| Collector code signing | Private pilot PKI; RSA 3072/SHA-256; Code Signing EKU; RFC 3161 timestamp | Recognized public code-signing CA; details deferred |
| Evidence signing | Separate pilot PKI key; ECDSA P-256/SHA-256; dedicated policy/EKU | Separate governed decision required |
| Key storage | LocalMachine context; non-exportable TPM/CNG where supported | Deferred, with equivalent or stronger controls |
| Root trust | Offline private pilot root; public material only on runtime host | Public CA chain for code signing; evidence trust deferred |
| Review | Independent phase review; Robert final approval | Qualified independent human security review required |
| Evidence retention | 2 years | 7 years, subject to legal/privacy review |

## 4. Fail-closed policy

The following outcomes are binding:

- Unknown, unapproved, wrong-purpose, malformed, tampered, or revoked
  certificates/signatures: **FAIL**.
- Indeterminate chain, revocation, trusted time, key status, or required evidence:
  **BLOCKED**.
- Missing independent review or phase approval: **BLOCKED**.
- Any attempt to use pilot trust as production trust without explicit approval:
  **FAIL**.
- Any attempt to use the current runtime's observed hash as authoritative:
  **FAIL**.
- Any attempted fallback to mutable Git code, PATH resolution, environment keys,
  or the protected interpreter for trust bootstrap: **FAIL**.

## 5. Remaining design details

The approved policy intentionally leaves these implementation details for their
separately authorized phases:

- pilot root/intermediate hierarchy and certificate profile documents;
- certificate policy identifiers and evidence-signing EKU representation;
- approved CNG/TPM provider and exact signing identities;
- LocalMachine public-certificate placement and key access rules;
- RFC 3161 timestamp authority selection and outage behavior;
- revocation publication, cache, availability, and offline-validation procedure;
- rotation overlap, emergency revocation, and incident-response runbooks;
- reproducible collector build and signing ceremony;
- controlled reinstall and rollback commands;
- privacy-safe evidence storage and approved retention/deletion procedure;
- commercialization-phase production evidence-signing trust.

These are unresolved implementation prerequisites, not unresolved R4C policy
decisions. None may be silently selected by an implementer.

## 6. Future phase gates

No later phase is authorized by this record:

| Phase | Scope | Current authorization |
|---|---|---|
| R4D | Collector implementation using synthetic fixtures only | NOT AUTHORIZED |
| R4E | Independent security review | NOT AUTHORIZED |
| R4F | Pilot certificate provisioning and immutable collector packaging | NOT AUTHORIZED |
| R4G | Verified installer acquisition and controlled reinstall | NOT AUTHORIZED |
| R4H | Authenticated evidence collection | NOT AUTHORIZED |
| R4I | Digest approval and runtime-contract update | NOT AUTHORIZED |
| R4J | Live certification and MVP reassessment | NOT AUTHORIZED |

The next possible action is a separate operator decision authorizing R4D's
synthetic-only implementation scope. Until then, no collector code or host
action is permitted.

## 7. Relationship to existing governance

This record adopts the recommended decision package in
`docs/governance/HC-309-R4B_TRUSTED_COLLECTOR_AND_RUNTIME_PROVENANCE_SPEC.md`.
It does not weaken or supersede the fail-closed controls in:

- `docs/governance/HC-309_PROTECTED_RUNTIME_CERTIFICATION.md`;
- `docs/governance/HC-307_TRUSTED_OPERATOR_RUNBOOK.md`;
- `docs/governance/HC-306B_EXTERNAL_PRIVILEGED_EVIDENCE.md`;
- `config/companion_runtime.json`.

HC-307's existing environment-HMAC tooling remains separate and is not approved
as the certificate-backed HC-309 trust anchor. The protected executable digest
must remain absent, the current runtime must remain inactive, and HC-309 PASS
must remain unreachable until the applicable later phases pass and receive
explicit approval.
