/**
 * Live Data Export UI — New Export → Preview → Generate (no auto-export on load).
 */
(function () {
  "use strict";

  function $(sel, root) {
    return (root || document).querySelector(sel);
  }

  function $all(sel, root) {
    return Array.prototype.slice.call((root || document).querySelectorAll(sel));
  }

  function parseBoot() {
    var el = document.getElementById("lex-bootstrap");
    if (!el) return {};
    try {
      return JSON.parse(el.textContent || "{}");
    } catch (e) {
      return {};
    }
  }

  function setState(text) {
    var el = $("#lex-state");
    if (el) el.textContent = text;
  }

  function setError(msg) {
    var el = $("#lex-error");
    if (!el) return;
    if (!msg) {
      el.hidden = true;
      el.textContent = "";
      return;
    }
    el.hidden = false;
    el.textContent = msg;
  }

  function fmtBytes(n) {
    n = Number(n) || 0;
    if (n < 1024) return n + " B";
    if (n < 1024 * 1024) return (n / 1024).toFixed(1) + " KB";
    return (n / (1024 * 1024)).toFixed(1) + " MB";
  }

  function init() {
    var root = document.getElementById("lex-root");
    if (!root) return;
    var boot = parseBoot();
    var sources = boot.sources || [];
    var defaults = boot.defaults || {};
    var lastPreview = null;
    var ouPicker = null;
    var pollTimer = null;

    var sourceSel = $("#lex-source");
    var envSel = $("#lex-env");
    var quarterSel = $("#lex-quarter");
    var formatSel = $("#lex-format");
    var rowLimit = $("#lex-row-limit");
    var statusSel = $("#lex-status");
    var ipSel = $("#lex-ip");
    var colList = $("#lex-column-list");
    var descEl = $("#lex-source-desc");
    var previewBtn = $("#lex-preview-btn");
    var exportBtn = $("#lex-export-btn");
    var previewBox = $("#lex-preview");
    var downloadBox = $("#lex-download");

    // Tabs
    $all(".lex-tab", root).forEach(function (tab) {
      tab.addEventListener("click", function () {
        var name = tab.getAttribute("data-tab");
        $all(".lex-tab", root).forEach(function (t) {
          t.classList.toggle("is-active", t === tab);
          t.setAttribute("aria-selected", t === tab ? "true" : "false");
        });
        $all(".lex-panel", root).forEach(function (p) {
          var on = p.getAttribute("data-panel") === name;
          p.hidden = !on;
          p.classList.toggle("is-active", on);
        });
        if (name === "jobs") refreshJobs();
        if (name === "presets") renderPresets(boot.presets || []);
        if (name === "history") refreshHistory();
        if (name === "settings") renderSettings();
      });
    });

    function currentSource() {
      var key = sourceSel.value;
      for (var i = 0; i < sources.length; i++) {
        if (sources[i].source_key === key) return sources[i];
      }
      return null;
    }

    function fillSources() {
      sourceSel.innerHTML = "";
      sources.forEach(function (s) {
        var opt = document.createElement("option");
        opt.value = s.source_key;
        opt.textContent = s.display_name + (s.available ? "" : " (unavailable)");
        opt.disabled = !s.available;
        sourceSel.appendChild(opt);
      });
      var first = sources.find(function (s) { return s.available; });
      if (first) sourceSel.value = first.source_key;
      onSourceChange();
    }

    function supported(src, name) {
      return src && (src.filters_supported || []).indexOf(name) >= 0;
    }

    function onSourceChange() {
      var src = currentSource();
      lastPreview = null;
      exportBtn.disabled = true;
      previewBox.hidden = true;
      downloadBox.hidden = true;
      setError("");
      setState(src && src.available ? "Ready to preview" : "Select an available source");
      descEl.textContent = src ? (src.description || src.unavailable_reason || "") : "";

      $all("[data-filter]", root).forEach(function (el) {
        var f = el.getAttribute("data-filter");
        var show = false;
        if (!src) show = false;
        else if (f === "date_range") show = !!(src.date_column);
        else show = supported(src, f) || (f === "columns" && src.available);
        el.hidden = !show;
      });

      // columns
      colList.innerHTML = "";
      if (src && src.available) {
        var defaultsCols = src.default_columns || [];
        (src.allowed_columns || []).forEach(function (c) {
          if ((src.excluded_columns || []).indexOf(c) >= 0) return;
          if ((src.sensitive_columns || []).indexOf(c) >= 0) return;
          var lab = document.createElement("label");
          var cb = document.createElement("input");
          cb.type = "checkbox";
          cb.value = c;
          cb.checked = defaultsCols.indexOf(c) >= 0 || !defaultsCols.length;
          lab.appendChild(cb);
          lab.appendChild(document.createTextNode(c));
          colList.appendChild(lab);
        });
        rowLimit.value = Math.min(Number(src.maximum_rows) || 1000, 1000);
        rowLimit.max = src.maximum_rows || defaults.max_rows_hard || 100000;
        // formats
        formatSel.innerHTML = "";
        (src.supported_formats || ["csv"]).forEach(function (f) {
          var o = document.createElement("option");
          o.value = f;
          o.textContent = f === "csv_gz" ? "Compressed CSV" : f.toUpperCase();
          formatSel.appendChild(o);
        });
      }
      updateGenerateEnabled();
    }

    function selectedColumns() {
      return $all("#lex-column-list input:checked").map(function (el) { return el.value; });
    }

    function gatherFilters() {
      var src = currentSource();
      var filters = {
        environment: envSel.value,
        row_limit: Number(rowLimit.value) || undefined,
      };
      if (supported(src, "quarter") || (src && (src.required_filters || []).indexOf("quarter") >= 0)) {
        filters.quarter = quarterSel.value;
      }
      if (supported(src, "status") && statusSel.value) filters.status = statusSel.value;
      if (supported(src, "ip_flag") && ipSel.value) filters.ip_flag = ipSel.value;
      if (src && src.date_column) {
        var df = $("#lex-date-from").value;
        var dt = $("#lex-date-to").value;
        if (df) filters.date_from = df;
        if (dt) filters.date_to = dt;
      }
      if (supported(src, "organisation_unit")) {
        var uid = ($("#lex-ou").value || "").trim();
        if (uid) {
          filters.organisation_unit = { uid: uid, name: ($("#lex-ou-path").textContent || "").trim() };
        }
      }
      return filters;
    }

    function requiredOk() {
      var src = currentSource();
      if (!src || !src.available) return false;
      var req = src.required_filters || [];
      var filters = gatherFilters();
      for (var i = 0; i < req.length; i++) {
        if (req[i] === "quarter" && !filters.quarter) return false;
      }
      if (!selectedColumns().length) return false;
      return true;
    }

    function updateGenerateEnabled() {
      exportBtn.disabled = !(lastPreview && requiredOk());
    }

    async function loadQuarters() {
      quarterSel.innerHTML = "";
      var fallback = ["2025Q1", "2025Q2", "2025Q3", "2025Q4"];
      try {
        var url = root.getAttribute("data-periods-url") + "?environment=" + encodeURIComponent(envSel.value);
        var res = await fetch(url, { headers: { Accept: "application/json" } });
        var data = await res.json();
        var periods = (data && (data.periods || data.items || data.quarters)) || [];
        if (!periods.length) periods = fallback;
        periods.forEach(function (p) {
          var val = typeof p === "string" ? p : (p.id || p.code || p.period || "");
          if (!val) return;
          var o = document.createElement("option");
          o.value = val;
          o.textContent = typeof p === "string" ? p : (p.label || p.name || val);
          quarterSel.appendChild(o);
        });
      } catch (e) {
        fallback.forEach(function (q) {
          var o = document.createElement("option");
          o.value = q;
          o.textContent = q;
          quarterSel.appendChild(o);
        });
      }
    }

    function wireOu() {
      if (!window.HubOrgUnitPicker || !window.HubOrgUnitPicker.createPicker) return;
      ouPicker = window.HubOrgUnitPicker.createPicker({
        root: $("#lex-ou-block"),
        hiddenEl: $("#lex-ou"),
        pathEl: $("#lex-ou-path"),
        chipRow: null,
        chipLabel: $("#lex-ou-path"),
        clearBtn: $("#lex-ou-clear"),
        retryBtn: null,
        refreshMetaBtn: $("#lex-ou-refresh-meta"),
        errorEl: $("#lex-ou-error"),
        syncEl: $("#lex-ou-sync"),
        searchEl: $("#lex-ou-search"),
        searchResultsEl: $("#lex-ou-search-results"),
        apiUrl: root.getAttribute("data-org-units-url"),
        getEnvironment: function () {
          var e = envSel.value;
          return e === "dev" ? "stage" : e;
        },
        storagePrefix: "centralhub.lex.ou.",
        onChange: function () {
          lastPreview = null;
          exportBtn.disabled = true;
          updateGenerateEnabled();
        },
      });
      if (ouPicker && ouPicker.loadRoots) ouPicker.loadRoots();
    }

    async function postJson(url, body) {
      var res = await fetch(url, {
        method: "POST",
        headers: { "Content-Type": "application/json", Accept: "application/json" },
        body: JSON.stringify(body || {}),
      });
      var data = await res.json().catch(function () { return {}; });
      if (!res.ok) throw new Error(data.error || ("Request failed (" + res.status + ")"));
      return data;
    }

    previewBtn.addEventListener("click", async function () {
      setError("");
      downloadBox.hidden = true;
      if (!requiredOk()) {
        setError("Complete required filters and select at least one column.");
        return;
      }
      setState("Counting rows");
      previewBtn.disabled = true;
      try {
        var data = await postJson(root.getAttribute("data-preview-url"), {
          source_key: sourceSel.value,
          filters: gatherFilters(),
          columns: selectedColumns(),
        });
        lastPreview = data;
        renderPreview(data);
        setState("Preview ready");
        updateGenerateEnabled();
      } catch (err) {
        setState("Ready to preview");
        setError(err.message || String(err));
        lastPreview = null;
        exportBtn.disabled = true;
      } finally {
        previewBtn.disabled = false;
      }
    });

    exportBtn.addEventListener("click", async function () {
      setError("");
      if (!requiredOk() || !lastPreview) {
        setError("Run Preview before generating an export.");
        return;
      }
      setState("Export queued");
      exportBtn.disabled = true;
      try {
        var data = await postJson(root.getAttribute("data-export-url"), {
          source_key: sourceSel.value,
          filters: gatherFilters(),
          columns: selectedColumns(),
          format: formatSel.value,
        });
        var job = data.job || {};
        if (data.mode === "async" || job.status === "queued" || job.status === "reading" || job.status === "writing") {
          setState("Exporting");
          pollJob(job.id);
        } else if (job.status === "ready") {
          showDownload(job);
          setState("Ready to download");
        } else {
          setState(job.state || job.status || "Failed");
          setError(job.error || data.error || "Export failed");
        }
        refreshJobs();
      } catch (err) {
        setState("Failed");
        setError(err.message || String(err));
      } finally {
        updateGenerateEnabled();
      }
    });

    function renderPreview(data) {
      previewBox.hidden = false;
      var meta = $("#lex-preview-meta");
      meta.innerHTML = "";
      [
        ["Source", data.display_name || data.source_key],
        ["Description", data.description || ""],
        ["Filters", JSON.stringify(data.filters || {})],
        ["Columns", (data.columns || []).join(", ")],
        ["Estimated rows", String(data.estimated_rows ?? "")],
        ["Estimated size", fmtBytes(data.estimated_bytes)],
      ].forEach(function (pair) {
        var li = document.createElement("li");
        li.textContent = pair[0] + ": " + pair[1];
        meta.appendChild(li);
      });
      var warn = $("#lex-warnings");
      if (data.warnings && data.warnings.length) {
        warn.hidden = false;
        warn.innerHTML = data.warnings.map(function (w) { return "<div>" + escapeHtml(w) + "</div>"; }).join("");
      } else {
        warn.hidden = true;
        warn.innerHTML = "";
      }
      var table = $("#lex-preview-table");
      var thead = table.querySelector("thead");
      var tbody = table.querySelector("tbody");
      thead.innerHTML = "";
      tbody.innerHTML = "";
      var trh = document.createElement("tr");
      (data.columns || []).forEach(function (c) {
        var th = document.createElement("th");
        th.textContent = c;
        trh.appendChild(th);
      });
      thead.appendChild(trh);
      (data.sample_rows || []).forEach(function (row) {
        var tr = document.createElement("tr");
        (data.columns || []).forEach(function (_c, i) {
          var td = document.createElement("td");
          td.textContent = row[i] == null ? "" : String(row[i]);
          tr.appendChild(td);
        });
        tbody.appendChild(tr);
      });
    }

    function escapeHtml(s) {
      return String(s)
        .replace(/&/g, "&amp;")
        .replace(/</g, "&lt;")
        .replace(/>/g, "&gt;");
    }

    function showDownload(job) {
      downloadBox.hidden = false;
      var link = $("#lex-download-link");
      var url = root.getAttribute("data-download-url").replace("__ID__", job.id);
      link.href = url + "?token=" + encodeURIComponent(job.download_token || "");
      $("#lex-download-meta").textContent =
        (job.exported_rows != null ? job.exported_rows + " rows · " : "") +
        fmtBytes(job.file_size) +
        (job.expires_at ? " · expires " + job.expires_at : "");
    }

    function pollJob(jobId) {
      if (pollTimer) clearInterval(pollTimer);
      pollTimer = setInterval(async function () {
        try {
          var url = root.getAttribute("data-job-url").replace("__ID__", jobId);
          var res = await fetch(url, { headers: { Accept: "application/json" } });
          var data = await res.json();
          var job = data.job || {};
          if (job.status === "ready") {
            clearInterval(pollTimer);
            pollTimer = null;
            showDownload(job);
            setState("Ready to download");
            refreshJobs();
          } else if (job.status === "failed" || job.status === "cancelled" || job.status === "expired") {
            clearInterval(pollTimer);
            pollTimer = null;
            setState(job.state || job.status);
            setError(job.error || "");
            refreshJobs();
          } else {
            setState("Exporting");
          }
        } catch (e) {
          /* keep polling briefly */
        }
      }, 1200);
    }

    async function refreshJobs() {
      try {
        var res = await fetch(root.getAttribute("data-jobs-url"), { headers: { Accept: "application/json" } });
        var data = await res.json();
        renderJobs(data.jobs || []);
      } catch (e) {
        $("#lex-jobs").textContent = "Could not load jobs.";
      }
    }

    function renderJobs(jobs) {
      var box = $("#lex-jobs");
      if (!jobs.length) {
        box.innerHTML = "<p class='muted'>No export jobs yet.</p>";
        return;
      }
      box.innerHTML = jobs.map(function (j) {
        var actions = "";
        if (j.status === "ready" && j.download_token) {
          var dl = root.getAttribute("data-download-url").replace("__ID__", j.id) +
            "?token=" + encodeURIComponent(j.download_token);
          actions += "<a class='btn btn-sm' href='" + dl + "'>Download</a> ";
        }
        if (j.status === "queued" || j.status === "reading" || j.status === "writing") {
          actions += "<button type='button' class='btn btn-sm lex-cancel' data-id='" + j.id + "'>Cancel</button>";
        }
        return (
          "<div class='lex-card'>" +
          "<h3>" + escapeHtml(j.source_key) + " · " + escapeHtml(j.status) + "</h3>" +
          "<div class='muted'>" +
          escapeHtml(j.environment) + " · " + escapeHtml(j.format) +
          " · rows " + (j.exported_rows != null ? j.exported_rows : "—") +
          "/" + (j.estimated_rows != null ? j.estimated_rows : "—") +
          " · " + fmtBytes(j.file_size) +
          (j.expires_at ? " · exp " + escapeHtml(j.expires_at) : "") +
          "</div>" +
          "<div class='lex-actions'>" + actions + "</div>" +
          (j.error ? "<div class='lex-error'>" + escapeHtml(j.error) + "</div>" : "") +
          "</div>"
        );
      }).join("");
      $all(".lex-cancel", box).forEach(function (btn) {
        btn.addEventListener("click", async function () {
          var id = btn.getAttribute("data-id");
          var url = root.getAttribute("data-cancel-url").replace("__ID__", id);
          await fetch(url, { method: "POST", headers: { Accept: "application/json" } });
          refreshJobs();
        });
      });
    }

    function renderPresets(presets) {
      var box = $("#lex-presets");
      if (!presets.length) {
        box.innerHTML = "<p class='muted'>No saved presets.</p>";
        return;
      }
      box.innerHTML = presets.map(function (p) {
        return (
          "<div class='lex-card'>" +
          "<h3>" + escapeHtml(p.name) + "</h3>" +
          "<div class='muted'>" + escapeHtml(p.source_key) + " · " + escapeHtml(p.environment) + " · " + escapeHtml(p.format) + "</div>" +
          "<button type='button' class='btn btn-sm lex-load-preset' data-id='" + escapeHtml(p.id) + "'>Load</button> " +
          "<button type='button' class='btn btn-sm lex-del-preset' data-id='" + escapeHtml(p.id) + "'>Delete</button>" +
          "</div>"
        );
      }).join("");
      var byId = {};
      presets.forEach(function (p) { byId[p.id] = p; });
      $all(".lex-load-preset", box).forEach(function (btn) {
        btn.addEventListener("click", function () {
          var p = byId[btn.getAttribute("data-id")];
          if (!p) return;
          sourceSel.value = p.source_key;
          onSourceChange();
          envSel.value = p.environment || "dev";
          if (p.filters && p.filters.quarter) quarterSel.value = p.filters.quarter;
          if (p.filters && p.filters.status) statusSel.value = p.filters.status;
          if (p.filters && p.filters.ip_flag) ipSel.value = p.filters.ip_flag;
          if (p.filters && p.filters.row_limit) rowLimit.value = p.filters.row_limit;
          formatSel.value = p.format || "csv";
          $all("#lex-column-list input").forEach(function (cb) {
            cb.checked = (p.columns || []).indexOf(cb.value) >= 0;
          });
          $all(".lex-tab", root).forEach(function (t) {
            if (t.getAttribute("data-tab") === "new") t.click();
          });
        });
      });
      $all(".lex-del-preset", box).forEach(function (btn) {
        btn.addEventListener("click", async function () {
          await fetch(root.getAttribute("data-presets-url") + "/" + btn.getAttribute("data-id"), {
            method: "DELETE",
          });
          var res = await fetch(root.getAttribute("data-presets-url"));
          var data = await res.json();
          boot.presets = data.presets || [];
          renderPresets(boot.presets);
        });
      });
    }

    async function refreshHistory() {
      try {
        var res = await fetch(root.getAttribute("data-history-url"));
        var data = await res.json();
        var box = $("#lex-history");
        var items = data.history || [];
        if (!items.length) {
          box.innerHTML = "<p class='muted'>No history yet.</p>";
          return;
        }
        box.innerHTML = items.map(function (h) {
          return (
            "<div class='lex-card'><h3>" + escapeHtml(h.event) + "</h3>" +
            "<div class='muted'>" + escapeHtml(h.created_at) + " · " + escapeHtml(h.actor) +
            (h.job_id ? " · " + escapeHtml(h.job_id) : "") + "</div>" +
            "<pre class='muted' style='white-space:pre-wrap;margin:0.35rem 0 0'>" +
            escapeHtml(JSON.stringify(h.detail || {}, null, 0)) + "</pre></div>"
          );
        }).join("");
      } catch (e) {
        $("#lex-history").textContent = "Could not load history.";
      }
    }

    function renderSettings() {
      var d = defaults;
      $("#lex-settings").innerHTML =
        "<dl>" +
        "<dt>Sync threshold</dt><dd>" + (d.max_rows_sync || "") + " rows</dd>" +
        "<dt>Hard row cap</dt><dd>" + (d.max_rows_hard || "") + "</dd>" +
        "<dt>Preview rows</dt><dd>" + (d.preview_rows || "") + "</dd>" +
        "<dt>Download TTL</dt><dd>" + (d.download_ttl_seconds || "") + "s</dd>" +
        "<dt>Formats</dt><dd>" + (d.formats || []).join(", ") + "</dd>" +
        "<dt>Connections</dt><dd><code>" + escapeHtml(JSON.stringify(d.connection_by_environment || {})) + "</code></dd>" +
        "</dl>" +
        "<p class='muted'>Exports use dedicated read-only SQL connections. Arbitrary SQL and table names are not accepted.</p>";
    }

    $("#lex-save-preset-btn").addEventListener("click", async function () {
      var name = window.prompt("Preset name");
      if (!name) return;
      await postJson(root.getAttribute("data-presets-url"), {
        name: name,
        source_key: sourceSel.value,
        filters: gatherFilters(),
        columns: selectedColumns(),
        format: formatSel.value,
      });
      var res = await fetch(root.getAttribute("data-presets-url"));
      var data = await res.json();
      boot.presets = data.presets || [];
      setState("Preset saved");
    });

    $("#lex-refresh-jobs").addEventListener("click", refreshJobs);
    sourceSel.addEventListener("change", onSourceChange);
    envSel.addEventListener("change", function () {
      loadQuarters();
      lastPreview = null;
      exportBtn.disabled = true;
      if (ouPicker && ouPicker.onEnvironmentChange) ouPicker.onEnvironmentChange();
      else if (ouPicker && ouPicker.loadRoots) ouPicker.loadRoots();
    });
    ["lex-quarter", "lex-status", "lex-ip", "lex-row-limit", "lex-format"].forEach(function (id) {
      var el = document.getElementById(id);
      if (el) el.addEventListener("change", function () {
        lastPreview = null;
        exportBtn.disabled = true;
        updateGenerateEnabled();
      });
    });

    envSel.value = "dev";
    fillSources();
    loadQuarters();
    wireOu();
    renderPresets(boot.presets || []);
    renderJobs(boot.jobs || []);
    renderSettings();
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", init);
  } else {
    init();
  }
})();
