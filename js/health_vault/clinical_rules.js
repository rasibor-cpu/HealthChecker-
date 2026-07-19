/**
 * HC-201C — Browser clinical rules (mirrors backend config subset).
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

  global.HCClinicalRules = { RULES, classify, apply };
})(typeof window !== "undefined" ? window : globalThis);
