/* HC-321-UAT12E authenticated Trends surface — JSON API only, never an HTML shell. */
(function (global) {
  "use strict";

  const TRENDS_API = "/api/health-vault/trends";

  function dashboard() {
    return global.HCConsumerDashboard;
  }

  function authHeaders() {
    return dashboard() && dashboard().getAuthorizationHeaders
      ? dashboard().getAuthorizationHeaders()
      : {};
  }

  function parseJsonResponse(response) {
    const ct = String((response && response.headers && response.headers.get("content-type")) || "").toLowerCase();
    if (ct.indexOf("text/html") >= 0) {
      return Promise.reject(new Error("Trends returned an HTML page instead of JSON. Sign in and retry."));
    }
    return response.text().then(function (text) {
      const trimmed = String(text || "").trim();
      if (!trimmed || trimmed.charAt(0) === "<") {
        throw new Error("Trends returned an HTML page instead of JSON. Sign in and retry.");
      }
      try {
        return JSON.parse(trimmed);
      } catch (_err) {
        throw new Error("Trends returned an HTML page instead of JSON. Sign in and retry.");
      }
    });
  }

  function queryString(options) {
    const params = new URLSearchParams();
    const metric = options && options.metric;
    const list = ((options && options.metrics) || []).filter(Boolean);
    if (metric) params.set("metric", String(metric));
    if (list.length) params.set("metrics", list.join(","));
    const qs = params.toString();
    return qs ? "?" + qs : "";
  }

  async function loadFiltered(options) {
    const response = await fetch(TRENDS_API + queryString(options), {
      headers: Object.assign({ Accept: "application/json" }, authHeaders()),
      cache: "no-store",
    });
    if (response.status === 401 || response.status === 403) {
      if (dashboard()) dashboard().handleLogout();
      throw new Error("Your session expired. Please sign in again.");
    }
    const body = await parseJsonResponse(response);
    if (!response.ok) throw new Error((body && body.error) || "HealthChecker could not load trends.");
    return body;
  }

  global.HCConsumerTrends = {
    TRENDS_API: TRENDS_API,
    loadFiltered: loadFiltered,
    parseJsonResponse: parseJsonResponse,
  };
})(typeof window !== "undefined" ? window : globalThis);
