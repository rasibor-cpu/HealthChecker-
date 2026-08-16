# HC-316A: Consumer Dashboard Architecture Design

This document details the architectural design for the consumer-facing HealthChecker dashboard. The dashboard serves as the default post-authentication landing page, consuming outputs from the Health Intelligence Engine and allowing user personalization while maintaining strict privacy boundaries.

---

## 1. Component Architecture

The frontend dashboard is designed as a modular, widget-based interface using clean CSS flexbox/grid layouts to support drag-and-drop customization and fluid responsiveness:

```
+------------------------------------------------------------------------+
|                          DashboardContainer                            |
|  +------------------------------------------------------------------+  |
|  |                            DashboardHeader                       |  |
|  +------------------------------------------------------------------+  |
|  +-------------------------------------+ +--------------------------+  |
|  |           Draggable Widget A        | |    Draggable Widget B    |  |
|  |         (Health Status Summary)     | |    (Key Observations)    |  |
|  +-------------------------------------+ +--------------------------+  |
|  +-------------------------------------+ +--------------------------+  |
|  |           Draggable Widget C        | |    Draggable Widget D    |  |
|  |            (Trends Widget)          | |    (Timeline Widget)     |  |
|  +-------------------------------------+ +--------------------------+  |
+------------------------------------------------------------------------+
```

### Core Frontend Components:
- **`DashboardContainer`**: Orchestrates state management, reads/writes layout settings, and handles dark/light theme classes.
- **`WidgetRegistry`**: A registry that maps component definitions to user preference keys:
  - `HealthStatusSummaryWidget`: Displays quick health badges, baseline counts, and warnings.
  - `KeyObservationsWidget`: Renders `HealthObservation` objects complete with calculation basis and safety disclaimers.
  - `TrendsWidget`: Visualizes `HealthMetric` trends (glucose variability, eGFR slopes, BP levels).
  - `TimelineWidget`: Integrates the unified patient history timeline.
  - `ImportWizardWidget`: Point-of-entry for medical record uploads and intake actions.
- **`DashboardCustomizer`**: Side-panel control enabling users to toggle widget visibility, reorder sections, and configure target metrics (e.g. prioritizing glycemic or cardiovascular widgets).

---

## 2. Data Flow & API Boundaries

To maintain vault security and multi-tenant boundaries, the browser frontend **never** accesses vault storage directly. All data access occurs through a session-gated controller API layer:

```
[Browser Client] 
     │
     ▼ (Gated API Requests / Cookie Auth)
[Backend Controller (Flask / app.py)]
     │
     ├─► [Session Context Validation] ──► Extracts patient_id
     │
     ▼ (Decryption & Query Operations)
[VaultStore (Encrypted Index)]
```

### API Routes:
1. **`GET /api/dashboard/preferences`**
   - Retrieves active theme (`light` | `dark`), widget layout order, visibility toggles, and metrics priority settings.
2. **`POST /api/dashboard/preferences`**
   - Saves updated personalization choices directly into the patient's encrypted profile metadata.
3. **`GET /api/dashboard/data`**
   - Combines and returns decrypted timeline events, measurements, trends, and clinical observations.
   - Path filters: Strict parameter constraints enforce `patient_id` validation matching the authenticated session.

---

## 3. UI Information Hierarchy

To deliver a premium, function-driven user experience, the dashboard content is organized dynamically by importance:

1. **Safety Banner**: Standard clinical disclaimers are persistently fixed above dashboard content.
2. **Alerts & Gap Indicators**: High-priority missing-data warnings or critical observations appear at the top.
3. **User-Prioritized Widgets**: Sections reordered according to personal layout configurations (e.g., placing blood glucose trends at the top for diabetic tracking).
4. **Longitudinal History & Documents**: The chronologically sorted timeline and list of uploaded records form the footer layer.

---

## 4. Personalization & Preference Model

Personalization choices are stored as a JSON object inside the patient's encrypted vault profile to prevent preference loss across devices:

```json
{
  "dashboard_preferences": {
    "theme": "dark",
    "widget_order": [
      "status_summary",
      "key_observations",
      "trends_widget",
      "import_wizard",
      "timeline_widget"
    ],
    "visible_widgets": [
      "status_summary",
      "key_observations",
      "trends_widget",
      "import_wizard",
      "timeline_widget"
    ],
    "priority_metric": "glucose"
  }
}
```

---

## 5. Security & Privacy Considerations

- **Frontend Cryptographic Zero-Knowledge**: No encryption/decryption keys exist in the frontend browser context. All cryptographic operations occur on the secure backend, transmitting only the specific JSON payloads representing the current user's session data.
- **Strict Session Isolation**: Every API controller route checks cookie/token session identifiers, mapping them to the tenant's specific `patient_id`. Requests attempting to supply custom `patient_id` parameters outside session variables will fail closed.
- **Traceability Preservation**: The dashboard frontend preserves evidence links. When showing observations, users can click to inspect the exact source documents or measurements.

---

## 6. Implementation Phases

- **Phase 1: API Setup & Preferences Store**
  - Implement `/api/dashboard/preferences` endpoints.
  - Setup default layout models and extend profile schemas.
- **Phase 2: Modular Frontend Shell**
  - Implement the `DashboardContainer` with dark/light theme switching.
  - Create the layout manager and drag-and-drop reordering logic.
- **Phase 3: Widget Integration**
  - Connect the widgets to show observations, timelines, and metrics data from the backend.
  - Add traceability link interactions.
