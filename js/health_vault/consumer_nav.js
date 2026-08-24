/* HC325-R4 — single in-app consumer back stack (mobile + web).
 * Does not use URL fragments (Android origin policy rejects them) and does not
 * touch authentication/session storage.
 */
(function (global) {
  "use strict";

  var DASHBOARD = "dashboard";
  var ALIASES = {
    dash: DASHBOARD,
    welcome: DASHBOARD,
    dashboard: DASHBOARD,
    health_records_screen: "records",
    records: "records",
    vault: "records",
    health_records: "records",
    consumer_trends_screen: "trends",
    trends: "trends",
    consumer_observations_screen: "observations",
    observations: "observations",
    consumer_timeline_screen: "timeline",
    timeline: "timeline",
    consumer_reports_screen: "reports",
    reports: "reports",
    consumer_settings_screen: "settings",
    settings: "settings",
    import: "import",
    mobile_import: "import",
  };
  var DESKTOP_SCREEN = {
    dashboard: "dash",
    records: "health_records_screen",
    trends: "consumer_trends_screen",
    observations: "consumer_observations_screen",
    timeline: "consumer_timeline_screen",
    reports: "consumer_reports_screen",
    settings: "consumer_settings_screen",
    import: "health_records_screen",
  };

  var stack = [];
  var current = DASHBOARD;
  var overlays = [];
  var applying = false;

  function canonicalize(route) {
    var key = String(route == null ? "" : route).trim();
    if (!key) return DASHBOARD;
    if (ALIASES[key]) return ALIASES[key];
    return key;
  }

  function isDashboard(route) {
    return canonicalize(route) === DASHBOARD;
  }

  function cloneOverlays(list) {
    return (list || []).map(function (item) {
      return { id: item.id, close: item.close };
    });
  }

  function snapshot() {
    return { route: current, overlays: cloneOverlays(overlays) };
  }

  function restoreSnapshot(entry) {
    current = entry && entry.route ? canonicalize(entry.route) : DASHBOARD;
    overlays = cloneOverlays(entry && entry.overlays);
  }

  function closeAllOverlays() {
    while (overlays.length) {
      var overlay = overlays.pop();
      try {
        if (overlay && typeof overlay.close === "function") overlay.close();
      } catch (_err) {}
    }
  }

  function note(route, options) {
    options = options || {};
    var dest = canonicalize(route);
    if (options.deepLink) {
      closeAllOverlays();
      stack = [];
      overlays = [];
      current = dest;
      updateBackUi();
      return dest;
    }
    if (isDashboard(dest)) {
      closeAllOverlays();
      stack = [];
      overlays = [];
      current = DASHBOARD;
      updateBackUi();
      return DASHBOARD;
    }
    if (dest === current) {
      updateBackUi();
      return dest;
    }
    closeAllOverlays();
    stack.push({ route: current, overlays: [] });
    current = dest;
    overlays = [];
    updateBackUi();
    return dest;
  }

  function apply(route, options) {
    applying = true;
    try {
      var dest = canonicalize(route);
      var adapter = global.HCConsumerNavAdapter;
      if (adapter && typeof adapter.activate === "function") {
        adapter.activate(dest, options || {});
        return;
      }
      defaultActivate(dest);
    } finally {
      applying = false;
    }
  }

  function defaultActivate(route) {
    var doc = global.document;
    if (!doc || !doc.querySelectorAll) return;
    var body = doc.body;
    var mobile = !!(body && body.classList && body.classList.contains("mobile-consumer"));
    if (mobile) {
      var panelId = "mobile_" + route;
      var panels = doc.querySelectorAll("[data-mobile-panel]");
      for (var i = 0; i < panels.length; i++) {
        panels[i].hidden = panels[i].id !== panelId;
      }
      var buttons = doc.querySelectorAll("[data-mobile-view]");
      for (var j = 0; j < buttons.length; j++) {
        buttons[j].setAttribute("aria-pressed", String(buttons[j].getAttribute("data-mobile-view") === route));
      }
      return;
    }
    var screenId = DESKTOP_SCREEN[route] || route;
    var tab = doc.querySelector('.tab[data="' + screenId + '"]') ||
      doc.querySelector('#tabs_navbar .tab[data="' + screenId + '"]');
    if (tab && typeof tab.click === "function") {
      tab.click();
      return;
    }
    if (global.HCConsumerSurfaces && typeof HCConsumerSurfaces.activateConsumerScreen === "function") {
      HCConsumerSurfaces.activateConsumerScreen(screenId);
    }
  }

  function back(options) {
    options = options || {};
    if (overlays.length) {
      var overlay = overlays.pop();
      try {
        if (overlay && typeof overlay.close === "function") overlay.close();
      } catch (_err) {}
      updateBackUi();
      return { handled: true, route: current, overlay: overlay && overlay.id };
    }
    if (stack.length) {
      restoreSnapshot(stack.pop());
      apply(current, { fromBack: true, fromNav: true });
      updateBackUi();
      return { handled: true, route: current };
    }
    if (!isDashboard(current)) {
      current = DASHBOARD;
      overlays = [];
      apply(DASHBOARD, { fromBack: true, fromNav: true, fallback: true });
      updateBackUi();
      return { handled: true, route: DASHBOARD, fallback: true };
    }
    updateBackUi();
    return { handled: false, route: DASHBOARD };
  }

  function handleSystemBack() {
    return back().handled === true;
  }

  function pushOverlay(id, closeFn) {
    var key = String(id || "overlay");
    overlays = overlays.filter(function (item) { return item.id !== key; });
    overlays.push({ id: key, close: closeFn });
    updateBackUi();
  }

  function dismissOverlay(id) {
    var key = String(id || "");
    overlays = overlays.filter(function (item) { return item.id !== key; });
    updateBackUi();
  }

  function peekDeepLink(search) {
    try {
      var query = search != null ? search : ((global.location && location.search) || "");
      var q = String(query || "");
      if (q.charAt(0) === "?") q = q.slice(1);
      var raw = "";
      var decode = function (value) {
        var text = String(value || "").replace(/\+/g, " ");
        try {
          if (typeof decodeURIComponent === "function") return decodeURIComponent(text);
        } catch (_err) {}
        return text;
      };
      if (typeof URLSearchParams === "function") {
        var params = new URLSearchParams(q);
        raw = params.get("view") || params.get("screen") || "";
      } else {
        var parts = q.split("&");
        var map = {};
        for (var i = 0; i < parts.length; i++) {
          var pair = parts[i].split("=");
          map[decode(pair[0] || "")] = decode(pair[1] || "");
        }
        raw = map.view || map.screen || "";
      }
      if (!raw) return null;
      var dest = canonicalize(raw);
      return dest === DASHBOARD ? null : dest;
    } catch (_err) {
      return null;
    }
  }

  function reset() {
    closeAllOverlays();
    stack = [];
    overlays = [];
    current = DASHBOARD;
    updateBackUi();
  }

  function updateBackUi() {
    var doc = global.document;
    if (!doc || !doc.querySelectorAll) return;
    var show = !isDashboard(current) || overlays.length > 0;
    var nodes = doc.querySelectorAll("[data-hc-back]");
    for (var i = 0; i < nodes.length; i++) {
      var node = nodes[i];
      if (node.hasAttribute("data-hc-back-overlay")) continue;
      node.hidden = !show;
      node.setAttribute("aria-hidden", show ? "false" : "true");
    }
  }

  function bindDelegatedBack() {
    var doc = global.document;
    if (!doc || !doc.addEventListener || bindDelegatedBack._bound) return;
    bindDelegatedBack._bound = true;
    doc.addEventListener("click", function (event) {
      var target = event.target;
      var btn = target && target.closest ? target.closest("[data-hc-back]") : null;
      if (!btn) return;
      event.preventDefault();
      back();
    });
  }

  bindDelegatedBack();

  global.HCConsumerNav = {
    DASHBOARD: DASHBOARD,
    canonicalize: canonicalize,
    isDashboard: isDashboard,
    note: note,
    back: back,
    handleSystemBack: handleSystemBack,
    pushOverlay: pushOverlay,
    dismissOverlay: dismissOverlay,
    peekDeepLink: peekDeepLink,
    reset: reset,
    desktopScreenId: function (route) { return DESKTOP_SCREEN[canonicalize(route)] || route; },
    currentRoute: function () { return current; },
    stackRoutes: function () {
      return stack.map(function (entry) { return entry.route; });
    },
    overlayIds: function () {
      return overlays.map(function (item) { return item.id; });
    },
    isApplying: function () { return applying; },
    canLeaveApp: function () {
      return isDashboard(current) && stack.length === 0 && overlays.length === 0;
    },
  };
})(typeof window !== "undefined" ? window : globalThis);
