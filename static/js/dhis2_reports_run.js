/**
 * DHIS2 Reports — Generate & View (credentialed rendering bridge).
 */
(function () {
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
  var ouSearch = document.getElementById("dr-ou-search");
  var ouHidden = document.getElementById("dr-ou");
  var ouLabel = document.getElementById("dr-ou-label");
  var ouResults = document.getElementById("dr-ou-results");

  var catalog = null;
  var byId = {};
  var last = { viewer: "", open: "", download: "", browserOnly: false };
  var abortCtl = null;
  var ouTimer = null;
  var liveConfirmedForRun = false;

  function preferredEnv() {
    return root.getAttribute("data-preferred-env") || "stage";
  }

  function selectedMeta() {
    return byId[reportSel.value] || null;
  }

  function syncLive() {
    liveBox.hidden = envSel.value !== "live";
    if (envSel.value !== "live") {
      document.getElementById("dr-confirm-live").checked = false;
      liveConfirmedForRun = false;
    }
  }

  function periodValue() {
    var type = document.getElementById("dr-period-type").value;
    if (type === "custom") return document.getElementById("dr-period").value.trim();
    if (type === "yearly") {
      var y = document.getElementById("dr-period-year").value;
      return y ? String(y) : "";
    }
    if (type === "quarterly") {
      var yq = document.getElementById("dr-period-year").value;
      var q = document.getElementById("dr-period-quarter").value;
      return yq ? yq + "Q" + q : "";
    }
    // monthly from input type=month → YYYY-MM → YYYYMM
    var m = document.getElementById("dr-period-month").value || "";
    return m.replace("-", "");
  }

  function syncPeriodUi() {
    var type = document.getElementById("dr-period-type").value;
    document.getElementById("dr-period-month").hidden = type !== "monthly";
    document.getElementById("dr-period-quarter").hidden = type !== "quarterly";
    document.getElementById("dr-period-year").hidden = type !== "yearly" && type !== "quarterly";
    document.getElementById("dr-period").hidden = type !== "custom";
    validateForm();
  }

  function validateForm() {
    var meta = selectedMeta();
    var ok = !!(meta && !meta.browser_only && meta.render_supported !== false);
    if (meta && meta.browser_only) ok = false;
    if (meta && meta.needs_period && !periodValue()) ok = false;
    if (meta && meta.needs_org_unit && !(ouHidden.value || "").trim()) ok = false;
    if (envSel.value === "live" && !document.getElementById("dr-confirm-live").checked) ok = false;
    if (meta && meta.source_type === "dhis2_app_shell") {
      // Allow Open in DHIS2 path via generate which returns browser_only
      ok = true;
    }
    genBtn.disabled = !meta;
    if (meta && meta.browser_only) {
      genBtn.textContent = "Open in DHIS2";
    } else {
      genBtn.textContent = "Generate & View";
      if (!ok && meta) genBtn.disabled = true;
      else if (meta && !meta.browser_only) genBtn.disabled = !ok;
    }
    return !genBtn.disabled;
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
    // View Report only when cached/viewer output already exists
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
    validateForm();
  }

  function loadCatalog() {
    statusEl.textContent = "Loading report catalog…";
    var url =
      root.getAttribute("data-catalog-url") +
      "?environment=" +
      encodeURIComponent(envSel.value);
    return fetch(url, { credentials: "same-origin" })
      .then(function (r) {
        return r.json();
      })
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

  function searchOrgUnits(q) {
    if (!q || q.length < 2) {
      ouResults.hidden = true;
      ouResults.innerHTML = "";
      return;
    }
    var url =
      root.getAttribute("data-org-units-url") +
      "?environment=" +
      encodeURIComponent(envSel.value) +
      "&q=" +
      encodeURIComponent(q);
    fetch(url, { credentials: "same-origin" })
      .then(function (r) {
        return r.json();
      })
      .then(function (data) {
        var rows = (data && data.org_units) || [];
        ouResults.innerHTML = "";
        if (!rows.length) {
          ouResults.hidden = true;
          return;
        }
        rows.forEach(function (row) {
          var li = document.createElement("li");
          var btn = document.createElement("button");
          btn.type = "button";
          btn.textContent = row.name + " (" + row.id + ")";
          btn.onclick = function () {
            ouHidden.value = row.id;
            ouLabel.textContent = row.name + " · " + row.id;
            ouSearch.value = row.name;
            ouResults.hidden = true;
            validateForm();
          };
          li.appendChild(btn);
          ouResults.appendChild(li);
        });
        ouResults.hidden = false;
      })
      .catch(function () {
        ouResults.hidden = true;
      });
  }

  function generateAndView(forceRefresh) {
    var meta = selectedMeta();
    if (!meta) {
      statusEl.textContent = "Select a report first.";
      return;
    }
    if (envSel.value === "live" && !document.getElementById("dr-confirm-live").checked) {
      statusEl.textContent = "Confirm Live before running.";
      return;
    }
    if (meta.needs_period && !periodValue()) {
      statusEl.textContent = "Period is required for this report.";
      return;
    }
    if (meta.needs_org_unit && !ouHidden.value) {
      statusEl.textContent = "Organisation unit is required for this report.";
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
      period: periodValue(),
      org_unit: ouHidden.value,
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
          empty.textContent = data.message || "Open this item in DHIS2 — it is not a hub-rendered report.";
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

  // Init period defaults
  (function initPeriod() {
    var now = new Date();
    var ym =
      now.getFullYear() +
      "-" +
      String(now.getMonth() + 1).padStart(2, "0");
    document.getElementById("dr-period-month").value = ym;
    document.getElementById("dr-period-year").value = String(now.getFullYear());
    var presetPe = root.getAttribute("data-preset-period") || "";
    if (presetPe) {
      document.getElementById("dr-period-type").value = "custom";
      document.getElementById("dr-period").value = presetPe;
    }
    var presetOu = root.getAttribute("data-preset-ou") || "";
    if (presetOu) {
      ouHidden.value = presetOu;
      ouLabel.textContent = presetOu;
    }
    syncPeriodUi();
  })();

  envSel.value = preferredEnv() === "live" ? "live" : "stage";
  syncLive();
  loadCatalog();

  envSel.addEventListener("change", function () {
    liveConfirmedForRun = false;
    syncLive();
    loadCatalog();
    validateForm();
  });
  reportSel.addEventListener("change", onReportChange);
  document.getElementById("dr-period-type").addEventListener("change", syncPeriodUi);
  ["dr-period-month", "dr-period-quarter", "dr-period-year", "dr-period", "dr-confirm-live"].forEach(
    function (id) {
      var el = document.getElementById(id);
      if (el) el.addEventListener("change", validateForm);
      if (el) el.addEventListener("input", validateForm);
    }
  );

  ouSearch.addEventListener("input", function () {
    clearTimeout(ouTimer);
    var q = ouSearch.value.trim();
    ouTimer = setTimeout(function () {
      searchOrgUnits(q);
    }, 220);
  });

  genBtn.addEventListener("click", function () {
    generateAndView(false);
  });
  refreshBtn.addEventListener("click", function () {
    generateAndView(true);
  });
  fsBtn.addEventListener("click", function () {
    var wrap = document.getElementById("dr-viewer-wrap");
    if (!wrap) return;
    if (wrap.requestFullscreen) wrap.requestFullscreen();
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
    fetch(root.getAttribute("data-preset-url"), {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      credentials: "same-origin",
      body: JSON.stringify({
        name: name,
        report_id: reportSel.value,
        environment: envSel.value,
        period: periodValue(),
        org_unit: ouHidden.value,
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
