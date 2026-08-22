/**
 * HC-301 — Always-On Health Guardian orchestrator (browser mirror).
 * event → baselines → rules → alerts → timeline → EventBus → status
 * Local-first; observational only. Never sends off-device notifications.
 */
(function (global) {
  "use strict";

  const OVERALL_STATES = [
    "NORMAL",
    "WATCH",
    "WARNING",
    "URGENT",
    "CRITICAL",
    "MONITORING_DEGRADED",
    "UNKNOWN",
  ];

  const KNOWN_LIMITATIONS = [
    "Samsung Health / Galaxy Watch / Libre support remains upload/parser and manual registry based in HC-301.",
    "Health Connect is not implemented in this phase.",
    "Galaxy Watch does not measure glucose.",
    "Samsung Watch blood pressure is user-initiated, not continuous.",
    "ECG is not continuously collected.",
    "A PWA cannot guarantee unrestricted background execution.",
    "Manufacturer CGM and device alarms must remain enabled.",
    "HealthChecker+ does not replace medical care or emergency services.",
    "No caregiver/SMS/email/emergency notifications are sent in HC-301.",
  ];

  const ABSOLUTE_THRESHOLDS = [
    {
      rule_id: "glucose_low",
      title: "Low glucose",
      category: "glucose",
      severity: "urgent",
      metric: "glucose",
      units: ["mg/dL"],
      operator: "lte",
      threshold: 70,
      recommended_next_step:
        "Review glucose reading and follow your clinician-approved hypoglycemia plan. Manufacturer CGM alarms must remain enabled.",
    },
    {
      rule_id: "glucose_very_low",
      title: "Very low glucose",
      category: "glucose",
      severity: "critical",
      metric: "glucose",
      units: ["mg/dL"],
      operator: "lte",
      threshold: 54,
      recommended_next_step:
        "Treat per clinician-approved plan and seek urgent medical care if needed. This app does not replace emergency services.",
    },
    {
      rule_id: "glucose_high",
      title: "High glucose",
      category: "glucose",
      severity: "warning",
      metric: "glucose",
      units: ["mg/dL"],
      operator: "gte",
      threshold: 250,
      recommended_next_step:
        "Review recent meals, medication timing, and CGM/meter confirmation with your care plan. No dosing advice is provided.",
    },
    {
      rule_id: "elevated_resting_hr",
      title: "Elevated resting heart rate",
      category: "cardiology",
      severity: "watch",
      metric: "resting_hr",
      units: ["bpm"],
      operator: "gte",
      threshold: 100,
      recommended_next_step: "Review context (illness, activity, medication). Wearable HR is observational.",
    },
    {
      rule_id: "low_resting_hr",
      title: "Unusually low resting heart rate",
      category: "cardiology",
      severity: "warning",
      metric: "resting_hr",
      units: ["bpm"],
      operator: "lte",
      threshold: 40,
      recommended_next_step: "If symptomatic, seek medical care. Observational wearable reading only.",
    },
    {
      rule_id: "low_oxygen_saturation",
      title: "Low oxygen saturation",
      category: "respiratory",
      severity: "urgent",
      metric: "oxygen_saturation",
      units: ["%"],
      operator: "lte",
      threshold: 92,
      recommended_next_step:
        "Confirm with a validated pulse oximeter if available and seek care if symptomatic.",
    },
  ];

  function utcNow() {
    return new Date().toISOString().replace(/\.\d{3}Z$/, "Z");
  }

  function uuid() {
    if (global.crypto && crypto.randomUUID) return crypto.randomUUID();
    return "g-" + Date.now().toString(36) + "-" + Math.random().toString(36).slice(2, 10);
  }

  function cmp(op, left, right) {
    if (op === "gte" || op === ">=") return left >= right;
    if (op === "lte" || op === "<=") return left <= right;
    if (op === "gt" || op === ">") return left > right;
    if (op === "lt" || op === "<") return left < right;
    if (op === "eq" || op === "==") return left === right;
    return false;
  }

  function severityRank() {
    return (global.HCAlertEngine && HCAlertEngine.SEVERITY_RANK) || {
      informational: 0,
      watch: 1,
      warning: 2,
      urgent: 3,
      critical: 4,
    };
  }

  function HCHealthGuardian() {
    this.baselines = new global.HCBaselineEngine();
    this.cgm = new global.HCCGMContinuity();
    this.alerts = new global.HCAlertEngine();
  }

  HCHealthGuardian.OVERALL_STATES = OVERALL_STATES;
  HCHealthGuardian.KNOWN_LIMITATIONS = KNOWN_LIMITATIONS;
  HCHealthGuardian.SAFETY_DISCLAIMER =
    (global.HCAlertEngine && HCAlertEngine.SAFETY_DISCLAIMER) ||
    "Observational only — not a diagnosis.";

  HCHealthGuardian.prototype.evaluate = function (opts) {
    opts = opts || {};
    const patientId = opts.patient_id || "default-patient";
    const nowTs = opts.now || utcNow();
    const auditId = uuid();
    const Bus = global.HCEventBus;
    const Vault = global.HCHealthVault;
    try {
      const baselineSummary = this.baselines.rebuild({
        patient_id: patientId,
        as_of: nowTs,
      });
      this.cgm.detectGlucoseGap({ patient_id: patientId, now: nowTs });
      const continuity = this.cgm.evaluateContinuity(patientId, nowTs);
      const latest = this._latestByMetric();
      const evaluations = this._evaluateRules({
        patient_id: patientId,
        continuity: continuity,
        pipeline_failure: !!opts.pipeline_failure,
        latest_by_metric: latest,
        now: nowTs,
      });
      const alertResults = [];
      evaluations.forEach((ev) => {
        const saved = this.alerts.ingestEvaluation(ev, {
          patient_id: patientId,
          source_event_ids: opts.source_event_ids,
          now: nowTs,
        });
        if (saved) {
          alertResults.push(saved);
          this._timelineAlertEvent(saved);
        }
      });
      const status = this.buildStatus({
        patient_id: patientId,
        now: nowTs,
        baseline_summary: baselineSummary,
        continuity: continuity,
        trigger: opts.trigger || "manual",
      });
      if (Vault && Vault.saveGuardianStatus) Vault.saveGuardianStatus(status);
      if (Bus && Bus.publish) {
        Bus.publish("GuardianEvaluated", {
          audit_id: auditId,
          overall_state: status.overall_state,
          alert_count: alertResults.length,
        });
      }
      return {
        ok: true,
        status: status,
        alerts: alertResults,
        evaluations: evaluations,
        continuity: continuity,
        baselines: baselineSummary,
        audit_id: auditId,
        disclaimer: KNOWN_LIMITATIONS[0],
      };
    } catch (exc) {
      const degraded = {
        ok: false,
        overall_state: "MONITORING_DEGRADED",
        errors: [String(exc && exc.message ? exc.message : exc)],
        evaluated_at: nowTs,
        known_limitations: KNOWN_LIMITATIONS,
      };
      if (Vault && Vault.saveGuardianStatus) Vault.saveGuardianStatus(degraded);
      if (Bus && Bus.publish) {
        Bus.publish("GuardianEvaluationFailed", {
          error: String(exc && exc.name ? exc.name : "Error"),
        });
      }
      try {
        this.alerts.ingestEvaluation(
          {
            triggered: true,
            rule_id: "monitoring_pipeline_failure",
            rule_version: "1.0.0",
            title: "Monitoring pipeline failure",
            message: "Guardian evaluation failed: " + (exc && exc.name ? exc.name : "Error"),
            severity: "urgent",
            category: "system",
            metrics: [],
            evidence: { error: String(exc && exc.name ? exc.name : exc) },
            recommended_next_step:
              "Retry evaluation. Background PWA checks are not guaranteed.",
          },
          { patient_id: patientId, now: nowTs }
        );
      } catch (_) {}
      return { ok: false, status: degraded, errors: degraded.errors };
    }
  };

  /** Convenience refresh used by dashboard / Guardian tab. */
  HCHealthGuardian.prototype.refresh = function (opts) {
    opts = Object.assign({ trigger: "ui_refresh" }, opts || {});
    const self = this;
    const headers = (function () {
      try {
        if (global.HCConsumerDashboard && typeof global.HCConsumerDashboard.getAuthorizationHeaders === "function") {
          const h = global.HCConsumerDashboard.getAuthorizationHeaders();
          if (h && h.Authorization) return h;
        }
      } catch (e) { /* fall through */ }
      try {
        const raw = global.sessionStorage.getItem("hc_auth_session");
        if (!raw) return null;
        const parsed = JSON.parse(raw);
        return parsed.token ? { Authorization: "Bearer " + parsed.token } : null;
      } catch (e) {
        return null;
      }
    })();
    if (headers) {
      return Promise.all([
        fetch("/api/guardian/status", { headers: headers }).then(function (r) {
          return r.ok ? r.json() : null;
        }),
        fetch("/api/guardian/alerts?active_only=true", { headers: headers }).then(function (r) {
          return r.ok ? r.json() : null;
        }),
      ])
        .then(function (parts) {
          const status = parts[0];
          const alertsPayload = parts[1];
          if (status) {
            self._serverStatus = status;
            self._serverAlerts = (alertsPayload && alertsPayload.alerts) || [];
            self.renderUI({ status: status, server_alerts: self._serverAlerts, source: "server" });
            return { ok: true, status: status, alerts: self._serverAlerts, source: "server" };
          }
          const result = self.evaluate(opts);
          self.renderUI(result);
          return result;
        })
        .catch(function () {
          const result = self.evaluate(opts);
          self.renderUI(result);
          return result;
        });
    }
    const result = this.evaluate(opts);
    this.renderUI(result);
    return Promise.resolve(result);
  };

  HCHealthGuardian.prototype.getStatus = function (patientId) {
    const Vault = global.HCHealthVault;
    const status = Vault && Vault.getGuardianStatus ? Vault.getGuardianStatus() : null;
    if (status && status.patient_id === (patientId || "default-patient")) return status;
    return this.buildStatus({ patient_id: patientId || "default-patient" });
  };

  HCHealthGuardian.prototype.buildStatus = function (opts) {
    opts = opts || {};
    const patientId = opts.patient_id || "default-patient";
    const nowTs = opts.now || utcNow();
    const Vault = global.HCHealthVault;
    const baselineSummary =
      opts.baseline_summary ||
      (Vault && Vault.getBaselines ? Vault.getBaselines() : {}) ||
      {};
    const continuity =
      opts.continuity ||
      (Vault && Vault.getCgmContinuity ? Vault.getCgmContinuity() : null) ||
      this.cgm.evaluateContinuity(patientId);
    const counts = this.alerts.activeCounts(patientId);
    const latest = this._latestValues();
    const reasons = [];
    let measurementCount = 0;
    if (Vault && Vault.listMeasurements) {
      const ms = Vault.listMeasurements() || [];
      for (let i = 0; i < ms.length; i++) {
        const m = ms[i];
        const pid = m.patient_id || m.patientId || "default-patient";
        if (pid === patientId) measurementCount += 1;
      }
    }
    const emptyVault = measurementCount === 0 && !(counts && counts.total);
    let overall = emptyVault ? "UNKNOWN" : "NORMAL";
    if (emptyVault) {
      reasons.push("No confirmed measurements in vault for this patient.");
    }
    if (counts.critical) {
      overall = "CRITICAL";
      reasons.unshift(counts.critical + " critical alert(s) active.");
    } else if (counts.urgent) {
      overall = "URGENT";
      reasons.unshift(counts.urgent + " urgent alert(s) active.");
    } else if (counts.warning) {
      if (overall !== "CRITICAL" && overall !== "URGENT") overall = "WARNING";
      reasons.unshift(counts.warning + " warning alert(s) active.");
    } else if (counts.watch || counts.informational) {
      if (overall === "NORMAL" || overall === "UNKNOWN") overall = "WATCH";
      reasons.unshift("Watch/informational alerts present.");
    }

    const contState = continuity.state;
    const rank = severityRank();
    if (
      contState === "CRITICAL_SHORTAGE" ||
      contState === "SENSOR_EXPIRED" ||
      contState === "SIGNAL_LOSS" ||
      contState === "DATA_PIPELINE_FAILURE"
    ) {
      if (overall !== "CRITICAL" && overall !== "URGENT") {
        overall = contState === "CRITICAL_SHORTAGE" ? "CRITICAL" : "URGENT";
      }
      (continuity.reasons || []).slice(0, 3).forEach((r) => reasons.push(r));
    } else if (
      contState === "SENSOR_EXPIRING" ||
      contState === "REORDER_REQUIRED" ||
      contState === "INVENTORY_UNKNOWN"
    ) {
      if (emptyVault && contState === "INVENTORY_UNKNOWN") {
        (continuity.reasons || []).slice(0, 2).forEach((r) => reasons.push(r));
      } else {
        const key = overall === "NORMAL" || overall === "UNKNOWN" ? "informational" : overall.toLowerCase();
        if ((rank[key] || 0) < (rank.warning || 2)) {
          if (overall === "NORMAL" || overall === "UNKNOWN") {
            overall = contState === "INVENTORY_UNKNOWN" ? "WATCH" : "WARNING";
          }
        }
        (continuity.reasons || []).slice(0, 2).forEach((r) => reasons.push(r));
      }
    }

    if (!reasons.length) reasons.push("No active Guardian warnings under current rules.");

    const baselines = baselineSummary.baselines || {};
    const readyMetrics = Object.keys(baselines).filter((k) => baselines[k].ready);
    const inv = continuity.inventory || {};
    return {
      schema_version: "hc.guardian_status.v1",
      patient_id: patientId,
      overall_state: overall,
      reasons: reasons,
      monitoring_active: true,
      last_evaluation_time: nowTs,
      active_alert_count_by_severity: counts,
      glucose_feed_state: this._glucoseFeedState(continuity, latest),
      cgm_continuity_state: contState,
      active_sensor_time_remaining_hours: continuity.hours_remaining,
      spare_sensor_inventory: inv.unused_sensor_count,
      projected_coverage_days: inv.projected_coverage_days,
      next_reorder_deadline: inv.reorder_deadline,
      active_sensor: continuity.active_sensor,
      latest_bp: latest.bp,
      latest_pulse: latest.pulse,
      latest_glucose: latest.glucose,
      latest_oxygen_saturation: latest.oxygen_saturation,
      baseline_readiness: {
        ready_metrics: readyMetrics,
        ready_count: readyMetrics.length,
        total_tracked: Object.keys(baselines).length,
      },
      source_connection_availability: {
        health_connect: false,
        live_libre_api: false,
        samsung_live_api: false,
        upload_parsers: true,
        manual_entry: true,
      },
      background_capability: {
        supported: !!(navigator.serviceWorker),
        permission_required: true,
        limited: true,
        unavailable: false,
        note:
          "Service worker foundation present; OS may suspend PWAs. Continuous execution is not guaranteed.",
      },
      known_limitations: KNOWN_LIMITATIONS,
      trigger: opts.trigger || "status",
      disclaimer:
        "Observational safety companion only. Not a medical device claim. " +
        "Does not replace manufacturer alarms or emergency care.",
    };
  };

  HCHealthGuardian.prototype._evaluateRules = function (ctx) {
    const Rules = global.HCClinicalRules;
    let results = [];
    if (Rules && typeof Rules.evaluateGuardianRules === "function") {
      results = Rules.evaluateGuardianRules(ctx) || [];
    } else {
      results = this._evaluateAbsoluteSubset(ctx);
    }
    // Continuity-driven rules
    const continuity = ctx.continuity || {};
    const patientId = ctx.patient_id || "default-patient";
    const states = continuity.states || [];
    if (states.indexOf("SENSOR_EXPIRING") >= 0 || continuity.state === "SENSOR_EXPIRING") {
      results.push({
        triggered: true,
        rule_id: "cgm_sensor_expiring",
        rule_version: "1.0.0",
        title: "CGM sensor expiring",
        category: "cgm_continuity",
        severity: "warning",
        message: "Active CGM sensor is approaching expected expiry.",
        metrics: ["cgm_sensor"],
        evidence: { hours_remaining: continuity.hours_remaining },
        deduplication_key: patientId + "|cgm_sensor_expiring",
        recommended_next_step: "Prepare a replacement sensor and confirm inventory before expiry.",
        safety_disclaimer: HCHealthGuardian.SAFETY_DISCLAIMER,
      });
    }
    if (states.indexOf("SENSOR_EXPIRED") >= 0 || continuity.state === "SENSOR_EXPIRED") {
      results.push({
        triggered: true,
        rule_id: "cgm_sensor_expired",
        rule_version: "1.0.0",
        title: "CGM sensor expired",
        category: "cgm_continuity",
        severity: "urgent",
        message: "Active CGM sensor is past expected expiry.",
        metrics: ["cgm_sensor"],
        evidence: { active_sensor: continuity.active_sensor },
        deduplication_key: patientId + "|cgm_sensor_expired",
        recommended_next_step:
          "Replace the sensor per manufacturer instructions and update the sensor registry.",
        safety_disclaimer: HCHealthGuardian.SAFETY_DISCLAIMER,
      });
    }
    if (
      states.indexOf("REORDER_REQUIRED") >= 0 ||
      continuity.state === "REORDER_REQUIRED"
    ) {
      results.push({
        triggered: true,
        rule_id: "cgm_reserve_below_minimum",
        rule_version: "1.0.0",
        title: "CGM reserve below minimum",
        category: "cgm_continuity",
        severity: "warning",
        message: "Unused CGM sensors are below the configured protected reserve.",
        metrics: ["cgm_inventory"],
        evidence: continuity.inventory || {},
        deduplication_key: patientId + "|cgm_reserve",
        recommended_next_step:
          "Reorder sensors to restore the protected reserve. Confirm inventory manually.",
        safety_disclaimer: HCHealthGuardian.SAFETY_DISCLAIMER,
      });
    } else if ((continuity.inventory || {}).confidence === "unknown") {
      results.push({
        triggered: true,
        rule_id: "cgm_inventory_unknown",
        rule_version: "1.0.0",
        title: "CGM inventory unknown",
        category: "cgm_continuity",
        severity: "watch",
        message: "Sensor inventory has not been confirmed.",
        metrics: ["cgm_inventory"],
        evidence: continuity.inventory || {},
        deduplication_key: patientId + "|cgm_inventory_unknown",
        recommended_next_step: "Confirm unused sensor count in the Guardian tab.",
        safety_disclaimer: HCHealthGuardian.SAFETY_DISCLAIMER,
      });
    }
    if (
      states.indexOf("CRITICAL_SHORTAGE") >= 0 ||
      continuity.state === "CRITICAL_SHORTAGE"
    ) {
      results.push({
        triggered: true,
        rule_id: "cgm_projected_coverage_shortfall",
        rule_version: "1.0.0",
        title: "CGM projected coverage shortfall",
        category: "cgm_continuity",
        severity: "urgent",
        message: "Projected CGM coverage is insufficient for configured buffers.",
        metrics: ["cgm_inventory"],
        evidence: continuity.inventory || {},
        deduplication_key: patientId + "|cgm_coverage",
        recommended_next_step:
          "Reorder promptly; projected coverage is below configured buffer needs.",
        safety_disclaimer: HCHealthGuardian.SAFETY_DISCLAIMER,
      });
    }
    const gaps = continuity.open_data_gaps || [];
    if (gaps.length) {
      const gap = gaps[0];
      results.push({
        triggered: true,
        rule_id: "glucose_data_gap",
        rule_version: "1.0.0",
        title: "Glucose data gap",
        category: "cgm_continuity",
        severity:
          gap.escalation_status === "critical"
            ? "critical"
            : gap.escalation_status === "urgent"
              ? "urgent"
              : "warning",
        message: "Glucose data gap detected. No data is never treated as normal.",
        metrics: ["glucose"],
        evidence: gap,
        deduplication_key: patientId + "|glucose_data_gap",
        recommended_next_step:
          "Confirm sensor/reader connectivity or enter a confirmed reading. No data is never treated as normal.",
        safety_disclaimer: HCHealthGuardian.SAFETY_DISCLAIMER,
      });
    }
    if (ctx.pipeline_failure) {
      results.push({
        triggered: true,
        rule_id: "monitoring_pipeline_failure",
        rule_version: "1.0.0",
        title: "Monitoring pipeline failure",
        category: "system",
        severity: "urgent",
        message: "Guardian evaluation or import pipeline reported a failure.",
        metrics: [],
        evidence: { pipeline_failure: true },
        recommended_next_step:
          "Retry evaluation or re-import the latest confirmed record. Background PWA checks are not guaranteed.",
        safety_disclaimer: HCHealthGuardian.SAFETY_DISCLAIMER,
      });
    }
    // Multi-metric BP
    const latest = ctx.latest_by_metric || {};
    const sys =
      latest.systolic || latest.systolic_bp;
    const dia = latest.diastolic || latest.diastolic_bp;
    if (sys && dia) {
      const sv = Number(sys.value);
      const dv = Number(dia.value);
      if (Number.isFinite(sv) && Number.isFinite(dv) && sv >= 140 && dv >= 90) {
        results.push({
          triggered: true,
          rule_id: "elevated_blood_pressure",
          rule_version: "1.0.0",
          title: "Elevated blood pressure",
          category: "blood_pressure",
          severity: "warning",
          message: "Elevated blood pressure",
          metrics: ["systolic", "diastolic"],
          evidence: { mode: "all", metrics: ["systolic", "diastolic"] },
          deduplication_key: patientId + "|elevated_blood_pressure",
          recommended_next_step:
            "Samsung BP is user-initiated, not continuous. Follow clinician guidance for repeat checks.",
          safety_disclaimer: HCHealthGuardian.SAFETY_DISCLAIMER,
        });
      }
      if (
        Number.isFinite(sv) &&
        Number.isFinite(dv) &&
        (sv <= 90 || dv <= 50)
      ) {
        results.push({
          triggered: true,
          rule_id: "low_blood_pressure",
          rule_version: "1.0.0",
          title: "Unusually low blood pressure",
          category: "blood_pressure",
          severity: "warning",
          message: "Unusually low blood pressure",
          metrics: ["systolic", "diastolic"],
          evidence: { mode: "any", metrics: ["systolic", "diastolic"] },
          deduplication_key: patientId + "|low_blood_pressure",
          recommended_next_step: "If dizzy or unwell, seek medical care. Observational only.",
          safety_disclaimer: HCHealthGuardian.SAFETY_DISCLAIMER,
        });
      }
    }
    // Baseline deviation for glucose when ready
    const glucose = latest.glucose;
    if (glucose) {
      const dev = this.baselines.deviation("glucose", glucose.value, {
        units: glucose.units,
        patient_id: patientId,
      });
      if (dev.available && dev.outside_band) {
        results.push({
          triggered: true,
          rule_id: "baseline_deviation_glucose",
          rule_version: "1.0.0",
          title: "Glucose outside personal baseline band",
          category: "baseline",
          severity: "watch",
          message: "glucose outside personal baseline band.",
          metrics: ["glucose"],
          evidence: dev,
          deduplication_key: patientId + "|baseline_deviation_glucose|glucose",
          recommended_next_step:
            "Value is outside your personal rolling band when baseline is ready. Not a diagnosis.",
          safety_disclaimer: HCHealthGuardian.SAFETY_DISCLAIMER,
        });
      }
    }
    return results;
  };

  HCHealthGuardian.prototype._evaluateAbsoluteSubset = function (ctx) {
    const latest = ctx.latest_by_metric || {};
    const patientId = ctx.patient_id || "default-patient";
    const out = [];
    ABSOLUTE_THRESHOLDS.forEach((rule) => {
      const row = latest[rule.metric];
      if (!row) return;
      const val = Number(row.value);
      if (!Number.isFinite(val)) return;
      const units = row.units;
      if (units && rule.units && rule.units.length && rule.units.indexOf(units) < 0) return;
      if (!cmp(rule.operator, val, rule.threshold)) return;
      out.push({
        triggered: true,
        rule_id: rule.rule_id,
        rule_version: "1.0.0",
        title: rule.title,
        category: rule.category,
        severity: rule.severity,
        metric: rule.metric,
        metrics: [rule.metric],
        message:
          rule.title +
          ": observed " +
          val +
          " " +
          (units || "") +
          " (threshold " +
          rule.operator +
          " " +
          rule.threshold +
          ").",
        evidence: {
          value: val,
          units: units,
          measured_at: row.measured_at,
          threshold: rule.threshold,
        },
        deduplication_key: patientId + "|" + rule.rule_id + "|" + rule.metric,
        recommended_next_step: rule.recommended_next_step,
        safety_disclaimer: HCHealthGuardian.SAFETY_DISCLAIMER,
      });
    });
    return out;
  };

  HCHealthGuardian.prototype._latestByMetric = function () {
    const Vault = global.HCHealthVault;
    const out = {};
    const items = Vault && Vault.listMeasurements ? Vault.listMeasurements() : [];
    items.forEach((m) => {
      const metric = String(m.metric || "");
      if (!metric) return;
      const prev = out[metric];
      if (!prev || String(m.measured_at || "") > String(prev.measured_at || "")) {
        out[metric] = m;
      }
    });
    return out;
  };

  HCHealthGuardian.prototype._latestValues = function () {
    const by = this._latestByMetric();
    const glucose = by.glucose;
    const sys = by.systolic || by.systolic_bp;
    const dia = by.diastolic || by.diastolic_bp;
    const pulse = by.resting_hr || by.heart_rate || by.average_hr;
    const spo2 = by.oxygen_saturation;
    let bp = null;
    if (sys && dia) {
      bp = {
        systolic: sys.value,
        diastolic: dia.value,
        units: "mmHg",
        measured_at: sys.measured_at || dia.measured_at,
      };
    }
    return {
      glucose: glucose
        ? { value: glucose.value, units: glucose.units, measured_at: glucose.measured_at }
        : null,
      bp: bp,
      pulse: pulse
        ? {
            value: pulse.value,
            units: pulse.units || "bpm",
            measured_at: pulse.measured_at,
          }
        : null,
      oxygen_saturation: spo2
        ? {
            value: spo2.value,
            units: spo2.units || "%",
            measured_at: spo2.measured_at,
          }
        : null,
    };
  };

  HCHealthGuardian.prototype._glucoseFeedState = function (continuity, latest) {
    if (continuity.state === "SIGNAL_LOSS") return "gap";
    if (!latest.glucose) return "no_data";
    return "available_upload_or_manual";
  };

  HCHealthGuardian.prototype._timelineAlertEvent = function (alert) {
    const Vault = global.HCHealthVault;
    if (!Vault || !Vault.appendTimelineEvent) return;
    Vault.appendTimelineEvent({
      event_id: uuid(),
      kind: "alert",
      category: alert.category || "alert",
      measured_at: alert.last_detected_at || alert.created_at || utcNow(),
      imported_at: utcNow(),
      provenance: "health_guardian",
      severity: alert.severity,
      summary: alert.title,
      payload: {
        alert_id: alert.alert_id,
        status: alert.status,
        rule_id: alert.rule_id,
      },
      dedupe_key: "alert|" + alert.alert_id + "|" + alert.occurrence_count,
    });
  };

  function esc(s) {
    return String(s == null ? "" : s)
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;");
  }

  HCHealthGuardian.prototype.renderUI = function (result) {
    const status = (result && result.status) || this.getStatus();
    const banner = document.getElementById("guardian_critical_banner");
    if (banner) {
      const sev = status.overall_state;
      if (sev === "CRITICAL" || sev === "URGENT") {
        banner.style.display = "block";
        banner.className =
          "alert " + (sev === "CRITICAL" ? "bad" : "warn");
        banner.innerHTML =
          "<strong>" +
          esc(sev) +
          ":</strong> " +
          esc((status.reasons || []).join(" "));
      } else {
        banner.style.display = "none";
        banner.innerHTML = "";
      }
    }
    const statusEl = document.getElementById("guardian_status_panel");
    if (statusEl) {
      const counts = status.active_alert_count_by_severity || {};
      statusEl.innerHTML =
        `<div class="kpi"><strong>Overall:</strong> <span class="${
          status.overall_state === "CRITICAL" || status.overall_state === "URGENT"
            ? "bad"
            : status.overall_state === "WARNING"
              ? "warn"
              : "ok"
        }">${esc(status.overall_state || "UNKNOWN")}</span></div>` +
        `<div class="kpi small"><strong>CGM continuity:</strong> ${esc(
          status.cgm_continuity_state || "—"
        )}</div>` +
        `<div class="kpi small"><strong>Active alerts:</strong> critical ${esc(
          counts.critical || 0
        )} · urgent ${esc(counts.urgent || 0)} · warning ${esc(
          counts.warning || 0
        )} · watch ${esc(counts.watch || 0)}</div>` +
        `<div class="kpi small"><strong>Last evaluation:</strong> ${esc(
          status.last_evaluation_time
            ? new Date(status.last_evaluation_time).toLocaleString()
            : "—"
        )}</div>` +
        `<div class="small muted">${esc(status.disclaimer || "")}</div>`;
    }
    const alertsEl = document.getElementById("guardian_alerts_list");
    if (alertsEl) {
      const items =
        result && result.server_alerts
          ? result.server_alerts
          : this.alerts.listAlerts({ active_only: true });
      alertsEl.innerHTML = items.length
        ? items
            .map((a) => {
              const alertId = a.alert_id || a.id;
              return (
                `<div class="kpi">` +
                `<div><strong class="${
                  a.severity === "critical" || a.severity === "urgent" ? "bad" : "warn"
                }">${esc(a.severity)}</strong> — ${esc(a.title)}</div>` +
                `<div class="small">${esc(a.message)}</div>` +
                `<div class="small muted">${esc(a.recommended_next_step || "")}</div>` +
                (result && result.source === "server"
                  ? `<div class="small muted">Source: encrypted vault Guardian engine</div>`
                  : `<div class="vault-batch-actions">` +
                    `<button type="button" class="secondary" onclick="HCHealthGuardian.ack('${esc(
                      alertId
                    )}')">Acknowledge</button>` +
                    `<button type="button" class="secondary" onclick="HCHealthGuardian.resolve('${esc(
                      alertId
                    )}')">Resolve</button>` +
                    `</div>`) +
                `</div>`
              );
            })
            .join("")
        : '<div class="muted small">No active alerts.</div>';
    }
    const baseEl = document.getElementById("guardian_baselines");
    if (baseEl) {
      const summaries = this.baselines.getSummaries();
      const bases = summaries.baselines || {};
      const keys = Object.keys(bases);
      baseEl.innerHTML = keys.length
        ? keys
            .map((k) => {
              const b = bases[k];
              return (
                `<div class="kpi small"><strong>${esc(k)}</strong>: ` +
                `median ${esc(b.median != null ? Number(b.median).toFixed(1) : "—")} ` +
                `(n=${esc(b.sample_count)}) ` +
                `<span class="${b.ready ? "ok" : "warn"}">${
                  b.ready ? "ready" : "insufficient"
                }</span></div>`
              );
            })
            .join("")
        : '<div class="muted small">No baselines yet (need confirmed samples).</div>';
    }
    const invEl = document.getElementById("guardian_inventory_summary");
    if (invEl) {
      const inv = this.cgm.getInventory();
      invEl.innerHTML =
        `<div class="kpi small"><strong>Unused sensors:</strong> ${esc(
          inv.unused_sensor_count
        )} · status ${esc(inv.status)} · confidence ${esc(inv.confidence)}</div>` +
        `<div class="kpi small"><strong>Projected coverage:</strong> ${esc(
          inv.projected_coverage_days
        )} days · reorder by ${esc(
          inv.reorder_deadline
            ? new Date(inv.reorder_deadline).toLocaleDateString()
            : "—"
        )}</div>`;
    }
    const sensorsEl = document.getElementById("guardian_sensors_list");
    if (sensorsEl) {
      const sensors = this.cgm.listSensors();
      sensorsEl.innerHTML = sensors.length
        ? sensors
            .map((s) => {
              return (
                `<div class="kpi small"><strong>${esc(s.model)}</strong> · ${esc(
                  s.status
                )}` +
                `<div class="muted">${esc(s.sensor_id)}</div>` +
                (s.status === "planned"
                  ? `<button type="button" class="secondary" onclick="HCHealthGuardian.activateSensor('${esc(
                      s.sensor_id
                    )}')">Activate</button>`
                  : "") +
                `</div>`
              );
            })
            .join("")
        : '<div class="muted small">No sensors registered.</div>';
    }
    const dashSnap = document.getElementById("guardian_dash_snapshot");
    if (dashSnap) {
      dashSnap.innerHTML =
        `<div class="kpi"><strong>Guardian:</strong> ${esc(
          status.overall_state || "—"
        )}</div>` +
        `<div class="small muted">${esc((status.reasons || [])[0] || "")}</div>`;
    }
  };

  // Singleton helpers for inline onclick wiring
  const singleton = new HCHealthGuardian();
  HCHealthGuardian.instance = singleton;
  HCHealthGuardian.refresh = function (opts) {
    return singleton.refresh(opts);
  };
  HCHealthGuardian.evaluate = function (opts) {
    return singleton.evaluate(opts);
  };
  HCHealthGuardian.ack = function (alertId) {
    singleton.alerts.acknowledge(alertId);
    singleton.refresh({ trigger: "ack" });
  };
  HCHealthGuardian.resolve = function (alertId) {
    const r = singleton.alerts.resolve(alertId);
    if (!r.ok && r.errors && r.errors.indexOf("critical_requires_acknowledgement") >= 0) {
      alert("Critical alerts must be acknowledged before resolve.");
    }
    singleton.refresh({ trigger: "resolve" });
  };
  HCHealthGuardian.activateSensor = function (sensorId) {
    singleton.cgm.activateSensor(sensorId);
    singleton.refresh({ trigger: "sensor_activate" });
  };
  HCHealthGuardian.registerSensorFromForm = function () {
    const model = (document.getElementById("guardian_sensor_model") || {}).value || "FreeStyle Libre";
    const serial = (document.getElementById("guardian_sensor_serial") || {}).value || null;
    singleton.cgm.registerSensor({ model: model, serial_or_reference: serial });
    singleton.refresh({ trigger: "sensor_register" });
  };
  HCHealthGuardian.saveInventoryFromForm = function () {
    const countEl = document.getElementById("guardian_unused_count");
    const count = countEl ? Number(countEl.value) : 0;
    singleton.cgm.updateInventory({
      unused_sensor_count: Number.isFinite(count) && count >= 0 ? count : 0,
      confidence: "confirmed",
    });
    singleton.refresh({ trigger: "inventory_update" });
  };
  HCHealthGuardian.renderTimelineFiltered = function () {
    const el = document.getElementById("guardian_timeline");
    if (!el || !global.HCHealthTimeline) return;
    const severity = (document.getElementById("guardian_tl_severity") || {}).value || "";
    const category = (document.getElementById("guardian_tl_category") || {}).value || "";
    const entries = HCHealthTimeline.build({
      severity: severity || null,
      category: category || null,
      include_guardian_events: true,
      include_hc_v6: true,
    });
    if (!entries.length) {
      el.innerHTML = '<div class="muted small">No timeline events for this filter.</div>';
      return;
    }
    el.innerHTML = entries
      .slice(0, 40)
      .map((e) => {
        const title =
          e.summary ||
          (e.document && (e.document.document_type || e.document.original_filename)) ||
          e.entry_kind ||
          "event";
        return (
          `<div class="kpi small"><strong>${esc(
            e.date ? new Date(e.date).toLocaleString() : "—"
          )}</strong> · ${esc(e.entry_kind || "document")}` +
          (e.severity ? ` · <span class="warn">${esc(e.severity)}</span>` : "") +
          `<div>${esc(title)}</div></div>`
        );
      })
      .join("");
  };

  global.HCHealthGuardian = HCHealthGuardian;
})(typeof window !== "undefined" ? window : globalThis);
