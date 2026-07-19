/**
 * HC-201 — Health Vault UI helpers (wired from index.html).
 * HC-201G: multi-file batch queue, preview, progress, retry.
 */
(function (global) {
  "use strict";

  let batchQueue = [];
  let lastBatchReport = null;

  /** Legacy single-file path — still supported. */
  async function handleFileImport(fileInput, statusEl) {
    const files = fileInput && fileInput.files ? Array.from(fileInput.files) : [];
    if (!files.length) {
      if (statusEl) statusEl.textContent = "Choose one or more files first (PDF, PNG, JPG, JSON).";
      return null;
    }
    if (files.length === 1 && !batchQueue.length) {
      if (statusEl) statusEl.textContent = "Importing…";
      try {
        const result = await global.HCImportEngine.importHealthRecord({ file: files[0] });
        if (statusEl) {
          const conf =
            result.confidence && typeof result.confidence === "object"
              ? result.confidence.overall_confidence
              : result.confidence;
          statusEl.textContent =
            `Imported ${(result.document && result.document.original_filename) || files[0].name} · ` +
            `${(result.measurements || []).length} measurements · ` +
            `confidence ${Number(conf || 0).toFixed(2)}`;
        }
        refreshVaultViews();
        return result;
      } catch (err) {
        if (statusEl) statusEl.textContent = "Import failed: " + (err && err.message ? err.message : err);
        return null;
      }
    }
    await enqueueFiles(files);
    return importAllQueued();
  }

  async function enqueueFiles(fileList) {
    const Batch = global.HCBatchImport;
    if (!Batch) return;
    const files = Array.from(fileList || []);
    for (let i = 0; i < files.length; i++) {
      const item = Batch.createQueueItem(files[i], batchQueue.length + i);
      await Batch.makeThumbnail(item);
      batchQueue.push(item);
    }
    renderBatchPreview();
  }

  function onFilesSelected(fileInput) {
    const files = fileInput && fileInput.files ? Array.from(fileInput.files) : [];
    if (!files.length) return;
    enqueueFiles(files).then(() => {
      try {
        fileInput.value = "";
      } catch (_) {}
    });
  }

  function clearQueue() {
    batchQueue.forEach((it) => {
      if (it.thumbnail_url) {
        try {
          URL.revokeObjectURL(it.thumbnail_url);
        } catch (_) {}
      }
    });
    batchQueue = [];
    lastBatchReport = null;
    renderBatchPreview();
    const statusEl = document.getElementById("vault_status");
    if (statusEl) statusEl.textContent = "Queue cleared.";
    const prog = document.getElementById("vault_batch_progress");
    if (prog) prog.innerHTML = "";
  }

  function removeFromQueue(id) {
    const idx = batchQueue.findIndex((x) => x.id === id);
    if (idx < 0) return;
    const it = batchQueue[idx];
    if (it.thumbnail_url) {
      try {
        URL.revokeObjectURL(it.thumbnail_url);
      } catch (_) {}
    }
    batchQueue.splice(idx, 1);
    renderBatchPreview();
  }

  function renderBatchPreview() {
    const el = document.getElementById("vault_batch_preview");
    const summary = document.getElementById("vault_batch_summary");
    if (!el) return;
    const Batch = global.HCBatchImport;
    if (!batchQueue.length) {
      el.innerHTML = '<div class="muted small">No files queued. Select multiple files or drop them here.</div>';
      if (summary) summary.textContent = "";
      return;
    }
    const validation = Batch ? Batch.validateQueue(batchQueue) : { ok: true, errors: [] };
    if (summary) {
      const totalBytes = batchQueue.reduce((s, x) => s + (x.size_bytes || 0), 0);
      summary.textContent =
        batchQueue.length +
        " file(s) · " +
        (Batch ? Batch.formatBytes(totalBytes) : totalBytes + " B") +
        (validation.ok ? "" : " · limits exceeded — fix before Import All");
      summary.className = validation.ok ? "small muted" : "small bad";
    }
    el.innerHTML = batchQueue
      .map((it) => {
        const thumb = it.thumbnail_url
          ? `<img class="vault-thumb" src="${it.thumbnail_url}" alt="" />`
          : `<div class="vault-thumb vault-thumb-placeholder">${escapeHtml(
              (it.filename.split(".").pop() || "?").toUpperCase()
            )}</div>`;
        return (
          `<div class="vault-queue-item" data-id="${escapeHtml(it.id)}">` +
          thumb +
          `<div class="vault-queue-meta">` +
          `<div><strong>${escapeHtml(it.filename)}</strong></div>` +
          `<div class="small muted">${escapeHtml(it.document_type)} · ${escapeHtml(
            it.mime_type
          )} · ${Batch ? Batch.formatBytes(it.size_bytes) : it.size_bytes}</div>` +
          `<div class="small">Status: <span class="vault-status-${escapeHtml(
            it.status
          )}">${escapeHtml(it.status)}</span>` +
          (it.group_title
            ? ` · ${escapeHtml(it.group_title)} (#${it.sequence_number || "?"})`
            : "") +
          `</div>` +
          (it.errors && it.errors.length
            ? `<details class="small bad"><summary>Errors</summary><pre>${escapeHtml(
                it.errors.join("\n")
              )}</pre></details>`
            : "") +
          `</div>` +
          `<button type="button" class="vault-queue-remove" data-remove="${escapeHtml(
            it.id
          )}" aria-label="Remove">✕</button>` +
          `</div>`
        );
      })
      .join("");

    if (!validation.ok) {
      el.innerHTML +=
        `<div class="bad small" style="margin-top:8px">${validation.errors
          .map((e) => escapeHtml(e.message))
          .join("<br>")}</div>`;
    }

    el.querySelectorAll("[data-remove]").forEach((btn) => {
      btn.addEventListener("click", () => removeFromQueue(btn.getAttribute("data-remove")));
    });
  }

  function renderProgress(snap) {
    const prog = document.getElementById("vault_batch_progress");
    if (!prog || !snap) return;
    const pct = snap.total ? Math.round((100 * snap.processed) / snap.total) : 0;
    prog.innerHTML =
      `<div class="vault-progress-bar"><div class="vault-progress-fill" style="width:${pct}%"></div></div>` +
      `<div class="small muted">Processed ${snap.processed}/${snap.total} · ` +
      `imported ${snap.imported} · duplicates ${snap.duplicates} · ` +
      `failed ${snap.failed} · review ${snap.requires_review || 0}</div>`;
  }

  async function importAllQueued() {
    const statusEl = document.getElementById("vault_status");
    const Batch = global.HCBatchImport;
    if (!Batch) {
      if (statusEl) statusEl.textContent = "Batch import module missing.";
      return null;
    }
    if (!batchQueue.length) {
      if (statusEl) statusEl.textContent = "Queue is empty.";
      return null;
    }
    const validation = Batch.validateQueue(batchQueue);
    if (!validation.ok) {
      if (statusEl) {
        statusEl.textContent = validation.errors.map((e) => e.message).join(" · ");
      }
      renderBatchPreview();
      return null;
    }
    if (statusEl) statusEl.textContent = "Importing batch…";
    const report = await Batch.processQueue(batchQueue, {
      onProgress: (snap) => {
        renderProgress(snap);
        renderBatchPreview();
      },
    });
    lastBatchReport = report;
    renderBatchPreview();
    renderProgress({
      total: report.total,
      processed: report.total,
      imported: report.imported,
      duplicates: report.duplicates,
      failed: report.failed,
      requires_review: report.requires_review,
    });
    if (statusEl) {
      statusEl.textContent =
        `Batch ${report.status} · imported ${report.imported} · ` +
        `duplicates ${report.duplicates} · failed ${report.failed}` +
        (report.partial_success ? " (partial success)" : "");
    }
    refreshVaultViews();
    return report;
  }

  async function retryFailedOnly() {
    const statusEl = document.getElementById("vault_status");
    const Batch = global.HCBatchImport;
    if (!Batch || !batchQueue.length) return null;
    const failed = batchQueue.filter(
      (x) => x.status === "failed" || x.status === "requires_review"
    );
    if (!failed.length) {
      if (statusEl) statusEl.textContent = "No failed files to retry.";
      return null;
    }
    failed.forEach((x) => {
      x.status = "queued";
      x.errors = [];
    });
    if (statusEl) statusEl.textContent = "Retrying failed files…";
    const report = await Batch.processQueue(batchQueue, {
      only_failed: true,
      onProgress: (snap) => {
        renderProgress(snap);
        renderBatchPreview();
      },
    });
    lastBatchReport = report;
    renderBatchPreview();
    if (statusEl) {
      statusEl.textContent =
        `Retry complete · imported ${report.imported} · failed ${report.failed}`;
    }
    refreshVaultViews();
    return report;
  }

  function bindDropZone() {
    const zone = document.getElementById("vault_drop_zone");
    if (!zone || zone.getAttribute("data-bound") === "1") return;
    zone.setAttribute("data-bound", "1");
    zone.addEventListener("dragover", (e) => {
      e.preventDefault();
      zone.classList.add("vault-drop-active");
    });
    zone.addEventListener("dragleave", () => zone.classList.remove("vault-drop-active"));
    zone.addEventListener("drop", (e) => {
      e.preventDefault();
      zone.classList.remove("vault-drop-active");
      const files = e.dataTransfer && e.dataTransfer.files ? Array.from(e.dataTransfer.files) : [];
      if (files.length) enqueueFiles(files);
    });
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
        source_system: payload.source_system || "external_ai",
        measured_at: payload.measured_at || null,
        extracted_measurements: payload.extracted_measurements || [],
        interpretation: payload.interpretation || null,
        confidence: payload.confidence,
        provenance: payload.provenance || null,
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
    bindDropZone();
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
            .map((d) => {
              const prov =
                d.provenance ||
                ((d.tags || []).find((t) => String(t).indexOf("provenance:") === 0) || "")
                  .toString()
                  .replace(/^provenance:/, "") ||
                "unspecified";
              const group =
                d.group_title || d.group_id
                  ? ` · group ${d.group_title || String(d.group_id).slice(0, 8)}` +
                    (d.sequence_number != null ? ` #${d.sequence_number}` : "")
                  : "";
              return (
                `<div class="kpi small"><strong>${d.document_type}</strong> · ${
                  d.original_filename || d.id
                }<div class="muted">${d.imported_at} · sha ${String(d.sha256 || "").slice(
                  0,
                  12
                )}… · <em>${prov}</em>${group}</div></div>`
              );
            })
            .join("")
        : '<div class="muted">Vault is empty.</div>';
    }
  }

  function openDoctorVisit() {
    const el = document.getElementById("doctor_visit_report");
    if (el && global.HCDoctorVisit) global.HCDoctorVisit.renderPrintable(el);
  }

  function escapeHtml(v) {
    return String(v == null ? "" : v)
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;");
  }

  global.HCVaultUI = {
    handleFileImport,
    handleAiJsonImport,
    saveVaultProfile,
    refreshVaultViews,
    openDoctorVisit,
    onFilesSelected,
    clearQueue,
    importAllQueued,
    retryFailedOnly,
    enqueueFiles,
    getBatchQueue: () => batchQueue.slice(),
    getLastBatchReport: () => lastBatchReport,
  };
})(typeof window !== "undefined" ? window : globalThis);
