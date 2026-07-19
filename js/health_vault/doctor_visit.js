/**
 * HC-201 — Doctor Visit Mode (printable professional summary).
 */
(function (global) {
  "use strict";

  function latestMeasurement(metric) {
    const items = global.HCHealthVault.listMeasurements({ metric }).slice();
    items.sort((a, b) =>
      String(b.measured_at || "").localeCompare(String(a.measured_at || ""))
    );
    return items[0] || null;
  }

  function trendLine(metric) {
    const t = (global.HCTrendEngine.getSnapshot() || {})[metric];
    if (!t) return "n/a";
    return t.label + (t.latest != null ? ` (latest ${t.latest})` : "");
  }

  function generateReport(options) {
    const opts = options || {};
    const Vault = global.HCHealthVault;
    const Timeline = global.HCHealthTimeline;
    const profile = Vault.getProfile();
    const docs = Vault.listDocuments();
    const timeline = Timeline.build();

    // Optionally merge legacy HC_V6 symptoms as soft context
    let legacy = null;
    try {
      if (typeof data === "function") legacy = data();
    } catch (_) {}

    const ecg = docs
      .filter((d) => d.document_type === "samsung_health_ecg" || (d.tags || []).includes("ecg"))
      .slice(-3)
      .reverse();

    const report = {
      title: "HealthChecker+ Doctor Visit Report",
      generated_at: new Date().toISOString(),
      patient_id: opts.patient_id || "default-patient",
      fhir_bundle_hint: [
        "Patient",
        "Medication",
        "Observation",
        "DiagnosticReport",
        "DocumentReference",
        "Encounter",
      ],
      current_diagnoses: profile.diagnoses || [],
      current_medications: profile.medications || [],
      recent_ecg: ecg.map((d) => ({
        id: d.id,
        filename: d.original_filename,
        imported_at: d.imported_at,
        storage_uri: d.storage_uri,
      })),
      kidney_trend: trendLine("egfr"),
      blood_pressure_trend: `${trendLine("systolic_bp")} / ${trendLine("diastolic_bp")}`,
      sleep_trend: trendLine("sleep_score"),
      diabetes_trend: `${trendLine("glucose")} · HbA1c ${trendLine("hba1c")}`,
      latest: {
        glucose: latestMeasurement("glucose"),
        egfr: latestMeasurement("egfr"),
        systolic: latestMeasurement("systolic_bp") || latestMeasurement("systolic"),
        diastolic: latestMeasurement("diastolic_bp") || latestMeasurement("diastolic"),
        sleep_score: latestMeasurement("sleep_score"),
      },
      imported_reports: docs.map((d) => ({
        id: d.id,
        type: d.document_type,
        filename: d.original_filename,
        imported_at: d.imported_at,
        confidence: d.parser_confidence,
        sha256: d.sha256,
        provenance:
          d.provenance ||
          ((d.tags || []).find((t) => String(t).indexOf("provenance:") === 0) || "")
            .toString()
            .replace(/^provenance:/, "") ||
          "unspecified",
      })),
      health_timeline: timeline.slice(0, 25),
      medical_disclaimer:
        "Observational decision-support only. Not a diagnosis. User-reported and historical-summary values are not laboratory-document verified.",
      legacy_summary: legacy
        ? {
            readings: (legacy.logs || []).length,
            symptoms: legacy.sym || [],
            foot_pain_analyses: (legacy.footPainLogs || []).length,
          }
        : null,
    };
    return report;
  }

  function renderPrintable(targetEl) {
    const report = generateReport();
    if (!targetEl) return report;

    const dx =
      report.current_diagnoses.length > 0
        ? report.current_diagnoses.map((x) => `<li>${esc(x)}</li>`).join("")
        : "<li class='muted'>None recorded in vault profile</li>";
    const meds =
      report.current_medications.length > 0
        ? report.current_medications.map((x) => `<li>${esc(x)}</li>`).join("")
        : "<li class='muted'>None recorded in vault profile</li>";
    const imports = report.imported_reports
      .slice()
      .reverse()
      .slice(0, 15)
      .map(
        (d) =>
          `<li>${esc(d.type)} — ${esc(d.filename || d.id)} <span class="small muted">${esc(
            d.provenance
          )} · ${esc(d.imported_at)}</span></li>`
      )
      .join("");

    const timeline = report.health_timeline
      .map(
        (e) =>
          `<li><strong>${esc(new Date(e.date).toLocaleDateString())}</strong> ${esc(
            e.document.document_type
          )} — ${esc(e.trend_impact)}</li>`
      )
      .join("");

    targetEl.innerHTML = `
      <div class="doctor-visit-report">
        <h3 class="section-title">${esc(report.title)}</h3>
        <div class="small muted">Generated ${esc(new Date(report.generated_at).toLocaleString())}</div>
        <div class="small muted">${esc(report.medical_disclaimer)}</div>
        <div class="hr"></div>
        <div class="kpi"><strong>Current diagnoses</strong><ul>${dx}</ul></div>
        <div class="kpi"><strong>Current medications</strong><ul>${meds}</ul></div>
        <div class="kpi"><strong>Recent ECG</strong>
          ${
            report.recent_ecg.length
              ? report.recent_ecg
                  .map((e) => `<div class="small">${esc(e.filename || e.id)} (${esc(e.imported_at)})</div>`)
                  .join("")
              : '<div class="muted small">No ECG imports yet</div>'
          }
        </div>
        <div class="kpi"><strong>Kidney trend:</strong> ${esc(report.kidney_trend)}</div>
        <div class="kpi"><strong>Blood pressure trend:</strong> ${esc(report.blood_pressure_trend)}</div>
        <div class="kpi"><strong>Sleep trend:</strong> ${esc(report.sleep_trend)}</div>
        <div class="kpi"><strong>Diabetes trend:</strong> ${esc(report.diabetes_trend)}</div>
        <div class="kpi"><strong>Imported reports</strong><ul>${imports || "<li class='muted'>None</li>"}</ul></div>
        <div class="kpi"><strong>Health timeline</strong><ul>${timeline || "<li class='muted'>Empty</li>"}</ul></div>
        <button type="button" onclick="window.print()">Print Doctor Visit Report</button>
      </div>
    `;
    return report;
  }

  function esc(v) {
    return String(v == null ? "" : v)
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;");
  }

  global.HCDoctorVisit = {
    generateReport,
    renderPrintable,
  };
})(typeof window !== "undefined" ? window : globalThis);
