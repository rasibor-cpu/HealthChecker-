# HC-317C Consumer Records UI Plan

## Planning identity and scope

- Repository: `C:\rasib\source\HealthChecker-HC310E`
- Branch: `hc311-encrypted-vault-at-rest`
- Planning baseline: `a619fff HC-317B: implement health records backend`
- Architecture inputs: HC-316 Consumer Dashboard, HC-317A records
  architecture, HC-317B records backend, and the HC-317 Integration Review Gate.
- This document is planning evidence only. No HTML, JavaScript, CSS, backend, or
  test implementation is included in this phase.

## Existing frontend architecture

HC-316 is a single-page, no-framework frontend composed from:

- `index.html`: login screen, top tab navigation, `.screen` containers, dashboard
  markup, Health Vault markup, and the tab-switching controller.
- `js/health_vault/dashboard.js`: `HCConsumerDashboard`, login/logout, local
  session restoration, bearer-token requests, preferences, theme application,
  and dashboard widget rendering.
- `style.css` plus inline variables in `index.html`: reusable cards, badges,
  forms, responsive grids, modal/drop-zone/filter styles, and light-theme
  overrides.
- `js/health_vault/ui.js`: the legacy/browser-local Health Vault UI.

Navigation uses `.tab[data="screen-id"]` and matching `.screen#screen-id`
elements. It does not use URL routing. Screen-specific refresh hooks run inside
the shared tab click handler.

The HC-316 session controller stores `{patientId, token}` under
`hc_auth_session`, sends `Authorization: Bearer <token>`, logs out on HTTP 401,
and applies `light-theme` or `dark-theme` to `document.body`.

### Important boundary

The existing Health Vault frontend uses browser-local vault/import modules. It
must not become the HC-317C records data source. HC-317C may reuse its visual and
accessibility patterns, but records must be listed, uploaded, read, and
downloaded exclusively through the authenticated HC-317B API.

## Implementation preconditions from the integration gate

These are not HC-317C presentation tasks, but must be resolved before production
acceptance:

1. Activate HC-311 protected-key loading in the normal API/intake runtime and
   fail closed when the key is unavailable.
2. Preserve HC-313 Gmail message/attachment provenance through the HC-312
   handoff.
3. Fix HC-316 patient scoping for dashboard measurement counts and preferences.
4. Require authentication on the legacy clinical/PHI API surface.
5. Replace the development login/password and in-memory session implementation
   with the intended production identity/session system.

HC-317C can be developed against the current API, but its acceptance evidence
must not claim these backend/security gaps are solved by the UI.

## 1. User experience

### Records navigation entry point

- Add a top-level **Records** tab to `#tabs_navbar`, targeting
  `#health_records_screen`.
- Change the HC-316 `import_wizard` action from "Go to Health Vault Upload" to
  "Open Health Records", selecting the new Records tab.
- Keep the existing Health Vault tab for legacy features during migration, but
  label and behavior must clearly distinguish it from authenticated Records.
- When the Records tab activates, call `HCRecordsUI.refreshRecords()` only after
  confirming an active HC-316 session.
- Do not add a parallel router. Use the established `.tab`/`.screen` pattern.

### Record list view

The screen header contains:

- title and short privacy statement;
- **Add records** primary action;
- last-refreshed text and explicit refresh action;
- record count based on the returned filtered list.

The list uses responsive record cards rather than a wide table. Each card shows:

- original filename;
- measured date, falling back to imported date with an explicit label;
- category label;
- lifecycle status badge;
- source system when available;
- extracted metric count;
- **View details** action.

Cards must be keyboard operable and use semantic buttons/headings. The whole
card should not be an ambiguous click target.

Empty states are distinct:

- no records exist: explain how to add the first report;
- filters/search have no matches: offer **Clear filters**;
- load failed: preserve controls and provide **Retry**.

### Filters

- Category chips map exactly to HC-317B category values:
  `blood_pressure`, `sleep`, `ecg_cardiology`, `glucose_diabetes`,
  `kidney_renal`, `laboratory_report`, `weight_body_metrics`, `medication`, and
  `other`.
- Status chips map to `incoming`, `processing`, `imported`, `requires_review`,
  `quarantined`, `duplicate`, and `failed`.
- Selecting a category or status triggers a new authenticated
  `GET /api/records?category=...&status=...` request.
- Include an **All** option for each filter and keep selected state visible in
  both themes.
- Filter changes cancel or supersede stale requests to prevent out-of-order
  results.

### Search

HC-317B has no search query parameter. The first HC-317C version therefore uses
client-side search over the currently loaded, patient-scoped result set:

- search `original_filename`, `primary_category`, `source_system`, and status;
- normalize case and whitespace;
- debounce input to avoid unnecessary rendering;
- never place patient ID, tokens, filenames, or other PHI in a URL beyond the
  documented category/status filter values.

If record volume requires pagination or server search, add a separately reviewed
backend contract (`q`, cursor, limit) rather than silently loading the full vault.

### Upload/import workflow

- Open an upload panel/modal from **Add records**.
- Use one accessible file input and drop zone accepting the backend-supported
  PDF, JSON, PNG, and JPEG types.
- Show filename, size, detected type, and validation status before upload.
- Never parse or store clinical file contents in browser localStorage.
- Submit `FormData` under the `file` field to `POST /api/records/upload` with the
  bearer token. Do not include `patient_id`; the server binds identity.
- HC-317B currently processes one file per request. For initial delivery, either
  restrict selection to one file or upload a selected queue sequentially with a
  separate result per file. Do not pretend the endpoint is a multi-file atomic
  batch.
- Show deterministic phases: queued, uploading, processing, imported,
  requires review, duplicate, rejected/quarantined, or failed.
- On success, announce the result with an ARIA live region, close/reset the
  chooser only after the user acknowledges it, refresh records, and offer
  **View record** when `document_id` is present.
- Preserve server `validation`, `errors`, and `warnings` in user-friendly text.
  Do not expose raw exception strings or internal paths.
- Prevent double submission and cancel/ignore stale UI updates after logout.

### Record detail view

Use a modal on desktop and a full-height dialog/sheet on narrow screens. It must
have focus trapping, Escape/close behavior, return focus to the invoking card,
and an accessible title.

Sections:

1. **Record summary** — filename, category, status, measured/imported dates,
   size, source, and interpretation/classification metadata.
2. **Extracted health metrics** — metric, value, units, abnormal/reference flag,
   measured date, and method when available.
3. **Linked intelligence** — trend impacts and AI observations.
4. **Timeline** — record-linked timeline events in chronological order.
5. **Provenance and evidence** — source system, acquisition method, Gmail fields
   when present, content hash shown only if product policy approves, batch/group
   identifiers, and evidence references.
6. **Processing history** — authoritative lifecycle events from `lifecycle`.
7. **Download original** — explicit authenticated download action.

Do not label missing provenance as "manual" or invent lifecycle steps. Display
"Not available" and explain that the source metadata was not supplied.

### Linked intelligence display

- Trends show metric name, direction/label, latest value, and sample count from
  each `trend_references[].trend` object.
- AI observations show fact, interpretation, explanation, confidence, safety
  disclaimer, and only evidence associated with the open record.
- Avoid diagnostic language. Retain HC-315 safety text verbatim as structured UI
  content, not hidden tooltips.
- Evidence links may highlight the matching metric/timeline item in the dialog;
  they must not navigate to a document owned by another patient.
- Empty intelligence states distinguish "not generated", "insufficient data",
  and "no evidence linked to this record" where the response allows it.

## 2. Components required

### `HCRecordsUI` controller

Proposed file: `js/health_vault/records.js`.

Responsibilities:

- own records-screen state, active filters, search term, selected record, upload
  state, loading/error state, and request cancellation;
- obtain authentication through `HCConsumerDashboard`, not by maintaining a
  second session store;
- call only HC-317B endpoints;
- render escaped text and bind events without inline user-derived HTML handlers;
- clear all record/detail/upload state on logout.

### Records dashboard panel

- screen header, count, refresh, add action, search, and filter toolbar;
- loading skeleton, empty state, retry state, and list container;
- optional dashboard summary strip using `import_wizard.payload.records_count`
  and `recent_records`, with `/api/records` remaining authoritative.

### Upload component

- drop zone/file input;
- queued-file preview and client validation;
- progress/status row;
- validation/warning/error summary;
- success action linking to detail.

Reuse `.vault-drop-zone`, modal, queue, and action-layout styling only after
renaming or extracting neutral component classes. Do not call `HCVaultUI` or the
browser-local import engine.

### Record cards

- semantic article/list item;
- filename and metadata summary;
- category/status badges;
- metric count and source;
- view action.

### Status indicators

Use one mapping function and stable CSS classes:

- processing/incoming: neutral-progress;
- imported: success;
- requires_review: warning;
- quarantined/failed: error;
- duplicate: informational.

Never communicate status by color alone. Include text and an optional icon with
hidden/accessible labeling.

### Detail modal/page

- dialog shell and focus management;
- summary, metrics, intelligence, provenance, timeline, lifecycle, and download
  subcomponents;
- local loading/error state independent of the list;
- safe cleanup of downloaded object URLs and pending requests.

### Timeline/provenance display

- compact chronological timeline using returned dates/event types;
- provenance definition list;
- evidence-reference chips that connect observation/trend claims to source
  measurements without exposing raw vault paths.

## 3. API integration

### Endpoints

| Purpose | Request | Contract used |
|---|---|---|
| Login | `POST /api/auth/login` | Existing HC-316 token/session flow |
| Dashboard | `GET /api/dashboard/summary` | Import widget record count/recent summaries |
| List/filter | `GET /api/records` | `{"records": [summary...]}` |
| Detail | `GET /api/records/{document_id}` | Metadata, provenance, measurements, timeline, trends, observations, evidence, lifecycle |
| Upload | `POST /api/records/upload` | Multipart `file`; structured status/validation/document ID |
| Download | `GET /api/records/download/{document_id}` | Authenticated binary response |

### Authentication handling

- Extend `HCConsumerDashboard` with a small shared method such as
  `getAuthorizationHeaders()` and a session-change/logout notification.
- `HCRecordsUI` must not read or write a second token copy.
- Every records request includes the bearer token.
- Any HTTP 401 invokes the existing logout flow, clears PHI from the DOM and
  in-memory component state, aborts requests, and returns focus to login.
- HTTP 403, if later introduced, uses a generic access-denied message.
- HTTP 404 detail/download displays "Record not found" without suggesting that
  another patient's record exists.

The current localStorage token behavior is inherited from HC-316 and is a known
production-security risk. HC-317C must not expand token exposure through logs,
URLs, data attributes, markup, or error messages.

### Loading states

- list: skeleton/card placeholders with `aria-busy`;
- detail: dialog-level spinner/skeleton while preserving close controls;
- upload: determinate state when browser upload progress is available, otherwise
  clear indeterminate text;
- download: disabled action plus "Preparing secure download" status;
- dashboard-to-records navigation: show the screen immediately, then load.

### Error states

- network/offline: retain prior data as visibly stale and offer retry;
- 400 upload: map structured validation errors to the selected file;
- 401: logout and clear sensitive state;
- 404: close or replace detail with not-found state;
- 413 if later supplied: file-too-large guidance;
- 500: generic failure with retry; never render server internals;
- malformed/unexpected JSON: generic contract error captured by telemetry without
  PHI payloads.

### Download boundary

Use authenticated `fetch`, verify `response.ok`, convert to a Blob, derive the
filename from a sanitized `Content-Disposition` value, create a short-lived
object URL, trigger download, and immediately revoke the URL. Do not navigate
directly to the download URL because the bearer header would be absent. Do not
cache the Blob or decrypted bytes in IndexedDB/localStorage.

## 4. Security requirements

- Patient identity is never accepted from UI fields, query strings, filenames,
  or file content for records operations.
- No browser code may instantiate or read backend `VaultStore`, filesystem paths,
  encryption keys, or vault URIs.
- Do not use the browser-local Health Vault as a records cache or secondary
  repository.
- Clear records, selected detail, upload previews, Blob URLs, and search text on
  logout/session expiration.
- Escape all API-derived strings before inserting them into HTML. Prefer DOM
  text nodes for filenames, observations, provenance, errors, and metadata.
- Never log tokens, patient IDs, filenames, medical values, provenance IDs,
  response bodies, or upload content.
- Avoid PHI in URLs; only opaque `document_id` appears in the detail/download
  path, and browser history routing is not introduced.
- Add `Cache-Control: no-store`/related response-header requirements to backend
  acceptance for detail and download; the UI cannot guarantee this alone.
- Downloads remain decrypted only in browser memory long enough to complete the
  user-requested save.
- All cross-patient behavior must remain indistinguishable from not-found or
  unauthorized outcomes.

## 5. Integration design

### HC-315 observations and trends

- Render only record-detail `ai_observations`, `trend_references`, and
  `evidence_references`; do not fetch unauthenticated legacy intelligence routes.
- Preserve observation disclaimers and evidence identifiers.
- Link evidence visually within the current detail response only.

### HC-316 dashboard

- Reuse HC-316 authentication, logout, theme, card, badge, responsive, and widget
  patterns.
- Update the `import_entry` widget to navigate to Records and show
  `records_count` plus up to five `recent_records` links.
- After upload, refresh both Records and `HCConsumerDashboard` so counts, trends,
  timeline, and observations update together.
- Add a Records refresh hook to the existing tab controller.
- Ensure the new screen inherits both `light-theme` and `dark-theme`; add explicit
  light-theme overrides for record cards, chips, dialog, drop zone, skeletons,
  and error/success panels.

### HC-317B backend

- Treat summary and detail fields as separate contracts; do not assume detail
  arrays exist on list summaries.
- Respect exact category/status enum values.
- Handle `requires_review`, duplicate, rejected/quarantined, and failed outcomes
  without inventing a saved record when `document_id` is absent.
- Client search remains bounded to the server-returned patient list.
- Do not add a parallel browser persistence layer.

### Proposed implementation files

Expected later HC-317C implementation scope:

- `index.html` — Records tab, screen, upload panel, list target, live regions,
  and detail dialog shell.
- `js/health_vault/records.js` — controller and render/API behavior.
- `js/health_vault/dashboard.js` — shared authenticated request/session hooks and
  import-widget navigation/recent record actions.
- `style.css` — Records component, responsive, theme, focus, and reduced-motion
  styling.
- frontend/contract/acceptance tests and HC-317C validation evidence.

No backend model or storage change is planned unless contract tests expose a
specific blocker. Integration-gate backend fixes should be isolated from the UI
commit when practical.

## 6. Testing strategy

### Frontend unit tests

Add executable JavaScript/DOM tests rather than relying only on source-marker
assertions. Cover:

- summary/detail rendering with complete and missing optional fields;
- category/status labels and CSS class mapping;
- client search normalization and filter query construction;
- HTML escaping/XSS payloads in filenames, source metadata, observations, and
  server errors;
- loading, empty, stale, retry, not-found, and validation states;
- upload queue state transitions and prevention of duplicate submission;
- logout clearing DOM state, requests, file input, and object URLs;
- focus trap, Escape close, focus restoration, keyboard navigation, and ARIA live
  announcements;
- light/dark theme classes and high-contrast status text;
- authenticated download Blob URL creation and revocation.

### API contract tests

- assert list envelope and every summary field used by cards;
- assert category/status query behavior;
- assert detail fields used by every section;
- assert upload success, requires-review, duplicate, validation rejection,
  missing document ID, and filename sanitization outcomes;
- assert download headers/content and 401/404/500 behavior;
- assert all requests reject missing/forged tokens and cross-patient access;
- snapshot or schema-test dashboard `import_wizard` and records responses to
  detect contract drift.

### User-journey acceptance tests

Use a real browser against a temporary encrypted VaultStore:

1. Log in and land on Dashboard.
2. Open Records from the navigation and dashboard widget.
3. Observe the empty state.
4. Upload a supported clinical record.
5. Observe upload progress and the authoritative lifecycle outcome.
6. Find the record by category/status and client search.
7. Open details and verify extracted metrics, timeline, trends, observations,
   safety disclaimer, provenance, evidence, and lifecycle.
8. Download the original through the authenticated boundary and verify content.
9. Return to Dashboard and verify record count, recent record, trend, and
   observation refresh.
10. Log out and prove record PHI is removed from the DOM and back navigation does
    not restore it.

Repeat with two patients to prove no list, count, preferences, detail, evidence,
search result, or cached DOM leakage.

Additional acceptance scenarios:

- requires-review, duplicate, unsupported type, oversized/quarantined, parser
  warning, empty metrics, absent provenance, and network interruption;
- narrow/mobile layout, touch target size, screen reader naming, keyboard-only
  operation, reduced motion, and both themes;
- session expiry during list, upload, detail, and download;
- HC-313 Gmail-acquired record after provenance handoff is fixed;
- production key unavailable/invalid startup after fail-closed wiring is fixed.

### Regression matrix

Run HC-311 through HC-317C tests, plus focused browser acceptance. HC-317C is not
complete if source-marker tests pass but the browser journey or patient-isolation
acceptance fails.

## Delivery sequence

1. Resolve/track Priority-0 integration gate blockers and freeze response
   contracts used by HC-317C.
2. Add shared session/auth request hooks to HC-316 without duplicating token
   ownership.
3. Add Records navigation and accessible screen skeleton.
4. Implement list, filters, client search, loading/error/empty states.
5. Implement single-file/sequential upload workflow through HC-317B.
6. Implement detail, intelligence, provenance, lifecycle, and secure download.
7. Integrate dashboard widget navigation and post-upload refresh.
8. Complete responsive/light-dark/accessibility styling.
9. Add unit, contract, security, two-patient, and real-browser acceptance tests.
10. Run the HC-311–317C regression matrix and create HC-317C validation evidence.

## Plan completion gate

Implementation may begin only after this plan is reviewed and the team agrees
which integration-gate blockers must be fixed before development versus before
production acceptance. HC-317C must extend the existing HC-316 screen/session/
theme architecture and use HC-317B as its only records data boundary.

RESULT=HC317C_PLAN_COMPLETE
