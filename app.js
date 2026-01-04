/* =========================================================
   HealthChecker+ — Master Controller (app.js)
   Build: GI–Sleep–Kidney Stack v1 (Phone-safe / GitHub Pages)
   - Tabs / Screens
   - Snapshot cards
   - GI Stability scoring + simplified stool descriptions
   - Sleep × Glucose coupling insight card
   - Kidney Load score (heuristic)
   - LocalStorage-first persistence
   ========================================================= */

(function () {
  "use strict";

  // -----------------------------
  // Utilities
  // -----------------------------
  const U = {
    qs(sel, root = document) {
      try { return root.querySelector(sel); } catch (e) { return null; }
    },
    qsa(sel, root = document) {
      try { return Array.from(root.querySelectorAll(sel)); } catch (e) { return []; }
    },
    nowISO() {
      try { return new Date().toISOString(); } catch (e) { return ""; }
    },
    fmtDateTime(iso) {
      if (!iso) return "";
      try {
        const d = new Date(iso);
        return d.toLocaleString();
      } catch (e) {
        return iso;
      }
    },
    toNum(x) {
      const n = Number(x);
      return Number.isFinite(n) ? n : null;
    },
    clamp(n, lo, hi) {
      return Math.max(lo, Math.min(hi, n));
    },
    load(key, fallback) {
      try {
        const raw = localStorage.getItem(key);
        return raw ? JSON.parse(raw) : fallback;
      } catch (e) {
        return fallback;
      }
    },
    save(key, value) {
      try { localStorage.setItem(key, JSON.stringify(value)); } catch (e) {}
    }
  };

  // -----------------------------
  // Storage keys
  // -----------------------------
  const K = {
    ACTIVE_PERSON: "hc_active_person",
    PEOPLE: "hc_people",
    READINGS: "hc_readings", // { personId: { glucose:[], bp:[], sleep:[], labs:[] } }
    GI_LATEST: "hc_gi_latest", // single latest GI entry (current person)
    KIDNEY_FLAGS: "hc_kidney_flags" // simple flags for kidney load
  };

  // -----------------------------
  // Domain: GI
  // -----------------------------
  const GI_OPTIONS = [
    { id: "pellets", label: "Very hard, pellet-like", desc: "Constipated", bristol: 1, baseScore: 45 },
    { id: "lumpy", label: "Hard sausage, lumpy", desc: "Mild constipation", bristol: 2, baseScore: 75 },
    { id: "cracked", label: "Normal sausage with cracks", desc: "Normal", bristol: 3, baseScore: 92 },
    { id: "smooth", label: "Smooth, soft sausage", desc: "Ideal", bristol: 4, baseScore: 98 },
    { id: "blobs", label: "Very soft blobs", desc: "Borderline loose", bristol: 5, baseScore: 80 },
    { id: "mushy", label: "Mushy, ragged", desc: "Diarrhea", bristol: 6, baseScore: 55 },
    { id: "watery", label: "Watery", desc: "Severe diarrhea", bristol: 7, baseScore: 35 }
  ];

  function computeGIScore(entry) {
    if (!entry || !entry.typeId) return null;
    const opt = GI_OPTIONS.find(o => o.id === entry.typeId);
    if (!opt) return null;

    let score = opt.baseScore;
    if (entry.noBloating === true) score += 5;
    if (entry.noUrgency === true) score += 5;
    if (entry.straining === true) score -= 10;
    if (typeof entry.bmCount === "number" && entry.bmCount > 1) score -= 10;

    score = U.clamp(score, 0, 100);

    let status = "Unstable";
    if (score >= 86) status = "Stable";
    else if (score >= 70) status = "Borderline";

    return { score, status, bristol: opt.bristol, label: opt.label };
  }

  // -----------------------------
  // Domain: Sleep × Glucose insight
  // -----------------------------
  function computeSleepGlucoseInsight({ sleepScore, energyScore, latestGlucose, giStatus }) {
    const g = U.toNum(latestGlucose);
    const s = U.toNum(sleepScore);
    const e = U.toNum(energyScore);

    let flag = "Neutral";
    let insight = "Add more sleep and glucose entries to unlock deeper insights.";

    const hasSleep = s !== null || e !== null;
    const hasGlucose = g !== null;

    if (hasSleep && hasGlucose) {
      const sleepLow = (s !== null && s < 55) || (e !== null && e < 55);
      const glucoseHigh = g >= 160;
      const glucoseMid = g >= 125 && g < 160;

      if (sleepLow && glucoseHigh) {
        flag = "High likelihood";
        insight = "Pattern suggests poor sleep/stress may be amplifying glucose. Focus on earlier dinner, wind-down routine, and sleep consolidation.";
      } else if (sleepLow && glucoseMid) {
        flag = "Moderate likelihood";
        insight = "Sleep looks suboptimal and glucose is moderately elevated. Stabilize sleep to reduce overnight variability.";
      } else if (!sleepLow && glucoseHigh) {
        flag = "Action needed";
        insight = "Sleep appears reasonable but glucose is high. Review dinner composition, timing, and post-meal activity.";
      } else if (!sleepLow && glucoseMid) {
        flag = "On track";
        insight = "Sleep is adequate and glucose is mid-range. Incremental gains will come from meal timing and GI stability.";
      } else {
        flag = "Good";
        insight = "Sleep and glucose look aligned. Maintain the routine; avoid late-night refined carbs.";
      }

      if (giStatus === "Unstable" || giStatus === "Borderline") {
        insight += " GI stability may be contributing to volatility.";
      }
    }

    return { flag, insight };
  }

  // -----------------------------
  // Domain: Kidney Load score (heuristic)
  // Higher score = higher load (worse)
  // -----------------------------
  function computeKidneyLoad({ sodiumFlag, proteinFlag, hydrationFlag, bpSys, bpDia, glucose }) {
    let load = 25;

    const sys = U.toNum(bpSys);
    const dia = U.toNum(bpDia);
    const g = U.toNum(glucose);

    if (sodiumFlag) load += 15;
    if (proteinFlag) load += 15;

    if (hydrationFlag === "low") load += 15;
    else if (hydrationFlag === "good") load -= 5;

    if (sys !== null && sys >= 140) load += 15;
    if (dia !== null && dia >= 90) load += 10;

    if (g !== null && g >= 180) load += 15;
    else if (g !== null && g >= 140) load += 8;

    load = U.clamp(load, 0, 100);

    let label = "Low";
    if (load >= 60) label = "Elevated";
    else if (load >= 40) label = "Moderate";

    return { load, label };
  }

  // -----------------------------
  // UI: Card template
  // -----------------------------
  function cardHTML(title, main, sub) {
    const t = title || "";
    const m = main || "";
    const s = sub || "";
    return `
      <div class="card">
        <div class="card-title">${t}</div>
        <div class="card-main">${m}</div>
        <div class="card-sub">${s}</div>
      </div>
    `;
  }

  // -----------------------------
  // Data model: People + Readings
  // -----------------------------
  function initPeople() {
    // If you already have people stored, keep them.
    let people = U.load(K.PEOPLE, null);
    if (!people || !Array.isArray(people) || people.length === 0) {
      people = [{ id: "primary", name: "Robert (Primary)" }];
      U.save(K.PEOPLE, people);
    }
    let active = U.load(K.ACTIVE_PERSON, null);
    if (!active) {
      active = people[0].id;
      U.save(K.ACTIVE_PERSON, active);
    }
    return { people, active };
  }

  function getReadingsStore() {
    return U.load(K.READINGS, {});
  }

  function setReadingsStore(store) {
    U.save(K.READINGS, store || {});
  }

  function ensurePersonStore(store, personId) {
    store[personId] = store[personId] || {};
    store[personId].glucose = store[personId].glucose || [];
    store[personId].bp = store[personId].bp || [];
    store[personId].sleep = store[personId].sleep || [];
    store[personId].labs = store[personId].labs || [];
    return store;
  }

  function latestOf(arr) {
    if (!Array.isArray(arr) || arr.length === 0) return null;
    // assume entries have ts; choose most recent
    return arr.slice().sort((a, b) => (b.ts || "").localeCompare(a.ts || ""))[0];
  }

  // -----------------------------
  // UI: Screens / Tabs
  // -----------------------------
  function setupTabs() {
    const tabButtons = U.qsa("[data-target], .tab-btn, button[data-tab]");
    const screens = U.qsa(".screen, [data-screen], section[id]");

    // Fallback: if your HTML uses IDs like snapshotScreen, addScreen, etc.
    function showScreen(targetId) {
      screens.forEach(s => {
        const sid = s.getAttribute("data-screen") || s.id || "";
        // show if matches target or contains it
        const match = sid === targetId || sid === `${targetId}Screen` || sid.toLowerCase() === targetId.toLowerCase();
        s.style.display = match ? "" : "none";
      });
    }

    tabButtons.forEach(btn => {
      btn.addEventListener("click", () => {
        const t = btn.getAttribute("data-target") || btn.getAttribute("data-tab") || btn.getAttribute("data-screen");
        if (t) {
          tabButtons.forEach(b => b.classList.remove("active"));
          btn.classList.add("active");
          showScreen(t);
          if (t.toLowerCase().includes("snap")) renderSnapshot();
        }
      });
    });

    // Default to Snapshot if present
    showScreen("snapshot");
  }

  // -----------------------------
  // Render: Snapshot
  // -----------------------------
  function renderSnapshot() {
    const { active } = initPeople();
    let store = getReadingsStore();
    store = ensurePersonStore(store, active);

    const gLatest = latestOf(store[active].glucose);
    const bpLatest = latestOf(store[active].bp);
    const sLatest = latestOf(store[active].sleep);

    const glucoseVal = gLatest ? gLatest.value : null;
    const glucoseTs = gLatest ? gLatest.ts : null;

    // Moving HbA1c estimate from recent glucose (simple, conservative)
    // If you have your own implementation elsewhere, you can refine later.
    const last14 = store[active].glucose
      .slice()
      .sort((a, b) => (b.ts || "").localeCompare(a.ts || ""))
      .slice(0, 14)
      .map(x => U.toNum(x.value))
      .filter(x => x !== null);

    const avgG = last14.length ? Math.round(last14.reduce((a, b) => a + b, 0) / last14.length) : null;
    const estA1c = avgG !== null ? ((avgG + 46.7) / 28.7) : null; // standard eAG conversion

    const bpMain = bpLatest ? `${bpLatest.sys}/${bpLatest.dia} mmHg (P ${bpLatest.pulse ?? "—"})` : "Not recorded";
    const bpSub = bpLatest ? (bpLatest.note || U.fmtDateTime(bpLatest.ts)) : "Add BP reading";

    const sleepMain = sLatest ? `Score ${sLatest.score ?? "—"} • Energy ${sLatest.energy ?? "—"}` : "Not recorded";
    const sleepSub = sLatest ? (sLatest.note || U.fmtDateTime(sLatest.ts)) : "Add sleep data";

    // Where to render:
    // Prefer explicit containers if present, else fallback to first visible screen.
    const snapRoot =
      U.qs("#snapshot") ||
      U.qs("[data-screen='snapshot']") ||
      U.qs("#snapshotScreen") ||
      U.qs(".snapshot") ||
      null;

    if (!snapRoot) return;

    // Ensure a dedicated cards container exists (so we can append safely)
    let cards = U.qs("#snapshotCards", snapRoot);
    if (!cards) {
      cards = document.createElement("div");
      cards.id = "snapshotCards";
      snapRoot.innerHTML = ""; // clean render
      snapRoot.appendChild(cards);
    } else {
      cards.innerHTML = "";
    }

    // Expose latest values for other modules (optional)
    window.latestGlucoseValue = glucoseVal;
    window.latestSleepScore = sLatest ? sLatest.score : null;
    window.latestEnergyScore = sLatest ? sLatest.energy : null;
    window.latestBP = bpLatest ? `${bpLatest.sys}/${bpLatest.dia}` : null;

    // Core cards
    cards.insertAdjacentHTML("beforeend",
      cardHTML("Glucose (latest)", glucoseVal !== null ? `${glucoseVal} mg/dL` : "Not recorded", glucoseTs ? U.fmtDateTime(glucoseTs) : "Add glucose reading") +
      cardHTML("Estimated HbA1c (moving)", (estA1c !== null ? `${estA1c.toFixed(2)}%` : "Insufficient data"), (avgG !== null ? `Avg glucose ${avgG} mg/dL • ${last14.length} point(s) • last 14d` : "Add more glucose readings")) +
      cardHTML("Blood Pressure (latest)", bpMain, bpSub) +
      cardHTML("Sleep (latest)", sleepMain, sleepSub)
    );

    // NEW: GI / Sleep×Glucose / Kidney
    const giLatest = U.load(`${K.GI_LATEST}:${active}`, null);
    const giComputed = computeGIScore(giLatest);

    const giMain = giComputed ? `${giComputed.status} • ${giComputed.score}/100` : "Not recorded";
    const giSub  = giComputed ? `Selected: ${giComputed.label}` : "Add GI status via Add → Symptoms";

    const sgi = computeSleepGlucoseInsight({
      sleepScore: sLatest ? sLatest.score : null,
      energyScore: sLatest ? sLatest.energy : null,
      latestGlucose: glucoseVal,
      giStatus: giComputed ? giComputed.status : null
    });

    const kidneyFlags = U.load(`${K.KIDNEY_FLAGS}:${active}`, { sodiumFlag: false, proteinFlag: false, hydrationFlag: "unknown" });

    const kidney = computeKidneyLoad({
      sodiumFlag: !!kidneyFlags.sodiumFlag,
      proteinFlag: !!kidneyFlags.proteinFlag,
      hydrationFlag: kidneyFlags.hydrationFlag,
      bpSys: bpLatest ? bpLatest.sys : null,
      bpDia: bpLatest ? bpLatest.dia : null,
      glucose: glucoseVal
    });

    cards.insertAdjacentHTML("beforeend",
      cardHTML("Gut Stability (latest)", giMain, giSub) +
      cardHTML("Sleep × Glucose (insight)", sgi.flag, sgi.insight) +
      cardHTML("Kidney Load (score)", `${kidney.label} • ${kidney.load}/100`, "Context score only (not a lab result)")
    );
  }

  // -----------------------------
  // Add: simple handlers (optional)
  // If your UI already has its own forms, these won’t interfere.
  // -----------------------------
  function wireQuickButtons() {
    // Buttons on your screenshot: Add reading / Add symptoms / Report
    const btnAddReading = U.qs("button#addReading, button[data-action='add-reading'], .btn-add-reading");
    const btnAddSymptoms = U.qs("button#addSymptoms, button[data-action='add-symptoms'], .btn-add-symptoms");

    // If your HTML uses bottom bar buttons without IDs, try match by text
    const allButtons = U.qsa("button");
    const byText = (txt) => allButtons.find(b => (b.textContent || "").trim().toLowerCase() === txt);

    const addReadingBtn = btnAddReading || byText("add reading");
    const addSymptomsBtn = btnAddSymptoms || byText("add symptoms");

    if (addReadingBtn) {
      addReadingBtn.addEventListener("click", () => {
        // Minimal prompt-based entry (works on phone). Later you can replace with form UI.
        const val = prompt("Enter glucose (mg/dL):");
        const g = U.toNum(val);
        if (g === null) return;

        const { active } = initPeople();
        let store = getReadingsStore();
        store = ensurePersonStore(store, active);
        store[active].glucose.push({ value: g, ts: U.nowISO(), source: "manual" });
        setReadingsStore(store);

        renderSnapshot();
        alert("Glucose saved.");
      });
    }

    if (addSymptomsBtn) {
      addSymptomsBtn.addEventListener("click", () => {
        const { active } = initPeople();

        // Choose GI type
        const menu = GI_OPTIONS.map((o, idx) => `${idx + 1}. ${o.label} (${o.desc})`).join("\n");
        const pick = prompt(`GI Status — pick one:\n\n${menu}\n\nEnter number (1-${GI_OPTIONS.length}):`);
        const idx = U.toNum(pick);
        if (idx === null || idx < 1 || idx > GI_OPTIONS.length) return;

        const typeId = GI_OPTIONS[idx - 1].id;

        const noBloating = (prompt("No bloating today? (y/n)") || "").toLowerCase().startsWith("y");
        const noUrgency = (prompt("No urgency today? (y/n)") || "").toLowerCase().startsWith("y");
        const straining = (prompt("Straining? (y/n)") || "").toLowerCase().startsWith("y");
        const bmCountRaw = prompt("How many bowel movements today? (Enter 1 if unsure)");
        const bmCount = U.toNum(bmCountRaw) || 1;

        const entry = { typeId, noBloating, noUrgency, straining, bmCount, ts: U.nowISO() };
        U.save(`${K.GI_LATEST}:${active}`, entry);

        renderSnapshot();
        alert("GI status saved.");
      });
    }
  }

  // -----------------------------
  // Init
  // -----------------------------
  window.addEventListener("DOMContentLoaded", () => {
    // Maintain existing UI where possible
    try { initPeople(); } catch (e) {}

    // Basic tabs (non-destructive)
    try { setupTabs(); } catch (e) {}

    // Render Snapshot on load
    try { renderSnapshot(); } catch (e) {}

    // Wire quick buttons (if present)
    try { wireQuickButtons(); } catch (e) {}
  });

})();
