/**
 * Shared navigation helpers: deduped fetch, visibility-aware polling.
 */
(function (global) {
  "use strict";

  var inflight = new Map();

  function dedupeFetch(url, options) {
    var key = String(url) + "|" + ((options && options.method) || "GET");
    if (inflight.has(key)) return inflight.get(key);
    var req = fetch(url, options || {}).finally(function () {
      inflight.delete(key);
    });
    inflight.set(key, req);
    return req;
  }

  function whenVisible(callback) {
    if (document.visibilityState !== "hidden") {
      callback();
      return;
    }
    function onChange() {
      if (document.visibilityState === "visible") {
        document.removeEventListener("visibilitychange", onChange);
        callback();
      }
    }
    document.addEventListener("visibilitychange", onChange);
  }

  function pauseableInterval(fn, ms) {
    var timer = null;
    function start() {
      if (timer || document.visibilityState === "hidden") return;
      timer = setInterval(fn, ms);
    }
    function stop() {
      if (!timer) return;
      clearInterval(timer);
      timer = null;
    }
    document.addEventListener("visibilitychange", function () {
      if (document.visibilityState === "hidden") stop();
      else start();
    });
    start();
    return { start: start, stop: stop };
  }

  global.HubPerf = {
    dedupeFetch: dedupeFetch,
    whenVisible: whenVisible,
    pauseableInterval: pauseableInterval,
  };
})(window);
