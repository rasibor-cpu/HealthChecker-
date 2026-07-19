/**
 * HC-201 — Built-in parsers. Each registers on load.
 */
(function (global) {
  "use strict";

  const Reg = global.HCParserRegistry;
  const MM = global.HCMeasurementModel;
  if (!Reg || !MM) {
    console.warn("HC parsers: registry or measurement model missing");
    return;
  }

  function fromJsonText(text) {
    try {
      return JSON.parse(text);
    } catch (_) {
      return null;
    }
  }

  function measurementsFromObject(obj, documentId, confidence) {
    if (!obj || typeof obj !== "object") return [];
    const out = [];
    const flat = obj.measurements || obj.extracted_measurements || obj;
    if (Array.isArray(flat)) {
      return flat.map((m) =>
        MM.createMeasurement(
          Object.assign({}, m, {
            document_id: documentId,
            confidence: m.confidence != null ? m.confidence : confidence,
          })
        )
      );
    }
    Object.keys(flat).forEach((key) => {
      const val = flat[key];
      if (val == null || typeof val === "object") return;
      if (["document", "interpretation", "confidence", "parser", "filename"].includes(key)) return;
      out.push(
        MM.createMeasurement({
          document_id: documentId,
          metric: key,
          value: val,
          confidence,
          measured_at: flat.measured_at || flat.date || null,
        })
      );
    });
    return out;
  }

  function textIncludes(ctx, tokens) {
    const blob = [
      ctx.filename || "",
      ctx.document_type || "",
      ctx.text || "",
      ctx.source_system || "",
    ]
      .join(" ")
      .toLowerCase();
    return tokens.some((t) => blob.includes(t));
  }

  Reg.register({
    id: "samsung_health_parser",
    name: "SamsungHealthParser",
    version: "1.0.0",
    priority: 20,
    supportedTypes: ["samsung_health_ecg", "samsung_health_sleep", "samsung_health_energy_score"],
    canParse(ctx) {
      return (
        textIncludes(ctx, ["samsung", "samsung_health"]) ||
        ["samsung_health_ecg", "samsung_health_sleep", "samsung_health_energy_score"].includes(
          ctx.document_type
        )
      );
    },
    parse(ctx) {
      const json = typeof ctx.text === "string" ? fromJsonText(ctx.text) : ctx.json || null;
      let measurements = measurementsFromObject(json, ctx.document_id, 0.7);
      if (!measurements.length && ctx.document_type === "samsung_health_ecg") {
        measurements = [
          MM.createMeasurement({
            document_id: ctx.document_id,
            metric: "ecg_result",
            value: "imported",
            category: "ECG",
            confidence: 0.4,
            measured_at: ctx.measured_at || null,
          }),
        ];
      }
      if (!measurements.length && ctx.document_type === "samsung_health_sleep") {
        measurements = [
          MM.createMeasurement({
            document_id: ctx.document_id,
            metric: "sleep_score",
            value: null,
            category: "Sleep Score",
            confidence: 0.35,
            measured_at: ctx.measured_at || null,
          }),
        ];
      }
      if (!measurements.length && ctx.document_type === "samsung_health_energy_score") {
        measurements = [
          MM.createMeasurement({
            document_id: ctx.document_id,
            metric: "energy_score",
            value: null,
            category: "Energy Score",
            confidence: 0.35,
            measured_at: ctx.measured_at || null,
          }),
        ];
      }
      return {
        measurements,
        confidence: measurements.length ? 0.65 : 0.3,
        notes: ["Samsung Health parser (heuristic / JSON)"],
      };
    },
  });

  Reg.register({
    id: "galaxy_watch_parser",
    name: "GalaxyWatchParser",
    version: "1.0.0",
    priority: 18,
    supportedTypes: ["galaxy_watch_report"],
    canParse(ctx) {
      return textIncludes(ctx, ["galaxy", "watch"]) || ctx.document_type === "galaxy_watch_report";
    },
    parse(ctx) {
      const json = typeof ctx.text === "string" ? fromJsonText(ctx.text) : ctx.json || null;
      const measurements = measurementsFromObject(json, ctx.document_id, 0.6);
      return {
        measurements,
        confidence: measurements.length ? 0.6 : 0.3,
        notes: ["Galaxy Watch parser"],
      };
    },
  });

  Reg.register({
    id: "lifelabs_parser",
    name: "LifeLabsParser",
    version: "1.0.0",
    priority: 25,
    supportedTypes: ["laboratory_pdf"],
    canParse(ctx) {
      return textIncludes(ctx, ["lifelabs", "lab", "laboratory"]) || ctx.document_type === "laboratory_pdf";
    },
    parse(ctx) {
      const json = typeof ctx.text === "string" ? fromJsonText(ctx.text) : ctx.json || null;
      let measurements = measurementsFromObject(json, ctx.document_id, 0.75);
      const text = String(ctx.text || "");
      const egfr = text.match(/eGFR[^0-9]*([0-9]+(?:\.[0-9]+)?)/i);
      const creat = text.match(/Creatinine[^0-9]*([0-9]+(?:\.[0-9]+)?)/i);
      const hba1c = text.match(/HbA1c[^0-9]*([0-9]+(?:\.[0-9]+)?)/i);
      if (egfr) {
        measurements.push(
          MM.createMeasurement({
            document_id: ctx.document_id,
            metric: "egfr",
            value: Number(egfr[1]),
            confidence: 0.55,
          })
        );
      }
      if (creat) {
        measurements.push(
          MM.createMeasurement({
            document_id: ctx.document_id,
            metric: "creatinine",
            value: Number(creat[1]),
            confidence: 0.55,
          })
        );
      }
      if (hba1c) {
        measurements.push(
          MM.createMeasurement({
            document_id: ctx.document_id,
            metric: "hba1c",
            value: Number(hba1c[1]),
            confidence: 0.55,
          })
        );
      }
      return {
        measurements,
        confidence: measurements.length ? 0.7 : 0.25,
        notes: ["LifeLabs / laboratory parser"],
      };
    },
  });

  Reg.register({
    id: "libre_parser",
    name: "LibreParser",
    version: "1.0.0",
    priority: 22,
    supportedTypes: ["libre_cgm_report", "blood_glucose"],
    canParse(ctx) {
      return (
        textIncludes(ctx, ["libre", "cgm", "freestyle"]) ||
        ["libre_cgm_report", "blood_glucose"].includes(ctx.document_type)
      );
    },
    parse(ctx) {
      const json = typeof ctx.text === "string" ? fromJsonText(ctx.text) : ctx.json || null;
      let measurements = measurementsFromObject(json, ctx.document_id, 0.7);
      const text = String(ctx.text || "");
      const g = text.match(/(?:glucose|avg|average)[^0-9]*([0-9]{2,3}(?:\.[0-9]+)?)/i);
      if (g && !measurements.some((m) => m.metric === "glucose")) {
        measurements.push(
          MM.createMeasurement({
            document_id: ctx.document_id,
            metric: "glucose",
            value: Number(g[1]),
            confidence: 0.5,
          })
        );
      }
      return {
        measurements,
        confidence: measurements.length ? 0.65 : 0.3,
        notes: ["Libre / CGM parser"],
      };
    },
  });

  Reg.register({
    id: "blood_pressure_parser",
    name: "BloodPressureParser",
    version: "1.0.0",
    priority: 21,
    supportedTypes: ["blood_pressure_screenshot"],
    canParse(ctx) {
      return (
        textIncludes(ctx, ["blood_pressure", "bp ", "systolic", "diastolic"]) ||
        ctx.document_type === "blood_pressure_screenshot"
      );
    },
    parse(ctx) {
      const json = typeof ctx.text === "string" ? fromJsonText(ctx.text) : ctx.json || null;
      let measurements = measurementsFromObject(json, ctx.document_id, 0.7);
      const text = String(ctx.text || ctx.filename || "");
      const bp = text.match(/(\d{2,3})\s*[\/]\s*(\d{2,3})/);
      if (bp) {
        measurements.push(
          MM.createMeasurement({
            document_id: ctx.document_id,
            metric: "systolic",
            value: Number(bp[1]),
            confidence: 0.55,
          }),
          MM.createMeasurement({
            document_id: ctx.document_id,
            metric: "diastolic",
            value: Number(bp[2]),
            confidence: 0.55,
          })
        );
      }
      return {
        measurements,
        confidence: measurements.length ? 0.6 : 0.25,
        notes: ["Blood pressure parser"],
      };
    },
  });

  Reg.register({
    id: "hospital_report_parser",
    name: "HospitalReportParser",
    version: "1.0.0",
    priority: 10,
    supportedTypes: ["hospital_report", "medication_report", "imaging_report"],
    canParse(ctx) {
      return (
        textIncludes(ctx, ["hospital", "clinic", "discharge", "imaging", "medication"]) ||
        ["hospital_report", "medication_report", "imaging_report", "unknown"].includes(
          ctx.document_type
        )
      );
    },
    parse(ctx) {
      const json = typeof ctx.text === "string" ? fromJsonText(ctx.text) : ctx.json || null;
      const measurements = measurementsFromObject(json, ctx.document_id, 0.5);
      return {
        measurements,
        confidence: measurements.length ? 0.5 : 0.2,
        notes: ["Hospital / generic clinical report parser"],
      };
    },
  });

  /** External AI assistant path — accepts pre-extracted measurements without local OCR. */
  Reg.register({
    id: "ai_assisted_parser",
    name: "AIAssistedParser",
    version: "1.0.0",
    priority: 100,
    supportedTypes: ["ai_assisted_import"],
    canParse(ctx) {
      return (
        ctx.acquisition_method === "external_ai" ||
        ctx.document_type === "ai_assisted_import" ||
        (Array.isArray(ctx.extracted_measurements) && ctx.extracted_measurements.length > 0)
      );
    },
    parse(ctx) {
      const confidence = ctx.confidence != null ? Number(ctx.confidence) : 0.85;
      const measurements = (ctx.extracted_measurements || []).map((m) =>
        MM.createMeasurement(
          Object.assign({}, m, {
            document_id: ctx.document_id,
            confidence: m.confidence != null ? m.confidence : confidence,
          })
        )
      );
      return {
        measurements,
        confidence,
        notes: ["External AI extraction accepted as-is"],
      };
    },
  });

  Reg.register({
    id: "generic_json_parser",
    name: "GenericJsonParser",
    version: "1.0.0",
    priority: 5,
    supportedTypes: ["json_measurements"],
    canParse(ctx) {
      const mime = String(ctx.mime_type || "").toLowerCase();
      const name = String(ctx.filename || "").toLowerCase();
      return (
        ctx.document_type === "json_measurements" ||
        mime.includes("json") ||
        name.endsWith(".json") ||
        !!ctx.json
      );
    },
    parse(ctx) {
      const json = typeof ctx.text === "string" ? fromJsonText(ctx.text) : ctx.json || null;
      const measurements = measurementsFromObject(json, ctx.document_id, 0.7);
      return {
        measurements,
        confidence: measurements.length ? 0.7 : 0.2,
        notes: ["Generic JSON measurement parser"],
      };
    },
  });
})(typeof window !== "undefined" ? window : globalThis);
