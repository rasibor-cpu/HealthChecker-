/**
 * HC-301/302 — Health Guardian + Continuous Monitoring service worker (PWA foundation).
 *
 * Capability notes (honest limits):
 * - Caches the offline app shell only. Never caches vault_storage clinical blobs or API secrets.
 * - Periodic Background Sync / push are best-effort; many mobile browsers suspend PWAs.
 * - showNotification runs only when Notification.permission === 'granted'.
 * - Never sends caregiver SMS/email/push off-device in HC-302.
 * - Manufacturer CGM/device alarms must remain the primary safety net.
 * - Continuous unrestricted background execution is NOT guaranteed on iOS/Android browsers.
 * - MONITORING_SYNC nudges open clients only; SW does not invent LIVE device readings.
 */

const CACHE_NAME = "hc-guardian-v1";
const CACHE_REVISION = "hc321uat12e";
const ACTIVE_CACHE_NAME = `${CACHE_NAME}-${CACHE_REVISION}`;

/** HC-325 R3 / HC-321-C1: network-first for dashboard scripts (Ctrl+F5 does not bypass an active SW). */
const NETWORK_FIRST_JS = /\/js\/health_vault\/(executive_dashboard|health_snapshot|dashboard|health_guardian|consumer_surfaces|trends)\.js(?:\?|$)/;

/** App-shell URLs safe to precache (relative to SW scope). */
const APP_SHELL = [
  "./",
  "./index.html",
  "./style.css",
  "./css/style.css",
  "./manifest.webmanifest",
  "./js/measurement_model.js",
  "./js/app.js",
  "./js/health_vault/medical_document.js",
  "./js/health_vault/parser_registry.js",
  "./js/health_vault/parsers/builtin_parsers.js",
  "./js/health_vault/event_bus.js",
  "./js/health_vault/clinical_rules.js",
  "./js/health_vault/vault_store.js",
  "./js/health_vault/trend_engine.js",
  "./js/health_vault/timeline.js",
  "./js/health_vault/trends.js",
  "./js/health_vault/import_engine.js",
  "./js/health_vault/batch_import.js",
  "./js/health_vault/import_confirm.js",
  "./js/health_vault/ai_health_bridge.js",
  "./js/health_vault/doctor_visit.js",
  "./js/health_vault/executive_dashboard.js",
  "./js/health_vault/health_snapshot.js",
  "./js/health_vault/dashboard.js",
  "./js/health_vault/records.js",
  "./js/health_vault/consumer_surfaces.js",
  "./js/health_vault/alert_engine.js",
  "./js/health_vault/baseline_engine.js",
  "./js/health_vault/cgm_continuity.js",
  "./js/health_vault/health_guardian.js",
  "./js/health_vault/continuous_monitoring.js",
  "./js/health_vault/ui.js",
];

function isForbiddenCacheUrl(url) {
  try {
    const u = new URL(url);
    const path = u.pathname || "";
    // Never cache clinical vault blobs or anything that looks like secrets
    if (path.indexOf("vault_storage") >= 0) return true;
    if (path.indexOf("/api/") >= 0) return true;
    if (path.indexOf("secret") >= 0 || path.indexOf("token") >= 0) return true;
    if (u.search && /(key|token|secret|password)=/i.test(u.search)) return true;
    return false;
  } catch (_) {
    return true;
  }
}

self.addEventListener("install", (event) => {
  event.waitUntil(
    caches
      .open(ACTIVE_CACHE_NAME)
      .then((cache) =>
        Promise.all(
          APP_SHELL.map((url) =>
            cache.add(url).catch(() => {
              /* optional assets may 404; ignore */
            })
          )
        )
      )
      .then(() => self.skipWaiting())
  );
});

self.addEventListener("activate", (event) => {
  event.waitUntil(
    caches
      .keys()
      .then((keys) =>
        Promise.all(keys.filter((k) => k !== ACTIVE_CACHE_NAME).map((k) => caches.delete(k)))
      )
      .then(() => self.skipWaiting())
      .then(() => self.clients.claim())
      .then(() => self.clients.matchAll({ type: "window" }))
      .then((clientList) =>
        Promise.all(
          clientList.map((client) =>
            typeof client.navigate === "function" ? client.navigate(client.url).catch(() => {}) : Promise.resolve()
          )
        )
      )
  );
});

function networkFirstShell(req) {
  return fetch(req)
    .then((response) => {
      // Never re-cache HTML navigations — they must track deploy revisions.
      const isHtmlNav = req.mode === "navigate";
      if (
        response &&
        response.ok &&
        !isHtmlNav &&
        req.url.indexOf(self.location.origin) === 0
      ) {
        const clone = response.clone();
        caches.open(ACTIVE_CACHE_NAME).then((cache) => cache.put(req, clone));
      }
      return response;
    })
    .catch(() => {
      if (isNavigationRequest(req)) {
        return caches.match(req).then((cached) => cached || caches.match("./index.html"));
      }
      return caches.match(req).then((cached) => cached || Promise.reject(new TypeError("network_failed")));
    });
}

function isNavigationRequest(req) {
  if (req.mode === "navigate") return true;
  const accept = req.headers.get("accept") || "";
  return accept.indexOf("text/html") >= 0;
}

self.addEventListener("fetch", (event) => {
  const req = event.request;
  if (req.method !== "GET") return;
  if (isForbiddenCacheUrl(req.url)) return;

  // HC-325 R3: navigations + executive/dashboard/guardian JS are network-first so a
  // controlling SW cannot keep serving the pre-HC324 localStorage executive plane.
  if (isNavigationRequest(req) || NETWORK_FIRST_JS.test(req.url)) {
    event.respondWith(networkFirstShell(req));
    return;
  }

  event.respondWith(
    caches.match(req).then((cached) => {
      if (cached) return cached;
      return fetch(req)
        .then((response) => {
          // Cache successful same-origin shell responses only — never JSON clinical payloads
          const ct = (response && response.headers && response.headers.get("content-type")) || "";
          const isJson = ct.indexOf("application/json") >= 0;
          if (
            response &&
            response.ok &&
            req.url.indexOf(self.location.origin) === 0 &&
            !isForbiddenCacheUrl(req.url) &&
            !isJson
          ) {
            const clone = response.clone();
            caches.open(ACTIVE_CACHE_NAME).then((cache) => cache.put(req, clone));
          }
          return response;
        })
        .catch(() => {
          if (isNavigationRequest(req)) {
            return cached || caches.match("./index.html");
          }
          return cached || Promise.reject(new TypeError("network_failed"));
        });
    })
  );
});

/**
 * Message protocol:
 * - { type: 'GUARDIAN_EVAL' } → ask open clients to run HCHealthGuardian.refresh
 * - { type: 'MONITORING_SYNC' } → ask open clients to refresh HCContinuousMonitoring status
 * - { type: 'SKIP' } → no-op acknowledge (used to suppress noisy evals)
 */
self.addEventListener("message", (event) => {
  const data = event.data || {};
  if (data.type === "SKIP_WAITING") {
    self.skipWaiting();
    return;
  }
  if (data.type === "SKIP") {
    return;
  }
  if (data.type === "GUARDIAN_EVAL") {
    event.waitUntil(notifyClientsToEvaluate(data.reason || "sw_message"));
  }
  if (data.type === "MONITORING_SYNC") {
    event.waitUntil(notifyClientsMonitoringSync(data.reason || "sw_message"));
  }
});

async function notifyClientsToEvaluate(reason) {
  const all = await self.clients.matchAll({ type: "window", includeUncontrolled: true });
  all.forEach((client) => {
    client.postMessage({ type: "GUARDIAN_EVAL", reason: reason });
  });
  // Local notification only — never off-device
  await maybeNotify(
    "Health Guardian check",
    "Open HealthChecker+ to refresh observational Guardian status. Not a medical alarm."
  );
}

async function notifyClientsMonitoringSync(reason) {
  const all = await self.clients.matchAll({ type: "window", includeUncontrolled: true });
  all.forEach((client) => {
    client.postMessage({ type: "MONITORING_SYNC", reason: reason });
  });
  await maybeNotify(
    "Monitoring sync reminder",
    "Open HealthChecker+ to refresh connector status. Background sync is best-effort and not continuous."
  );
}

async function maybeNotify(title, body) {
  try {
    if (typeof Notification === "undefined") return;
    if (Notification.permission !== "granted") return;
    await self.registration.showNotification(title, {
      body: body,
      tag: "hc-guardian-eval",
      renotify: false,
    });
  } catch (_) {
    /* permission revoked or unsupported */
  }
}

// Periodic Background Sync when supported (Chrome/Android mostly). Often unavailable on iOS.
self.addEventListener("periodicsync", (event) => {
  if (event.tag === "hc-guardian-eval") {
    event.waitUntil(notifyClientsToEvaluate("periodic_sync"));
  }
  if (event.tag === "hc-monitoring-sync") {
    event.waitUntil(notifyClientsMonitoringSync("periodic_monitoring_sync"));
  }
});

self.addEventListener("sync", (event) => {
  if (event.tag === "hc-guardian-eval") {
    event.waitUntil(notifyClientsToEvaluate("background_sync"));
  }
  if (event.tag === "hc-monitoring-sync") {
    event.waitUntil(notifyClientsMonitoringSync("background_monitoring_sync"));
  }
});
