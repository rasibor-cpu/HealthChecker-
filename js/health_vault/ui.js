/**
 * HC-201 — Health Vault UI helpers (wired from index.html).
 * HC-201G: multi-file batch queue, preview, progress, retry.
 */
(function (global) {
  "use strict";

  let batchQueue = [];
  let lastBatchReport = null;
  let activeCategoryFilter = "all";
  let timelineNewestFirst = true;

  /** Legacy single-file path — still supported (uses confirmation). */
  async function handleFileImport(fileInput, statusEl) {
    const Confirm = global.HCImportConfirmUI;
    if (Confirm && Confirm.isProcessingLocked()) return null;
    const files = fileInput && fileInput.files ? Array.from(fileInput.files) : [];
    if (!files.length) {
      if (statusEl) statusEl.textContent = "Choose one or more files first (PDF, PNG, JPG, JSON).";
      return null;
    }
    await enqueueFiles(files);
    try {
      fileInput.value = "";
    } catch (_) {}
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
    const Confirm = global.HCImportConfirmUI;
    if (!Batch) {
      if (statusEl) statusEl.textContent = "Batch import module missing.";
      return null;
    }
    if (Confirm && Confirm.isProcessingLocked()) return null;
    if (!batchQueue.length) {
      if (statusEl) statusEl.textContent = "Queue is empty.";
      return null;
    }
    const validation = Batch.validateQueue(batchQueue);
    if (!validation.ok) {
      if (statusEl) statusEl.textContent = validation.errors.map((e) => e.message).join(" · ");
      renderBatchPreview();
      return null;
    }

    const summary = Batch.summarizeQueue(batchQueue);
    let confirmed = true;
    if (Confirm && Confirm.openConfirm) {
      confirmed = await Confirm.openConfirm(summary);
    }
    if (!confirmed) {
      if (statusEl) statusEl.textContent = "Import cancelled.";
      return { ok: false, cancelled: true, selected: batchQueue.length };
    }

    if (Confirm) Confirm.setProcessingLock(true);
    if (statusEl) statusEl.textContent = "Importing batch…";
    const confirmationTimestamp = new Date().toISOString();
    const report = await Batch.processQueue(batchQueue, {
      onProgress: (snap) => {
        if (Confirm) Confirm.showProgress(snap);
        renderProgress(snap);
        renderBatchPreview();
      },
    });
    report.confirmed_by_user = true;
    report.confirmation_timestamp = confirmationTimestamp;
    report.selected = report.selected || batchQueue.length;
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
    if (Confirm) {
      Confirm.setProcessingLock(false);
      await Confirm.showResult(report, {
        viewRecords: () => showRecentlyImported(report),
        viewTimeline: () => {
          const tab = document.querySelector('[data="vault"]');
          if (tab) tab.click();
          const tl = document.getElementById("vault_timeline");
          if (tl) tl.scrollIntoView({ behavior: "smooth", block: "start" });
        },
      });
    }
    if (statusEl) {
      statusEl.textContent =
        `Batch ${report.status} · imported ${report.imported} · ` +
        `duplicates ${report.duplicates} · failed ${report.failed}` +
        (report.partial_success ? " (partial success)" : "");
    }
    refreshVaultViews();
    if (global.HCExecutiveDashboard && HCExecutiveDashboard.refresh) {
      try {
        HCExecutiveDashboard.refresh();
      } catch (e) {
        /* ignore */
      }
    }
    return report;
  }

  function showRecentlyImported(report) {
    const el = document.getElementById("vault_recent");
    if (!el) return;
    const ids = (report.results || [])
      .filter((r) => r.status === "imported" || r.status === "requires_review")
      .map((r) => r.document_id)
      .filter(Boolean);
    const docs = (global.HCHealthVault.listDocuments() || []).filter((d) => ids.indexOf(d.id) >= 0);
    el.innerHTML = docs.length
      ? docs
          .map(
            (d) =>
              `<div class="kpi small"><strong>${escapeHtml(
                d.primary_category || "other"
              )}</strong> · ${escapeHtml(d.original_filename || d.id)}` +
              `<div class="muted">${escapeHtml(d.measured_at || d.imported_at || "")}</div></div>`
          )
          .join("")
      : '<div class="muted small">No newly imported documents.</div>';
    el.scrollIntoView({ behavior: "smooth", block: "start" });
    const tab = document.querySelector('[data="vault"]');
    if (tab) tab.click();
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
    const Bridge = global.HCAIHealthBridge;
    const Confirm = global.HCImportConfirmUI;
    if (!Bridge || !Bridge.importWithConfirmation) {
      if (statusEl) statusEl.textContent = "AI Health Bridge module missing.";
      return null;
    }
    if (Confirm && Confirm.isProcessingLocked && Confirm.isProcessingLocked()) return null;
    if (statusEl) statusEl.textContent = "Preparing AI import preview…";
    try {
      const report = await Bridge.importWithConfirmation(payload, {
        actions: {
          viewRecords: () => showRecentlyImported(report),
          viewTimeline: () => {
            const tab = document.querySelector('[data="vault"]');
            if (tab) tab.click();
            const tl = document.getElementById("vault_timeline");
            if (tl) tl.scrollIntoView({ behavior: "smooth", block: "start" });
          },
        },
      });
      if (report && report.cancelled) {
        if (statusEl) statusEl.textContent = "AI import cancelled.";
        return report;
      }
      if (statusEl) {
        statusEl.textContent =
          `AI import ${report.status} · imported ${report.imported} · ` +
          `duplicates ${report.duplicates} · failed ${report.failed}`;
      }
      return report;
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
      let docs = global.HCHealthVault.listDocuments().slice();
      docs.sort((a, b) => {
        const da = String(a.measured_at || a.report_date || a.imported_at || "");
        const db = String(b.measured_at || b.report_date || b.imported_at || "");
        return timelineNewestFirst ? db.localeCompare(da) : da.localeCompare(db);
      });
      if (activeCategoryFilter && activeCategoryFilter !== "all") {
        docs = docs.filter(
          (d) =>
            d.primary_category === activeCategoryFilter ||
            (d.secondary_categories || []).indexOf(activeCategoryFilter) >= 0
        );
      }
      docsEl.innerHTML = docs.length
        ? docs
            .map((d) => {
              const prov =
                d.provenance ||
                ((d.tags || []).find((t) => String(t).indexOf("provenance:") === 0) || "")
                  .toString()
                  .replace(/^provenance:/, "") ||
                "unspecified";
              const meas = global.HCHealthVault.listMeasurements({ document_id: d.id }) || [];
              return (
                `<div class="kpi small"><span class="vault-cat-chip">${escapeHtml(
                  d.primary_category || "other"
                )}</span> <strong>${escapeHtml(d.document_type)}</strong> · ${escapeHtml(
                  d.original_filename || d.id
                )}` +
                `<div class="muted">Measured ${escapeHtml(
                  d.measured_at || d.report_date || "—"
                )} · ${meas.length} metrics · ${escapeHtml(prov)} · ${escapeHtml(
                  d.status || ""
                )}</div></div>`
              );
            })
            .join("")
        : '<div class="muted">Vault is empty.</div>';
    }
    renderCategoryChips();
  }

  function renderCategoryChips() {
    const el = document.getElementById("vault_category_filters");
    if (!el) return;
    const cats = [
      ["all", "All"],
      ["blood_pressure", "Blood Pressure"],
      ["sleep", "Sleep"],
      ["ecg_cardiology", "ECG / Heart"],
      ["glucose_diabetes", "Glucose"],
      ["kidney_renal", "Kidney"],
      ["laboratory_report", "Labs"],
      ["weight_body_metrics", "Weight"],
      ["respiratory_oxygen", "Oxygen"],
      ["medication", "Medications"],
      ["other", "Other"],
    ];
    el.innerHTML = cats
      .map(
        ([id, label]) =>
          `<button type="button" class="vault-filter-chip${
            activeCategoryFilter === id ? " active" : ""
          }" data-cat="${id}" aria-pressed="${activeCategoryFilter === id}">${label}</button>`
      )
      .join("");
    el.querySelectorAll("[data-cat]").forEach((btn) => {
      btn.onclick = () => {
        activeCategoryFilter = btn.getAttribute("data-cat");
        refreshVaultViews();
      };
    });
  }

  function setTimelineSort(newestFirst) {
    timelineNewestFirst = !!newestFirst;
    refreshVaultViews();
  }

  function setCategoryFilter(category) {
    activeCategoryFilter = category || "all";
    refreshVaultViews();
  }

  function openMetricDetail(category, metric) {
    const recordsTab = document.querySelector('.tab[data="health_records_screen"]');
    const vaultTab = document.querySelector('.tab[data="vault"]');
    if (recordsTab) recordsTab.click();
    else if (vaultTab) vaultTab.click();
    if (category) setCategoryFilter(category);
    const trends = document.getElementById("vault_trends") || document.getElementById("consumer_trends_screen");
    const timeline = document.getElementById("vault_timeline") || document.getElementById("consumer_timeline_screen");
    if (metric && trends) {
      const nodes = trends.querySelectorAll(".kpi");
      nodes.forEach((el) => {
        const text = (el.textContent || "").toLowerCase();
        if (text.indexOf(String(metric).toLowerCase()) >= 0) {
          el.setAttribute("data-snapshot-focus", "true");
          if (el.scrollIntoView) el.scrollIntoView({ behavior: "smooth", block: "start" });
        }
      });
    } else if (timeline && timeline.scrollIntoView) {
      timeline.scrollIntoView({ behavior: "smooth", block: "start" });
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
    setTimelineSort,
    setCategoryFilter,
    openMetricDetail,
    showRecentlyImported,
    getBatchQueue: () => batchQueue.slice(),
    getLastBatchReport: () => lastBatchReport,
  };
})(typeof window !== "undefined" ? window : globalThis);
