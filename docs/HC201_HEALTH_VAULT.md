# HC-201 / HC-201A — Enterprise Health Vault & Intelligent Medical Record Ingestion

**Product:** HealthChecker+  
**Branch:** `feature/hc201-health-vault`  
**Mode:** Additive extension (no rewrite of existing dashboards)

---

## Architecture

Health Vault is a longitudinal electronic health record layer that sits **beside** existing HC_V6 storage.

```
UI / API
   │
   ▼
ImportService ──► ImportPipeline (HC-201C autonomous path)
   │
   ├── EventBus
   ├── OCRProvider (swappable)
   ├── ParserRegistry
   ├── ClinicalRulesEngine (config JSON)
   ├── ValidationEngine
   ├── ConfidenceEngine
   ├── VaultStore (append-only + digital signature metadata)
   ├── Timeline / Trends / Doctor Visit
   └── HealthIntelligence (observational only)
```

| Layer | Browser (PWA) | Server (optional) |
|-------|---------------|-------------------|
| Models | `js/measurement_model.js`, `js/health_vault/medical_document.js` | `backend/health_vault/models.py` |
| Store | `localStorage` + IndexedDB | `vault_storage/` |
| Parsers | `js/health_vault/parsers/builtin_parsers.js` | `backend/health_vault/parsers/` |
| Import | `js/health_vault/import_engine.js` + `batch_import.js` | `backend/health_vault/import_pipeline.py` via `ImportService` / `BatchImportService` |
| API | client-ready payload shape | `POST /api/import-health-record`, `POST /api/import-health-records/batch` |

**HC-201G:** multi-file upload, batch preview, grouping, and limits — see [HC201G_BATCH_UPLOAD.md](HC201G_BATCH_UPLOAD.md).

**HC-201H:** confirm-before-import, fixed progress/result, category taxonomy, measured-date priority, chronological sorting, and trend normalization — see [HC201H_CONFIRMED_CATEGORIZED_INGESTION.md](HC201H_CONFIRMED_CATEGORIZED_INGESTION.md).

See also: [HC201_RC1_READINESS_REPORT.md](HC201_RC1_READINESS_REPORT.md)

Existing features continue to use `HC_V6`. Vault imports may **optionally** append flattened glucose/BP/eGFR points into `HC_V6.logs` for Trend Intelligence continuity (`source: "health_vault"`).

---

## Storage model

- **Never overwrite** a document id.
- Duplicate content (same SHA-256) still creates a **new** document record + import/audit entry, tagged `duplicate_content` with `duplicate_of`.
- Persisted artifacts:
  - Original document bytes
  - Extracted measurements
  - Interpretation
  - Parser id/version + confidence
  - Checksum (SHA-256)
  - Import history
  - Full audit history
- Browser key: `HC_HEALTH_VAULT_V1`
- Server root: `vault_storage/`

---

## Measurement model

Universal `Measurement` entity (FHIR **Observation**-compatible):

`measurement_id`, `document_id`, `category`, `metric`, `value`, `units`, `reference_range`, `abnormal_flag`, `confidence`, `measured_at`

Categories/metrics include Cardiology/ECG/HR/HRV, Sleep, Energy Score, Kidney labs, Diabetes/CGM, Blood Pressure, Weight/BMI. New metrics register via `registerMetric` / `register_metric` without forking the pipeline.

---

## MedicalDocument model

Generic (not Samsung-specific):

`id`, `patient_id`, `document_type`, `source_system`, `acquisition_method`, `original_filename`, `storage_uri`, `sha256`, `imported_at`, `measured_at`, `parser_version`, `parser_confidence`, `status`, `tags`

Supported types include Samsung Health ECG/Sleep/Energy, Galaxy Watch, BP screenshots, glucose, Libre CGM, lab PDFs, hospital/medication/imaging reports, AI-assisted imports.

FHIR hint: `DocumentReference`.

---

## Parser registry

No single hard-coded parser path. Parsers implement `canParse` / `parse` and **register** themselves:

- SamsungHealthParser  
- GalaxyWatchParser  
- LifeLabsParser  
- LibreParser  
- BloodPressureParser  
- HospitalReportParser  
- AIAssistedParser (external ChatGPT / assistant path)

Future parsers call `HCParserRegistry.register(...)` or `ParserRegistry.register(...)`.

---

## Import engine

Reusable service architecture for:

`POST /api/import-health-record`

Accepts **PDF / PNG / JPG / JSON**, stores original + measurements + confidence + hash + parser + timestamp.

AI path (no internal logic change required later):

```json
{
  "document": "...",
  "extracted_measurements": [{"metric":"glucose","value":112}],
  "interpretation": "...",
  "confidence": 0.9
}
```

Browser: `HCImportEngine.importHealthRecord(...)`  
Python: `ImportService.import_health_record(...)` / `import_health_record_handler(...)`

---

## Timeline

`HCHealthTimeline` / `build_timeline()` produces chronological entries with:

- Date  
- Document  
- Measurements  
- Trend impact  
- Link to original (`storage_uri`)

---

## Trend engine

On every successful import, trends recompute automatically.

Directions: **Improving**, **Stable**, **Worsening** (metric-aware: e.g. rising glucose = worsening, rising eGFR = improving).

---

## Doctor Visit Mode

Printable report including:

- Current diagnoses / medications (vault profile)  
- Recent ECG imports  
- Kidney, BP, sleep, diabetes trends  
- Imported reports  
- Health timeline  

UI: Health Vault → Generate Doctor Visit Report → Print.

---

## Future AI integration

The ingestion pipeline already accepts pre-extracted measurements + interpretation + confidence. An external assistant can POST the JSON shape above to `/api/import-health-record/json` without changing vault internals.

---

## Future FHIR readiness

Terminology prepared (not a full FHIR server):

| Vault concept | FHIR hint |
|---------------|-----------|
| Patient profile | Patient |
| Measurement | Observation |
| MedicalDocument | DocumentReference |
| Doctor Visit report | DiagnosticReport / Encounter |
| Medications list | Medication |

---

## Non-regression

Preserved:

- Trend Intelligence (`HC_V6` logs + existing trend UI)  
- Foot Pain Analysis  
- Dashboard / Add / Symptoms / Reports  
- Existing localStorage schema for core app  

---

## Testing

```bash
python -m pytest tests/test_hc201_health_vault.py -q
```

---

## Safety / privacy notes

- Local-first browser vault; server vault is optional.  
- No automatic sharing of clinical documents.  
- Append-only audit supports clinical governance review.
