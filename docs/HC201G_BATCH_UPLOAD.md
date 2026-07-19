# HC-201G — Multi-file Health Vault Upload & Batch Ingestion

## Overview

HealthChecker+ accepts **multiple** PDFs, PNGs, JPGs, and JSON files in one action.
Every file is processed through the existing canonical **ImportPipeline** — parsers,
validation, storage, trends, timeline, Doctor Visit, and audit are not duplicated.

## Browser workflow

1. Open the **Health Vault** tab.
2. Choose files (multi-select), use **Camera / gallery** (where supported), or drag-and-drop onto the drop zone.
3. Review the **batch preview** (filename, type, size, thumbnail, classification, status).
4. Remove unwanted items or **Clear Queue**.
5. Tap **Import All** (no per-document confirmation required).
6. Watch progress: processed / imported / duplicates / failed.
7. Expand error details if needed; use **Retry Failed** for failures only.

Single-file import remains available (selecting one file still works).

### Mobile usability

- Large tap targets (≥44px) on action buttons
- Flexible wrap layout for small screens
- Camera/gallery input with `capture="environment"` when the browser supports it
- Scrollable queue preview

## Grouping rules

| Field | Meaning |
|-------|---------|
| `batch_id` | Shared by all files selected in one Import All action |
| `group_id` | Logical report group (related screenshots / pages) |
| `sequence_number` | Order within the group |
| `page_number` | Detected or assigned page index |
| `group_title` | Human-readable group label |

**Defaults**

- One selection ⇒ one `batch_id`
- Auto-grouping uses document type + filename stem (page/seq tokens stripped)
- Page-like names (`page1`, `pg_2`, `_3`) sort into sequence
- Uncertain / unrelated files share the batch but keep **separate** `group_id`s
- Source files are **never merged destructively** — each original remains retrievable

## Safety limits (configurable)

Defined in `backend/health_vault/batch_config.py`:

| Limit | Default |
|-------|---------|
| Max files per batch | 25 |
| Max size per file | 20 MB |
| Max total batch size | 150 MB |
| Allowed types | `.pdf` `.png` `.jpg` `.jpeg` `.json` |

UI and API both surface clear messages when a limit is exceeded.

## Duplicate handling

- Per-document SHA-256 (+ existing soft metadata rules) via ImportPipeline
- Duplicates are skipped (reference original), other files continue
- Re-uploading the same batch ⇒ **zero** new documents

## Partial success

If some files import and others fail:

- Successful files remain stored
- Batch report sets `partial_success: true`
- Failed items can be retried without re-importing successes

## Privacy

Do **not** commit:

- Uploaded images / PDFs
- Live batch payloads
- `vault_storage/index.json` or document blobs
- Private backfill JSON under `private_imports/`

Tests use fictional fixtures only.

## API

### `GET /api/health-vault/batch-limits`

Returns the configured limits object.

### `POST /api/import-health-records/batch`

Multipart form:

- `files`: one or more files
- `payload_json` (optional):

```json
{
  "batch_id": "optional-client-id",
  "auto_group": true,
  "items": [
    {
      "filename": "lab.json",
      "mime_type": "application/json",
      "document": "{\"note\":\"fictional\"}",
      "extracted_measurements": [{"metric": "glucose", "value": 105}]
    }
  ]
}
```

Example response:

```json
{
  "ok": true,
  "batch_id": "…",
  "total": 6,
  "imported": 5,
  "duplicates": 1,
  "failed": 0,
  "requires_review": 0,
  "partial_success": false,
  "results": [
    {
      "filename": "sleep_page1.png",
      "status": "imported",
      "document_id": "…",
      "sha256": "…",
      "group_id": "…",
      "sequence_number": 1
    }
  ]
}
```

Filenames are sanitized (basename only). Absolute storage paths are never returned.

### CLI-oriented service

```python
from backend.health_vault.batch_import import BatchImportService

report = BatchImportService().import_batch(items, auto_group=True)
```

## Related docs

- [HC201_HEALTH_VAULT.md](HC201_HEALTH_VAULT.md)
- [HC201F_HEALTH_RECORD_BACKFILL.md](HC201F_HEALTH_RECORD_BACKFILL.md)
