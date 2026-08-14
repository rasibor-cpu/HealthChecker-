# HC-309-R4F-EXEC-P3 — Offline Ceremony Readiness

Status: **PREPARED; actual ceremony and all mutation remain BLOCKED**

## Scope

P3 prepares a strict repository-only policy validator for a future offline PKI
ceremony-readiness proposal. It does not inspect or configure a workstation,
network interface, TPM, provider, account, removable medium, key, certificate,
CRL, backup, or runtime. It performs no cryptographic operation and stores no
personal custodian identity or private ceremony evidence in Git.

An accepted proposal records reviewed assertions only. The validator cannot
authenticate those assertions. Its result remains `BLOCKED` with exit 20 and
labels preparation checks only `READY_FOR_SEPARATE_APPROVAL`. Invalid or relaxed
policy returns one redacted FAIL record with exit 22.

## Two-custodian boundary

The owner custodian and independent second custodian must be distinct and present
under simultaneous control. Their personal identities exist only in the
protected offline ceremony record outside Git. Role substitution, single-person
control, identity material in repository input, or a missing independent review
is invalid.

## Equipment, media, and backup readiness

The proposed root CA station is dedicated and offline. The issuing CA station is
offline or dedicated and isolated. Network disablement, equipment inventory, and
trusted time with the existing 120-second ceiling require independent evidence
kept outside Git.

Transfer media is ceremony-only, dedicated, encrypted, tamper-evident,
inventoried, scanned offline before and after transfer, and write-protected when
supported. Root backup readiness requires exactly two encrypted geographically
separated copies in tamper-evident storage and an independently verified restore
test that performs no live activation. Private material is prohibited in Git.

## TPM capability boundary

The readiness proposal requires independent non-production evidence for the
Microsoft Platform Crypto Provider, RSA-3072 and ECDSA-P256 behavior,
non-exportability, and ACL enforcement. Software fallback is prohibited. P3 does
not collect this evidence or treat a recorded assertion as host proof.

The current repository finding remains unchanged: TPM readiness and algorithm
behavior are unproven. A future proposal can pass structural validation only
after the protected external evidence and reviews actually exist.

## Locked sequence

The exact readiness sequence is:

1. authorization and scope review;
2. custodian presence confirmation;
3. equipment-isolation verification;
4. trusted-time verification;
5. media-inventory verification;
6. root CA profile review;
7. issuing CA profile review;
8. root CRL procedure review;
9. leaf CRL procedure review;
10. backup-and-restore review;
11. rollback-and-abort review;
12. independent record review; and
13. closeout and seal.

Order changes, omitted steps, inserted steps, and resume after abort are
prohibited. An aborted attempt requires a new ceremony record.

## Revocation and abort controls

The root CRL retains a maximum 90-day validity and the leaf CRL a maximum
seven-day freshness. Both require explicit `nextUpdate`, independent signature
verification, and independent transfer verification. Missing, stale, or
unavailable status remains BLOCKED; invalid or revoked status is FAIL.

Custodian conflict, unexpected connectivity, equipment or media mismatch,
indeterminate TPM/provider state, invalid trusted time, or any evidence/procedure
deviation aborts the attempt. Partial state retention is prohibited. Robert
Asibor retains rollback authority, and the current runtime must remain retained
without deletion or overwrite.

## Authorization boundary

The readiness record requires exact `false` values for equipment mutation, key,
certificate and CRL creation, signing, runtime reinstall, activation, and
certification PASS. It also records that the actual assigned-PEN transition is
not yet approved and the actual ceremony is not authorized.

P3 therefore creates no authority to conduct a ceremony. After IANA assignment,
P2 must be independently completed first. A later separate commit and explicit
approval must adopt protected evidence and authorize any actual non-production
ceremony.
