# HC-201H — Confirmed, Chronological, Categorized Health-Record Ingestion

## Confirmation workflow

1. User selects one or more files (multi-select, camera/gallery, or drag-and-drop).
2. Files appear in the batch queue preview.
3. **Import All** opens a fixed confirmation sheet:
   - total / image / PDF / JSON counts
   - estimated categories
   - batch size
   - Cancel or Confirm Import
4. Escape / Cancel closes without importing.
5. Confirm runs the batch **once** (double-submit locked).

## Progress & result (no scroll required)

- Sticky/fixed progress panel: Processing X of Y, bar, counts, current filename
- Fixed result dialog with:
  - success / mixed / failure headline
  - imported / duplicates / failed / grouped reports / categories
  - **View Imported Records** → Recently Imported
  - **View Timeline**
  - **Close**

## Category taxonomy

Primary categories (observational only — not diagnoses):

`blood_pressure`, `sleep`, `ecg_cardiology`, `glucose_diabetes`, `kidney_renal`,
`laboratory_report`, `weight_body_metrics`, `medication`, `respiratory_oxygen`,
`activity_fitness`, `hospital_clinical_report`, `symptom_record`, `other`

Each document stores:

- `primary_category`, `secondary_categories`
- `classification_confidence`, `classification_method`, `classification_version`
- `requires_review`

## Date extraction hierarchy

1. Explicit `measured_at` / report date  
2. Parser-extracted date  
3. Source metadata  
4. EXIF capture date  
5. Filename date  
6. `imported_at` fallback (low confidence, `requires_review`)

Fields: `measured_at`, `report_date`, `imported_at`, `file_capture_date`,
`date_confidence`, `date_source`

## Chronological sorting

Default: `measured_at` → `report_date` → `imported_at` (newest first).

Grouped multi-image reports sort by measured date; pages keep `sequence_number` /
`page_number` order inside the group.

UI filters: All / Blood Pressure / Sleep / ECG / Glucose / Kidney / Labs / Weight /
Medications / Other · Newest/Oldest · Back to Top.

## Trend normalization

Canonical metrics (examples): `systolic_bp`, `diastolic_bp`, `heart_rate`,
`sleep_duration` (minutes), `hrv_rmssd`, `glucose` (mg/dL), `egfr`, `creatinine` (µmol/L).

Original value/unit preserved. Incompatible units are marked `unit_compatible=false`
and excluded from trends.

Trend engine ignores duplicates, failed imports, missing/low-confidence dates, and
incompatible units. No diagnosis inference.

## Batch audit metadata

Stored under vault `batch_audits`:

`batch_id`, `selected_count`, `confirmed_by_user`, `confirmation_timestamp`,
`imported_count`, `duplicate_count`, `failed_count`, `category_counts`,
`earliest_measured_at`, `latest_measured_at`, `completed_at`

## Mobile scrolling design

- Queue scrolls inside a max-height panel
- Sticky import action bar
- Fixed confirmation / progress / result overlays
- Recently Imported section for post-import focus
- Touch-friendly (≥44px) controls

## Privacy

No private clinical files in Git. Fictional fixtures only in tests/docs.
