/**
 * HC-201C — Lightweight event bus (browser).
 */
(function (global) {
  "use strict";

  const handlers = Object.create(null);
  const history = [];

  function subscribe(name, fn) {
    if (!handlers[name]) handlers[name] = [];
    handlers[name].push(fn);
  }

  function publish(name, payload) {
    const evt = { name, payload: payload || {}, at: new Date().toISOString() };
    history.push(evt);
    (handlers[name] || []).forEach((fn) => {
      try {
        fn(evt);
      } catch (_) {}
    });
    (handlers["*"] || []).forEach((fn) => {
      try {
        fn(evt);
      } catch (_) {}
    });
    return evt;
  }

  global.HCEventBus = {
    subscribe,
    publish,
    history,
    EVENTS: {
      DocumentImported: "DocumentImported",
      OCRCompleted: "OCRCompleted",
      MeasurementsExtracted: "MeasurementsExtracted",
      ValidationCompleted: "ValidationCompleted",
      DocumentStored: "DocumentStored",
      MeasurementStored: "MeasurementStored",
      TimelineUpdated: "TimelineUpdated",
      TrendUpdated: "TrendUpdated",
      DoctorReportUpdated: "DoctorReportUpdated",
      DuplicateDetected: "DuplicateDetected",
      ParserFailed: "ParserFailed",
      ImportCompleted: "ImportCompleted",
      // HC-301 Guardian / Alert / CGM
      AlertCreated: "AlertCreated",
      AlertUpdated: "AlertUpdated",
      AlertEscalated: "AlertEscalated",
      AlertAcknowledged: "AlertAcknowledged",
      AlertResolved: "AlertResolved",
      AlertSnoozed: "AlertSnoozed",
      GuardianEvaluated: "GuardianEvaluated",
      GuardianEvaluationFailed: "GuardianEvaluationFailed",
      CGMSensorRegistered: "CGMSensorRegistered",
      CGMSensorActivated: "CGMSensorActivated",
      CGMSensorFailed: "CGMSensorFailed",
      CGMInventoryUpdated: "CGMInventoryUpdated",
      DataGapDetected: "DataGapDetected",
    },
  };
})(typeof window !== "undefined" ? window : globalThis);
