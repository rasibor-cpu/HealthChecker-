/**
 * HC-201C — Browser Import Engine / Autonomous Pipeline mirror.
 * Workflow: receive → parser → (text OCR passthrough) → extract → validate flags →
 * duplicate detect → store → timeline → trends → doctor → audit → UI notify.
 */
(function (global) {
  "use strict";

  const Doc = () => global.HCMedicalDocument;
  const Vault = () => global.HCHealthVault;
  const Reg = () => global.HCParserRegistry;
  const Trend = () => global.HCTrendEngine;
  const Timeline = () => global.HCHealthTimeline;
  const Bus = () => global.HCEventBus;
  const Rules = () => global.HCClinicalRules;

  async function readAsArrayBuffer(file) {
    return file.arrayBuffer ? file.arrayBuffer() : new Response(file).arrayBuffer();
  }

  async function readAsText(file) {
    return file.text ? file.text() : new Response(file).text();
  }

  function publish(name, payload) {
    if (Bus() && Bus().publish) Bus().publish(name, payload);
  }

  function computeConfidence(extraction, measurements) {
    const flags = (measurements || []).map((m) => m.abnormal_flag);
    const known = flags.filter((f) => f && f !== "Unknown").length;
    const clinical = flags.length ? 0.5 + 0.5 * (known / flags.length) : 0.4;
    const validation = measurements && measurements.length ? 0.85 : 0.4;
    const storage = 1.0;
    const e = Number(extraction) || 0;
    const overall = e * 0.3 + validation * 0.3 + clinical * 0.2 + storage * 0.2;
    return {
      extraction_confidence: round(e),
      validation_confidence: round(validation),
      clinical_confidence: round(clinical),
      storage_confidence: round(storage),
      overall_confidence: round(overall),
    };
  }

  function round(v) {
    return Math.round(Math.max(0, Math.min(1, Number(v) || 0)) * 1000) / 1000;
  }

  async function importHealthRecord(request) {
    const req = request || {};
    const file = req.file || null;
    const filename = req.filename || (file && file.name) || "upload.bin";
    const mime = req.mime_type || (file && file.type) || "application/octet-stream";

    let buffer = null;
    let text = req.text || "";
    let json = req.json || null;

    publish(Bus() && Bus().EVENTS ? Bus().EVENTS.DocumentImported : "DocumentImported", {
      filename,
      mime,
    });

    if (file) {
      buffer = await readAsArrayBuffer(file);
      if (mime.includes("json") || filename.toLowerCase().endsWith(".json") || mime.includes("text")) {
        text = await readAsText(file);
        try {
          json = JSON.parse(text);
        } catch (_) {}
      }
    } else if (typeof req.document === "string") {
      text = req.document;
      try {
        json = JSON.parse(req.document);
      } catch (_) {}
    }

    publish("OCRCompleted", { provider: "passthrough_text", has_text: !!text });

    const sha256 = buffer
      ? await Vault().sha256Hex(buffer)
      : req.sha256 ||
        (text
          ? await Vault().sha256Hex(new TextEncoder().encode(text))
          : null);

    // Duplicate detection — do not re-import
    const existing = (Vault().listDocuments() || []).find((d) => d.sha256 && sha256 && d.sha256 === sha256);
    if (existing) {
      publish("DuplicateDetected", { original_id: existing.id });
      publish("ImportCompleted", { duplicate: true, document_id: existing.id });
      return {
        ok: true,
        duplicate: true,
        status: "Duplicate",
        document: existing,
        measurements: [],
        confidence: null,
        sha256,
        imported_at: new Date().toISOString(),
        warnings: ["Duplicate content — import skipped"],
      };
    }

    const documentType =
      req.document_type || Doc().classifyDocumentType(filename, mime, req.document_type);

    const tags = Array.isArray(req.tags) ? req.tags.slice() : [];
    if (req.provenance) {
      const pt = "provenance:" + req.provenance;
      if (tags.indexOf(pt) < 0) tags.push(pt);
    }

    const document = Doc().createMedicalDocument({
      patient_id: req.patient_id || "default-patient",
      document_type: documentType,
      source_system: req.source_system || "healthchecker_plus",
      acquisition_method:
        req.acquisition_method || (req.extracted_measurements ? "external_ai" : "manual_upload"),
      original_filename: filename,
      sha256,
      mime_type: mime,
      size_bytes: buffer ? buffer.byteLength : null,
      tags,
      interpretation: req.interpretation || null,
      measured_at: req.measured_at || null,
      status: Doc().STATUS.IMPORTED,
      provenance: req.provenance || null,
      batch_id: req.batch_id || null,
      group_id: req.group_id || null,
      sequence_number: req.sequence_number != null ? Number(req.sequence_number) : null,
      page_number: req.page_number != null ? Number(req.page_number) : null,
      group_title: req.group_title || null,
    });

    // HC-201H lightweight browser classification
    const cls = classifyBrowser(document, req);
    document.primary_category = cls.primary_category;
    document.secondary_categories = cls.secondary_categories;
    document.classification_confidence = cls.classification_confidence;
    document.classification_method = cls.classification_method;
    document.classification_version = "hc201h.browser.v1";
    document.requires_review = !!cls.requires_review;
    document.date_confidence = document.measured_at ? 0.9 : 0.3;
    document.date_source = document.measured_at ? "explicit_measured_at" : "imported_at_fallback";
    if (!document.measured_at) document.measured_at = document.imported_at;

    const parseCtx = {
      document_id: document.id,
      document_type: document.document_type,
      filename,
      mime_type: mime,
      text,
      json,
      source_system: document.source_system,
      acquisition_method: document.acquisition_method,
      extracted_measurements: req.extracted_measurements || null,
      confidence: req.confidence,
      measured_at: req.measured_at || null,
    };

    let parsed;
    try {
      parsed = await Reg().parseWithRegistry(parseCtx);
    } catch (_) {
      publish("ParserFailed", { filename });
      parsed = { parser: null, measurements: [], confidence: 0, notes: ["parser_failed"] };
    }

    let measurements = parsed.measurements || [];
    if (Rules() && Rules().apply) measurements = Rules().apply(measurements);
    publish("MeasurementsExtracted", { count: measurements.length });
    publish("ValidationCompleted", { count: measurements.length });

    document.parser_version = parsed.parser ? parsed.parser.id + "@" + parsed.parser.version : null;
    document.parser_confidence =
      req.confidence != null ? Number(req.confidence) : parsed.confidence;
    document.status = measurements.length ? Doc().STATUS.PARSED : Doc().STATUS.PARTIAL;

    const digitalSignature = {
      hash: sha256,
      import_timestamp: document.imported_at,
      parser_version: document.parser_version,
      ai_version: req.ai_version || null,
    };

    const stored = await Vault().storeDocument({
      document,
      measurements,
      blob: buffer || null,
      interpretation: req.interpretation || null,
      parser: parsed.parser,
      importMeta: {
        notes: parsed.notes,
        mime_type: mime,
        digital_signature: digitalSignature,
        pipeline: "hc201c_browser_pipeline",
      },
    });
    publish("DocumentStored", { document_id: document.id });
    publish("MeasurementStored", { count: measurements.length });

    if (Trend() && typeof Trend().recompute === "function") {
      Trend().recompute();
      publish("TrendUpdated", {});
    }
    if (Timeline() && typeof Timeline().invalidate === "function") {
      Timeline().invalidate();
      publish("TimelineUpdated", {});
    }

    const confidence = computeConfidence(parsed.confidence, measurements);

    const syncLegacy = req.sync_legacy_logs !== false;
    if (syncLegacy && global.HCMeasurementModel && typeof data === "function" && typeof saveData === "function") {
      try {
        const flat = global.HCMeasurementModel.flattenForLegacyLog(measurements);
        if (flat.g != null || flat.sys != null || flat.e != null) {
          const d = data();
          d.logs = d.logs || [];
          d.logs.push({
            g: flat.g,
            sys: flat.sys,
            e: flat.e,
            p: flat.p || "",
            ts: flat.ts || document.imported_at,
            source: "health_vault",
            document_id: document.id,
          });
          saveData(d);
          if (typeof render === "function") render();
        }
      } catch (_) {}
    }

    publish("DoctorReportUpdated", {});
    publish("ImportCompleted", {
      document_id: document.id,
      overall_confidence: confidence.overall_confidence,
    });

    return {
      ok: true,
      duplicate: false,
      document: stored.document,
      measurements,
      parser: parsed.parser,
      confidence,
      digital_signature: digitalSignature,
      import_record: stored.import_record,
      sha256,
      imported_at: document.imported_at,
      ui_notify: true,
    };
  }

  function classifyBrowser(document, req) {
    const t = String(document.document_type || "").toLowerCase();
    const n = String(document.original_filename || "").toLowerCase();
    let primary = "other";
    if (t.indexOf("ecg") >= 0 || n.indexOf("ecg") >= 0) primary = "ecg_cardiology";
    else if (t.indexOf("sleep") >= 0 || n.indexOf("sleep") >= 0) primary = "sleep";
    else if (t.indexOf("blood_pressure") >= 0 || n.indexOf("bp") >= 0) primary = "blood_pressure";
    else if (t.indexOf("glucose") >= 0 || t.indexOf("libre") >= 0) primary = "glucose_diabetes";
    else if (t.indexOf("lab") >= 0) primary = "laboratory_report";
    else if (t.indexOf("medication") >= 0) primary = "medication";
    else if (t.indexOf("hospital") >= 0) primary = "hospital_clinical_report";
    const secondary = primary === "kidney_renal" ? ["laboratory_report"] : [];
    return {
      primary_category: primary,
      secondary_categories: secondary,
      classification_confidence: primary === "other" ? 0.4 : 0.75,
      classification_method: "browser_document_type",
      requires_review: primary === "other",
    };
  }

  function buildApiPayload(request) {
    return {
      document: request.document || null,
      filename: request.filename || null,
      mime_type: request.mime_type || null,
      document_type: request.document_type || null,
      extracted_measurements: request.extracted_measurements || null,
      interpretation: request.interpretation || null,
      confidence: request.confidence,
      patient_id: request.patient_id || "default-patient",
      tags: request.tags || [],
    };
  }

  global.HCImportEngine = {
    importHealthRecord,
    buildApiPayload,
  };
})(typeof window !== "undefined" ? window : globalThis);
