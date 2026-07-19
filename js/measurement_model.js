/**
 * HC-201 — Universal Measurement entity (FHIR Observation–compatible naming).
 * Generic: not Samsung-specific. Easy to extend with new categories/metrics.
 */
(function (global) {
  "use strict";

  /** @typedef {{
   *  measurement_id: string,
   *  document_id: string,
   *  category: string,
   *  metric: string,
   *  value: string|number|null,
   *  units: string|null,
   *  reference_range: string|null,
   *  abnormal_flag: string|null,
   *  confidence: number|null,
   *  measured_at: string|null,
   *  fhir_resource?: string
   * }} Measurement
   */

  const CATEGORIES = Object.freeze({
    CARDIOLOGY: "Cardiology",
    ECG: "ECG",
    HEART_RHYTHM: "Heart Rhythm",
    HEART_RATE: "Heart Rate",
    RESTING_HR: "Resting HR",
    HRV: "HRV",
    SLEEP: "Sleep",
    SLEEP_SCORE: "Sleep Score",
    SLEEP_DURATION: "Sleep Duration",
    DEEP_SLEEP: "Deep Sleep",
    REM: "REM",
    RESPIRATORY_RATE: "Respiratory Rate",
    SKIN_TEMPERATURE: "Skin Temperature",
    ENERGY_SCORE: "Energy Score",
    KIDNEY: "Kidney",
    CREATININE: "Creatinine",
    EGFR: "eGFR",
    PROTEIN: "Protein",
    UACR: "UACR",
    POTASSIUM: "Potassium",
    DIABETES: "Diabetes",
    GLUCOSE: "Glucose",
    HBA1C: "HbA1c",
    CGM: "CGM",
    BLOOD_PRESSURE: "Blood Pressure",
    SYSTOLIC: "Systolic",
    DIASTOLIC: "Diastolic",
    WEIGHT: "Weight",
    BMI: "BMI",
  });

  /** Registry of known metrics → default units / parent category (extensible). */
  const METRIC_CATALOG = Object.freeze({
    ecg_result: { category: "ECG", units: null, fhir_code: "Observation" },
    heart_rhythm: { category: "Heart Rhythm", units: null, fhir_code: "Observation" },
    heart_rate: { category: "Heart Rate", units: "bpm", fhir_code: "Observation" },
    average_hr: { category: "Heart Rate", units: "bpm", fhir_code: "Observation" },
    resting_hr: { category: "Resting HR", units: "bpm", fhir_code: "Observation" },
    hrv: { category: "HRV", units: "ms", fhir_code: "Observation" },
    sleep_score: { category: "Sleep Score", units: "score", fhir_code: "Observation" },
    sleep_duration: { category: "Sleep Duration", units: "h", fhir_code: "Observation" },
    deep_sleep: { category: "Deep Sleep", units: "h", fhir_code: "Observation" },
    rem_sleep: { category: "REM", units: "h", fhir_code: "Observation" },
    respiratory_rate: { category: "Respiratory Rate", units: "/min", fhir_code: "Observation" },
    skin_temperature: { category: "Skin Temperature", units: "C", fhir_code: "Observation" },
    energy_score: { category: "Energy Score", units: "score", fhir_code: "Observation" },
    creatinine: { category: "Creatinine", units: "umol/L", fhir_code: "Observation" },
    egfr: { category: "eGFR", units: "mL/min/1.73m2", fhir_code: "Observation" },
    protein: { category: "Protein", units: null, fhir_code: "Observation" },
    uacr: { category: "UACR", units: "mg/mmol", fhir_code: "Observation" },
    potassium: { category: "Potassium", units: "mmol/L", fhir_code: "Observation" },
    glucose: { category: "Glucose", units: "mg/dL", fhir_code: "Observation" },
    hba1c: { category: "HbA1c", units: "%", fhir_code: "Observation" },
    cgm_average: { category: "CGM", units: "mg/dL", fhir_code: "Observation" },
    cgm_time_in_range: { category: "CGM", units: "%", fhir_code: "Observation" },
    cgm_gmi: { category: "CGM", units: "%", fhir_code: "Observation" },
    systolic: { category: "Systolic", units: "mmHg", fhir_code: "Observation" },
    diastolic: { category: "Diastolic", units: "mmHg", fhir_code: "Observation" },
    weight: { category: "Weight", units: "kg", fhir_code: "Observation" },
    bmi: { category: "BMI", units: "kg/m2", fhir_code: "Observation" },
  });

  function uuid() {
    if (global.crypto && crypto.randomUUID) return crypto.randomUUID();
    return "m-" + Date.now().toString(36) + "-" + Math.random().toString(36).slice(2, 10);
  }

  /**
   * Create a Measurement. Unknown metrics are allowed (future-proof).
   * @returns {Measurement}
   */
  function createMeasurement(partial) {
    const p = partial || {};
    const metric = String(p.metric || "unknown");
    const catalog = METRIC_CATALOG[metric] || {};
    return {
      measurement_id: p.measurement_id || uuid(),
      document_id: p.document_id || null,
      category: p.category || catalog.category || "Uncategorized",
      metric,
      value: p.value !== undefined ? p.value : null,
      units: p.units !== undefined ? p.units : catalog.units || null,
      reference_range: p.reference_range || null,
      abnormal_flag: p.abnormal_flag || null,
      confidence: p.confidence != null ? Number(p.confidence) : null,
      measured_at: p.measured_at || null,
      fhir_resource: p.fhir_resource || catalog.fhir_code || "Observation",
    };
  }

  /** Register a new metric for future measurements without code forks. */
  function registerMetric(metric, meta) {
    if (!metric) return;
    METRIC_CATALOG[metric] = Object.assign({}, METRIC_CATALOG[metric] || {}, meta || {});
  }

  /** Map measurements into legacy HC_V6 log fields for Trend Intelligence. */
  function flattenForLegacyLog(measurements) {
    const out = {
      g: null,
      sys: null,
      dia: null,
      e: null,
      p: "",
      ts: null,
    };
    (measurements || []).forEach((m) => {
      if (!m) return;
      const metric = String(m.metric || "").toLowerCase();
      const v = m.value;
      if (metric === "glucose" && v != null) out.g = Number(v);
      if (metric === "systolic" && v != null) out.sys = Number(v);
      if (metric === "diastolic" && v != null) out.dia = Number(v);
      if (metric === "egfr" && v != null) out.e = Number(v);
      if (metric === "protein" && v != null) out.p = String(v);
      if (m.measured_at && (!out.ts || m.measured_at > out.ts)) out.ts = m.measured_at;
    });
    return out;
  }

  global.HCMeasurementModel = {
    CATEGORIES,
    METRIC_CATALOG,
    createMeasurement,
    registerMetric,
    flattenForLegacyLog,
    uuid,
  };
})(typeof window !== "undefined" ? window : globalThis);
