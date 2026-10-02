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
  let recentReceivedRecord = null;
  const recordPreviewUrls = new Set();
  let pendingUploadPayload = null;
  let pendingUploadPreviewUrl = null;
  let uploadPreviewController = null;
  let uploadReviewGeneration = 0;

  const byId = id => document.getElementById(id);
  const authHeaders = () => session ? { Authorization: `Bearer ${session.token}` } : {};

  function isJsonContentType(value) {
    const base = String(value || "").split(";")[0].trim().toLowerCase();
    return base === "application/json";
  }

  function safeApiPath(path) {
    const raw = String(path || "");
    try {
      if (/^[a-zA-Z][a-zA-Z0-9+.-]*:/.test(raw)) {
        return new URL(raw).pathname || "/";
      }
    } catch (_) {}
    return raw.split("#")[0].split("?")[0] || "/";
  }

  function safeFinalUrl(response) {
    try {
      const raw = String((response && response.url) || "");
      if (!raw) return "unknown";
      if (/^[a-zA-Z][a-zA-Z0-9+.-]*:/.test(raw)) {
        const parsed = new URL(raw);
        return parsed.origin + parsed.pathname;
      }
      return raw.split("#")[0].split("?")[0] || "unknown";
    } catch (_) {
      return "unknown";
    }
  }

  function jsonContractError(code, path, response) {
    const status = response && response.status != null ? String(response.status) : "unknown";
    const headerGet = response && response.headers && response.headers.get;
    const contentType = headerGet ? String(response.headers.get("content-type") || "") : "";
    return new Error(
      code +
        " path=" + safeApiPath(path) +
        " status=" + status +
        " content_type=" + contentType +
        " final_url=" + safeFinalUrl(response)
    );
  }

  async function parseJsonResponse(response, path) {
    const contract = globalThis.HCMobileJsonContract;
    if (contract && typeof contract.parseJsonResponse === "function" && contract.parseJsonResponse !== parseJsonResponse) {
      return contract.parseJsonResponse(response, path);
    }
    const redirected = !!(response && response.redirected);
    const headerGet = response && response.headers && response.headers.get;
    const contentType = headerGet ? String(response.headers.get("content-type") || "") : "";
    if (redirected && !isJsonContentType(contentType)) {
      throw jsonContractError("API_RESPONSE_NOT_JSON", path, response);
    }
    if (!isJsonContentType(contentType)) {
      throw jsonContractError("API_RESPONSE_NOT_JSON", path, response);
    }
    let text;
    try {
      text = await response.text();
    } catch (_) {
      throw jsonContractError("JSON_PARSE_FAILED", path, response);
    }
    try {
      return JSON.parse(text);
    } catch (_) {
      throw jsonContractError("JSON_PARSE_FAILED", path, response);
    }
  }

  try {
    globalThis.HCMobileJsonContract = Object.assign({}, globalThis.HCMobileJsonContract || {}, {
      isJsonContentType,
      safeApiPath,
      safeFinalUrl,
      parseJsonResponse,
      jsonContractError,
    });
  } catch (_) {}

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
    if (window.HCScreenshotPolicy && typeof window.HCScreenshotPolicy.setRoute === "function") {
      window.HCScreenshotPolicy.setRoute(active ? "password_recovery" : "dashboard");
    }
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
    const response = await fetch(path, {
      ...(options || {}),
      headers: { Accept: "application/json", ...authHeaders(), ...((options || {}).headers || {}) },
    });
    const gated = window.HCConsumerNav && HCConsumerNav.isSecurityGate && HCConsumerNav.isSecurityGate();
    if (session && (response.status === 401 || response.status === 403) && !gated) {
      await logout(false);
      throw new Error(response.status === 403 ? "Password change required" : "Session expired");
    }
    const body = await parseJsonResponse(response, path);
    if (!response.ok) throw new Error(userFacingAuthError(body.code || body.error, "Request failed"));
    return body;
  }

  try {
    if (globalThis.HCMobileJsonContract) globalThis.HCMobileJsonContract.request = request;
    if (globalThis.HCMobileJsonContract) globalThis.HCMobileJsonContract.inspectSettingsSelects = inspectSettingsSelects;
  } catch (_) {}

  function showAuthenticated(active) {
    byId("mobile_login").hidden = active;
    byId("mobile_consumer").hidden = !active;
  }

  async function loadCatalog() {
    if (catalog && catalog.length) return catalog;
    const response = await fetch("/api/auth/recovery/catalog", {
      headers: { Accept: "application/json" },
    });
    const body = await parseJsonResponse(response, "/api/auth/recovery/catalog");
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
      if (body.must_change_password) {
        enterPasswordGate();
        return;
      }
      setAuthState("authenticated");
      showAuthenticated(true);
      await loadDashboard();
      byId("mobile_password").value = "";
      const deep = window.HCConsumerNav && HCConsumerNav.peekDeepLink();
      if (deep) await showView(deep);
    } catch (err) { error.textContent = err.message; }
  }

  async function submitPasswordChange(payload) {
    const response = await fetch("/api/auth/password/change", {
      method: "POST",
      headers: { "Content-Type": "application/json", Accept: "application/json", ...authHeaders() },
      body: JSON.stringify(payload),
    });
    const body = await parseJsonResponse(response, "/api/auth/password/change");
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
    byId("mobile_password").value = "";
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
    recentReceivedRecord = null;
    clearRecordPreviews();
    clearUploadReview(true);
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

  function clearRecordPreviews() {
    recordPreviewUrls.forEach(url => URL.revokeObjectURL(url));
    recordPreviewUrls.clear();
    document.querySelectorAll("img[data-record-preview]").forEach(image => {
      image.removeAttribute("src");
    });
  }

  function friendlyRecordSource(source) {
    const labels = {
      health_connect_companion: "Health Connect",
      healthchecker_plus: "HealthChecker upload",
      manual_upload: "HealthChecker upload",
      hc313a_gmail: "Gmail import",
      gmail: "Gmail import",
      hc312a_intake: "Imported file",
    };
    return labels[String(source || "").trim().toLowerCase()] || "Source not available";
  }

  function recordFileType(record) {
    const name = String(record.original_filename || record.display_title || "").toLowerCase();
    const extension = name.split(".").pop();
    if (extension === "pdf") return "PDF";
    if (["png", "jpg", "jpeg", "webp", "gif", "tif", "tiff", "bmp"].includes(extension)) return "Image";
    if (extension === "json") return "Data file";
    return "Record";
  }

  function recordDate(value) {
    if (!value) return "Not available";
    const raw = String(value);
    if (/^\d{4}-\d{2}-\d{2}$/.test(raw)) return raw;
    const parsed = new Date(raw);
    if (Number.isNaN(parsed.getTime())) return "Not available";
    return new Intl.DateTimeFormat(undefined, {
      dateStyle: "medium",
      timeStyle: raw.includes("T") ? "short" : undefined,
    }).format(parsed);
  }

  async function loadRecordPreview(record, image, fallback) {
    const path = `/api/records/thumbnail/${encodeURIComponent(record.document_id)}`;
    let response;
    try {
      response = await fetch(path, {
        cache: "no-store",
        headers: { Accept: "image/png", ...authHeaders() },
      });
    } catch (error) {
      if (!(error instanceof TypeError)) throw error;
      fallback.textContent = "Preview unavailable";
      return;
    }
    const gated = window.HCConsumerNav && HCConsumerNav.isSecurityGate && HCConsumerNav.isSecurityGate();
    if (session && (response.status === 401 || response.status === 403) && !gated) {
      await logout(false);
      return;
    }
    if (!response.ok || !String(response.headers.get("content-type") || "").toLowerCase().startsWith("image/png")) {
      fallback.textContent = `${recordFileType(record)} preview unavailable`;
      return;
    }
    const blob = await response.blob();
    if (!image.isConnected) return;
    const url = URL.createObjectURL(blob);
    recordPreviewUrls.add(url);
    image.src = url;
    image.hidden = false;
    fallback.hidden = true;
  }

  function appendRecordMeta(target, labelText, value) {
    if (!value) return;
    const line = document.createElement("p");
    line.className = "mobile-recent-record-meta";
    const labelNode = document.createElement("strong");
    labelNode.textContent = `${labelText}: `;
    line.append(labelNode, document.createTextNode(value));
    target.appendChild(line);
  }

  function renderRecentRecord(target, recentRecords) {
    clearRecordPreviews();
    recentReceivedRecord = recentRecords[0] || null;
    const viewLast = byId("mobile_view_last_record");
    viewLast.disabled = !recentReceivedRecord;
    if (!recentReceivedRecord) {
      text(target, "No records have been received yet.", "muted");
      return;
    }

    const record = recentReceivedRecord;
    const card = document.createElement("article");
    card.className = "mobile-recent-record";
    const heading = document.createElement("h2");
    heading.textContent = "Recent record";
    const previewButton = document.createElement("button");
    previewButton.type = "button";
    previewButton.className = "mobile-record-thumbnail";
    previewButton.setAttribute("aria-label", `Open ${record.display_title || "recent record"}`);
    const fallback = document.createElement("span");
    fallback.className = "mobile-record-thumbnail-fallback";
    fallback.textContent = recordFileType(record);
    const image = document.createElement("img");
    image.alt = `${record.display_title || "Recent record"} preview`;
    image.hidden = true;
    image.dataset.recordPreview = "true";
    previewButton.append(fallback, image);
    previewButton.addEventListener("click", () => openRecord(record.document_id));

    const content = document.createElement("div");
    content.className = "mobile-recent-record-content";
    const title = document.createElement("h3");
    title.className = "mobile-recent-record-title";
    title.textContent = record.display_title || "Health record";
    content.appendChild(title);
    appendRecordMeta(content, "Source", friendlyRecordSource(record.source_system));
    appendRecordMeta(content, "Document date", recordDate(record.source_document_date));
    appendRecordMeta(content, "Clinical date", recordDate(record.measured_at));
    appendRecordMeta(content, "Received", recordDate(record.imported_at));
    const open = document.createElement("button");
    open.type = "button";
    open.className = "secondary mobile-record-open";
    open.textContent = "Open record";
    open.addEventListener("click", () => openRecord(record.document_id));
    content.appendChild(open);
    card.append(heading, previewButton, content);
    target.appendChild(card);
    loadRecordPreview(record, image, fallback);
  }

  async function loadDashboard() {
    summary = await request("/api/dashboard/summary");
    preferences = await request("/api/dashboard/preferences");
    setTheme(preferences.theme);
    const target = clearContent("mobile_dashboard");
    const status = widget("status_summary").payload;
    const imported = widget("import_wizard").payload;
    const attentionCount = Number(summary.active_warnings_count || 0);
    text(target, `Overall status: ${summary.overall_status_label || status.status_label_override || label(summary.overall_status)}`, "mobile-dash-meta");
    text(target, `${attentionCount} attention item${attentionCount === 1 ? "" : "s"}`, "mobile-dash-meta");
    text(
      target,
      `${Number(imported.records_count || 0)} records · ${Number(status.measurements_count || 0)} measurements`,
      "mobile-dash-meta"
    );
    renderRecentRecord(target, imported.recent_records || []);
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
      card.dataset.recordCard = "true";
      card.dataset.documentId = row.document_id;
      text(card, row.display_title || row.original_filename || row.title || "Health record");
      text(card, `${label(row.primary_category)} · ${label(row.status)}`, "muted");
      const button = document.createElement("button");
      button.type = "button";
      button.textContent = "Open record";
      button.addEventListener("click", () => loadRecordDetail(row.document_id, card));
      card.appendChild(button);
    });
  }

  async function openRecord(documentId) {
    await showView("records");
    const cards = Array.from(byId("mobile_records").querySelectorAll("[data-record-card]"));
    const card = cards.find(item => item.dataset.documentId === documentId);
    if (card) await loadRecordDetail(documentId, card);
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
      text(detail, `Source: ${friendlyRecordSource((record.source_provenance || {}).source_system)}`);
      text(detail, `Document date: ${recordDate(record.source_document_date)}`);
      text(detail, `Clinical date: ${recordDate(record.measured_at)}`);
      text(detail, `Received: ${recordDate(record.imported_at)}`);
      const preview = document.createElement("div");
      preview.className = "mobile-record-detail-preview";
      const image = document.createElement("img");
      image.alt = `${record.display_title || "Record"} preview`;
      image.hidden = true;
      image.dataset.recordPreview = "true";
      const fallback = document.createElement("p");
      fallback.className = "muted";
      fallback.textContent = `${recordFileType(record)} preview loading…`;
      preview.append(image, fallback);
      detail.appendChild(preview);
      loadRecordPreview(record, image, fallback);
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

  function setMobileStatus(message) {
    const node = byId("mobile_status");
    if (node) node.textContent = message || "";
  }

  const SURFACE_UNAVAILABLE = {
    dashboard: "Dashboard data unavailable.",
    records: "Records data unavailable.",
    trends: "Trends data unavailable.",
    observations: "Observations data unavailable.",
    timeline: "Timeline data unavailable.",
    reports: "Reports data unavailable.",
    settings: "Settings data unavailable.",
    import: "Import is ready.",
  };

  function surfaceUnavailable(name, error) {
    const base = SURFACE_UNAVAILABLE[name] || "This screen is unavailable.";
    const msg = String((error && error.message) || "");
    if (msg.indexOf("API_RESPONSE_NOT_JSON ") === 0 || msg.indexOf("JSON_PARSE_FAILED ") === 0) {
      return base;
    }
    if (/Unexpected token|DOCTYPE|is not valid JSON/i.test(msg)) {
      return base;
    }
    return base;
  }

  function ensureSelectInteractive(el) {
    if (!el) return;
    el.disabled = false;
    el.removeAttribute("disabled");
    el.removeAttribute("aria-disabled");
    el.removeAttribute("inert");
    try {
      el.style.pointerEvents = "auto";
      el.style.touchAction = "manipulation";
      el.style.position = "relative";
      el.style.zIndex = "6";
    } catch (_) {}
  }

  function inspectSettingsSelects() {
    const ids = ["mobile_theme", "mobile_priority_metric"];
    const results = {};
    ids.forEach(function (id) {
      const el = byId(id);
      ensureSelectInteractive(el);
      let rect = { left: 0, top: 0, width: 0, height: 0 };
      try {
        if (el && el.getBoundingClientRect) rect = el.getBoundingClientRect();
      } catch (_) {}
      const x = rect.left + (rect.width / 2);
      const y = rect.top + (rect.height / 2);
      let hit = null;
      try {
        if (typeof document.elementFromPoint === "function") {
          hit = document.elementFromPoint(x, y);
        }
      } catch (_) {}
      const contained = !!(el && hit && (hit === el || (el.contains && el.contains(hit))));
      results[id] = {
        enabled: !!(el && !el.disabled),
        pointerEvents: !el || String((el.style && el.style.pointerEvents) || "") !== "none",
        hitOk: !el || !hit || contained,
        blocking: (hit && !contained) ? String(hit.id || hit.className || hit.tagName || "unknown") : "",
      };
    });
    try { globalThis.HCMobileSettingsHitTest = results; } catch (_) {}
    return results;
  }

  async function showView(name, options) {
    options = options || {};
    if (window.HCConsumerNav && HCConsumerNav.isSecurityGate && HCConsumerNav.isSecurityGate()) {
      return;
    }
    if (!options.fromNav && window.HCConsumerNav) HCConsumerNav.note(name);
    if (name !== "dashboard") clearRecordPreviews();
    if (name !== "import") clearUploadReview(true);
    document.querySelectorAll("[data-mobile-panel]").forEach(panel => { panel.hidden = panel.id !== `mobile_${name}`; });
    document.querySelectorAll("[data-mobile-view]").forEach(button => button.setAttribute("aria-pressed", String(button.dataset.mobileView === name)));
    if (window.HCScreenshotPolicy && typeof window.HCScreenshotPolicy.setRoute === "function") {
      window.HCScreenshotPolicy.setRoute(name);
    }
    if (name !== "dashboard" && window.HCHealthSnapshot && typeof HCHealthSnapshot.closeDrillDown === "function") {
      try { HCHealthSnapshot.closeDrillDown(); } catch (_) {}
    }
    setMobileStatus("Loading…");
    let statusCleared = false;
    const fail = (surface, error, emptyTarget, emptyMessage) => {
      if (emptyTarget) text(emptyTarget, emptyMessage || SURFACE_UNAVAILABLE[surface], "muted");
      setMobileStatus(surfaceUnavailable(surface, error));
      statusCleared = false;
    };
    const ok = () => {
      if (!statusCleared) {
        setMobileStatus("");
        statusCleared = true;
      }
    };
    if (name === "dashboard") {
      try {
        await loadDashboard();
        ok();
      } catch (error) {
        fail("dashboard", error, clearContent("mobile_dashboard"), "Dashboard data unavailable.");
      }
      return;
    }
    if (name === "records") {
      try {
        await loadRecords();
        ok();
      } catch (error) {
        fail("records", error, clearContent("mobile_records"), "Records data unavailable.");
      }
      return;
    }
    if (name === "trends") {
      try {
        if (!summary) await loadDashboard();
        const trends = widget("trends_widget").payload.trends || {};
        renderList(clearContent("mobile_trends"), Object.entries(trends), "No trends available.", (card, row) => {
          const trend = row[1] || {};
          text(card, label(row[0]));
          text(card, `${trend.label || trend.direction || "Not enough data"} · Latest ${trend.latest == null ? "not available" : trend.latest} · ${trend.sample_count || 0} samples`, "muted");
        });
        ok();
      } catch (error) {
        fail("trends", error, clearContent("mobile_trends"), "Trends data unavailable.");
      }
      return;
    }
    if (name === "observations") {
      try {
        if (!summary) await loadDashboard();
        const observations = widget("key_observations").payload.observations || [];
        renderList(clearContent("mobile_observations"), observations, "No observations available.", (card, row) => {
          text(card, row.fact || row.interpretation || "Observation");
          text(card, row.interpretation || row.explanation || "", "muted");
          text(card, row.safety_boundary_disclaimer || "Observational information only — not a diagnosis.", "muted");
        });
        ok();
      } catch (error) {
        fail("observations", error, clearContent("mobile_observations"), "Observations data unavailable.");
      }
      return;
    }
    if (name === "timeline") {
      try {
        const body = await request("/api/health-vault/timeline?unified=true");
        renderList(clearContent("mobile_timeline"), body.entries || [], "Your timeline is empty.", (card, row) => {
          text(card, row.date ? String(row.date).slice(0, 10) : "Timeline event");
          text(card, row.summary || row.trend_impact || row.event_type || "Health event", "muted");
          text(card, `Source: ${row.provenance || row.source || "Not available"}`, "muted");
        });
        ok();
      } catch (error) {
        fail("timeline", error, clearContent("mobile_timeline"), "Timeline data unavailable.");
      }
      return;
    }
    if (name === "reports") {
      try {
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
        ok();
      } catch (error) {
        fail("reports", error, clearContent("mobile_reports"), "Reports data unavailable.");
      }
      return;
    }
    if (name === "settings") {
      ensureSelectInteractive(byId("mobile_theme"));
      ensureSelectInteractive(byId("mobile_priority_metric"));
      inspectSettingsSelects();
      try {
        await loadCatalog();
        renderQuestionPickers("mobile_settings_recovery_questions", "mobile_settings_enroll");
        ok();
      } catch (error) {
        fail("settings", error);
      }
      inspectSettingsSelects();
      return;
    }
    if (name === "import") {
      ok();
    }
  }

  function base64ToBlob(base64, mimeType) {
    const binary = atob(base64 || "");
    const bytes = new Uint8Array(binary.length);
    for (let i = 0; i < binary.length; i++) bytes[i] = binary.charCodeAt(i);
    return new Blob([bytes], { type: mimeType || "application/octet-stream" });
  }

  // HC329: Chromium's WebView can fail (or hang, then reject fetch() with a
  // generic "Failed to fetch") when asked to stream a content:// blob
  // directly into a multipart body. When the native bridge is present, read
  // the selected document's bytes natively instead and hand fetch() a plain
  // in-memory Blob, which does not depend on the WebView re-resolving the
  // content:// URI at upload time. Falls back to the <input> File object when
  // no bridge is installed (e.g. desktop/browser development).
  async function readSelectedFileViaNativeBridge() {
    const bridge = window.HCNativeImport;
    if (!bridge || typeof bridge.readSelectedRecordBase64 !== "function") return null;
    let parsed;
    try {
      parsed = JSON.parse(bridge.readSelectedRecordBase64());
    } catch (_) {
      const err = new Error("Could not read the selected file.");
      err.uploadErrorCode = "native_import_bridge_invalid_response";
      throw err;
    }
    if (!parsed || parsed.ok !== true) {
      const code = (parsed && parsed.error) || "native_import_failed";
      const err = new Error(code);
      err.uploadErrorCode = code;
      throw err;
    }
    return { blob: base64ToBlob(parsed.base64, parsed.mime_type), name: parsed.name || "upload" };
  }

  function describeUploadError(error) {
    const code = error && error.uploadErrorCode;
    if (code === "no_file_selected") return "Choose a report first.";
    if (code === "file_too_large") return "That file is too large to upload.";
    if (code === "file_unreadable" || code === "read_failed" || code === "native_import_bridge_invalid_response") {
      return "Could not read the selected file. Try selecting it again.";
    }
    const message = String((error && error.message) || "");
    if (/^(API_RESPONSE_NOT_JSON|JSON_PARSE_FAILED)\b/.test(message)) {
      try { console.warn("mobile_upload_invalid_response", message); } catch (_) {}
      return "Unexpected response from the server. Please try again.";
    }
    if (error instanceof TypeError) {
      try { console.warn("mobile_upload_network_error", message); } catch (_) {}
      return "Upload could not reach the server. Check your connection and try again.";
    }
    return message || "Upload failed.";
  }

  function clearUploadReview(clearSelection) {
    uploadReviewGeneration += 1;
    if (uploadPreviewController) {
      uploadPreviewController.abort();
      uploadPreviewController = null;
    }
    pendingUploadPayload = null;
    if (pendingUploadPreviewUrl) {
      URL.revokeObjectURL(pendingUploadPreviewUrl);
      pendingUploadPreviewUrl = null;
    }
    const image = byId("mobile_upload_preview_image");
    image.removeAttribute("src");
    image.hidden = true;
    byId("mobile_upload_review").hidden = true;
    byId("mobile_upload_file_name").textContent = "";
    byId("mobile_upload_file_summary").textContent = "";
    byId("mobile_upload_preview_message").textContent = "";
    byId("mobile_upload_button").disabled = true;
    byId("mobile_cancel_upload_review").disabled = false;
    if (clearSelection) byId("mobile_record_file").value = "";
    return uploadReviewGeneration;
  }

  function formatFileSize(bytes) {
    if (bytes < 1024) return `${bytes} B`;
    if (bytes < 1024 * 1024) return `${Math.max(1, Math.round(bytes / 1024))} KB`;
    return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
  }

  function selectedFileKind(name, mimeType) {
    const lowerName = String(name || "").toLowerCase();
    const mime = String(mimeType || "").toLowerCase();
    if (mime === "application/pdf" || lowerName.endsWith(".pdf")) return "pdf";
    if (
      ["image/png", "image/jpeg"].includes(mime) ||
      /\.(png|jpe?g)$/i.test(lowerName)
    ) return "image";
    if (mime === "application/json" || lowerName.endsWith(".json")) return "data";
    return "other";
  }

  async function loadSelectedFilePreview(payload, kind, generation) {
    const image = byId("mobile_upload_preview_image");
    const message = byId("mobile_upload_preview_message");
    if (kind === "data") {
      message.textContent = "Preview is not available for this data file. Confirm its name and type before importing.";
      return;
    }
    if (kind === "other") {
      message.textContent = "No safe preview is available for this file type. Confirm its name and type before importing.";
      return;
    }

    message.textContent = kind === "pdf" ? "Creating a secure first-page preview…" : "Creating a secure image preview…";
    const form = new FormData();
    form.append("file", payload.blob, payload.name);
    uploadPreviewController = new AbortController();
    try {
      const response = await fetch("/api/records/preview", {
        method: "POST",
        body: form,
        cache: "no-store",
        signal: uploadPreviewController.signal,
        headers: { Accept: "image/png", ...authHeaders() },
      });
      uploadPreviewController = null;
      if (generation !== uploadReviewGeneration) return;
      const gated = window.HCConsumerNav && HCConsumerNav.isSecurityGate && HCConsumerNav.isSecurityGate();
      if (session && (response.status === 401 || response.status === 403) && !gated) {
        await logout(false);
        return;
      }
      if (!response.ok || !String(response.headers.get("content-type") || "").toLowerCase().startsWith("image/png")) {
        message.textContent = response.status === 413
          ? "This file is too large to preview safely. Confirm the file name and type before importing."
          : "Preview unavailable. Confirm the file name and type before importing.";
        return;
      }
      const preview = await response.blob();
      if (generation !== uploadReviewGeneration) return;
      pendingUploadPreviewUrl = URL.createObjectURL(preview);
      image.src = pendingUploadPreviewUrl;
      image.alt = kind === "pdf" ? "First page of selected PDF" : "Selected image preview";
      image.hidden = false;
      message.textContent = kind === "pdf" ? "First page preview" : "Image preview";
    } catch (error) {
      uploadPreviewController = null;
      if (generation !== uploadReviewGeneration) return;
      message.textContent = error instanceof TypeError
        ? "Preview could not reach the service. Confirm the file name and type before importing."
        : "Preview unavailable. Confirm the file name and type before importing.";
    }
  }

  async function reviewSelectedFile() {
    const inputFile = byId("mobile_record_file").files[0];
    const generation = clearUploadReview(false);
    byId("mobile_upload_status").textContent = "";
    if (!inputFile && !window.HCNativeImport) return;

    byId("mobile_upload_review").hidden = false;
    byId("mobile_upload_preview_message").textContent = "Preparing file review…";
    let payload;
    try {
      payload = await readSelectedFileViaNativeBridge();
    } catch (error) {
      if (generation !== uploadReviewGeneration) return;
      byId("mobile_upload_preview_message").textContent = describeUploadError(error);
      return;
    }
    if (!payload && inputFile) payload = { blob: inputFile, name: inputFile.name };
    if (generation !== uploadReviewGeneration) return;
    if (!payload) {
      byId("mobile_upload_review").hidden = true;
      return;
    }

    pendingUploadPayload = payload;
    const kind = selectedFileKind(payload.name, payload.blob.type);
    byId("mobile_upload_file_name").textContent = payload.name || "Selected record";
    const typeLabel = kind === "pdf" ? "PDF" : kind === "image" ? "Image" : kind === "data" ? "Data file" : "File type not recognized";
    byId("mobile_upload_file_summary").textContent = `${typeLabel} · ${formatFileSize(payload.blob.size)}`;
    byId("mobile_upload_button").disabled = false;
    await loadSelectedFilePreview(payload, kind, generation);
  }

  async function upload() {
    const status = byId("mobile_upload_status");
    if (!pendingUploadPayload) {
      status.textContent = "Choose and review a record before confirming the upload.";
      return;
    }
    const payload = pendingUploadPayload;
    byId("mobile_upload_button").disabled = true;
    byId("mobile_record_file").disabled = true;
    byId("mobile_cancel_upload_review").disabled = true;
    status.textContent = "Uploading securely and processing the confirmed record…";
    const form = new FormData();
    form.append("file", payload.blob, payload.name);
    try {
      const result = await request("/api/records/upload", { method: "POST", body: form });
      clearUploadReview(true);
      status.textContent = `Upload ${label(result.status || "accepted")}. ${result.document_id ? "The record is now available in Records." : "The record is processing."}`;
      summary = null; records = [];
    } catch (error) {
      status.textContent = describeUploadError(error);
      byId("mobile_upload_button").disabled = !pendingUploadPayload;
    } finally {
      byId("mobile_record_file").disabled = false;
      byId("mobile_cancel_upload_review").disabled = false;
      byId("mobile_upload_button").disabled = !pendingUploadPayload;
    }
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
    const response = await fetch("/api/auth/recovery/start", {
      method: "POST", headers: { "Content-Type": "application/json", Accept: "application/json" },
      body: JSON.stringify({ user_id: byId("mobile_recovery_user_id").value.trim() }),
    });
    const body = await parseJsonResponse(response, "/api/auth/recovery/start");
    recoveryId = body.recovery_id;
    renderRecoveryPrompts("mobile_recovery_question_fields", body.questions || []);
    byId("mobile_recovery_start_step").hidden = true;
    byId("mobile_recovery_verify_step").hidden = false;
  }

  async function handleRecoveryVerify() {
    const error = byId("mobile_recovery_error");
    error.textContent = "";
    const response = await fetch("/api/auth/recovery/verify", {
      method: "POST", headers: { "Content-Type": "application/json", Accept: "application/json" },
      body: JSON.stringify({
        recovery_id: recoveryId,
        answers: collectPromptAnswers("mobile_recovery_question_fields"),
      }),
    });
    const body = await parseJsonResponse(response, "/api/auth/recovery/verify");
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
      headers: { "Content-Type": "application/json", Accept: "application/json", Authorization: "Bearer " + (recoveryToken || "") },
      body: JSON.stringify({ new_password: next, confirm_password: confirm }),
    });
    const body = await parseJsonResponse(response, "/api/auth/recovery/complete");
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

  byId("mobile_login_form").addEventListener("submit", event => {
    event.preventDefault();
    login();
  });
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
  byId("mobile_record_file").addEventListener("change", reviewSelectedFile);
  byId("mobile_cancel_upload_review").addEventListener("click", () => {
    clearUploadReview(true);
    byId("mobile_upload_status").textContent = "Selection canceled. Nothing was imported.";
  });
  byId("mobile_add_record").addEventListener("click", () => showView("import"));
  byId("mobile_view_last_record").addEventListener("click", () => {
    if (recentReceivedRecord) openRecord(recentReceivedRecord.document_id);
  });
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
