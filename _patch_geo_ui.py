# Patch hcsc_indicator_summary.js for geographic breakdown UI wiring.
from pathlib import Path

path = Path(r"c:\PMNP\personal\central-hub\static\js\hcsc_indicator_summary.js")
text = path.read_text(encoding="utf-8")

# Extend state
old_state = """    updatingBackground: false,
  };"""
new_state = """    updatingBackground: false,
    geographicBreakdown: "none",
    breakdown: null,
    geoEstimate: null,
    bdPage: 0,
    bdPageSize: 50,
    pendingForceRefresh: false,
  };"""
if "geographicBreakdown:" not in text:
    text = text.replace(old_state, new_state, 1)

# Insert helpers after selectedDisaggLabel
helper = r'''
  function selectedPopulationLabel() {
    var sel = $("hcsc-disagg");
    if (!sel || !sel.selectedOptions || !sel.selectedOptions[0]) return "All Households";
    return sel.selectedOptions[0].textContent || "All Households";
  }

  function selectedGeoBreakdown() {
    var sel = $("hcsc-geo-breakdown");
    return (sel && sel.value) || "none";
  }

  function selectedGeoLabel() {
    var sel = $("hcsc-geo-breakdown");
    if (!sel || !sel.selectedOptions || !sel.selectedOptions[0]) return "None";
    return sel.selectedOptions[0].textContent || "None";
  }

  function selectedOuLevel() {
    if (ouPicker && ouPicker.selectedPath) {
      var p = ouPicker.selectedPath();
      if (p && p.level != null) return p.level;
    }
    return null;
  }

  function geoOptionsForLevel(level) {
    var meta = (boot.geographic_breakdown && boot.geographic_breakdown.labels) || {};
    var opts = [{ id: "none", label: meta.none || "None (selected area total)" }];
    if (level == null) return opts;
    var order = [
      ["region", 2],
      ["province", 3],
      ["municipality_city", 4],
      ["barangay", 5],
    ];
    order.forEach(function (pair) {
      if (pair[1] > level) {
        opts.push({
          id: pair[0],
          label: meta[pair[0]] || ("By " + pair[0]),
        });
      }
    });
    return opts;
  }

  function fillGeoBreakdown() {
    var sel = $("hcsc-geo-breakdown");
    if (!sel) return;
    var level = selectedOuLevel();
    var opts = geoOptionsForLevel(level);
    var cur = sel.value || "none";
    var allowed = {};
    sel.innerHTML = opts
      .map(function (o) {
        allowed[o.id] = true;
        return (
          '<option value="' +
          escapeHtml(o.id) +
          '"' +
          (o.id === cur ? " selected" : "") +
          ">" +
          escapeHtml(o.label) +
          "</option>"
        );
      })
      .join("");
    if (!allowed[sel.value]) sel.value = "none";
    state.geographicBreakdown = sel.value || "none";
    refreshGeoEstimate();
  }

  function refreshGeoEstimate() {
    var est = $("hcsc-geo-estimate");
    var geo = selectedGeoBreakdown();
    if (!est) return;
    if (!geo || geo === "none") {
      est.textContent = "Select a breakdown to see estimate.";
      state.geoEstimate = null;
      return;
    }
    var ou = selectedOu();
    if (!ou) {
      est.textContent = "Select an organisation unit to see estimate.";
      state.geoEstimate = null;
      return;
    }
    var url = root.getAttribute("data-breakdown-estimate-url");
    if (!url) {
      est.textContent = "Estimate unavailable.";
      return;
    }
    est.textContent = "Estimating…";
    var qs =
      "?environment=" +
      encodeURIComponent(($("hcsc-env") && $("hcsc-env").value) || "stage") +
      "&orgUnit=" +
      encodeURIComponent(ou) +
      "&geographicBreakdown=" +
      encodeURIComponent(geo);
    fetch(url + qs, { credentials: "same-origin" })
      .then(function (r) {
        return r.json();
      })
      .then(function (data) {
        if (selectedGeoBreakdown() !== geo) return;
        if (!data || !data.ok) {
          est.textContent = (data && data.error) || "Estimate unavailable.";
          state.geoEstimate = null;
          return;
        }
        state.geoEstimate = data;
        est.textContent = data.estimate_label || (data.child_count + " units");
        if (data.requires_confirmation) {
          est.textContent += " · confirmation required";
        }
      })
      .catch(function () {
        est.textContent = "Estimate unavailable.";
        state.geoEstimate = null;
      });
  }

  function showGeoConfirm(message, onContinue) {
    var box = $("hcsc-geo-confirm");
    var msg = $("hcsc-geo-confirm-msg");
    var ok = $("hcsc-geo-confirm-ok");
    var cancel = $("hcsc-geo-confirm-cancel");
    if (!box || !ok || !cancel) {
      onContinue();
      return;
    }
    if (msg) msg.textContent = message;
    box.hidden = false;
    function close() {
      box.hidden = true;
      ok.removeEventListener("click", onOk);
      cancel.removeEventListener("click", onCancel);
    }
    function onOk() {
      close();
      onContinue();
    }
    function onCancel() {
      close();
      var geo = $("hcsc-geo-breakdown");
      if (geo) geo.value = "none";
      fillGeoBreakdown();
      applyGenUi();
    }
    ok.addEventListener("click", onOk);
    cancel.addEventListener("click", onCancel);
  }

  function renderBreakdown() {
    var panel = $("hcsc-breakdown-panel");
    var tbody = $("hcsc-breakdown-tbody");
    var title = $("hcsc-breakdown-title");
    var status = $("hcsc-breakdown-status");
    var retry = $("hcsc-bd-retry");
    if (!panel || !tbody) return;
    var bd = state.breakdown;
    if (!bd || bd.mode === "none") {
      panel.hidden = true;
      return;
    }
    panel.hidden = false;
    if (title) {
      title.textContent =
        bd.label ||
        ("Breakdown by " + (bd.mode || "").replace(/_/g, " "));
    }
    if (status) {
      if (bd.ok === false) {
        status.textContent = "Breakdown generation failed" + (bd.error ? ": " + bd.error : "");
      } else {
        status.textContent =
          (bd.child_count != null ? bd.child_count + " organisation units" : "") +
          (bd.cache && bd.cache.hit ? " · cached" : "");
      }
    }
    if (retry) retry.hidden = !(bd.ok === false);
    var indicator = (($("hcsc-bd-indicator") && $("hcsc-bd-indicator").value) || "eligible_households");
    var q = (($("hcsc-bd-filter") && $("hcsc-bd-filter").value) || "").trim().toLowerCase();
    var sort = (($("hcsc-bd-sort") && $("hcsc-bd-sort").value) || "name_asc");
    var rows = (bd.children || []).map(function (child) {
      var hit = null;
      (child.results || []).forEach(function (r) {
        if (r.indicator_key === indicator) hit = r;
      });
      return {
        child: child,
        row: hit,
        name: child.org_unit_name || child.org_unit || "",
        path: child.hierarchy_path || "",
        value: hit
          ? hit.percentage != null
            ? hit.percentage
            : hit.count != null
              ? hit.count
              : null
          : null,
      };
    });
    rows = rows.filter(function (r) {
      if (!q) return true;
      return (
        r.name.toLowerCase().indexOf(q) >= 0 ||
        (r.path || "").toLowerCase().indexOf(q) >= 0 ||
        (r.child.org_unit || "").toLowerCase().indexOf(q) >= 0
      );
    });
    rows.sort(function (a, b) {
      if (sort === "name_desc") return b.name.localeCompare(a.name);
      if (sort === "value_desc") return (b.value || 0) - (a.value || 0);
      if (sort === "value_asc") return (a.value || 0) - (b.value || 0);
      return a.name.localeCompare(b.name);
    });
    var pageSize = state.bdPageSize || 50;
    var pages = Math.max(1, Math.ceil(rows.length / pageSize));
    if (state.bdPage >= pages) state.bdPage = pages - 1;
    if (state.bdPage < 0) state.bdPage = 0;
    var start = state.bdPage * pageSize;
    var pageRows = rows.slice(start, start + pageSize);
    var pager = $("hcsc-breakdown-pager");
    var pageLabel = $("hcsc-bd-page-label");
    if (pager) pager.hidden = rows.length <= pageSize;
    if (pageLabel) pageLabel.textContent = "Page " + (state.bdPage + 1) + " / " + pages;
    if (!pageRows.length) {
      tbody.innerHTML =
        '<tr class="hcsc-empty-row"><td colspan="7"><div class="hcsc-empty"><strong>No breakdown rows</strong></div></td></tr>';
      return;
    }
    tbody.innerHTML = pageRows
      .map(function (item) {
        var r = item.row || {};
        return (
          "<tr>" +
          '<td><button type="button" class="hcsc-bd-ou-link" data-ou="' +
          escapeHtml(item.child.org_unit || "") +
          '" title="' +
          escapeHtml(item.path || item.name) +
          '">' +
          escapeHtml(item.name) +
          "</button></td>" +
          "<td>" +
          escapeHtml(r.numerator != null ? r.numerator : "—") +
          "</td>" +
          "<td>" +
          escapeHtml(r.denominator != null ? r.denominator : "—") +
          "</td>" +
          "<td>" +
          escapeHtml(r.value_text || "—") +
          "</td>" +
          "<td>" +
          escapeHtml(r.source_badge || "—") +
          "</td>" +
          "<td>" +
          escapeHtml(r.validation_status || "—") +
          "</td>" +
          "<td>" +
          escapeHtml((r.last_updated || r.freshness || "—").toString().replace("T", " ").slice(0, 19)) +
          "</td>" +
          "</tr>"
        );
      })
      .join("");
  }

  function exportBreakdownCsv() {
    var bd = state.breakdown;
    if (!bd || !bd.children) return;
    var indicator = (($("hcsc-bd-indicator") && $("hcsc-bd-indicator").value) || "eligible_households");
    var lines = [
      "Organisation Unit,UID,Path,Indicator,Numerator,Denominator,Result,Source,Validation,Last Updated,Environment,Period,Geographic Breakdown",
    ];
    bd.children.forEach(function (child) {
      var hit = null;
      (child.results || []).forEach(function (r) {
        if (r.indicator_key === indicator) hit = r;
      });
      lines.push(
        [
          child.org_unit_name,
          child.org_unit,
          child.hierarchy_path,
          indicator,
          hit && hit.numerator != null ? hit.numerator : "",
          hit && hit.denominator != null ? hit.denominator : "",
          hit && hit.value_text != null ? hit.value_text : "",
          hit && hit.source_badge != null ? hit.source_badge : "",
          hit && hit.validation_status != null ? hit.validation_status : "",
          hit && (hit.last_updated || hit.freshness) ? hit.last_updated || hit.freshness : "",
          ($("hcsc-env") && $("hcsc-env").value) || "",
          selectedPeriod() || "",
          selectedGeoBreakdown() || "",
        ]
          .map(function (v) {
            return '"' + String(v == null ? "" : v).replace(/"/g, '""') + '"';
          })
          .join(",")
      );
    });
    copyText(lines.join("\n"));
  }

'''

if "function selectedGeoBreakdown()" not in text:
    needle = "  function selectedDisaggLabel() {"
    idx = text.find(needle)
    if idx < 0:
        raise SystemExit("selectedDisaggLabel not found")
    # insert after selectedDisaggLabel function block
    end = text.find("\n  function selectedOuPath()", idx)
    if end < 0:
        raise SystemExit("selectedOuPath not found")
    text = text[:end] + "\n" + helper + text[end:]

# Patch currentScopeKey
old_scope = '''  function currentScopeKey() {
    var env = (($("hcsc-env") && $("hcsc-env").value) || "stage").toLowerCase();
    var pe = selectedPeriod() || "";
    var ou = selectedOu() || "";
    var disagg = (($("hcsc-disagg") && $("hcsc-disagg").value) || "none");
    return env + "|" + pe + "|" + ou + "|" + disagg;
  }'''
new_scope = '''  function currentScopeKey() {
    var env = (($("hcsc-env") && $("hcsc-env").value) || "stage").toLowerCase();
    var pe = selectedPeriod() || "";
    var ou = selectedOu() || "";
    var disagg = (($("hcsc-disagg") && $("hcsc-disagg").value) || "none");
    var geo = selectedGeoBreakdown() || "none";
    return env + "|" + pe + "|" + ou + "|" + disagg + "|" + geo;
  }'''
if '|" + disagg + "|" + geo' not in text:
    text = text.replace(old_scope, new_scope)

# Patch scopeQuery
old_sq = '''  function scopeQuery(force) {
    var env = ($("hcsc-env") && $("hcsc-env").value) || "stage";
    var period = selectedPeriod();
    var ou = selectedOu();
    var disagg = ($("hcsc-disagg") && $("hcsc-disagg").value) || "none";
    return (
      "?environment=" +
      encodeURIComponent(env) +
      "&period=" +
      encodeURIComponent(period) +
      "&orgUnit=" +
      encodeURIComponent(ou) +
      "&disaggregation=" +
      encodeURIComponent(disagg) +
      (force ? "&fresh=1" : "")
    );
  }'''
new_sq = '''  function scopeQuery(force) {
    var env = ($("hcsc-env") && $("hcsc-env").value) || "stage";
    var period = selectedPeriod();
    var ou = selectedOu();
    var disagg = ($("hcsc-disagg") && $("hcsc-disagg").value) || "none";
    var geo = selectedGeoBreakdown() || "none";
    return (
      "?environment=" +
      encodeURIComponent(env) +
      "&period=" +
      encodeURIComponent(period) +
      "&orgUnit=" +
      encodeURIComponent(ou) +
      "&disaggregation=" +
      encodeURIComponent(disagg) +
      "&geographicBreakdown=" +
      encodeURIComponent(geo) +
      (force ? "&fresh=1" : "")
    );
  }'''
if "geographicBreakdown=" not in text:
    text = text.replace(old_sq, new_sq)

# Patch chips in applyGenUi — find population chip insertion
old_chips_tail = '''        '<span class="hcsc-param-chip"><span class="hcsc-chip-k">Disaggregation</span> ' +
        escapeHtml(disagg) +
        "</span>";'''
new_chips_tail = '''        '<span class="hcsc-param-chip"><span class="hcsc-chip-k">Population Filter</span> ' +
        escapeHtml(selectedPopulationLabel()) +
        "</span>" +
        '<span class="hcsc-param-chip"><span class="hcsc-chip-k">Geographic Breakdown</span> ' +
        escapeHtml(selectedGeoLabel()) +
        "</span>";'''
if "Geographic Breakdown" not in text.split("hcsc-param-chips")[1][:2000] if "hcsc-param-chips" in text else True:
    if old_chips_tail in text:
        text = text.replace(old_chips_tail, new_chips_tail)
    else:
        # try older variant
        alt = '''        '<span class="hcsc-param-chip"><span class="hcsc-chip-k">Disaggregation</span> ' +
        escapeHtml(disagg) +
        "</span>";'''
        if alt in text:
            text = text.replace(alt, new_chips_tail)

# In applyGenUi chips, remove unused disagg var usage issues - still used? selectedDisaggLabel may remain.
# Patch fillDisagg callers / wire to call fillGeoBreakdown

# loadReport: confirmation + store breakdown
# After successful data parse, set state.breakdown
success_marker = "state.lastDiagnostics = buildDiagnostics();\n\n        setGenPhase(state.cacheHit ? GEN.SUCCESS_CACHED : GEN.SUCCESS_FRESH,"
if "state.breakdown = data.geographic_breakdown" not in text:
    text = text.replace(
        success_marker,
        "state.lastDiagnostics = buildDiagnostics();\n"
        "        state.breakdown = data.geographic_breakdown || { mode: \"none\", children: [] };\n"
        "        state.bdPage = 0;\n\n"
        "        setGenPhase(state.cacheHit ? GEN.SUCCESS_CACHED : GEN.SUCCESS_FRESH,",
    )

# After renderRetrieval in success, call renderBreakdown
if "renderBreakdown();" not in text:
    text = text.replace(
        "renderRetrieval();\n        var openSql = $(\"hcsc-open-sql\");",
        "renderRetrieval();\n          renderBreakdown();\n        var openSql = $(\"hcsc-open-sql\");",
    )

# Wrap loadReport start to confirm large breakdowns — replace first lines of loadReport body after isActiveGeneration check
old_lr = '''  function loadReport(force) {
    if (isActiveGeneration()) {
      return;
    }
    if (!validateForm({ revealErrors: true })) {'''
new_lr = '''  function loadReport(force) {
    if (isActiveGeneration()) {
      return;
    }
    var geo = selectedGeoBreakdown();
    var est = state.geoEstimate;
    if (
      !force &&
      geo &&
      geo !== "none" &&
      est &&
      est.requires_confirmation &&
      !state.pendingForceRefresh
    ) {
      showGeoConfirm(
        "This will generate results for " +
          (est.estimate_label || est.child_count + " organisation units") +
          " and may take longer.",
        function () {
          state.pendingForceRefresh = true;
          loadReport(force);
          state.pendingForceRefresh = false;
        }
      );
      return;
    }
    if (!validateForm({ revealErrors: true })) {'''
if "requires_confirmation" not in text.split("function loadReport")[1][:800]:
    text = text.replace(old_lr, new_lr)

# fillDisagg - also fill geo; find function fillDisagg
if "fillGeoBreakdown();" not in text:
    # after fillDisagg definition ends - call from wire
    pass

# wire: after fillDisagg(); add fillGeoBreakdown and event listeners
old_wire_start = '''  function wire() {
    fillPeriods();
    fillDisagg();
    wireOuSearch();'''
new_wire_start = '''  function wire() {
    fillPeriods();
    fillDisagg();
    fillGeoBreakdown();
    wireOuSearch();'''
if "fillGeoBreakdown();" not in text.split("function wire()")[1][:400]:
    text = text.replace(old_wire_start, new_wire_start)

# onChange OU already calls onScopeMaybeChanged - add fillGeoBreakdown there
old_osc = '''  function onScopeMaybeChanged() {
    var key = currentScopeKey();'''
new_osc = '''  function onScopeMaybeChanged() {
    fillGeoBreakdown();
    var key = currentScopeKey();'''
if "fillGeoBreakdown();\n    var key = currentScopeKey()" not in text:
    text = text.replace(old_osc, new_osc)

# Add wire listeners before hydrateFromQuery
wire_extra = '''
    var geoSel = $("hcsc-geo-breakdown");
    if (geoSel) {
      geoSel.addEventListener("change", function () {
        state.geographicBreakdown = geoSel.value || "none";
        refreshGeoEstimate();
        onScopeMaybeChanged();
      });
    }
    var helpBtn = $("hcsc-geo-help-btn");
    var help = $("hcsc-geo-help");
    if (helpBtn && help) {
      helpBtn.addEventListener("click", function () {
        help.hidden = !help.hidden;
        helpBtn.setAttribute("aria-expanded", help.hidden ? "false" : "true");
      });
    }
    ["hcsc-bd-filter", "hcsc-bd-indicator", "hcsc-bd-sort"].forEach(function (id) {
      var el = $(id);
      if (el) el.addEventListener("input", function () { state.bdPage = 0; renderBreakdown(); });
      if (el) el.addEventListener("change", function () { state.bdPage = 0; renderBreakdown(); });
    });
    var bdExport = $("hcsc-bd-export-csv");
    if (bdExport) bdExport.addEventListener("click", exportBreakdownCsv);
    var bdRetry = $("hcsc-bd-retry");
    if (bdRetry) {
      bdRetry.addEventListener("click", function () {
        loadReport(true);
      });
    }
    var bdPrev = $("hcsc-bd-prev");
    var bdNext = $("hcsc-bd-next");
    if (bdPrev) {
      bdPrev.addEventListener("click", function () {
        state.bdPage -= 1;
        renderBreakdown();
      });
    }
    if (bdNext) {
      bdNext.addEventListener("click", function () {
        state.bdPage += 1;
        renderBreakdown();
      });
    }
    root.addEventListener("click", function (ev) {
      var ouBtn = ev.target.closest(".hcsc-bd-ou-link");
      if (!ouBtn) return;
      var uid = ouBtn.getAttribute("data-ou") || "";
      if (!uid || !ouPicker || !ouPicker.setSelection) return;
      ouPicker.setSelection(uid, ouBtn.textContent || uid, ouBtn.getAttribute("title") || "");
      var geo = $("hcsc-geo-breakdown");
      if (geo) geo.value = "none";
      fillGeoBreakdown();
      onScopeMaybeChanged();
    });
'''

if 'id="hcsc-geo-help-btn"' in Path(r"c:\PMNP\personal\central-hub\templates\hcsc_indicator_summary.html").read_text(encoding="utf-8"):
    if "hcsc-geo-help-btn" not in text:
        marker = "    // Restore controls from URL if present"
        if marker not in text:
            raise SystemExit("hydrate marker missing")
        text = text.replace(marker, wire_extra + "\n" + marker)

# fillDisagg should use population_filters / All Households from boot
old_fill = None
# Update status texts for generating with geo
# Optional: tweak GENERATING helper when geo selected - skip for now

path.write_text(text, encoding="utf-8")
print("patched", path)
print("geo helpers", "selectedGeoBreakdown" in text)
print("scope geo", "geographicBreakdown=" in text)
print("renderBreakdown", "function renderBreakdown" in text)
print("confirm", "showGeoConfirm" in text)
print("wire help", "hcsc-geo-help-btn" in text)
