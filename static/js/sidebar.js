/**
 * Central Hub sidebar — compact fixed rail, collapse memory, DHIS2 group expand.
 */
(function () {
  "use strict";

  var STORAGE_KEY = "centralhub.sidebar.collapsed";
  var shell = document.querySelector(".app-shell");
  var sidebar = document.getElementById("hub-sidebar");
  var collapseBtn = document.getElementById("sidebar-collapse-btn");

  function readCollapsed() {
    try {
      return localStorage.getItem(STORAGE_KEY) === "1";
    } catch (e) {
      return false;
    }
  }

  function writeCollapsed(on) {
    try {
      localStorage.setItem(STORAGE_KEY, on ? "1" : "0");
    } catch (e) {
      /* ignore quota / private mode */
    }
  }

  function applyCollapsed(on) {
    if (!shell) return;
    shell.classList.toggle("is-sidebar-collapsed", !!on);
    if (sidebar) sidebar.classList.toggle("is-collapsed", !!on);
    if (collapseBtn) {
      collapseBtn.setAttribute("aria-expanded", on ? "false" : "true");
      collapseBtn.title = on ? "Expand sidebar" : "Collapse sidebar";
      var label = collapseBtn.querySelector(".sidebar-collapse-label");
      if (label) label.textContent = on ? "Expand" : "Collapse";
      var ico = collapseBtn.querySelector(".sidebar-collapse-ico");
      if (ico) ico.textContent = on ? "»" : "«";
    }
  }

  function initCollapse() {
    applyCollapsed(readCollapsed());
    if (!collapseBtn) return;
    collapseBtn.addEventListener("click", function () {
      var next = !shell.classList.contains("is-sidebar-collapsed");
      applyCollapsed(next);
      writeCollapsed(next);
    });
  }

  function initNavGroups() {
    document.querySelectorAll("[data-nav-toggle]").forEach(function (btn) {
      btn.addEventListener("click", function () {
        var id = btn.getAttribute("data-nav-toggle");
        var group = document.querySelector('[data-nav-group="' + id + '"]');
        if (!group) return;
        var open = !group.classList.contains("is-expanded");
        group.classList.toggle("is-expanded", open);
        btn.setAttribute("aria-expanded", open ? "true" : "false");
        var children = group.querySelector(".nav-group-children");
        if (children) children.hidden = !open;
      });
    });
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", function () {
      initCollapse();
      initNavGroups();
    });
  } else {
    initCollapse();
    initNavGroups();
  }
})();
