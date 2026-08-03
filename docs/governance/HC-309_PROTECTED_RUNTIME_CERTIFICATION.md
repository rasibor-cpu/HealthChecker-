# HC-309 — Protected Runtime Certification Boundaries

HC-309-R3-R2 separates unprivileged offline policy evaluation from privileged
evidence collection. It does not provide a live certification path yet.

The offline module `protected_runtime_policy` consumes a privacy-safe evidence
JSON document and performs deterministic policy evaluation only. It never
accesses ProgramData, tasks, processes, protected executables, or endpoints.
Public offline evidence is unauthenticated, so even internally consistent
evidence remains BLOCKED. Offline evaluation is not live provenance
certification.

## Prerequisites and command

Unprivileged offline evaluation:

```powershell
python -B -m backend.health_vault.companion_host.protected_runtime_policy evidence.json
```

The privileged `Test-ProtectedRuntimeCertification.ps1` boundary is
intentionally hard-disabled: it always emits one fixed BLOCKED JSON line with
exit code 20 through a direct console call. It performs no command-resolved
serialization or output and never launches an executable, subprocess, module,
or repository code. Live collection remains BLOCKED until an
independently trusted immutable collector is installed by the governed release
process and authenticates evidence through HC-307. Mutable repository code
must never be elevated.

The authoritative `config/companion_runtime.json` must eventually contain a reviewed
`python.executable_sha256` for the protected installed `python.exe`. Until that
digest is independently established and committed, live certification fails
closed with an invocation/configuration result. Never derive it from a
user-profile interpreter.

The evaluator emits one compact JSON object to stdout. Schema
`hc.protected_runtime_policy_result.v1` contains `overall`, `exit_code`,
`evidence_authenticated`, and privacy-safe `checks` entries containing only
`name`, `status`, and `reason`. Implemented individual states are `PASS`,
`BLOCKED`, and `FAIL`.

Exit codes:

- `0`: PASS
- `20`: BLOCKED because required evidence is inaccessible
- `21`: FAIL because evidence contradicts a security/runtime contract
- `22`: invocation or authoritative configuration error

PASS is reserved and currently unreachable. No public or internal evaluator
accepts an authentication assertion, and evidence cannot authenticate itself
through Python, CLI, JSON, or environment input. A future PASS path requires a
separately reviewed HC-307 trusted-envelope validator. No trusted live collector
currently exists. HTTP health never compensates for missing provenance.

The protected executable digest must remain absent until it is independently
established through that governed trusted path. Offline policy evaluation is
not live certification, even when every supplied runtime claim is consistent.

Evidence must contain only bounded booleans, enums, hashes, versions, and commit
identifiers. It must never contain environment dumps, paths, commands,
arguments, usernames, device identifiers, tokens, pairing material, secrets,
raw HTTP bodies, or ACL identities.

This policy is only groundwork for future software/runtime certification. It is
not clinical certification, medical-device certification or approval,
production-deployment approval, or commercialization readiness.
