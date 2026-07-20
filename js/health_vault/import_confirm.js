/**
 * HC-201H — Mobile confirmation / progress / result overlays (no-scroll required).
 */
(function (global) {
  "use strict";

  let activeModal = null;
  let previousFocus = null;
  let processingLock = false;

  function ensureRoot() {
    let root = document.getElementById("vault_overlay_root");
    if (!root) {
      root = document.createElement("div");
      root.id = "vault_overlay_root";
      document.body.appendChild(root);
    }
    return root;
  }

  function trapFocus(modal) {
    const focusable = modal.querySelectorAll(
      'button, [href], input, select, textarea, [tabindex]:not([tabindex="-1"])'
    );
    if (!focusable.length) return;
    const first = focusable[0];
    const last = focusable[focusable.length - 1];
    function onKey(e) {
      if (e.key === "Escape") {
        e.preventDefault();
        closeModal(false);
        return;
      }
      if (e.key !== "Tab") return;
      if (e.shiftKey && document.activeElement === first) {
        e.preventDefault();
        last.focus();
      } else if (!e.shiftKey && document.activeElement === last) {
        e.preventDefault();
        first.focus();
      }
    }
    modal.addEventListener("keydown", onKey);
    modal._focusHandler = onKey;
    first.focus();
  }

  function closeModal(confirmed) {
    const root = ensureRoot();
    if (activeModal && activeModal._focusHandler) {
      activeModal.removeEventListener("keydown", activeModal._focusHandler);
    }
    root.innerHTML = "";
    activeModal = null;
    if (previousFocus && previousFocus.focus) {
      try {
        previousFocus.focus();
      } catch (_) {}
    }
    if (activeModal && activeModal._resolver) {
      /* noop */
    }
    const resolver = root._resolver;
    root._resolver = null;
    if (typeof resolver === "function") resolver(!!confirmed);
  }

  function openConfirm(summary) {
    return new Promise((resolve) => {
      previousFocus = document.activeElement;
      const root = ensureRoot();
      root._resolver = resolve;
      const cats = summary.estimated_categories || {};
      const catLines = Object.keys(cats)
        .map((k) => `${k}: ${cats[k]}`)
        .join(", ");
      root.innerHTML = `
        <div class="vault-modal-backdrop" role="presentation">
          <div class="vault-modal" role="dialog" aria-modal="true" aria-labelledby="vault_confirm_title">
            <h3 id="vault_confirm_title" class="section-title">Confirm Health Record Import</h3>
            <p>Confirm the addition of <strong>${summary.total}</strong> files to your HealthChecker+ records?</p>
            <ul class="small vault-confirm-list" aria-label="Batch summary">
              <li>Total files: ${summary.total}</li>
              <li>Images: ${summary.images || 0}</li>
              <li>PDFs: ${summary.pdfs || 0}</li>
              <li>JSON: ${summary.json || 0}</li>
              <li>Batch size: ${summary.size_label || "—"}</li>
              <li>Estimated categories: ${catLines || "pending classification"}</li>
              <li>Possible duplicates: ${summary.duplicate_candidates || 0}</li>
            </ul>
            <div class="vault-modal-actions">
              <button type="button" class="secondary" id="vault_confirm_cancel">Cancel</button>
              <button type="button" id="vault_confirm_ok">Confirm Import</button>
            </div>
          </div>
        </div>`;
      activeModal = root.querySelector(".vault-modal");
      document.getElementById("vault_confirm_cancel").onclick = () => {
        const r = root._resolver;
        root._resolver = null;
        root.innerHTML = "";
        activeModal = null;
        if (r) r(false);
      };
      document.getElementById("vault_confirm_ok").onclick = () => {
        const r = root._resolver;
        root._resolver = null;
        root.innerHTML = "";
        activeModal = null;
        if (r) r(true);
      };
      trapFocus(activeModal);
    });
  }

  function showProgress(snap) {
    const root = ensureRoot();
    let panel = document.getElementById("vault_fixed_progress");
    if (!panel) {
      panel = document.createElement("div");
      panel.id = "vault_fixed_progress";
      panel.className = "vault-fixed-progress";
      panel.setAttribute("role", "status");
      panel.setAttribute("aria-live", "polite");
      root.appendChild(panel);
    }
    const pct = snap.total ? Math.round((100 * (snap.processed || 0)) / snap.total) : 0;
    panel.innerHTML = `
      <div class="small"><strong>Processing ${snap.processed || 0} of ${snap.total || 0}</strong></div>
      <div class="vault-progress-bar" aria-valuemin="0" aria-valuemax="100" aria-valuenow="${pct}">
        <div class="vault-progress-fill" style="width:${pct}%"></div>
      </div>
      <div class="small muted">Imported ${snap.imported || 0} · Duplicates ${snap.duplicates || 0} · Failed ${snap.failed || 0}</div>
      <div class="small">Current: ${escapeHtml(snap.current_filename || "—")}</div>`;
  }

  function hideProgress() {
    const panel = document.getElementById("vault_fixed_progress");
    if (panel) panel.remove();
  }

  function showResult(report, actions) {
    return new Promise((resolve) => {
      previousFocus = document.activeElement;
      const root = ensureRoot();
      hideProgress();
      const imported = report.imported || 0;
      const dup = report.duplicates || 0;
      const failed = report.failed || 0;
      const selected = report.selected || report.total || 0;
      let headline;
      if (failed === 0 && dup === 0) {
        headline = `${imported} file${imported === 1 ? "" : "s"} successfully added to HealthChecker+.`;
      } else if (failed === 0) {
        headline = `Batch completed: ${imported} imported, ${dup} duplicate, ${failed} failed.`;
      } else {
        headline = `${failed} file${failed === 1 ? "" : "s"} could not be added. ${imported} file${imported === 1 ? "" : "s"} were imported successfully.`;
      }
      const cats = report.category_counts || {};
      const catTxt = Object.keys(cats)
        .map((k) => `${k}: ${cats[k]}`)
        .join(", ");
      root.innerHTML = `
        <div class="vault-modal-backdrop" role="presentation">
          <div class="vault-modal" role="dialog" aria-modal="true" aria-labelledby="vault_result_title">
            <h3 id="vault_result_title" class="section-title">Import Result</h3>
            <p class="${failed ? "bad" : "ok"}" role="status">${escapeHtml(headline)}</p>
            <ul class="small vault-confirm-list">
              <li>Selected: ${selected}</li>
              <li>Imported: ${imported}</li>
              <li>Duplicates: ${dup}</li>
              <li>Failed: ${failed}</li>
              <li>Grouped reports: ${report.grouped_reports || 0}</li>
              <li>Categories: ${escapeHtml(catTxt || "—")}</li>
            </ul>
            <div class="vault-modal-actions">
              <button type="button" id="vault_result_records">View Imported Records</button>
              <button type="button" class="secondary" id="vault_result_timeline">View Timeline</button>
              <button type="button" class="secondary" id="vault_result_close">Close</button>
            </div>
          </div>
        </div>`;
      activeModal = root.querySelector(".vault-modal");
      const finish = (action) => {
        root.innerHTML = "";
        activeModal = null;
        resolve(action);
      };
      document.getElementById("vault_result_close").onclick = () => finish("close");
      document.getElementById("vault_result_records").onclick = () => {
        if (actions && actions.viewRecords) actions.viewRecords();
        finish("records");
      };
      document.getElementById("vault_result_timeline").onclick = () => {
        if (actions && actions.viewTimeline) actions.viewTimeline();
        finish("timeline");
      };
      trapFocus(activeModal);
    });
  }

  function escapeHtml(v) {
    return String(v == null ? "" : v)
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;");
  }

  function setProcessingLock(v) {
    processingLock = !!v;
    document.querySelectorAll(".vault-batch-actions button, .vault-file-label").forEach((el) => {
      if (processingLock) el.setAttribute("aria-disabled", "true");
      else el.removeAttribute("aria-disabled");
      if ("disabled" in el) el.disabled = processingLock;
    });
  }

  function isProcessingLocked() {
    return processingLock;
  }

  function openAiConfirm(summary) {
    return new Promise((resolve) => {
      previousFocus = document.activeElement;
      const root = ensureRoot();
      root._resolver = resolve;
      const provider = summary.provider_label || summary.provider_id || "ChatGPT";
      const n = summary.record_count || summary.total || 0;
      const cats = summary.categories || summary.estimated_categories || {};
      const catLines = Object.keys(cats)
        .map((k) => `${k}: ${cats[k]}`)
        .join(", ");
      const dr = summary.date_range || {};
      const dateLine =
        dr.earliest && dr.latest
          ? `${dr.earliest} → ${dr.latest}`
          : dr.earliest || dr.latest || "pending";
      root.innerHTML = `
        <div class="vault-modal-backdrop" role="presentation">
          <div class="vault-modal" role="dialog" aria-modal="true" aria-labelledby="ai_confirm_title">
            <h3 id="ai_confirm_title" class="section-title">AI Health Import</h3>
            <p><strong>${escapeHtml(provider)}</strong> has prepared <strong>${n}</strong> health record${n === 1 ? "" : "s"}.</p>
            <p>Import into HealthChecker+?</p>
            <ul class="small vault-confirm-list" aria-label="AI import summary">
              <li>Records: ${n}</li>
              <li>Categories: ${escapeHtml(catLines || "pending classification")}</li>
              <li>Date range: ${escapeHtml(dateLine)}</li>
              <li>Possible duplicates: ${summary.duplicate_estimate || summary.duplicate_candidates || 0}</li>
            </ul>
            <div class="vault-modal-actions">
              <button type="button" class="secondary" id="ai_confirm_cancel">Cancel</button>
              <button type="button" id="ai_confirm_ok">Import</button>
            </div>
          </div>
        </div>`;
      activeModal = root.querySelector(".vault-modal");
      document.getElementById("ai_confirm_cancel").onclick = () => {
        const r = root._resolver;
        root._resolver = null;
        root.innerHTML = "";
        activeModal = null;
        if (r) r(false);
      };
      document.getElementById("ai_confirm_ok").onclick = () => {
        const r = root._resolver;
        root._resolver = null;
        root.innerHTML = "";
        activeModal = null;
        if (r) r(true);
      };
      trapFocus(activeModal);
    });
  }

  function showAiResult(report, actions) {
    return new Promise((resolve) => {
      previousFocus = document.activeElement;
      const root = ensureRoot();
      hideProgress();
      const imported = report.imported || 0;
      const dup = report.duplicates || 0;
      const failed = report.failed || 0;
      root.innerHTML = `
        <div class="vault-modal-backdrop" role="presentation">
          <div class="vault-modal" role="dialog" aria-modal="true" aria-labelledby="ai_result_title">
            <h3 id="ai_result_title" class="section-title">AI Import Result</h3>
            <ul class="small vault-confirm-list" role="status">
              <li>Imported: ${imported}</li>
              <li>Duplicates: ${dup}</li>
              <li>Failed: ${failed}</li>
              <li>Grouped reports: ${report.grouped_reports || 0}</li>
              <li>Updated trends: ${report.updated_trends ? "Yes" : "No"}</li>
              <li>Dashboard refreshed: ${report.dashboard_refreshed ? "Yes" : "No"}</li>
              <li>Doctor visit updated: ${report.doctor_visit_updated ? "Yes" : "No"}</li>
            </ul>
            <div class="vault-modal-actions">
              <button type="button" id="ai_result_records">View Records</button>
              <button type="button" class="secondary" id="ai_result_timeline">View Timeline</button>
              <button type="button" class="secondary" id="ai_result_close">Close</button>
            </div>
          </div>
        </div>`;
      activeModal = root.querySelector(".vault-modal");
      const finish = (action) => {
        root.innerHTML = "";
        activeModal = null;
        resolve(action);
      };
      document.getElementById("ai_result_close").onclick = () => finish("close");
      document.getElementById("ai_result_records").onclick = () => {
        if (actions && actions.viewRecords) actions.viewRecords();
        finish("records");
      };
      document.getElementById("ai_result_timeline").onclick = () => {
        if (actions && actions.viewTimeline) actions.viewTimeline();
        finish("timeline");
      };
      trapFocus(activeModal);
    });
  }

  global.HCImportConfirmUI = {
    openConfirm,
    openAiConfirm,
    showProgress,
    hideProgress,
    showResult,
    showAiResult,
    setProcessingLock,
    isProcessingLocked,
  };
})(typeof window !== "undefined" ? window : globalThis);
