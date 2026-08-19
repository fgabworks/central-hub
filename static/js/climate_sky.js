/**
 * CLIMATE weather-galaxy sky — pause CSS motion when the tab is hidden
 * or the user prefers reduced motion. CSS transforms only.
 */
(function () {
  "use strict";

  var sky = document.querySelector(".climate-sky");
  if (!sky) return;

  var reduce = window.matchMedia
    ? window.matchMedia("(prefers-reduced-motion: reduce)")
    : { matches: false, addEventListener: function () {}, addListener: function () {} };

  function sync() {
    var pause = document.hidden || !!reduce.matches;
    document.body.classList.toggle("is-sky-paused", pause);
    sky.setAttribute("data-paused", pause ? "1" : "0");
  }

  sync();
  document.addEventListener("visibilitychange", sync);
  window.addEventListener("pagehide", function () {
    document.body.classList.add("is-sky-paused");
  });
  window.addEventListener("pageshow", sync);
  if (reduce.addEventListener) reduce.addEventListener("change", sync);
  else if (reduce.addListener) reduce.addListener(sync);
})();
