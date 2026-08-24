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

  async function parseJsonResponse(response) {
    const ct = String((response.headers && response.headers.get("content-type")) || "").toLowerCase();
    if (ct.indexOf("text/html") >= 0) {
      throw new Error("HealthChecker returned a page instead of data. Sign in and retry.");
    }
    const text = await response.text();
    const trimmed = String(text || "").trim();
    if (!trimmed || trimmed.charAt(0) === "<") {
      throw new Error("HealthChecker returned a page instead of data. Sign in and retry.");
    }
    try {
      return JSON.parse(trimmed);
    } catch (_err) {
      throw new Error("HealthChecker returned a page instead of data. Sign in and retry.");
    }
  }

  async function request(path) {
    const response = await fetch(path, {
      headers: Object.assign({ Accept: "application/json" }, headers()),
      cache: "no-store",
      credentials: "same-origin",
    });
    if (response.status === 401 || response.status === 403) {
      if (dashboard()) dashboard().handleLogout();
      throw new Error("Your session expired. Please sign in again.");
    }
    const body = await parseJsonResponse(response);
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
    if (provenance === "health_connect_observational") return "Health Connect observational";
    if (provenance === "combined_clinical_and_health_connect" || (trend && trend.data_plane === "combined")) {
      return "Combined clinical + Health Connect observational";
    }
    if (provenance === "clinical") return "Clinical / lab evidence";
    return String(provenance);
  }

  let metricFilter = null;

  async function loadTrends(options) {
    const force = !!(options && options.force);
    const filterMetric = (options && options.metric) || (metricFilter && metricFilter.metric) || null;
    const filterMetrics = ((options && options.metrics) || (metricFilter && metricFilter.metrics) || [])
      .map(value => String(value || "").toLowerCase());
    const view = begin("consumer_trends_screen");
    try {
      let trendsPayload = {};
      if (global.HCConsumerTrends && typeof HCConsumerTrends.loadFiltered === "function") {
        trendsPayload = await HCConsumerTrends.loadFiltered({
          metric: filterMetric,
          metrics: filterMetrics,
        }) || {};
      } else {
        trendsPayload = widget(await summary(force), "trends_widget").payload || {};
      }
      const trends = trendsPayload.trends || {};
      const exclusions = trendsPayload.exclusions || [];
      let entries = Object.entries(trends);
      if (filterMetric || filterMetrics.length) {
        const want = new Set(filterMetrics.concat(filterMetric ? [String(filterMetric).toLowerCase()] : []));
        entries = entries.filter(([metric]) => {
          const key = String(metric || "").toLowerCase();
          return want.has(key) || want.has(key.replace(/_/g, ""));
        });
      }
      if (!entries.length && !exclusions.length) {
        empty(view.content, filterMetric
          ? `No trends are available yet for ${String(filterMetric).replace(/_/g, " ")}.`
          : "No trends are available yet. Add records with repeated measurements or sync Health Connect observations to build longitudinal trends.");
      }
      entries.forEach(([metric, trend]) => card(view.content, metric.replace(/_/g, " "), [
        `Direction: ${trend.label || trend.direction || "Not enough data"}`,
        `Latest: ${trend.latest == null ? "Not available" : trend.latest}`,
        `Samples: ${trend.sample_count == null ? "Not available" : trend.sample_count}`,
        `Source: ${provenanceLabel(trend)}`,
      ]));
      if (!filterMetric) {
        exclusions.forEach(item => card(view.content, `${String(item.metric || "metric").replace(/_/g, " ")} (excluded)`, [
          item.message || "Intentionally excluded from classical Trends.",
          `Reason: ${item.reason || "excluded"}`,
        ]));
      }
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

  const METRIC_ALIASES = {
    pulse: "heart_rate",
    hr: "heart_rate",
    spo2: "oxygen_saturation",
    ldl_c: "ldl",
    ldl_cholesterol: "ldl",
    exercise_minutes: "activity_minutes",
  };

  const METRIC_LABELS = {
    heart_rate: "Heart Rate",
    resting_hr: "Resting Heart Rate",
    oxygen_saturation: "Oxygen Saturation",
    sleep_duration: "Sleep",
    steps: "Steps",
    exercise_minutes: "Activity",
    activity_minutes: "Activity",
    blood_pressure: "Blood Pressure",
    glucose: "Glucose",
    weight: "Weight",
    bmi: "BMI",
  };

  const MEANINGLESS_CATEGORY = {
    "": true,
    "not available": true,
    "n/a": true,
    na: true,
    none: true,
    unknown: true,
    other: true,
    null: true,
    undefined: true,
    "-": true,
  };

  function canonicalizeMetric(metric) {
    const key = String(metric || "").toLowerCase().trim();
    return METRIC_ALIASES[key] || key;
  }

  function consumerMetricLabel(metric) {
    const key = canonicalizeMetric(metric);
    if (METRIC_LABELS[key]) return METRIC_LABELS[key];
    return String(metric || "").replace(/_/g, " ").replace(/\b[a-z]/g, ch => ch.toUpperCase());
  }

  function entryDay(event) {
    const raw = (event && (event.measured_at || event.date || event.report_date || event.imported_at)) || "";
    const text = String(raw);
    return text.length >= 10 ? text.slice(0, 10) : (text || "Timeline");
  }

  function sourceLabel(event) {
    const doc = event && event.document && typeof event.document === "object" ? event.document : {};
    return String(
      (event && (event.provenance || event.source)) ||
      doc.provenance ||
      doc.source_system ||
      ""
    ).trim();
  }

  function provenanceBucket(event) {
    const doc = event && event.document && typeof event.document === "object" ? event.document : {};
    const blob = [
      event && event.provenance,
      event && event.source,
      event && event.entry_kind,
      doc.provenance,
      doc.source_system,
      doc.document_type,
    ].join(" ").toLowerCase();
    if (/health_connect|companion|wearable|monitoring|hc_v6/.test(blob)) return "health_connect";
    if ((event && event.entry_kind) === "guardian_event") return "guardian";
    if (/clinical|lab|laboratory/.test(blob)) return "clinical";
    return "other";
  }

  function asFiniteNumber(value) {
    if (value == null || value === "") return null;
    const num = Number(value);
    return Number.isFinite(num) ? num : null;
  }

  function formatValue(value, unit) {
    const num = asFiniteNumber(value);
    let shown = value == null ? "" : String(value);
    if (num != null) {
      shown = Math.abs(num - Math.round(num)) < 1e-6 ? String(Math.round(num)) : String(Math.round(num * 100) / 100);
    }
    const suffix = String(unit || "").trim();
    return suffix ? shown + " " + suffix : shown;
  }

  function extractSample(event) {
    const measurements = (event && event.measurements) || [];
    const metrics = [];
    let sample = null;
    measurements.forEach(row => {
      if (!row || typeof row !== "object") return;
      const metric = canonicalizeMetric(row.metric || row.metric_type);
      const value = asFiniteNumber(row.value);
      if (!metric || value == null) return;
      if (metrics.indexOf(metric) < 0) metrics.push(metric);
      const measuredAt = row.measured_at || (event && (event.measured_at || event.date)) || "";
      if (!sample || String(measuredAt || "").localeCompare(String(sample.measuredAt || "")) > 0) {
        sample = {
          metric: metric,
          value: value,
          unit: row.units || row.unit || "",
          measuredAt: measuredAt,
        };
      }
    });
    if (!sample && event && event.payload && typeof event.payload === "object") {
      const metric = canonicalizeMetric(event.payload.metric || event.payload.metric_type);
      const value = asFiniteNumber(event.payload.value);
      if (metric && value != null) {
        metrics.push(metric);
        sample = {
          metric: metric,
          value: value,
          unit: event.payload.units || event.payload.unit || "",
          measuredAt: event.payload.ts || event.measured_at || event.date || "",
        };
      }
    }
    return {
      compactable: !!(sample && metrics.length === 1),
      metric: sample && sample.metric,
      value: sample && sample.value,
      unit: sample && sample.unit,
      measuredAt: sample && sample.measuredAt,
      metrics: metrics,
    };
  }

  function categoryLine(event) {
    const raw = String((event && (event.primary_category || event.category || event.category_label)) || "").trim();
    const key = raw.toLowerCase().replace(/_/g, " ");
    if (!raw || MEANINGLESS_CATEGORY[key]) return "";
    return "Category: " + raw.replace(/_/g, " ");
  }

  function eventCardLines(event) {
    const sample = extractSample(event);
    const lines = [];
    if (sample.compactable) {
      lines.push(consumerMetricLabel(sample.metric));
      lines.push(formatValue(sample.value, sample.unit));
    } else {
      const summary = (event && (event.summary || event.trend_impact || event.event_type)) ||
        (event && event.document && event.document.original_filename) || "";
      if (summary) lines.push(String(summary));
    }
    const category = categoryLine(event);
    if (category) lines.push(category);
    const source = sourceLabel(event);
    if (source) lines.push("Source: " + source);
    return lines;
  }

  function groupedCardLines(group) {
    const lines = [];
    lines.push(consumerMetricLabel(group.metric));
    const count = group.underlyingCount;
    lines.push(count === 1 ? "1 observation" : String(count) + " observations");
    if (group.latest && group.latest.value != null) {
      lines.push("Latest: " + formatValue(group.latest.value, group.latest.unit || group.unit));
    }
    if (group.count >= 2 && group.min != null && group.max != null) {
      const unit = (group.latest && group.latest.unit) || group.unit || "";
      lines.push("Range: " + formatValue(group.min, "") + "–" + formatValue(group.max, unit));
    }
    if (group.count >= 2 && group.average != null) {
      lines.push("Average: " + formatValue(group.average, (group.latest && group.latest.unit) || group.unit || ""));
    }
    if (group.source) lines.push("Source: " + group.source);
    return lines;
  }

  function compactTimelineEntries(events) {
    const groups = [];
    const index = Object.create(null);
    (events || []).forEach(event => {
      const sample = extractSample(event);
      const bucket = provenanceBucket(event);
      const groupable = sample.compactable && bucket === "health_connect";
      if (!groupable) {
        groups.push({ kind: "event", event: event, underlyingCount: 1 });
        return;
      }
      const day = entryDay(event);
      const key = day + "|" + sample.metric + "|" + bucket;
      let group = index[key];
      if (!group) {
        group = {
          kind: "group",
          day: day,
          metric: sample.metric,
          bucket: bucket,
          source: sourceLabel(event),
          events: [],
          samples: [],
        };
        index[key] = group;
        groups.push(group);
      }
      group.events.push(event);
      group.samples.push(sample);
      if (sourceLabel(event)) group.source = sourceLabel(event);
    });
    groups.forEach(group => {
      if (group.kind !== "group") return;
      group.underlyingCount = group.events.length;
      const nums = group.samples.map(row => row.value).filter(value => value != null);
      group.count = nums.length;
      group.min = nums.length ? Math.min.apply(null, nums) : null;
      group.max = nums.length ? Math.max.apply(null, nums) : null;
      group.average = nums.length
        ? Math.round((nums.reduce(function (sum, value) { return sum + value; }, 0) / nums.length) * 100) / 100
        : null;
      group.latest = group.samples.slice().sort(function (a, b) {
        return String(b.measuredAt || "").localeCompare(String(a.measuredAt || ""));
      })[0] || null;
      group.unit = (group.latest && group.latest.unit) || "";
    });
    return groups;
  }

  function matchesMetricFilter(event, want) {
    if (!want || !want.size) return true;
    const sample = extractSample(event);
    if (sample.metric && (want.has(sample.metric) || want.has(String(sample.metric).replace(/_/g, "")))) {
      return true;
    }
    const metrics = sample.metrics || [];
    for (let i = 0; i < metrics.length; i++) {
      if (want.has(metrics[i]) || want.has(String(metrics[i]).replace(/_/g, ""))) return true;
    }
    const blob = [
      event && event.summary,
      event && event.trend_impact,
      event && event.event_type,
      event && event.primary_category,
    ].join(" ").toLowerCase().replace(/_/g, " ");
    for (const key of want) {
      const needle = String(key || "").toLowerCase().replace(/_/g, " ").trim();
      if (needle.length < 4) continue;
      if (blob.indexOf(needle) >= 0) return true;
    }
    return false;
  }

  function timelineGroupCard(target, group) {
    const article = document.createElement("article");
    article.className = "card";
    article.setAttribute("data-timeline-group", group.metric || "");
    article.setAttribute("data-underlying-count", String(group.underlyingCount));
    const heading = document.createElement("h4");
    heading.className = "section-title";
    heading.textContent = group.day || "Timeline";
    article.appendChild(heading);
    groupedCardLines(group).forEach(value => {
      const row = document.createElement("p");
      row.className = "small";
      row.textContent = String(value);
      article.appendChild(row);
    });
    if (group.underlyingCount > 1) {
      const details = document.createElement("details");
      details.className = "timeline-observation-details";
      const summary = document.createElement("summary");
      summary.textContent = "Show observations";
      details.appendChild(summary);
      group.samples.slice().sort(function (a, b) {
        return String(b.measuredAt || "").localeCompare(String(a.measuredAt || ""));
      }).forEach(sample => {
        const row = document.createElement("p");
        row.className = "small";
        const stamp = String(sample.measuredAt || "");
        const time = stamp.length >= 16 ? stamp.slice(11, 16) : stamp;
        row.textContent = time
          ? time + " — " + formatValue(sample.value, sample.unit)
          : formatValue(sample.value, sample.unit);
        details.appendChild(row);
      });
      article.appendChild(details);
    }
    target.appendChild(article);
  }

  let timelineLoadSeq = 0;

  function buildTimelineQuery(options) {
    const rawMetric = (options && options.metric) || (metricFilter && metricFilter.metric) || "";
    const filterMetric = canonicalizeMetric(rawMetric);
    const params = new URLSearchParams({ unified: "true" });
    if (filterMetric) params.set("metric", filterMetric);
    return {
      path: "/api/health-vault/timeline?" + params.toString(),
      filterMetric: filterMetric || null,
      want: filterMetric ? new Set([filterMetric]) : new Set(),
    };
  }

  function activateConsumerScreen(screenId) {
    const tabs = document.querySelectorAll("#tabs_navbar .tab");
    for (let i = 0; i < tabs.length; i++) tabs[i].classList.remove("active");
    const tab = document.querySelector('#tabs_navbar .tab[data="' + screenId + '"]') ||
      document.querySelector('.tab[data="' + screenId + '"]');
    if (tab) tab.classList.add("active");
    const screens = document.querySelectorAll(".screen");
    for (let i = 0; i < screens.length; i++) screens[i].classList.remove("active");
    const screen = document.getElementById(screenId);
    if (screen) screen.classList.add("active");
  }

  async function loadTimeline(options) {
    const query = buildTimelineQuery(options);
    const seq = ++timelineLoadSeq;
    const view = begin("consumer_timeline_screen");
    try {
      const body = await request(query.path);
      if (seq !== timelineLoadSeq) return;
      let events = Array.isArray(body.entries) ? body.entries : [];
      if (query.want.size) events = events.filter(event => matchesMetricFilter(event, query.want));
      let groups = compactTimelineEntries(events);
      if (query.filterMetric) {
        groups = groups.filter(group => {
          if (group.kind === "group") return group.metric === query.filterMetric;
          const sample = extractSample(group.event);
          if (sample.metric) return sample.metric === query.filterMetric;
          return matchesMetricFilter(group.event, query.want);
        });
      }
      if (!groups.length) {
        empty(view.content, query.filterMetric
          ? `No timeline events matched ${consumerMetricLabel(query.filterMetric)}.`
          : "Your timeline is empty. Imported records and measurements will appear here.");
      }
      groups.forEach(group => {
        if (group.kind === "group") {
          timelineGroupCard(view.content, group);
          return;
        }
        card(view.content, entryDay(group.event), eventCardLines(group.event));
      });
      finish(view);
    } catch (error) {
      if (seq !== timelineLoadSeq) return;
      finish(view, error.message);
    }
  }

  function openFiltered(surface, options) {
    const primary = canonicalizeMetric(options && options.metric ? String(options.metric) : "");
    metricFilter = {
      metric: primary || null,
      metrics: primary ? [primary] : ((options && options.metrics) || []).map(canonicalizeMetric).filter(Boolean),
      category: options && options.category ? options.category : null,
    };
    const map = {
      records: "health_records_screen",
      health_records: "health_records_screen",
      vault: "health_records_screen",
      timeline: "consumer_timeline_screen",
      trends: "consumer_trends_screen",
    };
    const screenId = map[surface] || surface;
    if (screenId === "health_records_screen") {
      if (global.HCVaultUI && HCVaultUI.setMetricFilter) {
        HCVaultUI.setMetricFilter(metricFilter.metric, metricFilter.metrics, metricFilter.category);
      }
      if (global.HCRecordsUI && typeof HCRecordsUI.setMetricFilter === "function") {
        HCRecordsUI.setMetricFilter(metricFilter.metric, metricFilter.metrics, metricFilter.category);
      }
    }
    // HC321-UAT12H: do not tab.click() Timeline/Trends. index.html onclick only
    // switches screens, but bind() SCREEN_LOADERS also fire on click and call
    // loadTimeline() without a metric — that second unfiltered GET overwrote
    // Heart Rate → Filtered Timeline on S24 after the cooldown window, or when
    // the tab listener ran before lastNav was visible to that handler.
    if (screenId === "consumer_trends_screen" || screenId === "consumer_timeline_screen") {
      lastNavAt = Date.now();
      lastNavScreen = screenId;
      if (global.HCConsumerNav) HCConsumerNav.note(screenId);
      activateConsumerScreen(screenId);
      if (screenId === "consumer_trends_screen") loadTrends(metricFilter);
      if (screenId === "consumer_timeline_screen") loadTimeline(metricFilter);
      return;
    }
    const tab = document.querySelector('.tab[data="' + screenId + '"]');
    if (tab) tab.click();
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
    if (identity && dashboard()) {
      const dash = dashboard();
      const name = (dash.summary && dash.summary.display_name) || dash.displayName;
      // Patient ID remains visible in Settings for support/ops scoping.
      identity.textContent = name && name !== dash.patientId
        ? `Signed in as ${name} (Patient ID: ${dash.patientId}).`
        : `Signed in as Patient ID ${dash.patientId}.`;
    }
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
        if (screenId === "consumer_timeline_screen" || screenId === "consumer_trends_screen") {
          SCREEN_LOADERS[screenId](metricFilter);
        } else {
          SCREEN_LOADERS[screenId]();
        }
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
  global.HCConsumerSurfaces = {
    loadTrends, loadObservations, loadTimeline, loadReport, openFiltered, parseJsonResponse,
    compactTimelineEntries, consumerMetricLabel, groupedCardLines, eventCardLines,
    categoryLine, extractSample, canonicalizeMetric, buildTimelineQuery,
    activateConsumerScreen, matchesMetricFilter,
  };
})(typeof window !== "undefined" ? window : globalThis);
