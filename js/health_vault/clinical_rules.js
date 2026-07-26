/**
 * HC-201C — Browser clinical rules (mirrors backend config subset).
 * HC-301: expanded Guardian rules live in HCHealthGuardian / evaluateGuardianRules.
 */
(function (global) {
  "use strict";

  const RULES = {
    systolic: { normal: [90, 120], borderline: [121, 139], abnormal: [140, 179], critical_above: 180, critical_below: 70 },
    diastolic: { normal: [60, 80], borderline: [81, 89], abnormal: [90, 119], critical_above: 120, critical_below: 40 },
    glucose: { normal: [70, 99], borderline: [100, 125], abnormal: [126, 250], critical_above: 400, critical_below: 54 },
    hba1c: { normal: [4.0, 5.6], borderline: [5.7, 6.4], abnormal: [6.5, 10], critical_above: 12 },
    egfr: { normal: [90, 200], borderline: [60, 89], abnormal: [30, 59], critical_below: 15 },
    resting_hr: { normal: [50, 90], borderline: [91, 100], abnormal: [101, 140], critical_above: 160, critical_below: 35 },
    sleep_score: { normal: [75, 100], borderline: [60, 74], abnormal: [0, 59] },
  };

  const GUARDIAN_ABSOLUTE = [
    { rule_id: "glucose_low", title: "Low glucose", category: "glucose", severity: "urgent", metric: "glucose", units: ["mg/dL"], operator: "lte", threshold: 70 },
    { rule_id: "glucose_very_low", title: "Very low glucose", category: "glucose", severity: "critical", metric: "glucose", units: ["mg/dL"], operator: "lte", threshold: 54 },
    { rule_id: "glucose_high", title: "High glucose", category: "glucose", severity: "warning", metric: "glucose", units: ["mg/dL"], operator: "gte", threshold: 250 },
    { rule_id: "elevated_resting_hr", title: "Elevated resting heart rate", category: "cardiology", severity: "watch", metric: "resting_hr", units: ["bpm"], operator: "gte", threshold: 100 },
    { rule_id: "low_resting_hr", title: "Unusually low resting heart rate", category: "cardiology", severity: "warning", metric: "resting_hr", units: ["bpm"], operator: "lte", threshold: 40 },
    { rule_id: "low_oxygen_saturation", title: "Low oxygen saturation", category: "respiratory", severity: "urgent", metric: "oxygen_saturation", units: ["%"], operator: "lte", threshold: 92 },
  ];

  function classify(metric, value) {
    const spec = RULES[metric];
    if (!spec) return "Unknown";
    const num = Number(value);
    if (!Number.isFinite(num)) return "Unknown";
    if (spec.critical_above != null && num >= spec.critical_above) return "Critical";
    if (spec.critical_below != null && num <= spec.critical_below) return "Critical";
    for (const [flag, key] of [
      ["Normal", "normal"],
      ["Borderline", "borderline"],
      ["Abnormal", "abnormal"],
    ]) {
      const rng = spec[key];
      if (rng && num >= rng[0] && num <= rng[1]) return flag;
    }
    return "Unknown";
  }

  function apply(measurements) {
    return (measurements || []).map((m) => {
      const copy = Object.assign({}, m);
      copy.abnormal_flag = classify(copy.metric, copy.value);
      return copy;
    });
  }

  function _cmp(op, left, right) {
    if (op === "gte" || op === ">=") return left >= right;
    if (op === "lte" || op === "<=") return left <= right;
    if (op === "gt" || op === ">") return left > right;
    if (op === "lt" || op === "<") return left < right;
    return false;
  }

  /**
   * HC-301 stub: mirrors key absolute Guardian thresholds.
   * Full multi-condition pack remains authoritative in Python ExpandedClinicalRulesEngine
   * and the browser HCHealthGuardian orchestrator.
   */
  function evaluateGuardianRules(ctx) {
    ctx = ctx || {};
    const latest = ctx.latest_by_metric || {};
    const patientId = ctx.patient_id || "default-patient";
    const disclaimer =
      (global.HCAlertEngine && HCAlertEngine.SAFETY_DISCLAIMER) ||
      "Observational only — not a diagnosis.";
    const out = [];
    GUARDIAN_ABSOLUTE.forEach((rule) => {
      const row = latest[rule.metric];
      if (!row) return; // missing data never evaluates as Normal
      const val = Number(row.value);
      if (!Number.isFinite(val)) return;
      if (row.units && rule.units && rule.units.indexOf(row.units) < 0) return;
      if (!_cmp(rule.operator, val, rule.threshold)) return;
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
          (row.units || "") +
          " (threshold " +
          rule.operator +
          " " +
          rule.threshold +
          ").",
        evidence: {
          value: val,
          units: row.units,
          measured_at: row.measured_at,
          threshold: rule.threshold,
        },
        deduplication_key: patientId + "|" + rule.rule_id + "|" + rule.metric,
        safety_disclaimer: disclaimer,
      });
    });
    return out;
  }

  global.HCClinicalRules = {
    RULES,
    classify,
    apply,
    evaluateGuardianRules,
    GUARDIAN_NOTE:
      "Expanded Guardian rules (rate-of-change, continuity, baselines) live in HCHealthGuardian; this module keeps HC-201 classify() plus absolute-threshold stubs.",
  };
})(typeof window !== "undefined" ? window : globalThis);
