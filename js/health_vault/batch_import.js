/**
 * HC-201G — Browser batch import orchestrator.
 * Delegates each file to HCImportEngine (canonical pipeline mirror).
 */
(function (global) {
  "use strict";

  const DEFAULT_LIMITS = {
    max_files_per_batch: 25,
    max_file_bytes: 20 * 1024 * 1024,
    max_batch_bytes: 150 * 1024 * 1024,
    allowed_extensions: [".pdf", ".png", ".jpg", ".jpeg", ".json"],
  };

  function uuid() {
    if (global.crypto && crypto.randomUUID) return crypto.randomUUID();
    return "b-" + Date.now().toString(36) + "-" + Math.random().toString(36).slice(2, 10);
  }

  function sanitizeFilename(name) {
    const base = String(name || "upload.bin").split(/[/\\]/).pop() || "upload.bin";
    return base.replace(/[^\w.\- ()\[\]]+/g, "_").replace(/^[.\s_]+|[.\s_]+$/g, "") || "upload.bin";
  }

  function extensionOf(name) {
    const m = String(name || "").toLowerCase().match(/(\.[a-z0-9]+)$/);
    return m ? m[1] : "";
  }

  function classifyHint(filename, mime) {
    if (global.HCMedicalDocument && HCMedicalDocument.classifyDocumentType) {
      return HCMedicalDocument.classifyDocumentType(filename, mime);
    }
    return "unknown";
  }

  function formatBytes(n) {
    const v = Number(n) || 0;
    if (v < 1024) return v + " B";
    if (v < 1024 * 1024) return (v / 1024).toFixed(1) + " KB";
    return (v / (1024 * 1024)).toFixed(1) + " MB";
  }

  function createQueueItem(file, index) {
    const filename = sanitizeFilename(file.name);
    const mime = file.type || "application/octet-stream";
    return {
      id: uuid(),
      index,
      file,
      filename,
      mime_type: mime,
      size_bytes: file.size || 0,
      document_type: classifyHint(filename, mime),
      status: "queued",
      thumbnail_url: null,
      group_id: null,
      sequence_number: null,
      page_number: null,
      group_title: null,
      result: null,
      errors: [],
      warnings: [],
    };
  }

  function validateQueue(items, limits) {
    const cfg = Object.assign({}, DEFAULT_LIMITS, limits || {});
    const errors = [];
    if (!items.length) {
      errors.push({ code: "empty_batch", message: "Batch contains no files" });
    }
    if (items.length > cfg.max_files_per_batch) {
      errors.push({
        code: "max_files_exceeded",
        message:
          "Maximum " + cfg.max_files_per_batch + " files per batch (selected " + items.length + ")",
      });
    }
    let total = 0;
    items.forEach((it, i) => {
      total += it.size_bytes || 0;
      const ext = extensionOf(it.filename);
      if (cfg.allowed_extensions.indexOf(ext) < 0) {
        errors.push({
          code: "unsupported_type",
          index: i,
          filename: it.filename,
          message: "Unsupported type: " + it.filename,
        });
      }
      if ((it.size_bytes || 0) > cfg.max_file_bytes) {
        errors.push({
          code: "max_file_bytes_exceeded",
          index: i,
          filename: it.filename,
          message: it.filename + " exceeds " + formatBytes(cfg.max_file_bytes),
        });
      }
    });
    if (total > cfg.max_batch_bytes) {
      errors.push({
        code: "max_batch_bytes_exceeded",
        message: "Batch exceeds " + formatBytes(cfg.max_batch_bytes),
      });
    }
    return { ok: errors.length === 0, errors: errors, total_bytes: total, limits: cfg };
  }

  function suggestGroups(items) {
    const pageRe = /(?:page|pg|p)[_\-\s]?(\d+)/i;
    const seqRe = /[_\-](\d{1,3})$/i;
    const buckets = {};
    items.forEach((it, i) => {
      let stem = it.filename.replace(/\.[^.]+$/, "").toLowerCase();
      stem = stem.replace(pageRe, "").replace(seqRe, "").replace(/[_\-\s]+$/, "");
      const key = (it.document_type || "unknown") + "|" + stem;
      (buckets[key] = buckets[key] || []).push(i);
    });
    Object.keys(buckets).forEach((key) => {
      const idxs = buckets[key];
      if (idxs.length < 2) {
        idxs.forEach((i) => {
          items[i].group_id = uuid();
          items[i].sequence_number = 1;
          const m = pageRe.exec(items[i].filename);
          items[i].page_number = m ? Number(m[1]) : null;
          items[i].group_title = null;
        });
        return;
      }
      const gid = uuid();
      const ordered = idxs.slice().sort((a, b) => {
        const fa = items[a].filename;
        const fb = items[b].filename;
        const pa = pageRe.exec(fa);
        const pb = pageRe.exec(fb);
        const sa = seqRe.exec(fa.replace(/\.[^.]+$/, ""));
        const sb = seqRe.exec(fb.replace(/\.[^.]+$/, ""));
        const na = Number((pa && pa[1]) || (sa && sa[1]) || 10000);
        const nb = Number((pb && pb[1]) || (sb && sb[1]) || 10000);
        return na - nb || fa.localeCompare(fb);
      });
      const title = "Grouped report · " + items[ordered[0]].filename.replace(/\.[^.]+$/, "");
      ordered.forEach((i, seq) => {
        items[i].group_id = gid;
        items[i].sequence_number = seq + 1;
        const m = pageRe.exec(items[i].filename);
        items[i].page_number = m ? Number(m[1]) : seq + 1;
        items[i].group_title = title;
      });
    });
  }

  async function makeThumbnail(item) {
    if (!item.file || !String(item.mime_type || "").startsWith("image/")) return;
    try {
      item.thumbnail_url = URL.createObjectURL(item.file);
    } catch (_) {}
  }

  async function processQueue(items, options) {
    const opts = options || {};
    const limits = opts.limits || DEFAULT_LIMITS;
    const onProgress = typeof opts.onProgress === "function" ? opts.onProgress : null;
    const batchId = opts.batch_id || uuid();
    const validation = validateQueue(items, limits);
    if (!validation.ok) {
      return {
        ok: false,
        status: "rejected",
        batch_id: batchId,
        total: items.length,
        imported: 0,
        duplicates: 0,
        failed: items.length,
        requires_review: 0,
        results: [],
        validation: validation,
      };
    }

    suggestGroups(items);
    let imported = 0;
    let duplicates = 0;
    let failed = 0;
    let requires_review = 0;
    const results = [];

    for (let i = 0; i < items.length; i++) {
      const item = items[i];
      if (opts.only_failed && item.status !== "failed" && item.status !== "requires_review") {
        continue;
      }
      item.status = "processing";
      if (onProgress) onProgress(snapshot(items, batchId, imported, duplicates, failed, requires_review));

      try {
        const result = await global.HCImportEngine.importHealthRecord({
          file: item.file,
          filename: item.filename,
          mime_type: item.mime_type,
          document_type: item.document_type,
          batch_id: batchId,
          group_id: item.group_id,
          sequence_number: item.sequence_number,
          page_number: item.page_number,
          group_title: item.group_title,
          tags: ["hc201g_batch", "batch_id:" + batchId].concat(
            item.group_id ? ["group_id:" + item.group_id] : []
          ),
        });
        item.result = result;
        if (result.duplicate) {
          item.status = "duplicate";
          duplicates += 1;
        } else if (!result.ok) {
          item.status = "failed";
          item.errors = result.errors || ["import_failed"];
          failed += 1;
        } else if ((result.errors || []).length) {
          item.status = "requires_review";
          requires_review += 1;
        } else {
          item.status = "imported";
          imported += 1;
        }
        item.warnings = result.warnings || [];
        results.push({
          index: i,
          filename: item.filename,
          ok: !!result.ok,
          duplicate: !!result.duplicate,
          status: item.status,
          document_id: result.document && result.document.id,
          sha256: result.sha256,
          confidence: result.confidence,
          batch_id: batchId,
          group_id: item.group_id,
          sequence_number: item.sequence_number,
          page_number: item.page_number,
          group_title: item.group_title,
          errors: item.errors,
          warnings: item.warnings,
        });
      } catch (err) {
        item.status = "failed";
        item.errors = [err && err.message ? err.message : String(err)];
        failed += 1;
        results.push({
          index: i,
          filename: item.filename,
          ok: false,
          duplicate: false,
          status: "failed",
          errors: item.errors,
          batch_id: batchId,
          group_id: item.group_id,
        });
      }
      if (onProgress) onProgress(snapshot(items, batchId, imported, duplicates, failed, requires_review));
    }

    const processed = imported + duplicates + failed + requires_review;
    const ok = failed === 0 && requires_review === 0;
    return {
      ok: ok,
      partial_success: imported > 0 && failed > 0,
      status: ok ? "complete" : imported || duplicates ? "partial_success" : "failed",
      batch_id: batchId,
      total: items.length,
      imported: imported,
      duplicates: duplicates,
      failed: failed,
      requires_review: requires_review,
      processed: processed,
      results: results,
      limits: limits,
    };
  }

  function snapshot(items, batchId, imported, duplicates, failed, requires_review) {
    const processed = items.filter((x) =>
      ["imported", "duplicate", "failed", "requires_review"].includes(x.status)
    ).length;
    return {
      batch_id: batchId,
      total: items.length,
      processed: processed,
      imported: imported,
      duplicates: duplicates,
      failed: failed,
      requires_review: requires_review,
      items: items,
    };
  }

  global.HCBatchImport = {
    DEFAULT_LIMITS: DEFAULT_LIMITS,
    sanitizeFilename: sanitizeFilename,
    formatBytes: formatBytes,
    createQueueItem: createQueueItem,
    validateQueue: validateQueue,
    suggestGroups: suggestGroups,
    makeThumbnail: makeThumbnail,
    processQueue: processQueue,
  };
})(typeof window !== "undefined" ? window : globalThis);
