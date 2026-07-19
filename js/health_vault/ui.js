/**
 * HC-201 — Health Vault UI helpers (wired from index.html).
 */
(function (global) {
  "use strict";

  async function handleFileImport(fileInput, statusEl) {
    const file = fileInput && fileInput.files && fileInput.files[0];
    if (!file) {
      if (statusEl) statusEl.textContent = "Choose a file first (PDF, PNG, JPG, JSON).";
      return null;
    }
    if (statusEl) statusEl.textContent = "Importing…";
    try {
      const result = await global.HCImportEngine.importHealthRecord({ file });
      if (statusEl) {
        statusEl.textContent =
          `Imported ${result.document.original_filename} · ` +
          `${(result.measurements || []).length} measurements · ` +
          `confidence ${(result.confidence || 0).toFixed(2)} · ` +
          `parser ${result.parser ? result.parser.name : "none"}`;
      }
      refreshVaultViews();
      return result;
    } catch (err) {
      if (statusEl) statusEl.textContent = "Import failed: " + (err && err.message ? err.message : err);
      return null;
    }
  }

  async function handleAiJsonImport(textarea, statusEl) {
    let payload;
    try {
      payload = JSON.parse((textarea && textarea.value) || "{}");
    } catch (err) {
      if (statusEl) statusEl.textContent = "Invalid JSON";
      return null;
    }
    if (statusEl) statusEl.textContent = "Importing AI payload…";
    try {
      const result = await global.HCImportEngine.importHealthRecord({
        document: payload.document || JSON.stringify(payload),
        filename: payload.filename || "ai-import.json",
        mime_type: "application/json",
        document_type: payload.document_type || "ai_assisted_import",
        acquisition_method: "external_ai",
        extracted_measurements: payload.extracted_measurements || [],
        interpretation: payload.interpretation || null,
        confidence: payload.confidence,
        tags: ["external_ai"].concat(payload.tags || []),
      });
      if (statusEl) {
        statusEl.textContent =
          `AI import stored · doc ${result.document.id} · ` +
          `${(result.measurements || []).length} measurements`;
      }
      refreshVaultViews();
      return result;
    } catch (err) {
      if (statusEl) statusEl.textContent = "AI import failed: " + (err && err.message ? err.message : err);
      return null;
    }
  }

  function saveVaultProfile() {
    const dx = (document.getElementById("vault_diagnoses") || {}).value || "";
    const meds = (document.getElementById("vault_medications") || {}).value || "";
    global.HCHealthVault.updateProfile({
      diagnoses: dx
        .split("\n")
        .map((x) => x.trim())
        .filter(Boolean),
      medications: meds
        .split("\n")
        .map((x) => x.trim())
        .filter(Boolean),
    });
    refreshVaultViews();
  }

  function refreshVaultViews() {
    const timelineEl = document.getElementById("vault_timeline");
    if (timelineEl && global.HCHealthTimeline) {
      global.HCHealthTimeline.renderInto(timelineEl);
    }
    const trendEl = document.getElementById("vault_trends");
    if (trendEl && global.HCTrendEngine) {
      const trends = global.HCTrendEngine.recompute();
      const keys = Object.keys(trends);
      trendEl.innerHTML = keys.length
        ? keys
            .map((k) => {
              const t = trends[k];
              const cls =
                t.direction === "improving"
                  ? "ok"
                  : t.direction === "worsening"
                    ? "bad"
                    : "muted";
              return `<div class="kpi"><strong>${k}:</strong> <span class="${cls}">${t.label}</span> <span class="small muted">(n=${t.sample_count})</span></div>`;
            })
            .join("")
        : '<div class="muted">No vault trends yet.</div>';
    }
    const integrityEl = document.getElementById("vault_integrity");
    if (integrityEl && global.HCHealthVault) {
      const v = global.HCHealthVault.verifyIntegrity();
      integrityEl.innerHTML = v.ok
        ? `<span class="ok">Storage integrity OK · ${v.document_count} documents</span>`
        : `<span class="bad">Integrity issues: ${v.issues.join(", ")}</span>`;
    }
    const docsEl = document.getElementById("vault_docs");
    if (docsEl && global.HCHealthVault) {
      const docs = global.HCHealthVault.listDocuments().slice().reverse();
      docsEl.innerHTML = docs.length
        ? docs
            .map(
              (d) =>
                `<div class="kpi small"><strong>${d.document_type}</strong> · ${
                  d.original_filename || d.id
                }<div class="muted">${d.imported_at} · sha ${String(d.sha256 || "").slice(
                  0,
                  12
                )}…</div></div>`
            )
            .join("")
        : '<div class="muted">Vault is empty.</div>';
    }
  }

  function openDoctorVisit() {
    const el = document.getElementById("doctor_visit_report");
    if (el && global.HCDoctorVisit) global.HCDoctorVisit.renderPrintable(el);
  }

  global.HCVaultUI = {
    handleFileImport,
    handleAiJsonImport,
    saveVaultProfile,
    refreshVaultViews,
    openDoctorVisit,
  };
})(typeof window !== "undefined" ? window : globalThis);
