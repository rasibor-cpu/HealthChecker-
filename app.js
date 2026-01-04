/* ============================================================
   HealthChecker+ • Iteration 21
   Master Controller (app.js) – matches .tab-btn / .screen UI
============================================================ */

window.addEventListener("DOMContentLoaded", () => {
 /* =========================================================
     GI / SLEEP / KIDNEY MODULES — v1
     - GI Stability (simple stool types + score)
     - Sleep × Glucose coupling insight
     - Kidney Load score (diet/BP/glucose aware)
     ========================================================= */

  const HCX = {
    nowISO() {
      try { return new Date().toISOString(); } catch(e) { return ""; }
    },
    // ---- storage helpers ----
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
    },

    // ---- GI mapping (user-friendly -> Bristol-ish category) ----
    GI_OPTIONS: [
      { id: "pellets", label: "Very hard, pellet-like", desc: "Constipated", bristol: 1, baseScore: 45 },
      { id: "lumpy", label: "Hard sausage, lumpy", desc: "Mild constipation", bristol: 2, baseScore: 75 },
      { id: "cracked", label: "Normal sausage with cracks", desc: "Normal", bristol: 3, baseScore: 92 },
      { id: "smooth", label: "Smooth, soft sausage", desc: "Ideal", bristol: 4, baseScore: 98 },
      { id: "blobs", label: "Very soft blobs", desc: "Borderline loose", bristol: 5, baseScore: 80 },
      { id: "mushy", label: "Mushy, ragged", desc: "Diarrhea", bristol: 6, baseScore: 55 },
      { id: "watery", label: "Watery", desc: "Severe diarrhea", bristol: 7, baseScore: 35 }
    ],

    computeGIScore(entry) {
      if (!entry || !entry.typeId) return null;
      const opt = this.GI_OPTIONS.find(o => o.id === entry.typeId);
      if (!opt) return null;

      let score = opt.baseScore;

      // Modifiers
      if (entry.noBloating === true) score += 5;
      if (entry.noUrgency === true) score += 5;
      if (entry.straining === true) score -= 10;
      if (typeof entry.bmCount === "number" && entry.bmCount > 1) score -= 10;

      // clamp
      score = Math.max(0, Math.min(100, score));

      let status = "Unstable";
      if (score >= 86) status = "Stable";
      else if (score >= 70) status = "Borderline";

      return { score, status, bristol: opt.bristol, label: opt.label };
    },

    // ---- Sleep × Glucose insight (simple rules, transparent) ----
    computeSleepGlucoseInsight({ sleepScore, energyScore, latestGlucose, giStatus }) {
      const g = Number(latestGlucose);
      const s = Number(sleepScore);
      const e = Number(energyScore);

      // Fallback strings
      let headline = "Sleep–Metabolic Coupling";
      let insight = "Add more sleep/glucose data to unlock deeper insights.";
      let flag = "Neutral";

      const hasSleep = !Number.isNaN(s) || !Number.isNaN(e);
      const hasGlucose = !Number.isNaN(g);

      if (hasSleep && hasGlucose) {
        const sleepLow = (!Number.isNaN(s) && s < 55) || (!Number.isNaN(e) && e < 55);
        const glucoseHigh = g >= 160;
        const glucoseMid = g >= 125 && g < 160;

        if (sleepLow && glucoseHigh) {
          insight = "Pattern suggests stress/poor sleep may be amplifying glucose. Focus on earlier dinner + sleep consolidation.";
          flag = "High likelihood";
        } else if (sleepLow && glucoseMid) {
          insight = "Sleep looks suboptimal and glucose is moderately elevated. Stabilize sleep to reduce overnight variability.";
          flag = "Moderate likelihood";
        } else if (!sleepLow && glucoseHigh) {
          insight = "Sleep appears reasonable but glucose is high. Review dinner composition, timing, and post-meal activity.";
          flag = "Action needed";
        } else if (!sleepLow && glucoseMid) {
          insight = "Sleep is adequate and glucose is in mid-range. Incremental gains will come from meal timing and GI stability.";
          flag = "On track";
        } else {
          insight = "Sleep and glucose look aligned. Maintain the routine; avoid late-night refined carbs.";
          flag = "Good";
        }

        if (giStatus === "Unstable" || giStatus === "Borderline") {
          insight += " GI stability may be contributing to volatility.";
        }
      }

      return { headline, insight, flag };
    },

    // ---- Kidney Load score (0–100), conservative heuristic ----
    computeKidneyLoad({ sodiumFlag, proteinFlag, hydrationFlag, bpSys, bpDia, glucose }) {
      // Higher score = higher load (worse)
      let load = 25;

      const sys = Number(bpSys);
      const dia = Number(bpDia);
      const g = Number(glucose);

      if (sodiumFlag) load += 15;
      if (proteinFlag) load += 15;
      if (hydrationFlag === "low") load += 15;
      if (hydrationFlag === "good") load -= 5;

      if (!Number.isNaN(sys) && sys >= 140) load += 15;
      if (!Number.isNaN(dia) && dia >= 90) load += 10;

      if (!Number.isNaN(g) && g >= 180) load += 15;
      else if (!Number.isNaN(g) && g >= 140) load += 8;

      load = Math.max(0, Math.min(100, load));

      let label = "Low";
      if (load >= 60) label = "Elevated";
      else if (load >= 40) label = "Moderate";

      return { load, label };
    },

    // ---- UI card builder (safe, minimal) ----
    cardHTML(title, main, sub) {
      const safeTitle = title || "";
      const safeMain = main || "";
      const safeSub = sub || "";
      return `
        <div class="card">
          <div class="card-title">${safeTitle}</div>
          <div class="card-main">${safeMain}</div>
          <div class="card-sub">${safeSub}</div>
        </div>
      `;
    }
  };   /* ---------------- TAB HANDLING ---------------- */
    const tabButtons = document.querySelectorAll(".tab-btn");
    const screens = document.querySelectorAll(".screen");

    tabButtons.forEach((btn) => {
        btn.addEventListener("click", () => {
            // set active button
            tabButtons.forEach(b => b.classList.remove("active"));
            btn.classList.add("active");

            // show target screen
            const target = btn.getAttribute("data-target");
            screens.forEach(s => {
                if (s.id === target) {
                    s.classList.add("active");
                } else {
                    s.classList.remove("active");
                }
            });

            // screen-specific hooks
            if (target === "screen-dashboard") {
                refreshDashboard();
            } else if (target === "screen-reports") {
                renderReports();
            } else if (target === "screen-profile") {
                loadProfileIntoForm();
            } else if (target === "screen-check") {
                buildSymptomAccordion();   // ensure accordion is present
            } else if (target === "screen-urine") {
                Helpers.renderUrineHelper("urine-area");
            } else if (target === "screen-stool") {
                Helpers.renderStoolHelper("stool-area");
            } else if (target === "screen-heart") {
                Helpers.renderHeartHelper("heart-area");
            }
        });
    });

    // initial load
    loadProfileIntoForm();
    buildSymptomAccordion();
    Helpers.renderUrineHelper("urine-area");
    Helpers.renderStoolHelper("stool-area");
    Helpers.renderHeartHelper("heart-area");
    refreshDashboard();

    // wire run button for multi-symptom check if present
    const runBtn = document.getElementById("run-multisymptom");
    if (runBtn) {
        runBtn.addEventListener("click", runMultiSymptomCheck);
    }

    // wire report exports (placeholders for now)
    const btnCsv = document.getElementById("btn-export-csv");
    const btnPdf = document.getElementById("btn-export-pdf");
    if (btnCsv) btnCsv.addEventListener("click", () =>
        alert("CSV export will be added in Iteration 22.")
    );
    if (btnPdf) btnPdf.addEventListener("click", () =>
        alert("PDF export will be added in Iteration 22.")
    );
});


/* ================= PROFILE ================= */

function loadProfileIntoForm() {
    const data = HCStore.getProfile();
    if (!data) return;

    const nameEl = document.getElementById("prof-name");
    const ageEl = document.getElementById("prof-age");
    const sexEl = document.getElementById("prof-sex");

    if (nameEl) nameEl.value = data.name || "";
    if (ageEl) ageEl.value = data.age || "";
    if (sexEl && data.sex) sexEl.value = data.sex;
}

const saveProfileBtn = document.getElementById("btn-save-profile");
if (saveProfileBtn) {
    saveProfileBtn.addEventListener("click", () => {
        const profile = {
            name: (document.getElementById("prof-name")?.value || "").trim(),
            age: (document.getElementById("prof-age")?.value || "").trim(),
            sex: document.getElementById("prof-sex")?.value || "male"
        };
        HCStore.saveProfile(profile);
        alert("Profile saved.");
    });
}


/* ================= MULTI-SYMPTOM CHECKER ================= */

function buildSymptomAccordion() {
    const container = document.getElementById("symptom-accordion");
    if (!container) return;

    // If already built, don't rebuild
    if (container.dataset.built === "yes") return;

    const masterList = Symptoms.getMasterList();
    container.innerHTML = "";

    const COUNT = 10;
    for (let i = 1; i <= COUNT; i++) {
        const item = document.createElement("div");
        item.className = "accordion-item";

        item.innerHTML = `
            <button class="accordion-header">Symptom ${i}</button>
            <div class="accordion-content">
                <div class="form-block">
                    <label>Symptom</label>
                    <select class="symptom-select">
                        <option value="">-- choose --</option>
                        ${masterList.map(s => `<option value="${s}">${s}</option>`).join("")}
                    </select>
                </div>
                <div class="form-block">
                    <label>Description (optional)</label>
                    <textarea class="symptom-desc" rows="2"></textarea>
                </div>
            </div>
        `;
        container.appendChild(item);
    }

    // accordion behaviour
    container.querySelectorAll(".accordion-header").forEach(header => {
        header.addEventListener("click", () => {
            header.classList.toggle("open");
            const panel = header.nextElementSibling;
            if (!panel) return;
            panel.style.display = panel.style.display === "block" ? "none" : "block";
        });
    });

    container.dataset.built = "yes";
}

function runMultiSymptomCheck() {
    const selects = document.querySelectorAll(".symptom-select");
    const descs = document.querySelectorAll(".symptom-desc");

    const chosen = [];
    selects.forEach((sel, idx) => {
        if (sel.value) {
            chosen.push({
                name: sel.value,
                description: descs[idx].value.trim()
            });
        }
    });

    if (!chosen.length) {
        alert("Please select at least one symptom.");
        return;
    }

    const result = Symptoms.evaluate(chosen);
    const output = document.getElementById("symptom-output");
    if (output) output.innerHTML = result.html;

    HCStore.addSymptomLog({
        type: "symptoms",
        symptoms: chosen,
        summary: result.summary,
        urgent: result.urgent,
        timestamp: Date.now()
    });

    if (result.urgent) {
        const overlay = document.getElementById("urgent-overlay");
        if (overlay) {
            overlay.style.display = "flex";
            setTimeout(() => overlay.style.display = "none", 6000);
        }
    }

    refreshDashboard();
}


/* ================= DASHBOARD ================= */

function refreshDashboard() {
    const logs = HCStore.getLogs();
    Dashboard.renderDashboardCards(logs);
    Dashboard.renderCharts(logs);
}


/* ================= REPORTS ================= */

function renderReports() {
    const container = document.getElementById("report-list");
    if (!container) return;

    const filter = document.getElementById("report-filter")?.value || "all";

    const logs = HCStore.getLogs();
    const symLogs = HCStore.getSymptomLogs();

    let combined = [];
    if (filter === "all") {
        combined = [...logs, ...symLogs];
    } else if (filter === "bp") {
        combined = logs.filter(l => l.type === "bp");
    } else if (filter === "glucose") {
        combined = logs.filter(l => l.type === "glucose");
    } else if (filter === "symptoms") {
        combined = symLogs;
    }

    combined.sort((a, b) => b.timestamp - a.timestamp);

    if (!combined.length) {
        container.innerHTML = "<p>No logs recorded yet.</p>";
        return;
    }

    container.innerHTML = combined.map(log => `
        <div class="log-entry">
            <div><b>${new Date(log.timestamp).toLocaleString()}</b></div>
            <div>${(log.type || "SYMPTOMS").toUpperCase()}</div>
            <div>${log.summary || log.assessment || ""}</div>
        </div>
    `).join("");
}

