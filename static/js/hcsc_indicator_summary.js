/**
 * Central Hub HCSC–RF UI
 * Read-only presentation of registry + batched analytics. No formula engine.
 */
(function () {
  "use strict";

  var root = document.getElementById("hcsc-root");
  if (!root) return;

  var boot = {};
  try {
    var bootEl = document.getElementById("hcsc-bootstrap");
    if (bootEl && bootEl.textContent) {
      boot = JSON.parse(bootEl.textContent);
    } else {
      // Legacy attribute fallback
      boot = JSON.parse(root.getAttribute("data-bootstrap") || "{}");
    }
  } catch (e) {
    boot = {};
    showShellBanner("Bootstrap JSON failed to parse. Shell still usable — reload or check registry.", true);
  }

  var state = {
    results: [],
    sections: [],
    retrieval: null,
    lastPayload: null,
    validation: null,
    activeTab: "summary",
    activeRtab: "retrieval_request",
    activeCategory: "overview",
    lastRunAt: null,
    lastRunDurationMs: null,
    lastRunOk: null,
    statusMode: "need_ou",
    generated: false,
    showFieldErrors: false,
    reportInFlight: false,
  };
  var ouPicker = null;
  var allowedQuarters = {};
  var CARD_TITLES = {
    eligible_households: "Eligible Households",
    approved_eligible_households: "Approved Eligible Households",
    convergent_households: "Convergent Households",
    convergence_rate: "Overall Convergence Rate",
    completion_validated_eligible_rate: "Completion Rate",
  };
  var CARD_KEYS = [
    "eligible_households",
    "approved_eligible_households",
    "convergent_households",
    "convergence_rate",
    "completion_validated_eligible_rate",
  ];
  var hiddenCols = {};

  function $(id) {
    return document.getElementById(id);
  }

  function escapeHtml(s) {
    return String(s == null ? "" : s)
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;");
  }

  function copyText(text) {
    if (!text) return;
    if (navigator.clipboard && navigator.clipboard.writeText) {
      navigator.clipboard.writeText(String(text));
    }
  }

  function showShellBanner(msg, isError) {
    var el = $("hcsc-shell-banner");
    if (!el) return;
    if (!msg) {
      el.hidden = true;
      el.textContent = "";
      return;
    }
    el.hidden = false;
    el.textContent = msg;
    el.classList.toggle("is-error", !!isError);
    el.classList.toggle("is-loading", !isError && /loading|generating|validat/i.test(msg));
  }

  function setStatus(msg, isError) {
    updateStatusStrip(msg, isError ? "error" : null);
    if (isError) showShellBanner(msg, true);
  }

  function storageKey(kind) {
    var env = ($("hcsc-env") && $("hcsc-env").value) || "stage";
    return "centralhub.hcsc." + kind + "." + env;
  }

  function loadRememberedQuarter() {
    try {
      var raw = JSON.parse(localStorage.getItem(storageKey("period")) || "null");
      return raw && raw.id ? String(raw.id) : "";
    } catch (e) {
      return "";
    }
  }

  function saveRememberedQuarter(id) {
    try {
      localStorage.setItem(storageKey("period"), JSON.stringify({ id: id, at: Date.now() }));
    } catch (e) {}
  }

  function setFieldError(id, msg) {
    var el = $(id);
    if (!el) return;
    if (!msg) {
      el.hidden = true;
      el.textContent = "";
      return;
    }
    el.hidden = false;
    el.textContent = msg;
  }

  function isValidOuUid(value) {
    if (window.CentralHubOuPicker && window.CentralHubOuPicker.isValidUid) {
      return window.CentralHubOuPicker.isValidUid(value);
    }
    return /^[A-Za-z0-9]{11}$/.test(String(value || "").trim());
  }

  function selectedPeriod() {
    var pe = ($("hcsc-period") && $("hcsc-period").value) || "";
    return allowedQuarters[pe] ? pe : "";
  }

  function selectedOu() {
    if (ouPicker && ouPicker.selectedUid) {
      var fromPicker = ouPicker.selectedUid();
      if (fromPicker) return fromPicker;
    }
    var ou = ($("hcsc-ou") && $("hcsc-ou").value) || "";
    return isValidOuUid(ou) ? ou.trim() : "";
  }

  function formatDuration(ms) {
    var total = Math.max(0, Math.round((ms || 0) / 1000));
    var m = Math.floor(total / 60);
    var s = total % 60;
    return String(m).padStart(2, "0") + ":" + String(s).padStart(2, "0");
  }

  function formatRunStamp(iso) {
    if (!iso) return "";
    try {
      var d = new Date(iso);
      if (isNaN(d.getTime())) return String(iso).replace("T", " ").slice(0, 19);
      return d.toLocaleString(undefined, {
        year: "numeric",
        month: "short",
        day: "numeric",
        hour: "numeric",
        minute: "2-digit",
      });
    } catch (e) {
      return String(iso);
    }
  }

  function selectedDisaggLabel() {
    var sel = $("hcsc-disagg");
    if (!sel || !sel.selectedOptions || !sel.selectedOptions[0]) return "None";
    return sel.selectedOptions[0].textContent || "None";
  }

  function selectedOuPath() {
    if (ouPicker && ouPicker.selectedPath) {
      var p = ouPicker.selectedPath();
      if (p && p.path) return p.path;
      if (p && p.name) return p.name;
    }
    var chip = $("hcsc-ou-chip-label");
    return (chip && chip.textContent) || "";
  }

  function updateStatusStrip(explicitMsg, mode) {
    var el = $("hcsc-status");
    var badge = $("hcsc-status-badge");
    var pe = selectedPeriod();
    var ou = selectedOu();
    var resolved =
      mode ||
      (state.statusMode === "loading"
        ? "loading"
        : state.statusMode === "error"
          ? "error"
          : !ou
            ? "need_ou"
            : pe && ou
              ? "ready"
              : "need_ou");
    if (mode) state.statusMode = mode;
    else if (resolved === "ready" || resolved === "need_ou") state.statusMode = resolved;
    var msg =
      explicitMsg ||
      (resolved === "loading"
        ? "Generating report…"
        : resolved === "error"
          ? "Error"
          : resolved === "ready"
            ? "Ready to generate"
            : "Select an organisation unit to continue");
    var badgeText =
      resolved === "loading"
        ? "Generating report…"
        : resolved === "error"
          ? "Error"
          : resolved === "ready"
            ? "Ready to generate"
            : "Awaiting selection";
    if (el) {
      el.textContent = msg;
      el.classList.toggle("is-error", resolved === "error");
      el.classList.toggle("is-loading", resolved === "loading");
      el.classList.toggle("is-ready", resolved === "ready");
    }
    if (badge) {
      badge.textContent = badgeText;
      badge.className =
        "hcsc-status-badge" +
        (resolved === "ready"
          ? " is-ready"
          : resolved === "error"
            ? " is-error"
            : resolved === "loading"
              ? " is-loading"
              : "");
    }
    var readyMark = $("hcsc-ou-ready");
    if (readyMark) readyMark.hidden = !ou;
    var emptyOu = $("hcsc-ou-empty");
    if (emptyOu) emptyOu.hidden = !!ou;
    var helper = $("hcsc-ou-helper");
    if (helper) helper.hidden = !!ou;
    var chips = $("hcsc-param-chips");
    if (chips) {
      var envRaw = (($("hcsc-env") && $("hcsc-env").value) || "stage").toLowerCase();
      var peLabel =
        ($("hcsc-period") &&
          $("hcsc-period").selectedOptions &&
          $("hcsc-period").selectedOptions[0] &&
          $("hcsc-period").selectedOptions[0].textContent) ||
        pe ||
        "—";
      var path = selectedOuPath() || "—";
      var disagg = selectedDisaggLabel();
      chips.innerHTML =
        '<span class="hcsc-param-chip"><span class="hcsc-chip-k">Environment</span> ' +
        escapeHtml(envRaw === "live" ? "Live" : "Stage") +
        "</span>" +
        '<span class="hcsc-param-chip"><span class="hcsc-chip-k">Quarter</span> ' +
        escapeHtml(peLabel) +
        "</span>" +
        '<span class="hcsc-param-chip" title="' +
        escapeHtml(path) +
        '"><span class="hcsc-chip-k">Organisation Unit</span> ' +
        escapeHtml(path) +
        "</span>" +
        '<span class="hcsc-param-chip"><span class="hcsc-chip-k">Disaggregation</span> ' +
        escapeHtml(disagg) +
        "</span>";
    }
    var last = $("hcsc-last-run");
    var runBadge = $("hcsc-run-badge");
    if (last) {
      if (!state.lastRunAt) {
        last.textContent = "No report generated yet";
        last.classList.add("muted");
        if (runBadge) {
          runBadge.hidden = true;
          runBadge.textContent = "";
        }
      } else {
        last.classList.remove("muted");
        last.textContent =
          formatRunStamp(state.lastRunAt) +
          (state.lastRunDurationMs != null
            ? " · " + formatDuration(state.lastRunDurationMs)
            : "");
        if (runBadge) {
          runBadge.hidden = false;
          runBadge.textContent = state.lastRunOk === false ? "Error" : "Success";
          runBadge.className =
            "hcsc-run-badge" + (state.lastRunOk === false ? " is-error" : " is-success");
        }
      }
    }
  }

  function validateForm(opts) {
    var options = opts || {};
    if (options.revealErrors) state.showFieldErrors = true;
    var pe = selectedPeriod();
    var ou = selectedOu();
    var peOk = !!pe;
    var ouOk = !!ou;
    // Field errors only after Generate/Refresh attempt or an invalid prior selection.
    if (state.showFieldErrors) {
      setFieldError("hcsc-period-error", peOk ? "" : "Select a valid quarter.");
      setFieldError("hcsc-ou-error", ouOk ? "" : "Select an organisation unit.");
    } else {
      setFieldError("hcsc-period-error", "");
      setFieldError("hcsc-ou-error", "");
    }
    var run = $("hcsc-run");
    if (run) run.disabled = !(peOk && ouOk) || state.reportInFlight;
    var refresh = $("hcsc-refresh");
    // Refresh stays available without OU; only block during an active report request.
    if (refresh) refresh.disabled = !!state.reportInFlight;
    if (state.statusMode !== "loading" && state.statusMode !== "error") {
      state.statusMode = peOk && ouOk ? "ready" : "need_ou";
    }
    updateStatusStrip();
    return peOk && ouOk;
  }

  function fillPeriods() {
    var sel = $("hcsc-period");
    if (!sel) return;
    var quarters = (boot.periods && boot.periods.quarters) || [];
    var remembered = loadRememberedQuarter();
    var rememberedOk =
      !!(remembered && quarters.some(function (q) { return q.id === remembered; }));
    // Invalid remembered (e.g. removed 2027Qx) → latest valid default from server.
    var def =
      (rememberedOk && remembered) ||
      (boot.periods && boot.periods.default_period) ||
      (quarters.length ? quarters[quarters.length - 1].id : "") ||
      "";
    allowedQuarters = {};
    sel.innerHTML = quarters
      .map(function (q) {
        allowedQuarters[q.id] = true;
        return (
          '<option value="' +
          escapeHtml(q.id) +
          '"' +
          (q.id === def ? " selected" : "") +
          ">" +
          escapeHtml(q.label || q.id) +
          "</option>"
        );
      })
      .join("");
    if (!sel.value && sel.options.length) sel.selectedIndex = 0;
    if (sel.value) saveRememberedQuarter(sel.value);
    validateForm();
  }

  function typePill(displayType) {
    var t = displayType || "Count";
    var cls = "hcsc-type-pill is-" + String(t).toLowerCase();
    return '<span class="' + cls + '">' + escapeHtml(t) + "</span>";
  }

  function sourceBadge(code, label) {
    var c = (code || "PI").toLowerCase();
    return (
      '<span class="hcsc-badge is-' +
      escapeHtml(c) +
      '" title="' +
      escapeHtml(label || code) +
      '">' +
      escapeHtml(code || "PI") +
      "</span>"
    );
  }

  function classificationBadge(row) {
    var code = row.classification_badge || "unresolved";
    var label = row.classification_badge_label || row.classification || "Unresolved";
    return (
      '<span class="hcsc-badge is-' +
      escapeHtml(code) +
      '" title="Classification: ' +
      escapeHtml(label) +
      '">' +
      escapeHtml(label) +
      "</span>"
    );
  }

  function uidCell(row) {
    var uid = row.source_uid || (row.dhis2_uids && row.dhis2_uids.value) || "";
    var tip = row.uid_tooltip || {};
    var tipText =
      "UID: " +
      (tip.uid || uid || "unresolved") +
      "\nType: " +
      (tip.source_type || row.source_badge_label || "") +
      "\nName: " +
      (tip.source_name || row.display_name || "") +
      "\nOwner: " +
      (tip.source_owner || row.source_owner || "") +
      "\nObject: " +
      (tip.source_object || row.source_table_view_reference || "") +
      "\nDefinition: " +
      (tip.definition || row.population_definition_reference || "");
    if (!uid) {
      return '<span class="muted">—</span>';
    }
    return (
      '<span class="hcsc-uid-wrap" title="' +
      escapeHtml(tipText) +
      '">' +
      sourceBadge(row.source_badge, row.source_badge_label) +
      ' <button type="button" class="hcsc-uid-link" data-key="' +
      escapeHtml(row.indicator_key) +
      '"><code>' +
      escapeHtml(uid) +
      "</code></button>" +
      ' <button type="button" class="hcsc-copy-uid" data-uid="' +
      escapeHtml(uid) +
      '" title="Copy UID" aria-label="Copy UID">⧉</button>' +
      ' <button type="button" class="hcsc-uid-btn" data-key="' +
      escapeHtml(row.indicator_key) +
      '" title="' +
      escapeHtml(tipText) +
      '" aria-label="Indicator details">ℹ</button>' +
      "</span>"
    );
  }

  function valueCell(row) {
    return '<div class="hcsc-value">' + escapeHtml(row.value_text || "—") + "</div>";
  }

  function validationCell(row) {
    var status = row.validation_status || "Not Yet Validated";
    var cls = "is-pending";
    var lower = String(status).toLowerCase();
    if (lower.indexOf("exact") >= 0) cls = "is-exact";
    else if (lower.indexOf("rounding") >= 0) cls = "is-rounding";
    else if (lower.indexOf("expected") >= 0) cls = "is-expected";
    else if (lower.indexOf("unexplained") >= 0) cls = "is-unexplained";
    else if (lower.indexOf("unavailable") >= 0) cls = "is-unavailable";
    var short =
      lower.indexOf("rounding") >= 0 && lower.indexOf("difference") >= 0
        ? "Rounding"
        : status;
    return (
      '<span class="hcsc-validation-cell" title="' +
      escapeHtml(status) +
      '"><span class="hcsc-val-dot ' +
      cls +
      '" aria-hidden="true"></span>' +
      escapeHtml(short) +
      "</span>"
    );
  }

  function calculationBasisText(row) {
    if (row.calculation_basis) return row.calculation_basis;
    if (row.display_result_type === "Count") {
      return row.population_definition_reference || row.definition || "—";
    }
    return "—";
  }

  function renderCards(rows) {
    var host = $("hcsc-cards");
    if (!host) return;
    var tones = ["is-blue", "is-green", "is-purple", "is-amber", "is-teal"];
    var byKey = {};
    (rows || []).forEach(function (r) {
      byKey[r.indicator_key] = r;
    });
    var hasData = CARD_KEYS.some(function (k) { return !!byKey[k]; });
    host.innerHTML = CARD_KEYS.map(function (k, i) {
      var r = byKey[k];
      var title = CARD_TITLES[k] || (r && r.display_name) || k;
      if (!hasData || !r) {
        return (
          '<article class="hcsc-card ' +
          tones[i] +
          ' hcsc-card-placeholder is-skeleton"><h3>' +
          escapeHtml(title) +
          '</h3><p class="hcsc-card-value hcsc-skel">&nbsp;</p><p class="hcsc-skel hcsc-skel-line">&nbsp;</p></article>'
        );
      }
      return (
        '<article class="hcsc-card ' +
        tones[i] +
        '"><h3>' +
        escapeHtml(title) +
        '</h3><p class="hcsc-card-value">' +
        escapeHtml(r.value_text || "—") +
        "</p>" +
        (r.calculation_basis
          ? '<p class="muted hcsc-basis">' + escapeHtml(r.calculation_basis) + "</p>"
          : "") +
        '<div class="hcsc-card-meta">' +
        sourceBadge(r.source_badge, r.source_badge_label) +
        " " +
        validationCell(r) +
        (r.last_updated
          ? ' <span class="muted">' +
            escapeHtml(String(r.last_updated).replace("T", " ").slice(0, 19)) +
            "</span>"
          : "") +
        "</div></article>"
      );
    }).join("");
  }

  function categoryMatches(row, category) {
    if (!category || category === "overview") return true;
    if (category === "unresolved") return !!row.unresolved || row.display_group === "unresolved";
    if (category === "eligible_beneficiaries") {
      return row.display_group === "eligible_beneficiaries" || row.display_group === "overview";
    }
    return row.display_group === category;
  }

  function filteredRows() {
    var q = (($("hcsc-filter") && $("hcsc-filter").value) || "").trim().toLowerCase();
    var typeF = (($("hcsc-filter-type") && $("hcsc-filter-type").value) || "").trim();
    var srcF = (($("hcsc-filter-source") && $("hcsc-filter-source").value) || "").trim();
    var valF = (($("hcsc-filter-validation") && $("hcsc-filter-validation").value) || "").trim();
    return state.results.filter(function (r) {
      if (!categoryMatches(r, state.activeCategory)) return false;
      if (typeF && r.display_result_type !== typeF) return false;
      if (srcF && String(r.source_badge || "").toUpperCase() !== srcF.toUpperCase()) return false;
      if (valF && String(r.validation_status || "Not Yet Validated") !== valF) return false;
      if (!q) return true;
      return (
        (r.display_name || "").toLowerCase().indexOf(q) >= 0 ||
        (r.indicator_key || "").toLowerCase().indexOf(q) >= 0 ||
        (r.category || "").toLowerCase().indexOf(q) >= 0 ||
        (r.source_uid || "").toLowerCase().indexOf(q) >= 0
      );
    });
  }

  function emptyTableHtml(kind) {
    if (kind === "filtered") {
      return (
        '<tr class="hcsc-empty-row"><td colspan="8"><div class="hcsc-empty">' +
        "<strong>No indicators match the current filters.</strong>" +
        '<p class="muted">Adjust search or filter dropdowns.</p>' +
        '<button type="button" class="btn btn-sm" id="hcsc-clear-filters">Clear Filters</button>' +
        "</div></td></tr>"
      );
    }
    return (
      '<tr class="hcsc-empty-row"><td colspan="8"><div class="hcsc-empty">' +
      "<strong>No indicators to display</strong>" +
      '<p class="muted">Select an organisation unit and generate the report.</p>' +
      "</div></td></tr>"
    );
  }

  function colHidden(name) {
    return !!hiddenCols[name];
  }

  function applyColumnVisibility() {
    var table = $("hcsc-table");
    if (!table) return;
    ["basis", "scope", "validation", "updated"].forEach(function (name) {
      var hide = colHidden(name);
      table.querySelectorAll('[data-col="' + name + '"]').forEach(function (el) {
        el.classList.toggle("is-col-hidden", hide);
      });
    });
  }

  function renderTable() {
    var tbody = $("hcsc-tbody");
    if (!tbody) return;
    if (!state.generated || !state.results.length) {
      tbody.innerHTML = emptyTableHtml("initial");
      applyColumnVisibility();
      return;
    }
    var rows = filteredRows();
    if (!rows.length) {
      tbody.innerHTML = emptyTableHtml("filtered");
      var clearBtn = $("hcsc-clear-filters");
      if (clearBtn) {
        clearBtn.addEventListener("click", function () {
          if ($("hcsc-filter")) $("hcsc-filter").value = "";
          if ($("hcsc-filter-type")) $("hcsc-filter-type").value = "";
          if ($("hcsc-filter-source")) $("hcsc-filter-source").value = "";
          if ($("hcsc-filter-validation")) $("hcsc-filter-validation").value = "";
          renderTable();
        });
      }
      applyColumnVisibility();
      return;
    }
    var sections = state.sections && state.sections.length
      ? state.sections
      : [{ id: "_all", label: "Indicators", results: rows }];
    var rfDomains = {
      maternal_health: true,
      child_nutrition_health: true,
      household_wash_sbc: true,
      food_security: true,
    };
    var html = [];
    sections.forEach(function (sec) {
      var secRows = rows.filter(function (r) {
        return !sec.id || sec.id === "_all" || r.display_group === sec.id;
      });
      var isRfParent = sec.id === "results_framework";
      var isRfDomain = !!sec.rf_domain || !!rfDomains[sec.id];
      if (isRfParent) {
        var anyRf = rows.some(function (r) {
          return rfDomains[r.display_group];
        });
        if (!anyRf && !secRows.length) return;
        html.push(
          '<tr class="hcsc-section-row hcsc-section-rf"><td colspan="8"><strong>' +
            escapeHtml(sec.label || "Results Framework") +
            "</strong></td></tr>"
        );
        return;
      }
      if (!secRows.length) return;
      var label = sec.label || sec.id;
      if (isRfDomain) label = "↳ " + label;
      html.push(
        '<tr class="hcsc-section-row' +
          (isRfDomain ? " hcsc-section-rf-domain" : "") +
          '"><td colspan="8"><strong>' +
          escapeHtml(label) +
          '</strong> <span class="muted">(' +
          secRows.length +
          ")</span></td></tr>"
      );
      secRows.forEach(function (r) {
        html.push(
          '<tr data-key="' +
            escapeHtml(r.indicator_key) +
            '">' +
            "<td><strong>" +
            escapeHtml(r.display_name) +
            "</strong> " +
            typePill(r.display_result_type) +
            " " +
            classificationBadge(r) +
            "</td>" +
            "<td>" +
            valueCell(r) +
            "</td>" +
            '<td data-col="basis"><div class="hcsc-basis">' +
            escapeHtml(calculationBasisText(r)) +
            "</div></td>" +
            '<td data-col="scope"><div class="hcsc-scope">' +
            escapeHtml(r.population_scope || "—") +
            "</div></td>" +
            "<td>" +
            sourceBadge(r.source_badge, r.source_badge_label) +
            "</td>" +
            "<td>" +
            uidCell(r) +
            "</td>" +
            '<td data-col="validation">' +
            validationCell(r) +
            "</td>" +
            '<td data-col="updated" class="muted">' +
            escapeHtml((r.last_updated || r.freshness || "").replace("T", " ").slice(0, 19)) +
            "</td>" +
            "</tr>"
        );
      });
    });
    tbody.innerHTML = html.join("") || emptyTableHtml("filtered");
    applyColumnVisibility();
  }

  function renderMapping() {
    var tbody = $("hcsc-mapping-tbody");
    if (!tbody) return;
    var rows =
      (state.retrieval &&
        state.retrieval.tabs &&
        state.retrieval.tabs.source_mapping &&
        state.retrieval.tabs.source_mapping.rows) ||
      state.results.map(function (r) {
        return {
          indicator_key: r.indicator_key,
          display_name: r.display_name,
          source_badge: r.source_badge,
          source_type: r.source_type,
          source_owner: r.source_owner,
          uid: r.source_uid,
          source_table_view_reference: r.source_table_view_reference,
          unresolved: r.unresolved,
        };
      });
    if (!rows.length) {
      tbody.innerHTML = '<tr><td colspan="7" class="muted">No mapping rows.</td></tr>';
      return;
    }
    tbody.innerHTML = rows
      .map(function (r) {
        return (
          "<tr>" +
          "<td>" +
          escapeHtml(r.display_name || r.indicator_key) +
          "</td>" +
          "<td>" +
          sourceBadge(r.source_badge) +
          "</td>" +
          "<td>" +
          escapeHtml(r.source_type || "") +
          "</td>" +
          "<td>" +
          escapeHtml(r.source_owner || "") +
          "</td>" +
          "<td><code>" +
          escapeHtml(r.uid || "—") +
          "</code></td>" +
          "<td>" +
          escapeHtml(r.source_table_view_reference || "—") +
          "</td>" +
          "<td>" +
          (r.unresolved ? "Unresolved" : "Mapped") +
          "</td>" +
          "</tr>"
        );
      })
      .join("");
  }

  function renderLineage() {
    var host = $("hcsc-lineage-body");
    if (!host) return;
    var refs =
      (state.retrieval &&
        state.retrieval.tabs &&
        state.retrieval.tabs.calculation &&
        state.retrieval.tabs.calculation.references) ||
      [];
    var fromResults = state.results
      .filter(function (r) {
        return r.lineage_reference || r.percentage_formula_reference;
      })
      .map(function (r) {
        return {
          display_name: r.display_name,
          formula_reference: r.percentage_formula_reference,
          lineage_reference: r.lineage_reference,
        };
      });
    var rows = refs.length ? refs : fromResults;
    if (!rows.length) {
      host.innerHTML =
        '<p class="muted">No lineage references in registry for the current Overview set. Unresolved items are marked, not guessed.</p>';
      return;
    }
    host.innerHTML =
      "<ul class=\"hcsc-lineage-list\">" +
      rows
        .map(function (r) {
          return (
            "<li><strong>" +
            escapeHtml(r.display_name || r.indicator_key) +
            "</strong>" +
            (r.lineage_reference
              ? '<div><code>' + escapeHtml(r.lineage_reference) + "</code></div>"
              : "") +
            (r.formula_reference
              ? '<div class="muted">' + escapeHtml(r.formula_reference) + "</div>"
              : "") +
            "</li>"
          );
        })
        .join("") +
      "</ul>";
  }

  function fmtNum(v) {
    if (v == null || v === "") return "—";
    var n = Number(v);
    if (isNaN(n)) return String(v);
    return Math.abs(n - Math.round(n)) < 1e-9 ? String(Math.round(n)) : n.toFixed(2);
  }

  function fillSelect(sel, values) {
    if (!sel) return;
    var cur = sel.value;
    sel.innerHTML =
      '<option value="">All</option>' +
      (values || [])
        .map(function (v) {
          return '<option value="' + escapeHtml(v) + '">' + escapeHtml(v) + "</option>";
        })
        .join("");
    if (cur) sel.value = cur;
  }

  function filteredValidationRows() {
    var rows = (state.validation && state.validation.comparisons) || [];
    var cat = ($("hcsc-val-category") && $("hcsc-val-category").value) || "";
    var st = ($("hcsc-val-status") && $("hcsc-val-status").value) || "";
    var src = ($("hcsc-val-source") && $("hcsc-val-source").value) || "";
    return rows.filter(function (r) {
      var catLabel = r.display_group_label || r.section_label || r.section || "";
      var srcLabel = r.comparison_source_label || r.comparison_source || "";
      if (cat && catLabel !== cat) return false;
      if (st && r.validation_status !== st) return false;
      if (src && srcLabel !== src && r.comparison_source !== src) return false;
      return true;
    });
  }

  function renderValidationCards(summary) {
    var host = $("hcsc-val-cards");
    if (!host) return;
    var by = (summary && summary.by_status) || {};
    var keys = [
      "Exact Match",
      "Rounding Difference",
      "Expected Logic Difference",
      "Unexplained Difference",
      "Not Yet Validated",
      "Comparison Source Unavailable",
    ];
    host.innerHTML = keys
      .map(function (k) {
        return (
          '<article class="hcsc-val-card"><h4>' +
          escapeHtml(k) +
          "</h4><p>" +
          escapeHtml(String(by[k] || 0)) +
          "</p></article>"
        );
      })
      .join("");
  }

  function renderValidation() {
    var tbody = $("hcsc-validation-tbody");
    if (!tbody) return;
    if (!state.validation) {
      tbody.innerHTML =
        '<tr><td colspan="7" class="muted">Generate a report, then Review Differences. Comparisons are read-only.</td></tr>';
      return;
    }
    renderValidationCards(state.validation.summary);
    fillSelect($("hcsc-val-category"), (state.validation.filters || {}).categories);
    fillSelect($("hcsc-val-status"), (state.validation.filters || {}).statuses);
    fillSelect($("hcsc-val-source"), (state.validation.filters || {}).sources);
    var rows = filteredValidationRows();
    if (!rows.length) {
      tbody.innerHTML = '<tr><td colspan="7" class="muted">No comparisons match filters.</td></tr>';
      return;
    }
    tbody.innerHTML = rows
      .map(function (r) {
        var delta =
          r.pp_diff != null
            ? fmtNum(r.pp_diff) + " pp"
            : r.value_diff != null
              ? fmtNum(r.value_diff)
              : "—";
        return (
          "<tr data-val-key=\"" +
          escapeHtml(r.indicator_key) +
          '">' +
          "<td><strong>" +
          escapeHtml(r.display_name) +
          "</strong><div class=\"muted\">" +
          escapeHtml(r.display_group_label || r.section_label || "") +
          "</div></td>" +
          "<td>" +
          fmtNum(r.primary_value) +
          '<div class="muted">' +
          escapeHtml(r.primary_source_label || r.primary_source || "DHIS2 Analytics Result") +
          "</div></td>" +
          "<td>" +
          fmtNum(r.comparison_value) +
          '<div class="muted">' +
          escapeHtml(r.comparison_source_label || r.comparison_source || "") +
          "</div></td>" +
          "<td>" +
          escapeHtml(delta) +
          "</td>" +
          "<td><span class=\"hcsc-val\">" +
          escapeHtml(r.validation_status || "") +
          "</span></td>" +
          "<td class=\"muted\">" +
          escapeHtml(r.note || "") +
          "</td>" +
          "<td>" +
          '<button type="button" class="btn btn-sm hcsc-val-detail" data-key="' +
          escapeHtml(r.indicator_key) +
          '">Details</button>' +
          "</td>" +
          "</tr>"
        );
      })
      .join("");
  }

  function openValidationDetail(key) {
    var rows = (state.validation && state.validation.comparisons) || [];
    var row = null;
    for (var i = 0; i < rows.length; i++) {
      if (rows[i].indicator_key === key) {
        row = rows[i];
        break;
      }
    }
    if (!row) return;
    var d = $("hcsc-drawer");
    var body = $("hcsc-drawer-body");
    var title = $("hcsc-drawer-title");
    if (title) title.textContent = row.display_name || key;
    if (body) {
      body.innerHTML =
        "<dl class=\"detail-list\">" +
        "<div><dt>Status</dt><dd>" +
        escapeHtml(row.validation_status || "") +
        "</dd></div>" +
        "<div><dt>DHIS2 Analytics Result</dt><dd>" +
        escapeHtml(String(row.primary_value)) +
        " · " +
        escapeHtml(row.primary_source_label || row.primary_source || "") +
        "</dd></div>" +
        "<div><dt>Comparison Source</dt><dd>" +
        escapeHtml(String(row.comparison_value)) +
        " · " +
        escapeHtml(row.comparison_source_label || row.comparison_source || "") +
        "</dd></div>" +
        "<div><dt>Numerator</dt><dd>" +
        fmtNum(row.numerator) +
        " / cmp " +
        fmtNum(row.comparison_numerator) +
        " (" +
        escapeHtml(row.numerator_label || "") +
        ")</dd></div>" +
        "<div><dt>Denominator</dt><dd>" +
        fmtNum(row.denominator) +
        " / cmp " +
        fmtNum(row.comparison_denominator) +
        " (" +
        escapeHtml(row.denominator_label || "") +
        ")</dd></div>" +
        "<div><dt>Compatibility</dt><dd>" +
        escapeHtml(row.compatibility_note || "") +
        "</dd></div>" +
        "<div><dt>Note</dt><dd>" +
        escapeHtml(row.note || "") +
        "</dd></div>" +
        "<div><dt>Evidence</dt><dd><pre class=\"hcsc-query-pre\">" +
        escapeHtml(JSON.stringify(row.evidence || {}, null, 2)) +
        "</pre></dd></div>" +
        "</dl>" +
        '<div class="hcsc-drawer-actions">' +
        '<button type="button" class="btn btn-sm" id="hcsc-copy-evidence">Copy Evidence</button> ' +
        '<button type="button" class="btn btn-sm" id="hcsc-copy-diagnostics">Copy Diagnostics</button> ' +
        (row.open_mapping_url
          ? '<a class="btn btn-sm" href="' + escapeHtml(row.open_mapping_url) + '">Open Mapping</a> '
          : "") +
        (row.open_sql_workspace_url
          ? '<a class="btn btn-sm" href="' + escapeHtml(row.open_sql_workspace_url) + '">Open SQL Workspace</a>'
          : "") +
        "</div>";
      var ce = $("hcsc-copy-evidence");
      if (ce) {
        ce.addEventListener("click", function () {
          copyText(JSON.stringify(row, null, 2));
        });
      }
      var cd = $("hcsc-copy-diagnostics");
      if (cd) {
        cd.addEventListener("click", function () {
          copyText(
            JSON.stringify(
              {
                indicator_key: row.indicator_key,
                validation_status: row.validation_status,
                note: row.note,
                evidence: row.evidence,
                timings: state.validation && state.validation.timings,
              },
              null,
              2
            )
          );
        });
      }
    }
    if (d) d.hidden = false;
    root.classList.add("is-drawer-open");
  }

  function scopeQuery(force) {
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
  }

  function loadValidation(force) {
    if (!validateForm()) {
      setStatus("Select a valid quarter and organisation unit before Compare Sources.", true);
      return;
    }
    var url = root.getAttribute("data-validation-url");
    if (!url) {
      setStatus("Compare Sources API not available.", true);
      return;
    }
    showShellBanner("Running validation…");
    setStatus("Validating…");
    fetch(url + scopeQuery(force), { credentials: "same-origin" })
      .then(function (r) {
        return r.json().then(function (body) {
          body._status = r.status;
          return body;
        });
      })
      .then(function (data) {
        if (!data.ok) {
          setStatus(data.error || "Compare Sources failed", true);
          return;
        }
        state.validation = data;
        renderValidation();
        setTab("validation");
        showShellBanner("");
        setStatus(
          "Compare Sources complete · " +
            ((data.summary && data.summary.total) || 0) +
            " comparisons · " +
            ((data.timings && data.timings.total_ms) || "?") +
            " ms" +
            (data.timings && data.timings.report_cache_hit ? " (report cache)" : "")
        );
      })
      .catch(function () {
        setStatus("Compare Sources request failed.", true);
      });
  }

  function saveValidationSnapshot() {
    if (!validateForm()) {
      setStatus("Select a valid quarter and organisation unit first.", true);
      return;
    }
    var env = ($("hcsc-env") && $("hcsc-env").value) || "stage";
    var period = selectedPeriod();
    var ou = selectedOu();
    fetch("/api/dhis2/hcsc-indicators/validation/snapshot", {
      method: "POST",
      credentials: "same-origin",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        environment: env,
        period: period,
        orgUnit: ou,
        disaggregation: ($("hcsc-disagg") && $("hcsc-disagg").value) || "none",
        note: "Manual Central Hub HCSC–RF Evidence Package from Compare Sources",
      }),
    })
      .then(function (r) {
        return r.json();
      })
      .then(function (data) {
        if (!data.ok) {
          setStatus(data.error || "Snapshot failed", true);
          return;
        }
        setStatus(
          "Central Hub HCSC–RF Evidence Package saved: " +
            ((data.snapshot && data.snapshot.id) || "")
        );
      })
      .catch(function () {
        setStatus("Snapshot request failed.", true);
      });
  }

  function renderRetrieval() {
    var retrieval = state.retrieval || state.lastPayload && state.lastPayload.query;
    var reqHost = $("hcsc-rtab-request");
    var calcHost = $("hcsc-rtab-calc");
    var mapHost = $("hcsc-rtab-map");
    if (!retrieval) return;
    var tabs = retrieval.tabs || {};
    var req = tabs.retrieval_request || {};
    var calc = tabs.calculation || {};
    var map = tabs.source_mapping || {};

    if (reqHost) {
      var sqlBlock = req.sql
        ? '<pre class="hcsc-query-pre">' + escapeHtml(req.sql) + "</pre>"
        : "";
      var capBlock = req.capability_ref
        ? "<p><strong>Capability:</strong> " + escapeHtml(req.capability_ref) + "</p>"
        : "";
      reqHost.innerHTML =
        "<p><strong>Retrieval method:</strong> " +
        escapeHtml(retrieval.retrieval_method || "") +
        "</p>" +
        "<p><strong>Endpoint:</strong> <code>" +
        escapeHtml(req.endpoint || "") +
        "</code></p>" +
        "<p><strong>Period:</strong> " +
        escapeHtml(req.period || "") +
        " · <strong>OU:</strong> " +
        escapeHtml(req.organisation_unit || "") +
        "</p>" +
        "<p><strong>dx UIDs:</strong> <code>" +
        escapeHtml((req.indicator_uids || []).join("; ")) +
        "</code></p>" +
        "<p><strong>Aggregation:</strong> " +
        escapeHtml(req.aggregation_request || "") +
        "</p>" +
        "<p>" +
        escapeHtml(req.readable || "") +
        "</p>" +
        sqlBlock +
        capBlock +
        '<pre class="hcsc-query-pre">' +
        escapeHtml(req.query_string || "") +
        "</pre>" +
        '<p class="muted">' +
        escapeHtml(req.note || "") +
        "</p>";
    }
    if (calcHost) {
      var refs = calc.references || [];
      calcHost.innerHTML =
        '<p class="muted">' +
        escapeHtml(calc.note || "") +
        "</p>" +
        (Object.keys(calc.pi_expressions || {}).length
          ? "<pre class=\"hcsc-query-pre\">" +
            escapeHtml(JSON.stringify(calc.pi_expressions, null, 2)) +
            "</pre>"
          : '<p class="muted">No PI numerator/denominator expressions loaded from metadata yet (not invented).</p>') +
        (refs.length
          ? "<ul>" +
            refs
              .map(function (r) {
                return (
                  "<li><strong>" +
                  escapeHtml(r.display_name || "") +
                  "</strong>: " +
                  escapeHtml(r.formula_reference || r.lineage_reference || "") +
                  "</li>"
                );
              })
              .join("") +
            "</ul>"
          : "");
    }
    if (mapHost) {
      var rows = map.rows || [];
      mapHost.innerHTML = rows.length
        ? "<ul>" +
          rows
            .map(function (r) {
              return (
                "<li>" +
                escapeHtml(r.display_name || "") +
                " · " +
                escapeHtml(r.source_badge || "") +
                " · <code>" +
                escapeHtml(r.uid || "—") +
                "</code></li>"
              );
            })
            .join("") +
          "</ul>"
        : '<p class="muted">No source mapping rows.</p>';
    }
  }

  function setTab(tab) {
    state.activeTab = tab;
    document.querySelectorAll(".hcsc-tab").forEach(function (btn) {
      var on = btn.getAttribute("data-tab") === tab;
      btn.classList.toggle("is-active", on);
      btn.setAttribute("aria-selected", on ? "true" : "false");
    });
    document.querySelectorAll(".hcsc-tab-panel").forEach(function (panel) {
      panel.hidden = panel.getAttribute("data-panel") !== tab;
    });
  }

  function setRtab(tab) {
    state.activeRtab = tab;
    document.querySelectorAll(".hcsc-subtab").forEach(function (btn) {
      btn.classList.toggle("is-active", btn.getAttribute("data-rtab") === tab);
    });
    document.querySelectorAll(".hcsc-rtab-panel").forEach(function (panel) {
      panel.hidden = panel.getAttribute("data-rpanel") !== tab;
    });
  }

  function openDrawer(key) {
    var url = (root.getAttribute("data-detail-url") || "").replace("__KEY__", encodeURIComponent(key));
    var drawer = $("hcsc-drawer");
    var body = $("hcsc-drawer-body");
    var title = $("hcsc-drawer-title");
    var badge = $("hcsc-drawer-badge");
    if (!drawer || !body) return;
    root.classList.add("is-drawer-open");
    drawer.hidden = false;
    body.innerHTML = '<p class="muted">Loading…</p>';
    var live = state.results.find(function (r) {
      return r.indicator_key === key;
    });
    fetch(url, { credentials: "same-origin" })
      .then(function (r) {
        return r.json();
      })
      .then(function (data) {
        var ind = (data && data.indicator) || {};
        var uids = ind.dhis2_uids || {};
        if (title) title.textContent = ind.display_name || "Indicator Details";
        if (badge) {
          badge.textContent = ind.source_badge || "PI";
          badge.className = "hcsc-badge is-" + String(ind.source_badge || "PI").toLowerCase();
        }
        var uid = uids.value || "";
        var basis =
          live && live.calculation_basis
            ? "<dt>Calculation basis</dt><dd>" + escapeHtml(live.calculation_basis) + "</dd>"
            : "";
        body.innerHTML =
          '<dl class="hcsc-dl">' +
          "<dt>Result type</dt><dd>" +
          escapeHtml(ind.display_result_type || ind.result_type || "") +
          "</dd>" +
          "<dt>Definition</dt><dd>" +
          escapeHtml(ind.definition || ind.population_definition_reference || "—") +
          "</dd>" +
          "<dt>Population / age range</dt><dd>" +
          escapeHtml(ind.population_definition_reference || "—") +
          (ind.age_range ? " · " + escapeHtml(ind.age_range) : "") +
          "</dd>" +
          "<dt>Numerator</dt><dd>" +
          escapeHtml(ind.numerator_label || "—") +
          (live && live.numerator != null ? " · " + escapeHtml(String(live.numerator)) : "") +
          "</dd>" +
          "<dt>Denominator</dt><dd>" +
          escapeHtml(ind.denominator_label || "—") +
          (live && live.denominator != null ? " · " + escapeHtml(String(live.denominator)) : "") +
          "</dd>" +
          basis +
          "<dt>Percentage formula</dt><dd>" +
          escapeHtml(ind.percentage_formula_reference || "—") +
          "</dd>" +
          "<dt>Source type / owner</dt><dd>" +
          escapeHtml(ind.source_badge_label || ind.source_type || "") +
          " · " +
          escapeHtml(ind.source_owner || "") +
          "</dd>" +
          "<dt>UID(s)</dt><dd><pre>" +
          escapeHtml(
            Object.keys(uids)
              .map(function (k) {
                return k + ": " + uids[k];
              })
              .join("\n") || "(none — unresolved)"
          ) +
          "</pre></dd>" +
          "<dt>Source table/view</dt><dd>" +
          escapeHtml(ind.source_table_view_reference || "—") +
          "</dd>" +
          "<dt>Source columns / DEs</dt><dd>" +
          escapeHtml(ind.source_columns_reference || "—") +
          "</dd>" +
          "<dt>Quarter / OU filters</dt><dd>" +
          escapeHtml(ind.quarter_rule_reference || "—") +
          " · " +
          escapeHtml(ind.organisation_unit_rule || "—") +
          "</dd>" +
          "<dt>Status / IP filters</dt><dd>" +
          escapeHtml(ind.status_filters_reference || "—") +
          " · " +
          escapeHtml(ind.ip_non_ip_rule || "—") +
          "</dd>" +
          "<dt>Lineage</dt><dd>" +
          escapeHtml(ind.lineage_reference || "—") +
          "</dd>" +
          "<dt>Validation</dt><dd>" +
          escapeHtml((live && live.validation_status) || "Not Yet Validated") +
          "</dd>" +
          "<dt>Last refreshed</dt><dd>" +
          escapeHtml((live && (live.last_updated || live.freshness)) || data.last_refreshed || "—") +
          "</dd>" +
          "<dt>Unresolved notes</dt><dd>" +
          escapeHtml(ind.notes || "—") +
          "</dd>" +
          "</dl>" +
          '<div class="hcsc-drawer-actions">' +
          '<button type="button" class="btn btn-sm" id="hcsc-copy-uid" data-uid="' +
          escapeHtml(uid) +
          '">Copy UID</button> ' +
          '<a class="btn btn-sm" id="hcsc-open-mapping" href="' +
          escapeHtml(data.open_mapping_url || "/dhis2/uid-explorer") +
          '">Open Mapping</a>' +
          "</div>";
        var copyBtn = $("hcsc-copy-uid");
        if (copyBtn) {
          copyBtn.onclick = function () {
            copyText(copyBtn.getAttribute("data-uid") || "");
          };
        }
      })
      .catch(function () {
        body.innerHTML = '<p class="muted">Failed to load details.</p>';
      });
  }

  function closeDrawer() {
    var d = $("hcsc-drawer");
    if (d) d.hidden = true;
    root.classList.remove("is-drawer-open");
  }

  function loadReport(force) {
    if (!validateForm({ revealErrors: true })) {
      state.statusMode = "error";
      setStatus("Select a valid quarter and organisation unit.", true);
      return;
    }
    var period = selectedPeriod();
    var ou = selectedOu();
    if (!isValidOuUid(ou)) {
      state.showFieldErrors = true;
      state.statusMode = "error";
      setStatus("Organisation unit selection was lost. Select it again, then Generate Report.", true);
      validateForm();
      return;
    }
    var started = Date.now();
    state.reportInFlight = true;
    state.statusMode = "loading";
    updateStatusStrip("Generating report…", "loading");
    showShellBanner("");
    validateForm();
    var reportUrl = root.getAttribute("data-report-url") || root.getAttribute("data-overview-url");
    if (!reportUrl) {
      state.reportInFlight = false;
      state.statusMode = "error";
      setStatus("Report API URL is missing from the page.", true);
      validateForm();
      return;
    }
    var url = reportUrl + scopeQuery(force);
    fetch(url, { credentials: "same-origin" })
      .then(function (r) {
        return r.json().then(function (body) {
          body._status = r.status;
          return body;
        });
      })
      .then(function (data) {
        state.reportInFlight = false;
        if (!data.ok) {
          state.statusMode = "error";
          state.lastRunOk = false;
          state.lastRunAt = new Date().toISOString();
          setStatus(data.error || "Report failed", true);
          validateForm();
          return;
        }
        showShellBanner("");
        state.results = data.results || [];
        state.sections = data.sections || [];
        state.retrieval = data.retrieval || data.query || null;
        state.lastPayload = data;
        state.generated = true;
        state.lastRunAt = data.freshness || new Date().toISOString();
        state.lastRunDurationMs =
          (data.timings && data.timings.total_ms != null
            ? data.timings.total_ms
            : Date.now() - started);
        state.lastRunOk = true;
        state.statusMode = "ready";
        renderCards(state.results);
        renderTable();
        renderMapping();
        renderLineage();
        renderValidation();
        renderRetrieval();
        var openSql = $("hcsc-open-sql");
        if (openSql) {
          var showSql =
            (state.retrieval && state.retrieval.open_sql_workspace) ||
            (state.results || []).some(function (r) {
              return r.approved_sql_query_id || r.approved_sql_reference;
            });
          openSql.hidden = !showSql;
        }
        updateStatusStrip("Ready to generate", "ready");
        var fresh = $("hcsc-freshness");
        if (fresh) fresh.textContent = "Last updated: " + (data.freshness || "");
        validateForm();
      })
      .catch(function () {
        state.reportInFlight = false;
        state.statusMode = "error";
        state.lastRunOk = false;
        state.lastRunAt = new Date().toISOString();
        setStatus("Report request failed. Check network or DHIS2 connectivity.", true);
        validateForm();
      });
  }

  // Backward-compatible alias used by older hooks/tests.
  function loadOverview(force) {
    loadReport(force);
  }

  function readQueryParams() {
    try {
      return new URLSearchParams(window.location.search || "");
    } catch (e) {
      return null;
    }
  }

  function hydrateFromQuery() {
    var qs = readQueryParams();
    if (!qs) return false;
    var env = (qs.get("environment") || qs.get("env") || "").trim().toLowerCase();
    var pe = (qs.get("period") || "").trim();
    var ou = (qs.get("orgUnit") || qs.get("org_unit") || "").trim();
    var disagg = (qs.get("disaggregation") || qs.get("disagg") || "").trim();
    var changed = false;
    var envSel = $("hcsc-env");
    if (envSel && (env === "live" || env === "stage") && envSel.value !== env) {
      envSel.value = env;
      changed = true;
      fillPeriods();
      if (ouPicker && ouPicker.onEnvironmentChange) ouPicker.onEnvironmentChange();
    }
    var peSel = $("hcsc-period");
    if (peSel && pe && allowedQuarters[pe] && peSel.value !== pe) {
      peSel.value = pe;
      saveRememberedQuarter(pe);
      changed = true;
    }
    var disSel = $("hcsc-disagg");
    if (disSel && disagg) {
      var has = Array.prototype.some.call(disSel.options || [], function (o) {
        return o.value === disagg;
      });
      if (has && disSel.value !== disagg) {
        disSel.value = disagg;
        changed = true;
      }
    }
    if (isValidOuUid(ou) && ouPicker && ouPicker.setSelection) {
      if (selectedOu() !== ou) {
        ouPicker.setSelection(ou, ou, ou);
        changed = true;
      }
    }
    return changed || !!(pe || ou || env);
  }

  function wireOuSearch() {
    if (!window.CentralHubOuPicker || !window.CentralHubOuPicker.create) {
      setStatus("Organisation unit picker failed to load.", true);
      return;
    }
    ouPicker = window.CentralHubOuPicker.create({
      root: $("hcsc-ou-picker"),
      hiddenEl: $("hcsc-ou"),
      pathEl: $("hcsc-ou-path"),
      chipRow: $("hcsc-ou-chip-row"),
      chipLabel: $("hcsc-ou-chip-label"),
      clearBtn: $("hcsc-ou-clear"),
      retryBtn: $("hcsc-ou-retry"),
      refreshMetaBtn: $("hcsc-ou-refresh-meta"),
      errorEl: $("hcsc-ou-error"),
      syncEl: $("hcsc-ou-sync"),
      searchEl: $("hcsc-ou-search"),
      searchResultsEl: $("hcsc-ou-search-results"),
      apiUrl: root.getAttribute("data-org-units-url") || "",
      getEnvironment: function () {
        return ($("hcsc-env") && $("hcsc-env").value) || "stage";
      },
      storagePrefix: "centralhub.hcsc.ou.",
      idPrefix: "hcsc-ou-",
      onChange: function () {
        validateForm();
      },
      onEnvironmentStatus: function (status) {
        if (status && status.maintenance) {
          showShellBanner(
            status.message ||
              (window.CentralHubOuPicker &&
                window.CentralHubOuPicker.MAINTENANCE_MESSAGE) ||
              "Stage is temporarily unavailable due to maintenance.",
            false
          );
        }
      },
    });
    // Bootstrap may already know Stage is under maintenance.
    var envs = (BOOT && BOOT.environments) || [];
    var stageMeta = envs.filter(function (e) { return e && e.id === "stage"; })[0];
    var currentEnv = ($("hcsc-env") && $("hcsc-env").value) || "stage";
    if (currentEnv === "stage" && stageMeta && stageMeta.maintenance) {
      showShellBanner(
        stageMeta.message ||
          "Stage is temporarily unavailable due to maintenance.",
        false
      );
    }
  }

  function fillDisagg() {
    var sel = $("hcsc-disagg");
    if (!sel) return;
    var opts = (boot.disaggregations || []).filter(function (d) {
      return d && !d.disabled;
    });
    if (!opts.length) {
      sel.innerHTML = '<option value="none">None</option>';
      return;
    }
    sel.innerHTML = opts
      .map(function (d) {
        var label = d.id === "none" ? "None" : d.label || d.id;
        return (
          '<option value="' +
          escapeHtml(d.id) +
          '">' +
          escapeHtml(label) +
          "</option>"
        );
      })
      .join("");
  }

  function exportVisible(kind) {
    var rows = filteredRows();
    if (kind === "json") {
      copyText(JSON.stringify(rows, null, 2));
      return;
    }
    var header = [
      "Indicator",
      "Value",
      "Calculation Basis",
      "Population / Scope",
      "Source",
      "UID",
      "Validation",
      "Last Updated",
    ];
    var lines = [header.join(",")];
    rows.forEach(function (r) {
      lines.push(
        [
          r.display_name,
          r.value_text,
          calculationBasisText(r),
          r.population_scope,
          r.source_badge,
          r.source_uid,
          r.validation_status,
          r.last_updated || r.freshness || "",
        ]
          .map(function (v) {
            return '"' + String(v == null ? "" : v).replace(/"/g, '""') + '"';
          })
          .join(",")
      );
    });
    copyText(lines.join("\n"));
  }

  function wire() {
    fillPeriods();
    fillDisagg();
    wireOuSearch();
    renderCards([]);
    renderTable();
    validateForm();
    var form = $("hcsc-controls");
    if (form) {
      form.addEventListener("submit", function (ev) {
        ev.preventDefault();
        ev.stopPropagation();
        loadReport(false);
        return false;
      });
    }
    var run = $("hcsc-run");
    if (run) {
      run.addEventListener("click", function (ev) {
        ev.preventDefault();
        loadReport(false);
      });
    }
    var refresh = $("hcsc-refresh");
    if (refresh) refresh.addEventListener("click", function () { loadReport(true); });
    var periodSel = $("hcsc-period");
    if (periodSel) {
      periodSel.addEventListener("change", function () {
        if (selectedPeriod()) saveRememberedQuarter(selectedPeriod());
        validateForm();
      });
    }
    var disagg = $("hcsc-disagg");
    if (disagg) disagg.addEventListener("change", function () { updateStatusStrip(); });
    var envSel = $("hcsc-env");
    if (envSel) {
      envSel.addEventListener("change", function () {
        fillPeriods();
        if (ouPicker && ouPicker.onEnvironmentChange) ouPicker.onEnvironmentChange();
        validateForm();
        showShellBanner("");
        var envs = (boot && boot.environments) || [];
        var meta = envs.filter(function (e) {
          return e && e.id === envSel.value;
        })[0];
        if (meta && meta.maintenance) {
          showShellBanner(
            meta.message || "Stage is temporarily unavailable due to maintenance.",
            false
          );
        }
      });
    }
    var filter = $("hcsc-filter");
    if (filter) filter.addEventListener("input", renderTable);
    ["hcsc-filter-type", "hcsc-filter-source", "hcsc-filter-validation"].forEach(function (id) {
      var el = $(id);
      if (el) el.addEventListener("change", renderTable);
    });
    document.querySelectorAll(".hcsc-cat").forEach(function (btn) {
      btn.addEventListener("click", function () {
        document.querySelectorAll(".hcsc-cat").forEach(function (b) {
          b.classList.toggle("is-active", b === btn);
        });
        state.activeCategory = btn.getAttribute("data-category") || "overview";
        renderTable();
      });
    });
    document.querySelectorAll("#hcsc-export-menu [data-export]").forEach(function (btn) {
      btn.addEventListener("click", function () {
        exportVisible(btn.getAttribute("data-export"));
        var menu = $("hcsc-export-menu");
        if (menu) menu.open = false;
      });
    });
    document.querySelectorAll("#hcsc-columns-menu [data-col]").forEach(function (inp) {
      inp.addEventListener("change", function () {
        hiddenCols[inp.getAttribute("data-col")] = !inp.checked;
        applyColumnVisibility();
      });
    });
    var valRun = $("hcsc-val-run");
    if (valRun) valRun.addEventListener("click", function () { loadValidation(false); });
    var valSnap = $("hcsc-val-snapshot");
    if (valSnap) valSnap.addEventListener("click", saveValidationSnapshot);
    ["hcsc-val-category", "hcsc-val-status", "hcsc-val-source"].forEach(function (id) {
      var el = $(id);
      if (el) el.addEventListener("change", renderValidation);
    });

    document.querySelectorAll(".hcsc-tab").forEach(function (btn) {
      btn.addEventListener("click", function () {
        setTab(btn.getAttribute("data-tab"));
      });
    });
    document.querySelectorAll(".hcsc-subtab").forEach(function (btn) {
      btn.addEventListener("click", function () {
        setRtab(btn.getAttribute("data-rtab"));
      });
    });

    root.addEventListener("click", function (ev) {
      var copy = ev.target.closest(".hcsc-copy-uid");
      if (copy) {
        copyText(copy.getAttribute("data-uid") || "");
        return;
      }
      var valBtn = ev.target.closest(".hcsc-val-detail");
      if (valBtn) {
        openValidationDetail(valBtn.getAttribute("data-key"));
        return;
      }
      var link = ev.target.closest(".hcsc-uid-link, .hcsc-uid-btn");
      if (link) {
        openDrawer(link.getAttribute("data-key"));
      }
    });
    var close = $("hcsc-drawer-close");
    if (close) close.addEventListener("click", closeDrawer);
    var drawer = $("hcsc-drawer");
    if (drawer) {
      drawer.addEventListener("click", function (ev) {
        if (ev.target === drawer) closeDrawer();
      });
    }
    var copyQ = $("hcsc-copy-query");
    if (copyQ) {
      copyQ.addEventListener("click", function () {
        var text =
          (state.retrieval && state.retrieval.copy_text) ||
          (state.retrieval &&
            state.retrieval.tabs &&
            state.retrieval.tabs.retrieval_request &&
            state.retrieval.tabs.retrieval_request.copy_text) ||
          "";
        copyText(text);
      });
    }

    // Restore controls from URL if present — do NOT auto-run analytics.
    // Awaiting selection must not trigger report/analytics endpoints.
    hydrateFromQuery();
    validateForm();
  }

  wire();
})();
