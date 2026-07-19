/**
 * HC-201 — Generic MedicalDocument model (DocumentReference-ready).
 * Not Samsung-specific.
 */
(function (global) {
  "use strict";

  const DOCUMENT_TYPES = Object.freeze([
    "samsung_health_ecg",
    "samsung_health_sleep",
    "samsung_health_energy_score",
    "galaxy_watch_report",
    "blood_pressure_screenshot",
    "blood_glucose",
    "libre_cgm_report",
    "laboratory_pdf",
    "hospital_report",
    "medication_report",
    "imaging_report",
    "ai_assisted_import",
    "json_measurements",
    "unknown",
  ]);

  const STATUS = Object.freeze({
    IMPORTED: "imported",
    PARSED: "parsed",
    PARTIAL: "partial",
    FAILED: "failed",
    ARCHIVED: "archived",
  });

  function uuid() {
    if (global.crypto && crypto.randomUUID) return crypto.randomUUID();
    return "d-" + Date.now().toString(36) + "-" + Math.random().toString(36).slice(2, 10);
  }

  function createMedicalDocument(partial) {
    const p = partial || {};
    return {
      id: p.id || uuid(),
      patient_id: p.patient_id || "default-patient",
      document_type: p.document_type || "unknown",
      source_system: p.source_system || "unknown",
      acquisition_method: p.acquisition_method || "manual_upload",
      original_filename: p.original_filename || null,
      storage_uri: p.storage_uri || null,
      sha256: p.sha256 || null,
      imported_at: p.imported_at || new Date().toISOString(),
      measured_at: p.measured_at || null,
      parser_version: p.parser_version || null,
      parser_confidence: p.parser_confidence != null ? Number(p.parser_confidence) : null,
      status: p.status || STATUS.IMPORTED,
      tags: Array.isArray(p.tags) ? p.tags.slice() : [],
      // FHIR readiness (architecture only)
      fhir_resource: p.fhir_resource || "DocumentReference",
      interpretation: p.interpretation || null,
      mime_type: p.mime_type || null,
      size_bytes: p.size_bytes != null ? Number(p.size_bytes) : null,
      provenance: p.provenance || null,
      batch_id: p.batch_id || null,
      group_id: p.group_id || null,
      sequence_number: p.sequence_number != null ? Number(p.sequence_number) : null,
      page_number: p.page_number != null ? Number(p.page_number) : null,
      group_title: p.group_title || null,
      primary_category: p.primary_category || null,
      secondary_categories: Array.isArray(p.secondary_categories) ? p.secondary_categories.slice() : [],
      classification_confidence:
        p.classification_confidence != null ? Number(p.classification_confidence) : null,
      classification_method: p.classification_method || null,
      classification_version: p.classification_version || null,
      requires_review: !!p.requires_review,
      report_date: p.report_date || null,
      file_capture_date: p.file_capture_date || null,
      date_confidence: p.date_confidence != null ? Number(p.date_confidence) : null,
      date_source: p.date_source || null,
    };
  }

  function classifyDocumentType(filename, mime, hint) {
    if (hint && DOCUMENT_TYPES.includes(hint)) return hint;
    const name = String(filename || "").toLowerCase();
    const type = String(mime || "").toLowerCase();
    if (name.includes("ecg") || name.includes("ekg")) return "samsung_health_ecg";
    if (name.includes("sleep")) return "samsung_health_sleep";
    if (name.includes("energy")) return "samsung_health_energy_score";
    if (name.includes("galaxy") || name.includes("watch")) return "galaxy_watch_report";
    if (name.includes("libre") || name.includes("cgm")) return "libre_cgm_report";
    if (name.includes("glucose") || name.includes("bg")) return "blood_glucose";
    if (name.includes("medication") || name.includes("rx") || name.includes("pharma")) {
      return "medication_report";
    }
    if (name.includes("imaging") || name.includes("xray") || name.includes("mri") || name.includes("ct")) {
      return "imaging_report";
    }
    if (name.includes("bp") || name.includes("blood_pressure") || name.includes("pressure")) {
      return "blood_pressure_screenshot";
    }
    if (type.includes("pdf") || name.endsWith(".pdf")) {
      if (name.includes("lab") || name.includes("lifelabs") || name.includes("blood")) {
        return "laboratory_pdf";
      }
      return "hospital_report";
    }
    if (type.includes("json") || name.endsWith(".json")) return "json_measurements";
    return "unknown";
  }

  global.HCMedicalDocument = {
    DOCUMENT_TYPES,
    STATUS,
    createMedicalDocument,
    classifyDocumentType,
    uuid,
  };
})(typeof window !== "undefined" ? window : globalThis);
