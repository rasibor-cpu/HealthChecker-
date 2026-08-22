/**
 * HC-321 Health Snapshot — latest-valid observations, consumer statuses, HealthMetricCard.
 *
 * Observational only — not a diagnosis. Thresholds live in HCClinicalRules / this domain
 * module. The card renderer consumes normalized statuses and must not embed medical bands.
 */
(function (global) {
  "use strict";

  const DISCLAIMER =
    "Observational decision-support only. Not a diagnosis or prescription. " +
    "Status colours summarise the latest valid HealthChecker data and do not " +
    "replace professional medical assessment.";

  const STATUS_NORMAL = "NORMAL";
  const STATUS_CAUTION = "CAUTION";
  const STATUS_ATTENTION = "ATTENTION";
  const STATUS_UNKNOWN = "UNKNOWN";

  const COLOR_GREEN = "GREEN";
  const COLOR_AMBER = "AMBER";
  const COLOR_RED = "RED";
  const COLOR_GREY = "GREY";

  const FLAG_TO_STATUS = {
    Normal: STATUS_NORMAL,
    Borderline: STATUS_CAUTION,
    Abnormal: STATUS_ATTENTION,
    Critical: STATUS_ATTENTION,
    Unknown: STATUS_UNKNOWN,
  };

  const STATUS_TO_COLOR = {
    NORMAL: COLOR_GREEN,
    CAUTION: COLOR_AMBER,
    ATTENTION: COLOR_RED,
    UNKNOWN: COLOR_GREY,
  };

  const STATUS_TEXT = {
    NORMAL: "Normal",
    CAUTION: "Caution",
    ATTENTION: "Attention",
    UNKNOWN: "Unknown",
  };

  const ALIASES = {
    systolic: "systolic_bp",
    diastolic: "diastolic_bp",
    spo2: "oxygen_saturation",
    pulse: "heart_rate",
    hr: "heart_rate",
    ldl_c: "ldl",
    ldl_cholesterol: "ldl",
    exercise_minutes: "activity_minutes",
  };

  const DEFAULT_ORDER = [
    "blood_pressure",
    "glucose",
    "resting_hr",
    "heart_rate",
    "oxygen_saturation",
    "weight",
    "bmi",
    "egfr",
    "ldl",
    "hba1c",
    "sleep_duration",
    "sleep_score",
    "steps",
    "activity_minutes",
  ];

  const FRESHNESS_WINDOWS = {
    heart_rate: 180,
    resting_hr: 1440,
    average_hr: 1440,
    oxygen_saturation: 360,
    glucose: 1440,
    hba1c: 129600,
    systolic_bp: 10080,
    diastolic_bp: 10080,
    blood_pressure: 10080,
    steps: 1440,
    activity_minutes: 1440,
    sleep_duration: 2160,
    sleep_score: 2160,
    weight: 10080,
    bmi: 43200,
    egfr: 129600,
    creatinine: 129600,
    ldl: 129600,
    default: 10080,
  };

  const METRIC_SPECS = {
    blood_pressure: {
      title: "Blood Pressure",
      unit: "mmHg",
      kind: "composite_bp",
      clinical: true,
      detail_tab: "vault",
      detail_category: "blood_pressure",
      detail_metric: "systolic_bp",
    },
    glucose: {
      title: "Glucose",
      unit: "mg/dL",
      kind: "scalar",
      clinical: true,
      detail_tab: "vault",
      detail_category: "glucose_diabetes",
      detail_metric: "glucose",
    },
    heart_rate: {
      title: "Heart Rate",
      unit: "bpm",
      kind: "scalar",
      clinical: true,
      detail_tab: "vault",
      detail_category: "ecg_cardiology",
      detail_metric: "heart_rate",
    },
    resting_hr: {
      title: "Resting Heart Rate",
      unit: "bpm",
      kind: "scalar",
      clinical: true,
      detail_tab: "vault",
      detail_category: "ecg_cardiology",
      detail_metric: "resting_hr",
    },
    oxygen_saturation: {
      title: "Oxygen Saturation",
      unit: "%",
      kind: "scalar",
      clinical: true,
      aliases: ["spo2", "oxygen_saturation"],
      detail_tab: "vault",
      detail_category: "respiratory_oxygen",
      detail_metric: "oxygen_saturation",
    },
    weight: {
      title: "Weight",
      unit: "kg",
      kind: "scalar",
      clinical: false,
      informational: true,
      detail_tab: "vault",
      detail_category: "weight_body_metrics",
      detail_metric: "weight",
    },
    bmi: {
      title: "BMI",
      unit: "kg/m2",
      kind: "scalar",
      clinical: true,
      detail_tab: "vault",
      detail_category: "weight_body_metrics",
      detail_metric: "bmi",
    },
    egfr: {
      title: "Kidney function (eGFR)",
      unit: "mL/min/1.73m2",
      kind: "scalar",
      clinical: true,
      detail_tab: "vault",
      detail_category: "kidney_renal",
      detail_metric: "egfr",
    },
    ldl: {
      title: "LDL cholesterol",
      unit: "mg/dL",
      kind: "scalar",
      clinical: true,
      aliases: ["ldl", "ldl_c"],
      detail_tab: "vault",
      detail_category: "laboratory_report",
      detail_metric: "ldl",
    },
    hba1c: {
      title: "HbA1c",
      unit: "%",
      kind: "scalar",
      clinical: true,
      detail_tab: "vault",
      detail_category: "glucose_diabetes",
      detail_metric: "hba1c",
    },
    sleep_duration: {
      title: "Sleep",
      unit: "h",
      kind: "sleep_duration",
      clinical: true,
      detail_tab: "vault",
      detail_category: "sleep",
      detail_metric: "sleep_duration",
    },
    sleep_score: {
      title: "Sleep score",
      unit: "score",
      kind: "scalar",
      clinical: true,
      detail_tab: "vault",
      detail_category: "sleep",
      detail_metric: "sleep_score",
    },
    steps: {
      title: "Steps",
      unit: "steps",
      kind: "scalar",
      clinical: false,
      informational: true,
      detail_tab: "vault",
      detail_category: "other",
      detail_metric: "steps",
    },
    activity_minutes: {
      title: "Activity",
      unit: "min",
      kind: "scalar",
      clinical: false,
      informational: true,
      aliases: ["activity_minutes", "exercise_minutes"],
      detail_tab: "vault",
      detail_category: "other",
      detail_metric: "activity_minutes",
    },
  };

  const LAYOUT_KEY = "hc_dashboard_layout_v1";
  const THEME_KEY = "hc_theme";
  const STALE_MULT = 3;
  let _snapshotScrollY = 0;
  let _activeDrillMetric = null;
  let _lastSnapshotCards = [];

  function metricAliasesFor(metricId) {
    const id = canonicalize(metricId);
    const spec = METRIC_SPECS[id] || {};
    const aliases = [id].concat((spec.aliases || []).map(canonicalize));
    if (id === "activity_minutes") aliases.push("exercise_minutes");
    if (id === "blood_pressure") aliases.push("systolic_bp", "diastolic_bp");
    return aliases;
  }

  function esc(s) {
    return String(s == null ? "" : s)
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;");
  }

  function canonicalize(metric) {
    const key = String(metric || "").toLowerCase();
    return ALIASES[key] || key;
  }

  function asNumber(value) {
    if (value == null || value === "") return null;
    const n = Number(value);
    return Number.isFinite(n) ? n : null;
  }

  function parseIso(value) {
    if (!value) return null;
    const t = Date.parse(value);
    return Number.isFinite(t) ? t : null;
  }

  function observationMetric(row) {
    return canonicalize(row && (row.metric_type || row.metric));
  }

  function observationContext(row) {
    const tags = (row && row.tags) || [];
    return [row && row.context, row && row.tag, row && row.notes, tags.join(" ")]
      .map(function (x) {
        return String(x || "");
      })
      .join(" ")
      .toLowerCase();
  }

  function isValidObservation(row) {
    if (!row) return false;
    if (String(row.acquisition_mode || "").toUpperCase() === "SIMULATED_TEST_ONLY") return false;
    if (row.invalid || row.is_invalid) return false;
    const quality = row.quality || {};
    if (quality.invalid === true) return false;
    if (quality.unit_compatible === false || row.unit_compatible === false) return false;
    if (!parseIso(row.measured_at)) return false;
    if (row.value == null || row.value === "") return false;
    if (asNumber(row.value) == null) return false;
    return true;
  }

  function selectLatestValid(observations, metric) {
    const want = metric ? canonicalize(metric) : null;
    const eligible = [];
    (observations || []).forEach(function (row, idx) {
      if (want && observationMetric(row) !== want) return;
      if (!isValidObservation(row)) return;
      const ts = parseIso(row.measured_at);
      if (ts == null) return;
      eligible.push({ ts: ts, idx: idx, row: row });
    });
    if (!eligible.length) return null;
    eligible.sort(function (a, b) {
      if (b.ts !== a.ts) return b.ts - a.ts;
      return b.idx - a.idx;
    });
    return eligible[0].row;
  }

  function freshnessLabel(ageSeconds, currentness) {
    if (ageSeconds == null) return "No timestamp";
    const seconds = Math.max(0, Math.floor(ageSeconds));
    let relative;
    if (seconds < 60) relative = "just now";
    else if (seconds < 3600) {
      const mins = Math.max(1, Math.floor(seconds / 60));
      relative = mins + " minute" + (mins === 1 ? "" : "s") + " ago";
    } else if (seconds < 86400) {
      const hours = Math.max(1, Math.floor(seconds / 3600));
      relative = hours + " hour" + (hours === 1 ? "" : "s") + " ago";
    } else {
      const days = Math.max(1, Math.floor(seconds / 86400));
      relative = days + " day" + (days === 1 ? "" : "s") + " ago";
    }
    if (currentness === "stale") return "Last recorded " + relative + " (not current)";
    return "Updated " + relative;
  }

  function computeFreshness(metric, measuredAt, nowMs) {
    const ts = parseIso(measuredAt);
    if (ts == null) {
      return {
        freshness_status: "missing",
        currentness: "missing",
        age_seconds: null,
        label: "No observation time",
      };
    }
    const ageSec = Math.max(0, ((nowMs != null ? nowMs : Date.now()) - ts) / 1000);
    const ageMin = ageSec / 60;
    const canonical = canonicalize(metric);
    const windowMin = FRESHNESS_WINDOWS[canonical] || FRESHNESS_WINDOWS.default;
    let freshness_status;
    let currentness;
    if (ageMin <= windowMin) {
      freshness_status = "fresh";
      currentness = "current";
    } else if (ageMin <= windowMin * STALE_MULT) {
      freshness_status = "aging";
      // Beyond the current-data window: still visible, but not a CURRENT clinical picture.
      currentness = "stale";
    } else {
      freshness_status = "stale";
      currentness = "stale";
    }
    return {
      freshness_status: freshness_status,
      currentness: currentness,
      age_seconds: ageSec,
      label: freshnessLabel(ageSec, currentness),
    };
  }

  function consumerStatusFromFlag(flag) {
    return FLAG_TO_STATUS[flag] || STATUS_UNKNOWN;
  }

  function statusColor(status) {
    return STATUS_TO_COLOR[status] || COLOR_GREY;
  }

  function statusText(status) {
    return STATUS_TEXT[status] || "Unknown";
  }

  function classifyFlag(metric, value, units) {
    const Rules = global.HCClinicalRules;
    if (!Rules || typeof Rules.classify !== "function") return "Unknown";
    const aliases = [metric, canonicalize(metric)];
    if (metric === "systolic_bp") aliases.push("systolic");
    if (metric === "diastolic_bp") aliases.push("diastolic");
    let flag = "Unknown";
    for (let i = 0; i < aliases.length; i++) {
      flag = Rules.classify(aliases[i], value);
      if (flag && flag !== "Unknown") return flag;
    }
    return flag;
  }

  function contextHas(text, tokens) {
    const blob = String(text || "").toLowerCase();
    for (let i = 0; i < tokens.length; i++) {
      if (blob.indexOf(tokens[i]) >= 0) return true;
    }
    return false;
  }

  function evaluateConsumerStatus(opts) {
    opts = opts || {};
    const currentness = opts.currentness || "current";
    if (currentness === "missing" || currentness === "invalid" || currentness === "stale") {
      return statusResult(STATUS_UNKNOWN, currentness);
    }
    if (opts.informational) {
      return statusResult(STATUS_UNKNOWN, "informational_no_clinical_target");
    }
    const metric = canonicalize(opts.metric);
    const num = asNumber(opts.value);
    const ctx = opts.context || "";
    if (metric === "glucose") return glucoseStatus(num, opts.units, ctx);
    if (metric === "heart_rate") return heartRateStatus(num, opts.units, ctx);
    if (metric === "sleep_duration") return sleepDurationStatus(num, opts.units, opts.sample_count);
    const flag = classifyFlag(metric, opts.value, opts.units);
    return statusResult(consumerStatusFromFlag(flag), "clinical_flag:" + flag);
  }

  function statusResult(status, reason) {
    return {
      status: status,
      status_text: statusText(status),
      status_color: statusColor(status),
      reason: reason,
    };
  }

  function glucoseStatus(num, units, context) {
    if (num == null) return statusResult(STATUS_UNKNOWN, "non_numeric");
    if (contextHas(context, ["post_meal", "postprandial", "after_meal", "after meal", "ppg", "nonfasting"])) {
      if (num < 70) return statusResult(STATUS_ATTENTION, "post_meal_hypoglycaemia_band");
      if (num < 140) return statusResult(STATUS_NORMAL, "post_meal_target_band");
      if (num < 200) return statusResult(STATUS_CAUTION, "post_meal_elevated_band");
      return statusResult(STATUS_ATTENTION, "post_meal_high_band");
    }
    const flag = classifyFlag("glucose", num, units || "mg/dL");
    const reason = contextHas(context, ["fasting", "fasted", "pre-meal", "premeal"])
      ? "glucose_fasting"
      : "glucose_context_unknown";
    return statusResult(consumerStatusFromFlag(flag), reason + ":" + flag);
  }

  function heartRateStatus(num, units, context) {
    if (num == null) return statusResult(STATUS_UNKNOWN, "non_numeric");
    if (contextHas(context, ["exercise", "workout", "activity", "walking", "running", "training"])) {
      if (num <= 40 || num >= 181) return statusResult(STATUS_ATTENTION, "activity_hr_extreme");
      return statusResult(STATUS_UNKNOWN, "activity_hr_no_resting_assumption");
    }
    const flag = classifyFlag("heart_rate", num, units || "bpm");
    return statusResult(consumerStatusFromFlag(flag), "heart_rate_unlabelled:" + flag);
  }

  function sleepMinutes(num, units) {
    const unit = String(units || "").toLowerCase();
    if (["h", "hr", "hrs", "hour", "hours"].indexOf(unit) >= 0) return num * 60;
    if (["min", "mins", "minute", "minutes"].indexOf(unit) >= 0) return num;
    if (!unit && num > 0 && num <= 24) return num * 60;
    return num;
  }

  function sleepDurationStatus(num, units, sampleCount) {
    if (num == null) return statusResult(STATUS_UNKNOWN, "non_numeric");
    const minutes = sleepMinutes(num, units);
    const hours = minutes / 60;
    let status;
    let reason;
    if (hours >= 7 && hours <= 9) {
      status = STATUS_NORMAL;
      reason = "adult_sleep_7_to_9h";
    } else if ((hours >= 6 && hours < 7) || (hours > 9 && hours <= 10)) {
      status = STATUS_CAUTION;
      reason = "adult_sleep_borderline_duration";
    } else {
      status = STATUS_ATTENTION;
      reason = "adult_sleep_short_or_long_night";
    }
    if (sampleCount != null && sampleCount < 3) reason += ";single_night_not_chronic";
    return statusResult(status, reason);
  }

  function trendFromValues(metric, values) {
    const Trend = global.HCTrendEngine;
    if (!values || values.length < 3) {
      return { direction: null, label: null, indicator: null, reason: "insufficient_points" };
    }
    if (Trend && typeof Trend.classify === "function") {
      const result = Trend.classify(canonicalize(metric), values);
      const direction = result.direction || "stable";
      const lower =
        Trend.LOWER_BETTER &&
        (Trend.LOWER_BETTER.has(metric) || Trend.LOWER_BETTER.has(canonicalize(metric)));
      const indicators = {
        improving: lower ? "↓" : "↑",
        worsening: lower ? "↑" : "↓",
        rising: "↑",
        falling: "↓",
        stable: "→",
      };
      return {
        direction: direction,
        label: result.label || null,
        indicator: indicators[direction] || "→",
        reason: result.reason || "auto",
      };
    }
    return { direction: null, label: null, indicator: null, reason: "no_engine" };
  }

  function formatDisplayValue(value, metric, units) {
    if (value == null || value === "") return "—";
    if (canonicalize(metric) === "sleep_duration") {
      const hours = sleepMinutes(Number(value), units) / 60;
      if (Math.abs(hours - Math.round(hours)) < 0.05) return String(Math.round(hours));
      return hours.toFixed(1);
    }
    const num = asNumber(value);
    if (num == null) return String(value);
    if (Math.abs(num - Math.round(num)) < 0.05) return String(Math.round(num));
    return num.toFixed(1);
  }

  function worseStatus(a, b) {
    const rank = { UNKNOWN: 0, NORMAL: 1, CAUTION: 2, ATTENTION: 3 };
    if ((a === STATUS_UNKNOWN || b === STATUS_UNKNOWN) && a !== STATUS_ATTENTION && b !== STATUS_ATTENTION && a !== STATUS_CAUTION && b !== STATUS_CAUTION) {
      return STATUS_UNKNOWN;
    }
    return (rank[a] || 0) >= (rank[b] || 0) ? a : b;
  }

  function accessibilityLabel(card) {
    const name = card.title || card.metric_id || "Metric";
    const value = card.display_value || "no value";
    const unit = card.unit || "";
    const status = card.status_text || "unknown";
    const fresh = card.freshness_label || "";
    let spokenValue = String(value);
    let spokenUnit = unit;
    if (card.metric_id === "blood_pressure" && String(value).indexOf("/") >= 0) {
      const parts = String(value).split("/");
      spokenValue = parts[0].trim() + " over " + parts[1].trim();
      spokenUnit = /mmhg/i.test(unit) ? "millimetres of mercury" : unit;
    } else {
      const map = {
        "mg/dl": "milligrams per decilitre",
        mmhg: "millimetres of mercury",
        bpm: "beats per minute",
        "%": "percent",
        kg: "kilograms",
        "kg/m2": "kilograms per square metre",
        "ml/min/1.73m2": "millilitres per minute",
        h: "hours",
        steps: "steps",
        min: "minutes",
        score: "score",
      };
      spokenUnit = map[String(unit).toLowerCase()] || unit;
    }
    const bits = [name, spokenValue];
    if (spokenUnit) bits.push(spokenUnit);
    bits.push("status " + status);
    if (fresh) bits.push(fresh);
    return bits.join(", ") + ".";
  }

  function collectObservations() {
    const rows = [];
    try {
      if (global.HCHealthVault && HCHealthVault.listMeasurements) {
        const docs = {};
        (HCHealthVault.listDocuments() || []).forEach(function (d) {
          if (d && d.id) docs[d.id] = d;
        });
        (HCHealthVault.listMeasurements() || []).forEach(function (m) {
          const doc = docs[m.document_id] || {};
          rows.push({
            metric: m.metric,
            value: m.value,
            units: m.units,
            measured_at: m.measured_at,
            provenance: m.provenance || doc.provenance,
            source: m.source || doc.source_system,
            unit_compatible: m.unit_compatible,
            quality: m.quality,
            acquisition_mode: m.acquisition_mode || "IMPORTED",
            context: m.context || m.tag,
            notes: m.notes,
            tags: m.tags || doc.tags,
            document_id: m.document_id,
            invalid: m.invalid,
          });
        });
      }
    } catch (_) {}
    try {
      const raw = localStorage.getItem("HC_V6");
      if (raw) {
        const parsed = JSON.parse(raw);
        (parsed.logs || []).forEach(function (log) {
          const ts = log.ts || log.measured_at;
          if (log.g != null) {
            rows.push({ metric: "glucose", value: log.g, units: "mg/dL", measured_at: ts, provenance: "hc_v6", source: "manual" });
          }
          if (log.sys != null) {
            rows.push({ metric: "systolic_bp", value: log.sys, units: "mmHg", measured_at: ts, provenance: "hc_v6", source: "manual" });
          }
          if (log.dia != null) {
            rows.push({ metric: "diastolic_bp", value: log.dia, units: "mmHg", measured_at: ts, provenance: "hc_v6", source: "manual" });
          }
          if (log.e != null) {
            rows.push({ metric: "egfr", value: log.e, units: "mL/min/1.73m2", measured_at: ts, provenance: "hc_v6", source: "manual" });
          }
        });
      }
    } catch (_) {}
    return rows;
  }

  function seriesValues(rows, metric) {
    const want = canonicalize(metric);
    return (rows || [])
      .filter(function (r) {
        return observationMetric(r) === want && isValidObservation(r);
      })
      .sort(function (a, b) {
        return String(a.measured_at || "").localeCompare(String(b.measured_at || ""));
      })
      .map(function (r) {
        return asNumber(r.value);
      })
      .filter(function (v) {
        return v != null;
      });
  }

  function scalarCard(metricId, spec, rows, nowMs) {
    const aliases = (spec.aliases || [metricId]).map(canonicalize);
    if (aliases.indexOf(canonicalize(metricId)) < 0) aliases.unshift(canonicalize(metricId));
    const matching = rows.filter(function (r) {
      return aliases.indexOf(observationMetric(r)) >= 0;
    });
    const latest = selectLatestValid(matching);
    if (!latest) return null;
    const values = seriesValues(matching, observationMetric(latest));
    const unit = latest.units || latest.unit || spec.unit;
    const fresh = computeFreshness(metricId, latest.measured_at, nowMs);
    const status = evaluateConsumerStatus({
      metric: metricId,
      value: latest.value,
      units: unit,
      context: observationContext(latest),
      informational: !!spec.informational || spec.clinical === false,
      currentness: fresh.currentness,
      sample_count: values.length,
    });
    const historical = evaluateConsumerStatus({
      metric: metricId,
      value: latest.value,
      units: unit,
      context: observationContext(latest),
      informational: !!spec.informational || spec.clinical === false,
      currentness: "current",
      sample_count: values.length,
    });
    const trend = trendFromValues(metricId, values);
    const card = {
      metric_id: metricId,
      title: spec.title || metricId,
      display_value: formatDisplayValue(latest.value, metricId, unit),
      unit: metricId === "sleep_duration" ? "h" : spec.unit || unit,
      status: status.status,
      status_text: status.status_text,
      status_color: status.status_color,
      status_reason: status.reason,
      historical_status: historical.status,
      historical_status_text: historical.status_text,
      historical_status_color: historical.status_color,
      measured_at: latest.measured_at,
      freshness_status: fresh.freshness_status,
      currentness: fresh.currentness,
      freshness_label: fresh.label,
      age_seconds: fresh.age_seconds,
      trend_direction: trend.direction,
      trend_label: trend.label,
      trend_indicator: trend.indicator,
      provenance: latest.provenance,
      source: latest.source,
      source_metric: observationMetric(latest),
      detail_tab: spec.detail_tab || "vault",
      detail_category: spec.detail_category || "other",
      detail_metric: spec.detail_metric || metricId,
      informational: !!spec.informational,
    };
    card.accessibility_label = accessibilityLabel(card);
    return card;
  }

  function bpPair(sysRows, diaRows) {
    const validSys = sysRows.filter(isValidObservation);
    const validDia = diaRows.filter(isValidObservation);
    const diaByTs = {};
    validDia.forEach(function (r) {
      diaByTs[String(r.measured_at || "")] = r;
    });
    const paired = [];
    validSys.forEach(function (s) {
      const ts = String(s.measured_at || "");
      if (diaByTs[ts]) paired.push({ ts: parseIso(ts), s: s, d: diaByTs[ts] });
    });
    paired.sort(function (a, b) {
      return (b.ts || 0) - (a.ts || 0);
    });
    if (paired.length) return [paired[0].s, paired[0].d];
    let best = null;
    validSys.forEach(function (s) {
      const st = parseIso(s.measured_at);
      if (st == null) return;
      validDia.forEach(function (d) {
        const dt = parseIso(d.measured_at);
        if (dt == null) return;
        const delta = Math.abs(st - dt);
        if (delta > 5 * 60 * 1000) return;
        const newest = Math.max(st, dt);
        if (!best || newest > best.newest || (newest === best.newest && delta < best.delta)) {
          best = { delta: delta, newest: newest, s: s, d: d };
        }
      });
    });
    return best ? [best.s, best.d] : null;
  }

  function bloodPressureCard(spec, rows, nowMs) {
    const sysRows = rows.filter(function (r) {
      return observationMetric(r) === "systolic_bp";
    });
    const diaRows = rows.filter(function (r) {
      return observationMetric(r) === "diastolic_bp";
    });
    const sysLatest = selectLatestValid(sysRows);
    const diaLatest = selectLatestValid(diaRows);
    if (!sysLatest && !diaLatest) return null;
    const pair = bpPair(sysRows, diaRows);
    const sysObs = pair ? pair[0] : sysLatest;
    const diaObs = pair ? pair[1] : diaLatest;
    const measuredAt =
      (sysObs && diaObs
        ? String(sysObs.measured_at) >= String(diaObs.measured_at)
          ? sysObs.measured_at
          : diaObs.measured_at
        : (sysObs || diaObs).measured_at) || null;
    const fresh = computeFreshness("blood_pressure", measuredAt, nowMs);
    const sysNum = asNumber(sysObs && sysObs.value);
    const diaNum = asNumber(diaObs && diaObs.value);
    let display;
    let status;
    let reason;
    if (sysNum != null && diaNum != null) {
      display = formatDisplayValue(sysNum) + "/" + formatDisplayValue(diaNum);
      const sysStatus = evaluateConsumerStatus({
        metric: "systolic_bp",
        value: sysNum,
        units: "mmHg",
        currentness: fresh.currentness,
      });
      const diaStatus = evaluateConsumerStatus({
        metric: "diastolic_bp",
        value: diaNum,
        units: "mmHg",
        currentness: fresh.currentness,
      });
      status = worseStatus(sysStatus.status, diaStatus.status);
      reason = "bp_pair:" + sysStatus.reason + "+" + diaStatus.reason;
    } else {
      display = formatDisplayValue(sysNum != null ? sysNum : diaNum);
      status = STATUS_UNKNOWN;
      reason = "incomplete_bp_pair";
    }
    const trend = trendFromValues("systolic_bp", seriesValues(sysRows, "systolic_bp"));
    const card = {
      metric_id: "blood_pressure",
      title: spec.title || "Blood Pressure",
      display_value: display,
      unit: spec.unit || "mmHg",
      status: status,
      status_text: statusText(status),
      status_color: statusColor(status),
      status_reason: reason,
      measured_at: measuredAt,
      freshness_status: fresh.freshness_status,
      currentness: fresh.currentness,
      freshness_label: fresh.label,
      age_seconds: fresh.age_seconds,
      trend_direction: trend.direction,
      trend_label: trend.label,
      trend_indicator: trend.indicator,
      provenance: (sysObs || diaObs || {}).provenance,
      source: (sysObs || diaObs || {}).source,
      detail_tab: spec.detail_tab || "vault",
      detail_category: spec.detail_category || "blood_pressure",
      detail_metric: spec.detail_metric || "systolic_bp",
      informational: false,
    };
    card.accessibility_label = accessibilityLabel(card);
    return card;
  }

  function applyLayout(cards, layout, defaultOrder) {
    layout = layout || {};
    const hidden = {};
    (layout.hidden || []).forEach(function (id) {
      hidden[id] = true;
    });
    const order = layout.order && layout.order.length ? layout.order.slice() : (defaultOrder || []).slice();
    const byId = {};
    cards.forEach(function (c) {
      let id = c.metric_id;
      if (id === "exercise_minutes") {
        // Prefer the Activity card when both arrive from the API.
        id = "activity_minutes";
        c = Object.assign({}, c, {
          metric_id: "activity_minutes",
          title: (METRIC_SPECS.activity_minutes && METRIC_SPECS.activity_minutes.title) || "Activity",
          detail_metric: "activity_minutes",
          source_metric: c.source_metric || "exercise_minutes",
        });
      }
      if (!byId[id]) byId[id] = c;
    });
    const visible = [];
    const seen = {};
    order.forEach(function (id) {
      if (hidden[id]) return;
      if (byId[id]) {
        visible.push(byId[id]);
        seen[id] = true;
      }
    });
    Object.keys(byId).forEach(function (id) {
      if (seen[id] || hidden[id]) return;
      visible.push(byId[id]);
    });
    return visible;
  }

  function loadLayout() {
    try {
      const raw = localStorage.getItem(LAYOUT_KEY);
      return raw ? JSON.parse(raw) : { order: DEFAULT_ORDER.slice(), hidden: [] };
    } catch (_) {
      return { order: DEFAULT_ORDER.slice(), hidden: [] };
    }
  }

  function saveLayout(layout) {
    try {
      localStorage.setItem(LAYOUT_KEY, JSON.stringify(layout));
    } catch (_) {}
    return layout;
  }

  function buildCards(observations, nowMs, layout) {
    const rows = observations || collectObservations();
    const now = nowMs != null ? nowMs : Date.now();
    const cards = [];
    const seen = {};
    Object.keys(METRIC_SPECS).forEach(function (id) {
      const spec = METRIC_SPECS[id];
      const card =
        spec.kind === "composite_bp" ? bloodPressureCard(spec, rows, now) : scalarCard(id, spec, rows, now);
      if (card) {
        cards.push(card);
        seen[id] = true;
        (spec.aliases || []).forEach(function (a) {
          seen[canonicalize(a)] = true;
        });
      }
    });
    // Do not invent a second Activity / Exercise Minutes card.
    return applyLayout(cards, layout || loadLayout(), DEFAULT_ORDER);
  }

  function trendMarkup(card) {
    if (!card.trend_label) return '<div class="hc-metric-trend muted">Trend unavailable</div>';
    return (
      '<div class="hc-metric-trend">' +
      esc(card.trend_indicator || "") +
      " " +
      esc(card.trend_label) +
      "</div>"
    );
  }

  function provenanceMarkup(card) {
    const bits = [];
    if (card.source) bits.push(card.source);
    if (card.provenance) bits.push(card.provenance);
    if (!bits.length) return "";
    return '<div class="hc-metric-source muted">' + esc(bits.join(" · ")) + "</div>";
  }

  function renderHealthMetricCard(card) {
    const color = String(card.status_color || "GREY").toLowerCase();
    const trend = trendMarkup(card);
    const value = card.display_value == null ? "—" : String(card.display_value);
    const unit = card.unit ? '<span class="hc-metric-unit">' + esc(card.unit) + "</span>" : "";
    return (
      '<button type="button" class="hc-metric-card hc-status-' +
      esc(color) +
      '" data-metric="' +
      esc(card.metric_id) +
      '" data-category="' +
      esc(card.detail_category || "") +
      '" data-detail-metric="' +
      esc(card.detail_metric || card.metric_id) +
      '" aria-label="' +
      esc(card.accessibility_label || card.title) +
      '">' +
      '<div class="hc-metric-name">' +
      esc(card.title) +
      "</div>" +
      '<div class="hc-metric-value">' +
      esc(value) +
      " " +
      unit +
      "</div>" +
      '<div class="hc-metric-status" data-status="' +
      esc(card.status) +
      '"><span class="hc-status-dot" aria-hidden="true"></span>' +
      esc(card.status_text) +
      "</div>" +
      '<div class="hc-metric-freshness">' +
      esc(card.freshness_label || "") +
      "</div>" +
      trend +
      provenanceMarkup(card) +
      "</button>"
    );
  }

  function summarizeHistory(rows, metricId) {
    const aliases = metricAliasesFor(metricId);
    const eligible = (rows || [])
      .filter(function (r) {
        return aliases.indexOf(observationMetric(r)) >= 0 && isValidObservation(r);
      })
      .sort(function (a, b) {
        return String(b.measured_at || "").localeCompare(String(a.measured_at || ""));
      });
    const nums = eligible
      .map(function (r) {
        return asNumber(r.value);
      })
      .filter(function (v) {
        return v != null;
      });
    const history = eligible.slice(0, 40).map(function (r) {
      const hist = evaluateConsumerStatus({
        metric: observationMetric(r),
        value: r.value,
        units: r.units || r.unit,
        context: observationContext(r),
        currentness: "current",
        informational: !!(METRIC_SPECS[canonicalize(metricId)] || {}).informational,
      });
      return {
        metric: observationMetric(r),
        value: r.value,
        display_value: formatDisplayValue(r.value, observationMetric(r), r.units || r.unit),
        units: r.units || r.unit,
        measured_at: r.measured_at,
        provenance: r.provenance,
        source: r.source,
        historical_status: hist.status,
        historical_status_text: hist.status_text,
        historical_status_color: hist.status_color,
      };
    });
    let stats = null;
    if (nums.length) {
      stats = {
        sample_count: nums.length,
        average: Math.round((nums.reduce(function (a, b) {
          return a + b;
        }, 0) / nums.length) * 100) / 100,
        minimum: Math.min.apply(null, nums),
        maximum: Math.max.apply(null, nums),
      };
    }
    return { history: history, stats: stats };
  }

  function sparklineMarkup(history, unit) {
    const values = (history || [])
      .map(function (h) {
        return asNumber(h.value);
      })
      .filter(function (v) {
        return v != null;
      })
      .reverse();
    if (values.length < 2) {
      return '<p class="small muted">Trend chart needs at least two valid points.</p>';
    }
    const min = Math.min.apply(null, values);
    const max = Math.max.apply(null, values);
    const span = max - min || 1;
    const w = 280;
    const h = 64;
    const pts = values
      .map(function (v, i) {
        const x = (i / (values.length - 1)) * (w - 8) + 4;
        const y = h - 4 - ((v - min) / span) * (h - 8);
        return x.toFixed(1) + "," + y.toFixed(1);
      })
      .join(" ");
    return (
      '<div class="hc-metric-chart" role="img" aria-label="Recent trend chart">' +
      '<svg viewBox="0 0 ' +
      w +
      " " +
      h +
      '" width="100%" height="' +
      h +
      '" preserveAspectRatio="none">' +
      '<polyline fill="none" stroke="currentColor" stroke-width="2" points="' +
      pts +
      '" /></svg>' +
      '<div class="small muted">Range ' +
      esc(String(min)) +
      "–" +
      esc(String(max)) +
      (unit ? " " + esc(unit) : "") +
      "</div></div>"
    );
  }

  function openFilteredSurface(surface, metricId, category) {
    const aliases = metricAliasesFor(metricId);
    const primary = canonicalize(metricId);
    if (global.HCVaultUI && typeof HCVaultUI.setMetricFilter === "function") {
      HCVaultUI.setMetricFilter(primary, aliases, category || null);
    }
    if (global.HCConsumerSurfaces && typeof HCConsumerSurfaces.openFiltered === "function") {
      HCConsumerSurfaces.openFiltered(surface, {
        metric: primary,
        metrics: aliases,
        category: category || null,
      });
      return;
    }
    const tabMap = {
      records: "health_records_screen",
      health_records: "health_records_screen",
      vault: "health_records_screen",
      timeline: "consumer_timeline_screen",
      trends: "consumer_trends_screen",
    };
    const tabName = tabMap[surface] || surface;
    const tab = document.querySelector('.tab[data="' + tabName + '"]');
    if (tab) tab.click();
  }

  function renderDrillDown(root, detail) {
    if (!root) return;
    const card = (detail && detail.card) || {};
    const history = (detail && detail.history) || [];
    const stats = (detail && detail.stats) || null;
    const color = String(card.status_color || "GREY").toLowerCase();
    const histColor = String(card.historical_status_color || card.status_color || "GREY").toLowerCase();
    const sourceMetric =
      (detail && detail.canonical_source_metric) || card.source_metric || card.detail_metric || card.metric_id;
    const statsHtml = stats
      ? '<div class="hc-drill-stats" aria-label="Summary statistics">' +
        '<div><span class="muted">Samples</span><strong>' +
        esc(String(stats.sample_count)) +
        "</strong></div>" +
        '<div><span class="muted">Average</span><strong>' +
        esc(String(stats.average)) +
        "</strong></div>" +
        '<div><span class="muted">Min</span><strong>' +
        esc(String(stats.minimum)) +
        "</strong></div>" +
        '<div><span class="muted">Max</span><strong>' +
        esc(String(stats.maximum)) +
        "</strong></div></div>"
      : '<p class="small muted">No summary statistics available yet.</p>';
    const historyHtml = history.length
      ? '<ul class="hc-drill-history">' +
        history
          .map(function (row) {
            return (
              "<li><strong>" +
              esc(row.display_value != null ? row.display_value : row.value) +
              (row.units ? " " + esc(row.units) : "") +
              "</strong> · " +
              esc(row.historical_status_text || "") +
              '<div class="small muted">' +
              esc(String(row.measured_at || "").slice(0, 19)) +
              (row.source || row.provenance ? " · " + esc([row.source, row.provenance].filter(Boolean).join(" · ")) : "") +
              (row.metric && row.metric !== card.metric_id ? " · source metric " + esc(row.metric) : "") +
              "</div></li>"
            );
          })
          .join("") +
        "</ul>"
      : '<p class="small muted">No recent valid measurements for this metric.</p>';
    const histBlock =
      card.currentness === "stale" && card.historical_status
        ? '<div class="hc-drill-historical small">Historical classification (at measurement time): <strong class="hc-status-' +
          esc(histColor) +
          '">' +
          esc(card.historical_status_text || card.historical_status) +
          "</strong></div>"
        : "";
    root.innerHTML =
      '<section class="hc-health-snapshot hc-snapshot-drilldown" aria-label="' +
      esc(card.title || "Metric detail") +
      '">' +
      '<button type="button" class="secondary hc-drill-back" id="hc_snapshot_back">← Back to Health Snapshot</button>' +
      '<div class="hc-metric-card hc-status-' +
      esc(color) +
      ' hc-drill-hero" tabindex="-1">' +
      '<div class="hc-metric-name">' +
      esc(card.title || card.metric_id || "Metric") +
      "</div>" +
      '<div class="hc-metric-value">' +
      esc(card.display_value == null ? "—" : String(card.display_value)) +
      " " +
      (card.unit ? '<span class="hc-metric-unit">' + esc(card.unit) + "</span>" : "") +
      "</div>" +
      '<div class="hc-metric-status" data-status="' +
      esc(card.status || "UNKNOWN") +
      '"><span class="hc-status-dot" aria-hidden="true"></span>' +
      esc(card.status_text || "Unknown") +
      "</div>" +
      '<div class="hc-metric-freshness">' +
      esc(card.freshness_label || "") +
      "</div>" +
      trendMarkup(card) +
      provenanceMarkup(card) +
      "</div>" +
      histBlock +
      (sourceMetric && sourceMetric !== card.metric_id
        ? '<p class="small muted">Underlying source metric: <code>' + esc(sourceMetric) + "</code></p>"
        : "") +
      "<h4 class=\"section-title\">Recent history</h4>" +
      sparklineMarkup(history, card.unit) +
      statsHtml +
      historyHtml +
      '<div class="hc-drill-actions">' +
      '<button type="button" class="secondary" data-open-filtered="records">Filtered Health Records</button>' +
      '<button type="button" class="secondary" data-open-filtered="timeline">Filtered Timeline</button>' +
      '<button type="button" class="secondary" data-open-filtered="trends">Filtered Trends</button>' +
      "</div>" +
      '<p class="small muted">' +
      esc(DISCLAIMER) +
      "</p></section>";
    const back = root.querySelector("#hc_snapshot_back");
    if (back) {
      back.addEventListener("click", function () {
        closeDrillDown();
      });
    }
    root.querySelectorAll("[data-open-filtered]").forEach(function (btn) {
      btn.addEventListener("click", function () {
        openFilteredSurface(
          btn.getAttribute("data-open-filtered"),
          card.metric_id || card.detail_metric,
          card.detail_category
        );
      });
    });
  }

  function closeDrillDown() {
    _activeDrillMetric = null;
    const root = document.getElementById("hc_health_snapshot");
    if (!root) return;
    renderInto(root, _lastSnapshotCards.length ? _lastSnapshotCards : buildCards());
    try {
      if (typeof window !== "undefined" && window.scrollTo) {
        window.scrollTo(0, _snapshotScrollY || 0);
      }
    } catch (_) {}
  }

  function openMetricDetail(card) {
    if (!card) return;
    const metricId = card.metric_id || card.detail_metric;
    if (!metricId) return;
    try {
      _snapshotScrollY =
        (typeof window !== "undefined" && (window.scrollY || window.pageYOffset)) || 0;
    } catch (_) {
      _snapshotScrollY = 0;
    }
    _activeDrillMetric = metricId;
    const root = document.getElementById("hc_health_snapshot");
    if (!root) return;
    const headers = authHeaders();
    const paintLocal = function () {
      const localCard =
        (_lastSnapshotCards || []).find(function (c) {
          return c.metric_id === metricId;
        }) || card;
      const local = summarizeHistory(collectObservations(), metricId);
      // Attach historical status if missing (stale Snapshot cards).
      if (!localCard.historical_status && localCard.currentness === "stale") {
        const hist = evaluateConsumerStatus({
          metric: metricId,
          value: localCard.display_value,
          units: localCard.unit,
          currentness: "current",
          informational: !!localCard.informational,
        });
        localCard.historical_status = hist.status;
        localCard.historical_status_text = hist.status_text;
        localCard.historical_status_color = hist.status_color;
      }
      renderDrillDown(root, {
        card: localCard,
        history: local.history,
        stats: local.stats,
        canonical_source_metric:
          metricId === "activity_minutes" ? "exercise_minutes" : localCard.source_metric || null,
      });
    };
    if (!headers) {
      paintLocal();
      return;
    }
    root.innerHTML =
      '<section class="hc-health-snapshot hc-snapshot-drilldown"><p class="muted">Loading metric detail…</p></section>';
    fetch("/api/health-vault/health-snapshot?metric=" + encodeURIComponent(metricId), {
      headers: headers,
      cache: "no-store",
    })
      .then(function (res) {
        if (!res.ok) throw new Error("metric_detail_" + res.status);
        return res.json();
      })
      .then(function (body) {
        if (!body || body.found === false || !body.card) {
          paintLocal();
          return;
        }
        renderDrillDown(root, body);
      })
      .catch(function () {
        paintLocal();
      });
  }

  function bindCardClicks(root) {
    if (!root) return;
    root.querySelectorAll(".hc-metric-card").forEach(function (btn) {
      btn.addEventListener("click", function () {
        const metricId = btn.getAttribute("data-metric");
        const fromCache = (_lastSnapshotCards || []).find(function (c) {
          return c.metric_id === metricId;
        });
        openMetricDetail(
          fromCache || {
            detail_tab: "vault",
            detail_category: btn.getAttribute("data-category"),
            detail_metric: btn.getAttribute("data-detail-metric"),
            metric_id: metricId,
            title: (btn.querySelector(".hc-metric-name") || {}).textContent,
          }
        );
      });
    });
  }

  function renderCustomize(layout, availableIds) {
    const order = (layout.order && layout.order.length ? layout.order : DEFAULT_ORDER).slice();
    availableIds.forEach(function (id) {
      if (order.indexOf(id) < 0) order.push(id);
    });
    const hidden = layout.hidden || [];
    return (
      '<details class="hc-snapshot-customize">' +
      "<summary>Customize dashboard</summary>" +
      '<p class="small muted">Choose and reorder Health Snapshot cards. Existing risks, trends, and reports stay on this dashboard.</p>' +
      '<ul class="hc-layout-list">' +
      order
        .map(function (id, idx) {
          const spec = METRIC_SPECS[id] || { title: id };
          const checked = hidden.indexOf(id) < 0 ? " checked" : "";
          return (
            "<li data-id=\"" +
            esc(id) +
            '">' +
            '<label><input type="checkbox" data-layout-toggle="' +
            esc(id) +
            '"' +
            checked +
            " /> " +
            esc(spec.title || id) +
            "</label>" +
            '<span class="hc-layout-move">' +
            '<button type="button" class="secondary hc-layout-up" data-move="' +
            idx +
            '" aria-label="Move ' +
            esc(spec.title || id) +
            ' up">Up</button>' +
            '<button type="button" class="secondary hc-layout-down" data-move="' +
            idx +
            '" aria-label="Move ' +
            esc(spec.title || id) +
            ' down">Down</button>' +
            "</span></li>"
          );
        })
        .join("") +
      "</ul></details>"
    );
  }

  function bindCustomize(root) {
    if (!root) return;
    const layout = loadLayout();
    root.querySelectorAll("[data-layout-toggle]").forEach(function (box) {
      box.addEventListener("change", function () {
        const id = box.getAttribute("data-layout-toggle");
        layout.hidden = layout.hidden || [];
        const i = layout.hidden.indexOf(id);
        if (box.checked && i >= 0) layout.hidden.splice(i, 1);
        if (!box.checked && i < 0) layout.hidden.push(id);
        saveLayout(layout);
        refresh();
      });
    });
    function move(idx, dir) {
      const order = (layout.order && layout.order.length ? layout.order : DEFAULT_ORDER).slice();
      const j = idx + dir;
      if (j < 0 || j >= order.length) return;
      const tmp = order[idx];
      order[idx] = order[j];
      order[j] = tmp;
      layout.order = order;
      saveLayout(layout);
      refresh();
    }
    root.querySelectorAll(".hc-layout-up").forEach(function (btn) {
      btn.addEventListener("click", function (ev) {
        ev.preventDefault();
        ev.stopPropagation();
        move(Number(btn.getAttribute("data-move")), -1);
      });
    });
    root.querySelectorAll(".hc-layout-down").forEach(function (btn) {
      btn.addEventListener("click", function (ev) {
        ev.preventDefault();
        ev.stopPropagation();
        move(Number(btn.getAttribute("data-move")), 1);
      });
    });
  }

  function renderInto(root, cards) {
    if (!root) return;
    if (_activeDrillMetric) {
      // Preserve drill-down until Back; refresh will reopen with latest data.
      const keep = (cards || []).find(function (c) {
        return c.metric_id === _activeDrillMetric;
      });
      if (keep) {
        openMetricDetail(keep);
        return;
      }
    }
    const list = cards || buildCards();
    _lastSnapshotCards = list.slice();
    const layout = loadLayout();
    const empty =
      '<p class="muted small">No current HealthChecker observations yet. Import records or add a reading to populate this snapshot.</p>';
    const grid =
      list.length > 0
        ? '<div class="hc-metric-grid">' + list.map(renderHealthMetricCard).join("") + "</div>"
        : empty;
    root.innerHTML =
      '<section class="hc-health-snapshot" aria-label="Health Snapshot">' +
      '<div class="hc-snapshot-header">' +
      "<h3 class=\"section-title\">Health Snapshot</h3>" +
      '<p class="small muted">Your current health picture based on the latest valid HealthChecker data — not continuous real-time measurement.</p>' +
      "</div>" +
      grid +
      renderCustomize(layout, Object.keys(METRIC_SPECS)) +
      '<p class="small muted">' +
      esc(DISCLAIMER) +
      "</p></section>";
    bindCardClicks(root);
    bindCustomize(root);
  }

  function authHeaders() {
    try {
      if (global.HCConsumerDashboard && typeof global.HCConsumerDashboard.getAuthorizationHeaders === "function") {
        const headers = global.HCConsumerDashboard.getAuthorizationHeaders();
        if (headers && headers.Authorization) return headers;
      }
    } catch (_) {}
    try {
      if (global.HCExecutiveDashboard && typeof global.HCExecutiveDashboard.canonicalAuthHeaders === "function") {
        return global.HCExecutiveDashboard.canonicalAuthHeaders();
      }
    } catch (_) {}
    try {
      const raw = global.sessionStorage.getItem("hc_auth_session");
      if (!raw) return null;
      const parsed = JSON.parse(raw);
      return parsed.token ? { Authorization: "Bearer " + parsed.token } : null;
    } catch (_) {
      return null;
    }
  }

  function renderLocal() {
    const root = document.getElementById("hc_health_snapshot");
    if (!root) return;
    try {
      if (global.HCTrendEngine && HCTrendEngine.recompute) HCTrendEngine.recompute();
    } catch (_) {}
    renderInto(root, buildCards());
  }

  function refresh() {
    const root = document.getElementById("hc_health_snapshot");
    if (!root) return Promise.resolve();
    // Always paint the section shell first so authenticated Dashboard never leaves an empty mount
    // between Welcome controls and widgets / executive (HC321-UAT10).
    if (!root.querySelector(".hc-health-snapshot")) {
      try {
        renderInto(root, []);
      } catch (_) {}
    }
    const headers = authHeaders();
    if (!headers) {
      renderLocal();
      return Promise.resolve();
    }
    return fetch("/api/health-vault/health-snapshot", { headers: headers, cache: "no-store" })
      .then(function (res) {
        if (!res.ok) throw new Error("health_snapshot_http_" + res.status);
        return res.json();
      })
      .then(function (body) {
        const cards = applyLayout(body && body.cards ? body.cards : [], loadLayout());
        renderInto(root, cards);
        return cards;
      })
      .catch(function () {
        // Authenticated sessions prefer server cards; fall back locally only when API unavailable.
        renderLocal();
      });
  }

  function applyTheme(theme) {
    const next = theme === "light" ? "light" : "dark";
    const root = document.documentElement;
    root.setAttribute("data-theme", next);
    try {
      localStorage.setItem(THEME_KEY, next);
    } catch (_) {}
    const btn = document.getElementById("hc_theme_toggle");
    if (btn) {
      btn.textContent = next === "light" ? "Dark mode" : "Light mode";
      btn.setAttribute("aria-pressed", next === "light" ? "true" : "false");
    }
    return next;
  }

  function initTheme() {
    let saved = null;
    try {
      saved = localStorage.getItem(THEME_KEY);
    } catch (_) {}
    applyTheme(saved === "light" ? "light" : "dark");
    const btn = document.getElementById("hc_theme_toggle");
    if (btn && !btn.dataset.bound) {
      btn.dataset.bound = "1";
      btn.addEventListener("click", function () {
        const current = document.documentElement.getAttribute("data-theme") || "dark";
        applyTheme(current === "light" ? "dark" : "light");
      });
    }
  }

  if (typeof document !== "undefined" && document.addEventListener) {
    document.addEventListener("hc:session-changed", function (event) {
      const detail = (event && event.detail) || {};
      if (detail.authenticated) {
        try { refresh(); } catch (_) {}
        return;
      }
      const root = document.getElementById("hc_health_snapshot");
      if (root) root.innerHTML = "";
    });
  }

  global.HCHealthSnapshot = {
    DISCLAIMER: DISCLAIMER,
    STATUS_NORMAL: STATUS_NORMAL,
    STATUS_CAUTION: STATUS_CAUTION,
    STATUS_ATTENTION: STATUS_ATTENTION,
    STATUS_UNKNOWN: STATUS_UNKNOWN,
    COLOR_GREEN: COLOR_GREEN,
    COLOR_AMBER: COLOR_AMBER,
    COLOR_RED: COLOR_RED,
    COLOR_GREY: COLOR_GREY,
    DEFAULT_ORDER: DEFAULT_ORDER,
    canonicalize: canonicalize,
    isValidObservation: isValidObservation,
    selectLatestValid: selectLatestValid,
    computeFreshness: computeFreshness,
    evaluateConsumerStatus: evaluateConsumerStatus,
    consumerStatusFromFlag: consumerStatusFromFlag,
    statusColor: statusColor,
    statusText: statusText,
    trendFromValues: trendFromValues,
    formatDisplayValue: formatDisplayValue,
    accessibilityLabel: accessibilityLabel,
    applyLayout: applyLayout,
    buildCards: buildCards,
    collectObservations: collectObservations,
    renderHealthMetricCard: renderHealthMetricCard,
    renderInto: renderInto,
    refresh: refresh,
    openMetricDetail: openMetricDetail,
    closeDrillDown: closeDrillDown,
    metricAliasesFor: metricAliasesFor,
    loadLayout: loadLayout,
    saveLayout: saveLayout,
    applyTheme: applyTheme,
    initTheme: initTheme,
  };
})(typeof window !== "undefined" ? window : globalThis);
