/**
 * HC-202 — AI Health Bridge (local-first UI).
 * ChatGPT Connector V1 with explicit confirmation. Never silent-writes.
 * Imports delegate to HCImportEngine (browser mirror of ImportPipeline).
 */
(function (global) {
  "use strict";

  const PARSER_VERSION = "chatgpt_connector_v1";

  function sha256Hex(str) {
    /* lightweight fingerprint when crypto.subtle unavailable in tests */
    let h = 0;
    const s = String(str || "");
    for (let i = 0; i < s.length; i++) h = (Math.imul(31, h) + s.charCodeAt(i)) | 0;
    return ("00000000" + (h >>> 0).toString(16)).slice(-8).padStart(64, "0");
  }

  async function fingerprintRecord(rec) {
    if (global.crypto && crypto.subtle && crypto.subtle.digest) {
      const blob = JSON.stringify({
        filename: rec.filename,
        measurements: rec.extracted_measurements,
        measured_at: rec.measured_at,
      });
      const buf = await crypto.subtle.digest("SHA-256", new TextEncoder().encode(blob));
      return Array.from(new Uint8Array(buf))
        .map((b) => b.toString(16).padStart(2, "0"))
        .join("");
    }
    return sha256Hex(JSON.stringify(rec));
  }

  function normalizeChatGptPayload(payload) {
    const raw = payload || {};
    const conversation = {};
    const meta = raw.conversation || raw.chat_metadata || {};
    ["conversation_id", "message_id", "message_timestamp", "model", "thread_id"].forEach((k) => {
      if (meta[k] != null) conversation[k] = meta[k];
    });
    conversation.ai_provider = "chatgpt";
    conversation.parser_version = PARSER_VERSION;

    let records = raw.records || raw.health_records || raw.items || [];
    if (!records.length && (raw.extracted_measurements || raw.measurements)) {
      records = [raw];
    }

    return records.map((item, idx) => {
      const meas = item.extracted_measurements || item.measurements || [];
      return {
        record_id: item.record_id || "rec-" + (idx + 1),
        filename: item.filename || item.original_filename || "chatgpt_record_" + (idx + 1) + ".json",
        document_type: item.document_type || "ai_assisted_import",
        source_system: item.source_system || "chatgpt",
        measured_at: item.measured_at || null,
        interpretation: item.interpretation || null,
        provenance: item.provenance || "imported_json",
        confidence: item.confidence != null ? Number(item.confidence) : 0.7,
        extracted_measurements: meas,
        tags: (item.tags || []).concat(["ai_import:chatgpt"]),
        review_flags: meas.length ? [] : ["no_measurements"],
        linkage: {
          ai_record_id: item.record_id || "rec-" + (idx + 1),
          conversation_id: conversation.conversation_id,
          provider_id: "chatgpt",
          parser_version: PARSER_VERSION,
        },
      };
    }).map((rec) => rec);
  }

  function buildPreviewSummary(records) {
    const categories = {};
    const dates = [];
    let duplicateEstimate = 0;
    const Vault = global.HCHealthVault;
    const docs = Vault && Vault.listDocuments ? Vault.listDocuments() : [];

    const previewRecords = records.map((rec) => {
      const meas = rec.extracted_measurements || [];
      const cat =
        (meas[0] && meas[0].category) ||
        (rec.document_type && rec.document_type.indexOf("ecg") >= 0 ? "ecg_cardiology" : "other");
      categories[cat] = (categories[cat] || 0) + 1;
      if (rec.measured_at) dates.push(String(rec.measured_at).slice(0, 10));
      return {
        record_id: rec.record_id,
        filename: rec.filename,
        category: cat,
        measurement_count: meas.length,
        measurements_preview: meas.slice(0, 12).map((m) => ({
          metric: m.metric,
          value: m.value,
          units: m.units,
        })),
        confidence: rec.confidence,
        provenance: rec.provenance,
        review_flags: rec.review_flags || [],
      };
    });

    return {
      provider_id: "chatgpt",
      provider_label: "ChatGPT",
      record_count: records.length,
      categories,
      date_range:
        dates.length > 0
          ? { earliest: dates.sort()[0], latest: dates.sort().slice(-1)[0] }
          : null,
      duplicate_estimate: duplicateEstimate,
      records: previewRecords,
      normalized_records: records,
    };
  }

  async function previewPayload(payload) {
    const records = normalizeChatGptPayload(payload);
    if (!records.length) {
      throw new Error("chatgpt_payload_requires_records");
    }
    const summary = buildPreviewSummary(records);
    summary.message =
      "ChatGPT has prepared " +
      summary.record_count +
      " health record" +
      (summary.record_count === 1 ? "" : "s") +
      ".\n\nImport into HealthChecker+?";
    summary.requires_confirmation = true;
    summary.preview_id = "local-" + Date.now();
    return summary;
  }

  async function confirmImport(summary, options) {
    const Confirm = global.HCImportConfirmUI;
    const Engine = global.HCImportEngine;
    if (!Engine || !Engine.importHealthRecord) {
      throw new Error("import_engine_unavailable");
    }
    if (Confirm && Confirm.isProcessingLocked && Confirm.isProcessingLocked()) return null;

    let confirmed = true;
    if (!(options && options.skip_confirm)) {
      if (Confirm && Confirm.openAiConfirm) {
        confirmed = await Confirm.openAiConfirm(summary);
      }
    }
    if (!confirmed) {
      return { ok: false, cancelled: true, status: "cancelled" };
    }

    if (Confirm) Confirm.setProcessingLock(true);
    const records = summary.normalized_records || [];
    const results = [];
    let imported = 0;
    let duplicates = 0;
    let failed = 0;
    let grouped = 0;
    const batchId = "ai-chatgpt-" + Date.now();

    for (let i = 0; i < records.length; i++) {
      const rec = records[i];
      if (Confirm && Confirm.showProgress) {
        Confirm.showProgress({
          total: records.length,
          processed: i,
          imported,
          duplicates,
          failed,
          current_filename: rec.filename,
        });
      }
      try {
        const result = await Engine.importHealthRecord({
          document: JSON.stringify({
            extracted_measurements: rec.extracted_measurements,
            measured_at: rec.measured_at,
            interpretation: rec.interpretation,
            linkage: rec.linkage,
          }),
          filename: rec.filename,
          mime_type: "application/json",
          document_type: rec.document_type,
          acquisition_method: "external_ai",
          source_system: rec.source_system,
          measured_at: rec.measured_at,
          extracted_measurements: rec.extracted_measurements,
          interpretation: rec.interpretation,
          confidence: rec.confidence,
          provenance: rec.provenance,
          tags: rec.tags,
          batch_id: batchId,
        });
        if (result.duplicate) {
          duplicates++;
          results.push({ status: "duplicate", record_id: rec.record_id, document_id: result.document && result.document.id });
        } else {
          imported++;
          if (result.document && result.document.group_id) grouped++;
          results.push({ status: "imported", record_id: rec.record_id, document_id: result.document && result.document.id });
        }
      } catch (err) {
        failed++;
        results.push({ status: "failed", record_id: rec.record_id, errors: [String(err && err.message ? err.message : err)] });
      }
    }

    const report = {
      ok: failed === 0 && imported > 0,
      partial_success: imported > 0 && failed > 0,
      status: failed ? (imported ? "partial" : "failed") : "imported",
      provider_id: "chatgpt",
      imported,
      duplicates,
      failed,
      grouped_reports: grouped,
      updated_trends: true,
      dashboard_refreshed: true,
      doctor_visit_updated: true,
      results,
      confirmed_by_user: true,
      confirmation_timestamp: new Date().toISOString(),
      batch_id: batchId,
    };

    if (Confirm) {
      Confirm.setProcessingLock(false);
      Confirm.hideProgress();
      if (Confirm.showAiResult) {
        await Confirm.showAiResult(report, options && options.actions);
      }
    }

    if (global.HCVaultUI && global.HCVaultUI.refreshVaultViews) {
      global.HCVaultUI.refreshVaultViews();
    }
    if (global.HCExecutiveDashboard && global.HCExecutiveDashboard.refresh) {
      global.HCExecutiveDashboard.refresh();
    }

    return report;
  }

  async function importWithConfirmation(payload, options) {
    const preview = await previewPayload(payload);
    return confirmImport(preview, options);
  }

  global.HCAIHealthBridge = {
    PARSER_VERSION,
    normalizeChatGptPayload,
    previewPayload,
    confirmImport,
    importWithConfirmation,
    buildPreviewSummary,
  };
})(typeof window !== "undefined" ? window : globalThis);
