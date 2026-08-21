/**
 * HC-201 — Automatic Trend Engine (Improving / Stable / Worsening).
 */
(function (global) {
  "use strict";

  /** For these metrics, higher is better. */
  const HIGHER_BETTER = new Set(["egfr", "sleep_score", "energy_score", "cgm_time_in_range", "oxygen_saturation"]);
  /** For these metrics, lower is better. */
  const LOWER_BETTER = new Set([
    "glucose",
    "hba1c",
    "systolic",
    "diastolic",
    "systolic_bp",
    "diastolic_bp",
    "creatinine",
    "uacr",
    "resting_hr",
    "bmi",
    "ldl",
  ]);

  function seriesForMetric(metric) {
    const Vault = global.HCHealthVault;
    if (!Vault) return [];
    return Vault.listMeasurements({ metric })
      .filter((m) => m.value != null && m.value !== "" && Number.isFinite(Number(m.value)))
      .map((m) => ({
        t: m.measured_at || null,
        v: Number(m.value),
        document_id: m.document_id,
      }))
      .sort((a, b) => String(a.t || "").localeCompare(String(b.t || "")));
  }

  function classify(metric, values) {
    if (values.length < 3) {
      return { direction: "stable", label: "Stable", reason: "insufficient_points", values };
    }
    const a = values.slice(-3);
    const rising = a[2] > a[1] && a[1] > a[0];
    const falling = a[2] < a[1] && a[1] < a[0];
    let direction = "stable";
    if (HIGHER_BETTER.has(metric)) {
      if (rising) direction = "improving";
      else if (falling) direction = "worsening";
    } else if (LOWER_BETTER.has(metric)) {
      if (falling) direction = "improving";
      else if (rising) direction = "worsening";
    } else {
      if (rising) direction = "rising";
      else if (falling) direction = "falling";
    }
    const label =
      direction === "improving"
        ? "Improving"
        : direction === "worsening"
          ? "Worsening"
          : direction === "rising"
            ? "Rising"
            : direction === "falling"
              ? "Falling"
              : "Stable";
    return { direction, label, reason: "auto", values: a };
  }

  function recompute() {
    const Vault = global.HCHealthVault;
    if (!Vault) return {};
    const metrics = new Set(Vault.listMeasurements().map((m) => m.metric).filter(Boolean));
    const trends = {};
    metrics.forEach((metric) => {
      const series = seriesForMetric(metric);
      const result = classify(metric, series.map((s) => s.v));
      trends[metric] = {
        metric,
        direction: result.direction,
        label: result.label,
        reason: result.reason,
        sample_count: series.length,
        latest: series.length ? series[series.length - 1].v : null,
        updated_at: new Date().toISOString(),
        fhir_resource: "Observation",
      };
    });
    Vault.saveTrends(trends);
    return trends;
  }

  function getSnapshot() {
    const Vault = global.HCHealthVault;
    return Vault ? Vault.getTrends() : {};
  }

  global.HCTrendEngine = {
    HIGHER_BETTER,
    LOWER_BETTER,
    seriesForMetric,
    classify,
    recompute,
    getSnapshot,
  };
})(typeof window !== "undefined" ? window : globalThis);
