/* =========================================================
   HealthChecker+ — app.js (FULL REPLACEMENT)
   Build: GI–Sleep–Kidney Wired v1
   - ID-based screens: #snapshot, #add, #symptomChecker (and fallbacks)
   - Snapshot cards + GI/Sleep×Glucose/Kidney cards
   - GI Stability button inside Add Symptoms
   - LocalStorage-first
   ========================================================= */

(function () {
  "use strict";

  // ---------- Utilities ----------
  const U = {
    qs(sel, root = document) { try { return root.querySelector(sel); } catch { return null; } },
    qsa(sel, root = document) { try { return Array.from(root.querySelectorAll(sel)); } catch { return []; } },
    nowISO() { try { return new Date().toISOString(); } catch { return ""; } },
    fmt(iso) {
      if (!iso) return "";
      try { return new Date(iso).toLocaleString(); } catch { return iso; }
    },
    num(x) { const n = Number(x); return Number.isFinite(n) ? n : null; },
    clamp(n, lo, hi) { return Math.max(lo, Math.min(hi, n)); },
    load(key, fallback) {
      try { const raw = localStorage.getItem(key); return raw ? JSON.parse(raw) : fallback; }
      catch { return fallback; }
    },
    save(key, val) { try { localStorage.setItem(key, JSON.stringify(val)); } catch {} }
  };

  // ---------- Storage Keys ----------
  const K = {
    ACTIVE_PERSON: "hc_active_person",
    PEOPLE: "hc_people",
    READINGS: "hc_readings",           // { personId: { glucose:[], bp:[], sleep:[] } }
    GI_LATEST: "hc_gi_latest",         // { personId: entry }
    KIDNEY_FLAGS: "hc_kidney_flags"    // { personId: flags }
  };

  // ---------- GI Options ----------
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

    return { score, status, label: opt.label, bristol: opt.bristol };
  }

  // ---------- Sleep × Glucose Insight ----------
  function computeSleepGlucoseInsight({ sleepScore, energyScore, glucose, giStatus }) {
    const s = U.num(sleepScore);
    const e = U.num(energyScore);
    const g = U.num(glucose);

    let flag = "Neutral";
    let insight = "Add more sleep and glucose data to unlock deeper insights.";

    const hasSleep = (s !== null) || (e !== null);
    const hasGlucose = (g !== null);

    if (hasSleep && hasGlucose) {
      const sleepLow = (s !== null && s < 55) || (e !== null && e < 55);
      const glucoseHigh = g >= 160;
      const glucoseMid = g >= 125 && g < 160;

      if (sleepLow && glucoseHigh) {
        flag = "High likelihood";
        insight = "Poor sleep/stress may be amplifying glucose. Prioritize earlier dinner, wind-down routine, and sleep consolidation.";
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

  // ---------- Kidney Load (heuristic) ----------
  function computeKidneyLoad({ sodiumFlag, proteinFlag, hydrationFlag, bpSys, bpDia, glucose }) {
    let load = 25;

    const sys = U.num(bpSys);
    const dia = U.num(bpDia);
    const g = U.num(glucose);

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

  // ---------- UI: Card ----------
  function cardHTML(title, main, sub) {
    return `
      <div class="card">
        <div class="card-title">${title || ""}</div>
        <div class="card-main">${main || ""}</div>
        <div class="card-sub">${sub || ""}</div>
      </div>
    `;
  }

  // ---------- People + Readings ----------
  function initPeople() {
    let people = U.load(K.PEOPLE, null);
    if (!Array.isArray(people) || people.length === 0) {
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

  function getStore() { return U.load(K.READINGS, {}); }
  function setStore(s) { U.save(K.READINGS, s || {}); }

  function ensurePerson(store, pid) {
    store[pid] = store[pid] || {};
    store[pid].glucose = store[pid].glucose || [];
    store[pid].bp = store[pid].bp || [];
    store[pid].sleep = store[pid].sleep || [];
    return store;
  }

  function latestOf(arr) {
    if (!Array.isArray(arr) || arr.length === 0) return null;
    return arr.slice().sort((a, b) => (b.ts || "").localeCompare(a.ts || ""))[0];
  }

  function lastNGlucose(arr, n) {
    if (!Array.isArray(arr) || arr.length === 0) return [];
    return arr.slice().sort((a, b) => (b.ts || "").localeCompare(a.ts || "")).slice(0, n);
  }

  // ---------- Screens (ID-first) ----------
  function detectScreens() {
    // Common IDs from your repo; includes fallbacks
    const screens = [
      U.qs("#snapshot"),
      U.qs("#add"),
      U.qs("#symptomChecker"),
      U.qs("#trends"),
      U.qs("#reports"),
      U.qs("#profile")
    ].filter(Boolean);

    // Include any elements marked as screen-like
    const extra = U.qsa("[data-screen], .screen, section[id]").filter(el => el && el.id);
    extra.forEach(el => { if (!screens.includes(el)) screens.push(el); });

    return screens;
  }

  function showScreen(screenId) {
    const screens = detectScreens();
    screens.forEach(el => {
      const id = el.id || el.getAttribute("data-screen") || "";
      el.style.display = (id === screenId) ? "" : "none";
    });

    // Render Snapshot when it’s shown
    if (screenId === "snapshot") renderSnapshot();
    if (screenId === "symptomChecker") wireGIArea();
  }

  function setupTabClicks() {
    // Support buttons/links that target screens by:
    // - data-target="snapshot"
    // - data-screen="snapshot"
    // - href="#snapshot"
    const clickables = U.qsa("[data-target], [data-screen], a[href^='#'], button");
    clickables.forEach(el => {
      el.addEventListener("click", (ev) => {
        const dt = el.getAttribute("data-target") || el.getAttribute("data-screen") || "";
        const href = (el.tagName === "A") ? (el.getAttribute("href") || "") : "";
        const id = (dt || (href.startsWith("#") ? href.slice(1) : "")).trim();

        // Only intercept if it matches a known screen id
        if (id && U.qs(`#${CSS.escape(id)}`)) {
          ev.preventDefault();
          showScreen(id);
        }
      });
    });
  }

  // ---------- Snapshot Render ----------
  function renderSnapshot() {
    const { active } = initPeople();
    let store = getStore();
    store = ensurePerson(store, active);

    const gLatest = latestOf(store[active].glucose);
    const bpLatest = latestOf(store[active].bp);
    const sLatest = latestOf(store[active].sleep);

    const glucoseVal = gLatest ? gLatest.value : null;
    const glucoseTs = gLatest ? gLatest.ts : null;

    const last14 = lastNGlucose(store[active].glucose, 14)
      .map(x => U.num(x.value))
      .filter(x => x !== null);

    const avgG = last14.length ? Math.round(last14.reduce((a, b) => a + b, 0) / last14.length) : null;
    const estA1c = (avgG !== null) ? ((avgG + 46.7) / 28.7) : null;

    const bpMain = bpLatest ? `${bpLatest.sys}/${bpLatest.dia} mmHg (P ${bpLatest.pulse ?? "—"})` : "Not recorded";
    const bpSub = bpLatest ? (bpLatest.note || U.fmt(bpLatest.ts)) : "Add BP reading";

    const sleepMain = sLatest ? `Score ${sLatest.score ?? "—"} • Energy ${sLatest.energy ?? "—"}` : "Not recorded";
    const sleepSub = sLatest ? (sLatest.note || U.fmt(sLatest.ts)) : "Add sleep data";

    const snapRoot = U.qs("#snapshot") || U.qs("#snapshotScreen") || U.qs("[data-screen='snapshot']");
    if (!snapRoot) return;

    let cards = U.qs("#snapshotCards", snapRoot);
    if (!cards) {
      cards = document.createElement("div");
      cards.id = "snapshotCards";
      // Do not wipe whole snapshot if you have header elements; append cards container
      snapRoot.appendChild(cards);
    }
    cards.innerHTML = "";

    // Core cards
    cards.insertAdjacentHTML("beforeend",
      cardHTML("Glucose (latest)", glucoseVal !== null ? `${glucoseVal} mg/dL` : "Not recorded", glucoseTs ? U.fmt(glucoseTs) : "Add glucose reading") +
      cardHTML("Estimated HbA1c (moving)", (estA1c !== null ? `${estA1c.toFixed(2)}%` : "Insufficient data"), (avgG !== null ? `Avg glucose ${avgG} mg/dL • ${last14.length} point(s) • last 14` : "Add more glucose readings")) +
      cardHTML("Blood Pressure (latest)", bpMain, bpSub) +
      cardHTML("Sleep (latest)", sleepMain, sleepSub)
    );

    // GI card
    const giAll = U.load(K.GI_LATEST, {});
    const giLatest = giAll[active] || null;
    const giComputed = computeGIScore(giLatest);

    const giMain = giComputed ? `${giComputed.status} • ${giComputed.score}/100` : "Not recorded";
    const giSub  = giComputed ? `Selected: ${giComputed.label}` : "Open Add Symptoms → GI Stability";

    // Sleep × Glucose
    const sgi = computeSleepGlucoseInsight({
      sleepScore: sLatest ? sLatest.score : null,
      energyScore: sLatest ? sLatest.energy : null,
      glucose: glucoseVal,
      giStatus: giComputed ? giComputed.status : null
    });

    // Kidney load
    const kFlagsAll = U.load(K.KIDNEY_FLAGS, {});
    const flags = kFlagsAll[active] || { sodiumFlag: false, proteinFlag: false, hydrationFlag: "unknown" };
    const kidney = computeKidneyLoad({
      sodiumFlag: !!flags.sodiumFlag,
      proteinFlag: !!flags.proteinFlag,
      hydrationFlag: flags.hydrationFlag,
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

  // Make callable from UI if needed
  window.renderSnapshot = renderSnapshot;

  // ---------- GI Button inside Add Symptoms ----------
  function wireGIArea() {
    const { active } = initPeople();

    const symptomRoot =
      U.qs("#symptomChecker") ||
      U.qs("#symptoms") ||
      U.qs("[data-screen='symptomChecker']") ||
      U.qs("[data-screen='symptoms']");

    if (!symptomRoot) return;

    if (U.qs("#btnGIStability", symptomRoot)) return;

    const bar = document.createElement("div");
    bar.style.display = "flex";
    bar.style.gap = "10px";
    bar.style.alignItems = "center";
    bar.style.margin = "10px 0 14px 0";
    bar.style.flexWrap = "wrap";

    const btn = document.createElement("button");
    btn.id = "btnGIStability";
    btn.textContent = "GI Stability";
    btn.style.padding = "10px 12px";
    btn.style.borderRadius = "12px";
    btn.style.border = "1px solid rgba(0,0,0,0.15)";
    btn.style.background = "#fff";
    btn.style.fontWeight = "700";

    const note = document.createElement("div");
    note.style.fontSize = "12px";
    note.style.opacity = "0.75";
    note.textContent = "Log stool type + bloating/urgency/straining (simple).";

    bar.appendChild(btn);
    bar.appendChild(note);
    symptomRoot.prepend(bar);

    btn.addEventListener("click", () => {
      const menu = GI_OPTIONS.map((o, i) => `${i + 1}. ${o.label} (${o.desc})`).join("\n");
      const pick = prompt(`GI Status — pick one:\n\n${menu}\n\nEnter number (1-${GI_OPTIONS.length}):`);
      const idx = Number(pick);

      if (!Number.isFinite(idx) || idx < 1 || idx > GI_OPTIONS.length) return;

      const typeId = GI_OPTIONS[idx - 1].id;
      const noBloating = (prompt("No bloating today? (y/n)") || "").toLowerCase().startsWith("y");
      const noUrgency = (prompt("No urgency today? (y/n)") || "").toLowerCase().startsWith("y");
      const straining = (prompt("Straining? (y/n)") || "").toLowerCase().startsWith("y");
      const bmCountRaw = prompt("How many bowel movements today? (Enter 1 if unsure)");
      const bmCount = Number(bmCountRaw) || 1;

      const entry = { typeId, noBloating, noUrgency, straining, bmCount, ts: U.nowISO() };

      const giAll = U.load(K.GI_LATEST, {});
      giAll[active] = entry;
      U.save(K.GI_LATEST, giAll);

      const computed = computeGIScore(entry);
      alert(computed ? `Saved. GI is ${computed.status} (${computed.score}/100).` : "Saved GI entry.");

      // Refresh snapshot so card updates
      renderSnapshot();
    });
  }

  // ---------- Optional: Quick add buttons if they exist ----------
  function wireQuickAdd() {
    const { active } = initPeople();

    const btnAddReading =
      U.qs("#addReading") ||
      U.qs("button[data-action='add-reading']") ||
      U.qsa("button").find(b => (b.textContent || "").trim().toLowerCase() === "add reading");

    if (btnAddReading) {
      btnAddReading.addEventListener("click", () => {
        const val = prompt("Enter glucose (mg/dL):");
        const g = U.num(val);
        if (g === null) return;

        let store = getStore();
        store = ensurePerson(store, active);
        store[active].glucose.push({ value: g, ts: U.nowISO(), source: "manual" });
        setStore(store);

        alert("Glucose saved.");
        renderSnapshot();
      });
    }
  }

  // ---------- Init ----------
  window.addEventListener("DOMContentLoaded", () => {
    initPeople();
    setupTabClicks();

    // Default screen: snapshot if present
    if (U.qs("#snapshot")) showScreen("snapshot");
    else renderSnapshot();

    // Wire GI when symptom screen is opened, but also attempt once on load
    wireGIArea();

    wireQuickAdd();
  });

})();
