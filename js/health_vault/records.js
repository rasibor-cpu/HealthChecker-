/** HC-317C — authenticated consumer Health Records experience. */
(function (global) {
  "use strict";

  class RecordsUI {
    constructor() {
      this.records = [];
      this.searchTerm = "";
      this.selectedFile = null;
      this.listRequest = null;
      this.detailRequest = null;
      this.intelligenceByDocument = new Map();
      this.initialized = false;
    }

    init() {
      if (this.initialized) return;
      this.initialized = true;
      this.bindEvents();
      document.addEventListener("hc:session-changed", event => {
        if (!event.detail || !event.detail.authenticated) this.reset();
      });
    }

    dashboard() {
      return global.HCConsumerDashboard || null;
    }

    authHeaders() {
      const dashboard = this.dashboard();
      return dashboard && dashboard.getAuthorizationHeaders
        ? dashboard.getAuthorizationHeaders()
        : {};
    }

    isAuthenticated() {
      const dashboard = this.dashboard();
      return !!(dashboard && dashboard.token);
    }

    async request(url, options) {
      const config = Object.assign({}, options || {});
      config.headers = Object.assign({}, config.headers || {}, this.authHeaders());
      const response = await fetch(url, config);
      if (response.status === 401) {
        const dashboard = this.dashboard();
        if (dashboard) dashboard.handleLogout();
        throw new Error("authentication_required");
      }
      return response;
    }

    bindEvents() {
      this.on("records_refresh_btn", "click", () => this.refreshRecords());
      this.on("records_add_btn", "click", () => this.toggleUpload(true));
      this.on("records_upload_close_btn", "click", () => this.toggleUpload(false));
      this.on("records_upload_cancel_btn", "click", () => this.toggleUpload(false));
      this.on("records_upload_submit_btn", "click", () => this.uploadSelected());
      this.on("records_clear_filters_btn", "click", () => this.clearFilters());
      this.on("record_detail_close_btn", "click", () => this.closeDetail());

      const category = document.getElementById("records_category_filter");
      const status = document.getElementById("records_status_filter");
      if (category) category.addEventListener("change", () => this.refreshRecords());
      if (status) status.addEventListener("change", () => this.refreshRecords());

      const search = document.getElementById("records_search");
      if (search) search.addEventListener("input", () => {
        this.searchTerm = search.value.trim().toLowerCase();
        this.renderRecords();
      });

      const input = document.getElementById("records_file_input");
      if (input) input.addEventListener("change", () => this.selectFile(input.files && input.files[0]));
      const drop = document.getElementById("records_drop_zone");
      if (drop) {
        drop.addEventListener("click", event => {
          if (event.target !== input && input) input.click();
        });
        drop.addEventListener("keydown", event => {
          if ((event.key === "Enter" || event.key === " ") && input) {
            event.preventDefault();
            input.click();
          }
        });
        drop.addEventListener("dragover", event => {
          event.preventDefault();
          drop.classList.add("vault-drop-active");
        });
        drop.addEventListener("dragleave", () => drop.classList.remove("vault-drop-active"));
        drop.addEventListener("drop", event => {
          event.preventDefault();
          drop.classList.remove("vault-drop-active");
          this.selectFile(event.dataTransfer && event.dataTransfer.files[0]);
        });
      }

      const list = document.getElementById("records_list");
      if (list) list.addEventListener("click", event => {
        const button = event.target.closest("[data-record-detail]");
        if (button) this.openDetail(button.getAttribute("data-record-detail"));
      });
      const detail = document.getElementById("record_detail_content");
      if (detail) detail.addEventListener("click", event => {
        const button = event.target.closest("[data-record-download]");
        if (button) this.downloadRecord(button.getAttribute("data-record-download"), button);
      });
    }

    on(id, name, handler) {
      const element = document.getElementById(id);
      if (element) element.addEventListener(name, handler);
    }

    bindDashboardActions(root) {
      if (!root) return;
      root.querySelectorAll("[data-open-health-records]").forEach(button => {
        button.onclick = () => this.openScreen();
      });
    }

    openScreen() {
      const dashboard = this.dashboard();
      if (dashboard && dashboard.openScreen) dashboard.openScreen("health_records_screen");
    }

    reset() {
      if (this.listRequest) this.listRequest.abort();
      if (this.detailRequest) this.detailRequest.abort();
      this.records = [];
      this.searchTerm = "";
      this.intelligenceByDocument.clear();
      this.selectFile(null);
      const search = document.getElementById("records_search");
      if (search) search.value = "";
      this.closeDetail();
      this.toggleUpload(false);
      this.renderRecords();
      this.text("records_count", "0");
    }

    async refreshRecords() {
      if (!this.isAuthenticated()) return;
      if (this.listRequest) this.listRequest.abort();
      this.listRequest = new AbortController();
      this.state("records_loading", true);
      this.state("records_error", false);
      try {
        const params = new URLSearchParams();
        const category = document.getElementById("records_category_filter");
        const status = document.getElementById("records_status_filter");
        if (category && category.value) params.set("category", category.value);
        if (status && status.value) params.set("status", status.value);
        const response = await this.request(`/api/records${params.toString() ? `?${params}` : ""}`, {
          signal: this.listRequest.signal,
        });
        if (!response.ok) throw new Error("records_load_failed");
        const body = await response.json();
        this.records = Array.isArray(body.records) ? body.records : [];
        this.renderRecords();
        this.text("records_count", String(this.records.length));
        this.text("records_last_refreshed", `Updated ${new Date().toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" })}`);
      } catch (error) {
        if (error.name !== "AbortError" && error.message !== "authentication_required") {
          this.showError("records_error", "We could not load your records. Check your connection and try again.");
        }
      } finally {
        this.state("records_loading", false);
      }
    }

    filteredRecords() {
      if (!this.searchTerm) return this.records;
      return this.records.filter(record => [
        record.original_filename,
        record.primary_category,
        record.source_system,
        record.status,
      ].some(value => String(value || "").toLowerCase().includes(this.searchTerm)));
    }

    renderRecords() {
      const target = document.getElementById("records_list");
      if (!target) return;
      const records = this.filteredRecords();
      this.state("records_empty", records.length === 0);
      target.innerHTML = records.map(record => {
        const intelligence = this.intelligenceByDocument.get(record.document_id);
        const intelligenceText = intelligence == null
          ? "Intelligence: check details"
          : (intelligence ? "Linked intelligence available" : "No linked intelligence");
        return `
          <article class="card record-card">
            <div class="record-card-heading">
              <div>
                <h4>${this.escape(record.original_filename || "Untitled record")}</h4>
                <div class="muted small">${this.escape(this.dateLabel(record))}</div>
              </div>
              <span class="badge record-status record-status-${this.statusClass(record.status)}">${this.escape(this.label(record.status))}</span>
            </div>
            <div class="record-card-meta small">
              <span class="badge">${this.escape(this.label(record.primary_category))}</span>
              <span>${Number(record.metrics_count || 0)} extracted metrics</span>
              <span>Source: ${this.escape(record.source_system || "Not available")}</span>
              <span>${this.escape(intelligenceText)}</span>
            </div>
            <button type="button" class="secondary records-inline-action" data-record-detail="${this.escapeAttr(record.document_id)}">View details</button>
          </article>`;
      }).join("");
    }

    clearFilters() {
      ["records_category_filter", "records_status_filter", "records_search"].forEach(id => {
        const element = document.getElementById(id);
        if (element) element.value = "";
      });
      this.searchTerm = "";
      this.refreshRecords();
    }

    toggleUpload(show) {
      const panel = document.getElementById("records_upload_panel");
      if (panel) panel.hidden = !show;
      if (!show) {
        this.selectFile(null);
        this.text("records_upload_status", "");
      } else {
        const input = document.getElementById("records_file_input");
        if (input) input.focus();
      }
    }

    selectFile(file) {
      this.selectedFile = file || null;
      const input = document.getElementById("records_file_input");
      if (!file && input) input.value = "";
      const preview = document.getElementById("records_file_preview");
      if (preview) preview.textContent = file
        ? `${file.name} · ${this.formatBytes(file.size)} · ${file.type || "unknown type"}`
        : "No file selected.";
      const submit = document.getElementById("records_upload_submit_btn");
      if (submit) submit.disabled = !file;
    }

    async uploadSelected() {
      if (!this.selectedFile || !this.isAuthenticated()) return;
      const submit = document.getElementById("records_upload_submit_btn");
      if (submit) submit.disabled = true;
      this.text("records_upload_status", "Uploading securely and processing through HealthChecker intake…");
      const form = new FormData();
      form.append("file", this.selectedFile, this.selectedFile.name);
      try {
        const response = await this.request("/api/records/upload", { method: "POST", body: form });
        const body = await response.json();
        const state = body.status || (response.ok ? "imported" : "failed");
        const messages = [].concat(body.warnings || [], body.errors || []).filter(Boolean);
        if (!response.ok) {
          this.text("records_upload_status", `Upload ${this.label(state)}. ${messages.join(" ") || "Please review the file and try again."}`);
          return;
        }
        const outcome = state === "duplicate" ? "Duplicate detected" :
          (state === "requires_review" ? "Uploaded — review required" : `Upload ${this.label(state)}`);
        this.text("records_upload_status", `${outcome}. ${messages.join(" ")}`.trim());
        await this.refreshRecords();
        const dashboard = this.dashboard();
        if (dashboard) await dashboard.refresh();
        if (body.document_id) await this.openDetail(body.document_id);
      } catch (error) {
        if (error.message !== "authentication_required") {
          this.text("records_upload_status", "Upload failed because the service could not be reached. Your file was not stored by this page.");
        }
      } finally {
        if (submit) submit.disabled = !this.selectedFile;
      }
    }

    async openDetail(documentId) {
      if (!documentId || !this.isAuthenticated()) return;
      const dialog = document.getElementById("record_detail_dialog");
      if (!dialog) return;
      if (!dialog.open) dialog.showModal();
      this.state("record_detail_loading", true);
      this.state("record_detail_error", false);
      this.html("record_detail_content", "");
      if (this.detailRequest) this.detailRequest.abort();
      this.detailRequest = new AbortController();
      try {
        const response = await this.request(`/api/records/${encodeURIComponent(documentId)}`, {
          signal: this.detailRequest.signal,
        });
        if (!response.ok) {
          if (response.status === 404) throw new Error("record_not_found");
          throw new Error("record_detail_failed");
        }
        const record = await response.json();
        const hasIntelligence = (record.trend_references || []).length > 0 || (record.ai_observations || []).length > 0;
        this.intelligenceByDocument.set(documentId, hasIntelligence);
        this.renderRecords();
        this.renderDetail(record);
      } catch (error) {
        if (error.name !== "AbortError" && error.message !== "authentication_required") {
          this.showError("record_detail_error", error.message === "record_not_found"
            ? "This record was not found or is no longer available."
            : "We could not load this record. Please close the dialog and try again.");
        }
      } finally {
        this.state("record_detail_loading", false);
      }
    }

    renderDetail(record) {
      const metadata = record.metadata || {};
      const provenance = record.source_provenance || {};
      const gmail = provenance.gmail || null;
      this.text("record_detail_title", record.original_filename || "Record details");
      this.html("record_detail_content", `
        <section class="records-detail-section">
          <div class="record-card-heading"><h4>Record summary</h4><span class="badge record-status-${this.statusClass(record.status)}">${this.escape(this.label(record.status))}</span></div>
          ${this.definitionList([
            ["Category", this.label(record.primary_category)], ["Measured", this.formatDate(record.measured_at)],
            ["Imported", this.formatDate(record.imported_at)], ["Size", this.formatBytes(record.size_bytes)],
            ["Document type", metadata.document_type], ["Interpretation", metadata.interpretation],
          ])}
          <button type="button" data-record-download="${this.escapeAttr(record.document_id)}">Download original securely</button>
        </section>
        ${this.metricsSection(record.extracted_measurements || [])}
        ${this.trendsSection(record.trend_references || [])}
        ${this.observationsSection(record.ai_observations || [])}
        ${this.timelineSection(record.timeline_events || [])}
        <section class="records-detail-section"><h4>Provenance and evidence</h4>
          ${this.definitionList([
            ["Source system", provenance.source_system], ["Acquisition method", provenance.acquisition_method],
            ["Provenance", provenance.provenance], ["Original filename", provenance.original_filename],
            ["Gmail message", gmail && gmail.message_id], ["Gmail attachment", gmail && gmail.attachment_id],
            ["Batch", provenance.batch_id], ["Group", provenance.group_id],
          ])}
          ${this.evidenceList(record.evidence_references || [])}
        </section>
        ${this.lifecycleSection(record.lifecycle || [])}`);
    }

    metricsSection(items) {
      return `<section class="records-detail-section"><h4>Extracted health metrics</h4>${items.length ?
        `<div class="records-data-list">${items.map(item => `<div class="kpi small"><strong>${this.escape(item.metric || "Metric")}</strong><div>${this.escape(item.value)} ${this.escape(item.units || "")}</div><div class="muted">${this.escape(item.abnormal_flag || item.flag || "No flag supplied")}</div></div>`).join("")}</div>` :
        '<p class="muted small">No health metrics were extracted from this record.</p>'}</section>`;
    }

    trendsSection(items) {
      return `<section class="records-detail-section"><h4>Related trends</h4>${items.length ? items.map(item => {
        const trend = item.trend || {};
        return `<div class="kpi small"><strong>${this.escape(item.metric || trend.metric)}</strong><div>${this.escape(trend.label || trend.direction || "Trend available")}</div><div class="muted">Latest: ${this.escape(trend.latest)} · Samples: ${this.escape(trend.sample_count)}</div></div>`;
      }).join("") : '<p class="muted small">No trend evidence is linked to this record.</p>'}</section>`;
    }

    observationsSection(items) {
      return `<section class="records-detail-section"><h4>Related AI observations</h4>${items.length ? items.map(item => `<div class="kpi small"><strong>${this.escape(item.fact || "Observation")}</strong><div>${this.escape(item.interpretation || "")}</div>${item.explanation ? `<div class="muted">${this.escape(item.explanation)}</div>` : ""}<div class="records-safety-note">${this.escape(item.safety_boundary_disclaimer || "Observational information only — not a diagnosis.")}</div></div>`).join("") : '<p class="muted small">No AI observation is linked to this record.</p>'}</section>`;
    }

    timelineSection(items) {
      return `<section class="records-detail-section"><h4>Linked timeline</h4>${items.length ? `<ol class="records-timeline">${items.map(item => `<li><strong>${this.escape(this.formatDate(item.date))}</strong><div>${this.escape(item.trend_impact || item.summary || "Record imported")}</div></li>`).join("")}</ol>` : '<p class="muted small">No timeline event is linked to this record.</p>'}</section>`;
    }

    lifecycleSection(items) {
      return `<section class="records-detail-section"><h4>Processing history</h4>${items.length ? `<ol class="records-timeline">${items.map(item => `<li><strong>${this.escape(this.label(item.status))}</strong><div>${this.escape(item.event_type)} · ${this.escape(this.formatDate(item.timestamp))}</div><div class="muted small">Source: ${this.escape(item.source)}</div></li>`).join("")}</ol>` : '<p class="muted small">No persisted lifecycle events are available.</p>'}</section>`;
    }

    evidenceList(items) {
      if (!items.length) return '<p class="muted small">No evidence reference is linked to this record.</p>';
      return `<div class="records-evidence-list">${items.map(item => `<span class="badge">${this.escape(item.source_type || "evidence")}: ${this.escape(item.measurement_id || item.document_id || "linked")}</span>`).join("")}</div>`;
    }

    definitionList(rows) {
      return `<dl class="records-definition-list">${rows.filter(row => row[1] !== null && row[1] !== undefined && row[1] !== "").map(row => `<div><dt>${this.escape(row[0])}</dt><dd>${this.escape(row[1])}</dd></div>`).join("") || '<div><dt>Metadata</dt><dd>Not available</dd></div>'}</dl>`;
    }

    async downloadRecord(documentId, button) {
      if (!documentId) return;
      const original = button.textContent;
      button.disabled = true;
      button.textContent = "Preparing secure download…";
      try {
        const response = await this.request(`/api/records/download/${encodeURIComponent(documentId)}`);
        if (!response.ok) throw new Error("download_failed");
        const blob = await response.blob();
        const disposition = response.headers.get("Content-Disposition") || "";
        const match = disposition.match(/filename="?([^";]+)"?/i);
        const filename = (match && match[1] ? match[1] : "health-record").replace(/[/\\]/g, "_");
        const url = URL.createObjectURL(blob);
        const anchor = document.createElement("a");
        anchor.href = url;
        anchor.download = filename;
        anchor.click();
        URL.revokeObjectURL(url);
      } catch (error) {
        this.showError("record_detail_error", "The secure download could not be prepared. Please try again.");
      } finally {
        button.disabled = false;
        button.textContent = original;
      }
    }

    closeDetail() {
      if (this.detailRequest) this.detailRequest.abort();
      const dialog = document.getElementById("record_detail_dialog");
      if (dialog && dialog.open) dialog.close();
      this.html("record_detail_content", "");
      this.text("record_detail_title", "Record details");
    }

    dateLabel(record) {
      return record.measured_at ? `Measured ${this.formatDate(record.measured_at)}` : `Imported ${this.formatDate(record.imported_at)}`;
    }

    formatDate(value) {
      if (!value) return "Not available";
      const date = new Date(value);
      return Number.isNaN(date.getTime()) ? String(value) : date.toLocaleString([], { year: "numeric", month: "short", day: "numeric" });
    }

    formatBytes(value) {
      const bytes = Number(value);
      if (!Number.isFinite(bytes) || bytes < 0) return "Not available";
      if (bytes < 1024) return `${bytes} B`;
      if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
      return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
    }

    label(value) {
      return String(value || "not available").replace(/_/g, " ").replace(/\b\w/g, char => char.toUpperCase());
    }

    statusClass(value) {
      return String(value || "unknown").toLowerCase().replace(/[^a-z0-9_-]/g, "-");
    }

    escape(value) {
      if (value === null || value === undefined) return "";
      return String(value).replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;").replace(/"/g, "&quot;").replace(/'/g, "&#39;");
    }

    escapeAttr(value) {
      return this.escape(value).replace(/`/g, "&#96;");
    }

    state(id, visible) {
      const element = document.getElementById(id);
      if (element) element.hidden = !visible;
    }

    showError(id, message) {
      this.text(id, message);
      this.state(id, true);
    }

    text(id, value) {
      const element = document.getElementById(id);
      if (element) element.textContent = value;
    }

    html(id, value) {
      const element = document.getElementById(id);
      if (element) element.innerHTML = value;
    }
  }

  global.HCRecordsUI = new RecordsUI();
  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", () => global.HCRecordsUI.init());
  } else {
    global.HCRecordsUI.init();
  }
})(typeof window !== "undefined" ? window : globalThis);
