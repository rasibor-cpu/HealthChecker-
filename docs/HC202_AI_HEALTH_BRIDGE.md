# HC-202 — AI Health Bridge (ChatGPT Connector V1)

**Repository:** `C:\rasib\source\HealthChecker-`  
**Branch:** `main`  
**Baseline:** HC-201I (`5672e5b`)  
**Date:** 2026-07-19  

---

## Purpose

Provide the first **AI Health Bridge** for HealthChecker+, enabling structured import of
AI-extracted health records while preserving the canonical **ImportPipeline** path.

**Observational decision support only.** Not a diagnosis. Not a prescription.

---

## Architecture

```
AI Provider payload (ChatGPT V1)
  → AIConnector.normalize_payload()   # no vault writes
  → AIHealthBridge.preview()          # preview ticket + summary
  → User confirmation (required)
  → AIHealthBridge.confirm()
  → ImportPipeline.run() per record
       Validation → Classification → Duplicate Detection →
       Store → Timeline → Trends → Doctor Visit → Audit
  → Executive Dashboard / UI refresh
```

### Provider abstraction

| Module | Role |
|--------|------|
| `backend/ai_health/connectors/base.py` | `AIConnector`, registry, `resolve_connector` |
| `backend/ai_health/connectors/chatgpt.py` | **ChatGPT Connector V1** (implemented) |
| `backend/ai_health/connectors/stubs.py` | Gemini, Claude, Local LLM, Medical OCR (reserved) |
| `backend/ai_health/bridge.py` | `AIHealthBridge` preview / confirm / history |

Connectors register automatically on import. Future providers add a connector module and register.

### Canonical pipeline discipline

Connectors **never** write to `VaultStore` directly. Confirmed imports always call
`ImportPipeline.run()` — the same path as manual upload and batch import.

---

## ChatGPT Connector V1

Accepts structured payloads:

- `provider_id`: `"chatgpt"`
- `conversation`: metadata only (`conversation_id`, `message_timestamp`, `model`, `parser_version`)
- `records[]`: per-record metadata, `extracted_measurements`, interpretation, provenance, confidence, attachments

**Conversation text is not stored** unless `store_conversation_text: true` is explicitly set on the conversation object.

---

## Confirmation flow

### Preview

Shows:

- Record count
- Categories
- Date range
- Duplicate estimate
- Per-record measurement preview, confidence, review flags

Message pattern:

> ChatGPT has prepared N health records.  
> Import into HealthChecker+?

Buttons: **Cancel** | **Import**

### Result

Shows imported, duplicates, failed, grouped reports, trends/dashboard/doctor-visit refresh status.

---

## Document linkage

Each imported record carries `linkage` metadata:

- `ai_record_id`, `conversation_id`, `attachment_ids`
- `document_id` (post-import)
- flags: `timeline`, `trends`, `executive_dashboard`, `doctor_visit`

---

## Security

- Explicit user confirmation required for every AI import batch
- No automatic / background imports
- No silent writes
- API preview is read-only with respect to health records

---

## API

| Method | Path | Description |
|--------|------|-------------|
| `POST` | `/api/ai-health/import-preview` | Normalize + summarize; creates preview ticket |
| `POST` | `/api/ai-health/import-confirm` | Requires `confirmed: true` or `preview_id` from preview |
| `GET` | `/api/ai-health/import-history` | AI import audit history (metadata only) |

Responses are path-sanitized. No raw private file contents.

---

## UI

| File | Role |
|------|------|
| `js/health_vault/ai_health_bridge.js` | Local-first preview + confirm orchestration |
| `js/health_vault/import_confirm.js` | `openAiConfirm`, `showAiResult` modals |
| `js/health_vault/ui.js` | `handleAiJsonImport` wired to bridge |
| `index.html` | Health Vault AI Bridge section |

After successful import: timeline, trends, executive dashboard, and doctor visit views refresh.

---

## Audit

`VaultStore.record_ai_import_audit()` stores:

- AI provider, import time, user confirmation
- Imported / duplicate / failed counts
- Warnings, average confidence
- Conversation metadata (no chat body by default)

---

## Privacy

- Fictional fixtures in committed tests only
- No live vault data, PDFs, or images in git

---

## Medical disclaimer

Every bridge response includes an explicit observational disclaimer.

---

## Known limitations

- Browser path is local-first (IndexedDB); API serves server/tests and future mobile clients
- Only ChatGPT V1 is fully implemented; other connectors are stubs
- Attachments require bytes or base64 in the confirm payload; linkage IDs are metadata-only when bytes absent

---

## Roadmap (not in HC-202)

- Gemini / Claude / Local LLM connectors
- Live ChatGPT API integration (connector currently accepts prepared payloads)
- HC-202 live dashboard push / notifications
- Medical OCR connector
