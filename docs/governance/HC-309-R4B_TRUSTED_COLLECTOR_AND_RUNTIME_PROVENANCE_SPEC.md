# HC-309-R4B — Trusted Collector and Runtime Provenance Specification

Status: prerequisite specification only

Decision authority: operator approval is required at every later phase

Current certification state: **BLOCKED**

## 1. Scope and non-authorization

This document specifies prerequisites for a future Authenticode-signed,
certificate-backed protected-runtime collector, a controlled CPython 3.12.10
reinstall, authenticated evidence, and offline HC-309 validation. It does not
authorize or implement a collector, certificate, key, installer acquisition,
reinstall, ProgramData change, task, ACL, executable hash, digest adoption, or
live certification. Approval of R4B authorizes no later phase.

The existing controls in `config/companion_runtime.json`,
`requirements/production.txt`, HC-306, HC-307, and HC-309 remain fail-closed.
`python.executable_sha256` must remain absent until R4I is explicitly approved.

## 2. Trust model

### 2.1 Trust anchors

The future chain requires all of the following independent anchors:

1. The official CPython release identity and authenticated installer provenance.
2. Windows Authenticode chain validation under an approved certificate policy.
3. A pinned collector artifact SHA-256, minimum version, and allowed signer.
4. An immutable, versioned collector installation under ProgramData.
5. A separate evidence-signing certificate and non-exportable private key.
6. A committed offline trust policy containing public trust material only.
7. Git commit review for the runtime contract and immutable release manifest.
8. Independent human approval before an installed executable digest is adopted.

PowerShell execution policy is not a trust anchor. The Windows root store alone
is insufficient without the approved EKU, issuer/policy, signer allowlist,
revocation policy, artifact hash, and minimum collector version.

### 2.2 Boundaries

Trusted only after verification:

- fixed System32 Windows and .NET APIs;
- an approved code-signing chain and timestamp;
- a collector matching its pinned digest and immutable installation contract;
- an evidence certificate matching the evidence-signing policy;
- a signed envelope that passes schema, replay, freshness, host, release, and
  collector-allowlist validation;
- committed configuration and release manifests reviewed at the bound commit.

Always untrusted input:

- mutable Git-tree files, public JSON, CLI arguments, environment values, PATH,
  current-directory command resolution, HTTP bodies, runtime self-report, and
  any hash reported by the protected interpreter;
- a live executable hash without installer and installation provenance;
- certificate labels or thumbprints supplied inside an unverified envelope;
- user-profile interpreters, installers, keys, modules, and trust stores.

Mandatory rules:

- Protected Python cannot certify, launch, package, or hash itself.
- Mutable Git-tree code cannot run elevated.
- PATH resolution and command aliases/functions are forbidden.
- Public or offline evidence cannot assert its own authentication.
- An observed live hash is evidence, not authority, until the complete governed
  provenance chain and independent review pass.

### 2.3 Roles and separation of duties

| Role | Authorized responsibility | Must not alone authorize |
|---|---|---|
| Operator decision owner | Approves phase gates and policy choices | Technical evidence validity |
| Installer acquisition | Obtains the exact pinned artifact | Installer authenticity or installation |
| Installer verification | Verifies source, SHA-256, Authenticode, chain, and revocation | Acquisition or digest adoption |
| Controlled installation | Installs only the approved artifact with recorded parameters | Provenance approval |
| Collector signing | Signs an approved collector build | Collector source approval or evidence signing |
| Evidence collection | Runs the installed collector under the approved identity | Trust-policy changes |
| Independent reviewer | Compares acquisition, install, collector, and executable evidence | Evidence generation |
| Repository approver | Reviews schema, allowlist, and digest change | Host collection or signing |

Production must separate collector code-signing and evidence-signing keys and
should separate the roles above. During development, one person may perform
multiple roles only with separately timestamped checkpoints. A different
reviewer must approve the evidence before certification or digest adoption.

## 3. Minimal trusted collector

### 3.1 Artifact and placement

The collector must be a minimal, versioned PowerShell artifact using direct
.NET and Windows APIs only. It must:

- be Authenticode-signed with an approved code-signing certificate;
- have a committed allowed SHA-256 and minimum version before installation;
- be installed at a fixed versioned location such as
  `C:\ProgramData\HealthChecker\collectors\protected-runtime\<version>\`;
- be writable only by the governed installation identities and unreadable by
  identities not required by policy;
- refuse execution from Git, a user profile, TEMP, downloads, network paths,
  unversioned directories, or a reparse-resolved location;
- verify its own literal path, file identity, signature verdict, signer policy,
  pinned digest, and minimum version before collecting host evidence;
- produce exactly one bounded envelope and no other stdout record.

Self-checks do not establish bootstrap trust by themselves. A fixed trusted
launcher or operator preflight must verify the signature and pinned artifact
before execution. Execution policy may provide defense in depth only.

### 3.2 Forbidden behavior

The collector must not:

- invoke Python, pip, package managers, Git, PowerShell child processes, shells,
  WMI command-line utilities, PATH-resolved executables, repository modules, or
  inline/downloaded code;
- import mutable modules, evaluate expressions, dot-source external scripts, or
  accept a repository/evidence/executable path override;
- install, repair, start, stop, register, activate, or modify any runtime, task,
  service, ACL, release, package, key, certificate, configuration, or endpoint;
- write logs containing raw paths, arguments, identities, ACLs, HTTP bodies,
  environment values, certificate private data, or secrets;
- execute the protected interpreter or trust its self-reported identity.

### 3.3 Exact allowed reads

Only these fixed classes of reads are permitted:

1. Collector file identity, Authenticode signature, version, and reparse state.
2. The fixed CPython installer provenance record installed by the controlled
   reinstall phase; never an arbitrary installer path.
3. `C:\ProgramData\HealthChecker\tools\python\3.12.10\python.exe` metadata,
   stable file identity, reparse state, and bytes for SHA-256 hashing.
4. The fixed `releases\CURRENT` pointer and the exact selected release directory.
5. `SOURCE_COMMIT.txt`, `RELEASE_MANIFEST.json`, and only files enumerated by
   that manifest for read-only integrity verification.
6. `config\companion_runtime.json` and `requirements\production.txt` from the
   selected immutable release.
7. The two exact scheduled tasks `HealthCheckerCompanionHost` and
   `HealthCheckerCompanionProxy`, including bounded contract properties.
8. The four fixed loopback endpoints on ports 8743 and 8744, with bounded
   timeouts, exact response schemas, and strict body-size limits.
9. The evidence-signing certificate public metadata and private-key operation;
   private key bytes must never be readable or exportable.
10. The append-only evidence sequence store and prior-envelope digest.

Access denied, missing data, multiple matches, ambiguous paths, unexpected
fields, stale state, reparse points, or contradictions produce BLOCKED or FAIL;
the collector must never infer PASS from partial evidence.

### 3.4 TOCTOU and path controls

For every protected file, the collector must open by literal path without
following an unsafe reparse chain, obtain a stable volume/file identifier,
record size and last-write metadata, hash through the open handle, then recheck
identity, metadata, path ancestry, and reparse state. Any difference is FAIL.
The release pointer must be read once, validated as one 40-lowercase-hex commit,
and bound to the exact opened release directory. No fallback or search is
permitted.

## 4. Certificate and signing policy

### 4.1 Pilot and production models

Pilot/development may use a governed private/internal PKI if its root,
intermediate, certificate policies, issuance records, and revocation process are
documented and independently controlled. A self-signed leaf certificate is not
acceptable for certification.

Production may use either a public CA code-signing certificate or a governed
enterprise/private PKI. The operator must choose before R4C. Public CA issuance
improves external chain recognition; private PKI can provide tighter internal
issuance control but requires managed trust-anchor distribution and revocation.

### 4.2 Key separation and EKUs

- Collector code signing requires the Code Signing EKU and Authenticode use.
- Evidence signing requires a distinct certificate policy/EKU appropriate for
  document or application evidence signing.
- Production code-signing and evidence-signing keys must be separate.
- Separate keys are also the recommended pilot default.
- Neither certificate may be used for TLS, user authentication, or
  general-purpose signing unless the approved certificate policy explicitly
  requires it.

### 4.3 Storage and identities

Private keys must be non-exportable and held in a TPM, HSM, managed signing
service, or equivalently governed provider. Host evidence keys, if locally
present, belong in the LocalMachine certificate context with access limited to
the exact approved collector identity. Code-signing private keys should not be
installed on the runtime host.

Private key material must never enter Git, reports, environment variables,
command arguments, clipboard instructions, transcripts, or plaintext files in
a user profile. Public certificates, issuer constraints, policy identifiers,
key identifiers, and revocation endpoints may be committed after review.

### 4.4 Validation and lifecycle

Before collector execution or envelope acceptance, validation must check:

- full chain construction under the chosen policy and validation time;
- required EKU and key usage;
- allowed issuer, subject policy, and signer allowlist;
- revocation using the R4C-approved online/offline policy;
- certificate validity and an approved cryptographic algorithm/key size;
- trusted timestamp for collector code signatures;
- artifact digest and minimum permitted collector version.

Collector signatures require an approved timestamping authority so an artifact
signed while the certificate was valid can be assessed under the approved
lifetime-signing policy. Evidence signatures are short-lived attestations and
must satisfy collection-time validity and freshness; timestamping does not make
stale evidence current.

Rotation must overlap old and new public trust entries for a bounded window.
Compromise response must revoke the certificate, remove affected collector and
key identifiers from allowlists, block all dependent envelopes, investigate
previous evidence, rotate keys, and require re-collection. No silent fallback to
an expired, revoked, unknown, or older signer is allowed.

## 5. Controlled CPython 3.12.10 reinstall

The current protected installation is uncertifiable under available evidence.
It must be retained untouched until an explicitly approved R4G disposition.
R4B does not authorize deletion, quarantine, rename, or reinstall.

A future R4G procedure must perform these gates in order:

1. Acquisition records the exact CPython 3.12.10 Windows AMD64 filename,
   official version-specific source, acquisition time, and custody record.
2. A separate verifier confirms the official source identity and validates the
   installer Authenticode status, expected publisher, full chain, EKU, validity,
   timestamp, and R4C revocation policy.
3. The verifier compares installer bytes with an authenticated official SHA-256
   or equivalent authenticated official provenance. MD5 is never sufficient.
4. An append-only installer provenance record is signed and independently
   reviewed before installation.
5. Installation uses the exact verified artifact, fixed explicit installer path,
   fixed noninteractive arguments, and fixed target
   `C:\ProgramData\HealthChecker\tools\python\3.12.10\`.
6. The installer process is invoked by fixed system path or approved Windows API;
   PATH, current directory, user profile, aliases, environment overrides,
   junctions, and reparse points are rejected.
7. Parameters and exit status are recorded without environment dumps, usernames,
   or secrets. The installer and installation evidence are bound by digest.
8. The separately trusted collector hashes the installed `python.exe` without
   executing it and binds stable file identity, path class, version-resource
   metadata, architecture, reparse verdict, and installer provenance record.
9. Dependency installation occurs only in its separately approved phase using
   `--require-hashes`, `--only-binary=:all:`, the immutable production lock, and
   fixed protected Python after executable provenance review.
10. An independent reviewer compares acquisition, signature, installer digest,
    installation record, installed digest, dependency evidence, and rollback.
11. Runtime activation remains disabled until R4J.

The current runtime should be retained for rollback but excluded from selection
by a separately approved, atomic versioned-path transition. Coexistence must not
create two valid candidates for the same contract. The selected path, rollback
path, and active marker must be unambiguous. Quarantine or replacement requires
an explicit R4G decision; deletion is outside R4B.

## 6. Trusted envelope schema

### 6.1 Envelope and fields

Schema identifier: `hc.protected_runtime_evidence.v2`.

The envelope must contain exactly these top-level objects:

- `schema_version`
- `collector`
- `binding`
- `provenance`
- `runtime`
- `scheduled_tasks`
- `runtime_health`
- `replay`
- `signature`
- `verification`

Required fields:

- `collector`: identity, semantic version, artifact SHA-256, minimum-version
  verdict, literal path-class verdict, reparse verdict, Authenticode verdict,
  signer key identifier, certificate-policy identifier, and chain verdict.
- `binding`: repository commit, active-release commit, manifest commit,
  manifest schema, manifest-complete verdict, manifest-integrity verdict, and
  privacy-safe host binding.
- `provenance`: installer record identifier, installer SHA-256, official-source
  verdict, Authenticode publisher verdict, chain/revocation verdict, controlled
  installation record identifier, and independent-review state.
- `runtime`: fixed-path verdict, stable-file-identity verdict, implementation,
  version, architecture, executable SHA-256, reparse verdict, pre/post stability
  verdict, and `executed_during_collection=false`.
- `scheduled_tasks`: exactly the host and proxy task verdict objects, each with
  principal, enabled/state, action-count, fixed-system-PowerShell, strict
  file-only arguments, active-release binding, trigger, delay, single-instance,
  and bounded-restart verdicts. Raw task arguments are forbidden.
- `runtime_health`: exactly `8743/healthz`, `8743/readyz`, `8744/healthz`, and
  `8744/readyz`, each containing only HTTP-status, schema, body-size, timeout,
  expected-status, and overall verdicts. Raw bodies are forbidden.
- `replay`: collection timestamp, signature timestamp, validity start/end,
  cryptographic nonce, monotonic signer sequence, prior-envelope SHA-256, and
  append-only-ledger verdict.
- `signature`: algorithm, evidence signer key identifier, certificate-policy
  identifier, certificate-chain verdict, revocation verdict, and signature value.
- `verification`: schema, collector allowlist, signature, certificate policy,
  freshness, replay, host binding, release binding, redaction, and overall
  verdicts plus fixed reason codes.

### 6.2 Bounds and canonicalization

- Maximum encoded envelope size: 262,144 bytes.
- Maximum nesting depth: 10.
- Maximum total containers: 96.
- Maximum object members: 64.
- Maximum array elements: 16.
- Maximum general string length: 256 Unicode scalar values.
- Maximum fixed reason-code length: 64 ASCII characters.
- Maximum total scalar values: 768.
- Integers must be exact integers; booleans and floating-point values are not
  interchangeable. Non-finite numbers are forbidden.
- Duplicate keys, unknown fields, invalid Unicode, and control characters are
  rejected.

Canonical serialization must use RFC 8785 JSON Canonicalization Scheme after
strict schema validation. The evidence signature covers every envelope field
except `signature.signature_value`; all other signature metadata is covered.
The envelope SHA-256, if stored externally, covers the complete signed envelope.

The validator must reject future timestamps beyond 120 seconds of allowed clock
skew, evidence older than 10 minutes, validity windows longer than 10 minutes,
duplicate nonces, non-increasing signer sequences, incorrect prior-envelope
links, and signatures outside certificate validity. Clock disagreement never
extends freshness; it produces BLOCKED pending trusted time remediation.

### 6.3 Privacy and prohibited fields

Host binding must be a versioned privacy-safe keyed digest produced under the
approved policy; raw identifiers must not be present. The envelope must not
contain usernames, device identifiers, raw host IDs, file paths beyond approved
path-class enums, task arguments, ACL identities/lists, environment names or
values, command lines, endpoint bodies, tokens, secrets, private keys,
certificate private-key locations, or free-form exception strings.

Outputs use fixed reason codes only. Parsing and verification errors must not
echo input fragments or sensitive locations.

## 7. Offline validation integration

The future integration must expose one combined operation conceptually named
`validate_and_evaluate(envelope_bytes, committed_trust_policy)`. It must perform
strict parsing, signature and certificate-policy verification, freshness and
replay validation, collector allowlisting, privacy-safe host binding, release
binding, and schema validation before policy evaluation.

It must not accept `authenticated`, `trusted`, `verified`, a caller-created
result object, environment trust data, or CLI trust overrides. The validated
result type must be immutable, unexported, non-serializable as an authentication
capability, and constructible only inside the validator after cryptographic
verification. The public API must not accept that type back from a caller; the
validator and policy transition occur in the same closed operation. Security
comes from repeated cryptographic validation and the closed entry point, not
from relying on Python type secrecy.

Only the validator may translate authenticated claims into the existing HC-309
policy registry. Contradictions remain FAIL. Missing, inaccessible, stale, or
ambiguous evidence remains BLOCKED. Until this implementation has passed an
independent security review, overall HC-309 PASS remains unreachable.

## 8. Threat model

| Threat | Prevention | Detection | Fail-closed result |
|---|---|---|---|
| Circular bootstrap | Collector never invokes protected Python or mutable Git | Static/process tests and artifact review | BLOCKED |
| Interpreter self-certification | Separate signed collector hashes without execution | Envelope requires `executed_during_collection=false` | FAIL |
| Collector replacement | Immutable ACL, Authenticode, pinned digest/version | Pre-execution signature, identity, and digest checks | FAIL |
| Signature stripping | Signature is mandatory and schema-exact | Missing/unknown signature field rejection | FAIL |
| PATH/command shadowing | Direct APIs and fixed literal system paths | Hostile-session process tests | FAIL |
| Reparse/junction substitution | Reject reparse ancestry; stable handle identity | Pre/post identity and ancestry checks | FAIL |
| TOCTOU | Hash open stable handle and recheck metadata/identity | Any pre/post change | FAIL |
| Obsolete release/collector | Exact commit binding and minimum collector version | Allowlist and downgrade tests | FAIL |
| Evidence replay | Nonce, sequence, prior link, freshness, append-only ledger | Replay database validation | FAIL |
| Clock manipulation | Trusted-time policy and narrow skew/window | Clock-source disagreement | BLOCKED |
| Certificate misuse/expiry | EKU/policy/identity constraints and validity checks | Chain and policy validation | FAIL |
| Signer compromise | Non-exportable keys, separation, audit, revocation | Revocation and incident review | BLOCKED/FAIL |
| Task substitution | Exact two-task contract and release binding | Read-only task comparison | FAIL |
| Unauthorized digest update | Independent review and repository approval | Required evidence IDs in change review | BLOCKED |
| Malformed resource exhaustion | Byte/depth/container/scalar limits | Bounded parser tests | FAIL/22 |
| Privacy leakage | Exact schema, prohibited fields, fixed reasons | Redaction and negative corpus tests | FAIL |

## 9. Separately gated implementation plan

| Phase | Prerequisites and permitted changes | Prohibited changes | Evidence/tests and approval gate | Stop/rollback conditions |
|---|---|---|---|---|
| R4C — operator signing policy | Approved R4B; document issuer, keys, stores, timestamp, revocation, retention | Keys, cert issuance, runtime changes | Signed decision register; governance review | Any undecided trust anchor |
| R4D — synthetic collector | Approved R4C; collector source and synthetic fixtures only | ProgramData/live host, certificates, executable hashing | Static no-PATH/no-Python checks, hostile shadowing, reparse/TOCTOU simulations, schema tests | Any mutable-code or external-process route |
| R4E — independent review | Frozen R4D patch | Provisioning or installation | Independent security report and all findings closed | Any critical/high finding |
| R4F — provisioning/package | Approved R4E; provision approved certs and immutable collector package | Python reinstall or digest adoption | Chain/EKU/revocation/timestamp tests; artifact reproduction and immutable-install evidence | Signature, chain, ACL, or reproducibility mismatch |
| R4G — controlled reinstall | Approved R4F and verified installer provenance | Activation or runtime digest commit | Acquisition, signature, SHA-256, custody, install and rollback evidence; independent checkpoint | Ambiguous artifact/path, failed signature, unexpected coexistence |
| R4H — evidence collection | Approved R4G; one authorized read-only collection | Contract digest update or activation | Signed envelope, replay/freshness/privacy validation | Any BLOCKED/FAIL collector verdict |
| R4I — digest approval | Approved R4H and independent comparison | Unreviewed digest or unrelated config changes | Schema/runtime/release tests and explicit digest approval | Evidence mismatch or reviewer rejection |
| R4J — live certification | Approved R4I | Clinical/device/commercial claims | Live verifier, complete suite, security and MVP reassessment | Any certification or security gate not PASS |

Every phase requires an explicit new authorization. Failure must preserve the
previous runtime and keep activation/certification BLOCKED. Rollback must use an
already approved versioned artifact; it must never select an unreviewed runtime.

## 10. Acceptance matrix

| Criterion | Acceptance evidence | Current state |
|---|---|---|
| Collector source review | Independent review with no unresolved critical/high findings | BLOCKED |
| No PATH/no Python | Static scan plus hostile process observation proves no invocation | BLOCKED |
| Authenticode | Approved signer, chain, EKU, timestamp, and revocation all valid | BLOCKED |
| Artifact pinning | Reproducible artifact SHA-256 and minimum version approved | BLOCKED |
| Immutable installation | Fixed versioned path, no reparse, governed ACL evidence | BLOCKED |
| Envelope signature | Separate approved evidence key and full signature validation | BLOCKED |
| Replay resistance | Nonce, sequence, link, ledger and freshness negative tests | BLOCKED |
| Privacy/redaction | Exact-schema and prohibited-field test corpus passes | BLOCKED |
| Installer provenance | Official identity, Authenticode and authenticated SHA-256 | BLOCKED |
| Installed executable digest | Separate collector evidence plus independent comparison | BLOCKED |
| Dependency provenance | Exact lock digest and installed wheel/artifact evidence | BLOCKED |
| Release binding | Active commit and complete manifest verified | BLOCKED |
| Task binding | Exact two-task contracts verified without raw arguments in output | BLOCKED |
| Runtime health | Four bounded exact-schema verdicts; never provenance authority | BLOCKED |
| Offline validation | Closed cryptographic validate-and-evaluate operation | BLOCKED |
| Full regression | Focused security tests and complete suite pass | BLOCKED |

No unmet criterion may be described or represented as PASS. A contradiction is
FAIL; unavailable, missing, undecided, or not-yet-implemented evidence is
BLOCKED.

## 11. Operator decision register for Robert

Robert approved this complete package in R4C. The authoritative decision record
is `docs/governance/HC-309-R4C_OPERATOR_SIGNING_AND_PROVENANCE_DECISIONS.md`.

| Decision | Recommended default | Approval state |
|---|---|---|
| Pilot signing model | Private HealthChecker pilot PKI; pilot-only trust | APPROVED |
| Production signing model | Recognized public code-signing CA at commercialization; evidence trust separately governed | APPROVED |
| Code/evidence key separation | Separate certificates and private keys | APPROVED |
| Issuer/trust model | Offline pilot root; approved policy/issuer/signer allowlists | APPROVED |
| Code-signing key | RSA 3072, SHA-256, Code Signing EKU, non-exportable | APPROVED |
| Evidence-signing key | ECDSA P-256, SHA-256, dedicated policy/EKU, non-exportable | APPROVED |
| Private-key storage | LocalMachine context; TPM/CNG where supported; least privilege | APPROVED |
| Timestamping | Trusted RFC 3161 timestamp required for collector signatures | APPROVED |
| Revocation | Full-chain policy; invalid is FAIL, indeterminate is BLOCKED | APPROVED |
| Controlled reinstall | Approved in principle only after R4F and separate R4G authorization | APPROVED |
| Current runtime disposition | Retain inactive for rollback/evidence; never trust observed hash | APPROVED |
| Independent reviewer | Independent phase review; Robert final authority; human production review | APPROVED |
| Evidence retention | Pilot 2 years; production 7 years subject to legal/privacy review | APPROVED |

These approvals authorize governance recording only. Until a later phase is
explicitly authorized, collector implementation, certificate provisioning,
reinstall, evidence collection, digest adoption, and live certification remain
BLOCKED.

## 12. Relationship to existing governance

This specification refines, without overriding:

- `docs/governance/HC-306B_EXTERNAL_PRIVILEGED_EVIDENCE.md`;
- `docs/governance/HC-307_TRUSTED_OPERATOR_RUNBOOK.md`;
- `docs/governance/HC-309_PROTECTED_RUNTIME_CERTIFICATION.md`;
- `docs/HC304B_PRIVATE_HOST_FOUNDATION.md`;
- `config/companion_runtime.json` and `requirements/production.txt`.

The HC-307 environment-HMAC tooling remains a separate preflight mechanism and
is not approved as the HC-309 protected-runtime trust anchor. Existing release
packaging and SYSTEM-task controls remain relevant after bootstrap provenance is
established, but they cannot establish the initial trust of protected Python or
the collector. Any apparent conflict resolves toward BLOCKED and the stricter
rule in this specification until a later approved governance change.
