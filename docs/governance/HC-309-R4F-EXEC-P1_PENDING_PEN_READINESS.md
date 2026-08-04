# HC-309-R4F-EXEC-P1 — Pending-PEN Readiness Record

Status: **BLOCKED before mutation**

This repository-only phase records reviewed pilot decisions while the HealthChecker
Private Enterprise Number remains pending. It authorizes no live execution,
provisioning, key or certificate creation, signing, installation, runtime change,
or certification PASS.

## Authority and privacy

Robert Asibor is the HealthChecker owner and PEN assignee. The IANA request is
confirmed, but the assigned PEN remains pending. No IANA request reference, email
address, or other contact information is stored in Git.

The two-custodian model is approved. The owner custodian and an independent second
custodian are designated. Personal custodian identities belong only in the
protected offline ceremony record outside Git; the second custodian's personal
information is not recorded here.

## Authoritative repository record

`config/hc309_r4f_exec_pending_pen_readiness.json` is the exact public readiness
record. `backend.health_vault.companion_host.r4f_preparation` parses and evaluates
that bounded record without inspecting the host. Missing, duplicate, unknown, or
wrongly typed fields are configuration errors. Substituted policy, identity,
toolchain, authorization, or trust values fail closed.

The evaluator has a fixed mandatory-check registry and gives FAIL precedence over
BLOCKED. The accepted record remains BLOCKED because PEN assignment, OID
materialization, PKI mutation, signing, runtime reinstall, and certification PASS
are unavailable. It has no state transition that can enable mutation by editing
JSON, never returns PASS, and never returns exit 0.

## Pending PEN and OID boundary

- PEN state: `pending_assignment`.
- Assigned PEN: absent (`null` in the exact schema).
- Certificate-policy OID: absent.
- Private evidence EKU OID: absent.
- Placeholder, example, reserved, foreign-arc, UUID-derived `2.25`, and
  caller-supplied OIDs are prohibited.
- OID materialization remains prohibited until the assigned PEN is independently
  verified and reviewed through the transition below.

No keys, certificates, signatures, signed packages, or authenticated live evidence
exist as a result of this phase.

## Reviewed readiness decisions

The exact JSON record captures the approved pilot algorithms, lifetimes, CA and
leaf separation, non-exportability, Microsoft Platform Crypto Provider requirement,
prohibition on software fallback, Local Administrators/SYSTEM access classes,
DigiCert pilot RFC 3161 endpoint and full-chain validation, fail-closed revocation,
freshness and clock-skew limits, protected future locations, two-year pilot
retention, owner rollback authority, and retention of the current runtime.

The timestamp endpoint alone is not a trust anchor. Stale or unavailable
revocation status is BLOCKED; revoked, invalid, wrong-purpose, or wrong-policy
status is FAIL.

## Operator-supplied toolchain evidence

The record contains reviewed operator-supplied evidence for Windows SDK
10.1.26100.8876, the fixed x64 SignTool path, Microsoft publisher/signature status,
and the reviewed SignTool and SDK-installer SHA-256 values. Repository code did not
collect or independently authenticate this evidence. SignTool readiness does not
authorize signing, execution, installation, or certificate creation.

## Runtime disposition

The current runtime remains healthy but uncertified and must be retained without
deletion or overwrite. Active tasks remain bound to older immutable releases, not
the current repository HEAD. R4F PKI mutation and R4G reinstall remain BLOCKED.
Certification PASS is unreachable, and no executable digest is adopted.

## Required transition after PEN assignment

PEN assignment alone cannot authorize mutation. The next transition requires:

1. independent registry verification of the assigned PEN;
2. a repository update establishing the assigned enterprise arc;
3. independent review of the numeric certificate-policy and evidence-EKU OIDs;
4. a separate commit and explicit approval;
5. an offline ceremony-readiness review.

Only a later separately authorized phase may consider PKI mutation or runtime
replacement after those gates pass.
