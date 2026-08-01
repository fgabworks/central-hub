/**
 * DHIS2 Reports — Run Report parameter UI.
 * Searchable period dropdown + organisation-unit picker (reuses Hub APIs).
 * No arbitrary typed period/OU values; credentials stay server-side.
 */
(function () {
  "use strict";

  var root = document.getElementById("dr-run");
  if (!root) return;

  var statusEl = document.getElementById("dr-status");
  var previewEl = document.getElementById("dr-preview");
  var liveBox = document.getElementById("dr-live-box");
  var viewBtn = document.getElementById("dr-view");
  var openDhis2 = document.getElementById("dr-open-dhis2");
  var downloadBtn = document.getElementById("dr-download");
  var genBtn = document.getElementById("dr-generate-view");
  var refreshBtn = document.getElementById("dr-refresh");
  var fsBtn = document.getElementById("dr-fullscreen");
  var printBtn = document.getElementById("dr-print");
  var viewer = document.getElementById("dr-viewer");
  var skeleton = document.getElementById("dr-viewer-skeleton");
  var empty = document.getElementById("dr-viewer-empty");
  var hint = document.getElementById("dr-report-hint");
  var reportSel = document.getElementById("dr-report");
  var envSel = document.getElementById("dr-env");

  var periodSearch = document.getElementById("dr-period-search");
  var periodHidden = document.getElementById("dr-period");
  var periodResults = document.getElementById("dr-period-results");
  var periodHint = document.getElementById("dr-period-hint");
  var periodError = document.getElementById("dr-period-error");

  var ouSearch = document.getElementById("dr-ou-search");
  var ouHidden = document.getElementById("dr-ou");
  var ouResults = document.getElementById("dr-ou-results");
  var ouChipRow = document.getElementById("dr-ou-chip-row");
  var ouChipLabel = document.getElementById("dr-ou-chip-label");
  var ouClear = document.getElementById("dr-ou-clear");
  var ouTree = document.getElementById("dr-ou-tree");
  var ouError = document.getElementById("dr-ou-error");
  var ouHint = document.getElementById("dr-ou-hint");

  var catalog = null;
  var byId = {};
  var periodOptions = [];
  var periodById = {};
  var last = { viewer: "", open: "", download: "", browserOnly: false };
  var abortCtl = null;
  var ouTimer = null;
  var periodTimer = null;
  var liveConfirmedForRun = false;
  var inFlight = {};
  var ouCache = {}; // env -> { q|parent -> rows }

  function preferredEnv() {
    return root.getAttribute("data-preferred-env") || "stage";
  }

  function selectedMeta() {
    return byId[reportSel.value] || null;
  }

  function storageKey(kind, reportId) {
    return (
      "centralhub.dhis2.reports.run." +
      kind +
      "." +
      envSel.value +
      "." +
      (reportId || reportSel.value || "_")
    );
  }

  function ouFreqKey() {
    return "centralhub.dhis2.reports.run.oufreq." + envSel.value;
  }

  function loadRememberedPeriod(reportId) {
    try {
      return JSON.parse(localStorage.getItem(storageKey("period", reportId)) || "null");
    } catch (e) {
      return null;
    }
  }

  function saveRememberedPeriod(id, reportId) {
    try {
      localStorage.setItem(
        storageKey("period", reportId),
        JSON.stringify({ id: id, at: Date.now() })
      );
    } catch (e) {}
  }

  function loadRememberedOu(reportId) {
    try {
      return JSON.parse(localStorage.getItem(storageKey("ou", reportId)) || "null");
    } catch (e) {
      return null;
    }
  }

  function saveRememberedOu(id, name, reportId) {
    try {
      localStorage.setItem(
        storageKey("ou", reportId),
        JSON.stringify({ id: id, name: name || "", at: Date.now() })
      );
      bumpOuFrequency(id, name);
    } catch (e) {}
  }

  function bumpOuFrequency(id, name) {
    try {
      var map = JSON.parse(localStorage.getItem(ouFreqKey()) || "{}");
      var row = map[id] || { id: id, name: name || "", count: 0, at: 0 };
      row.count = (row.count || 0) + 1;
      row.name = name || row.name || "";
      row.at = Date.now();
      map[id] = row;
      localStorage.setItem(ouFreqKey(), JSON.stringify(map));
    } catch (e) {}
  }

  function recentAndFrequentOus() {
    var rows = [];
    try {
      var map = JSON.parse(localStorage.getItem(ouFreqKey()) || "{}");
      rows = Object.keys(map).map(function (k) {
        return map[k];
      });
      rows.sort(function (a, b) {
        if ((b.count || 0) !== (a.count || 0)) return (b.count || 0) - (a.count || 0);
        return (b.at || 0) - (a.at || 0);
      });
    } catch (e) {}
    return rows.slice(0, 8);
  }

  function isValidOuUid(value) {
    return /^[A-Za-z0-9]{11}$/.test((value || "").trim());
  }

  function syncLive() {
    liveBox.hidden = envSel.value !== "live";
    if (envSel.value !== "live") {
      document.getElementById("dr-confirm-live").checked = false;
      liveConfirmedForRun = false;
    }
  }

  function setFieldError(el, msg) {
    if (!el) return;
    if (!msg) {
      el.hidden = true;
      el.textContent = "";
      return;
    }
    el.hidden = false;
    el.textContent = msg;
  }

  function dedupeFetch(key, url) {
    if (inFlight[key]) return inFlight[key];
    var p = fetch(url, { credentials: "same-origin" })
      .then(function (r) {
        return r.json();
      })
      .finally(function () {
        delete inFlight[key];
      });
    inFlight[key] = p;
    return p;
  }

  function periodValue() {
    var id = (periodHidden.value || "").trim();
    return periodById[id] ? id : "";
  }

  function selectPeriod(id, label) {
    if (!id || !periodById[id]) {
      periodHidden.value = "";
      periodSearch.value = "";
      validateForm();
      return;
    }
    periodHidden.value = id;
    periodSearch.value = label || periodById[id].label || id;
    periodResults.hidden = true;
    periodSearch.setAttribute("aria-expanded", "false");
    saveRememberedPeriod(id);
    setFieldError(periodError, "");
    validateForm();
  }

  function renderPeriodResults(filterText) {
    var q = (filterText || "").trim().toLowerCase();
    periodResults.innerHTML = "";
    var matched = periodOptions.filter(function (p) {
      if (!q) return true;
      return (p.id + " " + p.label).toLowerCase().indexOf(q) >= 0;
    });
    if (!matched.length) {
      var emptyLi = document.createElement("li");
      emptyLi.className = "muted";
      emptyLi.textContent = "No matching periods";
      periodResults.appendChild(emptyLi);
      periodResults.hidden = false;
      return;
    }
    matched.slice(0, 40).forEach(function (p) {
      var li = document.createElement("li");
      var btn = document.createElement("button");
      btn.type = "button";
      btn.setAttribute("role", "option");
      btn.dataset.id = p.id;
      btn.innerHTML =
        "<strong>" +
        escapeHtml(p.label) +
        '</strong> <span class="muted">' +
        escapeHtml(p.id) +
        "</span>";
      btn.addEventListener("click", function () {
        selectPeriod(p.id, p.label);
      });
      li.appendChild(btn);
      periodResults.appendChild(li);
    });
    periodResults.hidden = false;
    periodSearch.setAttribute("aria-expanded", "true");
  }

  function escapeHtml(s) {
    return String(s == null ? "" : s)
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;");
  }

  function loadPeriodsForReport() {
    var meta = selectedMeta();
    var ptype = (meta && meta.preferred_period_type) || "monthly";
    var rel = ((meta && meta.relative_periods) || []).join(",");
    var remembered =
      (loadRememberedPeriod() && loadRememberedPeriod().id) ||
      root.getAttribute("data-preset-period") ||
      "";
    var url =
      root.getAttribute("data-periods-url") +
      "?environment=" +
      encodeURIComponent(envSel.value) +
      "&period_type=" +
      encodeURIComponent(ptype) +
      "&remembered=" +
      encodeURIComponent(remembered) +
      (rel ? "&relative=" + encodeURIComponent(rel) : "");
    var key = "periods:" + envSel.value + ":" + ptype + ":" + rel + ":" + remembered;
    return dedupeFetch(key, url)
      .then(function (data) {
        periodOptions = (data && data.periods) || [];
        if (data && data.relative_periods && data.relative_periods.length) {
          var seen = {};
          periodOptions.forEach(function (p) {
            seen[p.id] = true;
          });
          data.relative_periods.forEach(function (p) {
            if (!seen[p.id]) periodOptions.push(p);
          });
        }
        periodById = {};
        periodOptions.forEach(function (p) {
          periodById[p.id] = p;
        });
        var defaultId =
          (remembered && periodById[remembered] && remembered) ||
          (data && data.default_period) ||
          (periodOptions[0] && periodOptions[0].id) ||
          "";
        if (defaultId) {
          selectPeriod(defaultId, (periodById[defaultId] || {}).label);
        } else {
          periodHidden.value = "";
          periodSearch.value = "";
        }
        periodHint.textContent =
          "Type: " +
          ((data && data.period_type) || ptype) +
          " · select from list (canonical DHIS2 values)";
        validateForm();
      })
      .catch(function () {
        periodOptions = [];
        periodById = {};
        periodHint.textContent = "Period options unavailable.";
        validateForm();
      });
  }

  function setOuSelection(id, name, path) {
    if (!isValidOuUid(id)) {
      clearOuSelection();
      return;
    }
    ouHidden.value = id;
    ouChipLabel.textContent =
      (name || id) + (path ? " — " + path : "") + " · " + id;
    ouChipRow.hidden = false;
    ouSearch.value = "";
    ouResults.hidden = true;
    ouSearch.setAttribute("aria-expanded", "false");
    saveRememberedOu(id, name || "");
    setFieldError(ouError, "");
    validateForm();
  }

  function clearOuSelection() {
    ouHidden.value = "";
    ouChipRow.hidden = true;
    ouChipLabel.textContent = "";
    ouSearch.value = "";
    validateForm();
  }

  function cacheOu(env, key, rows) {
    if (!ouCache[env]) ouCache[env] = {};
    ouCache[env][key] = rows;
  }

  function getCachedOu(env, key) {
    return ouCache[env] && ouCache[env][key];
  }

  function renderOuRows(rows, host, opts) {
    opts = opts || {};
    host.innerHTML = "";
    if (!rows.length) {
      host.hidden = true;
      return;
    }
    rows.forEach(function (row) {
      var li = document.createElement(opts.asButtons ? "div" : "li");
      if (opts.asButtons) li.className = "dr-ou-node";
      var btn = document.createElement("button");
      btn.type = "button";
      var path = row.path || "";
      btn.innerHTML =
        "<strong>" +
        escapeHtml(row.name || row.id) +
        "</strong>" +
        (row.code ? ' <span class="muted">· ' + escapeHtml(row.code) + "</span>" : "") +
        ' <span class="muted">· ' +
        escapeHtml(row.id) +
        "</span>" +
        (path ? '<div class="dr-ou-path muted">' + escapeHtml(path) + "</div>" : "");
      btn.addEventListener("click", function () {
        setOuSelection(row.id, row.name || "", path);
      });
      if (opts.asButtons && row.has_children) {
        var expand = document.createElement("button");
        expand.type = "button";
        expand.className = "btn btn-sm";
        expand.textContent = "▸";
        expand.title = "Load children";
        var childHost = document.createElement("div");
        childHost.className = "dr-ou-children";
        childHost.hidden = true;
        expand.addEventListener("click", function (ev) {
          ev.stopPropagation();
          if (!childHost.hidden && childHost.childNodes.length) {
            childHost.hidden = true;
            return;
          }
          loadOuChildren(row.id, childHost);
          childHost.hidden = false;
        });
        var head = document.createElement("div");
        head.className = "dr-ou-node-head";
        head.appendChild(expand);
        head.appendChild(btn);
        li.appendChild(head);
        li.appendChild(childHost);
      } else {
        li.appendChild(btn);
      }
      host.appendChild(li);
    });
    host.hidden = false;
  }

  function searchOrgUnits(q) {
    var needle = (q || "").trim();
    if (needle.length === 1) {
      ouResults.hidden = true;
      return;
    }
    if (!needle) {
      var recent = recentAndFrequentOus();
      if (recent.length) {
        renderOuRows(
          recent.map(function (r) {
            return { id: r.id, name: r.name, code: "", path: "Recent / frequent", has_children: false };
          }),
          ouResults
        );
        ouSearch.setAttribute("aria-expanded", "true");
        return;
      }
      ouResults.hidden = true;
      return;
    }
    var env = envSel.value;
    var cacheKey = "q:" + needle.toLowerCase();
    var cached = getCachedOu(env, cacheKey);
    if (cached) {
      renderOuRows(cached, ouResults);
      ouSearch.setAttribute("aria-expanded", "true");
      return;
    }
    var url =
      root.getAttribute("data-org-units-url") +
      "?environment=" +
      encodeURIComponent(env) +
      "&q=" +
      encodeURIComponent(needle) +
      "&limit=25";
    var reqKey = "ou:" + env + ":" + cacheKey;
    dedupeFetch(reqKey, url)
      .then(function (data) {
        var rows = (data && data.org_units) || [];
        cacheOu(env, cacheKey, rows);
        renderOuRows(rows, ouResults);
        ouSearch.setAttribute("aria-expanded", "true");
      })
      .catch(function () {
        ouResults.hidden = true;
      });
  }

  function loadOuChildren(parentId, host) {
    var env = envSel.value;
    var cacheKey = "parent:" + parentId;
    var cached = getCachedOu(env, cacheKey);
    if (cached) {
      renderOuRows(cached, host, { asButtons: true });
      return;
    }
    var url =
      root.getAttribute("data-org-units-url") +
      "?environment=" +
      encodeURIComponent(env) +
      "&parent_id=" +
      encodeURIComponent(parentId) +
      "&limit=40";
    dedupeFetch("ou:" + env + ":" + cacheKey, url).then(function (data) {
      var rows = (data && data.org_units) || [];
      cacheOu(env, cacheKey, rows);
      renderOuRows(rows, host, { asButtons: true });
    });
  }

  function loadRootTree() {
    if (!ouTree) return;
    var env = envSel.value;
    var cacheKey = "root";
    var cached = getCachedOu(env, cacheKey);
    function apply(rows) {
      if (!rows.length) {
        ouTree.hidden = true;
        return;
      }
      renderOuRows(rows, ouTree, { asButtons: true });
      ouTree.hidden = false;
    }
    if (cached) {
      apply(cached);
      return;
    }
    var url =
      root.getAttribute("data-org-units-url") +
      "?environment=" +
      encodeURIComponent(env) +
      "&limit=20";
    dedupeFetch("ou:" + env + ":root", url)
      .then(function (data) {
        var rows = (data && data.org_units) || [];
        cacheOu(env, cacheKey, rows);
        apply(rows);
      })
      .catch(function () {
        ouTree.hidden = true;
      });
  }

  function needsPeriod(meta) {
    if (!meta) return false;
    if (meta.period_required != null) return !!meta.period_required;
    return !!meta.needs_period;
  }

  function needsOu(meta) {
    if (!meta) return false;
    if (meta.org_unit_required != null) return !!meta.org_unit_required;
    return !!meta.needs_org_unit;
  }

  function showPeriod(meta) {
    if (!meta) return true;
    if (meta.show_period != null) return !!meta.show_period;
    return needsPeriod(meta) || true;
  }

  function showOu(meta) {
    if (!meta) return true;
    if (meta.show_org_unit != null) return !!meta.show_org_unit;
    return needsOu(meta) || true;
  }

  function validateForm() {
    var meta = selectedMeta();
    var peOk = !needsPeriod(meta) || !!periodValue();
    var ouOk = !needsOu(meta) || isValidOuUid(ouHidden.value);
    var liveOk =
      envSel.value !== "live" || !!(document.getElementById("dr-confirm-live") || {}).checked;

    setFieldError(
      periodError,
      needsPeriod(meta) && !periodValue() ? "Select a valid period from the list." : ""
    );
    setFieldError(
      ouError,
      needsOu(meta) && !isValidOuUid(ouHidden.value)
        ? "Select an organisation unit (11-character UID)."
        : ""
    );

    var ok = !!(meta && !meta.browser_only && meta.render_supported !== false);
    if (meta && meta.browser_only) {
      genBtn.disabled = false;
      genBtn.textContent = "Open in DHIS2";
      return true;
    }
    if (!meta) {
      genBtn.disabled = true;
      genBtn.textContent = "Generate & View";
      return false;
    }
    ok = ok && peOk && ouOk && liveOk;
    genBtn.disabled = !ok;
    genBtn.textContent = "Generate & View";
    return ok;
  }

  function setActions(opts) {
    last = {
      viewer: opts.viewer || "",
      open: opts.open || "",
      download: opts.download || "",
      browserOnly: !!opts.browserOnly,
    };
    openDhis2.hidden = !last.open;
    openDhis2.href = last.open || "#";
    downloadBtn.hidden = !last.download;
    downloadBtn.href = last.download || "#";
    viewBtn.hidden = !last.viewer || last.browserOnly;
    viewBtn.href = last.viewer || "#";
    refreshBtn.hidden = !last.viewer || last.browserOnly;
    fsBtn.hidden = !last.viewer || last.browserOnly;
    printBtn.hidden = !last.viewer || last.browserOnly;
  }

  function showViewer(url) {
    if (!url) {
      viewer.hidden = true;
      skeleton.hidden = true;
      empty.hidden = false;
      return;
    }
    empty.hidden = true;
    skeleton.hidden = false;
    viewer.hidden = true;
    viewer.onload = function () {
      skeleton.hidden = true;
      viewer.hidden = false;
    };
    viewer.src = url;
  }

  function fillReports() {
    byId = {};
    reportSel.innerHTML = "";
    var groups = [
      { label: "Native Standard Reports", rows: (catalog && catalog.native_standard) || [] },
      { label: "Repository / static", rows: (catalog && catalog.catalog_other) || [] },
      { label: "DHIS2 app shells (browser only)", rows: (catalog && catalog.app_shells) || [] },
    ];
    var hasAny = false;
    groups.forEach(function (g) {
      if (!g.rows.length) return;
      hasAny = true;
      var og = document.createElement("optgroup");
      og.label = g.label;
      g.rows.forEach(function (r) {
        byId[r.id] = r;
        var opt = document.createElement("option");
        opt.value = r.id;
        opt.textContent =
          r.name +
          (r.browser_only ? " — browser only" : r.render_supported === false ? " — limited" : "");
        og.appendChild(opt);
      });
      reportSel.appendChild(og);
    });
    if (!hasAny) {
      var opt = document.createElement("option");
      opt.value = "";
      opt.textContent = "No reports — sync from Report Library first";
      reportSel.appendChild(opt);
    }
    var selected = root.getAttribute("data-selected-report") || "";
    if (selected && byId[selected]) reportSel.value = selected;
    onReportChange();
  }

  function onReportChange() {
    var meta = selectedMeta();
    document.getElementById("dr-period-field").hidden = !(meta && showPeriod(meta));
    document.getElementById("dr-ou-field").hidden = !(meta && showOu(meta));
    if (!meta) {
      hint.textContent = "";
      validateForm();
      return;
    }
    if (meta.browser_only) {
      hint.textContent =
        "This is the DHIS2 application shell, not an individual report. Use Open in DHIS2, or pick a Native Standard Report from the synced library.";
    } else if (meta.source_type === "native_standard") {
      hint.textContent =
        "Renders via /api/reports/" +
        (meta.uid || "") +
        "/data.html using Central Hub .env credentials (never sent to the browser).";
    } else {
      hint.textContent = meta.description || "";
    }
    loadPeriodsForReport();
    var rememberedOu = loadRememberedOu();
    var presetOu = root.getAttribute("data-preset-ou") || "";
    if (rememberedOu && isValidOuUid(rememberedOu.id)) {
      setOuSelection(rememberedOu.id, rememberedOu.name || "");
    } else if (presetOu && isValidOuUid(presetOu)) {
      setOuSelection(presetOu, presetOu);
    } else if (ouHidden.value && !isValidOuUid(ouHidden.value)) {
      clearOuSelection();
    }
    validateForm();
  }

  function loadCatalog() {
    statusEl.textContent = "Loading report catalog…";
    var url =
      root.getAttribute("data-catalog-url") +
      "?environment=" +
      encodeURIComponent(envSel.value);
    return dedupeFetch("catalog:" + envSel.value, url)
      .then(function (data) {
        catalog = data;
        fillReports();
        statusEl.textContent =
          "Catalog: " +
          ((data.counts && data.counts.native_standard) || 0) +
          " native · " +
          ((data.counts && data.counts.app_shells) || 0) +
          " app shells" +
          (data.cache === "hit" ? " (cached)" : "");
      })
      .catch(function (err) {
        statusEl.textContent = String(err && err.message ? err.message : err);
      });
  }

  function generateAndView() {
    var meta = selectedMeta();
    if (!meta) {
      statusEl.textContent = "Select a report first.";
      return;
    }
    if (!validateForm()) {
      statusEl.textContent = "Complete required parameters before generating.";
      return;
    }
    var pe = periodValue();
    var ou = isValidOuUid(ouHidden.value) ? ouHidden.value.trim() : "";
    if (needsPeriod(meta) && !pe) {
      statusEl.textContent = "Period is required — select from the dropdown.";
      return;
    }
    if (needsOu(meta) && !ou) {
      statusEl.textContent = "Organisation unit is required — select from the picker.";
      return;
    }

    if (abortCtl) abortCtl.abort();
    abortCtl = typeof AbortController !== "undefined" ? new AbortController() : null;

    statusEl.textContent = "Generating…";
    skeleton.hidden = false;
    empty.hidden = true;
    viewer.hidden = true;
    previewEl.textContent = "Working…";

    var body = {
      report_id: reportSel.value,
      environment: envSel.value,
      period: pe,
      org_unit: ou,
      output_format: document.getElementById("dr-format").value || "html",
      confirm_live: !!(document.getElementById("dr-confirm-live") || {}).checked,
    };

    fetch(root.getAttribute("data-generate-view-url"), {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      credentials: "same-origin",
      body: JSON.stringify(body),
      signal: abortCtl ? abortCtl.signal : undefined,
    })
      .then(function (r) {
        return r.json().then(function (data) {
          return { ok: r.ok, status: r.status, data: data };
        });
      })
      .then(function (res) {
        var data = res.data || {};
        previewEl.textContent = JSON.stringify(
          {
            ok: data.ok,
            source_type: data.source_type,
            browser_only: data.browser_only,
            cache: data.cache,
            diagnostics: data.diagnostics || {},
            message: data.message || "",
            viewer_url: data.viewer_url || "",
            open_url: data.open_url || "",
            period: pe,
            org_unit: ou,
          },
          null,
          2
        );
        if (!res.ok || data.ok === false) {
          skeleton.hidden = true;
          empty.hidden = false;
          statusEl.textContent = data.error || "Generate failed.";
          return;
        }
        if (envSel.value === "live") liveConfirmedForRun = true;
        setActions({
          viewer: data.viewer_url || "",
          open: data.open_url || "",
          download: data.download_url || "",
          browserOnly: !!data.browser_only,
        });
        if (data.browser_only) {
          skeleton.hidden = true;
          empty.hidden = false;
          empty.textContent =
            data.message || "Open this item in DHIS2 — it is not a hub-rendered report.";
          statusEl.textContent = data.message || "Browser-only app shell.";
          if (data.open_url) window.open(data.open_url, "_blank", "noopener");
          return;
        }
        statusEl.textContent =
          "Status: completed" +
          (data.cache === "hit" ? " (cached)" : "") +
          (data.diagnostics && data.diagnostics.elapsed_ms
            ? " · " + data.diagnostics.elapsed_ms + " ms"
            : "");
        showViewer(data.viewer_url);
      })
      .catch(function (err) {
        if (err && err.name === "AbortError") return;
        skeleton.hidden = true;
        empty.hidden = false;
        statusEl.textContent = String(err && err.message ? err.message : err);
      });
  }

  // Init
  envSel.value = preferredEnv() === "live" ? "live" : "stage";
  syncLive();
  loadCatalog();
  loadRootTree();

  envSel.addEventListener("change", function () {
    liveConfirmedForRun = false;
    syncLive();
    // Clear OU — Stage/Live UIDs are not interchangeable.
    clearOuSelection();
    ouCache = {};
    loadCatalog();
    loadRootTree();
    loadPeriodsForReport();
    validateForm();
  });
  reportSel.addEventListener("change", onReportChange);
  document.getElementById("dr-confirm-live").addEventListener("change", validateForm);

  periodSearch.addEventListener("focus", function () {
    renderPeriodResults(periodSearch.value);
  });
  periodSearch.addEventListener("input", function () {
    // Typing does not set a period — only explicit selection does.
    if (periodHidden.value && periodSearch.value !== (periodById[periodHidden.value] || {}).label) {
      periodHidden.value = "";
    }
    clearTimeout(periodTimer);
    periodTimer = setTimeout(function () {
      renderPeriodResults(periodSearch.value);
      validateForm();
    }, 120);
  });
  periodSearch.addEventListener("keydown", function (ev) {
    if (ev.key === "Escape") {
      periodResults.hidden = true;
      periodSearch.setAttribute("aria-expanded", "false");
    }
  });

  ouSearch.addEventListener("focus", function () {
    searchOrgUnits(ouSearch.value);
  });
  ouSearch.addEventListener("input", function () {
    // Typed text is never submitted as OU.
    clearTimeout(ouTimer);
    ouTimer = setTimeout(function () {
      searchOrgUnits(ouSearch.value);
    }, 220);
  });
  if (ouClear) {
    ouClear.addEventListener("click", function () {
      clearOuSelection();
    });
  }

  document.addEventListener("click", function (ev) {
    if (!root.contains(ev.target)) return;
    if (!document.getElementById("dr-period-picker").contains(ev.target)) {
      periodResults.hidden = true;
      periodSearch.setAttribute("aria-expanded", "false");
    }
    if (!document.getElementById("dr-ou-picker").contains(ev.target)) {
      ouResults.hidden = true;
      ouSearch.setAttribute("aria-expanded", "false");
    }
  });

  genBtn.addEventListener("click", function () {
    generateAndView();
  });
  refreshBtn.addEventListener("click", function () {
    generateAndView();
  });
  fsBtn.addEventListener("click", function () {
    var wrap = document.getElementById("dr-viewer-wrap");
    if (wrap && wrap.requestFullscreen) wrap.requestFullscreen();
  });
  printBtn.addEventListener("click", function () {
    try {
      viewer.contentWindow.print();
    } catch (e) {
      window.print();
    }
  });

  document.getElementById("dr-save-preset").onclick = function () {
    var name = window.prompt("Preset name");
    if (!name) return;
    var pe = periodValue();
    var ou = isValidOuUid(ouHidden.value) ? ouHidden.value.trim() : "";
    fetch(root.getAttribute("data-preset-url"), {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      credentials: "same-origin",
      body: JSON.stringify({
        name: name,
        report_id: reportSel.value,
        environment: envSel.value,
        period: pe,
        org_unit: ou,
        output_format: document.getElementById("dr-format").value,
      }),
    })
      .then(function (r) {
        return r.json();
      })
      .then(function (data) {
        statusEl.textContent = data.ok ? "Preset saved." : data.error || "Save failed.";
      });
  };
})();
