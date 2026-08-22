/**
 * HC-316C — Authenticated Consumer Dashboard Frontend Component.
 */
(function (global) {
  "use strict";

  const STORAGE_KEY = "hc_auth_session";

  class ConsumerDashboard {
    constructor() {
      this.patientId = null;
      this.token = null;
      this.displayName = null;
      this.preferences = null;
      this.summary = null;
      this._refreshInFlight = null;
      this._lastRefreshAt = 0;
      this._summaryFetchedAt = 0;
      this._loginInFlight = false;
      // Soft throttle for repeated auto-refresh / rapid navigation (ms).
      this.REFRESH_COOLDOWN_MS = 4000;
      this.SUMMARY_TTL_MS = 15000;
    }

    init() {
      this.loadSession();
      this.bindEvents();
      this.updateUIVisibility();
      
      if (this.token) {
        this.refresh();
      }
    }

    loadSession() {
      try {
        const raw = sessionStorage.getItem(STORAGE_KEY);
        if (raw) {
          const parsed = JSON.parse(raw);
          this.patientId = parsed.patientId;
          this.token = parsed.token;
          this.displayName = parsed.displayName || null;
        }
      } catch (e) {
        console.error("Failed to load dashboard session", e);
      }
      document.dispatchEvent(new CustomEvent("hc:session-changed", {
        detail: { authenticated: !!this.token },
      }));
    }

    saveSession(patientId, token, displayName) {
      this.patientId = patientId;
      this.token = token;
      this.displayName = displayName || null;
      sessionStorage.setItem(STORAGE_KEY, JSON.stringify({
        patientId,
        token,
        displayName: this.displayName || undefined,
      }));
      document.dispatchEvent(new CustomEvent("hc:session-changed", { detail: { authenticated: true } }));
    }

    clearSession() {
      this.patientId = null;
      this.token = null;
      this.displayName = null;
      this.preferences = null;
      this.summary = null;
      sessionStorage.removeItem(STORAGE_KEY);
      document.body.classList.remove("light-theme", "dark-theme");
      document.dispatchEvent(new CustomEvent("hc:session-changed", { detail: { authenticated: false } }));
    }

    consumerGreeting() {
      const fromSummary = this.summary && this.summary.display_name;
      const name = String(fromSummary || this.displayName || "").trim();
      if (name && name !== String(this.patientId || "")) {
        return `Welcome, ${name}`;
      }
      return "My Health Dashboard";
    }

    getAuthorizationHeaders() {
      return this.token ? { "Authorization": `Bearer ${this.token}` } : {};
    }

    openScreen(screenId) {
      const tab = document.querySelector(`[data="${screenId}"]`);
      if (tab) tab.click();
    }

    bindEvents() {
      const loginBtn = document.getElementById("login_btn");
      if (loginBtn) {
        loginBtn.onclick = () => this.handleLogin();
      }

      const logoutBtn = document.getElementById("logout_btn");
      if (logoutBtn) {
        logoutBtn.onclick = () => this.handleLogout();
      }

      const themeBtn = document.getElementById("theme_toggle_btn");
      if (themeBtn) {
        themeBtn.onclick = () => this.toggleTheme();
      }

      const customizeBtn = document.getElementById("customize_dashboard_btn");
      if (customizeBtn) {
        customizeBtn.onclick = () => this.toggleCustomizationPanel();
      }

      const refreshBtn = document.getElementById("dashboard_refresh_btn");
      if (refreshBtn) {
        refreshBtn.onclick = () => this.refresh({ force: true });
      }

      const saveConfigBtn = document.getElementById("save_config_btn");
      if (saveConfigBtn) {
        saveConfigBtn.onclick = () => this.savePreferencesFromUI();
      }

      const passwordForm = document.getElementById("password_change_form");
      if (passwordForm) passwordForm.onsubmit = event => {
        event.preventDefault();
        this.handlePasswordChange();
      };
    }

    updateUIVisibility() {
      const loginScreen = document.getElementById("login_screen");
      const tabsNavbar = document.getElementById("tabs_navbar");
      const consumerContainer = document.getElementById("consumer_dashboard_container");

      if (this.token) {
        if (loginScreen) loginScreen.style.display = "none";
        if (tabsNavbar) tabsNavbar.style.display = "flex";
        if (consumerContainer) consumerContainer.style.display = "block";
      } else {
        if (loginScreen) loginScreen.style.display = "block";
        if (tabsNavbar) tabsNavbar.style.display = "none";
        if (consumerContainer) consumerContainer.style.display = "none";
        
        // Hide all screens when logged out
        document.querySelectorAll(".screen").forEach(s => s.classList.remove("active"));
        if (loginScreen) loginScreen.classList.add("active");
      }
    }

    async handleLogin() {
      if (this._loginInFlight) return;
      const pidEl = document.getElementById("login_patient_id");
      const pwdEl = document.getElementById("login_password");
      const errEl = document.getElementById("login_error");
      const loginBtn = document.getElementById("login_btn");

      if (errEl) errEl.textContent = "";

      const patient_id = pidEl ? pidEl.value.trim() : "";
      const password = pwdEl ? pwdEl.value : "";

      if (!patient_id || !password) {
        if (errEl) errEl.textContent = "Please enter Patient ID and Password.";
        return;
      }

      this._loginInFlight = true;
      if (loginBtn) loginBtn.disabled = true;
      try {
        const res = await fetch("/api/auth/login", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ patient_id, password }),
        });

        if (!res.ok) {
          const errData = await res.json().catch(() => ({}));
          if (errEl) {
            errEl.textContent = errData.error === "invalid_credentials"
              ? "Sign-in failed. Check your Patient ID and password, or wait if the account is temporarily locked."
              : (errData.error || "Login failed.");
          }
          return;
        }

        const data = await res.json();
        const loginName = (data.name && data.name !== data.patient_id) ? data.name : null;
        this.saveSession(data.patient_id, data.token, loginName);
        
        if (pidEl) pidEl.value = "";
        if (pwdEl) pwdEl.value = "";

        if (data.must_change_password) {
          this.showPasswordChange();
          return;
        }
        this.updateUIVisibility();
        
        // Switch active tab to dashboard
        const dashTab = document.querySelector('[data="dash"]');
        if (dashTab) {
          dashTab.click();
        }

        await this.refresh({ force: true });
      } catch (e) {
        if (errEl) errEl.textContent = "Network error during login.";
      } finally {
        this._loginInFlight = false;
        if (loginBtn) loginBtn.disabled = false;
      }
    }

    handleLogout() {
      if (this.token) fetch("/api/auth/logout", { method: "POST", headers: this.getAuthorizationHeaders() }).catch(() => {});
      this.clearSession();
      this.updateUIVisibility();
    }

    showPasswordChange() {
      const loginScreen = document.getElementById("login_screen");
      const form = document.getElementById("password_change_form");
      if (loginScreen) loginScreen.style.display = "block";
      if (form) form.hidden = false;
      const loginButton = document.getElementById("login_btn");
      if (loginButton) loginButton.hidden = true;
    }

    async handlePasswordChange() {
      const current = document.getElementById("current_password");
      const next = document.getElementById("new_password");
      const confirm = document.getElementById("confirm_password");
      const error = document.getElementById("password_change_error");
      if (error) error.textContent = "";
      if (!next || next.value.length < 8 || next.value !== (confirm && confirm.value)) {
        if (error) error.textContent = "New passwords must match and contain at least 8 characters.";
        return;
      }
      const response = await fetch("/api/auth/password/change", {
        method: "POST", headers: { "Content-Type": "application/json", ...this.getAuthorizationHeaders() },
        body: JSON.stringify({ current_password: current ? current.value : "", new_password: next.value }),
      });
      const data = await response.json();
      if (!response.ok) {
        if (error) error.textContent = data.error || "Password change failed.";
        return;
      }
      this.saveSession(data.patient_id, data.token, (data.name && data.name !== data.patient_id) ? data.name : this.displayName);
      const form = document.getElementById("password_change_form");
      if (form) form.hidden = true;
      const loginButton = document.getElementById("login_btn");
      if (loginButton) loginButton.hidden = false;
      [current, next, confirm].forEach(input => { if (input) input.value = ""; });
      this.updateUIVisibility();
      const dashTab = document.querySelector('[data="dash"]');
      if (dashTab) dashTab.click();
      await this.refresh();
    }

    setRefreshState(message) {
      const el = document.getElementById("dashboard_refresh_state");
      if (!el) return;
      el.textContent = message || "";
    }

    async refresh(options) {
      const opts = options || {};
      const force = !!opts.force;
      if (!this.token) return;

      const now = Date.now();
      if (this._refreshInFlight) {
        return this._refreshInFlight;
      }
      if (
        !force &&
        this.summary &&
        this._summaryFetchedAt &&
        (now - this._summaryFetchedAt) < this.SUMMARY_TTL_MS
      ) {
        this.renderDashboard();
        return this.summary;
      }
      if (!force && this._lastRefreshAt && (now - this._lastRefreshAt) < this.REFRESH_COOLDOWN_MS) {
        return this.summary;
      }

      this._lastRefreshAt = now;
      this.setRefreshState("Refreshing health data…");
      this._refreshInFlight = this._refreshBody(force)
        .then(result => {
          return result;
        })
        .catch(e => {
          console.error("Dashboard refresh error", e);
          this.setRefreshState("Could not refresh dashboard. Try again.");
          // Fail closed: never invent a successful summary from a failed response.
          // Keep any prior in-memory summary for display only; callers must not treat this as a fresh fetch.
          return this.summary;
        })
        .finally(() => {
          this._refreshInFlight = null;
        });
      return this._refreshInFlight;
    }

    async _refreshBody(force) {
      // Fetch Preferences — never cache auth/security decisions
      const prefRes = await fetch("/api/dashboard/preferences", {
        headers: { "Authorization": `Bearer ${this.token}` },
        cache: "no-store",
      });
      if (prefRes.status === 401 || prefRes.status === 403) {
        this.handleLogout();
        return;
      }
      if (!prefRes.ok) {
        this.setRefreshState("Preferences unavailable.");
        throw new Error("preferences_failed");
      }
      this.preferences = await prefRes.json();
      this.applyTheme(this.preferences.theme);

      // Fetch Summary
      const sumRes = await fetch("/api/dashboard/summary", {
        headers: { "Authorization": `Bearer ${this.token}` },
        cache: "no-store",
      });
      if (sumRes.status === 401 || sumRes.status === 403) {
        this.handleLogout();
        return;
      }
      if (!sumRes.ok) {
        this.setRefreshState("Dashboard summary unavailable.");
        throw new Error("summary_failed");
      }
      this.summary = await sumRes.json();
      this._summaryFetchedAt = Date.now();

      this.renderDashboard();
      this.setRefreshState(force ? "Updated just now." : "");
      if (global.HCExecutiveDashboard && global.HCExecutiveDashboard.refresh) {
        try {
          await global.HCExecutiveDashboard.refresh();
        } catch (_) {
          /* executive refresh is best-effort */
        }
      }
      if (global.HCHealthSnapshot && global.HCHealthSnapshot.refresh) {
        try {
          await global.HCHealthSnapshot.refresh();
        } catch (_) {
          /* snapshot refresh is best-effort */
        }
      }
      return this.summary;
    }

    applyTheme(theme) {
      if (theme === "dark") {
        document.body.classList.remove("light-theme");
        document.body.classList.add("dark-theme");
      } else {
        document.body.classList.remove("dark-theme");
        document.body.classList.add("light-theme");
      }
    }

    async toggleTheme() {
      if (!this.preferences) return;
      const nextTheme = this.preferences.theme === "dark" ? "light" : "dark";
      this.preferences.theme = nextTheme;
      this.applyTheme(nextTheme);
      await this.savePreferences(this.preferences);
    }

    toggleCustomizationPanel() {
      const panel = document.getElementById("dashboard_config_panel");
      if (!panel) return;
      
      if (panel.style.display === "none") {
        this.renderCustomizationPanel();
        panel.style.display = "block";
      } else {
        panel.style.display = "none";
      }
    }

    renderCustomizationPanel() {
      const listEl = document.getElementById("config_widgets_list");
      const prioritySel = document.getElementById("config_priority_metric");
      
      if (!listEl || !this.preferences) return;

      const widgetLabels = {
        status_summary: "Health Status Summary",
        key_observations: "Key Observations",
        trends_widget: "Health Metric Trends",
        timeline_widget: "Health Timeline",
        import_wizard: "Import Medical Records"
      };

      // Populate check/order inputs
      listEl.innerHTML = this.preferences.widget_order.map(wId => {
        const isVisible = this.preferences.visible_widgets.indexOf(wId) >= 0;
        return `
          <div style="display: flex; align-items: center; justify-content: space-between; background: var(--bg); padding: 8px; border-radius: 6px;">
            <label style="display: flex; align-items: center; gap: 8px; margin: 0; width: auto; font-weight: normal; cursor: pointer;">
              <input type="checkbox" data-widget-visible="${wId}" ${isVisible ? "checked" : ""} style="width: auto; margin: 0;" />
              <span>${widgetLabels[wId] || wId}</span>
            </label>
            <div style="display: flex; gap: 4px;">
              <button type="button" data-move-up="${wId}" style="width: auto; padding: 2px 6px; margin: 0; font-size: 11px;">▲</button>
              <button type="button" data-move-down="${wId}" style="width: auto; padding: 2px 6px; margin: 0; font-size: 11px;">▼</button>
            </div>
          </div>
        `;
      }).join("");
      // Bind move arrows
      listEl.querySelectorAll("[data-move-up]").forEach(btn => {
        btn.onclick = () => {
          const wId = btn.getAttribute("data-move-up");
          const idx = this.preferences.widget_order.indexOf(wId);
          if (idx > 0) {
            this.preferences.widget_order.splice(idx, 1);
            this.preferences.widget_order.splice(idx - 1, 0, wId);
            this.renderCustomizationPanel();
          }
        };
      });

      listEl.querySelectorAll("[data-move-down]").forEach(btn => {
        btn.onclick = () => {
          const wId = btn.getAttribute("data-move-down");
          const idx = this.preferences.widget_order.indexOf(wId);
          if (idx >= 0 && idx < this.preferences.widget_order.length - 1) {
            this.preferences.widget_order.splice(idx, 1);
            this.preferences.widget_order.splice(idx + 1, 0, wId);
            this.renderCustomizationPanel();
          }
        };
      });

      if (prioritySel) {
        prioritySel.value = this.preferences.priority_metric || "";
      }
    }

    async savePreferencesFromUI() {
      if (!this.preferences) return;

      const listEl = document.getElementById("config_widgets_list");
      const prioritySel = document.getElementById("config_priority_metric");

      const visible = [];
      listEl.querySelectorAll("[data-widget-visible]").forEach(cb => {
        if (cb.checked) {
          visible.push(cb.getAttribute("data-widget-visible"));
        }
      });

      this.preferences.visible_widgets = visible;
      if (prioritySel) {
        this.preferences.priority_metric = prioritySel.value || null;
      }

      await this.savePreferences(this.preferences);
      
      const panel = document.getElementById("dashboard_config_panel");
      if (panel) panel.style.display = "none";

      await this.refresh();
    }

    async savePreferences(prefs) {
      try {
        await fetch("/api/dashboard/preferences", {
          method: "POST",
          headers: {
            "Content-Type": "application/json",
            "Authorization": `Bearer ${this.token}`,
          },
          body: JSON.stringify(prefs),
        });
      } catch (e) {
        console.error("Failed to save preferences", e);
      }
    }

    renderDashboard() {
      const greetingEl = document.getElementById("dashboard_greeting");
      if (greetingEl) {
        greetingEl.textContent = this.consumerGreeting();
      }

      const target = document.getElementById("dashboard_widgets_target");
      if (!target || !this.summary) return;

      target.innerHTML = this.summary.widgets.map(w => {
        return `
          <div class="card" id="widget_${w.widget_id}" style="border: 1px solid var(--line); margin-bottom: 12px; padding: 15px;">
            <h4 class="section-title" style="color: var(--accent); margin-bottom: 10px; display: flex; align-items: center; justify-content: space-between;">
              <span>${this.escape(w.title)}</span>
              ${w.priority === -1 ? '<span class="badge" style="background: var(--accent); color: var(--bg); margin: 0; font-size: 10px;">PRIORITY</span>' : ''}
            </h4>
            <div class="widget-content">${this.renderWidgetContent(w)}</div>
          </div>
        `;
      }).join("");
      target.querySelectorAll("[data-open-health-records]").forEach(button => {
        button.onclick = () => this.openScreen("health_records_screen");
      });
    }

    renderWidgetContent(widget) {
      const type = widget.widget_type;
      const payload = widget.payload;

      if (type === "status") {
        const colorClass = payload.status === "warning" ? "warn" : "ok";
        const latest = payload.monitoring_latest || {};
        const latestLines = Object.entries(latest).slice(0, 4).map(([metric, row]) => {
          const value = row && row.value != null ? row.value : "—";
          const unit = row && row.unit ? ` ${row.unit}` : "";
          return `<div class="small"><strong>${this.escape(metric.replace(/_/g, " "))}:</strong> ${this.escape(String(value))}${this.escape(unit)}</div>`;
        }).join("");
        const hcCount = payload.health_connect_observation_count || 0;
        const sync = payload.health_connect_sync || {};
        let syncState = sync.sync_state || "";
        // If backend omitted sync payload (stale process) but HC observations are present,
        // reflect observation presence — never hard-code "synced".
        if ((!syncState || syncState === "not_configured") && (hcCount > 0 || (sync.observation_count || 0) > 0)) {
          syncState = "observations_present";
        }
        if (!syncState) syncState = "not_configured";
        const connectionLabel =
          syncState === "synced" || syncState === "observations_present"
            ? "Connected"
            : syncState === "paired"
              ? "Paired"
              : syncState === "not_configured"
                ? "Not configured"
                : String(syncState).replace(/_/g, " ");
        const connectionClass =
          syncState === "synced" || syncState === "observations_present" || syncState === "paired" ? "ok" : "muted";
        // Data freshness is separate from technical sync/pairing (HC321-UAT11).
        let dataFreshness = "Unknown";
        let dataFreshnessClass = "muted";
        const lastObs = sync.last_observation_at || null;
        if (lastObs) {
          const ageMs = Date.now() - Date.parse(lastObs);
          if (Number.isFinite(ageMs)) {
            const ageHours = ageMs / 3600000;
            if (ageHours <= 36) {
              dataFreshness = "Current";
              dataFreshnessClass = "ok";
            } else if (ageHours <= 72) {
              dataFreshness = "Aging";
              dataFreshnessClass = "warn";
            } else {
              dataFreshness = "Stale";
              dataFreshnessClass = "bad";
            }
          }
        } else if (hcCount > 0) {
          dataFreshness = "Aging";
          dataFreshnessClass = "warn";
        }
        const syncDetails = [
          sync.label || sync.reason || null,
          sync.paired_device_count != null ? `Paired devices: ${sync.paired_device_count}` : null,
          (sync.observation_count != null ? sync.observation_count : hcCount) != null
            ? `HC observations: ${sync.observation_count != null ? sync.observation_count : hcCount}`
            : null,
          lastObs ? `Last observation: ${String(lastObs).slice(0, 19)}` : null,
          sync.last_device_seen_at ? `Last device seen: ${String(sync.last_device_seen_at).slice(0, 19)}` : null,
        ].filter(Boolean).map(line => `<div class="small muted">${this.escape(String(line))}</div>`).join("");
        return `
          <div style="display: flex; justify-content: space-between; flex-wrap: wrap; gap: 10px;">
            <div class="kpi" style="flex: 1; min-width: 120px;">
              <strong>Overall Status:</strong> 
              <span class="${colorClass}" style="font-weight: bold;">${this.escape(payload.status.toUpperCase())}</span>
            </div>
            <div class="kpi" style="flex: 1; min-width: 120px;">
              <strong>Attention Items:</strong> 
              <span class="${payload.active_warnings > 0 ? "bad" : "muted"}" style="font-weight: bold;">${payload.active_warnings}</span>
              <div class="small muted">Missing-data / trend warnings in Key Observations (not Guardian alerts).</div>
            </div>
            <div class="kpi" style="flex: 1; min-width: 120px;">
              <strong>Total Measurements:</strong> ${payload.measurements_count}
            </div>
            <div class="kpi" style="flex: 1; min-width: 160px;">
              <strong>Health Connect connection:</strong>
              <span class="${connectionClass}" style="font-weight: bold;">${this.escape(connectionLabel)}</span>
              <div class="small"><strong>Data freshness:</strong> <span class="${dataFreshnessClass}">${this.escape(dataFreshness)}</span></div>
              ${syncDetails}
            </div>
            ${hcCount ? `<div class="kpi" style="flex: 1; min-width: 120px;"><strong>Health Connect Observations:</strong> ${hcCount}</div>` : ""}
          </div>
          ${latestLines ? `<div style="margin-top: 10px;">${latestLines}</div>` : ""}
        `;
      }

      if (type === "observations_list") {
        const obs = payload.observations || [];
        if (!obs.length) {
          return '<div class="muted small">No observations available yet.</div>';
        }
        return obs.map(o => {
          const isWarning = o.interpretation.toLowerCase() === "missing data warning";
          const classColor = isWarning ? "warn" : (o.interpretation.toLowerCase() === "worsening" ? "bad" : "ok");
          
          let evidenceHtml = "";
          if (o.evidence && o.evidence.length) {
            evidenceHtml = `
              <div style="margin-top: 6px;" class="small">
                <strong>Source Evidence:</strong>
                ${o.evidence.map(e => `
                  <span class="badge" style="font-size: 11px;">
                    ${e.source_type} (Doc ID: ${e.document_id})
                  </span>
                `).join("")}
              </div>
            `;
          }

          return `
            <div class="kpi small" style="margin-bottom: 8px; border-left: 3px solid var(--line); padding-left: 10px;">
              <div style="display: flex; justify-content: space-between; align-items: center;">
                <strong>${this.escape(o.category.toUpperCase())}</strong>
                <span class="${classColor}" style="font-weight: bold; font-size: 11px;">${this.escape(o.interpretation.toUpperCase())}</span>
              </div>
              <div style="margin-top: 4px;">${this.escape(o.fact)}</div>
              ${o.explanation ? `<div class="muted" style="margin-top: 2px; font-style: italic;">${this.escape(o.explanation)}</div>` : ''}
              ${evidenceHtml}
              <div class="muted small" style="margin-top: 6px; border-top: 1px dashed var(--line); padding-top: 4px;">
                ${this.escape(o.safety_boundary_disclaimer)}
              </div>
            </div>
          `;
        }).join("");
      }

      if (type === "trends_chart") {
        const trends = payload.trends || {};
        const keys = Object.keys(trends);
        const exclusions = payload.exclusions || [];
        if (!keys.length && !exclusions.length) {
          return '<div class="muted small">No metrics available for trend mapping yet. Import records or sync Health Connect observations to build longitudinal trends.</div>';
        }
        const trendRows = keys.map(k => {
              const tr = trends[k];
              const isPriority = k === payload.priority_metric;
              const provenance = tr.provenance || (tr.data_plane === "monitoring" ? "health_connect_observational" : "clinical");
              const planeLabel = provenance === "health_connect_observational"
                ? "Health Connect observational"
                : (provenance === "combined_clinical_and_health_connect" || tr.data_plane === "combined"
                  ? "Combined clinical + Health Connect observational"
                  : (provenance === "clinical" ? "Clinical / lab" : provenance));
              return `
                <div class="kpi small" style="display: flex; justify-content: space-between; align-items: center; border-left: 2px solid ${isPriority ? "var(--accent)" : "var(--line)"}; padding-left: 8px;">
                  <div>
                    <strong>${this.escape(k.toUpperCase())}</strong> ${isPriority ? '<span class="badge" style="font-size:9px; background:var(--accent); color:var(--bg)">Priority</span>' : ''}
                    <div class="muted">Sample count: ${tr.sample_count} · Latest value: ${tr.latest == null ? "—" : tr.latest}</div>
                    <div class="muted" style="font-size: 11px;">Source: ${this.escape(planeLabel)}</div>
                  </div>
                  <span class="badge ${tr.direction === "worsening" ? "bad" : "ok"}">${this.escape(tr.label)}</span>
                </div>
              `;
            }).join("");
        const exclusionRows = exclusions.map(item => `
              <div class="kpi small muted" style="border-left: 2px dashed var(--line); padding-left: 8px;">
                <strong>${this.escape(String(item.metric || "").replace(/_/g, " ").toUpperCase())}</strong>
                <div>${this.escape(item.message || "Intentionally excluded from Trends.")}</div>
                <div style="font-size: 11px;">Reason: ${this.escape(item.reason || "excluded")}</div>
              </div>
            `).join("");
        return `
          <div style="display: flex; flex-direction: column; gap: 8px;">
            ${trendRows}
            ${exclusionRows ? `<div class="small muted" style="margin-top:4px;"><strong>Explicit exclusions</strong></div>${exclusionRows}` : ""}
          </div>
        `;
      }

      if (type === "timeline_list") {
        const events = payload.events || [];
        if (!events.length) {
          return '<div class="muted small">Timeline is empty.</div>';
        }
        return `
          <div style="display: flex; flex-direction: column; gap: 6px;">
            ${events.map(ev => `
              <div class="kpi small" style="display: flex; justify-content: space-between; align-items: flex-start; gap: 10px;">
                <div>
                  <strong>${this.escape(ev.date.substring(0, 10))}</strong> · ${this.escape(ev.trend_impact || ev.summary || "Measurement updated")}
                  <div class="muted" style="font-size: 11px;">Category: ${this.escape(ev.primary_category)} · Provenance: ${this.escape(ev.provenance)}</div>
                </div>
                ${ev.severity ? `<span class="badge ${ev.severity === "critical" ? "bad" : "warn"}">${this.escape(ev.severity.toUpperCase())}</span>` : ''}
              </div>
            `).join("")}
          </div>
        `;
      }

      if (type === "import_entry") {
        const recent = payload.recent_records || [];
        return `
          <div class="small">
            <p><strong>${Number(payload.records_count || 0)}</strong> health records available.</p>
            ${recent.length ? `<div class="muted">Recent: ${recent.slice(0, 3).map(r => this.escape(r.original_filename || "Record")).join(" · ")}</div>` : '<div class="muted">No records have been added yet.</div>'}
            <p>Upload reports and review extracted metrics, provenance, trends, and observations.</p>
            <div style="margin-top: 8px;">
              <button type="button" data-open-health-records style="width: auto; padding: 6px 12px; margin: 0;">Open Health Records</button>
            </div>
          </div>
        `;
      }

      return `<pre class="small">${this.escape(JSON.stringify(payload, null, 2))}</pre>`;
    }

    escape(val) {
      if (val == null) return "";
      return String(val)
        .replace(/&/g, "&amp;")
        .replace(/</g, "&lt;")
        .replace(/>/g, "&gt;")
        .replace(/"/g, "&quot;");
    }
  }

  global.HCConsumerDashboard = new ConsumerDashboard();

})(typeof window !== "undefined" ? window : globalThis);
