/**
 * Progress NPMO comparison UI — one Generate = one request; ignore stale responses.
 */
(function () {
  "use strict";

  function $(sel, root) { return (root || document).querySelector(sel); }
  function $all(sel, root) { return Array.prototype.slice.call((root || document).querySelectorAll(sel)); }
  function esc(s) {
    return String(s == null ? "" : s).replace(/&/g,"&amp;").replace(/</g,"&lt;").replace(/>/g,"&gt;");
  }
  function boot() {
    try { return JSON.parse(document.getElementById("pnc-bootstrap").textContent || "{}"); }
    catch (e) { return {}; }
  }

  function init() {
    var root = document.getElementById("pnc-root");
    if (!root) return;
    var state = {
      boot: boot(),
      requestId: 0,
      abort: null,
      lastPayload: null,
      ouPicker: null,
    };

    var envSel = $("#pnc-env");
    var periodSel = $("#pnc-period");
    var generateBtn = $("#pnc-generate");

    (state.boot.periods || []).forEach(function (p) {
      var o = document.createElement("option");
      o.value = p.id; o.textContent = p.label || p.id;
      periodSel.appendChild(o);
    });
    if (!periodSel.options.length) {
      ["2025Q3","2025Q4","2026Q1","2026Q2","2026Q3","2026Q4"].forEach(function (q) {
        var o = document.createElement("option"); o.value = q; o.textContent = q; periodSel.appendChild(o);
      });
    }

    $all(".pnc-tab", root).forEach(function (tab) {
      tab.addEventListener("click", function () {
        var name = tab.getAttribute("data-tab");
        $all(".pnc-tab", root).forEach(function (t) { t.classList.toggle("is-active", t === tab); });
        $all(".pnc-panel", root).forEach(function (p) {
          var on = p.getAttribute("data-panel") === name;
          p.hidden = !on; p.classList.toggle("is-active", on);
        });
      });
    });

    function wireOu() {
      if (!window.HubOrgUnitPicker || !window.HubOrgUnitPicker.createPicker) return;
      state.ouPicker = window.HubOrgUnitPicker.createPicker({
        root: $("#pnc-ou-block"),
        hiddenEl: $("#pnc-ou"),
        pathEl: $("#pnc-ou-path"),
        chipLabel: $("#pnc-ou-path"),
        clearBtn: null,
        refreshMetaBtn: null,
        errorEl: $("#pnc-ou-error"),
        syncEl: null,
        searchEl: $("#pnc-ou-search"),
        searchResultsEl: $("#pnc-ou-search-results"),
        apiUrl: root.getAttribute("data-org-units-url"),
        getEnvironment: function () { return envSel.value; },
        storagePrefix: "centralhub.pnc.ou.",
        onChange: updateGenerate,
      });
      if (state.ouPicker && state.ouPicker.loadRoots) state.ouPicker.loadRoots();
    }

    function updateGenerate() {
      generateBtn.disabled = !($("#pnc-ou").value && periodSel.value);
    }
    periodSel.addEventListener("change", updateGenerate);
    envSel.addEventListener("change", function () {
      if (state.ouPicker && state.ouPicker.loadRoots) state.ouPicker.loadRoots();
      updateGenerate();
    });

    function setError(msg) {
      var el = $("#pnc-error");
      if (!msg) { el.hidden = true; el.textContent = ""; return; }
      el.hidden = false; el.textContent = msg;
    }

    generateBtn.addEventListener("click", async function () {
      setError("");
      if (state.abort) state.abort.abort();
      state.abort = new AbortController();
      var reqId = ++state.requestId;
      generateBtn.disabled = true;
      generateBtn.textContent = "Comparing…";
      try {
        var res = await fetch(root.getAttribute("data-compare-url"), {
          method: "POST",
          headers: { "Content-Type": "application/json", Accept: "application/json" },
          signal: state.abort.signal,
          body: JSON.stringify({
            environment: envSel.value,
            period: periodSel.value,
            orgUnit: $("#pnc-ou").value,
            request_id: String(reqId),
            fresh: false,
          }),
        });
        var data = await res.json();
        if (reqId !== state.requestId) return; // stale
        if (!res.ok) throw new Error(data.error || "Comparison failed");
        state.lastPayload = data;
        renderAll(data);
        $("#pnc-last").textContent = "Last compared: " + (data.generated_at || "");
      } catch (err) {
        if (err.name === "AbortError") return;
        if (reqId !== state.requestId) return;
        setError(err.message || String(err));
      } finally {
        if (reqId === state.requestId) {
          updateGenerate();
          generateBtn.textContent = "Run Comparison";
        }
      }
    });

    function statusClass(st) {
      if (st === "Exact Match") return "";
      if (st === "Rounding Difference" || st === "Expected Logic Difference") return "is-diff";
      if (st === "Mapping Unresolved" || st === "Not Comparable" || st === "Source Unavailable") return "is-muted";
      return "is-bad";
    }

    function fmt(v, isPct) {
      if (v == null || v === "") return "—";
      if (isPct) return Number(v).toFixed(2) + "%";
      return Number(v).toLocaleString();
    }

    function renderAll(data) {
      renderCards(data);
      renderHighlights(data);
      renderTable(data);
      $("#pnc-diagnostics").textContent = JSON.stringify({
        diagnostics: data.diagnostics,
        timestamps: data.timestamps,
        period_months: data.period_months,
        previous_period: data.previous_period,
      }, null, 2);
      $("#pnc-evidence").textContent = JSON.stringify({
        evidence: data.evidence,
        report: data.report,
        mapping_notes: (data.indicators || []).map(function (r) {
          return { key: r.key, mapping_status: r.mapping_status, evidence: r.evidence, notes: r.explanation };
        }),
        not_comparable: data.not_comparable,
      }, null, 2);
    }

    function renderCards(data) {
      var o = data.overall || {};
      var sc = o.status_counts || {};
      var rep = data.report || {};
      $("#pnc-cards").innerHTML =
        "<article class='pnc-card'><h3>Overall Match Status</h3>" +
        "<p class='pnc-big'>" + esc(o.status) + "</p>" +
        "<p class='muted'>" + esc(o.exact_match) + " of " + esc(o.compared) + " compared indicators exact; " +
        esc((o.total || 0) - (o.compared || 0)) + " unresolved/not comparable.</p>" +
        "<button type='button' class='btn btn-sm' id='pnc-goto-ind'>View Indicator Comparison</button></article>" +
        "<article class='pnc-card'><h3>Indicators Compared</h3>" +
        "<p class='pnc-big'>" + esc(o.compared) + " / " + esc(o.total) + "</p>" +
        "<p class='muted'>Exact " + esc(sc["Exact Match"] || 0) +
        " · Diff " + esc((sc["Unexplained Difference"] || 0) + (sc["Expected Logic Difference"] || 0) + (sc["Rounding Difference"] || 0)) +
        " · Unresolved " + esc(sc["Mapping Unresolved"] || 0) + "</p></article>" +
        "<article class='pnc-card'><h3>Population Compatibility</h3>" +
        "<p class='pnc-big'>" + esc((data.population_compatibility || {}).status) + "</p>" +
        "<p class='muted'>" + esc((data.population_compatibility || {}).note) + "</p></article>" +
        "<article class='pnc-card'><h3>Report Generation / Sources</h3>" +
        "<p class='muted'>DHIS2 Report UID</p><code class='pnc-uid' data-copy='" + esc(rep.uid) + "'>" + esc(rep.uid) + "</code>" +
        "<p class='muted'>Central Hub Report</p><code class='pnc-uid' data-copy='" + esc(rep.central_hub_report_id) + "'>" + esc(rep.central_hub_report_id) + "</code>" +
        "<p class='muted'>Extraction: " + esc(rep.extraction_method) + "</p></article>";
      var btn = $("#pnc-goto-ind");
      if (btn) btn.addEventListener("click", function () {
        $all(".pnc-tab", root).forEach(function (t) {
          if (t.getAttribute("data-tab") === "indicators") t.click();
        });
      });
      wireCopy();
    }

    function renderHighlights(data) {
      var box = $("#pnc-highlights");
      box.hidden = false;
      var d = (data.highlights || {}).dhis2 || {};
      var h = (data.highlights || {}).hcsc || {};
      function items(map, pctKeys) {
        return Object.keys(map).map(function (k) {
          var isPct = (pctKeys || []).indexOf(k) >= 0;
          return "<div class='pnc-hl-item'><strong>" + esc(k.replace(/_/g, " ")) + "</strong>" +
            fmt(map[k], isPct) + "</div>";
        }).join("");
      }
      $("#pnc-hl-dhis2").innerHTML = items(d, ["completion_rate", "validation_rate"]);
      $("#pnc-hl-hcsc").innerHTML = items(h, ["completion_rate"]);
    }

    function renderTable(data) {
      var tbody = $("#pnc-tbody");
      var rows = data.indicators || [];
      if (!rows.length) {
        tbody.innerHTML = "<tr><td colspan='13' class='muted'>No rows</td></tr>";
        return;
      }
      tbody.innerHTML = rows.map(function (r) {
        var isPct = r.result_type === "percentage";
        var rep = r.report || {};
        var hc = r.hcsc || {};
        var diff = r.diffs || {};
        var st = r.comparison_status || "";
        var numRep = isPct ? fmt(rep.numerator, false) : "—";
        var denRep = isPct ? fmt(rep.denominator, false) : "—";
        var numHc = isPct ? fmt(hc.numerator, false) : "—";
        var denHc = isPct ? fmt(hc.denominator, false) : "—";
        return "<tr>" +
          "<td><button type='button' class='btn btn-sm pnc-diag' data-key='" + esc(r.key) + "'>ℹ</button> " +
          esc(r.report_label) + "<div class='muted'>" + esc(r.mapping_status) + " · " + esc(r.dhis2_source_type) + "</div></td>" +
          "<td>" + numRep + "</td><td>" + denRep + "</td>" +
          "<td>" + fmt(rep.result, isPct) + "</td>" +
          "<td><span class='pnc-uid' data-copy='" + esc(r.dhis2_uid || "") + "'>" + esc(r.dhis2_uid || rep.source_badge || "CLIENT") + "</span></td>" +
          "<td><span class='pnc-status-pill " + statusClass(st) + "'>" + esc(st) + "</span></td>" +
          "<td>" + numHc + "</td><td>" + denHc + "</td>" +
          "<td>" + fmt(hc.result, isPct) + "</td>" +
          "<td><span class='pnc-uid' data-copy='" + esc(r.hcsc_source_uid || "") + "'>" + esc(r.hcsc_source_uid || "—") + "</span></td>" +
          "<td>" + (diff.result_diff_pp != null ? (Number(diff.result_diff_pp).toFixed(2) + " pp") : (diff.result_diff != null ? diff.result_diff : "—")) + "</td>" +
          "<td>" + (diff.num_diff != null ? diff.num_diff : "—") + "</td>" +
          "<td>" + (diff.den_diff != null ? diff.den_diff : "—") + "</td>" +
          "</tr>";
      }).join("");
      $all(".pnc-diag", tbody).forEach(function (btn) {
        btn.addEventListener("click", function () {
          var key = btn.getAttribute("data-key");
          var row = (state.lastPayload.indicators || []).find(function (x) { return x.key === key; });
          $("#pnc-drawer").hidden = false;
          $("#pnc-drawer-title").textContent = (row && row.report_label) || key;
          $("#pnc-drawer-body").textContent = JSON.stringify(row, null, 2);
          $all(".pnc-tab", root).forEach(function (t) {
            if (t.getAttribute("data-tab") === "diagnostics") t.click();
          });
        });
      });
      wireCopy();
    }

    function wireCopy() {
      $all(".pnc-uid[data-copy]", root).forEach(function (el) {
        el.onclick = function () {
          var v = el.getAttribute("data-copy") || "";
          if (v && navigator.clipboard) navigator.clipboard.writeText(v);
        };
      });
    }

    $("#pnc-drawer-close").addEventListener("click", function () {
      $("#pnc-drawer").hidden = true;
    });

    $("#pnc-load-snapshot").addEventListener("click", async function () {
      var res = await fetch(root.getAttribute("data-snapshot-url") + "?environment=" + encodeURIComponent(envSel.value));
      var data = await res.json();
      if (!data.ok) { setError(data.error || "Snapshot unavailable"); return; }
      $("#pnc-snapshot").innerHTML = data.sanitized_html || "";
    });

    $all("[data-export]", root).forEach(function (btn) {
      btn.addEventListener("click", function () {
        if (!state.lastPayload) { setError("Run a comparison before export."); return; }
        var fmt = btn.getAttribute("data-export");
        var url = root.getAttribute("data-export-url") +
          "?environment=" + encodeURIComponent(envSel.value) +
          "&period=" + encodeURIComponent(periodSel.value) +
          "&orgUnit=" + encodeURIComponent($("#pnc-ou").value) +
          "&format=" + encodeURIComponent(fmt);
        window.location.href = url;
      });
    });

    // Evidence tab default
    $("#pnc-evidence").textContent = JSON.stringify({
      report: state.boot.report,
      mapping_summary: state.boot.mapping_summary,
    }, null, 2);

    wireOu();
    updateGenerate();
  }

  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", init);
  else init();
})();
