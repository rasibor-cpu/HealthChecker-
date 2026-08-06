# HC-309-R4F-EXEC-P2 — Assigned-PEN Transition Validator

Status: **PREPARED; BLOCKED pending actual assignment and separate approval**

## Purpose and boundary

This phase prepares strict validation for the repository transition that may be
proposed after IANA assigns the HealthChecker Private Enterprise Number. No PEN
has been assigned or recorded by this phase. Test PENs are synthetic and are not
HealthChecker identifiers.

`backend.health_vault.companion_host.assigned_pen_transition` accepts only a
bounded, operator-supplied JSON proposal. It performs no network, IANA, host,
certificate-store, filesystem, signing, installation, activation, or mutation
operation. It cannot authenticate registry evidence or reviewer identity; those
remain human and offline review responsibilities.

The accepted result is always `BLOCKED`, returns exit 20, labels validated
elements only `READY_FOR_SEPARATE_APPROVAL`, and keeps offline ceremony review,
PKI mutation, signing, runtime reinstall, and certification PASS blocked. Invalid
input returns one redacted FAIL result with exit 22.

## Exact proposed identifier profile

After independent registry verification, the proposed enterprise arc is:

`1.3.6.1.4.1.<assigned-pen>`

The transition proposal must derive two distinct identifiers beneath that exact
arc:

- certificate-policy OID: `1.3.6.1.4.1.<assigned-pen>.1.1`;
- private evidence EKU OID: `1.3.6.1.4.1.<assigned-pen>.2.1`.

Foreign arcs, Microsoft arcs, UUID-derived `2.25` identifiers, malformed or
non-canonical arcs, reused purpose identifiers, and RFC example PEN 32473 are
rejected. The assigned PEN must be an integer from 1 through 4294967294 and must
match the enterprise arc exactly.

The numeric boundary and reserved endpoints follow
[RFC 9371](https://www.rfc-editor.org/rfc/rfc9371.html); rejection of documentation
PEN 32473 follows [RFC 5612](https://www.rfc-editor.org/rfc/rfc5612.html).

This profile is a proposal for separate review, not an adopted live policy. No
authoritative transition JSON belongs in Git until the actual assignment has
been independently verified.

## Review record

An accepted future proposal must record:

1. the exact IANA registry and Robert Asibor assignee match;
2. independent verification with evidence retained only in a protected offline
   location outside Git;
3. approved owner review and independent OID review by distinct reviewers;
4. a required separate repository commit and offline ceremony review; and
5. explicit `false` values for every mutation, key, certificate, signing,
   reinstall, activation, and certification authorization field.

Recorded assertions do not authenticate themselves. Independent source review
and explicit approval remain mandatory before committing any assigned value.
RFC 9371 also notes that the public PEN registry does not cryptographically bind
a registrant to a PEN, so a registry match is necessary but not itself a trust
anchor.

## Transition after IANA assignment

When IANA responds, do not edit the pending-PEN record in place. First verify the
public registry assignment independently and preserve the evidence outside Git.
Then prepare a new transition proposal using the exact profile above, review it
with the owner and distinct independent reviewer, run the validator tests, and
submit it as a separate commit. Even an accepted transition remains BLOCKED until
the offline ceremony-readiness gate is separately approved.

The repository-only ceremony-readiness validator is documented in
`HC-309-R4F-EXEC-P3_OFFLINE_CEREMONY_READINESS.md`. P3 prepares policy checks but
does not approve an assigned-PEN transition or authorize an actual ceremony.
