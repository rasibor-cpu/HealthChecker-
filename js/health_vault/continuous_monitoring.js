/**
 * HC-302 — Browser continuous monitoring status mirror.
 * Prefers /api/monitoring/status when available; falls back to local vault snapshot.
 * Never fabricates LIVE readings. Escapes HTML. Labels freshness and acquisition mode.
 */
(function (global) {
  "use strict";

  const DISCLAIMER =
    "Continuous monitoring is observational only — not a diagnosis. " +
    "Live Health Connect / Libre require platform permissions and authorized bridges. " +
    "A PWA cannot guarantee continuous background execution. " +
    "Manufacturer alarms and emergency care remain primary.";

  function escapeHtml(value) {
    return String(value == null ? "" : value)
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;")
      .replace(/'/g, "&#39;");
  }

  function classifyMode(mode) {
    const m = String(mode || "IMPORTED").toUpperCase();
    if (m === "LIVE") return "live";
    if (m === "MANUAL") return "manual";
    if (m === "SIMULATED_TEST_ONLY") return "simulated_test_only";
    if (m === "UNAVAILABLE") return "unavailable";
    if (m === "STALE") return "stale";
    return "imported_or_delayed";
  }

  function defaultStatus() {
    return {
      schema_version: "hc.monitoring_status.v1",
      phase: "HC-302",
      connectors: [
        {
          connector_id: "health_connect",
          display_name: "Android Health Connect / Samsung Health",
          readiness: {
            state: "unavailable",
            live_available: false,
            action_required:
              "Android Health Connect companion bridge not available in browser-only mode.",
          },
        },
        {
          connector_id: "libre",
          display_name: "FreeStyle Libre",
          readiness: {
            state: "import_required",
            live_available: false,
            file_import_supported: true,
            action_required: "Use file/export import. Live Libre API is unavailable.",
          },
        },
      ],
      latest_reading_by_metric: {},
      active_alerts: [],
      action_required: [
        {
          connector_id: "health_connect",
          state: "unavailable",
          action: "Authorize Android Health Connect companion when available.",
        },
        {
          connector_id: "libre",
          state: "import_required",
          action: "Import Libre exports; live API not configured.",
        },
      ],
      background: {
        continuous_guaranteed: false,
        browser_pwa_limitation: true,
        android_workmanager_required_for_reliable_background: true,
      },
      last_successful_sync: null,
      last_attempt_at: null,
      disclaimer: DISCLAIMER,
    };
  }

  function localStatus() {
    try {
      if (global.HCHealthVault && typeof global.HCHealthVault.getMonitoringStatus === "function") {
        const s = global.HCHealthVault.getMonitoringStatus();
        if (s && s.schema_version) return s;
      }
    } catch (_) {}
    return defaultStatus();
  }

  function fetchStatus() {
    if (typeof fetch !== "function") {
      return Promise.resolve(localStatus());
    }
    return fetch("/api/monitoring/status")
      .then(function (res) {
        if (!res || !res.ok) throw new Error("status_http_" + (res && res.status));
        return res.json();
      })
      .then(function (payload) {
        if (!payload || !payload.schema_version) throw new Error("bad_payload");
        try {
          if (global.HCHealthVault && typeof global.HCHealthVault.saveMonitoringStatus === "function") {
            global.HCHealthVault.saveMonitoringStatus(payload);
          }
        } catch (_) {}
        return payload;
      })
      .catch(function () {
        return localStatus();
      });
  }

  const HCContinuousMonitoring = {
    disclaimer: DISCLAIMER,

    getStatus: function () {
      const status = localStatus();
      status.disclaimer = DISCLAIMER;
      return status;
    },

    refresh: function () {
      const self = this;
      return fetchStatus().then(function (status) {
        status.disclaimer = DISCLAIMER;
        self.renderUI(status);
        return status;
      });
    },

    renderUI: function (status) {
      status = status || this.getStatus();
      const panel = document.getElementById("monitoring_status_panel");
      const metrics = document.getElementById("monitoring_metrics_panel");
      const actions = document.getElementById("monitoring_actions_panel");
      if (!panel && !metrics && !actions) return;

      if (panel) {
        const connectors = status.connectors || [];
        const lines = connectors.map(function (c) {
          const r = c.readiness || {};
          const live = r.live_available ? "LIVE capable" : "not live";
          return (
            "<div><strong>" +
            escapeHtml(c.display_name || c.connector_id) +
            "</strong>: " +
            escapeHtml(r.state || "unknown") +
            " (" +
            live +
            ")</div>"
          );
        });
        lines.push(
          "<div class='muted small'>Last successful sync: " +
            escapeHtml(status.last_successful_sync || "never") +
            "</div>"
        );
        lines.push(
          "<div class='muted small'>Last attempt: " +
            escapeHtml(status.last_attempt_at || "never") +
            "</div>"
        );
        lines.push(
          "<div class='muted small'>Background continuous execution: <strong>not guaranteed</strong> in browser/PWA. Native Android companion + WorkManager remain future requirements.</div>"
        );
        panel.innerHTML = lines.join("");
      }

      if (metrics) {
        const latest = status.latest_reading_by_metric || {};
        const keys = Object.keys(latest);
        if (!keys.length) {
          metrics.innerHTML =
            "<div class='muted small'>No continuous-monitoring observations yet. Upload/import and connector sync will appear here with LIVE vs IMPORTED labels.</div>";
        } else {
          metrics.innerHTML = keys
            .map(function (k) {
              const row = latest[k] || {};
              const cls = classifyMode(row.acquisition_mode);
              const fresh = String(row.freshness_status || "unknown");
              const current = row.is_current === false || fresh === "stale" || fresh === "missing";
              const staleNote = current ? " · <em>not current</em>" : "";
              return (
                "<div class='mon-metric'>" +
                "<strong>" +
                escapeHtml(k) +
                "</strong>: " +
                escapeHtml(row.value != null ? row.value : "—") +
                (row.unit ? " " + escapeHtml(row.unit) : "") +
                " · <span class='mon-mode mon-mode-" +
                cls +
                "'>" +
                escapeHtml(row.acquisition_mode || "IMPORTED") +
                "</span>" +
                " · freshness " +
                escapeHtml(fresh) +
                staleNote +
                " · " +
                escapeHtml(row.measured_at || "") +
                "</div>"
              );
            })
            .join("");
        }
      }

      if (actions) {
        const req = status.action_required || [];
        if (!req.length) {
          actions.innerHTML = "<div class='muted small'>No connector actions required.</div>";
        } else {
          actions.innerHTML = req
            .map(function (a) {
              return (
                "<div><strong>" +
                escapeHtml(a.connector_id || "") +
                "</strong> [" +
                escapeHtml(a.state || "") +
                "]: " +
                escapeHtml(a.action || "") +
                "</div>"
              );
            })
            .join("");
        }
      }

      const disc = document.getElementById("monitoring_disclaimer");
      if (disc) disc.textContent = DISCLAIMER;
    },
  };

  global.HCContinuousMonitoring = HCContinuousMonitoring;
})(typeof window !== "undefined" ? window : globalThis);
