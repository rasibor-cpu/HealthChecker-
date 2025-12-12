/* ============================================================
   HealthChecker+ • Iteration 21
   Master Controller (app.js) – matches .tab-btn / .screen UI
============================================================ */

window.addEventListener("DOMContentLoaded", () => {
    /* ---------------- TAB HANDLING ---------------- */
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
