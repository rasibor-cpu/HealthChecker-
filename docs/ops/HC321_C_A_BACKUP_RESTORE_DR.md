# HC321-C-A Encrypted Backup / Restore / Disaster Recovery

## Scope

Governed encrypted vault backup and fail-closed restore for HealthChecker.
This is an **operator engineering** procedure. It does not claim insurance,
regulatory certification, or contractual SLOs.

## Key separation

| Material | Storage |
|---|---|
| Vault encryption key | Protected secrets store (not inside `.hcb` backup) |
| Backup recovery key | Separate operator custody (not inside vault payload) |
| Backup payload (`.hcb`) | Encrypted `hc.backup.v1` archive; no plaintext PHI staging |

`vault.key`, `server.key`, and `keystore.properties` are excluded from backups.

## Create backup

```powershell
.\scripts\hc_backup_restore_dr.ps1 -Action Create `
  -VaultRoot <encrypted-vault> `
  -BackupPath <dir\vault-YYYYMMDD.hcb> `
  -RecoveryKeyFile <protected-recovery-key-file>
```

Creates an authenticated archive (in-memory ZIP → encrypt → atomic write).

## Verify integrity (no activation)

```powershell
.\scripts\hc_backup_restore_dr.ps1 -Action Verify `
  -BackupPath <file.hcb> `
  -RecoveryKeyFile <protected-recovery-key-file>
```

Wrong recovery key, truncation, or tamper → fail closed. No vault mutation.

## Isolated restore (acceptance / DR drill)

**Never** restore over the live production vault for drills.

```powershell
.\scripts\hc_backup_restore_dr.ps1 -Action RestoreIsolated `
  -BackupPath <file.hcb> `
  -TargetRoot <empty-temp-vault> `
  -RecoveryKeyFile <protected-recovery-key-file> `
  -VaultKeyFile <protected-vault-key-file>
```

Order: VERIFY → stage beside target → authenticate staged vault → promote only
when valid. Existing target (if any) is moved to `.previous.*` and restored on
failure. Interrupted promotion: use `-Action RecoverInterrupted`.

## Production replacement restore

1. Stop consumer API.
2. Create a fresh encrypted backup of the current live vault (rollback path).
3. Restore into an isolated path first; compare fingerprints.
4. Only after isolated proof, perform governed cutover per operator change control.
5. Start API and run privacy-safe KPI probes.

## Corruption / wrong-key behavior

- Tampered/truncated backup → `backup_integrity_failed` / `backup_authentication_failed`
- Wrong recovery key → authentication failure; target untouched
- Wrong vault key → staged open fails before promotion; current vault preserved
- Interrupted mid-swap → `RecoverInterrupted` returns prior vault

## RPO / RTO policy placeholders

| Field | Value |
|---|---|
| RPO | `OPERATOR_POLICY_PLACEHOLDER_RPO` — owner must define maximum acceptable data loss window and backup frequency |
| RTO | `OPERATOR_POLICY_PLACEHOLDER_RTO` — owner must define maximum restore time to consumer availability |

These placeholders ship in backup manifests so operators cannot silently assume
an undocumented SLO.

## Rollback

Keep the pre-cutover `.hcb` and/or `.previous.*` vault copy until post-restore
acceptance completes. On failed activation, restore previous vault and restart.
