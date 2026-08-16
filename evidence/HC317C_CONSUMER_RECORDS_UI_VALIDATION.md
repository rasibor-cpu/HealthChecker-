# HC-317C Consumer Records UI Validation

Date: 2026-08-16

Branch: `hc311-encrypted-vault-at-rest`
Starting HEAD: `a619fffaa957820988f5526d6c8a8d6dcb1b3e0f` (`HC-317B: implement health records backend`)

## Implementation summary

- Added Health Records navigation and an authenticated records console to the existing HC-316 dashboard.
- Added record summary cards, search, category/status filters, refresh and empty/loading/error states.
- Added an authenticated upload experience using `POST /api/records/upload`, including selected-file, progress, success, duplicate/review/quarantine and failure presentation.
- Added record details using `GET /api/records/{document_id}` with metadata, measurements, timeline, trends, AI observations, provenance/evidence and authoritative lifecycle events.
- Added authenticated original-document download through the records API boundary.
- Updated the HC-316 import widget to show authoritative record counts/recent records and open the records console.
- Preserved HC-316 preferences and light/dark theme behavior, including responsive records layouts.
- Versioned the PWA shell cache and added the dashboard/records scripts so deployed clients receive HC-317C consistently. API responses, clinical payloads and secrets remain excluded from the service-worker cache.

## Architecture and API validation

- The UI calls only HC-317B HTTP APIs; it has no direct `VaultStore`, encryption-key or legacy browser-vault dependency.
- Authentication is inherited from the existing HC-316 session. Every records request carries the bearer token; uploads do not submit or permit a client-selected `patient_id`.
- Upload remains routed through HC-317B and therefore `BatchImportService.import_batch()` / HC-312 intake.
- Records and linkage content are rendered only from patient-scoped backend responses. Dynamic values are HTML-escaped.
- Original bytes are retrieved only through the authenticated download endpoint and a short-lived object URL that is revoked after use.

## Automated tests

Focused HC-316/HC-317 validation:

```text
python -m pytest -q tests/test_hc316b_dashboard_backend.py tests/test_hc316c_consumer_dashboard.py tests/test_hc317b_records_backend.py tests/test_hc317c_consumer_records_ui.py
21 passed, 1 warning in 1.91s
```

HC-317C coverage includes static integration, API contract compatibility, encrypted upload/download, unauthenticated and forged-token rejection, cross-patient isolation, PHI non-leakage, filtering and dashboard widget compatibility.

Full repository regression:

```text
python -m pytest -q
1164 passed, 3 skipped, 2 warnings, 5 subtests passed in 408.84s
```

The two previously reported compatibility failures were reconciled without changing their tests or assertions. Details are recorded in `evidence/HC317C_REGRESSION_RECONCILIATION.md`. The final full regression gate is green.

## Browser acceptance evidence

Validated in the local application using a synthetic patient and synthetic HC-313B PDF:

1. Authenticated successfully and retained HC-316 dashboard widgets.
2. Opened Health Records from dashboard navigation.
3. Confirmed records loading, empty state, search/category/status controls and theme-compatible presentation.
4. Selected and securely uploaded a synthetic PDF.
5. Received an imported result presented as `Requires Review` without losing the backend lifecycle state.
6. Confirmed the new card displayed filename, date, category, status, source and linked-intelligence availability.
7. Opened record details and confirmed metadata, empty-metric/trend/observation states, timeline, manual-upload provenance, batch/group identifiers and authoritative processing history.

The browser capture contained synthetic data only. No real PHI, authentication token or encryption material was exposed.

## Security checks

- Unauthenticated records requests: rejected (`401`).
- Forged bearer tokens: rejected (`401`).
- Cross-patient list/detail/download access: isolated; foreign details return `404`.
- Upload identity override: no patient identity field exists in the UI request.
- Linkage leakage: observations and measurements belonging to another patient are absent.
- At-rest storage: downloaded plaintext matches the source while the authoritative vault blob differs from plaintext.
- PWA caching: `/api/`, vault storage, JSON clinical responses and token/secret-like URLs remain excluded.

## Working-tree scope

HC-313/HC-314 acquisition logs, state, synthetic completed PDFs, reports and scratch artifacts are unrelated runtime material and are excluded from HC-317C staging. The earlier HC-317 integration-review evidence is also outside this implementation commit.
