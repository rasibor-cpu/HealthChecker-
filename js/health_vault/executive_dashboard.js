/**
 * HC-201I — Executive Health Dashboard (mobile-first, local-first).
 * Observational only — not a diagnosis or prescription.
 */
(function (global) {
  "use strict";

  const STORAGE_KEY = "hc_auth_session";
  let lastBriefingSource = "none";
  let lastServerBriefing = null;
  let refreshGeneration = 0;

  const DISCLAIMER =
    "Observational decision-support only. Not a diagnosis or prescription. Does not replace professional medical assessment.";

  const DOMAIN_DEFS = [
    { id: "heart", title: "Heart / Cardiology", categories: ["ecg_cardiology"], metrics: ["average_hr", "heart_rate", "resting_hr", "hrv_rmssd"] },
    { id: "kidney", title: "Kidney", categories: ["kidney_renal"], metrics: ["egfr", "creatinine", "uacr"] },
    { id: "diabetes", title: "Diabetes / Glucose", categories: ["glucose_diabetes"], metrics: ["glucose", "hba1c"] },
    { id: "blood_pressure", title: "Blood Pressure", categories: ["blood_pressure"], metrics: ["systolic_bp", "diastolic_bp"] },
    { id: "sleep", title: "Sleep / Recovery", categories: ["sleep"], metrics: ["sleep_duration", "sleep_score", "hrv_rmssd", "energy_score"] },
    { id: "weight", title: "Weight / Body Metrics", categories: ["weight_body_metrics"], metrics: ["weight", "bmi"] },
    { id: "respiratory", title: "Respiratory / Oxygen", categories: ["respiratory_oxygen"], metrics: ["respiratory_rate", "spo2"] },
    { id: "medications", title: "Medications", categories: ["medication"], metrics: [] },
    { id: "labs", title: "Laboratory Reports", categories: ["laboratory_report", "hospital_clinical_report"], metrics: [] },
  ];

  function esc(s) {
    return String(s == null ? "" : s)
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;");
  }

  function goTab(name) {
    const tab = document.querySelector(`.tab[data="${name}"]`);
    if (tab) tab.click();
  }

  function canonicalAuthHeaders() {
    try {
      if (global.HCConsumerDashboard && typeof global.HCConsumerDashboard.getAuthorizationHeaders === "function") {
        const headers = global.HCConsumerDashboard.getAuthorizationHeaders();
        if (headers && headers.Authorization) return headers;
      }
    } catch (e) {
      /* fall through to sessionStorage */
    }
    try {
      const raw = global.sessionStorage.getItem(STORAGE_KEY);
      if (!raw) return null;
      const parsed = JSON.parse(raw);
      return parsed.token ? { Authorization: "Bearer " + parsed.token } : null;
    } catch (e) {
      return null;
    }
  }

  function isAuthenticated() {
    if (global.HCConsumerDashboard && global.HCConsumerDashboard.token) return true;
    return !!canonicalAuthHeaders();
  }

  async function fetchServerBriefing() {
    const headers = canonicalAuthHeaders();
    if (!headers) return null;
    const res = await fetch("/api/health-vault/executive-briefing", { headers: headers });
    if (!res.ok) {
      const err = new Error("executive_briefing_http_" + res.status);
      err.status = res.status;
      throw err;
    }
    return res.json();
  }

  function listDocs() {
    return global.HCHealthVault ? HCHealthVault.listDocuments() : [];
  }

  function listMeasurements() {
    return global.HCHealthVault ? HCHealthVault.listMeasurements() : [];
  }

  function buildLocalBriefing() {
    const docs = listDocs();
    const measurements = listMeasurements();
    const profile = global.HCHealthVault ? HCHealthVault.getProfile() : { medications: [] };
    const trends = global.HCTrendEngine && HCTrendEngine.getSnapshot ? HCTrendEngine.getSnapshot() : {};

    const domain_summaries = {};
    DOMAIN_DEFS.forEach((def) => {
      const cats = new Set(def.categories);
      const domainDocs = docs
        .filter(
          (d) =>
            cats.has(d.primary_category) ||
            (d.secondary_categories || []).some((c) => cats.has(c))
        )
        .sort((a, b) => String(b.measured_at || b.imported_at || "").localeCompare(String(a.measured_at || a.imported_at || "")));
      const latest = domainDocs[0] || null;
      const latest_values = {};
      (def.metrics || []).forEach((metric) => {
        const related = measurements
          .filter((m) => String(m.metric || "").toLowerCase().includes(metric.replace("_bp", "").split("_")[0]) || String(m.metric) === metric)
          .filter((m) => !latest || m.document_id === latest.id || !latest);
        related.sort((a, b) => String(b.measured_at || "").localeCompare(String(a.measured_at || "")));
        if (related[0]) {
          latest_values[metric] = {
            value: related[0].value,
            units: related[0].units,
            measured_at: related[0].measured_at,
          };
        }
      });
      let bp_display = null;
      if (def.id === "blood_pressure" && latest_values.systolic_bp && latest_values.diastolic_bp) {
        bp_display = `${latest_values.systolic_bp.value}/${latest_values.diastolic_bp.value} ${latest_values.systolic_bp.units || "mmHg"}`;
      }
      let heart_detail = null;
      if (def.id === "heart" && latest) {
        heart_detail = {
          rhythm: (latest.interpretation || "").toLowerCase().includes("sinus") ? "Sinus rhythm" : latest.interpretation || null,
          average_heart_rate: (latest_values.average_hr || latest_values.heart_rate || {}).value,
          symptoms: "none reported",
          source_device: latest.source_system,
          wearable_note: "Wearable ECG findings are observational and do not exclude all heart conditions.",
          ecg_date: latest.measured_at || latest.report_date,
        };
      }
      let sleep_context = null;
      if (def.id === "sleep" && latest) {
        sleep_context = {
          single_night_result: true,
          contextual_note:
            "Single-night / short-sleep results must not automatically be treated as chronic deterioration (e.g. late bedtime).",
        };
      }
      const trendEntry = trends[def.metrics[0]] || {};
      domain_summaries[def.id] = {
        id: def.id,
        title: def.title,
        status_label: !latest && !Object.keys(latest_values).length
          ? "Insufficient data"
          : latest && latest.requires_review
            ? "Awaiting verification"
            : trendEntry.label || "Stable",
        latest_date: latest ? latest.measured_at || latest.report_date || latest.imported_at : null,
        latest_values,
        bp_display,
        trend_direction: trendEntry.label || "Insufficient data",
        data_confidence: latest ? latest.classification_confidence || latest.parser_confidence : null,
        provenance: latest ? latest.provenance : null,
        source_system: latest ? latest.source_system : null,
        recent_record_count: domainDocs.length,
        requires_review: !!(latest && latest.requires_review),
        heart_detail,
        sleep_context,
        expandable_provenance: latest
          ? {
              document_id: latest.id,
              filename: latest.original_filename,
              date_source: latest.date_source,
              date_confidence: latest.date_confidence,
            }
          : null,
      };
    });

    const review_count = docs.filter((d) => d.requires_review).length;
    const attention_items = [];
    docs.forEach((d) => {
      if (d.requires_review) {
        attention_items.push({
          kind: "data_quality",
          code: "requires_review",
          message: `Record requires review (${d.primary_category || "uncategorized"}).`,
        });
      }
      if (d.provenance === "historical_summary" || d.provenance === "user_reported") {
        attention_items.push({
          kind: "data_quality",
          code: "source_document_required",
          message: `Original source document still useful for ${d.original_filename || d.document_type}.`,
        });
      }
    });

    const meds = (profile.medications || []).map((m) => (typeof m === "string" ? m : m.name || String(m)));
    const uncertain = meds.filter((m) => /uncertain|unknown|\?|tbd/i.test(m));

    return {
      generated_at: new Date().toISOString(),
      data_status: docs.length ? (review_count ? "Needs record updates" : "Partially current") : "Limited data",
      latest_health_record_date: docs[0] ? docs[0].measured_at || docs[0].imported_at : null,
      new_records_imported_recently: docs.filter((d) => {
        const t = Date.parse(d.imported_at || "");
        return t && Date.now() - t < 7 * 86400000;
      }).length,
      records_requiring_review: review_count,
      domain_summaries,
      attention_items: attention_items.slice(0, 20),
      monitoring_actions: [
        ...(attention_items.some((a) => a.code === "source_document_required")
          ? [{ prompt: "Upload the original laboratory report", code: "upload_original_lab" }]
          : []),
        ...(uncertain.length ? [{ prompt: "Confirm medication dose", code: "confirm_med_dose" }] : []),
        { prompt: "Review low-confidence imported records", code: "review_low_confidence" },
      ],
      recent_imports: [],
      medications_summary: {
        current_medications: meds.filter((m) => !/uncertain|unknown|\?|tbd/i.test(m)).map((n) => ({ name: n })),
        uncertain_medication_statuses: uncertain.map((n) => ({ name: n, status: "uncertain" })),
        note: "Does not infer drug interactions or recommend medication changes.",
      },
      disclaimer: DISCLAIMER,
      observational_only: true,
    };
  }

  function renderDomainCard(domain) {
    if (!domain) return "";
    const vals = domain.latest_values || {};
    const lines = Object.keys(vals)
      .slice(0, 4)
      .map((k) => {
        const v = vals[k];
        return `<li><strong>${esc(k)}</strong>: ${esc(v.value)}${v.units ? " " + esc(v.units) : ""} <span class="muted">(${esc(v.measured_at || "—")})</span></li>`;
      })
      .join("");
    const heart = domain.heart_detail
      ? `<div class="small">Rhythm: ${esc(domain.heart_detail.rhythm || "—")} · Avg HR: ${esc(domain.heart_detail.average_heart_rate || "—")} bpm · Symptoms: ${esc(domain.heart_detail.symptoms || "—")}<br/><span class="muted">${esc(domain.heart_detail.wearable_note || "")}</span></div>`
      : "";
    const sleep = domain.sleep_context
      ? `<div class="small muted">${esc(domain.sleep_context.contextual_note || "")}</div>`
      : "";
    const bp = domain.bp_display ? `<div class="kpi"><strong>Latest BP:</strong> ${esc(domain.bp_display)}</div>` : "";
    const prov = domain.expandable_provenance
      ? `<details class="small"><summary>Provenance / confidence</summary>
          <div>Provenance: ${esc(domain.provenance || "—")}</div>
          <div>Source: ${esc(domain.source_system || "—")}</div>
          <div>Confidence: ${esc(domain.data_confidence ?? "—")}</div>
          <div>Date source: ${esc(domain.expandable_provenance.date_source || "—")} (${esc(domain.expandable_provenance.date_confidence ?? "—")})</div>
        </details>`
      : "";
    return `<div class="card exec-domain-card" data-domain="${esc(domain.id)}">
      <h4 class="section-title">${esc(domain.title)} <span class="badge">${esc(domain.status_label)}</span></h4>
      <div class="small muted">Latest: ${esc(domain.latest_date || "—")} · Trend: ${esc(domain.trend_direction || "—")} · Clinical records: ${esc(domain.clinical_record_count != null ? domain.clinical_record_count : domain.recent_record_count || 0)} · Observational samples: ${esc(domain.observational_sample_count || 0)}</div>
      ${domain.observational_note ? `<div class="small muted">${esc(domain.observational_note)}</div>` : ""}
      ${bp}
      ${heart}
      ${sleep}
      ${lines ? `<ul class="small">${lines}</ul>` : `<div class="muted small">Insufficient data for this domain.</div>`}
      ${prov}
    </div>`;
  }

  function renderLoading() {
    const root = document.getElementById("exec_health_dashboard");
    if (!root) return;
    lastBriefingSource = "loading";
    root.innerHTML = `
      <div class="card exec-summary-card">
        <h3 class="section-title">Executive Health Dashboard</h3>
        <p class="small muted">Loading authenticated executive briefing…</p>
      </div>`;
    root.hidden = false;
    root.removeAttribute("aria-hidden");
  }

  function renderFetchError(message) {
    const root = document.getElementById("exec_health_dashboard");
    if (!root) return;
    lastBriefingSource = "server_error";
    root.innerHTML = `
      <div class="card exec-summary-card">
        <h3 class="section-title">Executive Health Dashboard</h3>
        <p class="small warn">${esc(message || "Unable to load authenticated executive briefing.")}</p>
        <p class="small muted">Classic dashboard data remains available above. The empty local vault is not used while you are signed in.</p>
        <button type="button" class="secondary" onclick="HCExecutiveDashboard.refresh()">Retry executive briefing</button>
      </div>`;
    root.hidden = false;
    root.removeAttribute("aria-hidden");
  }

  function executiveKpis(briefing) {
    const vaultSummary = (briefing && briefing.vault_summary) || {};
    return {
      patient_id: briefing && briefing.patient_id,
      data_status: briefing && briefing.data_status,
      total_records: vaultSummary.total_records,
      total_measurements: vaultSummary.total_measurements,
      health_connect_observation_count: vaultSummary.health_connect_observation_count,
      latest_health_record_date: briefing && briefing.latest_health_record_date,
    };
  }

  function render(briefing, meta) {
    const root = document.getElementById("exec_health_dashboard");
    if (!root) return;
    const source = (meta && meta.source) || "unspecified";
    if (!briefing) {
      if (isAuthenticated()) {
        renderFetchError("Executive briefing payload missing while authenticated. Local vault data is not substituted.");
        return;
      }
      briefing = buildLocalBriefing();
      lastBriefingSource = "local";
    } else {
      lastBriefingSource = source;
    }
    if (lastBriefingSource === "server") lastServerBriefing = briefing;
    const b = briefing;
    const domains = b.domain_summaries || {};
    const attention = (b.attention_items || [])
      .map((a) => `<li><strong>[${esc(a.kind)}]</strong> ${esc(a.message)}</li>`)
      .join("") || "<li class='muted'>No attention items.</li>";
    const actions = (b.monitoring_actions || [])
      .map((a) => `<li>${esc(a.prompt || a)}</li>`)
      .join("") || "<li class='muted'>No monitoring prompts.</li>";
    const imports = (b.recent_imports || [])
      .map(
        (r) =>
          `<li>${esc(r.batch_date || "—")}: selected ${esc(r.selected)} · imported ${esc(r.imported)} · dup ${esc(r.duplicates)} · fail ${esc(r.failed)}</li>`
      )
      .join("") || "<li class='muted'>No batch imports recorded in this session.</li>";
    const meds = b.medications_summary || {};
    const medList = (meds.current_medications || [])
      .map((m) => `<li>${esc(m.name || m)}</li>`)
      .join("");
    const uncertain = (meds.uncertain_medication_statuses || [])
      .map((m) => `<li class="warn">${esc(m.name || m)}</li>`)
      .join("");

    const vaultSummary = b.vault_summary || {};
    const semantics = b.attention_semantics || {};
    root.innerHTML = `
      <div class="card exec-summary-card" id="exec_summary_top">
        <h3 class="section-title">Executive Health Dashboard</h3>
        <p class="small muted">${esc(b.disclaimer || DISCLAIMER)}</p>
        <div class="kpi"><strong>Data status:</strong> ${esc(b.data_status)}</div>
        <div class="kpi"><strong>Patient:</strong> ${esc((b.display_name && b.display_name !== b.patient_id) ? b.display_name : "My Health Dashboard")}</div>
        <div class="kpi"><strong>Last updated:</strong> ${esc(b.generated_at || "—")}</div>
        <div class="kpi"><strong>Latest health-record date:</strong> ${esc(b.latest_health_record_date || "—")}</div>
        <div class="kpi"><strong>Vault records:</strong> ${esc(vaultSummary.total_records ?? "—")}</div>
        <div class="kpi"><strong>Vault measurements:</strong> ${esc(vaultSummary.total_measurements ?? "—")}</div>
        <div class="kpi"><strong>Health Connect observations:</strong> ${esc(vaultSummary.health_connect_observation_count ?? "—")}</div>
        <div class="kpi"><strong>New records (7d):</strong> ${esc(b.new_records_imported_recently ?? 0)}</div>
        <div class="kpi"><strong>Requiring review:</strong> ${esc(b.records_requiring_review ?? 0)}</div>
        <div class="exec-nav-bar vault-sticky-actions" role="navigation" aria-label="Executive shortcuts">
          <button type="button" class="secondary" onclick="HCExecutiveDashboard.go('vault')">Health Vault</button>
          <button type="button" class="secondary" onclick="HCExecutiveDashboard.go('vault')">Timeline</button>
          <button type="button" class="secondary" onclick="HCExecutiveDashboard.go('vault')">Trends</button>
          <button type="button" class="secondary" onclick="HCVaultUI.openDoctorVisit()">Doctor Visit</button>
          <button type="button" class="secondary" onclick="HCExecutiveDashboard.goRecent()">Recently Imported</button>
          <button type="button" onclick="HCExecutiveDashboard.go('vault')">Upload Records</button>
          <button type="button" class="secondary" onclick="HCExecutiveDashboard.print()">Print Summary</button>
        </div>
      </div>

      <div class="card">
        <h3 class="section-title">Items Requiring Attention</h3>
        <p class="small muted">${esc(semantics.executive_attention_items || "Record-quality and missing-clinical-data prompts for executive review.")}</p>
        <p class="small muted">Classic dashboard Attention Items count missing-data Key Observations separately from Guardian alerts.</p>
        <ul class="small">${attention}</ul>
      </div>

      <div class="card">
        <h3 class="section-title">Recommended Monitoring Actions</h3>
        <ul class="small">${actions}</ul>
        <p class="small muted">These are record-completion prompts, not medical prescriptions.</p>
      </div>

      <div class="card">
        <h3 class="section-title">Recent Imports</h3>
        <ul class="small">${imports}</ul>
        <div class="vault-sticky-actions">
          <button type="button" class="secondary" onclick="HCExecutiveDashboard.go('vault')">View Records</button>
          <button type="button" class="secondary" onclick="HCExecutiveDashboard.go('vault')">View Timeline</button>
          <button type="button" class="secondary" onclick="HCExecutiveDashboard.go('vault')">Upload More</button>
        </div>
      </div>

      <div class="card">
        <h3 class="section-title">Medications</h3>
        <ul class="small">${medList || "<li class='muted'>No confirmed medications listed.</li>"}</ul>
        ${uncertain ? `<div class="small"><strong>Uncertain:</strong><ul>${uncertain}</ul></div>` : ""}
        <p class="small muted">${esc(meds.note || "")}</p>
        <button type="button" class="secondary" onclick="HCExecutiveDashboard.go('vault')">Open medication timeline / vault</button>
      </div>

      <details open class="card">
        <summary class="section-title">Health Domain Summaries</summary>
        ${DOMAIN_DEFS.map((d) => renderDomainCard(domains[d.id])).join("")}
      </details>

      <div class="card" id="exec_print_area" hidden></div>
    `;
  }

  function printSummary() {
    const b = lastServerBriefing || (isAuthenticated() ? null : buildLocalBriefing());
    const area = document.getElementById("exec_print_area");
    if (!area) return;
    if (!b) {
      area.hidden = false;
      area.innerHTML = `<h2>Executive Health Summary</h2><p>Authenticated briefing is not available to print yet.</p>`;
      global.print();
      return;
    }
    area.hidden = false;
    area.innerHTML = `<h2>Executive Health Summary</h2>
      <p>Generated: ${esc(b.generated_at)}</p>
      <p>Data status: ${esc(b.data_status)}</p>
      <pre class="small">${esc(JSON.stringify({ domains: b.domain_summaries, attention: b.attention_items, actions: b.monitoring_actions }, null, 2))}</pre>
      <p class="small">${esc(b.disclaimer)}</p>`;
    global.print();
  }

  async function refresh() {
    const generation = ++refreshGeneration;
    const authenticated = isAuthenticated();
    const root = document.getElementById("exec_health_dashboard");
    if (authenticated) {
      renderLoading();
    }
    let briefing = null;
    let fetchError = null;
    try {
      briefing = await fetchServerBriefing();
    } catch (e) {
      fetchError = e;
      briefing = null;
    }
    if (generation !== refreshGeneration) return;
    if (briefing) {
      render(briefing, { source: "server" });
      if (root) {
        root.hidden = false;
        root.removeAttribute("aria-hidden");
      }
      if (global.HCHealthSnapshot && HCHealthSnapshot.refresh) {
        try { HCHealthSnapshot.refresh(); } catch (e) { /* ignore */ }
      }
      return;
    }
    if (authenticated) {
      renderFetchError(
        fetchError && fetchError.status
          ? "Executive briefing request failed (HTTP " + fetchError.status + "). Local vault data is not substituted while authenticated."
          : "Executive briefing unavailable for the authenticated session."
      );
      return;
    }
    render(buildLocalBriefing(), { source: "local" });
    if (root) {
      root.hidden = true;
      root.setAttribute("aria-hidden", "true");
    }
  }

  document.addEventListener("hc:session-changed", function (event) {
    const detail = (event && event.detail) || {};
    if (detail.authenticated) {
      refresh();
      return;
    }
    lastBriefingSource = "none";
    const root = document.getElementById("exec_health_dashboard");
    if (root) {
      root.innerHTML = "";
      root.hidden = true;
      root.setAttribute("aria-hidden", "true");
    }
  });

  global.HCExecutiveDashboard = {
    refresh,
    render,
    buildLocalBriefing,
    print: printSummary,
    go: goTab,
    getLastBriefingSource: function () {
      return lastBriefingSource;
    },
    executiveKpis,
    canonicalAuthHeaders,
    goRecent: function () {
      goTab("vault");
      const el = document.getElementById("vault_recent");
      if (el && el.scrollIntoView) el.scrollIntoView({ behavior: "smooth", block: "start" });
    },
  };
})(window);
