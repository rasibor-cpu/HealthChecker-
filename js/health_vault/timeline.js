/**
 * HC-201 / HC-301 — Chronological Health Timeline.
 * Merges vault documents, guardian timeline_events, and optional HC_V6 logs with dedupe.
 * Sort precedence: measured_at → report_date → imported_at.
 */
(function (global) {
  "use strict";

  let cache = null;

  function directionLabel(dir) {
    if (dir === "improving") return "Improving";
    if (dir === "worsening") return "Worsening";
    if (dir === "stable") return "Stable";
    return "n/a";
  }

  function sortDateForDoc(doc) {
    return String(doc.measured_at || doc.report_date || doc.imported_at || "");
  }

  function loadHcV6Entries() {
    try {
      const raw = localStorage.getItem("HC_V6");
      if (!raw) return [];
      const parsed = JSON.parse(raw);
      const logs = parsed.logs || [];
      return logs.map((row) => {
        const ts = String(row.ts || row.measured_at || "");
        const dedupe = "hc_v6|" + ts + "|" + row.g + "|" + row.sys + "|" + row.dia;
        return {
          date: ts,
          measured_at: ts,
          imported_at: ts,
          primary_category: "hc_v6",
          category_label: "hc_v6",
          entry_kind: "hc_v6_log",
          provenance: row.source || "HC_V6",
          severity: null,
          summary: "HC_V6 reading",
          payload: row,
          document: null,
          measurements: [],
          trend_impact: "",
          original_link: null,
          fhir_resources: {},
          dedupe_key: dedupe,
        };
      });
    } catch (_) {
      return [];
    }
  }

  function build(opts) {
    opts = opts || {};
    const Vault = global.HCHealthVault;
    const Trend = global.HCTrendEngine;
    if (!Vault) return [];

    const category = opts.category || null;
    const severity = opts.severity || null;
    const includeGuardian = opts.include_guardian_events !== false;
    const includeHcV6 = opts.include_hc_v6 !== false;
    const dateFrom = opts.date_from || null;
    const dateTo = opts.date_to || null;

    const docs = Vault.listDocuments().slice();
    const trends = Trend && Trend.getSnapshot ? Trend.getSnapshot() : Vault.getTrends();
    const entries = [];
    const seenKeys = Object.create(null);

    docs.forEach((doc) => {
      if (category && category !== "all") {
        const primary = doc.primary_category;
        const secondary = doc.secondary_categories || [];
        if (primary !== category && secondary.indexOf(category) < 0) return;
      }
      const measurements = Vault.listMeasurements({ document_id: doc.id });
      const relatedTrends = {};
      measurements.forEach((m) => {
        const key = String(m.metric || "").toLowerCase();
        if (trends[key]) relatedTrends[key] = trends[key];
      });
      const impact = Object.keys(relatedTrends)
        .map((k) => k + ": " + directionLabel(relatedTrends[k].direction))
        .join("; ");
      const sortDate = sortDateForDoc(doc);
      if (dateFrom && sortDate < dateFrom) return;
      if (dateTo && sortDate > dateTo) return;
      const dedupe = "doc|" + doc.id;
      if (seenKeys[dedupe]) return;
      seenKeys[dedupe] = true;
      entries.push({
        date: sortDate,
        measured_at: doc.measured_at,
        report_date: doc.report_date,
        imported_at: doc.imported_at,
        primary_category: doc.primary_category,
        category_label: doc.primary_category,
        document: doc,
        measurements: measurements,
        trend_impact: impact || "No trend impact yet",
        original_link: doc.storage_uri,
        entry_kind: "document",
        provenance: doc.provenance || doc.source_system,
        severity: null,
        fhir_resources: {
          document: "DocumentReference",
          observations: "Observation",
        },
      });
    });

    if (includeGuardian && Vault.listTimelineEvents) {
      Vault.listTimelineEvents().forEach((ev) => {
        if (category && category !== "all") {
          if (ev.category !== category && ev.kind !== category) return;
        }
        if (severity && ev.severity !== severity) return;
        const sortDate = String(ev.measured_at || ev.imported_at || "");
        if (dateFrom && sortDate < dateFrom) return;
        if (dateTo && sortDate > dateTo) return;
        const dedupe = String(ev.dedupe_key || ev.event_id || "");
        if (dedupe && seenKeys[dedupe]) return;
        if (dedupe) seenKeys[dedupe] = true;
        entries.push({
          date: sortDate,
          measured_at: ev.measured_at,
          imported_at: ev.imported_at,
          primary_category: ev.category,
          category_label: ev.category,
          entry_kind: ev.kind || "guardian_event",
          provenance: ev.provenance,
          severity: ev.severity,
          summary: ev.summary,
          payload: ev.payload || {},
          document: null,
          measurements: [],
          trend_impact: ev.summary || "",
          original_link: null,
          fhir_resources: {},
        });
      });
    }

    if (includeHcV6) {
      loadHcV6Entries().forEach((row) => {
        if (category && category !== "all" && category !== "hc_v6" && category !== row.primary_category) {
          return;
        }
        const sortDate = String(row.date || "");
        if (dateFrom && sortDate < dateFrom) return;
        if (dateTo && sortDate > dateTo) return;
        const dedupe = String(row.dedupe_key || "");
        if (dedupe && seenKeys[dedupe]) return;
        if (dedupe) seenKeys[dedupe] = true;
        entries.push(row);
      });
    }

    entries.sort((a, b) => String(b.date).localeCompare(String(a.date)));
    cache = entries;
    return entries;
  }

  function getTimeline() {
    return cache || build();
  }

  function invalidate() {
    cache = null;
  }

  function renderInto(el) {
    if (!el) return;
    const entries = build();
    if (!entries.length) {
      el.innerHTML = '<div class="muted">No imported medical documents yet.</div>';
      return;
    }
    el.innerHTML = entries
      .map((e) => {
        if (e.entry_kind && e.entry_kind !== "document") {
          return (
            `<div class="kpi">` +
            `<div><strong>${escapeHtml(e.date ? new Date(e.date).toLocaleString() : "—")}</strong>` +
            (e.severity
              ? ` · <span class="warn">${escapeHtml(e.severity)}</span>`
              : "") +
            `</div>` +
            `<div class="small">${escapeHtml(e.entry_kind)} — ${escapeHtml(
              e.summary || e.trend_impact || ""
            )}</div>` +
            `<div class="small muted">Provenance: ${escapeHtml(
              e.provenance || "unspecified"
            )}</div>` +
            `</div>`
          );
        }
        const mtxt = e.measurements.length
          ? e.measurements
              .map((m) => `${m.metric}: ${m.value ?? "—"} ${m.units || ""}`.trim())
              .join(" · ")
          : "No extracted measurements";
        return (
          `<div class="kpi">` +
          `<div><strong>${escapeHtml(new Date(e.date).toLocaleString())}</strong></div>` +
          `<div class="small">${escapeHtml(e.document.document_type)} — ${escapeHtml(
            e.document.original_filename || e.document.id
          )}</div>` +
          `<div class="small">${escapeHtml(mtxt)}</div>` +
          `<div class="small muted">Provenance: ${escapeHtml(
            provenanceLabel(e.document)
          )}</div>` +
          `<div class="small muted">Trend impact: ${escapeHtml(e.trend_impact)}</div>` +
          `<div class="small muted">Original: ${escapeHtml(
            e.original_link && String(e.original_link).indexOf("vault://") === 0
              ? e.original_link
              : e.original_link && String(e.original_link).indexOf("idb://") === 0
                ? e.original_link
                : "— (no local source document)"
          )}</div>` +
          `</div>`
        );
      })
      .join("");
  }

  function provenanceLabel(doc) {
    if (!doc) return "unspecified";
    if (doc.provenance) return String(doc.provenance);
    const tags = doc.tags || [];
    const hit = tags.find((t) => String(t).indexOf("provenance:") === 0);
    return hit ? String(hit).slice("provenance:".length) : "unspecified";
  }

  function escapeHtml(v) {
    return String(v == null ? "" : v)
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;");
  }

  global.HCHealthTimeline = {
    build,
    getTimeline,
    invalidate,
    renderInto,
  };
})(typeof window !== "undefined" ? window : globalThis);
