# HC-309-R4G — Controlled CPython 3.12.10 Reinstall Runbook

Status: non-executed preparation; every mutation requires later R4G approval

This runbook deliberately contains no paste-ready installation command.
All state-changing steps below are labels for a future approved operator
procedure, not authorization to execute them.

## Stop conditions

Stop on missing approval, stale/ambiguous evidence, inaccessible protected
state, unavailable revocation, wrong installer identity, mutable/reparse path,
failed backup, active competing runtime, missing independent reviewer, phone or
background-monitoring continuity risk, or any request to delete current assets.

## Preflight

1. Independently approve the signed immutable collector package, public pilot
   chain, artifact allowlist, minimum version, and exact installed collector.
   Establish the external `HC_COLLECTOR_TRUST_POLICY.json` identity/version/hash
   before parsing the candidate package; validate the canonical manifest's
   detached CMS/PKCS#7 signature against that external policy. Package-contained
   trust material cannot override it.
2. Prove collector signature, RFC 3161 timestamp, chain, policy and revocation.
3. Capture privacy-safe companion-host state, inactive-runtime evidence,
   protected-host backup/rollback evidence, release/task bindings, and current
   evidence pointers without executing the protected interpreter.
4. Approve a maintenance window, operator and independent reviewer, monitoring
   continuity plan, phone/background-data gap procedure, and immediate rollback
   authority.

## Acquisition and independent verification

The only candidate is `python-3.12.10-amd64.exe`, acquired from the exact
version-specific HTTPS location on `python.org` under a later approved custody
procedure. A separate verifier confirms fixed filename, CPython 3.12.10,
Windows AMD64, expected Python Software Foundation Authenticode publisher,
full chain, Code Signing EKU, validity, RFC 3161 timestamp, and revocation.

SHA-256 must be authenticated by an approved official provenance channel and
reviewed independently. MD5, an unauthenticated web page, a search result,
transport alone, or a hash observed after installation is insufficient. Record
acquisition and verification separately without URLs containing secrets,
usernames, device identifiers, environment data, or private paths.

## Future state-changing installation — NOT AUTHORIZED

The later frozen procedure must use an explicitly verified installer artifact,
an explicitly approved Administrator identity, a fixed system invocation
boundary, and deterministic noninteractive parameters. It targets only the
versioned protected runtime class for CPython 3.12.10 Windows AMD64.

It must disable PATH registration, user-profile installation, Python launcher,
file associations, shortcuts, mutable download features, and reparse-point
resolution. It selects only approved runtime and standard-library features.
Privacy-safe logs record fixed reason codes, artifact identifiers, parameters
by approved enum, exit code and ceremony identifier—not raw command lines,
environment values, identities, or protected paths.

The existing runtime remains inactive and untouched. It is not overwritten,
renamed, quarantined, selected, or deleted.

## Provenance gates

After installation, the independently trusted collector opens the installed
`python.exe` without executing it, rejects reparse ancestry, records stable
file identity and version-resource/PE architecture, hashes through the open
handle, and rechecks identity, size and metadata. It binds that result to the
reviewed installer SHA-256, signature, publisher, timestamp, acquisition record,
installation record and immutable collector identity.

Dependency installation is a later explicit operation using the immutable
production hash lock, `--require-hashes`, binary-only policy, and the already
approved fixed runtime. Installed dependencies and lock hashes are verified
without trusting runtime self-report alone. A canonical append-only envelope is
signed by the distinct evidence key and independently reviewed.

The observed executable digest remains evidence—not authority—until the
independent reviewer approves the complete chain and a separate R4I change
commits it. Digest adoption before evidence approval is forbidden.

## Activation and rollback

The replacement remains inactive until installer provenance, runtime identity,
dependency lock, collector evidence, immutable release, tasks, and bounded
health checks all pass their separately approved gates. Activation uses one
atomic protected pointer; no PATH or newest-version discovery is allowed.

Immediate rollback triggers include any identity/hash drift, chain or
revocation change, task/release mismatch, failed readiness check, evidence
signature/replay failure, monitoring discontinuity, or ambiguous runtime.
Rollback atomically reselects only the retained approved pointer and preserves
both runtimes and evidence. Deletion requires a later retention decision and
explicit approval after successful observation.

R4G execution, R4H evidence collection, R4I digest adoption and R4J live
certification remain unauthorized.
