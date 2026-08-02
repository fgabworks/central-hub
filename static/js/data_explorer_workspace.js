/** Unified Data Explorer workspace navigation. */
(function () {
  "use strict";

  function all(selector, root) {
    return Array.prototype.slice.call((root || document).querySelectorAll(selector));
  }

  function escapeHtml(value) {
    return String(value == null ? "" : value)
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;");
  }

  function init() {
    var browseWorkspace = document.getElementById("dex-browse-workspace");
    var exportWorkspace = document.getElementById("dex-export-workspace");
    if (!browseWorkspace || !exportWorkspace) return;

    var allowed = ["browse", "schema", "relationships", "lineage", "export", "jobs", "history"];
    var initial = (browseWorkspace.getAttribute("data-initial-tab") || "browse").toLowerCase();
    if (allowed.indexOf(initial) < 0) initial = "browse";

    function selectInner(selector, value) {
      var button = document.querySelector(selector + "[data-tab='" + value + "']");
      if (button) button.click();
    }

    function renderAudit() {
      var target = document.getElementById("dex-audit-history");
      var dexRoot = document.getElementById("dex-root");
      if (!target || !dexRoot) return;
      fetch(dexRoot.getAttribute("data-audit-url"))
        .then(function (response) { return response.json(); })
        .then(function (payload) {
          var rows = payload.audit || [];
          target.innerHTML = rows.length ? rows.map(function (row) {
            return "<div class='lex-list-row'><strong>" + escapeHtml(row.event) +
              "</strong><span>" + escapeHtml(row.environment) + " · " +
              escapeHtml(row.object_ref || "") + " · " + escapeHtml(row.created_at) +
              "</span></div>";
          }).join("") : "<p class='muted'>No browse activity yet.</p>";
        })
        .catch(function () {
          target.innerHTML = "<p class='muted'>Audit history is unavailable.</p>";
        });
    }

    function activate(name, updateUrl) {
      var usesBrowse = ["browse", "schema", "relationships", "lineage"].indexOf(name) >= 0;
      browseWorkspace.hidden = !usesBrowse;
      exportWorkspace.hidden = usesBrowse;
      all("[data-workspace-tab]").forEach(function (button) {
        var on = button.getAttribute("data-workspace-tab") === name;
        button.classList.toggle("is-active", on);
        button.setAttribute("aria-selected", on ? "true" : "false");
      });

      if (usesBrowse) {
        selectInner("#dex-root .dex-tab", {
          browse: "data",
          schema: "columns",
          relationships: "relationships",
          lineage: "lineage",
        }[name]);
      } else {
        selectInner("#lex-root .lex-tab", { export: "new", jobs: "jobs", history: "history" }[name]);
        if (name === "history") renderAudit();
      }

      if (updateUrl && window.history && window.history.replaceState) {
        var url = new URL(window.location.href);
        if (name === "browse") url.searchParams.delete("tab");
        else url.searchParams.set("tab", name);
        window.history.replaceState({}, "", url.toString());
      }
    }

    all("[data-workspace-tab]").forEach(function (button) {
      button.addEventListener("click", function () {
        activate(button.getAttribute("data-workspace-tab"), true);
      });
    });
    activate(initial, false);
  }

  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", init);
  else init();
})();
