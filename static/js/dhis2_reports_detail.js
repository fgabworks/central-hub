/**
 * DHIS2 Standard Report detail — Generate & View with reused period/OU controls.
 * Remembers period + OU per environment in localStorage (no credentials).
 */
(function () {
  var root = document.getElementById("dr-detail");
  if (!root) return;

  var env = root.getAttribute("data-environment") || "stage";
  var reportId = root.getAttribute("data-report-id") || "";
  var uid = root.getAttribute("data-uid") || "";
  var statusEl = document.getElementById("dr-status");
  var previewEl = document.getElementById("dr-preview");
  var genBtn = document.getElementById("dr-generate-view");
  var cancelBtn = document.getElementById("dr-cancel");
  var refreshBtn = document.getElementById("dr-refresh");
  var fsBtn = document.getElementById("dr-fullscreen");
  var printBtn = document.getElementById("dr-print");
  var downloadBtn = document.getElementById("dr-download");
  var openDhis2 = document.getElementById("dr-open-dhis2");
  var viewer = document.getElementById("dr-viewer");
  var skeleton = document.getElementById("dr-viewer-skeleton");
  var empty = document.getElementById("dr-viewer-empty");
  var elapsedEl = document.getElementById("dr-elapsed");
  var periodType = document.getElementById("dr-period-type");
  var periodQuarter = document.getElementById("dr-period-quarter");
  var periodMonth = document.getElementById("dr-period-month");
  var periodYear = document.getElementById("dr-period-year");
  var periodCustom = document.getElementById("dr-period-custom");
  var periodFilter = document.getElementById("dr-period-filter");
  var ouSearch = document.getElementById("dr-ou-search");
  var ouHidden = document.getElementById("dr-ou");
  var ouLabel = document.getElementById("dr-ou-label");
  var ouResults = document.getElementById("dr-ou-results");
  var ouTree = document.getElementById("dr-ou-tree");

  var quarters = [];
  var abortCtl = null;
  var elapsedTimer = null;
  var startedAt = 0;
  var last = { viewer: "", download: "", open: openDhis2 ? openDhis2.href : "" };
  var ouTimer = null;
  var requestKey = "";

  function storageKey(kind) {
    return "centralhub.dhis2.reports." + kind + "." + env;
  }

  function loadRemembered() {
    try {
      var pe = JSON.parse(localStorage.getItem(storageKey("period")) || "null");
      var ou = JSON.parse(localStorage.getItem(storageKey("ou")) || "null");
      return { period: pe, ou: ou };
    } catch (e) {
      return { period: null, ou: null };
    }
  }

  function savePeriod(id) {
    try {
      localStorage.setItem(storageKey("period"), JSON.stringify({ id: id, at: Date.now() }));
    } catch (e) {}
  }

  function saveOu(id, name) {
    try {
      localStorage.setItem(
        storageKey("ou"),
        JSON.stringify({ id: id, name: name || "", at: Date.now() })
      );
    } catch (e) {}
  }

  function periodRequired() {
    return root.getAttribute("data-period-required") === "1";
  }
  function ouRequired() {
    return root.getAttribute("data-ou-required") === "1";
  }

  function periodValue() {
    var type = periodType.value;
    if (type === "custom") return (periodCustom.value || "").trim();
    if (type === "yearly") return periodYear.value ? String(periodYear.value) : "";
    if (type === "quarterly") return periodQuarter.value || "";
    var m = periodMonth.value || "";
    return m.replace("-", "");
  }

  function syncPeriodUi() {
    var type = periodType.value;
    periodQuarter.hidden = type !== "quarterly";
    periodFilter.hidden = type !== "quarterly";
    periodMonth.hidden = type !== "monthly";
    periodYear.hidden = type !== "yearly";
    periodCustom.hidden = type !== "custom";
    validateForm();
  }

  function fillQuarters(selectedId) {
    var filter = (periodFilter.value || "").trim().toLowerCase();
    periodQuarter.innerHTML = "";
    quarters.forEach(function (q) {
      var hay = (q.id + " " + q.label).toLowerCase();
      if (filter && hay.indexOf(filter) < 0) return;
      var opt = document.createElement("option");
      opt.value = q.id;
      opt.textContent = q.label;
      periodQuarter.appendChild(opt);
    });
    if (selectedId) periodQuarter.value = selectedId;
    if (!periodQuarter.value && periodQuarter.options.length) {
      periodQuarter.selectedIndex = 0;
    }
  }

  function loadPeriods() {
    var remembered = loadRemembered().period;
    var rememberedId = (remembered && remembered.id) || root.getAttribute("data-initial-period") || "";
    var url =
      root.getAttribute("data-periods-url") +
      "?remembered=" +
      encodeURIComponent(rememberedId);
    return fetch(url, { credentials: "same-origin" })
      .then(function (r) {
        return r.json();
      })
      .then(function (data) {
        quarters = (data && data.quarters) || [];
        var preferred = root.getAttribute("data-preferred-period-type") || "quarterly";
        periodType.value = preferred;
        fillQuarters(rememberedId || (data && data.default_period) || "");
        if (periodYear && !periodYear.value) {
          periodYear.value = String(new Date().getFullYear());
        }
        syncPeriodUi();
      })
      .catch(function () {
        syncPeriodUi();
      });
  }

  function validateForm() {
    var ok = true;
    if (root.getAttribute("data-show-period") === "1" && periodRequired() && !periodValue()) {
      ok = false;
    }
    if (root.getAttribute("data-show-ou") === "1" && ouRequired() && !(ouHidden.value || "").trim()) {
      ok = false;
    }
    if (env === "live") {
      var box = document.getElementById("dr-confirm-live");
      if (!box || !box.checked) ok = false;
    }
    genBtn.disabled = !ok;
    return ok;
  }

  function setViewerActions(show) {
    refreshBtn.hidden = !show;
    fsBtn.hidden = !show;
    printBtn.hidden = !show;
    downloadBtn.hidden = !show || !last.download;
    if (openDhis2 && last.open) {
      openDhis2.hidden = false;
      openDhis2.href = last.open;
    }
  }

  function showSkeleton(on) {
    skeleton.hidden = !on;
    empty.hidden = on || !viewer.hidden;
    if (!on) {
      clearInterval(elapsedTimer);
      elapsedTimer = null;
    }
  }

  function startElapsed() {
    startedAt = Date.now();
    clearInterval(elapsedTimer);
    elapsedTimer = setInterval(function () {
      if (elapsedEl) elapsedEl.textContent = ((Date.now() - startedAt) / 1000).toFixed(1) + "s";
    }, 200);
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
      encodeURIComponent(env) +
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
          btn.textContent =
            (row.name || row.id) +
            (row.code ? " · " + row.code : "") +
            " · " +
            row.id +
            (row.path ? " · " + row.path : "");
          btn.addEventListener("click", function () {
            ouHidden.value = row.id;
            ouLabel.textContent = (row.name || row.id) + (row.path ? " — " + row.path : "");
            saveOu(row.id, row.name || "");
            ouResults.hidden = true;
            validateForm();
          });
          li.appendChild(btn);
          ouResults.appendChild(li);
        });
        ouResults.hidden = false;
      })
      .catch(function () {
        ouResults.hidden = true;
      });
  }

  function loadRootTree() {
    if (!ouTree) return;
    var url =
      root.getAttribute("data-org-units-url") +
      "?environment=" +
      encodeURIComponent(env) +
      "&limit=20";
    fetch(url, { credentials: "same-origin" })
      .then(function (r) {
        return r.json();
      })
      .then(function (data) {
        var rows = (data && data.org_units) || [];
        if (!rows.length) {
          ouTree.hidden = true;
          return;
        }
        ouTree.innerHTML = "";
        rows.slice(0, 12).forEach(function (row) {
          ouTree.appendChild(treeNode(row));
        });
        ouTree.hidden = false;
      });
  }

  function treeNode(row) {
    var wrap = document.createElement("div");
    wrap.className = "dr-ou-node";
    var head = document.createElement("div");
    head.className = "dr-ou-node-head";
    var expand = document.createElement("button");
    expand.type = "button";
    expand.className = "btn btn-sm";
    expand.textContent = row.has_children ? "+" : "·";
    expand.disabled = !row.has_children;
    var pick = document.createElement("button");
    pick.type = "button";
    pick.className = "btn btn-sm";
    pick.textContent = row.name || row.id;
    pick.title = row.path || row.id;
    pick.addEventListener("click", function () {
      ouHidden.value = row.id;
      ouLabel.textContent = (row.name || row.id) + (row.path ? " — " + row.path : "");
      saveOu(row.id, row.name || "");
      validateForm();
    });
    var kids = document.createElement("div");
    kids.className = "dr-ou-children";
    kids.hidden = true;
    expand.addEventListener("click", function () {
      if (!kids.hidden) {
        kids.hidden = true;
        expand.textContent = "+";
        return;
      }
      if (kids.dataset.loaded === "1") {
        kids.hidden = false;
        expand.textContent = "−";
        return;
      }
      expand.disabled = true;
      var url =
        root.getAttribute("data-org-units-url") +
        "?environment=" +
        encodeURIComponent(env) +
        "&parent_id=" +
        encodeURIComponent(row.id) +
        "&limit=40";
      fetch(url, { credentials: "same-origin" })
        .then(function (r) {
          return r.json();
        })
        .then(function (data) {
          kids.innerHTML = "";
          ((data && data.org_units) || []).forEach(function (child) {
            kids.appendChild(treeNode(child));
          });
          kids.dataset.loaded = "1";
          kids.hidden = false;
          expand.textContent = "−";
          expand.disabled = false;
        })
        .catch(function () {
          expand.disabled = false;
        });
    });
    head.appendChild(expand);
    head.appendChild(pick);
    wrap.appendChild(head);
    wrap.appendChild(kids);
    return wrap;
  }

  function cancelInflight() {
    if (abortCtl) {
      try {
        abortCtl.abort();
      } catch (e) {}
      abortCtl = null;
    }
    cancelBtn.hidden = true;
    showSkeleton(false);
  }

  function generateAndView(forceRefresh) {
    if (!validateForm()) {
      statusEl.textContent = "Fill required parameters first.";
      return;
    }
    var pe = periodValue();
    var ou = (ouHidden.value || "").trim();
    var key = env + "|" + uid + "|" + pe + "|" + ou;
    if (requestKey === key && abortCtl) return;
    cancelInflight();
    requestKey = key;
    abortCtl = typeof AbortController !== "undefined" ? new AbortController() : null;
    cancelBtn.hidden = false;
    showSkeleton(true);
    viewer.hidden = true;
    empty.hidden = true;
    startElapsed();
    statusEl.textContent = forceRefresh ? "Refreshing…" : "Generating…";
    if (pe) savePeriod(pe);

    var body = {
      report_id: reportId,
      environment: env,
      period: pe,
      org_unit: ou,
      output_format: "html",
      confirm_live: env === "live" && !!(document.getElementById("dr-confirm-live") || {}).checked,
    };
    var opts = {
      method: "POST",
      credentials: "same-origin",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    };
    if (abortCtl) opts.signal = abortCtl.signal;

    fetch(root.getAttribute("data-generate-view-url"), opts)
      .then(function (r) {
        return r.json().then(function (j) {
          return { ok: r.ok, j: j };
        });
      })
      .then(function (res) {
        cancelBtn.hidden = true;
        abortCtl = null;
        requestKey = "";
        showSkeleton(false);
        if (!res.ok || !res.j || !res.j.ok) {
          statusEl.textContent = (res.j && res.j.error) || "Generate failed";
          empty.hidden = false;
          return;
        }
        if (res.j.browser_only) {
          statusEl.textContent = res.j.message || "Open in DHIS2 (browser only).";
          if (res.j.open_url) window.open(res.j.open_url, "_blank", "noopener");
          return;
        }
        last.viewer = res.j.viewer_url || "";
        last.download = res.j.download_url || "";
        last.open = res.j.open_url || last.open;
        setViewerActions(!!last.viewer);
        if (last.download) downloadBtn.href = last.download;
        if (last.viewer) {
          viewer.hidden = false;
          empty.hidden = true;
          viewer.src = last.viewer + (forceRefresh ? ((last.viewer.indexOf("?") >= 0 ? "&" : "?") + "_ts=" + Date.now()) : "");
        }
        var diag = res.j.diagnostics || {};
        var timings = diag.timings || res.j.timings || {};
        statusEl.textContent =
          "Ready" +
          (diag.cache || res.j.cache ? " · cache " + (diag.cache || res.j.cache) : "") +
          (timings.total_ms != null ? " · " + timings.total_ms + " ms" : "");
        if (previewEl) {
          previewEl.textContent = JSON.stringify(
            {
              viewer_url: last.viewer,
              cache: diag.cache || res.j.cache,
              timings: timings,
              fingerprint: res.j.fingerprint || diag.fingerprint,
              period: pe,
              org_unit: ou,
            },
            null,
            2
          );
        }
      })
      .catch(function (err) {
        cancelBtn.hidden = true;
        abortCtl = null;
        requestKey = "";
        showSkeleton(false);
        if (err && err.name === "AbortError") {
          statusEl.textContent = "Cancelled.";
          return;
        }
        statusEl.textContent = String(err && err.message ? err.message : err);
        empty.hidden = false;
      });
  }

  // Events
  periodType.addEventListener("change", syncPeriodUi);
  periodQuarter.addEventListener("change", validateForm);
  periodMonth.addEventListener("change", validateForm);
  periodYear.addEventListener("input", validateForm);
  periodCustom.addEventListener("input", validateForm);
  periodFilter.addEventListener("input", function () {
    fillQuarters(periodQuarter.value);
  });
  ouSearch.addEventListener("input", function () {
    clearTimeout(ouTimer);
    ouTimer = setTimeout(function () {
      searchOrgUnits(ouSearch.value.trim());
    }, 220);
  });
  genBtn.addEventListener("click", function () {
    generateAndView(false);
  });
  cancelBtn.addEventListener("click", cancelInflight);
  refreshBtn.addEventListener("click", function () {
    generateAndView(true);
  });
  fsBtn.addEventListener("click", function () {
    if (viewer.requestFullscreen) viewer.requestFullscreen();
  });
  printBtn.addEventListener("click", function () {
    try {
      viewer.contentWindow.print();
    } catch (e) {
      window.open(last.viewer, "_blank", "noopener");
    }
  });
  var liveBox = document.getElementById("dr-confirm-live");
  if (liveBox) liveBox.addEventListener("change", validateForm);

  document.getElementById("dr-refresh-meta").addEventListener("click", function () {
    var confirmLive =
      env !== "live" ||
      (document.getElementById("dr-confirm-live") &&
        document.getElementById("dr-confirm-live").checked);
    if (env === "live" && !confirmLive) {
      statusEl.textContent = "Confirm Live first.";
      return;
    }
    statusEl.textContent = "Refreshing metadata…";
    fetch("/api/dhis2/reports/standard/" + env + "/" + uid + "/refresh", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ confirm_live: !!confirmLive }),
    })
      .then(function (r) {
        return r.json().then(function (j) {
          return { ok: r.ok, j: j };
        });
      })
      .then(function (res) {
        if (!res.ok) {
          statusEl.textContent = (res.j && res.j.error) || "Refresh failed";
          return;
        }
        location.reload();
      });
  });

  // Restore remembered OU
  var remembered = loadRemembered();
  if (remembered.ou && remembered.ou.id && !ouHidden.value) {
    ouHidden.value = remembered.ou.id;
    ouLabel.textContent = remembered.ou.name || remembered.ou.id;
  } else if (ouHidden.value) {
    ouLabel.textContent = ouHidden.value;
  }

  loadPeriods().then(function () {
    validateForm();
  });
  loadRootTree();
  validateForm();
})();
