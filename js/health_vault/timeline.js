/**
 * HC-201 — Chronological Health Timeline.
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

  function build() {
    const Vault = global.HCHealthVault;
    const Trend = global.HCTrendEngine;
    if (!Vault) return [];

    const docs = Vault.listDocuments().slice();
    const trends = Trend && Trend.getSnapshot ? Trend.getSnapshot() : Vault.getTrends();

    const entries = docs.map((doc) => {
      const measurements = Vault.listMeasurements({ document_id: doc.id });
      const relatedTrends = {};
      measurements.forEach((m) => {
        const key = String(m.metric || "").toLowerCase();
        if (trends[key]) relatedTrends[key] = trends[key];
      });
      const impact = Object.keys(relatedTrends)
        .map((k) => k + ": " + directionLabel(relatedTrends[k].direction))
        .join("; ");

      return {
        date: doc.measured_at || doc.imported_at,
        document: doc,
        measurements,
        trend_impact: impact || "No trend impact yet",
        original_link: doc.storage_uri,
        fhir_resources: {
          document: "DocumentReference",
          observations: "Observation",
        },
      };
    });

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
