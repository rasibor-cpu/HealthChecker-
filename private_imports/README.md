# Private Imports (local only)

This directory holds **machine-local** clinical backfill JSON used by:

```bash
python -m backend.health_vault.backfill --input private_imports/<your_file>.json
```

## Rules

- Real patient health data **must never** be committed or pushed.
- Committed repository content is limited to this README, `.gitkeep`, and the fictional template under `docs/examples/`.
- Keep ECG PDFs, screenshots, vault databases, and IndexedDB exports outside Git.

## Schema

See `docs/HC201F_HEALTH_RECORD_BACKFILL.md` and `docs/examples/health_backfill_template.json`.
