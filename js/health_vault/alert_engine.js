/**
 * HC-301 — Browser Alert Engine (mirrors backend HCAlertEngine / AlertEngine).
 * Observational only — not a diagnosis. Local-first; never sends off-device.
 */
(function (global) {
  "use strict";

  const SAFETY_DISCLAIMER =
    "Observational only — not a diagnosis. HealthChecker+ does not replace " +
    "FreeStyle Libre alarms, Samsung Health Monitor, medical care, or emergency services. " +
    "No medication or insulin dosing advice is provided.";

  const SEVERITIES = ["informational", "watch", "warning", "urgent", "critical"];
  const SEVERITY_RANK = {};
  SEVERITIES.forEach((s, i) => {
    SEVERITY_RANK[s] = i;
  });

  const LS_KEY = "HC_GUARDIAN_ALERTS_V1";
  const DEFAULT_COOLDOWNS = {
    informational: 360,
    watch: 180,
    warning: 60,
    urgent: 30,
    critical: 15,
  };

  function utcNow() {
    return new Date().toISOString().replace(/\.\d{3}Z$/, "Z");
  }

  function uuid() {
    if (global.crypto && crypto.randomUUID) return crypto.randomUUID();
    return "a-" + Date.now().toString(36) + "-" + Math.random().toString(36).slice(2, 10);
  }

  function parseIso(ts) {
    if (!ts) return null;
    const t = Date.parse(String(ts).replace("Z", "+00:00"));
    return Number.isFinite(t) ? t / 1000 : null;
  }

  function addMinutes(isoTs, minutes) {
    const base = parseIso(isoTs);
    const ms = (base != null ? base * 1000 : Date.now()) + minutes * 60 * 1000;
    return new Date(ms).toISOString().replace(/\.\d{3}Z$/, "Z");
  }

  function loadAlertsRaw() {
    const Vault = global.HCHealthVault;
    if (Vault && typeof Vault.listAlerts === "function") {
      try {
        return Vault.listAlerts();
      } catch (_) {}
    }
    try {
      const raw = localStorage.getItem(LS_KEY);
      if (!raw) return [];
      const parsed = JSON.parse(raw);
      return Array.isArray(parsed) ? parsed : [];
    } catch (_) {
      return [];
    }
  }

  function saveAlertsRaw(alerts) {
    const Vault = global.HCHealthVault;
    if (Vault && typeof Vault.saveAlerts === "function") {
      try {
        Vault.saveAlerts(alerts);
        return;
      } catch (_) {}
    }
    try {
      localStorage.setItem(LS_KEY, JSON.stringify(alerts));
    } catch (_) {}
  }

  function upsertAlert(alert) {
    const Vault = global.HCHealthVault;
    if (Vault && typeof Vault.upsertAlert === "function") {
      try {
        return Vault.upsertAlert(alert);
      } catch (_) {}
    }
    const items = loadAlertsRaw();
    const id = alert.alert_id;
    let found = false;
    for (let i = 0; i < items.length; i++) {
      if (items[i].alert_id === id) {
        items[i] = alert;
        found = true;
        break;
      }
    }
    if (!found) items.push(alert);
    saveAlertsRaw(items);
    return alert;
  }

  function appendAudit(alert, at, action, detail) {
    const hist = Array.isArray(alert.audit_history) ? alert.audit_history.slice() : [];
    hist.push({ at: at, action: action, detail: detail || {} });
    alert.audit_history = hist;
  }

  function emit(name, payload) {
    const Bus = global.HCEventBus;
    if (!Bus || !Bus.publish) return;
    try {
      Bus.publish(name, {
        alert_id: payload && payload.alert_id,
        severity: payload && payload.severity,
      });
    } catch (_) {}
  }

  function HCAlertEngine(opts) {
    opts = opts || {};
    this.cooldowns = opts.cooldowns_minutes || Object.assign({}, DEFAULT_COOLDOWNS);
  }

  HCAlertEngine.SAFETY_DISCLAIMER = SAFETY_DISCLAIMER;
  HCAlertEngine.SEVERITIES = SEVERITIES;
  HCAlertEngine.SEVERITY_RANK = SEVERITY_RANK;

  HCAlertEngine.prototype.listAlerts = function (filter) {
    filter = filter || {};
    let items = loadAlertsRaw().slice();
    if (filter.patient_id) {
      items = items.filter((a) => a.patient_id === filter.patient_id);
    }
    if (filter.status) {
      items = items.filter((a) => a.status === filter.status);
    }
    if (filter.active_only) {
      items = items.filter((a) =>
        ["active", "acknowledged", "snoozed"].indexOf(a.status) >= 0
      );
    }
    return items;
  };

  HCAlertEngine.prototype.getAlert = function (alertId) {
    const items = loadAlertsRaw();
    for (let i = 0; i < items.length; i++) {
      if (items[i].alert_id === alertId) return items[i];
    }
    return null;
  };

  HCAlertEngine.prototype.ingestEvaluation = function (evaluation, opts) {
    opts = opts || {};
    if (!evaluation || !evaluation.triggered) return null;
    const nowTs = opts.now || utcNow();
    const patientId = opts.patient_id || "default-patient";
    let severity = String(evaluation.severity || "watch");
    if (!(severity in SEVERITY_RANK)) severity = "watch";
    const ruleId = String(evaluation.rule_id || "unknown");
    const metrics = (evaluation.metrics || []).slice();
    if (evaluation.metric && metrics.indexOf(evaluation.metric) < 0) {
      metrics.unshift(evaluation.metric);
    }
    const dedupe = String(
      evaluation.deduplication_key ||
        patientId + "|" + ruleId + "|" + metrics.slice().sort().join("+")
    );

    const existing = this._findOpenByDedupe(dedupe, patientId);
    if (existing) {
      return this._updateExisting(existing, evaluation, severity, opts.source_event_ids, nowTs);
    }

    if (this._inCooldown(dedupe, patientId, nowTs) && severity !== "critical") {
      return null;
    }

    const alert = {
      alert_id: uuid(),
      patient_id: patientId,
      rule_id: ruleId,
      rule_version: String(evaluation.rule_version || "1.0.0"),
      title: String(evaluation.title || ruleId),
      message: String(evaluation.message || evaluation.title || ruleId),
      severity: severity,
      category: String(evaluation.category || "general"),
      metrics: metrics,
      source_event_ids: (opts.source_event_ids || evaluation.source_event_ids || []).slice(),
      created_at: nowTs,
      updated_at: nowTs,
      first_detected_at: nowTs,
      last_detected_at: nowTs,
      occurrence_count: 1,
      status: "active",
      acknowledgement_state: "none",
      acknowledged_at: null,
      resolved_at: null,
      snoozed_until: null,
      cooldown_until: null,
      deduplication_key: dedupe,
      evidence: Object.assign({}, evaluation.evidence || {}),
      recommended_next_step: String(evaluation.recommended_next_step || ""),
      safety_disclaimer: String(evaluation.safety_disclaimer || SAFETY_DISCLAIMER),
      audit_history: [
        { at: nowTs, action: "created", detail: { severity: severity, rule_id: ruleId } },
      ],
    };
    const saved = upsertAlert(alert);
    emit("AlertCreated", saved);
    return saved;
  };

  HCAlertEngine.prototype.acknowledge = function (alertId, opts) {
    opts = opts || {};
    const alert = this.getAlert(alertId);
    if (!alert) return { ok: false, errors: ["alert_not_found"] };
    const nowTs = opts.now || utcNow();
    alert.acknowledgement_state = "acknowledged";
    alert.acknowledged_at = nowTs;
    alert.updated_at = nowTs;
    if (alert.status === "active") alert.status = "acknowledged";
    appendAudit(alert, nowTs, "acknowledged", { note: opts.note || null });
    const saved = upsertAlert(alert);
    emit("AlertAcknowledged", saved);
    return { ok: true, alert: saved };
  };

  HCAlertEngine.prototype.resolve = function (alertId, opts) {
    opts = opts || {};
    const alert = this.getAlert(alertId);
    if (!alert) return { ok: false, errors: ["alert_not_found"] };
    const nowTs = opts.now || utcNow();
    if (
      alert.severity === "critical" &&
      alert.acknowledgement_state !== "acknowledged" &&
      !opts.force
    ) {
      return { ok: false, errors: ["critical_requires_acknowledgement"], alert: alert };
    }
    alert.status = "resolved";
    alert.resolved_at = nowTs;
    alert.updated_at = nowTs;
    const cd = Number(this.cooldowns[String(alert.severity)] || 30);
    alert.cooldown_until = addMinutes(nowTs, cd);
    appendAudit(alert, nowTs, "resolved", { note: opts.note || null, force: !!opts.force });
    const saved = upsertAlert(alert);
    emit("AlertResolved", saved);
    return { ok: true, alert: saved };
  };

  HCAlertEngine.prototype.snooze = function (alertId, opts) {
    opts = opts || {};
    const alert = this.getAlert(alertId);
    if (!alert) return { ok: false, errors: ["alert_not_found"] };
    if (alert.severity === "critical") {
      return { ok: false, errors: ["critical_cannot_snooze"], alert: alert };
    }
    const nowTs = opts.now || utcNow();
    const minutes = opts.minutes != null ? opts.minutes : 60;
    alert.status = "snoozed";
    alert.snoozed_until = addMinutes(nowTs, minutes);
    alert.updated_at = nowTs;
    appendAudit(alert, nowTs, "snoozed", { minutes: minutes });
    const saved = upsertAlert(alert);
    emit("AlertSnoozed", saved);
    return { ok: true, alert: saved };
  };

  HCAlertEngine.prototype.activeCounts = function (patientId) {
    const counts = {};
    SEVERITIES.forEach((s) => {
      counts[s] = 0;
    });
    this.listAlerts({ patient_id: patientId || undefined, active_only: true }).forEach((a) => {
      if (a.severity in counts) counts[a.severity] += 1;
    });
    counts.total = SEVERITIES.reduce((n, s) => n + counts[s], 0);
    return counts;
  };

  HCAlertEngine.prototype._findOpenByDedupe = function (dedupe, patientId) {
    const items = this.listAlerts({ patient_id: patientId, active_only: true });
    for (let i = 0; i < items.length; i++) {
      if (items[i].deduplication_key === dedupe) return items[i];
    }
    return null;
  };

  HCAlertEngine.prototype._inCooldown = function (dedupe, patientId, nowTs) {
    const nowEpoch = parseIso(nowTs) || 0;
    const items = loadAlertsRaw();
    for (let i = 0; i < items.length; i++) {
      const a = items[i];
      if (a.patient_id !== patientId) continue;
      if (a.deduplication_key !== dedupe) continue;
      const until = parseIso(a.cooldown_until);
      if (until != null && until > nowEpoch) return true;
    }
    return false;
  };

  HCAlertEngine.prototype._updateExisting = function (
    existing,
    evaluation,
    severity,
    sourceEventIds,
    nowTs
  ) {
    const alert = Object.assign({}, existing);
    const prev = String(alert.severity || "watch");
    const escalated = (SEVERITY_RANK[severity] || 0) > (SEVERITY_RANK[prev] || 0);
    alert.occurrence_count = (Number(alert.occurrence_count) || 1) + 1;
    alert.last_detected_at = nowTs;
    alert.updated_at = nowTs;
    alert.evidence = Object.assign({}, evaluation.evidence || alert.evidence || {});
    if (sourceEventIds && sourceEventIds.length) {
      const ids = (alert.source_event_ids || []).slice();
      sourceEventIds.forEach((sid) => {
        if (ids.indexOf(sid) < 0) ids.push(sid);
      });
      alert.source_event_ids = ids;
    }
    if (escalated) {
      alert.severity = severity;
      if (alert.status === "acknowledged" || alert.status === "snoozed") {
        alert.status = "active";
        alert.acknowledgement_state = "none";
      }
      appendAudit(alert, nowTs, "escalated", {
        from: prev,
        to: severity,
        occurrence_count: alert.occurrence_count,
      });
      const saved = upsertAlert(alert);
      emit("AlertEscalated", saved);
      return saved;
    }
    appendAudit(alert, nowTs, "redetected", {
      occurrence_count: alert.occurrence_count,
      severity: alert.severity,
    });
    if (
      alert.occurrence_count >= 5 &&
      (prev === "watch" || prev === "warning" || prev === "informational")
    ) {
      const nextSev = SEVERITIES[Math.min((SEVERITY_RANK[prev] || 0) + 1, SEVERITIES.length - 1)];
      alert.severity = nextSev;
      alert.status = "active";
      appendAudit(alert, nowTs, "escalated_persistence", { from: prev, to: nextSev });
      const saved = upsertAlert(alert);
      emit("AlertEscalated", saved);
      return saved;
    }
    const saved = upsertAlert(alert);
    emit("AlertUpdated", saved);
    return saved;
  };

  global.HCAlertEngine = HCAlertEngine;
})(typeof window !== "undefined" ? window : globalThis);
