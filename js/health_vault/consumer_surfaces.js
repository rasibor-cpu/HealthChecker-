/* HC-321B authenticated, API-backed desktop consumer surfaces. */
(function (global) {
  "use strict";

  const SCREEN_LOADERS = {
    consumer_trends_screen: loadTrends,
    consumer_observations_screen: loadObservations,
    consumer_timeline_screen: loadTimeline,
    consumer_reports_screen: loadReport,
    consumer_settings_screen: loadSettings,
  };

  const NAV_COOLDOWN_MS = 1200;
  let lastNavAt = 0;
  let lastNavScreen = "";

  function dashboard() { return global.HCConsumerDashboard; }
  function headers() { return dashboard() ? dashboard().getAuthorizationHeaders() : {}; }
  function authenticated() { return !!(dashboard() && dashboard().token); }

  async function request(path) {
    const response = await fetch(path, { headers: headers(), cache: "no-store" });
    if (response.status === 401 || response.status === 403) {
      if (dashboard()) dashboard().handleLogout();
      throw new Error("Your session expired. Please sign in again.");
    }
    const body = await response.json();
    if (!response.ok) throw new Error(body.error || "HealthChecker could not load this view.");
    return body;
  }

  function parts(screenId) {
    const root = document.getElementById(screenId);
    return {
      root,
      state: root && root.querySelector("[data-consumer-state]"),
      content: root && root.querySelector("[data-consumer-content]"),
    };
  }

  function begin(screenId) {
    const view = parts(screenId);
    if (view.state) { view.state.className = "card records-state"; view.state.textContent = "Loading…"; }
    if (view.content) view.content.replaceChildren();
    return view;
  }

  function finish(view, error) {
    if (!view.state) return;
    view.state.textContent = error || "";
    view.state.className = error ? "card records-state bad" : "";
  }

  function empty(target, message) {
    const card = document.createElement("div");
    card.className = "card muted";
    card.textContent = message;
    target.appendChild(card);
  }

  function card(target, title, lines) {
    const article = document.createElement("article");
    article.className = "card";
    const heading = document.createElement("h4");
    heading.className = "section-title";
    heading.textContent = title || "Health information";
    article.appendChild(heading);
    (lines || []).filter(value => value !== null && value !== undefined && value !== "").forEach(value => {
      const row = document.createElement("p");
      row.className = "small";
      row.textContent = String(value);
      article.appendChild(row);
    });
    target.appendChild(article);
  }

  function widget(summary, id) {
    return ((summary && summary.widgets) || []).find(item => item.widget_id === id) || { payload: {} };
  }

  async function summary(force) {
    const dash = dashboard();
    if (dash) {
      if (force || !dash.summary) {
        await dash.refresh({ force: !!force });
      }
      if (dash.summary) return dash.summary;
    }
    return request("/api/dashboard/summary");
  }

  function provenanceLabel(trend) {
    const provenance = (trend && trend.provenance) || (trend && trend.data_plane === "monitoring" ? "health_connect_observational" : "clinical");
    if (provenance === "health_connect_observational") return "Health Connect (observational — not a lab result)";
    if (provenance === "clinical") return "Clinical / lab evidence";
    return String(provenance);
  }

  async function loadTrends(options) {
    const force = !!(options && options.force);
    const view = begin("consumer_trends_screen");
    try {
      const trendsPayload = widget(await summary(force), "trends_widget").payload || {};
      const trends = trendsPayload.trends || {};
      const exclusions = trendsPayload.exclusions || [];
      const entries = Object.entries(trends);
      if (!entries.length && !exclusions.length) {
        empty(view.content, "No trends are available yet. Add records with repeated measurements or sync Health Connect observations to build longitudinal trends.");
      }
      entries.forEach(([metric, trend]) => card(view.content, metric.replace(/_/g, " "), [
        `Direction: ${trend.label || trend.direction || "Not enough data"}`,
        `Latest: ${trend.latest == null ? "Not available" : trend.latest}`,
        `Samples: ${trend.sample_count == null ? "Not available" : trend.sample_count}`,
        `Source: ${provenanceLabel(trend)}`,
      ]));
      exclusions.forEach(item => card(view.content, `${String(item.metric || "metric").replace(/_/g, " ")} (excluded)`, [
        item.message || "Intentionally excluded from classical Trends.",
        `Reason: ${item.reason || "excluded"}`,
      ]));
      finish(view);
    } catch (error) { finish(view, error.message); }
  }

  async function loadObservations(options) {
    const force = !!(options && options.force);
    const view = begin("consumer_observations_screen");
    try {
      const observations = widget(await summary(force), "key_observations").payload.observations || [];
      if (!observations.length) empty(view.content, "No AI observations are available for your records yet.");
      observations.forEach(item => card(view.content, item.category || "Observation", [
        item.fact, item.interpretation, item.explanation,
        item.safety_boundary_disclaimer || "Observational information only — not a diagnosis.",
      ]));
      finish(view);
    } catch (error) { finish(view, error.message); }
  }

  async function loadTimeline() {
    const view = begin("consumer_timeline_screen");
    try {
      const body = await request("/api/health-vault/timeline?unified=true");
      const events = Array.isArray(body.entries) ? body.entries : [];
      if (!events.length) empty(view.content, "Your timeline is empty. Imported records and measurements will appear here.");
      events.forEach(event => card(view.content, event.date ? String(event.date).slice(0, 10) : "Timeline event", [
        event.summary || event.trend_impact || event.event_type,
        `Category: ${event.primary_category || event.category || "Not available"}`,
        `Source: ${event.provenance || event.source || "Not available"}`,
      ]));
      finish(view);
    } catch (error) { finish(view, error.message); }
  }

  function reportRows(value, prefix, rows, depth) {
    if (depth > 2 || rows.length >= 40 || value == null) return;
    if (Array.isArray(value)) {
      if (!value.length) rows.push([prefix, "None available"]);
      value.slice(0, 10).forEach((item, index) => reportRows(item, `${prefix} ${index + 1}`, rows, depth + 1));
      return;
    }
    if (typeof value === "object") {
      Object.entries(value).forEach(([key, item]) => reportRows(item, prefix ? `${prefix} — ${key}` : key, rows, depth + 1));
      return;
    }
    rows.push([prefix.replace(/_/g, " "), String(value)]);
  }

  async function loadReport() {
    const view = begin("consumer_reports_screen");
    try {
      const body = await request("/api/health-vault/doctor-visit");
      const rows = [];
      reportRows(body, "", rows, 0);
      if (!rows.length) empty(view.content, "No report information is available yet.");
      rows.forEach(([label, value]) => card(view.content, label || "Report item", [value]));
      finish(view);
    } catch (error) { finish(view, error.message); }
  }

  async function loadSettings() {
    const identity = document.getElementById("consumer_settings_identity");
    if (identity && dashboard()) identity.textContent = `Signed in as ${dashboard().patientId}.`;
    const ops = document.getElementById("consumer_settings_ops");
    if (!ops || !authenticated()) return;
    try {
      const readiness = await request("/api/ops/readiness");
      const r = (readiness && readiness.readiness) || {};
      const failures = r.failure_states || [];
      const hints = r.onboarding_hints || {};
      const guidance = r.recovery_guidance || {};
      const lines = [
        `Runtime: ${r.loopback_api || "unknown"}; vault schema ${r.vault_schema_ok ? "ok" : "attention"}; companion paired: ${r.companion_paired ? "yes" : "no"}.`,
        hints.first_run || "",
        failures.length ? (guidance[failures[0]] || hints.pairing || "") : (hints.offline_degraded || ""),
      ].filter(Boolean);
      ops.textContent = lines.join(" ");
    } catch (error) {
      ops.textContent = "Runtime readiness unavailable. Confirm the HealthChecker API is running on 127.0.0.1:8766 (not CSS :8765), then retry.";
    }
  }

  function bind() {
    document.querySelectorAll("#tabs_navbar .tab").forEach(tab => {
      const screenId = tab.getAttribute("data");
      tab.setAttribute("role", "button");
      tab.setAttribute("tabindex", "0");
      tab.addEventListener("keydown", event => {
        if (event.key === "Enter" || event.key === " ") { event.preventDefault(); tab.click(); }
      });
      if (SCREEN_LOADERS[screenId]) tab.addEventListener("click", () => {
        if (!authenticated()) return;
        const now = Date.now();
        if (screenId === lastNavScreen && (now - lastNavAt) < NAV_COOLDOWN_MS) {
          return;
        }
        lastNavAt = now;
        lastNavScreen = screenId;
        SCREEN_LOADERS[screenId]();
      });
    });
    const trendsRefresh = document.getElementById("consumer_trends_refresh");
    if (trendsRefresh) trendsRefresh.addEventListener("click", () => loadTrends({ force: true }));
    const refresh = document.getElementById("consumer_report_refresh");
    if (refresh) refresh.addEventListener("click", loadReport);
    const print = document.getElementById("consumer_report_print");
    if (print) print.addEventListener("click", () => global.print());
    const theme = document.getElementById("consumer_settings_theme");
    if (theme) theme.addEventListener("click", () => dashboard() && dashboard().toggleTheme());
    const customize = document.getElementById("consumer_settings_customize");
    if (customize) customize.addEventListener("click", () => {
      if (!dashboard()) return;
      dashboard().openScreen("dash");
      dashboard().toggleCustomizationPanel();
      const panel = document.getElementById("dashboard_config_panel");
      if (panel) panel.focus();
    });
    const supportBtn = document.getElementById("consumer_settings_support_bundle");
    if (supportBtn) supportBtn.addEventListener("click", async () => {
      if (!authenticated() || !dashboard()) return;
      const confirmed = global.confirm(
        "Export a redacted support bundle? It is not sent automatically; you choose whether to share it."
      );
      if (!confirmed) return;
      try {
        const response = await fetch("/api/ops/support-bundle", {
          method: "POST",
          headers: Object.assign(
            { "Content-Type": "application/json" },
            dashboard().getAuthorizationHeaders()
          ),
          body: JSON.stringify({ confirm_export: true }),
        });
        if (!response.ok) throw new Error("support_bundle_failed");
        const blob = await response.blob();
        const url = URL.createObjectURL(blob);
        const anchor = document.createElement("a");
        anchor.href = url;
        anchor.download = "healthchecker-support-bundle.zip";
        anchor.click();
        URL.revokeObjectURL(url);
      } catch (error) {
        global.alert("Support bundle export failed. Owner/admin session required; try again after sign-in.");
      }
    });
    document.addEventListener("hc:session-changed", event => {
      if (!event.detail || !event.detail.authenticated) {
        document.querySelectorAll(".consumer-api-screen [data-consumer-content]").forEach(node => node.replaceChildren());
      }
    });
  }

  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", bind);
  else bind();
  global.HCConsumerSurfaces = { loadTrends, loadObservations, loadTimeline, loadReport };
})(typeof window !== "undefined" ? window : globalThis);
