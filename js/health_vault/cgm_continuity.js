/**
 * HC-301 — Browser CGM Continuity Guardian (mirrors HCCGMContinuity).
 * Sensor registry + inventory + data gaps. No live Libre API.
 * Inventory never goes below 0.
 */
(function (global) {
  "use strict";

  const DEFAULT_CONFIG = {
    default_expected_wear_days: 14,
    minimum_protected_reserve: 1,
    travel_buffer_days: 7,
    reorder_lead_days: 5,
    expiring_warning_hours: 24,
    expected_reading_cadence_minutes: 15,
    data_gap_watch_minutes: 45,
    data_gap_urgent_minutes: 180,
    data_gap_critical_minutes: 360,
    default_manufacturer: "Abbott",
    default_model: "FreeStyle Libre",
    disclaimer:
      "HealthChecker+ supplements FreeStyle Libre alarms and does not replace them. " +
      "No live Libre API in HC-301; upload/parser and manual sensor registry only.",
  };

  const CONTINUITY_STATES = [
    "SAFE",
    "WATCH",
    "REORDER_REQUIRED",
    "CRITICAL_SHORTAGE",
    "SENSOR_EXPIRING",
    "SENSOR_EXPIRED",
    "SIGNAL_LOSS",
    "DATA_PIPELINE_FAILURE",
    "INVENTORY_UNKNOWN",
  ];

  function utcNow() {
    return new Date().toISOString().replace(/\.\d{3}Z$/, "Z");
  }

  function uuid() {
    if (global.crypto && crypto.randomUUID) return crypto.randomUUID();
    return "cgm-" + Date.now().toString(36) + "-" + Math.random().toString(36).slice(2, 10);
  }

  function parseTs(ts) {
    if (!ts) return null;
    const d = new Date(String(ts).replace("Z", "+00:00"));
    return Number.isFinite(d.getTime()) ? d : null;
  }

  function toIso(d) {
    return d.toISOString().replace(/\.\d{3}Z$/, "Z");
  }

  function store() {
    return global.HCHealthVault;
  }

  function emit(name, payload) {
    const Bus = global.HCEventBus;
    if (Bus && Bus.publish) {
      try {
        Bus.publish(name, payload || {});
      } catch (_) {}
    }
  }

  function HCCGMContinuity(config) {
    this.config = Object.assign({}, DEFAULT_CONFIG, config || {});
  }

  HCCGMContinuity.CONTINUITY_STATES = CONTINUITY_STATES;

  HCCGMContinuity.prototype.listSensors = function (patientId) {
    const Vault = store();
    const pid = patientId || "default-patient";
    const items = Vault && Vault.listCgmSensors ? Vault.listCgmSensors() : [];
    return items.filter((s) => s.patient_id === pid);
  };

  HCCGMContinuity.prototype.registerSensor = function (payload) {
    payload = payload || {};
    const wear = Number(
      payload.expected_wear_days || this.config.default_expected_wear_days || 14
    );
    const sensor = {
      sensor_id: payload.sensor_id || uuid(),
      patient_id: String(payload.patient_id || "default-patient"),
      manufacturer: String(payload.manufacturer || this.config.default_manufacturer || "Abbott"),
      model: String(payload.model || this.config.default_model || "FreeStyle Libre"),
      serial_or_reference: payload.serial_or_reference || null,
      activation_timestamp: null,
      expected_expiry_timestamp: null,
      actual_expiry_timestamp: null,
      expected_wear_days: wear,
      status: String(payload.status || "planned"),
      failure_reason: null,
      source: String(payload.source || "manual"),
      notes: payload.notes || null,
      created_at: utcNow(),
      updated_at: utcNow(),
    };
    const Vault = store();
    const saved = Vault && Vault.upsertCgmSensor ? Vault.upsertCgmSensor(sensor) : sensor;
    emit("CGMSensorRegistered", saved);
    this._timelineEvent("cgm_sensor_registered", saved);
    return saved;
  };

  HCCGMContinuity.prototype.activateSensor = function (sensorId, opts) {
    opts = opts || {};
    const sensors = this.listSensors();
    let sensor = null;
    for (let i = 0; i < sensors.length; i++) {
      if (sensors[i].sensor_id === sensorId) {
        sensor = Object.assign({}, sensors[i]);
        break;
      }
    }
    if (!sensor) {
      const all = store() && store().listCgmSensors ? store().listCgmSensors() : [];
      for (let i = 0; i < all.length; i++) {
        if (all[i].sensor_id === sensorId) {
          sensor = Object.assign({}, all[i]);
          break;
        }
      }
    }
    if (!sensor) return { ok: false, errors: ["sensor_not_found"] };

    const Vault = store();
    const pid = sensor.patient_id || "default-patient";

    // Idempotent: already activated (active/expiring/expired) does not re-decrement
    if (
      sensor.activation_timestamp &&
      (sensor.status === "active" || sensor.status === "expiring" || sensor.status === "expired")
    ) {
      const continuity = this.evaluateContinuity(pid);
      return {
        ok: true,
        sensor: sensor,
        inventory: this.getInventory(pid),
        continuity: continuity,
        idempotent: true,
      };
    }

    const now = opts.activation_timestamp || utcNow();
    const wear = Number(sensor.expected_wear_days || this.config.default_expected_wear_days || 14);
    const act = parseTs(now) || new Date();
    const expiry = new Date(act.getTime() + wear * 86400000);

    this.listSensors(pid).forEach((s) => {
      if (s.status === "active" && s.sensor_id !== sensorId) {
        const replaced = Object.assign({}, s, { status: "replaced", updated_at: utcNow() });
        if (Vault && Vault.upsertCgmSensor) Vault.upsertCgmSensor(replaced);
        this._timelineEvent("cgm_sensor_replaced", replaced);
      }
    });

    sensor.status = "active";
    sensor.activation_timestamp = toIso(act);
    sensor.expected_expiry_timestamp = toIso(expiry);
    sensor.updated_at = utcNow();
    const saved = Vault && Vault.upsertCgmSensor ? Vault.upsertCgmSensor(sensor) : sensor;
    let invResult = null;
    if (opts.reduce_inventory !== false) {
      invResult = this._decrementInventory(pid);
    }
    emit("CGMSensorActivated", saved);
    this._timelineEvent("cgm_sensor_activated", saved);
    const continuity = this.evaluateContinuity(pid);
    return { ok: true, sensor: saved, inventory: invResult, continuity: continuity };
  };

  HCCGMContinuity.prototype.failSensor = function (sensorId, opts) {
    opts = opts || {};
    const Vault = store();
    const all = Vault && Vault.listCgmSensors ? Vault.listCgmSensors() : [];
    let sensor = null;
    for (let i = 0; i < all.length; i++) {
      if (all[i].sensor_id === sensorId) {
        sensor = Object.assign({}, all[i]);
        break;
      }
    }
    if (!sensor) return { ok: false, errors: ["sensor_not_found"] };
    sensor.status = "failed";
    sensor.failure_reason = opts.reason || "unspecified";
    sensor.actual_expiry_timestamp = utcNow();
    sensor.updated_at = utcNow();
    const saved = Vault && Vault.upsertCgmSensor ? Vault.upsertCgmSensor(sensor) : sensor;
    emit("CGMSensorFailed", saved);
    this._timelineEvent("cgm_sensor_failed", saved);
    return {
      ok: true,
      sensor: saved,
      continuity: this.evaluateContinuity(sensor.patient_id),
    };
  };

  HCCGMContinuity.prototype.getInventory = function (patientId) {
    const pid = patientId || "default-patient";
    const Vault = store();
    const inv = Vault && Vault.getCgmInventory ? Vault.getCgmInventory(pid) : null;
    if (inv) return inv;
    return {
      patient_id: pid,
      unused_sensor_count: 0,
      protected_reserve_count: 0,
      minimum_protected_reserve: Number(this.config.minimum_protected_reserve || 1),
      last_confirmed_at: null,
      expected_wear_days: Number(this.config.default_expected_wear_days || 14),
      projected_coverage_days: 0,
      travel_buffer_days: Number(this.config.travel_buffer_days || 7),
      reorder_lead_days: Number(this.config.reorder_lead_days || 5),
      reorder_deadline: null,
      supply_location: null,
      supplier_notes: null,
      confidence: "unknown",
      status: "INVENTORY_UNKNOWN",
    };
  };

  HCCGMContinuity.prototype.updateInventory = function (payload) {
    payload = payload || {};
    const patientId = String(payload.patient_id || "default-patient");
    const current = Object.assign({}, this.getInventory(patientId));
    let unused = Number(
      payload.unused_sensor_count != null
        ? payload.unused_sensor_count
        : current.unused_sensor_count || 0
    );
    if (unused < 0) unused = 0;
    current.unused_sensor_count = unused;
    current.protected_reserve_count = Math.max(
      0,
      Number(
        payload.protected_reserve_count != null
          ? payload.protected_reserve_count
          : current.protected_reserve_count || 0
      )
    );
    current.minimum_protected_reserve = Number(
      payload.minimum_protected_reserve != null
        ? payload.minimum_protected_reserve
        : current.minimum_protected_reserve || this.config.minimum_protected_reserve || 1
    );
    current.expected_wear_days = Number(
      payload.expected_wear_days != null
        ? payload.expected_wear_days
        : current.expected_wear_days || this.config.default_expected_wear_days || 14
    );
    current.travel_buffer_days = Number(
      payload.travel_buffer_days != null
        ? payload.travel_buffer_days
        : current.travel_buffer_days || this.config.travel_buffer_days || 7
    );
    current.reorder_lead_days = Number(
      payload.reorder_lead_days != null
        ? payload.reorder_lead_days
        : current.reorder_lead_days || this.config.reorder_lead_days || 5
    );
    if (payload.supply_location !== undefined) current.supply_location = payload.supply_location;
    if (payload.supplier_notes !== undefined) current.supplier_notes = payload.supplier_notes;
    current.last_confirmed_at = utcNow();
    current.confidence = String(payload.confidence || "confirmed");
    current.patient_id = patientId;
    const recomputed = this._recomputeInventoryFields(current, patientId);
    const Vault = store();
    const saved =
      Vault && Vault.saveCgmInventory ? Vault.saveCgmInventory(recomputed) : recomputed;
    emit("CGMInventoryUpdated", saved);
    this._timelineEvent("cgm_inventory_updated", saved);
    return saved;
  };

  HCCGMContinuity.prototype.recordDataGap = function (payload) {
    payload = payload || {};
    const gap = {
      gap_id: String(payload.gap_id || uuid()),
      patient_id: String(payload.patient_id || "default-patient"),
      source: String(payload.source || "cgm_or_meter"),
      provider: String(payload.provider || "upload_parser"),
      expected_reading_cadence_minutes: Number(
        payload.expected_reading_cadence_minutes ||
          this.config.expected_reading_cadence_minutes ||
          15
      ),
      most_recent_reading_timestamp: payload.most_recent_reading_timestamp || null,
      gap_start_timestamp: payload.gap_start_timestamp || utcNow(),
      missing_duration_minutes: payload.missing_duration_minutes,
      reason_classification: payload.reason_classification || "unknown",
      retry_state: payload.retry_state || "pending",
      acknowledgement: payload.acknowledgement || "none",
      escalation_status: payload.escalation_status || "none",
      resolution_timestamp: payload.resolution_timestamp || null,
      created_at: utcNow(),
      updated_at: utcNow(),
      note: "No data is never interpreted as a normal measurement.",
    };
    const recent = parseTs(gap.most_recent_reading_timestamp);
    const start = parseTs(gap.gap_start_timestamp);
    if (recent && start && gap.missing_duration_minutes == null) {
      gap.missing_duration_minutes = Math.max(0, Math.floor((start - recent) / 60000));
    }
    const Vault = store();
    const saved = Vault && Vault.upsertDataGap ? Vault.upsertDataGap(gap) : gap;
    emit("DataGapDetected", saved);
    this._timelineEvent("data_gap", saved);
    return saved;
  };

  HCCGMContinuity.prototype.listDataGaps = function (patientId) {
    const pid = patientId || "default-patient";
    const Vault = store();
    const items = Vault && Vault.listDataGaps ? Vault.listDataGaps() : [];
    return items.filter((g) => g.patient_id === pid);
  };

  HCCGMContinuity.prototype.detectGlucoseGap = function (opts) {
    opts = opts || {};
    const patientId = opts.patient_id || "default-patient";
    const nowDt = parseTs(opts.now || utcNow()) || new Date();
    const Vault = store();
    const measurements = Vault && Vault.listMeasurements ? Vault.listMeasurements() : [];
    let latest = null;
    measurements.forEach((m) => {
      if (m.metric !== "glucose") return;
      const ts = parseTs(m.measured_at);
      if (!ts) return;
      if (!latest || ts > latest) latest = ts;
    });
    if (!latest) {
      return this.recordDataGap({
        patient_id: patientId,
        most_recent_reading_timestamp: null,
        gap_start_timestamp: toIso(nowDt),
        missing_duration_minutes: null,
        reason_classification: "no_glucose_measurements",
        escalation_status: "watch",
      });
    }
    const minutes = Math.floor((nowDt - latest) / 60000);
    const watch = Number(this.config.data_gap_watch_minutes || 45);
    if (minutes < watch) return null;
    const urgent = Number(this.config.data_gap_urgent_minutes || 180);
    const critical = Number(this.config.data_gap_critical_minutes || 360);
    let escalation = "watch";
    if (minutes >= critical) escalation = "critical";
    else if (minutes >= urgent) escalation = "urgent";
    return this.recordDataGap({
      patient_id: patientId,
      most_recent_reading_timestamp: toIso(latest),
      gap_start_timestamp: toIso(nowDt),
      missing_duration_minutes: minutes,
      reason_classification: "stale_glucose_feed",
      escalation_status: escalation,
    });
  };

  HCCGMContinuity.prototype.evaluateContinuity = function (patientId, now) {
    const pid = patientId || "default-patient";
    const nowDt = parseTs(now || utcNow()) || new Date();
    const sensors = this.listSensors(pid);
    let active = null;
    for (let i = 0; i < sensors.length; i++) {
      if (sensors[i].status === "active" || sensors[i].status === "expiring") {
        active = Object.assign({}, sensors[i]);
        break;
      }
    }
    if (!active) {
      const expired = [];
      for (let i = 0; i < sensors.length; i++) {
        if (sensors[i].status === "expired" && sensors[i].activation_timestamp) {
          expired.push(sensors[i]);
        }
      }
      if (expired.length) {
        expired.sort(function (a, b) {
          return String(a.activation_timestamp || "").localeCompare(String(b.activation_timestamp || ""));
        });
        active = Object.assign({}, expired[expired.length - 1]);
      }
    }
    let inv = this._recomputeInventoryFields(Object.assign({}, this.getInventory(pid)), pid, nowDt);
    const Vault = store();
    if (Vault && Vault.saveCgmInventory) Vault.saveCgmInventory(inv);

    const states = [];
    const reasons = [];
    let hoursRemaining = null;

    if (inv.confidence === "unknown") {
      states.push("INVENTORY_UNKNOWN");
      reasons.push("Sensor inventory has not been confirmed.");
    }
    if (active) {
      const exp = parseTs(active.expected_expiry_timestamp);
      if (exp) {
        hoursRemaining = (exp - nowDt) / 3600000;
        const warnH = Number(this.config.expiring_warning_hours || 24);
        if (hoursRemaining <= 0) {
          active.status = "expired";
          active.updated_at = utcNow();
          if (Vault && Vault.upsertCgmSensor) Vault.upsertCgmSensor(active);
          states.push("SENSOR_EXPIRED");
          reasons.push("Active CGM sensor is past expected expiry.");
        } else if (hoursRemaining <= warnH) {
          active.status = "expiring";
          active.updated_at = utcNow();
          if (Vault && Vault.upsertCgmSensor) Vault.upsertCgmSensor(active);
          states.push("SENSOR_EXPIRING");
          reasons.push("Active sensor expiring in ~" + hoursRemaining.toFixed(1) + " hours.");
        } else if (active.status === "expired" || active.status === "expiring") {
          active.status = "active";
          active.updated_at = utcNow();
          if (Vault && Vault.upsertCgmSensor) Vault.upsertCgmSensor(active);
        }
      }
    } else {
      reasons.push("No active CGM sensor registered (upload/manual registry only).");
    }

    const unused = Math.max(0, Number(inv.unused_sensor_count || 0));
    const minRes = Number(inv.minimum_protected_reserve || 1);
    if (unused < minRes && inv.confidence !== "unknown") {
      states.push("REORDER_REQUIRED");
      reasons.push("Unused sensors below protected reserve.");
    }
    const projected = Number(inv.projected_coverage_days || 0);
    const need = Number(inv.travel_buffer_days || 0) + Number(inv.reorder_lead_days || 0);
    if (inv.confidence !== "unknown" && projected < need) {
      states.push(
        projected < Number(inv.reorder_lead_days || 0) ? "CRITICAL_SHORTAGE" : "REORDER_REQUIRED"
      );
      reasons.push("Projected CGM coverage is below configured buffer needs.");
    }

    const gaps = this.listDataGaps(pid).filter((g) => !g.resolution_timestamp);
    gaps.forEach((g) => {
      if (g.escalation_status === "urgent" || g.escalation_status === "critical") {
        states.push("SIGNAL_LOSS");
        reasons.push("Glucose data gap detected; no data is not normal.");
      }
    });

    const priority = [
      "DATA_PIPELINE_FAILURE",
      "CRITICAL_SHORTAGE",
      "SENSOR_EXPIRED",
      "SIGNAL_LOSS",
      "SENSOR_EXPIRING",
      "REORDER_REQUIRED",
      "INVENTORY_UNKNOWN",
      "WATCH",
      "SAFE",
    ];
    let finalStates = states.slice();
    if (!finalStates.length) {
      finalStates = ["SAFE"];
      reasons.push("No continuity warnings under current configuration.");
    }
    let overall = finalStates[0];
    for (let i = 0; i < priority.length; i++) {
      if (finalStates.indexOf(priority[i]) >= 0) {
        overall = priority[i];
        break;
      }
    }
    const result = {
      patient_id: pid,
      state: overall,
      states: finalStates,
      reasons: reasons,
      active_sensor: active,
      hours_remaining: hoursRemaining,
      inventory: inv,
      open_data_gaps: gaps,
      live_libre_api: false,
      disclaimer: this.config.disclaimer,
      evaluated_at: toIso(nowDt),
    };
    if (Vault && Vault.saveCgmContinuity) Vault.saveCgmContinuity(result);
    return result;
  };

  HCCGMContinuity.prototype._decrementInventory = function (patientId) {
    const inv = Object.assign({}, this.getInventory(patientId));
    const Vault = store();
    if (inv.confidence === "unknown") {
      inv.supplier_notes =
        (inv.supplier_notes || "") + " | activation without confirmed inventory";
      return Vault && Vault.saveCgmInventory ? Vault.saveCgmInventory(inv) : inv;
    }
    let unused = Math.max(0, Number(inv.unused_sensor_count || 0) - 1);
    inv.unused_sensor_count = unused;
    inv.last_confirmed_at = utcNow();
    const recomputed = this._recomputeInventoryFields(inv, patientId);
    return Vault && Vault.saveCgmInventory ? Vault.saveCgmInventory(recomputed) : recomputed;
  };

  HCCGMContinuity.prototype._recomputeInventoryFields = function (inv, patientId, now) {
    now = now || new Date();
    const wear = Number(inv.expected_wear_days || this.config.default_expected_wear_days || 14);
    const unused = Math.max(0, Number(inv.unused_sensor_count || 0));
    let residual = 0;
    this.listSensors(patientId).forEach((s) => {
      if (s.status === "active" || s.status === "expiring") {
        const exp = parseTs(s.expected_expiry_timestamp);
        if (exp) residual = Math.max(0, (exp - now) / 86400000);
      }
    });
    const projected = residual + unused * wear;
    inv.projected_coverage_days = Math.round(projected * 100) / 100;
    const lead = Number(inv.reorder_lead_days || this.config.reorder_lead_days || 5);
    const travel = Number(inv.travel_buffer_days || this.config.travel_buffer_days || 7);
    const daysUntilDeadline = projected - lead - travel;
    const deadline = new Date(now.getTime() + Math.max(0, daysUntilDeadline) * 86400000);
    inv.reorder_deadline = toIso(deadline);
    const minRes = Number(inv.minimum_protected_reserve || 1);
    if (inv.confidence === "unknown") inv.status = "INVENTORY_UNKNOWN";
    else if (unused < minRes) inv.status = "REORDER_REQUIRED";
    else if (projected < lead + travel)
      inv.status = projected < lead ? "CRITICAL_SHORTAGE" : "REORDER_REQUIRED";
    else inv.status = "SAFE";
    inv.protected_reserve_count = Math.min(
      unused,
      Math.max(Number(inv.protected_reserve_count || 0), 0)
    );
    inv.unused_sensor_count = unused;
    return inv;
  };

  HCCGMContinuity.prototype._timelineEvent = function (kind, payload) {
    const Vault = store();
    if (!Vault || !Vault.appendTimelineEvent) return;
    const event = {
      event_id: uuid(),
      kind: kind,
      category: "cgm_continuity",
      measured_at:
        payload.activation_timestamp ||
        payload.updated_at ||
        payload.gap_start_timestamp ||
        utcNow(),
      imported_at: utcNow(),
      provenance: payload.source || "manual",
      severity: null,
      summary: String(kind).replace(/_/g, " "),
      payload: {},
      dedupe_key:
        kind +
        "|" +
        (payload.sensor_id || payload.gap_id || payload.patient_id) +
        "|" +
        (payload.updated_at || payload.created_at),
    };
    ["sensor_id", "status", "unused_sensor_count", "projected_coverage_days", "gap_id", "missing_duration_minutes", "escalation_status"].forEach(
      (k) => {
        if (payload[k] !== undefined) event.payload[k] = payload[k];
      }
    );
    Vault.appendTimelineEvent(event);
  };

  global.HCCGMContinuity = HCCGMContinuity;
})(typeof window !== "undefined" ? window : globalThis);
