# PAPS-001 Repository Census

Status: IN PROGRESS

## Objective

Create a precise, reproducible database of this project's information sources and inventory the complete repository before any artifact is declared missing or regenerated.

## Census layers

1. Repository metadata and branch register.
2. Canonical/current working heads and release-candidate heads.
3. Recursive file/tree inventory for each materially distinct branch.
4. Artifact classification: source, documentation, artwork/media, prompts, manifests, QA/tests, evidence, builds/releases, reports, generated outputs.
5. Information-source register: internal canonical sources, external primary sources, external generation services, legal/licensing references, runtime/test evidence.
6. Cross-branch reconciliation and duplicate/superseded detection.
7. Missing-artifact reconciliation under PAPS-001 search-before-regenerate rules.
8. Final machine-readable Project Information & Artifact Register.

## Controls

- Read-only census of project content except for governance/inventory records on this branch.
- No production deployment, merge, release, spend, destructive cleanup, or regeneration.
- Existing artifacts are not moved or deleted during census.
- Potential duplicates and obsolete artifacts are classified before any cleanup.
- Secrets, PHI, credentials, restricted licensed content, and unsuitable binaries are not copied into inventory records.
- Every claim of GENUINELY MISSING requires repository/history/manifest reconciliation first.

## Completion criterion

The census is complete only when important project information sources and retained artifacts are traceable to canonical repository paths or approved manifested external storage, and branch-only artifacts have been reconciled.
