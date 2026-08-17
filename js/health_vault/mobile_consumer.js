/* HC-319C API-only mobile consumer. Clinical payloads remain in memory only. */
(function () {
  "use strict";

  const SESSION_KEY = "hc_mobile_auth_session";
  let session = null;
  let summary = null;
  let records = [];

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

  async function request(path, options) {
    const response = await fetch(path, { ...(options || {}), headers: { ...authHeaders(), ...((options || {}).headers || {}) } });
    if (response.status === 401 || response.status === 403) {
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
    error.textContent = "";
    try {
      const body = await request("/api/auth/password/change", {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ current_password: byId("mobile_current_password").value, new_password: byId("mobile_new_password").value })
      });
      saveSession({ token: body.token, userId: body.user_id, name: body.name });
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
    document.querySelectorAll("[data-mobile-content]").forEach(node => node.replaceChildren());
    showAuthenticated(false);
    if (notifyServer && token) {
      await fetch("/api/auth/logout", { method: "POST", headers: { Authorization: `Bearer ${token}` } }).catch(() => {});
    }
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
    const target = clearContent("mobile_dashboard");
    text(target, `Welcome ${summary.patient_name || session.name || session.userId}`);
    text(target, `${summary.records_count || 0} records`);
    text(target, `${summary.measurements_count || 0} measurements`);
    byId("mobile_identity").textContent = `Signed in as ${session.name || session.userId} (${session.userId})`;
  }

  async function loadRecords() {
    const body = await request("/api/records");
    records = Array.isArray(body.records) ? body.records : [];
    renderList(clearContent("mobile_records"), records, "No records available.", (card, row) => {
      text(card, row.original_filename || row.title || "Health record");
      text(card, `${row.category || "other"} · ${row.status || "unknown"}`, "muted");
    });
  }

  async function showView(name) {
    document.querySelectorAll("[data-mobile-panel]").forEach(panel => { panel.hidden = panel.id !== `mobile_${name}`; });
    byId("mobile_status").textContent = "Loading…";
    try {
      if (!summary) await loadDashboard();
      if (name === "records") await loadRecords();
      if (name === "trends") {
        const trends = summary.trends || {};
        renderList(clearContent("mobile_trends"), Object.entries(trends), "No trends available.", (card, row) => {
          text(card, row[0]); text(card, JSON.stringify(row[1]), "muted");
        });
      }
      if (name === "observations") {
        const observations = summary.observations || [];
        renderList(clearContent("mobile_observations"), observations, "No observations available.", (card, row) => {
          text(card, row.fact || row.interpretation || "Observation");
          text(card, row.interpretation || row.explanation || "", "muted");
        });
      }
      byId("mobile_status").textContent = "";
    } catch (err) { byId("mobile_status").textContent = err.message; }
  }

  async function upload() {
    const file = byId("mobile_record_file").files[0];
    const target = clearContent("mobile_import");
    if (!file) return text(target, "Choose a report first.", "bad");
    const form = new FormData();
    form.append("file", file, file.name);
    try {
      const result = await request("/api/records/upload", { method: "POST", body: form });
      text(target, `Upload ${result.status || "accepted"}. Document ${result.document_id || "is processing"}.`);
      summary = null; records = [];
    } catch (err) { text(target, err.message, "bad"); }
  }

  async function restore() {
    try {
      const raw = sessionStorage.getItem(SESSION_KEY);
      if (!raw) return showAuthenticated(false);
      session = JSON.parse(raw);
      const current = await request("/api/auth/session");
      session.userId = current.user_id; session.name = current.name;
      showAuthenticated(true);
      await loadDashboard();
    } catch (_) { await logout(false); }
  }

  byId("mobile_login_button").addEventListener("click", login);
  byId("mobile_password_change").addEventListener("submit", changePassword);
  byId("mobile_logout_button").addEventListener("click", () => logout(true));
  byId("mobile_upload_button").addEventListener("click", upload);
  document.querySelectorAll("[data-mobile-view]").forEach(button => {
    button.addEventListener("click", () => showView(button.dataset.mobileView));
  });
  restore();
}());
