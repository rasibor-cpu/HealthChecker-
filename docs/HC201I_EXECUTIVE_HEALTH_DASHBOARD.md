# HC-201I — Executive Health Dashboard and Daily Briefing Engine

**Repository:** `C:\rasib\source\HealthChecker-`  
**Branch:** `main`  
**Baseline:** `240f738480cdec4ff482d56457051a11fd9d4cc0`  
**Date:** 2026-07-19  

---

## Purpose

Provide a mobile-first **Executive Health Dashboard** that summarizes the user’s latest
verified health position, meaningful trends, recent imports, unresolved data-quality
issues, and practical monitoring priorities.

**Observational decision-support only.** Not a diagnosis. Not a prescription.
Does not replace professional medical assessment.

---

## Briefing architecture

`ExecutiveHealthBriefingEngine` (`backend/health_vault/executive_briefing.py`) composes:

- documents / measurements (`VaultStore`)
- trends (`TrendEngine`)
- medications / profile
- import / batch audits
- clinical rule flags (`ClinicalRulesEngine`) — observational flags only
- configuration (`config/executive_dashboard.json`)

Output schema `hc.executive_briefing.v1` is UI-independent for Doctor Visit, print,
notifications, and future HC-202 clients.

---

## Domain cards

Heart, Kidney, Diabetes/Glucose, Blood Pressure, Sleep/Recovery, Weight, Respiratory,
Medications, Laboratory Reports.

Each card surfaces latest values, dates, short trend labels, confidence, provenance,
record counts, and review status using observational labels only
(Stable / Improving / Worsening / Needs attention / Insufficient data / Awaiting verification).

Special cases:

- **Heart:** rhythm, average HR, symptoms, source device; wearable ECG disclaimer
- **Sleep:** single-night context vs 7-day / longer trends; late bedtime must not auto-mark chronic deterioration
- **BP:** systolic/diastolic pair
- **Kidney:** provenance / verification status distinguished

---

## Attention items and monitoring actions

Attention kinds:

- A. Data-quality
- B. Monitoring
- C. Clinically configured flags

Monitoring actions are **record-completion prompts** (upload lab PDF, confirm dose, record BP, review low-confidence imports). They never recommend changing treatment.

---

## Trend windows

Configured windows: latest, 7d, 30d, 90d, 1y, all.

Trends require enough points, compatible units, reliable dates, confidence threshold,
and exclusion of duplicates / failed imports / low-confidence review items.

---

## Provenance and confidence

Expandable details expose provenance, source system, classification confidence,
date source/confidence. Main cards stay uncluttered.

---

## Mobile design

- Executive summary near top of Dashboard landing
- Classic summary/trends collapsed below
- Sticky action bar / touch-sized buttons
- Collapsible domain section
- Back to Top retained in vault views
- Screen-reader friendly labels where applicable

---

## API

- `GET /api/health-vault/executive-briefing`
- `GET /api/health-vault/executive-briefing/print`

Query params: `patient_id`, `as_of`, `trend_window`, `category`.

Responses are path-sanitized; no raw private file contents.

---

## Privacy

Committed tests use fictional fixtures only. No live vault data, images, or PDFs.

---

## Medical disclaimer

Every briefing includes an explicit observational disclaimer.
Wearable ECG findings do not exclude all heart conditions.

---

## Known limitations

- Browser path is local-first (IndexedDB); API serves server/tests and future clients
- Some domain metric mapping in JS is heuristic vs Python engine
- Physical-device UX polish may continue in later phases
- HC-202 live updates are out of scope
