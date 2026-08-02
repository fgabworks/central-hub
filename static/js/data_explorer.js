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

  function readUrlState() {
    var params = new URLSearchParams(window.location.search);
    var filters = [];
    var error = "";
    if (params.get("filters")) {
      try {
        filters = JSON.parse(params.get("filters"));
        if (!Array.isArray(filters) || filters.length > 20) throw new Error("invalid");
        filters = filters.map(function (filter) {
          if (!filter || typeof filter !== "object") throw new Error("invalid");
          return {
            column: String(filter.column || ""),
            op: String(filter.op || ""),
            value: filter.value == null ? null : filter.value,
          };
        });
      } catch (_error) {
        filters = [];
        error = "Invalid filter state in the URL. Clear all filters to continue.";
      }
    }
    var page = Number(params.get("page") || 1);
    return {
      environment: params.get("environment") || "",
      schema: params.get("schema") || "",
      object: params.get("object") || "",
      page: Number.isInteger(page) && page > 0 ? page : 1,
      sortColumn: params.get("sort") || null,
      sortDir: params.get("dir") === "desc" ? "desc" : "asc",
      filters: filters,
      quickSearch: params.get("q") || "",
      error: error,
    };
  }

  function init() {
    var root = document.getElementById("dex-root");
    if (!root) return;
    var urlState = readUrlState();
    var state = {
      boot: boot(),
      selected: null,
      detail: null,
      page: 1,
      sortColumn: null,
      sortDir: "asc",
      abort: null,
      selectedRow: null,
      filters: urlState.filters,
      quickSearch: urlState.quickSearch,
      urlError: urlState.error,
    };

    var envSel = $("#dex-env");
    envSel.value = state.boot.environment || "dev";
    $("#dex-search").value = state.quickSearch;
    updateEnvironmentBadge();
    updateConnStatus();
    renderTree(state.boot.tree);
    renderFavorites(state.boot.favorites || []);
    if (urlState.schema && urlState.object) {
      state.sortColumn = urlState.sortColumn;
      state.sortDir = urlState.sortDir;
      state.page = urlState.page;
      selectObject(urlState.schema, urlState.object, { restore: true });
    }

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
      state.selectedRow = null;
      resetGridQuery();
      updateEnvironmentBadge();
      clearSelectionDetails();
      setGridState("Select a table or view to browse data.");
      syncUrl();
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
    $("#dex-export-shortcut").addEventListener("click", function () {
      activateObjectTab("export");
    });
    $("#dex-details-toggle").addEventListener("click", toggleDetails);
    $("#dex-details-close").addEventListener("click", function () { setDetailsOpen(false); });
    $("#dex-search").addEventListener("keydown", function (e) {
      if (e.key === "Enter" && state.selected) {
        state.quickSearch = $("#dex-search").value.trim();
        applyGridQueryChange();
      }
    });
    $("#dex-table-filter").addEventListener("input", filterTree);
    $("#dex-filter-column").addEventListener("change", updateFilterOperators);
    $("#dex-filter-operator").addEventListener("change", updateFilterValueControl);
    $("#dex-filter-form").addEventListener("submit", addFilter);
    $("#dex-filter-clear").addEventListener("click", function () {
      state.filters = [];
      state.quickSearch = "";
      $("#dex-search").value = "";
      state.urlError = "";
      renderFilterChips();
      applyGridQueryChange();
    });
    window.addEventListener("popstate", function () { window.location.reload(); });

    var compactDetails = window.matchMedia("(max-width: 1280px)");
    if (compactDetails.matches) setDetailsOpen(false);
    compactDetails.addEventListener("change", function (event) {
      if (event.matches) setDetailsOpen(false);
    });

    function activateObjectTab(name) {
      var tab = $(".dex-tab[data-tab='" + name + "']", root);
      if (tab) tab.click();
    }

    function updateEnvironmentBadge() {
      var badge = $("#dex-header-env");
      var label = envSel.options[envSel.selectedIndex];
      badge.textContent = label ? label.text.replace(" (local demo)", "") : envSel.value;
      badge.classList.toggle("dex-status-live", envSel.value === "live");
    }

    function setDetailsOpen(open) {
      $(".dex-layout", root).classList.toggle("details-collapsed", !open);
      $("#dex-details-toggle").setAttribute("aria-expanded", String(open));
    }

    function toggleDetails() {
      var collapsed = $(".dex-layout", root).classList.contains("details-collapsed");
      setDetailsOpen(collapsed);
    }

    function setGridState(message, kind) {
      var el = $("#dex-grid-state");
      el.textContent = message || "";
      el.hidden = !message;
      el.classList.toggle("is-error", kind === "error");
      el.classList.toggle("is-invalid", kind === "invalid");
    }

    function clearSelectionDetails() {
      $("#dex-selected-row-empty").hidden = false;
      $("#dex-selected-row-list").hidden = true;
      $("#dex-selected-row-list").innerHTML = "";
    }

    function resetGridQuery() {
      state.page = 1;
      state.sortColumn = null;
      state.sortDir = "asc";
      state.filters = [];
      state.quickSearch = "";
      state.urlError = "";
      $("#dex-search").value = "";
      renderFilterChips();
    }

    function syncUrl() {
      var url = new URL(window.location.href);
      url.searchParams.set("environment", envSel.value);
      if (state.selected) {
        url.searchParams.set("schema", state.selected.schema);
        url.searchParams.set("object", state.selected.name);
        url.searchParams.set("page", String(state.page));
      } else {
        ["schema", "object", "page"].forEach(function (key) { url.searchParams.delete(key); });
      }
      if (state.sortColumn) {
        url.searchParams.set("sort", state.sortColumn);
        url.searchParams.set("dir", state.sortDir);
      } else {
        url.searchParams.delete("sort");
        url.searchParams.delete("dir");
      }
      if (state.filters.length) {
        url.searchParams.set("filters", JSON.stringify(state.filters));
      } else {
        url.searchParams.delete("filters");
      }
      if (state.quickSearch) url.searchParams.set("q", state.quickSearch);
      else url.searchParams.delete("q");
      window.history.replaceState(null, "", url.pathname + "?" + url.searchParams.toString());
    }

    function applyGridQueryChange() {
      state.page = 1;
      syncUrl();
      loadGrid(1);
    }

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
      filterTree();
    }

    function section(label, items) {
      items = items || [];
      if (!items.length) return "";
      return (
        "<div class='dex-tree-section-label'>" + esc(label) + "</div>" +
        items.map(function (it) {
          return "<button type='button' data-schema='" + esc(it.schema) + "' data-name='" + esc(it.name) + "'>" +
            "<span>" + esc(it.name) + "</span>" +
            (it.estimated_rows != null ? " <span class='dex-tree-count'>" + esc(formatNumber(it.estimated_rows)) + "</span>" : "") +
            "</button>";
        }).join("")
      );
    }

    function formatNumber(value) {
      var number = Number(value);
      return Number.isFinite(number) ? number.toLocaleString() : String(value == null ? "" : value);
    }

    function filterTree() {
      var query = ($("#dex-table-filter").value || "").trim().toLowerCase();
      $all("#dex-tree button[data-name]", root).forEach(function (button) {
        var value = (button.getAttribute("data-schema") + "." + button.getAttribute("data-name")).toLowerCase();
        button.hidden = Boolean(query && value.indexOf(query) === -1);
      });
      $all("#dex-tree details", root).forEach(function (details) {
        var visible = $all("button[data-name]", details).some(function (button) { return !button.hidden; });
        details.hidden = !visible;
        if (query && visible) details.open = true;
      });
    }

    function markSelectedObject() {
      $all("button[data-schema][data-name]", root).forEach(function (button) {
        var selected = state.selected &&
          button.getAttribute("data-schema") === state.selected.schema &&
          button.getAttribute("data-name") === state.selected.name;
        button.classList.toggle("is-active", Boolean(selected));
        button.setAttribute("aria-current", selected ? "true" : "false");
      });
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
      markSelectedObject();
    }

    async function selectObject(schema, name, options) {
      options = options || {};
      setError("");
      state.selected = { schema: schema, name: name };
      state.selectedRow = null;
      if (!options.restore) resetGridQuery();
      $("#dex-object-title").textContent = schema + "." + name;
      $("#dex-object-meta").textContent = "Loading table metadata…";
      $("#dex-browse-btn").disabled = false;
      $("#dex-fav-btn").disabled = false;
      $("#dex-export-btn").disabled = false;
      $("#dex-export-shortcut").disabled = false;
      clearSelectionDetails();
      markSelectedObject();
      syncUrl();
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
        renderFilterBuilder((data.object || {}).columns || []);
        renderFilterChips();
        if (state.urlError) {
          setGridState(state.urlError, "invalid");
          setError(state.urlError);
          return;
        }
        await loadGrid(state.page);
      } catch (err) {
        setError(err.message || String(err));
      }
    }

    function renderDetails(data) {
      var obj = data.object || {};
      var cls = data.classification || {};
      var dl = $("#dex-detail-dl");
      var typeLabel = obj.object_type || "object";
      $("#dex-object-meta").textContent =
        formatNumber(obj.estimated_rows) + " rows · " + typeLabel +
        (cls.group ? " · " + cls.group : "");
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

    function renderFilterBuilder(columns) {
      var select = $("#dex-filter-column");
      var available = columns.filter(function (column) {
        return column.filter_operators && column.filter_operators.length;
      });
      select.innerHTML = "<option value=''>Select column</option>" +
        available.map(function (column) {
          return "<option value='" + esc(column.name) + "'>" +
            esc(column.name) + " (" + esc(column.data_type) + ")</option>";
        }).join("");
      select.disabled = !available.length;
      $("#dex-filter-operator").innerHTML = "<option value=''>Select operator</option>";
      $("#dex-filter-operator").disabled = true;
      $("#dex-filter-value").value = "";
      $("#dex-filter-value").disabled = true;
      $("#dex-filter-add").disabled = true;
    }

    function selectedFilterColumn() {
      var name = $("#dex-filter-column").value;
      var columns = state.detail && state.detail.object ? state.detail.object.columns || [] : [];
      return columns.find(function (column) { return column.name === name; }) || null;
    }

    function operatorLabel(operator) {
      return {
        eq: "equals",
        neq: "does not equal",
        contains: "contains",
        gt: "greater than",
        gte: "greater than or equal",
        lt: "less than",
        lte: "less than or equal",
        is_null: "is null",
        not_null: "is not null",
      }[operator] || operator;
    }

    function updateFilterOperators() {
      var column = selectedFilterColumn();
      var operator = $("#dex-filter-operator");
      var operators = column ? column.filter_operators || [] : [];
      operator.innerHTML = "<option value=''>Select operator</option>" +
        operators.map(function (value) {
          return "<option value='" + esc(value) + "'>" + esc(operatorLabel(value)) + "</option>";
        }).join("");
      operator.disabled = !operators.length;
      updateFilterValueControl();
    }

    function updateFilterValueControl() {
      var operator = $("#dex-filter-operator").value;
      var noValue = operator === "is_null" || operator === "not_null";
      var value = $("#dex-filter-value");
      value.disabled = !operator || noValue;
      value.required = Boolean(operator && !noValue);
      if (noValue) value.value = "";
      $("#dex-filter-add").disabled = !selectedFilterColumn() || !operator;
    }

    function addFilter(event) {
      event.preventDefault();
      var column = selectedFilterColumn();
      var operator = $("#dex-filter-operator").value;
      var noValue = operator === "is_null" || operator === "not_null";
      var value = noValue ? null : $("#dex-filter-value").value;
      if (!column || !operator || (!noValue && value === "")) {
        setGridState("Choose a column, operator, and value.", "invalid");
        return;
      }
      if (state.filters.length >= 20) {
        setGridState("A maximum of 20 filters is allowed.", "invalid");
        return;
      }
      state.filters.push({ column: column.name, op: operator, value: value });
      state.urlError = "";
      $("#dex-filter-value").value = "";
      renderFilterChips();
      applyGridQueryChange();
    }

    function renderFilterChips() {
      var box = $("#dex-filter-chips");
      var chips = state.filters.map(function (filter, index) {
        var value = filter.value == null ? "" : " " + String(filter.value);
        return "<span class='dex-filter-chip'><span>" +
          esc(filter.column + " " + operatorLabel(filter.op) + value) +
          "</span><button type='button' data-filter-index='" + index +
          "' aria-label='Remove filter " + esc(filter.column) + "'>&times;</button></span>";
      });
      if (state.quickSearch) {
        chips.unshift(
          "<span class='dex-filter-chip'><span>Quick search contains " +
          esc(state.quickSearch) +
          "</span><button type='button' data-clear-quick='true' aria-label='Remove quick search'>&times;</button></span>"
        );
      }
      box.innerHTML = chips.join("");
      $("#dex-filter-logic").hidden = chips.length < 2;
      $("#dex-filter-clear").hidden = !chips.length && !state.urlError;
      $all("button[data-filter-index]", box).forEach(function (button) {
        button.addEventListener("click", function () {
          state.filters.splice(Number(button.getAttribute("data-filter-index")), 1);
          state.urlError = "";
          renderFilterChips();
          applyGridQueryChange();
        });
      });
      var quick = $("button[data-clear-quick]", box);
      if (quick) {
        quick.addEventListener("click", function () {
          state.quickSearch = "";
          $("#dex-search").value = "";
          renderFilterChips();
          applyGridQueryChange();
        });
      }
    }

    function activeFilters() {
      var filters = state.filters.slice();
      var q = state.quickSearch.trim();
      if (q && state.detail && state.detail.object && state.detail.object.columns) {
        var first = state.detail.object.columns.find(function (column) {
          return (column.filter_operators || []).indexOf("contains") !== -1;
        });
        if (first) filters.push({ column: first.name, op: "contains", value: q });
      }
      return filters;
    }

    async function loadGrid(page) {
      if (!state.selected) return;
      if (state.abort) state.abort.abort();
      state.abort = new AbortController();
      state.page = page;
      setError("");
      setGridState("Loading rows…");
      $("#dex-grid").setAttribute("aria-busy", "true");
      var filters = activeFilters();
      syncUrl();
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
        if (!res.ok) {
          var failure = new Error(data.error || "Browse failed");
          failure.code = data.code || "browse_error";
          throw failure;
        }
        renderGrid(data);
        if (data.safe_query) $("#dex-query").textContent = data.safe_query;
      } catch (err) {
        if (err.name === "AbortError") return;
        setError(err.message || String(err));
        setGridState(
          err.message || "Unable to load rows.",
          err.code === "invalid_filter" || err.code === "invalid_sort" ? "invalid" : "error"
        );
      } finally {
        $("#dex-grid").setAttribute("aria-busy", "false");
      }
    }

    function renderGrid(data) {
      var table = $("#dex-grid");
      var thead = table.querySelector("thead");
      var tbody = table.querySelector("tbody");
      thead.innerHTML = "";
      tbody.innerHTML = "";
      state.selectedRow = null;
      clearSelectionDetails();
      var trh = document.createElement("tr");
      (data.columns || []).forEach(function (c) {
        var th = document.createElement("th");
        th.textContent = c;
        var activeDirection = state.sortColumn === c ? state.sortDir : null;
        th.title = activeDirection === "asc" ? "Sort descending" :
          activeDirection === "desc" ? "Reset sorting" : "Sort ascending";
        th.setAttribute("aria-sort", activeDirection === "asc" ? "ascending" :
          activeDirection === "desc" ? "descending" : "none");
        if (activeDirection) th.setAttribute("data-sort", activeDirection);
        th.tabIndex = 0;
        function changeSort() {
          if (state.sortColumn !== c) {
            state.sortColumn = c;
            state.sortDir = "asc";
          } else if (state.sortDir === "asc") {
            state.sortDir = "desc";
          } else {
            state.sortColumn = null;
            state.sortDir = "asc";
          }
          applyGridQueryChange();
        }
        th.addEventListener("click", function () {
          changeSort();
        });
        th.addEventListener("keydown", function (event) {
          if (event.key === "Enter" || event.key === " ") {
            event.preventDefault();
            changeSort();
          }
        });
        trh.appendChild(th);
      });
      thead.appendChild(trh);
      (data.rows || []).forEach(function (row) {
        var tr = document.createElement("tr");
        tr.tabIndex = 0;
        tr.setAttribute("aria-selected", "false");
        (data.columns || []).forEach(function (_c, i) {
          var td = document.createElement("td");
          var val = row[i] == null ? "" : String(row[i]);
          td.textContent = val;
          td.title = "Click to copy";
          tr.appendChild(td);
        });
        function chooseRow() {
          $all("tbody tr", table).forEach(function (candidate) {
            candidate.classList.toggle("is-selected", candidate === tr);
            candidate.setAttribute("aria-selected", String(candidate === tr));
          });
          state.selectedRow = row;
          renderSelectedRow(data.columns || [], row);
          if (window.matchMedia("(max-width: 1280px)").matches) setDetailsOpen(true);
        }
        tr.addEventListener("click", chooseRow);
        tr.addEventListener("keydown", function (event) {
          if (event.key === "Enter" || event.key === " ") {
            event.preventDefault();
            chooseRow();
          }
        });
        tbody.appendChild(tr);
      });
      var total = data.total_rows || 0;
      var size = data.page_size || 100;
      var pages = Math.max(1, Math.ceil(total / size));
      var start = total ? ((data.page - 1) * size) + 1 : 0;
      var end = Math.min(total, data.page * size);
      $("#dex-row-meta").textContent = start + "–" + end + " of " + formatNumber(total) + " rows";
      $("#dex-filtered-count").textContent = activeFilters().length ?
        formatNumber(data.filtered_rows == null ? total : data.filtered_rows) + " filtered rows" : "";
      $("#dex-page-label").textContent = "Page " + data.page + " of " + pages;
      $("#dex-prev").disabled = data.page <= 1;
      $("#dex-next").disabled = data.page >= pages;
      setGridState((data.rows || []).length ? "" : "No rows match the current filter.");
    }

    function renderSelectedRow(columns, row) {
      var list = $("#dex-selected-row-list");
      $("#dex-selected-row-empty").hidden = true;
      list.hidden = false;
      list.innerHTML = columns.map(function (column, index) {
        var value = row[index] == null ? "" : row[index];
        return "<dt>" + esc(column) + "</dt><dd>" + esc(value) + "</dd>";
      }).join("");
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
            filters: activeFilters(),
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
