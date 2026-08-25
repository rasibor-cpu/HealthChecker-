/* HC-319C API-only mobile consumer. Clinical payloads remain in memory only. */
(function () {
  "use strict";

  const SESSION_KEY = "hc_mobile_auth_session";
  let session = null;
  let summary = null;
  let records = [];
  let preferences = null;
  let catalog = null;
  let recoveryId = null;
  let recoveryToken = null;
  let pendingPasswordChange = null;
  let authState = "login";

  const byId = id => document.getElementById(id);
  const authHeaders = () => session ? { Authorization: `Bearer ${session.token}` } : {};

  function userFacingAuthError(code, fallback) {
    const map = {
      recovery_enrollment_required: "Choose three recovery questions before continuing.",
      password_change_required: "A new password is required before HealthChecker can be used.",
      password_policy_violation: "Choose a password with at least 8 characters that is not the temporary sign-in password.",
      password_confirmation_mismatch: "New passwords must match.",
      invalid_credentials: "That current password was not accepted. Check it and try again.",
      invalid_recovery: "Recovery could not be completed.",
    };
    const key = String(code || "");
    if (map[key]) return map[key];
    if (/^[a-z0-9_]+$/.test(key)) return fallback || "That request could not be completed.";
    return key || fallback || "That request could not be completed.";
  }

  function setAuthState(state) {
    authState = state;
    try { document.body.dataset.hcAuthState = state; } catch (_) {}
    const gated = state === "password_change_required" || state === "recovery_enrollment_required";
    setSecurityGate(gated);
  }

  function setSecurityGate(active) {
    if (window.HCConsumerNav) HCConsumerNav.setSecurityGate(!!active);
  }

  function saveSession(value) {
    session = value;
    if (value) sessionStorage.setItem(SESSION_KEY, JSON.stringify(value));
    else sessionStorage.removeItem(SESSION_KEY);
    try {
      document.dispatchEvent(new CustomEvent("hc:session-changed", { detail: { authenticated: !!value } }));
    } catch (_) {}
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
    const gated = window.HCConsumerNav && HCConsumerNav.isSecurityGate && HCConsumerNav.isSecurityGate();
    if (session && (response.status === 401 || response.status === 403) && !gated) {
      await logout(false);
      throw new Error(response.status === 403 ? "Password change required" : "Session expired");
    }
    const body = await response.json();
    if (!response.ok) throw new Error(userFacingAuthError(body.code || body.error, "Request failed"));
    return body;
  }

  function showAuthenticated(active) {
    byId("mobile_login").hidden = active;
    byId("mobile_consumer").hidden = !active;
  }

  async function loadCatalog() {
    if (catalog && catalog.length) return catalog;
    const response = await fetch("/api/auth/recovery/catalog");
    const body = await response.json();
    if (!response.ok || !(body.questions || []).length) throw new Error("catalog_unavailable");
    catalog = body.questions || [];
    return catalog;
  }

  function renderQuestionPickers(containerId, prefix) {
    const host = byId(containerId);
    if (!host) return;
    host.replaceChildren();
    for (let i = 1; i <= 3; i++) {
      const label = document.createElement("label");
      label.textContent = "Recovery question " + i;
      const select = document.createElement("select");
      select.id = prefix + "_q" + i;
      const blank = document.createElement("option");
      blank.value = "";
      blank.textContent = "Choose a question";
      select.appendChild(blank);
      (catalog || []).forEach(question => {
        const option = document.createElement("option");
        option.value = question.question_id;
        option.textContent = question.prompt;
        select.appendChild(option);
      });
      const answer = document.createElement("input");
      answer.id = prefix + "_a" + i;
      answer.type = "text";
      answer.autocomplete = "off";
      host.appendChild(label);
      host.appendChild(select);
      host.appendChild(answer);
    }
  }

  function collectAnswers(prefix) {
    const rows = [];
    for (let i = 1; i <= 3; i++) {
      const question = byId(prefix + "_q" + i);
      const answer = byId(prefix + "_a" + i);
      rows.push({
        question_id: question ? String(question.value || "").trim() : "",
        answer: answer ? String(answer.value || "").trim() : "",
      });
    }
    return rows;
  }

  function validateEnrollment(rows) {
    const ids = (rows || []).map(row => String(row.question_id || "").trim());
    const answers = (rows || []).map(row => String(row.answer || "").trim());
    if (ids.length !== 3 || ids.some(id => !id) || new Set(ids).size !== 3) {
      return "Choose three different recovery questions.";
    }
    if (answers.some(answer => !answer)) {
      return "Enter an answer for each recovery question.";
    }
    return "";
  }

  function renderRecoveryPrompts(containerId, questions) {
    const host = byId(containerId);
    if (!host) return;
    host.replaceChildren();
    (questions || []).forEach(question => {
      const label = document.createElement("label");
      label.textContent = question.prompt;
      const input = document.createElement("input");
      input.type = "text";
      input.autocomplete = "off";
      input.dataset.questionId = question.question_id;
      host.appendChild(label);
      host.appendChild(input);
    });
  }

  function collectPromptAnswers(containerId) {
    const host = byId(containerId);
    if (!host) return [];
    return Array.from(host.querySelectorAll("input")).map(input => ({
      question_id: input.dataset.questionId,
      answer: input.value,
    }));
  }

  function hideSignInControls() {
    const loginBtn = byId("mobile_login_button");
    const forgot = byId("mobile_forgot_password_btn");
    if (loginBtn) loginBtn.hidden = true;
    if (forgot) forgot.hidden = true;
  }

  function hideLifecycleForms() {
    byId("mobile_password_change").hidden = true;
    const enroll = byId("mobile_recovery_enroll");
    if (enroll) enroll.hidden = true;
    byId("mobile_recovery_flow").hidden = true;
  }

  function showLoginExtras() {
    pendingPasswordChange = null;
    setAuthState("login");
    const loginBtn = byId("mobile_login_button");
    const forgot = byId("mobile_forgot_password_btn");
    if (loginBtn) loginBtn.hidden = false;
    if (forgot) forgot.hidden = false;
    hideLifecycleForms();
  }

  function enterPasswordGate() {
    setAuthState("password_change_required");
    showAuthenticated(false);
    hideSignInControls();
    hideLifecycleForms();
    byId("mobile_password_change").hidden = false;
  }

  async function enterEnrollmentGate() {
    setAuthState("recovery_enrollment_required");
    showAuthenticated(false);
    hideSignInControls();
    hideLifecycleForms();
    const enroll = byId("mobile_recovery_enroll");
    if (enroll) enroll.hidden = false;
    const error = byId("mobile_enroll_error");
    if (error) error.textContent = "";
    try {
      await loadCatalog();
      renderQuestionPickers("mobile_enroll_questions", "mobile_enroll");
    } catch (_err) {
      if (error) error.textContent = "Recovery questions could not be loaded. Try again.";
    }
  }

  async function login() {
    const error = byId("mobile_login_error");
    error.textContent = "";
    try {
      const body = await request("/api/auth/login", {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ user_id: byId("mobile_user_id").value.trim(), password: byId("mobile_password").value })
      });
      saveSession({
        token: body.token,
        userId: body.user_id,
        name: body.name,
        expiresAt: body.password_expires_at,
        recoveryEnrolled: !!body.recovery_enrolled,
      });
      byId("mobile_password").value = "";
      if (body.must_change_password) {
        enterPasswordGate();
        return;
      }
      setAuthState("authenticated");
      showAuthenticated(true);
      await loadDashboard();
      const deep = window.HCConsumerNav && HCConsumerNav.peekDeepLink();
      if (deep) await showView(deep);
    } catch (err) { error.textContent = err.message; }
  }

  async function submitPasswordChange(payload) {
    const response = await fetch("/api/auth/password/change", {
      method: "POST",
      headers: { "Content-Type": "application/json", ...authHeaders() },
      body: JSON.stringify(payload),
    });
    const body = await response.json().catch(() => ({}));
    const code = body.code || body.error;
    if (!response.ok && code === "recovery_enrollment_required") {
      await enterEnrollmentGate();
      return null;
    }
    if (!response.ok) {
      throw new Error(userFacingAuthError(code, "Password change failed."));
    }
    return body;
  }

  async function finishAuthenticated(body) {
    saveSession({
      token: body.token,
      userId: body.user_id,
      name: body.name,
      expiresAt: body.password_expires_at,
      recoveryEnrolled: !!body.recovery_enrolled,
    });
    pendingPasswordChange = null;
    ["mobile_current_password", "mobile_new_password", "mobile_confirm_password"].forEach(id => {
      if (byId(id)) byId(id).value = "";
    });
    hideLifecycleForms();
    const loginBtn = byId("mobile_login_button");
    const forgot = byId("mobile_forgot_password_btn");
    if (loginBtn) loginBtn.hidden = false;
    if (forgot) forgot.hidden = false;
    setAuthState("authenticated");
    showAuthenticated(true);
    await loadDashboard();
    const deep = window.HCConsumerNav && HCConsumerNav.peekDeepLink();
    if (deep) await showView(deep);
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
    pendingPasswordChange = {
      current_password: byId("mobile_current_password").value,
      new_password: next,
      confirm_password: byId("mobile_confirm_password").value,
    };
    if (!(session && session.recoveryEnrolled)) {
      await enterEnrollmentGate();
      return;
    }
    try {
      const body = await submitPasswordChange(pendingPasswordChange);
      if (body) await finishAuthenticated(body);
    } catch (err) { error.textContent = err.message; }
  }

  async function submitEnrollment(event) {
    event.preventDefault();
    const error = byId("mobile_enroll_error");
    error.textContent = "";
    const answers = collectAnswers("mobile_enroll");
    const invalid = validateEnrollment(answers);
    if (invalid) {
      error.textContent = invalid;
      return;
    }
    if (!pendingPasswordChange) {
      enterPasswordGate();
      return;
    }
    try {
      const body = await submitPasswordChange({
        ...pendingPasswordChange,
        recovery_answers: answers,
      });
      if (body) await finishAuthenticated(body);
    } catch (err) { error.textContent = err.message; }
  }

  async function logout(notifyServer) {
    const token = session && session.token;
    saveSession(null);
    summary = null;
    records = [];
    preferences = null;
    if (window.HCConsumerNav) {
      setAuthState("login");
      HCConsumerNav.reset();
    }
    document.querySelectorAll("[data-mobile-content]").forEach(node => node.replaceChildren());
    const snap = byId("hc_health_snapshot");
    if (snap) snap.replaceChildren();
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
    const attentionCount = Number(summary.active_warnings_count || 0);
    text(target, `Overall status: ${label(summary.overall_status)}`, "mobile-dash-meta");
    text(target, `${attentionCount} attention item${attentionCount === 1 ? "" : "s"}`, "mobile-dash-meta");
    text(
      target,
      `${Number(imported.records_count || 0)} records · ${Number(status.measurements_count || 0)} measurements`,
      "mobile-dash-meta"
    );
    byId("mobile_identity").textContent = session.name && session.name !== session.userId
      ? `Signed in as ${session.name} (Patient ID: ${session.userId})`
      : `Signed in as Patient ID ${session.userId}`;
    const passwordStatus = byId("mobile_password_status");
    if (passwordStatus) {
      passwordStatus.textContent = session && session.expiresAt
        ? ("Password expires " + String(session.expiresAt).slice(0, 10) + ".")
        : "";
    }
    byId("mobile_theme").value = preferences.theme === "dark" ? "dark" : "light";
    byId("mobile_priority_metric").value = preferences.priority_metric || "";
    if (window.HCHealthSnapshot && typeof HCHealthSnapshot.refresh === "function") {
      await HCHealthSnapshot.refresh();
    }
  }

  async function loadRecords() {
    const body = await request("/api/records?surface=clinical_document");
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
    const close = document.createElement("button");
    close.type = "button";
    close.className = "secondary";
    close.setAttribute("data-hc-back", "overlay");
    close.setAttribute("data-hc-back-overlay", "true");
    close.textContent = "← Back";
    detail.appendChild(close);
    text(detail, "Loading record details…", "muted");
    card.appendChild(detail);
    const closeDetail = () => {
      detail.remove();
      if (window.HCConsumerNav) HCConsumerNav.dismissOverlay("mobile-record-detail");
    };
    if (window.HCConsumerNav) HCConsumerNav.pushOverlay("mobile-record-detail", closeDetail);
    try {
      const record = await request(`/api/records/${encodeURIComponent(documentId)}`);
      detail.querySelectorAll("p").forEach(node => node.remove());
      text(detail, `Source: ${(record.source_provenance || {}).source_system || "Not available"}`);
      text(detail, `${(record.extracted_measurements || []).length} extracted measurements`);
      text(detail, `${(record.trend_references || []).length} related trends`);
      text(detail, `${(record.ai_observations || []).length} AI observations`);
      text(detail, `${(record.timeline_events || []).length} timeline events`);
      text(detail, `${(record.evidence_references || []).length} evidence references`);
      (record.extracted_measurements || []).forEach(item => text(detail, `${label(item.metric)}: ${item.value == null ? "Not available" : item.value} ${item.units || ""}`));
    } catch (error) {
      detail.querySelectorAll("p").forEach(node => node.remove());
      text(detail, error.message, "bad");
    }
  }

  async function showView(name, options) {
    options = options || {};
    if (window.HCConsumerNav && HCConsumerNav.isSecurityGate && HCConsumerNav.isSecurityGate()) {
      return;
    }
    if (!options.fromNav && window.HCConsumerNav) HCConsumerNav.note(name);
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
      if (name === "settings") {
        await loadCatalog();
        renderQuestionPickers("mobile_settings_recovery_questions", "mobile_settings_enroll");
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
      session.userId = current.user_id;
      session.name = current.name;
      session.expiresAt = current.password_expires_at || current.password_expiry_date;
      session.recoveryEnrolled = !!current.recovery_enrolled;
      if (current.must_change_password || current.scope !== "full") {
        enterPasswordGate();
        return;
      }
      setAuthState("authenticated");
      showAuthenticated(true);
      await showView("dashboard");
      const deep = window.HCConsumerNav && HCConsumerNav.peekDeepLink();
      if (deep) await showView(deep, { fromNav: false });
    } catch (_) { await logout(false); }
  }

  function showRecoveryFlow() {
    setSecurityGate(true);
    showAuthenticated(false);
    hideSignInControls();
    hideLifecycleForms();
    byId("mobile_recovery_flow").hidden = false;
    byId("mobile_recovery_start_step").hidden = false;
    byId("mobile_recovery_verify_step").hidden = true;
    byId("mobile_recovery_complete_step").hidden = true;
    byId("mobile_recovery_error").textContent = "";
    recoveryId = null;
    recoveryToken = null;
  }

  function cancelRecoveryFlow() {
    recoveryId = null;
    recoveryToken = null;
    setSecurityGate(false);
    showLoginExtras();
    showAuthenticated(false);
  }

  async function handleRecoveryStart() {
    const error = byId("mobile_recovery_error");
    error.textContent = "";
    const body = await fetch("/api/auth/recovery/start", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ user_id: byId("mobile_recovery_user_id").value.trim() }),
    }).then(res => res.json());
    recoveryId = body.recovery_id;
    renderRecoveryPrompts("mobile_recovery_question_fields", body.questions || []);
    byId("mobile_recovery_start_step").hidden = true;
    byId("mobile_recovery_verify_step").hidden = false;
  }

  async function handleRecoveryVerify() {
    const error = byId("mobile_recovery_error");
    error.textContent = "";
    const response = await fetch("/api/auth/recovery/verify", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        recovery_id: recoveryId,
        answers: collectPromptAnswers("mobile_recovery_question_fields"),
      }),
    });
    const body = await response.json();
    if (!response.ok) {
      error.textContent = "Recovery could not be completed.";
      return;
    }
    recoveryToken = body.token;
    byId("mobile_recovery_verify_step").hidden = true;
    byId("mobile_recovery_complete_step").hidden = false;
  }

  async function handleRecoveryComplete() {
    const error = byId("mobile_recovery_error");
    const next = byId("mobile_recovery_new_password").value;
    const confirm = byId("mobile_recovery_confirm_password").value;
    error.textContent = "";
    if (next.length < 8 || next !== confirm) {
      error.textContent = "New passwords must match and contain at least 8 characters.";
      return;
    }
    const response = await fetch("/api/auth/recovery/complete", {
      method: "POST",
      headers: { "Content-Type": "application/json", Authorization: "Bearer " + (recoveryToken || "") },
      body: JSON.stringify({ new_password: next, confirm_password: confirm }),
    });
    const body = await response.json();
    if (!response.ok) {
      error.textContent = userFacingAuthError(body.code || body.error, "Recovery could not be completed.");
      return;
    }
    saveSession(null);
    cancelRecoveryFlow();
    byId("mobile_login_error").textContent = "Password updated. Sign in with your new password.";
  }

  async function handleSettingsPassword(event) {
    event.preventDefault();
    const error = byId("mobile_settings_password_error");
    const next = byId("mobile_settings_new").value;
    error.textContent = "";
    if (next.length < 8 || next !== byId("mobile_settings_confirm").value) {
      error.textContent = "New passwords must match and contain at least 8 characters.";
      return;
    }
    try {
      const body = await request("/api/auth/password/change", {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          current_password: byId("mobile_settings_current").value,
          new_password: next,
          confirm_password: byId("mobile_settings_confirm").value,
        }),
      });
      saveSession({ token: body.token, userId: body.user_id, name: body.name, expiresAt: body.password_expires_at });
      ["mobile_settings_current", "mobile_settings_new", "mobile_settings_confirm"].forEach(id => { byId(id).value = ""; });
    } catch (err) { error.textContent = err.message; }
  }

  async function handleSettingsRecovery(event) {
    event.preventDefault();
    const error = byId("mobile_settings_recovery_error");
    error.textContent = "";
    try {
      await request("/api/auth/recovery/enroll", {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          current_password: byId("mobile_settings_recovery_current").value,
          recovery_answers: collectAnswers("mobile_settings_enroll"),
        }),
      });
      byId("mobile_settings_recovery_current").value = "";
    } catch (err) { error.textContent = err.message; }
  }

  byId("mobile_login_button").addEventListener("click", login);
  byId("mobile_password_change").addEventListener("submit", changePassword);
  byId("mobile_recovery_enroll").addEventListener("submit", submitEnrollment);
  byId("mobile_forgot_password_btn").addEventListener("click", showRecoveryFlow);
  byId("mobile_recovery_start_btn").addEventListener("click", () => handleRecoveryStart().catch(err => {
    byId("mobile_recovery_error").textContent = err.message;
  }));
  byId("mobile_recovery_verify_btn").addEventListener("click", () => handleRecoveryVerify().catch(err => {
    byId("mobile_recovery_error").textContent = "Recovery could not be completed.";
  }));
  byId("mobile_recovery_complete_btn").addEventListener("click", () => handleRecoveryComplete().catch(err => {
    byId("mobile_recovery_error").textContent = err.message;
  }));
  byId("mobile_recovery_cancel_btn").addEventListener("click", cancelRecoveryFlow);
  byId("mobile_settings_password_form").addEventListener("submit", handleSettingsPassword);
  byId("mobile_settings_recovery_form").addEventListener("submit", handleSettingsRecovery);
  byId("mobile_logout_button").addEventListener("click", () => logout(true));
  byId("mobile_upload_button").addEventListener("click", upload);
  byId("mobile_save_preferences").addEventListener("click", savePreferences);
  document.querySelectorAll("[data-mobile-view]").forEach(button => {
    button.setAttribute("aria-pressed", "false");
    button.addEventListener("click", () => showView(button.dataset.mobileView));
  });
  window.HCConsumerNavAdapter = {
    activate: function (route, options) {
      return showView(route, { fromNav: true, fromBack: !!(options && options.fromBack) });
    }
  };
  restore();
}());
