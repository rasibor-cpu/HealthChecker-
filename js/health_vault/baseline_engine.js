/**
 * HC-301 — Browser Baseline Engine (mirrors HCBaselineEngine).
 * Personalized baselines from vault measurements; observational only.
 */
(function (global) {
  "use strict";

  const DEFAULT_CONFIG = {
    schema_version: "hc.baseline.v1",
    minimum_sample_count: 5,
    rolling_window_days: 90,
    min_confidence: 0.5,
    percentile_low: 10,
    percentile_high: 90,
    supported_metrics: [
      "glucose",
      "systolic",
      "diastolic",
      "systolic_bp",
      "diastolic_bp",
      "resting_hr",
      "heart_rate",
      "oxygen_saturation",
      "egfr",
      "sleep_score",
      "weight",
    ],
    contexts: ["resting", "sleeping", "active", "fasting", "pre_meal", "post_meal", "unspecified"],
  };

  function utcNow() {
    return new Date().toISOString().replace(/\.\d{3}Z$/, "Z");
  }

  function toFloat(value) {
    if (value == null || value === "") return null;
    const num = Number(value);
    if (!Number.isFinite(num)) return null;
    return num;
  }

  function parseTs(ts) {
    if (!ts) return null;
    const t = Date.parse(String(ts).replace("Z", "+00:00"));
    return Number.isFinite(t) ? t / 1000 : null;
  }

  function percentile(sortedVals, pct) {
    if (!sortedVals.length) return 0;
    if (sortedVals.length === 1) return sortedVals[0];
    const k = (sortedVals.length - 1) * (pct / 100);
    const f = Math.floor(k);
    const c = Math.ceil(k);
    if (f === c) return sortedVals[k | 0];
    return sortedVals[f] * (c - k) + sortedVals[c] * (k - f);
  }

  function mean(vals) {
    if (!vals.length) return null;
    return vals.reduce((a, b) => a + b, 0) / vals.length;
  }

  function median(vals) {
    if (!vals.length) return null;
    const s = vals.slice().sort((a, b) => a - b);
    const mid = Math.floor(s.length / 2);
    return s.length % 2 ? s[mid] : (s[mid - 1] + s[mid]) / 2;
  }

  function pstdev(vals) {
    if (vals.length < 2) return 0;
    const m = mean(vals);
    const sum = vals.reduce((acc, v) => acc + (v - m) * (v - m), 0);
    return Math.sqrt(sum / vals.length);
  }

  function listMeasurements() {
    const Vault = global.HCHealthVault;
    return Vault && Vault.listMeasurements ? Vault.listMeasurements() : [];
  }

  function HCBaselineEngine(config) {
    this.config = Object.assign({}, DEFAULT_CONFIG, config || {});
  }

  HCBaselineEngine.prototype.rebuild = function (opts) {
    opts = opts || {};
    const patientId = opts.patient_id || "default-patient";
    const asOfTs = opts.as_of || utcNow();
    const windowDays = Number(this.config.rolling_window_days || 90);
    const minN = Number(this.config.minimum_sample_count || 5);
    const minConf = Number(this.config.min_confidence || 0.5);
    const loPct = Number(this.config.percentile_low || 10);
    const hiPct = Number(this.config.percentile_high || 90);
    const supported = this.config.supported_metrics || [];
    const asEpoch = parseTs(asOfTs);
    const cutoff = asEpoch != null ? asEpoch - windowDays * 86400 : null;

    const byMetric = Object.create(null);
    listMeasurements().forEach((m) => {
      const metric = String(m.metric || "");
      if (supported.length && supported.indexOf(metric) < 0) return;
      if (m.confidence != null) {
        const conf = Number(m.confidence);
        if (Number.isFinite(conf) && conf < minConf) return;
      }
      if (m.abnormal_flag === "Unknown" && m.unit_compatible === false) return;
      const val = toFloat(m.value);
      if (val == null) return;
      const measured = m.measured_at || m.imported_at;
      const epoch = parseTs(measured);
      if (cutoff != null && epoch != null && epoch < cutoff) return;
      if (asEpoch != null && epoch != null && epoch > asEpoch) return;
      const context = String(m.context || m.meal_context || "unspecified");
      if (!byMetric[metric]) byMetric[metric] = [];
      byMetric[metric].push({
        value: val,
        units: m.units || null,
        measured_at: measured,
        context: context,
      });
    });

    const baselines = {};
    Object.keys(byMetric).forEach((metric) => {
      const rows = byMetric[metric];
      const byUnits = Object.create(null);
      rows.forEach((r) => {
        const key = r.units || "";
        if (!byUnits[key]) byUnits[key] = [];
        byUnits[key].push(r);
      });
      let unitKey = "";
      let maxLen = -1;
      Object.keys(byUnits).forEach((k) => {
        if (byUnits[k].length > maxLen) {
          maxLen = byUnits[k].length;
          unitKey = k;
        }
      });
      const series = byUnits[unitKey] || [];
      const values = series.map((r) => r.value);
      const sampleCount = values.length;
      const ready = sampleCount >= minN;
      const sortedVals = values.slice().sort((a, b) => a - b);
      const contextual = {};
      (this.config.contexts || []).forEach((ctx) => {
        const ctxVals = series.filter((r) => r.context === ctx).map((r) => r.value);
        if (ctxVals.length >= minN) {
          contextual[ctx] = {
            sample_count: ctxVals.length,
            median: median(ctxVals),
            mean: mean(ctxVals),
            lower_percentile: percentile(ctxVals.slice().sort((a, b) => a - b), loPct),
            upper_percentile: percentile(ctxVals.slice().sort((a, b) => a - b), hiPct),
          };
        }
      });
      let confidence = 0;
      if (ready) confidence = Math.min(1, 0.5 + (sampleCount - minN) * 0.05);
      baselines[metric] = {
        metric: metric,
        patient_id: patientId,
        sample_count: sampleCount,
        observation_window_days: windowDays,
        units: unitKey || null,
        median: median(values),
        mean: mean(values),
        minimum: values.length ? Math.min.apply(null, values) : null,
        maximum: values.length ? Math.max.apply(null, values) : null,
        standard_deviation: pstdev(values),
        lower_percentile_band: values.length ? percentile(sortedVals, loPct) : null,
        upper_percentile_band: values.length ? percentile(sortedVals, hiPct) : null,
        last_updated: asOfTs,
        baseline_confidence: Math.round(confidence * 1000) / 1000,
        ready: ready,
        contextual: contextual,
        insufficient_data: !ready,
      };
    });

    const payload = {
      patient_id: patientId,
      as_of: asOfTs,
      baselines: baselines,
      config_version: this.config.schema_version,
      disclaimer: "Personalized baselines are observational and not diagnostic.",
    };
    const Vault = global.HCHealthVault;
    if (Vault && Vault.saveBaselines) Vault.saveBaselines(payload);
    return payload;
  };

  HCBaselineEngine.prototype.getSummaries = function (patientId) {
    const Vault = global.HCHealthVault;
    const data = Vault && Vault.getBaselines ? Vault.getBaselines() : null;
    if (!data || data.patient_id !== (patientId || "default-patient")) {
      return this.rebuild({ patient_id: patientId || "default-patient" });
    }
    return data;
  };

  HCBaselineEngine.prototype.deviation = function (metric, value, opts) {
    opts = opts || {};
    const num = toFloat(value);
    if (num == null) {
      return {
        metric: metric,
        available: false,
        outside_band: false,
        reason: "no_data",
        message: "No data is never interpreted as a normal measurement.",
      };
    }
    const summaries = this.getSummaries(opts.patient_id || "default-patient");
    const base = (summaries.baselines || {})[metric];
    if (!base || !base.ready) {
      return {
        metric: metric,
        available: false,
        outside_band: false,
        reason: "insufficient_baseline",
        fallback: "population_or_configured_rules",
      };
    }
    if (opts.units && base.units && opts.units !== base.units) {
      return {
        metric: metric,
        available: false,
        outside_band: false,
        reason: "unit_mismatch",
      };
    }
    const lo = base.lower_percentile_band;
    const hi = base.upper_percentile_band;
    const outside =
      (lo != null && num < Number(lo)) || (hi != null && num > Number(hi));
    return {
      metric: metric,
      available: true,
      value: num,
      outside_band: outside,
      lower: lo,
      upper: hi,
      baseline_confidence: base.baseline_confidence,
      reason: outside ? "outside_band" : "within_band",
    };
  };

  global.HCBaselineEngine = HCBaselineEngine;
})(typeof window !== "undefined" ? window : globalThis);
