# HC-309-R4D — Synthetic Collector Implementation Note

Status: synthetic development/test foundation only

The R4D entry point is
`scripts/operator/Invoke-SyntheticProtectedRuntimeCollector.ps1`. It exercises
bounded parsing, deterministic verdict aggregation, and future envelope shape
without performing trusted or live collection.

## Non-authorizations

R4D does not authorize or implement live runtime access, ProgramData access,
certificates, keys, signatures, installer acquisition, reinstall, tasks,
services, ACLs, release changes, endpoint queries, executable hashing, digest
adoption, authenticated evidence, certification PASS, or host mutation. The
entry point refuses elevated execution and has no live-mode or environment
override.

It launches no child process and imports no Python, Git, package manager, shell,
repository module, or downloaded code. Security-relevant parsing and output use
direct .NET APIs rather than command-resolved serialization or file cmdlets.

## Synthetic fixture

Schema: `hc.protected_runtime.synthetic_fixture.v1`.

The exact top-level fields are `schema_version` and `checks`. `checks` contains exactly one
`id`, `status`, and fixed `reason` object for every mandatory synthetic check.
Unknown, missing, or duplicate fields/checks are rejected. Authentication,
signature, certificate, host-binding, executable-digest, raw path, identity,
environment, task-argument, endpoint-body, secret, token, and free-form reason
fields are not part of the schema and are rejected. No caller-controlled
timestamp, nonce, sequence, path, or other collection metadata is accepted.

Bounds:

- 65,536 encoded bytes, enforced by reading at most 65,537 bytes into one fixed
  buffer before decoding or parsing;
- nesting depth 8;
- 32 containers;
- 16 members per object;
- 16 elements per array;
- 256 characters per string;
- 128 scalar values;
- strict UTF-8 with invalid sequences rejected; one optional leading UTF-8 BOM
  is removed, while a repeated or non-leading BOM is rejected;
- exact signed 64-bit integers only; floats are rejected;
- duplicate JSON keys rejected at every nesting level.

The complete standard-input operation has one fixed, non-configurable
10,000-millisecond total deadline. The deadline starts before the first read
and is not reset by partial reads. Expiry produces the same single redacted
configuration-error record, empty stderr, and exit 22; partial input is never
decoded, parsed, or emitted.

Windows PowerShell 5.1 does not reliably cancel an outstanding asynchronous
read from every redirected pipe implementation. R4D therefore does not claim
that the underlying pipe read is intrinsically cancellable. It uses one .NET
`CancellationTokenSource` for the entire operation and races each asynchronous
read against that single deadline. If the deadline wins, the collector exits
the process immediately through its fixed failure path, which also terminates
any outstanding reader. No external helper, child process, persistent timer,
environment setting, or caller field can configure or extend the deadline.

The production schema intentionally has no caller-controlled string whose
decoded value may be echoed. Consequently, the valid-surrogate regression can
prove deterministic redacted schema rejection without proving parser
acceptance through an observable output difference. R4D retains that safe
limitation rather than adding a public parser hook, debug output, or echo
surface solely for test observability.

## Synthetic output

Schema: `hc.protected_runtime.synthetic_envelope.v1`.

Output is one deterministic compact JSON document with canonical fixed field and
check order. It always includes:

- `environment: synthetic`;
- `authentication_status: unavailable`;
- `collector.artifact_identifier: synthetic-untrusted`;
- `certification_status: BLOCKED` or `FAIL`.

It contains no signature, signer assertion, certificate identifier,
authoritative executable digest, production host binding, or caller-controlled
collection metadata.

Exit codes are BLOCKED/20 for complete noncontradictory synthetic observations,
FAIL/21 when any observation is contradictory, and 22 with one fixed redacted
configuration record for malformed or forbidden input. FAIL takes precedence
over BLOCKED. Exit 0 and certification PASS are unreachable.

## No-write and hostile-environment guarantees

The collector accepts synthetic JSON only from redirected standard input, writes one stdout record,
and creates no file, directory, cache, log, evidence, or temporary artifact. It
does not modify environment or persistent configuration. Tests create fixtures
only beneath pytest's external temporary directory.

The approved future supported invocation is:

```powershell
C:\Windows\System32\WindowsPowerShell\v1.0\powershell.exe `
  -NoLogo -NoProfile -NonInteractive `
  -File <collector-script>
```

That exact invocation is currently BLOCKED because R4D is explicitly unsigned
and the host default policy is Restricted. R4D process tests therefore add
process-local `-ExecutionPolicy Bypass` solely to exercise unsigned synthetic
code. This does not alter machine or user policy and is not a supported trusted
invocation or a future trust anchor. R4F must Authenticode-sign the collector
and prove the exact invocation above before trusted operation.

Synthetic JSON is redirected to standard input. The collector accepts no
command-line arguments. Invocations that load a profile or resolve PowerShell
through PATH are unsupported; the script cannot suppress output emitted before
it starts.

Process tests install hostile functions for JSON conversion, file cmdlets,
output cmdlets, Python, Git, and PowerShell names. The collector does not resolve
or invoke them. Source checks prohibit live surfaces and child-process APIs.

## Later gates

R4E must independently review parser correctness, hostile-session resistance,
no-write behavior, elevation refusal, deterministic output, resource bounds,
and the absence of live/child-process surfaces.

R4F remains separately unauthorized. Before any trusted operation it must add
the approved pilot certificates and policies, Authenticode signing, immutable
versioned packaging, artifact hash/minimum-version enforcement, non-exportable
evidence signing, and independent installation evidence. Until all later gates
are explicitly approved and pass, authenticated evidence and HC-309 PASS remain
unreachable.
