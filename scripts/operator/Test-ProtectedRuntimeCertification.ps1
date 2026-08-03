# HC-309-R3-R2 - privileged live collection is intentionally BLOCKED.
# PowerShell 5.1 compatible. No command resolution or mutable-code launch.

[Console]::Out.WriteLine('{"checks":[],"error":"trusted_collector_unavailable","evidence_authenticated":false,"exit_code":20,"overall":"BLOCKED","schema_version":"hc.protected_runtime_policy_result.v1"}')
[Environment]::Exit(20)
