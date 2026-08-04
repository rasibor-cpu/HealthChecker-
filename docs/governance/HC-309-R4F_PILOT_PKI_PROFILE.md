# HC-309-R4F-PREP — Pilot PKI Profile

Status: implementation-ready design; provisioning is not authorized

This profile implements the approved decisions in
`HC-309-R4B_TRUSTED_COLLECTOR_AND_RUNTIME_PROVENANCE_SPEC.md` and
`HC-309-R4C_OPERATOR_SIGNING_AND_PROVENANCE_DECISIONS.md`. It creates no trust
anchor, certificate, key, signer, or runtime authorization.

## Hierarchy decision

The pilot uses an offline root and a separate constrained issuing CA. The extra
ceremony and revocation record are justified because the root can remain
offline during leaf issuance and an issuing-key incident can be contained
without immediately replacing the root. A root-issued-leaf design has fewer
components but exposes the root during every issuance and is rejected.

| Profile | Algorithm | Validity | Constraints |
|---|---|---:|---|
| Offline pilot root | RSA 4096, SHA-256 | 15 years | CA=true, critical keyCertSign/cRLSign, pathLen=1 |
| Pilot issuing CA | RSA 3072, SHA-256 | 5 years | CA=true, critical keyCertSign/cRLSign, pathLen=0 |
| Collector code signer | RSA 3072, SHA-256 | at most 18 months | critical BasicConstraints present with CA=false; critical KeyUsage has digitalSignature=true and all other represented usages=false; Code Signing EKU only |
| Evidence signer | ECDSA P-256, SHA-256 | at most 12 months | critical BasicConstraints present with CA=false; critical KeyUsage has digitalSignature=true and all other represented usages=false; dedicated private evidence policy only |

The root private key is generated, used, backed up, and recovered only in an
offline governed environment. It is never installed on the runtime host.
Root operations require two authorized custodians, a recorded ceremony,
tamper-evident offline storage, two geographically separated encrypted backup
copies, tested recovery, and an inventory that contains no private material.
Only reviewed public root and issuing certificates may later be distributed.

The issuing CA private key is held only in an offline or dedicated isolated CA
system and never on the runtime host. The collector code-signing private key is
held only by the approved signing station or signing service and never on the
runtime host. The evidence-signing private key is the only signing private key
that may reside on the runtime host; it is non-exportable and accessible only
to the approved collector identity. It must not be placed on the signing
station except during a separately approved recovery or issuance operation.
Public root and intermediate certificates may be distributed only through the
later approved trust procedure. There is no silent software-key fallback.

Root compromise immediately freezes issuance and validation, revokes or
distrusts the affected hierarchy through an independently approved policy
update, blocks dependent evidence, rotates the complete hierarchy, and requires
recollection. Retirement requires overlap only for already timestamped
collector artifacts allowed by policy; no new issuance occurs after cutoff.

## Policy namespace and issuance records

Preparation uses symbolic private identifiers:

- `hc-private-pilot-code-signing-v1`
- `hc-private-pilot-evidence-signing-v1`

Before provisioning, these must be assigned numeric OIDs beneath a private
enterprise arc that HealthChecker demonstrably controls. R4F-PREP does not
invent a public, medical, regulatory, or third-party OID. The issuing CA may
issue only the two approved leaf profiles. Name constraints are not relied on
for authorization; policy OID, EKU, issuer allowlist, key identifier, artifact
hash, and version are all enforced.

Every issuance record binds serial, public key identifier, profile, policy,
validity, requester authorization, approvers, ceremony identifier, and
revocation endpoints. It contains no private key, password, raw identity list,
or secret. Issuing CA rotation begins at least six months before expiry and
requires an independently reviewed trust-policy overlap window.

## Leaf-key controls

Both exact synthetic leaf profiles represent `is_ca=false` and a present,
critical BasicConstraints extension. Their KeyUsage schema requires exact
booleans: `digital_signature=true`, while `certificate_signing`, `crl_signing`,
`key_encipherment`, `data_encipherment`, `key_agreement`, and
`content_commitment` are all `false`. Missing, unknown, implied, or non-Boolean
values are invalid. The EKU and policy separation below remains unchanged.

Code and evidence keys are distinct and non-exportable in the LocalMachine
certificate context. TPM/CNG backing is mandatory where capability is proven.
If TPM support, provider behavior, or key ACL enforcement is indeterminate,
provisioning is BLOCKED; software-key fallback is not automatic.

Code-signing key access is limited to the separately approved signing identity
and Code Signing EKU. Every accepted collector signature requires SHA-256 and a
trusted RFC 3161 timestamp. The key is not used for evidence, TLS, encryption,
login, or general signing.

Evidence-key access is limited to the approved collector identity and the
dedicated evidence policy. It signs only bounded canonical evidence envelopes
using ECDSA P-256/SHA-256. It is not used for Authenticode, TLS, encryption,
login, or code signing. Private-key bytes are never readable or exportable.

## Validation, timestamp and revocation policy

Validation builds the complete chain using only committed allowed public trust
material and the approved validation time. It enforces issuer, policy, EKU,
key usage, algorithm, key size, validity, signer allowlist, minimum collector
version, artifact SHA-256, and immutable-package placement.

- Revoked, invalid, wrong-purpose, wrong-policy, wrong-issuer, wrong-signer, or
  disallowed algorithm: **FAIL**.
- Unavailable/indeterminate revocation, chain, trusted time, TPM status, or
  required public trust material: **BLOCKED**.
- No network fallback, soft-fail, or store-presence-only trust is permitted.

Pilot development requires an operator-selected RFC 3161 service or a governed
private pilot TSA. No service is selected or contacted by this phase. The
future decision must pin TSA policy and chain, timestamp digest, allowed URLs,
availability behavior, and procurement/operation ownership. Production TSA
selection is deferred to commercialization.

The offline root produces and signs a distinct root CRL covering issuing-CA
revocation during a two-custodian offline ceremony. The root CRL has a maximum
90-day validity and explicit `nextUpdate`, is transferred offline with its
ceremony record, independently verifies before publication, and is retained
with every superseded version under the evidence-retention policy. Emergency
root-CRL issuance follows the same signing and independent-verification controls
with expedited approval. Exact publication locations and operational
identities remain unresolved until a later phase.

The pilot issuing CA publishes a separate signed full leaf CRL and may add OCSP only after a
separate service design. Validators use a cached CRL only until its `nextUpdate`
and a maximum policy freshness of 7 days for the leaf CRL and 90 days for the
root CRL, whichever applicable limit comes first. Offline validation requires
both separately transferred, signature-verified, current CRLs. A missing,
stale or unavailable root or leaf CRL is BLOCKED. A revoked issuing CA, revoked
leaf, invalid CRL signature or wrong CRL policy is FAIL. Having only the leaf
CRL is BLOCKED. Clock skew is limited to 120 seconds.
Evidence remains subject to the ten-minute R4B freshness window.

Emergency revocation removes the signer and artifact from allowlists, blocks
new envelopes, preserves evidence for investigation, rotates the affected key,
and requires independent recollection. Rotation never permits downgrade below
the committed minimum collector version.

## Remaining prerequisites

- assign controlled numeric private policy OIDs;
- prove TPM readiness and RSA-3072/ECDSA-P256 key generation capability;
- approve exact CNG provider and least-privilege identities;
- select the pilot RFC 3161 service;
- approve CRL publication, transfer, freshness, and incident operations;
- independently review certificate templates before provisioning.

R4F-EXEC and R4G remain unauthorized. No pilot trust may silently become
production trust.
