# HC-201F — Private Health-Record Backfill & Visibility Audit

## Purpose

Provide a **generic, idempotent** backfill path so historical and wearable measurements
can be loaded into Health Vault **without committing private clinical data to Git**.

## Command

```bash
python -m backend.health_vault.backfill --input private_imports/robert_health_backfill.json
```

Optional:

```bash
python -m backend.health_vault.backfill --input path.json --dry-run
python -m backend.health_vault.backfill --input path.json --vault-root /tmp/vault --report private_imports/last_report.json
```

## Privacy

| May commit | Must never commit |
|------------|-------------------|
| `backend/health_vault/backfill.py` | Real patient JSON under `private_imports/` |
| Fictional `docs/examples/health_backfill_template.json` | ECG PDFs / screenshots |
| Tests with fictional fixtures | `vault_storage/index.json` and blobs |
| This documentation | IndexedDB exports / PII |

`private_imports/**` is gitignored except `README.md` and `.gitkeep`.

## Provenance values

Every record must set exactly one of:

- `original_document_verified`
- `user_reported`
- `historical_summary`
- `wearable_screenshot`
- `wearable_pdf`

User-reported and historical-summary values are **not** labeled as laboratory-document verified.

## Idempotency

Each `record_id` produces a **stable SHA-256** content payload. Re-running the importer
hits `ImportPipeline` duplicate detection and inserts **zero** new documents.

## Browser UI note

Server vault (`vault_storage/`) and browser vault (`HC_HEALTH_VAULT_V1` / IndexedDB) are
separate stores. After a server backfill, use the Health Vault AI JSON import (or the
helper `scripts/seed_browser_vault_from_backfill.py`) to mirror records into the browser
for Doctor Visit Mode and timeline visibility.

## Medical disclaimer

HealthChecker+ is observational decision-support tooling. Backfill context notes must not
be treated as diagnoses.
