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
      currentness = "current";
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
      byId[c.metric_id] = c;
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
    cards.forEach(function (c) {
      if (seen[c.metric_id] || hidden[c.metric_id]) return;
      visible.push(c);
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
    Object.keys(METRIC_SPECS).forEach(function (id) {
      const spec = METRIC_SPECS[id];
      const card =
        spec.kind === "composite_bp" ? bloodPressureCard(spec, rows, now) : scalarCard(id, spec, rows, now);
      if (card) cards.push(card);
    });
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

  function openMetricDetail(card) {
    const tabName = card.detail_tab || "vault";
    const tab = document.querySelector('.tab[data="' + tabName + '"]');
    if (tab) tab.click();
    if (global.HCVaultUI && typeof HCVaultUI.openMetricDetail === "function") {
      HCVaultUI.openMetricDetail(card.detail_category, card.detail_metric || card.metric_id);
      return;
    }
    const tl = document.getElementById("vault_timeline") || document.getElementById("vault_trends");
    if (tl && tl.scrollIntoView) tl.scrollIntoView({ behavior: "smooth", block: "start" });
  }

  function bindCardClicks(root) {
    if (!root) return;
    root.querySelectorAll(".hc-metric-card").forEach(function (btn) {
      btn.addEventListener("click", function () {
        openMetricDetail({
          detail_tab: "vault",
          detail_category: btn.getAttribute("data-category"),
          detail_metric: btn.getAttribute("data-detail-metric"),
          metric_id: btn.getAttribute("data-metric"),
        });
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
    const list = cards || buildCards();
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

  function refresh() {
    const root = document.getElementById("hc_health_snapshot");
    if (!root) return;
    try {
      if (global.HCTrendEngine && HCTrendEngine.recompute) HCTrendEngine.recompute();
    } catch (_) {}
    renderInto(root, buildCards());
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
    loadLayout: loadLayout,
    saveLayout: saveLayout,
    applyTheme: applyTheme,
    initTheme: initTheme,
  };
})(typeof window !== "undefined" ? window : globalThis);
