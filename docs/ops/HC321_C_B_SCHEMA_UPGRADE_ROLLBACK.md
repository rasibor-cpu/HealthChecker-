# HC321-C-B Schema Migration / Upgrade / Rollback

## Authoritative schema

- Vault index schema: `hc.health_vault.v1` (`CURRENT_SCHEMA`)
- Production startup calls `VaultMigrationManager.validate_current()` and refuses
  unsupported or future schemas (`production_vault_schema_incompatible`).

## Supported forward path

| From | To | Behavior |
|---|---|---|
| `hc.health_vault.v0` | `hc.health_vault.v1` | Deterministic stamp via default plan; clinical payload unchanged |
| `hc.health_vault.v1` | `hc.health_vault.v1` | Idempotent no-op |
| unknown / future | any | Fail closed (`migration_path_unavailable` / schema incompatible) |

## Pre-migration checkpoint (required)

Never migrate a live vault without an encrypted `.hcb` checkpoint:

```powershell
.\scripts\hc_schema_migrate.ps1 -Action CheckpointAndMigrate `
  -VaultRoot <temp-or-change-controlled-copy> `
  -VaultKeyFile <vault-key> `
  -RecoveryKeyFile <recovery-key> `
  -CheckpointPath <dir\pre-migrate.hcb>
```

Order: CREATE encrypted backup → VERIFY → MIGRATE → VALIDATE current schema.
On failure the index is rolled back to the pre-migration document; the
checkpoint remains for restore.

## Fail-closed rules

- Partial migration cannot silently become the active production vault: index
  write is atomic; contract/post-check failure restores the original index.
- Production API activation refuses incompatible schemas before serving traffic.
- Downgrade is not supported unless an explicit future plan is engineered and
  reviewed (none ship by default).

## Rollback / recovery

1. Stop consumer API.
2. Restore from the pre-migration `.hcb` into an **isolated** path (see C-A).
3. Compare fingerprints / smoke KPIs.
4. Cut over only after isolated proof.
5. Restart API.

Migration audit events (`migration_audit.json`) record schema ids and outcomes
only — not clinical payloads.

## Installed release upgrade

Desktop installer (C-B2 packaging) replaces application bits under Program Files
while vault data stays under ProgramData. Schema migration, when required, is a
separate governed checkpointed step against a vault copy — never an in-place
silent rewrite of production during package install.
