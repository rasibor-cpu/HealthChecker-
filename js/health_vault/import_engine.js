/**
 * HC-201 — Import Engine (browser). Architecture mirrors POST /api/import-health-record.
 * Accepts PDF/PNG/JPG/JSON and optional external AI extraction payload.
 */
(function (global) {
  "use strict";

  const Doc = () => global.HCMedicalDocument;
  const Vault = () => global.HCHealthVault;
  const Reg = () => global.HCParserRegistry;
  const Trend = () => global.HCTrendEngine;
  const Timeline = () => global.HCHealthTimeline;

  async function readAsArrayBuffer(file) {
    return file.arrayBuffer ? file.arrayBuffer() : new Response(file).arrayBuffer();
  }

  async function readAsText(file) {
    return file.text ? file.text() : new Response(file).text();
  }

  /**
   * @param {object} request
   * @param {File|Blob|null} request.file
   * @param {string} [request.filename]
   * @param {string} [request.mime_type]
   * @param {string} [request.document_type]
   * @param {string} [request.source_system]
   * @param {string} [request.acquisition_method]
   * @param {string} [request.patient_id]
   * @param {string} [request.text]
   * @param {object} [request.json]
   * @param {Array} [request.extracted_measurements] — future ChatGPT POST path
   * @param {string} [request.interpretation]
   * @param {number} [request.confidence]
   * @param {string[]} [request.tags]
   * @param {boolean} [request.sync_legacy_logs] — push flattened values into HC_V6 (default true)
   */
  async function importHealthRecord(request) {
    const req = request || {};
    const file = req.file || null;
    const filename = req.filename || (file && file.name) || "upload.bin";
    const mime = req.mime_type || (file && file.type) || "application/octet-stream";

    let buffer = null;
    let text = req.text || "";
    let json = req.json || null;

    if (file) {
      buffer = await readAsArrayBuffer(file);
      if (
        mime.includes("json") ||
        filename.toLowerCase().endsWith(".json") ||
        mime.includes("text")
      ) {
        text = await readAsText(file);
        try {
          json = JSON.parse(text);
        } catch (_) {
          /* keep text */
        }
      }
    } else if (typeof req.document === "string") {
      // AI path may send document as base64 or plain text
      text = req.document;
      try {
        json = JSON.parse(req.document);
      } catch (_) {}
    }

    const sha256 = buffer ? await Vault().sha256Hex(buffer) : req.sha256 || null;
    const documentType =
      req.document_type ||
      Doc().classifyDocumentType(filename, mime, req.document_type);

    const document = Doc().createMedicalDocument({
      patient_id: req.patient_id || "default-patient",
      document_type: documentType,
      source_system: req.source_system || "healthchecker_plus",
      acquisition_method:
        req.acquisition_method ||
        (req.extracted_measurements ? "external_ai" : "manual_upload"),
      original_filename: filename,
      sha256,
      mime_type: mime,
      size_bytes: buffer ? buffer.byteLength : null,
      tags: req.tags || [],
      interpretation: req.interpretation || null,
      measured_at: req.measured_at || null,
      status: Doc().STATUS.IMPORTED,
    });

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

    const parsed = await Reg().parseWithRegistry(parseCtx);
    document.parser_version = parsed.parser
      ? parsed.parser.id + "@" + parsed.parser.version
      : null;
    document.parser_confidence =
      req.confidence != null ? Number(req.confidence) : parsed.confidence;
    document.status = parsed.measurements.length
      ? Doc().STATUS.PARSED
      : Doc().STATUS.PARTIAL;

    const stored = await Vault().storeDocument({
      document,
      measurements: parsed.measurements,
      blob: buffer || null,
      interpretation: req.interpretation || null,
      parser: parsed.parser,
      importMeta: {
        notes: parsed.notes,
        mime_type: mime,
      },
    });

    // Automatic trend refresh
    if (Trend() && typeof Trend().recompute === "function") {
      Trend().recompute();
    }

    // Optional bridge into existing Trend Intelligence logs (additive)
    const syncLegacy = req.sync_legacy_logs !== false;
    if (syncLegacy && global.HCMeasurementModel && typeof data === "function" && typeof saveData === "function") {
      try {
        const flat = global.HCMeasurementModel.flattenForLegacyLog(parsed.measurements);
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
      } catch (_) {
        /* never break vault import if legacy sync fails */
      }
    }

    if (Timeline() && typeof Timeline().invalidate === "function") {
      Timeline().invalidate();
    }

    return {
      ok: true,
      document: stored.document,
      measurements: parsed.measurements,
      parser: parsed.parser,
      confidence: document.parser_confidence,
      import_record: stored.import_record,
      sha256,
      imported_at: document.imported_at,
    };
  }

  /** Future HTTP shape helper (client-side preview of POST body). */
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
