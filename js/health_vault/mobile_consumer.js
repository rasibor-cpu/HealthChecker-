/* HC-319C API-only mobile consumer. Clinical payloads remain in memory only. */
(function () {
  "use strict";

  const SESSION_KEY = "hc_mobile_auth_session";
  let session = null;
  let summary = null;
  let records = [];
  let preferences = null;

  const byId = id => document.getElementById(id);
  const authHeaders = () => session ? { Authorization: `Bearer ${session.token}` } : {};

  function saveSession(value) {
    session = value;
    if (value) sessionStorage.setItem(SESSION_KEY, JSON.stringify(value));
    else sessionStorage.removeItem(SESSION_KEY);
  }

  function text(parent, value, className) {
    const node = document.createElement("p");
    if (className) node.className = className;
    node.textContent = String(value == null ? "" : value);
    parent.appendChild(node);
  }

  function clearContent(panelId) {
    const target = byId(panelId).querySelector("[data-mobile-content]");
    target.replaceChildren();
    return target;
  }

  function widget(id) {
    return ((summary && summary.widgets) || []).find(item => item.widget_id === id) || { payload: {} };
  }

  function label(value) {
    return String(value || "Not available").replace(/_/g, " ").replace(/\b\w/g, char => char.toUpperCase());
  }

  function setTheme(theme) {
    document.body.classList.toggle("dark-theme", theme === "dark");
    document.body.classList.toggle("light-theme", theme !== "dark");
  }

  async function request(path, options) {
    const response = await fetch(path, { ...(options || {}), headers: { ...authHeaders(), ...((options || {}).headers || {}) } });
    if (session && (response.status === 401 || response.status === 403)) {
      await logout(false);
      throw new Error(response.status === 403 ? "Password change required" : "Session expired");
    }
    const body = await response.json();
    if (!response.ok) throw new Error(body.error || (body.errors || []).join(", ") || "Request failed");
    return body;
  }

  function showAuthenticated(active) {
    byId("mobile_login").hidden = active;
    byId("mobile_consumer").hidden = !active;
  }

  async function login() {
    const error = byId("mobile_login_error");
    error.textContent = "";
    try {
      const body = await request("/api/auth/login", {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ user_id: byId("mobile_user_id").value.trim(), password: byId("mobile_password").value })
      });
      saveSession({ token: body.token, userId: body.user_id, name: body.name });
      byId("mobile_password").value = "";
      if (body.must_change_password) {
        byId("mobile_password_change").hidden = false;
        return;
      }
      showAuthenticated(true);
      await loadDashboard();
    } catch (err) { error.textContent = err.message; }
  }

  async function changePassword(event) {
    event.preventDefault();
    const error = byId("mobile_password_error");
    const next = byId("mobile_new_password").value;
    error.textContent = "";
    if (next.length < 8 || next !== byId("mobile_confirm_password").value) {
      error.textContent = "New passwords must match and contain at least 8 characters.";
      return;
    }
    try {
      const body = await request("/api/auth/password/change", {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ current_password: byId("mobile_current_password").value, new_password: next })
      });
      saveSession({ token: body.token, userId: body.user_id, name: body.name });
      ["mobile_current_password", "mobile_new_password", "mobile_confirm_password"].forEach(id => { byId(id).value = ""; });
      byId("mobile_password_change").hidden = true;
      showAuthenticated(true);
      await loadDashboard();
    } catch (err) { error.textContent = err.message; }
  }

  async function logout(notifyServer) {
    const token = session && session.token;
    saveSession(null);
    summary = null;
    records = [];
    preferences = null;
    document.querySelectorAll("[data-mobile-content]").forEach(node => node.replaceChildren());
    showAuthenticated(false);
    if (notifyServer && token) {
      await fetch("/api/auth/logout", {
        method: "POST",
        headers: { Authorization: `Bearer ${token}`, "Content-Type": "application/json" },
        body: JSON.stringify({ revoke_companion_devices: true })
      }).catch(() => {});
    }
    window.location.replace("/mobile/native-logout-complete");
  }

  function renderList(target, rows, emptyMessage, formatter) {
    target.replaceChildren();
    if (!rows.length) return text(target, emptyMessage, "muted");
    rows.forEach(row => {
      const card = document.createElement("article");
      card.className = "card";
      formatter(card, row);
      target.appendChild(card);
    });
  }

  async function loadDashboard() {
    summary = await request("/api/dashboard/summary");
    preferences = await request("/api/dashboard/preferences");
    setTheme(preferences.theme);
    const target = clearContent("mobile_dashboard");
    const status = widget("status_summary").payload;
    const imported = widget("import_wizard").payload;
    text(target, session.name && session.name !== session.userId
      ? `Welcome, ${session.name}`
      : "My Health Dashboard");
    text(target, `Overall status: ${label(summary.overall_status)}`);
    text(target, `${Number(imported.records_count || 0)} records`);
    text(target, `${Number(status.measurements_count || 0)} measurements`);
    text(target, `${Number(summary.active_warnings_count || 0)} attention items`);
    byId("mobile_identity").textContent = session.name && session.name !== session.userId
      ? `Signed in as ${session.name} (Patient ID: ${session.userId})`
      : `Signed in as Patient ID ${session.userId}`;
    byId("mobile_theme").value = preferences.theme === "dark" ? "dark" : "light";
    byId("mobile_priority_metric").value = preferences.priority_metric || "";
  }

  async function loadRecords() {
    const body = await request("/api/records");
    records = Array.isArray(body.records) ? body.records : [];
    renderList(clearContent("mobile_records"), records, "No records available. Use Import to add your first report.", (card, row) => {
      text(card, row.original_filename || row.title || "Health record");
      text(card, `${label(row.primary_category)} · ${label(row.status)}`, "muted");
      const button = document.createElement("button");
      button.type = "button";
      button.textContent = "View details";
      button.addEventListener("click", () => loadRecordDetail(row.document_id, card));
      card.appendChild(button);
    });
  }

  async function loadRecordDetail(documentId, card) {
    card.querySelectorAll("[data-record-detail]").forEach(node => node.remove());
    const detail = document.createElement("section");
    detail.dataset.recordDetail = "true";
    detail.setAttribute("aria-live", "polite");
    text(detail, "Loading record details…", "muted");
    card.appendChild(detail);
    try {
      const record = await request(`/api/records/${encodeURIComponent(documentId)}`);
      detail.replaceChildren();
      text(detail, `Source: ${(record.source_provenance || {}).source_system || "Not available"}`);
      text(detail, `${(record.extracted_measurements || []).length} extracted measurements`);
      text(detail, `${(record.trend_references || []).length} related trends`);
      text(detail, `${(record.ai_observations || []).length} AI observations`);
      text(detail, `${(record.timeline_events || []).length} timeline events`);
      text(detail, `${(record.evidence_references || []).length} evidence references`);
      (record.extracted_measurements || []).forEach(item => text(detail, `${label(item.metric)}: ${item.value == null ? "Not available" : item.value} ${item.units || ""}`));
    } catch (error) {
      detail.replaceChildren();
      text(detail, error.message, "bad");
    }
  }

  async function showView(name) {
    document.querySelectorAll("[data-mobile-panel]").forEach(panel => { panel.hidden = panel.id !== `mobile_${name}`; });
    document.querySelectorAll("[data-mobile-view]").forEach(button => button.setAttribute("aria-pressed", String(button.dataset.mobileView === name)));
    byId("mobile_status").textContent = "Loading…";
    try {
      if (!summary) await loadDashboard();
      if (name === "dashboard") await loadDashboard();
      if (name === "records") await loadRecords();
      if (name === "trends") {
        const trends = widget("trends_widget").payload.trends || {};
        renderList(clearContent("mobile_trends"), Object.entries(trends), "No trends available.", (card, row) => {
          const trend = row[1] || {};
          text(card, label(row[0]));
          text(card, `${trend.label || trend.direction || "Not enough data"} · Latest ${trend.latest == null ? "not available" : trend.latest} · ${trend.sample_count || 0} samples`, "muted");
        });
      }
      if (name === "observations") {
        const observations = widget("key_observations").payload.observations || [];
        renderList(clearContent("mobile_observations"), observations, "No observations available.", (card, row) => {
          text(card, row.fact || row.interpretation || "Observation");
          text(card, row.interpretation || row.explanation || "", "muted");
          text(card, row.safety_boundary_disclaimer || "Observational information only — not a diagnosis.", "muted");
        });
      }
      if (name === "timeline") {
        const body = await request("/api/health-vault/timeline?unified=true");
        renderList(clearContent("mobile_timeline"), body.entries || [], "Your timeline is empty.", (card, row) => {
          text(card, row.date ? String(row.date).slice(0, 10) : "Timeline event");
          text(card, row.summary || row.trend_impact || row.event_type || "Health event", "muted");
          text(card, `Source: ${row.provenance || row.source || "Not available"}`, "muted");
        });
      }
      if (name === "reports") {
        const report = await request("/api/health-vault/doctor-visit");
        const target = clearContent("mobile_reports");
        const keys = Object.keys(report || {});
        if (!keys.length) text(target, "No report information is available yet.", "muted");
        keys.forEach(key => {
          const card = document.createElement("article");
          card.className = "card";
          const value = report[key];
          text(card, label(key));
          text(card, typeof value === "object" ? `${Array.isArray(value) ? value.length : Object.keys(value || {}).length} linked items` : value, "muted");
          target.appendChild(card);
        });
      }
      byId("mobile_status").textContent = "";
    } catch (error) { byId("mobile_status").textContent = error.message; }
  }

  async function upload() {
    const file = byId("mobile_record_file").files[0];
    const target = clearContent("mobile_import");
    if (!file) return text(target, "Choose a report first.", "bad");
    const form = new FormData();
    form.append("file", file, file.name);
    try {
      const result = await request("/api/records/upload", { method: "POST", body: form });
      text(target, `Upload ${label(result.status || "accepted")}. ${result.document_id ? "The record is now available in Records." : "The record is processing."}`);
      summary = null; records = [];
    } catch (error) { text(target, error.message, "bad"); }
  }

  async function savePreferences() {
    const status = byId("mobile_status");
    try {
      if (!preferences) preferences = await request("/api/dashboard/preferences");
      preferences.theme = byId("mobile_theme").value;
      preferences.priority_metric = byId("mobile_priority_metric").value || null;
      preferences = await request("/api/dashboard/preferences", {
        method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(preferences)
      });
      setTheme(preferences.theme);
      summary = null;
      status.textContent = "Preferences saved.";
    } catch (error) { status.textContent = error.message; }
  }

  async function restore() {
    try {
      const raw = sessionStorage.getItem(SESSION_KEY);
      if (!raw) return showAuthenticated(false);
      session = JSON.parse(raw);
      const current = await request("/api/auth/session");
      session.userId = current.user_id; session.name = current.name;
      showAuthenticated(true);
      await showView("dashboard");
    } catch (_) { await logout(false); }
  }

  byId("mobile_login_button").addEventListener("click", login);
  byId("mobile_password_change").addEventListener("submit", changePassword);
  byId("mobile_logout_button").addEventListener("click", () => logout(true));
  byId("mobile_upload_button").addEventListener("click", upload);
  byId("mobile_save_preferences").addEventListener("click", savePreferences);
  document.querySelectorAll("[data-mobile-view]").forEach(button => {
    button.setAttribute("aria-pressed", "false");
    button.addEventListener("click", () => showView(button.dataset.mobileView));
  });
  restore();
}());
