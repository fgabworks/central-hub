/**
 * Data Explorer UI — three-panel read-only browser (no ad-hoc SQL execution).
 */
(function () {
  "use strict";

  function $(sel, root) { return (root || document).querySelector(sel); }
  function $all(sel, root) { return Array.prototype.slice.call((root || document).querySelectorAll(sel)); }

  function boot() {
    var el = document.getElementById("dex-bootstrap");
    try { return JSON.parse(el.textContent || "{}"); } catch (e) { return {}; }
  }

  function esc(s) {
    return String(s == null ? "" : s)
      .replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
  }

  async function readJson(response) {
    var text = await response.text();
    try {
      return JSON.parse(text || "{}");
    } catch (_error) {
      throw new Error(
        "Data Explorer received an invalid server response (HTTP " + response.status + ")."
      );
    }
  }

  function init() {
    var root = document.getElementById("dex-root");
    if (!root) return;
    var state = {
      boot: boot(),
      selected: null,
      detail: null,
      page: 1,
      sortColumn: null,
      sortDir: "asc",
      abort: null,
    };

    var envSel = $("#dex-env");
    envSel.value = state.boot.environment || "dev";
    updateConnStatus();
    renderTree(state.boot.tree);
    renderFavorites(state.boot.favorites || []);

    $all(".dex-tab", root).forEach(function (tab) {
      tab.addEventListener("click", function () {
        var name = tab.getAttribute("data-tab");
        $all(".dex-tab", root).forEach(function (t) {
          t.classList.toggle("is-active", t === tab);
        });
        $all(".dex-panel", root).forEach(function (p) {
          var on = p.getAttribute("data-panel") === name;
          p.hidden = !on;
          p.classList.toggle("is-active", on);
        });
      });
    });

    envSel.addEventListener("change", async function () {
      setError("");
      state.selected = null;
      try {
        var res = await fetch(root.getAttribute("data-tree-url") + "?environment=" + encodeURIComponent(envSel.value));
        var data = await readJson(res);
        if (!res.ok) throw new Error(data.error || "Failed to load tree");
        renderTree(data.tree);
        updateConnStatus();
        var favRes = await fetch(root.getAttribute("data-favorites-url") + "?environment=" + encodeURIComponent(envSel.value));
        var favData = await readJson(favRes);
        renderFavorites(favData.favorites || []);
      } catch (err) {
        setError(err.message || String(err));
        renderTree(null);
      }
    });

    $("#dex-refresh").addEventListener("click", async function () {
      setError("");
      try {
        var res = await fetch(root.getAttribute("data-refresh-url"), {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ environment: envSel.value }),
        });
        var data = await readJson(res);
        if (!res.ok) throw new Error(data.error || "Refresh failed");
        renderTree(data.tree);
      } catch (err) {
        setError(err.message || String(err));
      }
    });

    $("#dex-inventory-btn").addEventListener("click", async function () {
      try {
        var res = await fetch(root.getAttribute("data-inventory-url") + "?environment=" + encodeURIComponent(envSel.value));
        var data = await readJson(res);
        if (!res.ok) throw new Error(data.error || "Inventory failed");
        renderInventory(data.inventory);
        document.getElementById("dex-inventory-dialog").showModal();
      } catch (err) {
        setError(err.message || String(err));
      }
    });

    $("#dex-browse-btn").addEventListener("click", function () { loadGrid(1); });
    $("#dex-prev").addEventListener("click", function () { if (state.page > 1) loadGrid(state.page - 1); });
    $("#dex-next").addEventListener("click", function () { loadGrid(state.page + 1); });
    $("#dex-fav-btn").addEventListener("click", addFavorite);
    $("#dex-copy-query").addEventListener("click", function () {
      var q = $("#dex-query").textContent || "";
      if (navigator.clipboard) navigator.clipboard.writeText(q);
    });
    $("#dex-explain").addEventListener("click", runExplain);
    $("#dex-export-btn").addEventListener("click", runExport);
    $("#dex-search").addEventListener("keydown", function (e) {
      if (e.key === "Enter") loadGrid(1);
    });

    function setError(msg) {
      var el = $("#dex-error");
      if (!msg) { el.hidden = true; el.textContent = ""; return; }
      el.hidden = false; el.textContent = msg;
    }

    function updateConnStatus() {
      var c = (state.boot.connection) || {};
      var live = state.boot.live_configured ? "Live RO configured" : "Live RO not configured";
      var stage = state.boot.stage_configured ? "Stage RO configured" : "Stage RO not configured";
      $("#dex-conn-status").textContent = (c.configured ? ("Connected: " + (c.id || "")) : "Connection not configured") + " · " + live + " · " + stage;
    }

    function renderTree(tree) {
      var box = $("#dex-tree");
      if (!tree || !tree.schemas || !tree.schemas.length) {
        box.innerHTML = "<p class='muted dex-small'>No schemas discovered. Configure a read-only SQL connection or use Dev.</p>";
        return;
      }
      box.innerHTML = tree.schemas.map(function (sch) {
        return (
          "<details open><summary>" + esc(sch.name) + "</summary>" +
          section("Tables", sch.tables) +
          section("Views", sch.views) +
          section("Materialized Views", sch.materialized_views) +
          "</details>"
        );
      }).join("");
      $all("button[data-schema]", box).forEach(function (btn) {
        btn.addEventListener("click", function () {
          selectObject(btn.getAttribute("data-schema"), btn.getAttribute("data-name"));
        });
      });
    }

    function section(label, items) {
      items = items || [];
      if (!items.length) return "";
      return (
        "<div class='dex-small'><em>" + esc(label) + "</em></div>" +
        items.map(function (it) {
          return "<button type='button' data-schema='" + esc(it.schema) + "' data-name='" + esc(it.name) + "'>" +
            esc(it.name) +
            (it.estimated_rows != null ? " <span class='muted'>(" + it.estimated_rows + ")</span>" : "") +
            "</button>";
        }).join("")
      );
    }

    function renderFavorites(list) {
      var box = $("#dex-favorites");
      if (!list.length) { box.innerHTML = "<p class='muted dex-small'>None</p>"; return; }
      box.innerHTML = list.map(function (f) {
        return "<button type='button' data-schema='" + esc(f.schema) + "' data-name='" + esc(f.object_name) + "'>" +
          esc(f.schema + "." + f.object_name) + "</button>";
      }).join("");
      $all("button[data-schema]", box).forEach(function (btn) {
        btn.addEventListener("click", function () {
          selectObject(btn.getAttribute("data-schema"), btn.getAttribute("data-name"));
        });
      });
    }

    async function selectObject(schema, name) {
      setError("");
      state.selected = { schema: schema, name: name };
      state.page = 1;
      $("#dex-object-title").textContent = schema + "." + name;
      $("#dex-browse-btn").disabled = false;
      $("#dex-fav-btn").disabled = false;
      $("#dex-export-btn").disabled = false;
      try {
        var url = root.getAttribute("data-object-url") +
          "?environment=" + encodeURIComponent(envSel.value) +
          "&schema=" + encodeURIComponent(schema) +
          "&name=" + encodeURIComponent(name);
        var res = await fetch(url);
        var data = await readJson(res);
        if (!res.ok) throw new Error(data.error || "Failed to load object");
        state.detail = data;
        renderDetails(data);
        renderMetaTabs(data);
        await loadGrid(1);
      } catch (err) {
        setError(err.message || String(err));
      }
    }

    function renderDetails(data) {
      var obj = data.object || {};
      var cls = data.classification || {};
      var dl = $("#dex-detail-dl");
      $("#dex-detail-empty").hidden = true;
      dl.hidden = false;
      dl.innerHTML =
        "<dt>Type</dt><dd>" + esc(obj.object_type) + "</dd>" +
        "<dt>Rows</dt><dd>" + esc(obj.estimated_rows) + (obj.approximate ? " (approx)" : "") + "</dd>" +
        "<dt>Group</dt><dd>" + esc(cls.group) + " · " + esc(cls.confidence) + "</dd>" +
        "<dt>Sensitivity</dt><dd>" + esc(cls.sensitivity) + "</dd>" +
        "<dt>Browse</dt><dd>" + esc(cls.browse_status) + "</dd>" +
        "<dt>Export</dt><dd>" + esc(cls.export_status) + "</dd>" +
        "<dt>Repository</dt><dd>" + esc(data.source_repository) + "</dd>" +
        "<dt>Role</dt><dd>" + esc(cls.likely_role) + "</dd>";
    }

    function renderMetaTabs(data) {
      var obj = data.object || {};
      $("#dex-columns").textContent = JSON.stringify(obj.columns || [], null, 2);
      $("#dex-rels").textContent = JSON.stringify(obj.keys || [], null, 2);
      $("#dex-indexes").textContent = JSON.stringify(obj.indexes || [], null, 2);
      $("#dex-query").textContent = data.safe_query || "";
      var lin = data.lineage || {};
      $("#dex-lineage").textContent = JSON.stringify(lin, null, 2);
      function list(id, items, empty) {
        var el = $(id);
        items = items || [];
        if (!items.length) { el.innerHTML = "<p class='muted'>" + empty + "</p>"; return; }
        el.innerHTML = "<ul>" + items.map(function (it) {
          return "<li><strong>" + esc(it.name || it.key || "") + "</strong> " +
            "<span class='muted'>" + esc(it.uid || it.source_key || "") + " · " +
            esc(it.confidence || "") +
            (it.unresolved_mapping ? " · unresolved" : "") +
            "</span></li>";
        }).join("") + "</ul>";
      }
      list("#dex-used-indicators", lin.used_by_indicators, "No verified indicator→table mappings for this object.");
      list("#dex-used-reports", lin.used_by_reports, "No verified report→table mappings.");
      list("#dex-used-exports", lin.used_by_exports, "No Live Data Export sources map here.");
    }

    async function loadGrid(page) {
      if (!state.selected) return;
      if (state.abort) state.abort.abort();
      state.abort = new AbortController();
      state.page = page;
      setError("");
      var filters = [];
      var q = ($("#dex-search").value || "").trim();
      if (q && state.detail && state.detail.object && state.detail.object.columns && state.detail.object.columns[0]) {
        filters.push({ column: state.detail.object.columns[0].name, op: "contains", value: q });
      }
      try {
        var res = await fetch(root.getAttribute("data-browse-url"), {
          method: "POST",
          headers: { "Content-Type": "application/json", Accept: "application/json" },
          signal: state.abort.signal,
          body: JSON.stringify({
            environment: envSel.value,
            schema: state.selected.schema,
            name: state.selected.name,
            page: page,
            sort_column: state.sortColumn,
            sort_dir: state.sortDir,
            filters: filters,
          }),
        });
        var data = await readJson(res);
        if (!res.ok) throw new Error(data.error || "Browse failed");
        renderGrid(data);
        if (data.safe_query) $("#dex-query").textContent = data.safe_query;
      } catch (err) {
        if (err.name === "AbortError") return;
        setError(err.message || String(err));
      }
    }

    function renderGrid(data) {
      var table = $("#dex-grid");
      var thead = table.querySelector("thead");
      var tbody = table.querySelector("tbody");
      thead.innerHTML = "";
      tbody.innerHTML = "";
      var trh = document.createElement("tr");
      (data.columns || []).forEach(function (c) {
        var th = document.createElement("th");
        th.textContent = c;
        th.title = "Sort by " + c;
        th.addEventListener("click", function () {
          if (state.sortColumn === c) state.sortDir = state.sortDir === "asc" ? "desc" : "asc";
          else { state.sortColumn = c; state.sortDir = "asc"; }
          loadGrid(1);
        });
        trh.appendChild(th);
      });
      thead.appendChild(trh);
      (data.rows || []).forEach(function (row) {
        var tr = document.createElement("tr");
        (data.columns || []).forEach(function (_c, i) {
          var td = document.createElement("td");
          var val = row[i] == null ? "" : String(row[i]);
          td.textContent = val;
          td.title = "Click to copy";
          td.addEventListener("click", function () {
            if (navigator.clipboard) navigator.clipboard.writeText(val);
          });
          tr.appendChild(td);
        });
        tbody.appendChild(tr);
      });
      var total = data.total_rows || 0;
      var size = data.page_size || 100;
      var pages = Math.max(1, Math.ceil(total / size));
      $("#dex-row-meta").textContent = total + " rows · page " + data.page + "/" + pages;
      $("#dex-page-label").textContent = "Page " + data.page;
      $("#dex-prev").disabled = data.page <= 1;
      $("#dex-next").disabled = data.page >= pages;
    }

    async function addFavorite() {
      if (!state.selected || !state.detail) return;
      await fetch(root.getAttribute("data-favorites-url"), {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          environment: envSel.value,
          schema: state.selected.schema,
          name: state.selected.name,
          object_type: (state.detail.object || {}).object_type || "table",
        }),
      });
      var favRes = await fetch(root.getAttribute("data-favorites-url") + "?environment=" + encodeURIComponent(envSel.value));
      var favData = await readJson(favRes);
      renderFavorites(favData.favorites || []);
    }

    async function runExplain() {
      if (!state.selected) return;
      var res = await fetch(root.getAttribute("data-explain-url"), {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          environment: envSel.value,
          schema: state.selected.schema,
          name: state.selected.name,
        }),
      });
      var data = await readJson(res);
      var out = $("#dex-explain-out");
      out.hidden = false;
      out.textContent = res.ok ? JSON.stringify(data.rows || data, null, 2) : (data.error || "Explain failed");
    }

    async function runExport() {
      if (!state.selected) return;
      $("#dex-export-result").textContent = "Exporting…";
      try {
        var res = await fetch(root.getAttribute("data-export-url"), {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            environment: envSel.value,
            schema: state.selected.schema,
            name: state.selected.name,
            format: $("#dex-format").value,
            row_limit: Number($("#dex-export-limit").value) || 5000,
          }),
        });
        var data = await readJson(res);
        if (!res.ok) throw new Error(data.error || "Export failed");
        var html = "Exported " + data.exported_rows + " rows · " + data.file_size + " bytes";
        if (data.download_url) html += " · <a href='" + esc(data.download_url) + "'>Download</a>";
        if (data.warnings && data.warnings.length) html += "<div>" + data.warnings.map(esc).join("<br>") + "</div>";
        $("#dex-export-result").innerHTML = html;
      } catch (err) {
        $("#dex-export-result").textContent = err.message || String(err);
      }
    }

    function renderInventory(inv) {
      var body = $("#dex-inventory-body");
      var groups = (inv && inv.groups) || {};
      var html = "<p class='muted'>" + esc(inv.note || "") + "</p>";
      html += "<p>Objects: " + esc(inv.object_count) + " · connection " + esc(inv.connection_id) + "</p>";
      Object.keys(groups).forEach(function (g) {
        var items = groups[g] || [];
        html += "<div class='dex-inv-group'><h4>" + esc(g) + " (" + items.length + ")</h4>";
        if (!items.length) html += "<p class='muted'>None discovered</p>";
        else {
          html += "<ul>" + items.map(function (it) {
            return "<li><code>" + esc(it.full_name) + "</code> · " + esc(it.object_type) +
              " · rows " + esc(it.estimated_rows) +
              " · " + esc(it.sensitivity_level) +
              " · browse " + esc(it.recommended_browse_status) +
              " · export " + esc(it.recommended_export_status) +
              (it.classification_unresolved ? " · <em>unresolved</em>" : "") +
              "</li>";
          }).join("") + "</ul>";
        }
        html += "</div>";
      });
      var unr = inv.unresolved_hub_lineage || {};
      if (unr.indicators && unr.indicators.length) {
        html += "<div class='dex-inv-group'><h4>Unresolved hub indicator mappings (" + unr.indicators.length + ")</h4>";
        html += "<p class='muted'>HCSC–RF references DHIS2 analytics UIDs, not physical tables — not guessed.</p></div>";
      }
      body.innerHTML = html;
    }
  }

  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", init);
  else init();
})();
