/**
 * HC-201 — Health Vault storage (append-only, never overwrite documents).
 * Metadata in localStorage HC_HEALTH_VAULT_V1; blobs in IndexedDB when available.
 * Existing HC_V6 storage is never mutated by vault writes except optional sync helpers.
 */
(function (global) {
  "use strict";

  const META_KEY = "HC_HEALTH_VAULT_V1";
  const IDB_NAME = "HCHealthVault";
  const IDB_STORE = "documents";
  const IDB_VERSION = 1;

  function emptyVault() {
    return {
      schema_version: "hc.health_vault.v1",
      patient_id: "default-patient",
      documents: [],
      measurements: [],
      audit: [],
      imports: [],
      trends: {},
      profile: {
        diagnoses: [],
        medications: [],
      },
      // HC-301 Guardian extensions (additive)
      alerts: [],
      baselines: {},
      cgm_sensors: [],
      cgm_inventory: {},
      cgm_continuity: {},
      data_gaps: [],
      timeline_events: [],
      guardian_status: {},
    };
  }

  function loadMeta() {
    try {
      const raw = localStorage.getItem(META_KEY);
      if (!raw) return emptyVault();
      const parsed = JSON.parse(raw);
      return Object.assign(emptyVault(), parsed, {
        documents: Array.isArray(parsed.documents) ? parsed.documents : [],
        measurements: Array.isArray(parsed.measurements) ? parsed.measurements : [],
        audit: Array.isArray(parsed.audit) ? parsed.audit : [],
        imports: Array.isArray(parsed.imports) ? parsed.imports : [],
        trends: parsed.trends && typeof parsed.trends === "object" ? parsed.trends : {},
        profile: parsed.profile && typeof parsed.profile === "object" ? parsed.profile : emptyVault().profile,
        alerts: Array.isArray(parsed.alerts) ? parsed.alerts : [],
        baselines: parsed.baselines && typeof parsed.baselines === "object" ? parsed.baselines : {},
        cgm_sensors: Array.isArray(parsed.cgm_sensors) ? parsed.cgm_sensors : [],
        cgm_inventory:
          parsed.cgm_inventory && typeof parsed.cgm_inventory === "object"
            ? parsed.cgm_inventory
            : {},
        cgm_continuity:
          parsed.cgm_continuity && typeof parsed.cgm_continuity === "object"
            ? parsed.cgm_continuity
            : {},
        data_gaps: Array.isArray(parsed.data_gaps) ? parsed.data_gaps : [],
        timeline_events: Array.isArray(parsed.timeline_events) ? parsed.timeline_events : [],
        guardian_status:
          parsed.guardian_status && typeof parsed.guardian_status === "object"
            ? parsed.guardian_status
            : {},
      });
    } catch (_) {
      return emptyVault();
    }
  }

  function saveMeta(vault) {
    localStorage.setItem(META_KEY, JSON.stringify(vault));
  }

  function appendAudit(vault, action, detail) {
    vault.audit.push({
      id: "a-" + Date.now().toString(36) + "-" + Math.random().toString(36).slice(2, 8),
      at: new Date().toISOString(),
      action,
      detail: detail || {},
    });
  }

  function openIdb() {
    return new Promise((resolve, reject) => {
      if (!global.indexedDB) {
        resolve(null);
        return;
      }
      const req = indexedDB.open(IDB_NAME, IDB_VERSION);
      req.onupgradeneeded = () => {
        const db = req.result;
        if (!db.objectStoreNames.contains(IDB_STORE)) {
          db.createObjectStore(IDB_STORE, { keyPath: "id" });
        }
      };
      req.onsuccess = () => resolve(req.result);
      req.onerror = () => resolve(null);
    });
  }

  async function putBlob(id, blobRecord) {
    const db = await openIdb();
    if (!db) return false;
    return new Promise((resolve) => {
      try {
        const tx = db.transaction(IDB_STORE, "readwrite");
        tx.objectStore(IDB_STORE).put(Object.assign({ id }, blobRecord));
        tx.oncomplete = () => resolve(true);
        tx.onerror = () => resolve(false);
      } catch (_) {
        resolve(false);
      }
    });
  }

  async function getBlob(id) {
    const db = await openIdb();
    if (!db) return null;
    return new Promise((resolve) => {
      try {
        const tx = db.transaction(IDB_STORE, "readonly");
        const req = tx.objectStore(IDB_STORE).get(id);
        req.onsuccess = () => resolve(req.result || null);
        req.onerror = () => resolve(null);
      } catch (_) {
        resolve(null);
      }
    });
  }

  async function sha256Hex(arrayBuffer) {
    if (!global.crypto || !crypto.subtle) {
      // Fallback non-cryptographic fingerprint for older browsers
      const bytes = new Uint8Array(arrayBuffer);
      let h = 2166136261;
      for (let i = 0; i < bytes.length; i++) {
        h ^= bytes[i];
        h = Math.imul(h, 16777619);
      }
      return "fnv1a-" + (h >>> 0).toString(16);
    }
    const digest = await crypto.subtle.digest("SHA-256", arrayBuffer);
    return Array.from(new Uint8Array(digest))
      .map((b) => b.toString(16).padStart(2, "0"))
      .join("");
  }

  /**
   * Permanently store a document. Never overwrites an existing id.
   * Duplicate sha256 still creates a new import audit entry and may skip blob re-write.
   */
  async function storeDocument({ document, measurements, blob, interpretation, parser, importMeta }) {
    const vault = loadMeta();
    if (vault.documents.some((d) => d.id === document.id)) {
      throw new Error("Document id already exists — refuse overwrite");
    }

    const existingByHash =
      document.sha256 && vault.documents.find((d) => d.sha256 && d.sha256 === document.sha256);

    const storageUri = existingByHash
      ? existingByHash.storage_uri
      : "idb://" + IDB_STORE + "/" + document.id;

    const doc = Object.assign({}, document, {
      storage_uri: document.storage_uri || storageUri,
      interpretation: interpretation || document.interpretation || null,
    });

    // Append-only document list (even if hash matches — keep lineage; mark duplicate_of)
    if (existingByHash) {
      doc.tags = Array.from(new Set([].concat(doc.tags || [], ["duplicate_content"])));
      doc.duplicate_of = existingByHash.id;
    }

    vault.documents.push(doc);

    (measurements || []).forEach((m) => {
      vault.measurements.push(
        Object.assign({}, m, {
          document_id: m.document_id || doc.id,
        })
      );
    });

    const importRecord = {
      import_id: "imp-" + Date.now().toString(36) + "-" + Math.random().toString(36).slice(2, 8),
      document_id: doc.id,
      imported_at: doc.imported_at,
      parser: parser || null,
      confidence: doc.parser_confidence,
      sha256: doc.sha256,
      measurement_count: (measurements || []).length,
      duplicate_content: Boolean(existingByHash),
      meta: importMeta || {},
    };
    vault.imports.push(importRecord);

    appendAudit(vault, "document_imported", {
      document_id: doc.id,
      sha256: doc.sha256,
      parser: parser,
      duplicate_content: Boolean(existingByHash),
    });

    saveMeta(vault);

    if (blob && !existingByHash) {
      await putBlob(doc.id, {
        mime_type: doc.mime_type,
        filename: doc.original_filename,
        data: blob,
        sha256: doc.sha256,
        stored_at: new Date().toISOString(),
      });
    }

    return { document: doc, import_record: importRecord, vault };
  }

  function listDocuments() {
    return loadMeta().documents.slice();
  }

  function listMeasurements(filter) {
    let items = loadMeta().measurements.slice();
    if (filter && filter.document_id) {
      items = items.filter((m) => m.document_id === filter.document_id);
    }
    if (filter && filter.metric) {
      items = items.filter((m) => m.metric === filter.metric);
    }
    if (filter && filter.category) {
      items = items.filter((m) => m.category === filter.category);
    }
    return items;
  }

  function getAudit() {
    return loadMeta().audit.slice();
  }

  function getImports() {
    return loadMeta().imports.slice();
  }

  function saveTrends(trends) {
    const vault = loadMeta();
    vault.trends = trends || {};
    appendAudit(vault, "trends_updated", { keys: Object.keys(vault.trends) });
    saveMeta(vault);
    return vault.trends;
  }

  function getTrends() {
    return loadMeta().trends || {};
  }

  function updateProfile(partial) {
    const vault = loadMeta();
    vault.profile = Object.assign({}, vault.profile, partial || {});
    appendAudit(vault, "profile_updated", { keys: Object.keys(partial || {}) });
    saveMeta(vault);
    return vault.profile;
  }

  function getProfile() {
    return loadMeta().profile;
  }

  /** Integrity: verify no document id collisions and measurements reference known docs. */
  function verifyIntegrity() {
    const vault = loadMeta();
    const ids = new Set();
    const issues = [];
    vault.documents.forEach((d) => {
      if (ids.has(d.id)) issues.push("duplicate_document_id:" + d.id);
      ids.add(d.id);
    });
    vault.measurements.forEach((m) => {
      if (m.document_id && !ids.has(m.document_id)) {
        issues.push("orphan_measurement:" + m.measurement_id);
      }
    });
    return { ok: issues.length === 0, issues, document_count: vault.documents.length };
  }

  // --- HC-301 Guardian storage helpers (additive) ---

  function listAlerts() {
    return loadMeta().alerts.slice();
  }

  function saveAlerts(alerts) {
    const vault = loadMeta();
    vault.alerts = Array.isArray(alerts) ? alerts : [];
    saveMeta(vault);
    return vault.alerts;
  }

  function upsertAlert(alert) {
    const vault = loadMeta();
    const items = vault.alerts || [];
    let found = false;
    for (let i = 0; i < items.length; i++) {
      if (items[i].alert_id === alert.alert_id) {
        items[i] = alert;
        found = true;
        break;
      }
    }
    if (!found) items.push(alert);
    vault.alerts = items;
    appendAudit(vault, "alert_upserted", { alert_id: alert.alert_id, severity: alert.severity });
    saveMeta(vault);
    return alert;
  }

  function saveBaselines(payload) {
    const vault = loadMeta();
    vault.baselines = payload || {};
    appendAudit(vault, "baselines_updated", {
      metrics: Object.keys((payload && payload.baselines) || {}),
    });
    saveMeta(vault);
    return vault.baselines;
  }

  function getBaselines() {
    return loadMeta().baselines || {};
  }

  function listCgmSensors() {
    return loadMeta().cgm_sensors.slice();
  }

  function upsertCgmSensor(sensor) {
    const vault = loadMeta();
    const items = vault.cgm_sensors || [];
    let found = false;
    for (let i = 0; i < items.length; i++) {
      if (items[i].sensor_id === sensor.sensor_id) {
        items[i] = sensor;
        found = true;
        break;
      }
    }
    if (!found) items.push(sensor);
    vault.cgm_sensors = items;
    appendAudit(vault, "cgm_sensor_upserted", {
      sensor_id: sensor.sensor_id,
      status: sensor.status,
    });
    saveMeta(vault);
    return sensor;
  }

  function getCgmInventory(patientId) {
    const inv = loadMeta().cgm_inventory || {};
    if (inv && inv.patient_id && patientId && inv.patient_id !== patientId) return null;
    if (inv && Object.keys(inv).length) return inv;
    return null;
  }

  function saveCgmInventory(inventory) {
    const vault = loadMeta();
    vault.cgm_inventory = inventory || {};
    appendAudit(vault, "cgm_inventory_updated", {
      patient_id: inventory && inventory.patient_id,
    });
    saveMeta(vault);
    return vault.cgm_inventory;
  }

  function saveCgmContinuity(payload) {
    const vault = loadMeta();
    vault.cgm_continuity = payload || {};
    appendAudit(vault, "cgm_continuity_updated", {
      state: payload && payload.state,
    });
    saveMeta(vault);
    return vault.cgm_continuity;
  }

  function getCgmContinuity() {
    return loadMeta().cgm_continuity || {};
  }

  function listDataGaps() {
    return loadMeta().data_gaps.slice();
  }

  function upsertDataGap(gap) {
    const vault = loadMeta();
    const items = vault.data_gaps || [];
    let found = false;
    for (let i = 0; i < items.length; i++) {
      if (items[i].gap_id === gap.gap_id) {
        items[i] = gap;
        found = true;
        break;
      }
    }
    if (!found) items.push(gap);
    vault.data_gaps = items;
    appendAudit(vault, "data_gap_upserted", { gap_id: gap.gap_id });
    saveMeta(vault);
    return gap;
  }

  function listTimelineEvents() {
    return loadMeta().timeline_events.slice();
  }

  function appendTimelineEvent(event) {
    const vault = loadMeta();
    const events = vault.timeline_events || [];
    const dedupe = event && event.dedupe_key;
    if (dedupe && events.some((e) => e.dedupe_key === dedupe)) {
      return events.find((e) => e.dedupe_key === dedupe);
    }
    events.push(event);
    vault.timeline_events = events;
    appendAudit(vault, "timeline_event_appended", { kind: event && event.kind });
    saveMeta(vault);
    return event;
  }

  function saveGuardianStatus(status) {
    const vault = loadMeta();
    vault.guardian_status = status || {};
    appendAudit(vault, "guardian_status_updated", {
      overall_state: status && status.overall_state,
    });
    saveMeta(vault);
    return vault.guardian_status;
  }

  function getGuardianStatus() {
    return loadMeta().guardian_status || {};
  }

  global.HCHealthVault = {
    META_KEY,
    loadMeta,
    storeDocument,
    listDocuments,
    listMeasurements,
    getAudit,
    getImports,
    saveTrends,
    getTrends,
    updateProfile,
    getProfile,
    getBlob,
    sha256Hex,
    verifyIntegrity,
    emptyVault,
    listAlerts,
    saveAlerts,
    upsertAlert,
    saveBaselines,
    getBaselines,
    listCgmSensors,
    upsertCgmSensor,
    getCgmInventory,
    saveCgmInventory,
    saveCgmContinuity,
    getCgmContinuity,
    listDataGaps,
    upsertDataGap,
    listTimelineEvents,
    appendTimelineEvent,
    saveGuardianStatus,
    getGuardianStatus,
  };
})(typeof window !== "undefined" ? window : globalThis);
