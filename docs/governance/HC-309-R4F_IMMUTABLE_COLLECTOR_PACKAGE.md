# HC-309-R4F-PREP — Immutable Collector Package Design

Status: design only; staging, signing, installation and activation unauthorized

The future package is rooted at the literal versioned class
`C:\ProgramData\HealthChecker\collectors\protected-runtime\<semver>\`.
This document does not create, inspect, or modify that location.

## Required package

Each version contains exactly:

- `Invoke-ProtectedRuntimeCollector.ps1` — approved Authenticode-signed artifact;
- `PACKAGE_MANIFEST.json` — canonical package contract;
- `PACKAGE_MANIFEST.p7s` — future detached CMS/PKCS#7 signature;
- `HC_PROTECTED_RUNTIME_ENVELOPE_SCHEMA.json` — pinned envelope schema;
- `PILOT_PUBLIC_TRUST.json` — informational public material only.

No private key, certificate password, mutable repository dependency, installer,
runtime binary, downloaded module, log, cache, or environment-derived value is
allowed.

`PACKAGE_MANIFEST.json` lists exactly the three payload assets: the collector,
the envelope schema and informational public trust file. It never lists or
hashes itself, and it never lists or hashes `PACKAGE_MANIFEST.p7s`. Each payload
entry has one literal relative name, byte size and SHA-256.

The manifest schema contains exactly: schema identifier, semantic package
version, minimum accepted version, artifact entries with literal relative name,
size and SHA-256, collector artifact SHA-256, allowed signer policy/key IDs,
required Authenticode and RFC 3161 policy, envelope schema identifier/hash,
public-trust-material hash, creation ceremony identifier, and independent
review identifier. Raw identities and paths are forbidden.

JSON is strict UTF-8 and RFC 8785 canonicalized exactly once after exact schema validation.
Duplicate/unknown fields, non-canonical names, path separators in asset names,
reparse targets, extra/missing assets, or hash discrepancies are FAIL. The
manifest SHA-256 covers every byte of that single RFC 8785 canonical
serialization; there is no manifest-digest field and no self-hash exclusion.
The detached CMS/PKCS#7 signature signs those exact same canonical bytes and is
not permitted to define a trust policy. Package acceptance independently hashes
every listed payload. Authenticode verification covers the collector.

## External bootstrap trust policy

The authoritative future artifact is named `HC_COLLECTOR_TRUST_POLICY.json`.
It is governed outside the candidate package: first as an independently
reviewed committed repository policy, then only under a separately approved
immutable installation procedure. Its live version, digest, signers, OIDs and
other values remain absent during PREP.

The external policy pins package schema version, permitted and minimum
collector versions, canonical manifest SHA-256, permitted manifest and code
signer policy, certificate-policy identifiers and signature algorithm. Its own
expected identity, version and SHA-256 are established by the governed launcher
or recorded operator checkpoint before any candidate package bytes are parsed.
Candidate package contents cannot modify, replace or override it. The
package-contained public trust file is informational until it exactly matches
the already established external policy.

Updates require repository review, independent approval and separate protected
installation authorization. Rollback may select only an externally allowlisted
non-revoked policy/version and may not lower the committed minimum version.
Emergency revocation updates the external policy first and blocks affected
signers, manifests and versions before package processing.

Bootstrap verification order is fixed:

1. Establish the independently committed/installed external trust policy.
2. Validate its expected identity, version and hash at the governed launcher or operator checkpoint.
3. Read only the bounded candidate manifest.
4. Canonicalize it exactly once under RFC 8785.
5. Compare its SHA-256 with the external policy.
6. Validate its detached signature and signer against the external policy.
7. Validate the exact payload set and payload hashes.
8. Validate collector Authenticode, version and literal location.
9. Reject downgrade, reparse, mutable placement or trust-policy mismatch.
10. Only then permit a later separately authorized execution.

The package never bootstraps trust from its own root, allowlist, signer policy,
minimum version, manifest or informational trust material.

## Path and immutability controls

The collector verifies its literal self-location, stable file identity,
versioned parent, and every ancestor from the fixed collector root. Any
reparse point, junction, symlink, network resolution, alternate data source,
user-profile location, Git location, or identity change is FAIL.

The later installer must establish protected inheritance-free ACLs granting
write only to approved installation identities and read/execute only where the
collector design requires it. ACL principals and exact commands are deferred
to R4F-EXEC review. Runtime identities cannot modify version directories,
manifests, schemas, trust files, activation pointers, or rollback pointers.

## Staging, activation and rollback

A later approved installer uses a sibling version-specific staging directory
under the governed collector root. It validates destination ancestry before
creation, writes exclusively, closes all handles, applies immutable ACLs, and
performs complete signature/hash/schema/reparse checks before an atomic rename
to the final version directory.

Activation is a separately protected atomic pointer containing one validated
semantic version. A rollback pointer names the last independently approved
version. Pointer replacement uses write-new, flush, validate, and atomic replace;
there is no search or newest-directory fallback. Activation refuses downgrade,
ambiguous candidates, missing review, incomplete package, or a version below
the committed minimum.

Pre-install checks include phase authorization, clean committed package input,
approved signer/TSA/revocation state, collision absence, fixed filesystem and
non-reparse ancestry, rollback availability, and inactive runtime collection.
Post-install checks reopen every artifact, revalidate stable identity and
hashes, Authenticode, canonical manifest, ACL class, version, pointers, and
absence of unexpected assets.

Partial installation leaves the active pointer unchanged. An incomplete
staging directory is evidence for later reviewed cleanup and is never executed.
Rollback atomically selects only the previously approved immutable version and
records a privacy-safe append-only decision. Uninstall first deactivates; no
version or evidence is deleted without separate retention and deletion approval.

R4F-EXEC must independently review the frozen signed package and installer
before any ProgramData mutation. R4G remains unauthorized.
