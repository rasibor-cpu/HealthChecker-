/* HC325-R7D — bounded JSON response contract for authenticated /mobile API fetches.
 * Never feed HTML to JSON.parse, never expose HTML bodies or Authorization tokens,
 * and strip query strings from diagnostic URLs.
 */
(function (global) {
  "use strict";

  function isJsonContentType(value) {
    const base = String(value || "").split(";")[0].trim().toLowerCase();
    return base === "application/json";
  }

  function safeApiPath(path) {
    const raw = String(path || "");
    try {
      if (/^[a-zA-Z][a-zA-Z0-9+.-]*:/.test(raw)) {
        return new URL(raw).pathname || "/";
      }
    } catch (_) {}
    return raw.split("#")[0].split("?")[0] || "/";
  }

  function safeFinalUrl(response) {
    try {
      const raw = String((response && response.url) || "");
      if (!raw) return "unknown";
      if (/^[a-zA-Z][a-zA-Z0-9+.-]*:/.test(raw)) {
        const parsed = new URL(raw);
        return parsed.origin + parsed.pathname;
      }
      return raw.split("#")[0].split("?")[0] || "unknown";
    } catch (_) {
      return "unknown";
    }
  }

  function jsonContractError(code, path, response) {
    const status = response && response.status != null ? String(response.status) : "unknown";
    const headerGet = response && response.headers && response.headers.get;
    const contentType = headerGet ? String(response.headers.get("content-type") || "") : "";
    return new Error(
      code +
        " path=" + safeApiPath(path) +
        " status=" + status +
        " content_type=" + contentType +
        " final_url=" + safeFinalUrl(response)
    );
  }

  function parseJsonResponse(response, path) {
    const redirected = !!(response && response.redirected);
    const headerGet = response && response.headers && response.headers.get;
    const contentType = headerGet ? String(response.headers.get("content-type") || "") : "";
    if (redirected && !isJsonContentType(contentType)) {
      return Promise.reject(jsonContractError("API_RESPONSE_NOT_JSON", path, response));
    }
    if (!isJsonContentType(contentType)) {
      return Promise.reject(jsonContractError("API_RESPONSE_NOT_JSON", path, response));
    }
    return Promise.resolve()
      .then(function () {
        return response.text();
      })
      .then(function (text) {
        try {
          return JSON.parse(text);
        } catch (_err) {
          throw jsonContractError("JSON_PARSE_FAILED", path, response);
        }
      });
  }

  global.HCMobileJsonContract = {
    isJsonContentType: isJsonContentType,
    safeApiPath: safeApiPath,
    safeFinalUrl: safeFinalUrl,
    jsonContractError: jsonContractError,
    parseJsonResponse: parseJsonResponse,
  };
})(typeof window !== "undefined" ? window : globalThis);
