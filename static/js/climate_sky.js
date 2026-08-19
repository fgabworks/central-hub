/**
 * CLIMATE atmosphere — pause CSS motion when the tab is hidden,
 * the window is unfocused, or the user prefers reduced motion.
 * Transforms/opacity only. No WebGL or bitmap drawing.
 */
(function () {
  "use strict";

  var sky = document.querySelector(".climate-sky");
  if (!sky) return;

  var reduce = window.matchMedia
    ? window.matchMedia("(prefers-reduced-motion: reduce)")
    : { matches: false, addEventListener: function () {}, addListener: function () {} };

  function sync() {
    var pause = document.hidden || document.visibilityState === "hidden" || !!reduce.matches;
    document.body.classList.toggle("is-sky-paused", pause);
    sky.setAttribute("data-paused", pause ? "1" : "0");
  }

  sync();
  document.addEventListener("visibilitychange", sync);
  window.addEventListener("blur", function () {
    document.body.classList.add("is-sky-paused");
    sky.setAttribute("data-paused", "1");
  });
  window.addEventListener("focus", sync);
  window.addEventListener("pagehide", function () {
    document.body.classList.add("is-sky-paused");
  });
  window.addEventListener("pageshow", sync);
  if (reduce.addEventListener) reduce.addEventListener("change", sync);
  else if (reduce.addListener) reduce.addListener(sync);
})();
