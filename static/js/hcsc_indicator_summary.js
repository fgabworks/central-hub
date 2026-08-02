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

  var GEN = {
    IDLE: "idle",
    AWAITING: "awaiting_selection",
    READY: "ready",
    GENERATING: "generating",
    SLOW: "slow",
    SUCCESS_FRESH: "success_fresh",
    SUCCESS_CACHED: "success_cached",
    SUCCESS_STALE: "success_stale",
    CANCELLED: "cancelled",
    TIMED_OUT: "timed_out",
    ERROR: "error",
  };
  var SLOW_AFTER_MS = 12000;
  var CLIENT_TIMEOUT_MS = 90000;

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
    genPhase: GEN.AWAITING,
    requestSeq: 0,
    activeRequestId: null,
    abortController: null,
    genStartedAt: null,
    genCompletedAt: null,
    elapsedTimer: null,
    slowTimer: null,
    timeoutTimer: null,
    cacheHit: false,
    staleReason: "",
    errorMessage: "",
    scopeKey: "",
    lastSuccessScopeKey: "",
    lastDiagnostics: "",
    updatingBackground: false,
    geographicBreakdown: "none",
    breakdown: null,
    geoEstimate: null,
    bdPage: 0,
    bdPageSize: 50,
    pendingForceRefresh: false,
    genSubPhase: "",
    bdLineage: null,
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
    var cur = state.geographicBreakdown || sel.value || "none";
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

  function registryIndicator(key) {
    var list = boot.indicators || [];
    for (var i = 0; i < list.length; i++) {
      if (list[i] && list[i].key === key) return list[i];
    }
    return null;
  }

  function findIndicatorByValueUid(uid) {
    if (!uid) return null;
    var list = boot.indicators || [];
    for (var i = 0; i < list.length; i++) {
      var u = list[i] && list[i].dhis2_uids && list[i].dhis2_uids.value;
      if (u && u === uid) return list[i];
    }
    return null;
  }

  function badgeCodeForSourceType(sourceType, sourceOwner) {
    var st = String(sourceType || "").toLowerCase();
    if (st === "program_indicator") return "PI";
    if (st === "indicator" || st === "aggregate_indicator") return "IND";
    if (st === "data_element") return "DE";
    if (st === "approved_sql" || st === "sql") return "SQL";
    if (st.indexOf("live_processing") >= 0) return "LP";
    var owner = String(sourceOwner || "").toLowerCase();
    if (owner.indexOf("live processing") >= 0) return "LP";
    if (owner.indexOf("sql") >= 0 || owner.indexOf("data_scripts") >= 0) return "SQL";
    return "";
  }

  function displayTypeOf(meta) {
    if (!meta) return "Count";
    if (meta.display_result_type) return meta.display_result_type;
    var rt = String(meta.result_type || "").toLowerCase();
    if (rt === "count") return "Count";
    if (rt === "ratio") return "Ratio";
    if (rt === "status" || rt === "derived_status") return "Status";
    if (
      rt === "percentage" ||
      rt === "numerator_denominator_percentage"
    ) {
      return "Percentage";
    }
    if (rt === "disaggregation") return "Disaggregation";
    return "Count";
  }

  function selectedBreakdownMeta() {
    var key =
      ($("hcsc-bd-indicator") && $("hcsc-bd-indicator").value) ||
      "eligible_households";
    var fromBoot = registryIndicator(key);
    var sample = null;
    var bd = state.breakdown;
    if (bd && bd.children) {
      for (var i = 0; i < bd.children.length; i++) {
        var results = bd.children[i].results || [];
        for (var j = 0; j < results.length; j++) {
          if (results[j].indicator_key === key) {
            sample = results[j];
            break;
          }
        }
        if (sample) break;
      }
    }
    var base = Object.assign({}, fromBoot || {}, sample || {});
    base.indicator_key = key;
    base.display_name =
      (sample && sample.display_name) ||
      (fromBoot && fromBoot.display_name) ||
      CARD_TITLES[key] ||
      key;
    return base;
  }

  function lineagePart(role, meta) {
    var uids = meta.dhis2_uids || {};
    var uid =
      role === "num"
        ? uids.numerator
        : role === "den"
          ? uids.denominator
          : uids.value || meta.source_uid;
    var label =
      role === "num"
        ? meta.numerator_label
        : role === "den"
          ? meta.denominator_label
          : meta.display_name;
    var companion = role === "result" ? null : findIndicatorByValueUid(uid);
    var sourceType =
      role === "result"
        ? meta.source_type
        : companion
          ? companion.source_type
          : null;
    var sourceOwner =
      role === "result"
        ? meta.source_owner
        : companion
          ? companion.source_owner
          : meta.source_owner;
    var unresolved = !uid || (role !== "result" && !(label || "").trim());
    var badge =
      role === "result"
        ? meta.source_badge || badgeCodeForSourceType(sourceType, sourceOwner)
        : badgeCodeForSourceType(sourceType, sourceOwner);
    var sourceUnresolved =
      !!unresolved || (role !== "result" && !!uid && !companion);
    return {
      role: role,
      label: label || (unresolved ? "Unresolved" : "—"),
      uid: uid || "",
      badge: badge,
      source_type: sourceType || "",
      source_type_label:
        sourceUnresolved && !badge
          ? "Unresolved"
          : badge === "PI"
            ? "Program Indicator"
            : badge === "IND"
              ? "Aggregate Indicator"
              : badge === "DE"
                ? "Data Element"
                : badge === "SQL"
                  ? "Approved query"
                  : badge === "LP"
                    ? "Live Processing capability"
                    : sourceType || "—",
      source_name:
        (companion && companion.display_name) ||
        label ||
        (role === "result" ? meta.display_name : "") ||
        "—",
      aggregation:
        (companion && companion.organisation_unit_rule) ||
        meta.organisation_unit_rule ||
        "DHIS2 Analytics ou/pe dimensions",
      population:
        (companion && companion.population_definition_reference) ||
        (role === "result"
          ? meta.population_definition_reference
          : null) ||
        "—",
      source_object:
        (companion && companion.source_table_view_reference) ||
        (role === "result" ? meta.source_table_view_reference : null) ||
        "—",
      indicator_key:
        (companion && companion.key) ||
        (role === "result" ? meta.indicator_key || meta.key : "") ||
        "",
      unresolved: sourceUnresolved,
    };
  }

  function readableFormula(meta) {
    var dtype = displayTypeOf(meta);
    if (dtype !== "Percentage" && dtype !== "Ratio") return "";
    var n = (meta.numerator_label || "").trim();
    var d = (meta.denominator_label || "").trim();
    if (n && d) {
      return dtype === "Ratio" ? n + " ÷ " + d : n + " ÷ " + d + " × 100";
    }
    if (meta.percentage_formula_reference) {
      return String(meta.percentage_formula_reference);
    }
    return "Unresolved";
  }

  function breakdownColumnMode(meta) {
    var dtype = displayTypeOf(meta);
    if (dtype === "Percentage" || dtype === "Ratio") return "percentage";
    if (dtype === "Status") return "status";
    return "count";
  }

  function hideBdTip() {
    var tip = $("hcsc-bd-tip");
    if (tip) tip.hidden = true;
  }

  function showBdTip(anchor, part) {
    var tip = $("hcsc-bd-tip");
    if (!tip || !part) return;
    var uid = part.uid || "";
    tip.innerHTML =
      '<div class="hcsc-bd-tip-head">' +
      escapeHtml(part.label || "Unresolved") +
      (part.unresolved
        ? ' <span class="hcsc-badge is-unresolved">Unresolved</span>'
        : "") +
      "</div>" +
      '<dl class="hcsc-bd-tip-dl">' +
      "<div><dt>Source type</dt><dd>" +
      escapeHtml(part.source_type_label || "—") +
      "</dd></div>" +
      "<div><dt>UID</dt><dd><code>" +
      escapeHtml(uid || "—") +
      "</code></dd></div>" +
      "<div><dt>Source name</dt><dd>" +
      escapeHtml(part.source_name || "—") +
      "</dd></div>" +
      "<div><dt>Aggregation</dt><dd>" +
      escapeHtml(part.aggregation || "—") +
      "</dd></div>" +
      "<div><dt>Population</dt><dd>" +
      escapeHtml(part.population || "—") +
      "</dd></div>" +
      "</dl>" +
      '<div class="hcsc-bd-tip-actions">' +
      '<button type="button" class="btn btn-sm" data-bd-copy-uid="' +
      escapeHtml(uid) +
      '"' +
      (uid ? "" : " disabled") +
      ">Copy UID</button>" +
      '<button type="button" class="btn btn-sm" data-bd-open-map="' +
      escapeHtml(part.indicator_key || "") +
      '"' +
      (part.indicator_key ? "" : " disabled") +
      ">Open Mapping</button>" +
      '<button type="button" class="btn btn-sm" data-bd-tip-close>Close</button>' +
      "</div>";
    tip.hidden = false;
    if (anchor && anchor.getBoundingClientRect) {
      var rect = anchor.getBoundingClientRect();
      var panel = $("hcsc-breakdown-panel");
      var pref = panel ? panel.getBoundingClientRect() : { top: 0, left: 0 };
      tip.style.top = Math.max(8, rect.bottom - pref.top + 6) + "px";
      tip.style.left =
        Math.max(8, Math.min(rect.left - pref.left, (panel ? panel.clientWidth : 400) - 280)) +
        "px";
    }
  }

  function renderBreakdownFormula(meta) {
    var host = $("hcsc-bd-formula");
    if (!host) return;
    if (!meta || !state.breakdown || state.breakdown.mode === "none") {
      host.hidden = true;
      host.innerHTML = "";
      return;
    }
    var mode = breakdownColumnMode(meta);
    var num = lineagePart("num", meta);
    var den = lineagePart("den", meta);
    var res = lineagePart("result", meta);
    var formula = readableFormula(meta);
    var lines =
      '<div class="hcsc-bd-formula-title">' +
      escapeHtml(meta.display_name || meta.indicator_key || "") +
      "</div>";
    if (mode === "percentage") {
      lines +=
        '<div class="hcsc-bd-formula-expr">' +
        escapeHtml(formula || "Unresolved") +
        "</div>";
      lines +=
        '<div class="hcsc-bd-formula-parts">' +
        '<span class="hcsc-bd-formula-part">N: ' +
        (num.badge
          ? sourceBadge(num.badge, num.source_type_label)
          : '<span class="hcsc-badge is-unresolved">Unresolved</span>') +
        " <code>" +
        escapeHtml(num.uid || "—") +
        "</code> · " +
        escapeHtml(num.label) +
        "</span>" +
        '<span class="hcsc-bd-formula-part">D: ' +
        (den.badge
          ? sourceBadge(den.badge, den.source_type_label)
          : '<span class="hcsc-badge is-unresolved">Unresolved</span>') +
        " <code>" +
        escapeHtml(den.uid || "—") +
        "</code> · " +
        escapeHtml(den.label) +
        "</span>" +
        '<span class="hcsc-bd-formula-part">Result: ' +
        (res.badge
          ? sourceBadge(res.badge, res.source_type_label)
          : '<span class="hcsc-badge is-unresolved">Unresolved</span>') +
        " <code>" +
        escapeHtml(res.uid || "—") +
        "</code></span>" +
        "</div>";
    } else if (mode === "status") {
      lines +=
        '<div class="hcsc-bd-formula-expr muted">Status indicator — no numerator/denominator lineage.</div>';
      lines +=
        '<div class="hcsc-bd-formula-parts"><span class="hcsc-bd-formula-part">Result: ' +
        (res.badge
          ? sourceBadge(res.badge, res.source_type_label)
          : '<span class="hcsc-badge is-unresolved">Unresolved</span>') +
        " <code>" +
        escapeHtml(res.uid || "—") +
        "</code></span></div>";
    } else {
      lines +=
        '<div class="hcsc-bd-formula-expr muted">Count indicator — numerator/denominator columns hidden.</div>';
      lines +=
        '<div class="hcsc-bd-formula-parts"><span class="hcsc-bd-formula-part">Result: ' +
        (res.badge
          ? sourceBadge(res.badge, res.source_type_label)
          : '<span class="hcsc-badge is-unresolved">Unresolved</span>') +
        " <code>" +
        escapeHtml(res.uid || "—") +
        "</code></span></div>";
    }
    lines +=
      '<div class="hcsc-bd-formula-actions">' +
      '<button type="button" class="btn btn-sm" data-bd-copy-uid="' +
      escapeHtml(res.uid || "") +
      '"' +
      (res.uid ? "" : " disabled") +
      ">Copy UID</button>" +
      '<button type="button" class="btn btn-sm" data-bd-open-map="' +
      escapeHtml(meta.indicator_key || meta.key || "") +
      '">Open Mapping</button>' +
      "</div>";
    host.innerHTML = lines;
    host.hidden = false;
  }

  function applyBreakdownColumnVisibility(mode) {
    var table = $("hcsc-breakdown-table");
    if (!table) return;
    table.setAttribute("data-bd-mode", mode || "count");
    var resultH = $("hcsc-bd-result-h");
    if (resultH) {
      resultH.textContent = mode === "status" ? "Status" : "Result";
    }
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
      hideBdTip();
      var formula = $("hcsc-bd-formula");
      if (formula) {
        formula.hidden = true;
        formula.innerHTML = "";
      }
      return;
    }
    panel.hidden = false;
    if (title) {
      title.textContent =
        bd.label ||
        ("Breakdown by " + (bd.mode || "").replace(/_/g, " "));
    }
    if (status) {
      if (bd.loading) {
        status.textContent = "Parent ready, breakdown loading…";
      } else if (bd.ok === false) {
        status.textContent =
          "Breakdown generation failed" + (bd.error ? ": " + bd.error : "");
      } else {
        status.textContent =
          (bd.child_count != null ? bd.child_count + " organisation units" : "") +
          (bd.cache && bd.cache.hit ? " · cached" : "");
      }
    }
    if (retry) retry.hidden = !(bd.ok === false && !bd.loading);

    var meta = selectedBreakdownMeta();
    var mode = breakdownColumnMode(meta);
    var showND = mode === "percentage";
    applyBreakdownColumnVisibility(mode);
    renderBreakdownFormula(meta);
    var numPart = lineagePart("num", meta);
    var denPart = lineagePart("den", meta);

    var indicator =
      (($("hcsc-bd-indicator") && $("hcsc-bd-indicator").value) ||
        "eligible_households");
    var q = (($("hcsc-bd-filter") && $("hcsc-bd-filter").value) || "")
      .trim()
      .toLowerCase();
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
    var colCount = showND ? 7 : 5;
    if (!pageRows.length) {
      tbody.innerHTML =
        '<tr class="hcsc-empty-row"><td colspan="' +
        colCount +
        '"><div class="hcsc-empty"><strong>No breakdown rows</strong></div></td></tr>';
      return;
    }
    tbody.innerHTML = pageRows
      .map(function (item) {
        var r = item.row || {};
        var resultCell =
          mode === "status"
            ? escapeHtml(r.value_text || r.notes || "—")
            : escapeHtml(r.value_text || "—");
        var numCell = showND
          ? "<td class=\"hcsc-bd-col-num\">" +
            '<span class="hcsc-bd-nd-val">' +
            escapeHtml(r.numerator != null ? r.numerator : "—") +
            '</span> <button type="button" class="hcsc-bd-info" data-bd-tip="num" title="Numerator lineage" aria-label="Numerator lineage">ⓘ</button>' +
            "</td>"
          : "";
        var denCell = showND
          ? "<td class=\"hcsc-bd-col-den\">" +
            '<span class="hcsc-bd-nd-val">' +
            escapeHtml(r.denominator != null ? r.denominator : "—") +
            '</span> <button type="button" class="hcsc-bd-info" data-bd-tip="den" title="Denominator lineage" aria-label="Denominator lineage">ⓘ</button>' +
            "</td>"
          : "";
        return (
          "<tr>" +
          '<td><button type="button" class="hcsc-bd-ou-link" data-ou="' +
          escapeHtml(item.child.org_unit || "") +
          '" title="' +
          escapeHtml(item.path || item.name) +
          '">' +
          escapeHtml(item.name) +
          "</button></td>" +
          numCell +
          denCell +
          "<td class=\"hcsc-bd-col-result\">" +
          resultCell +
          "</td>" +
          "<td>" +
          (r.source_badge
            ? sourceBadge(r.source_badge, r.source_badge_label)
            : "—") +
          "</td>" +
          "<td>" +
          escapeHtml(r.validation_status || "—") +
          "</td>" +
          "<td>" +
          escapeHtml(
            (r.last_updated || r.freshness || "—")
              .toString()
              .replace("T", " ")
              .slice(0, 19)
          ) +
          "</td>" +
          "</tr>"
        );
      })
      .join("");
    // Keep tip parts for header/value info clicks.
    state.bdLineage = { num: numPart, den: denPart, result: lineagePart("result", meta), mode: mode };
  }

  function exportBreakdownCsv() {
    var bd = state.breakdown;
    if (!bd || !bd.children) return;
    var meta = selectedBreakdownMeta();
    var mode = breakdownColumnMode(meta);
    var showND = mode === "percentage";
    var indicator =
      (($("hcsc-bd-indicator") && $("hcsc-bd-indicator").value) ||
        "eligible_households");
    var header = showND
      ? "Organisation Unit,UID,Path,Indicator,Numerator,Denominator,Result,Source,Validation,Last Updated,Environment,Period,Geographic Breakdown,Formula"
      : "Organisation Unit,UID,Path,Indicator,Result,Source,Validation,Last Updated,Environment,Period,Geographic Breakdown";
    var lines = [header];
    var formula = readableFormula(meta);
    bd.children.forEach(function (child) {
      var hit = null;
      (child.results || []).forEach(function (r) {
        if (r.indicator_key === indicator) hit = r;
      });
      var base = [
        child.org_unit_name,
        child.org_unit,
        child.hierarchy_path,
        indicator,
      ];
      if (showND) {
        base.push(
          hit && hit.numerator != null ? hit.numerator : "",
          hit && hit.denominator != null ? hit.denominator : ""
        );
      }
      base.push(
        hit && hit.value_text != null ? hit.value_text : "",
        hit && hit.source_badge != null ? hit.source_badge : "",
        hit && hit.validation_status != null ? hit.validation_status : "",
        hit && (hit.last_updated || hit.freshness)
          ? hit.last_updated || hit.freshness
          : "",
        ($("hcsc-env") && $("hcsc-env").value) || "",
        selectedPeriod() || "",
        selectedGeoBreakdown() || ""
      );
      if (showND) base.push(formula || "");
      lines.push(
        base
          .map(function (v) {
            return '"' + String(v == null ? "" : v).replace(/"/g, '""') + '"';
          })
          .join(",")
      );
    });
    copyText(lines.join("\n"));
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


  function currentScopeKey() {
    var env = (($("hcsc-env") && $("hcsc-env").value) || "stage").toLowerCase();
    var pe = selectedPeriod() || "";
    var ou = selectedOu() || "";
    var disagg = (($("hcsc-disagg") && $("hcsc-disagg").value) || "none");
    var geo = selectedGeoBreakdown() || "none";
    return env + "|" + pe + "|" + ou + "|" + disagg + "|" + geo;
  }

  function isActiveGeneration() {
    return (
      !!state.activeRequestId &&
      (state.genPhase === GEN.GENERATING || state.genPhase === GEN.SLOW)
    );
  }

  function clearGenTimers() {
    if (state.elapsedTimer) {
      clearInterval(state.elapsedTimer);
      state.elapsedTimer = null;
    }
    if (state.slowTimer) {
      clearTimeout(state.slowTimer);
      state.slowTimer = null;
    }
    if (state.timeoutTimer) {
      clearTimeout(state.timeoutTimer);
      state.timeoutTimer = null;
    }
  }

  function formatAge(fromMs) {
    if (!fromMs) return "";
    var sec = Math.max(0, Math.round((Date.now() - fromMs) / 1000));
    if (sec < 60) return sec + "s ago";
    var min = Math.floor(sec / 60);
    if (min < 60) return min + "m ago";
    var hr = Math.floor(min / 60);
    return hr + "h ago";
  }

  function formatGeneratedAgo(fromMsOrIso) {
    var fromMs =
      typeof fromMsOrIso === "number"
        ? fromMsOrIso
        : fromMsOrIso
          ? Date.parse(fromMsOrIso)
          : NaN;
    if (!fromMs || isNaN(fromMs)) return "Generated recently.";
    var sec = Math.max(0, Math.round((Date.now() - fromMs) / 1000));
    if (sec < 45) return "Generated just now.";
    if (sec < 90) return "Generated 1 minute ago.";
    var min = Math.floor(sec / 60);
    if (min < 60) return "Generated " + min + " minutes ago.";
    if (min < 120) return "Generated 1 hour ago.";
    var hr = Math.floor(min / 60);
    if (hr < 48) return "Generated " + hr + " hours ago.";
    var days = Math.floor(hr / 24);
    return "Generated " + days + " day" + (days === 1 ? "" : "s") + " ago.";
  }

  function statusTextsForPhase(phase, explicitMsg) {
    var elapsed = formatDuration(
      state.genStartedAt ? Date.now() - state.genStartedAt : 0
    );
    var badge = "Awaiting selection";
    var helper = "Select an organisation unit to continue.";
    var tone = "awaiting";

    if (phase === GEN.READY) {
      badge = "Ready to generate";
      helper = "All required parameters are selected.";
      tone = "ready";
    } else if (phase === GEN.GENERATING) {
      if (state.genSubPhase === "breakdown") {
        badge = "Generating breakdown";
        helper =
          (hasPriorResults()
            ? "Parent ready, breakdown loading · "
            : "Request in progress · ") + elapsed;
      } else if (state.genSubPhase === "parent") {
        badge = "Generating selected area";
        helper = "Request in progress · " + elapsed;
      } else {
        badge = "Generating report";
        helper = "Request in progress · " + elapsed;
      }
      tone = "generating";
    } else if (phase === GEN.SLOW) {
      badge =
        state.genSubPhase === "breakdown"
          ? "Still generating breakdown"
          : "Still generating";
      helper = "This is taking longer than usual · " + elapsed;
      tone = "slow";
    } else if (phase === GEN.SUCCESS_FRESH) {
      badge = "Report ready";
      helper = "Generated just now.";
      tone = "fresh";
    } else if (phase === GEN.SUCCESS_CACHED) {
      badge = "Cached result";
      helper = formatGeneratedAgo(state.genCompletedAt || state.lastRunAt);
      tone = "cached";
    } else if (phase === GEN.SUCCESS_STALE) {
      badge = "Stale result";
      helper = "Parameters changed. Generate the latest result.";
      tone = "stale";
    } else if (phase === GEN.CANCELLED) {
      badge = "Generation cancelled";
      helper = hasPriorResults()
        ? "Previous result kept. You can retry."
        : "No report was generated.";
      tone = "cancelled";
    } else if (phase === GEN.TIMED_OUT || phase === GEN.ERROR) {
      badge = "Generation failed";
      helper =
        explicitMsg ||
        state.errorMessage ||
        (phase === GEN.TIMED_OUT
          ? "Request timed out. Retry to try again."
          : "Report failed. Retry to try again.");
      tone = phase === GEN.TIMED_OUT ? "timeout" : "error";
    } else if (explicitMsg && phase === GEN.AWAITING) {
      helper = explicitMsg;
    }

    // Never repeat the exact badge text as the helper line.
    var norm = function (s) {
      return String(s || "")
        .replace(/[.…]+$/g, "")
        .replace(/\s+/g, " ")
        .trim()
        .toLowerCase();
    };
    if (norm(helper) === norm(badge)) {
      if (phase === GEN.READY) helper = "All required parameters are selected.";
      else if (phase === GEN.SUCCESS_CACHED) helper = "Refresh for a fresh pull.";
      else if (phase === GEN.SUCCESS_STALE) helper = "Generate the latest result.";
      else if (phase === GEN.ERROR || phase === GEN.TIMED_OUT)
        helper = "Retry to try again.";
      else helper = "Select an organisation unit to continue.";
    }
    return { badge: badge, helper: helper, tone: tone };
  }

  function hasPriorResults() {
    return !!(state.generated && state.results && state.results.length);
  }

  function deriveIdlePhase() {
    var pe = selectedPeriod();
    var ou = selectedOu();
    if (!(pe && ou)) return GEN.AWAITING;
    if (hasPriorResults() && state.lastSuccessScopeKey && state.lastSuccessScopeKey !== currentScopeKey()) {
      return GEN.SUCCESS_STALE;
    }
    if (hasPriorResults() && state.cacheHit) return GEN.SUCCESS_CACHED;
    if (hasPriorResults()) return GEN.SUCCESS_FRESH;
    return GEN.READY;
  }

  function setGenPhase(phase, opts) {
    var options = opts || {};
    state.genPhase = phase;
    state.statusMode =
      phase === GEN.AWAITING
        ? "need_ou"
        : phase === GEN.GENERATING || phase === GEN.SLOW
          ? "loading"
          : phase === GEN.ERROR || phase === GEN.TIMED_OUT
            ? "error"
            : "ready";
    state.reportInFlight = isActiveGeneration();
    if (options.staleReason != null) state.staleReason = options.staleReason;
    if (options.errorMessage != null) state.errorMessage = options.errorMessage;
    if (options.cacheHit != null) state.cacheHit = !!options.cacheHit;
    if (options.updatingBackground != null) state.updatingBackground = !!options.updatingBackground;
    applyGenUi(options.explicitMsg || "");
  }

  function buildDiagnostics() {
    return [
      "phase=" + state.genPhase,
      "requestId=" + (state.activeRequestId || "none"),
      "scope=" + currentScopeKey(),
      "lastSuccessScope=" + (state.lastSuccessScopeKey || "none"),
      "started=" + (state.genStartedAt ? new Date(state.genStartedAt).toISOString() : ""),
      "completed=" + (state.genCompletedAt ? new Date(state.genCompletedAt).toISOString() : ""),
      "cacheHit=" + !!state.cacheHit,
      "staleReason=" + (state.staleReason || ""),
      "error=" + (state.errorMessage || ""),
      "resultCount=" + ((state.results && state.results.length) || 0),
    ].join("\n");
  }

  function stopActiveRequest(reason) {
    clearGenTimers();
    if (state.abortController) {
      try {
        state.abortController.abort(reason || "cancelled");
      } catch (e) {}
    }
    state.abortController = null;
    state.activeRequestId = null;
    state.reportInFlight = false;
    state.updatingBackground = false;
  }

  function cancelGeneration(userInitiated) {
    if (!isActiveGeneration()) return;
    stopActiveRequest(userInitiated ? "user_cancel" : "superseded");
    state.lastDiagnostics = buildDiagnostics();
    setGenPhase(GEN.CANCELLED);
    renderCards(state.results);
    validateForm();
  }

  function markResultsStale(reason) {
    if (!hasPriorResults()) {
      setGenPhase(deriveIdlePhase());
      return;
    }
    if (isActiveGeneration()) {
      cancelGeneration(false);
    }
    setGenPhase(GEN.SUCCESS_STALE, {
      staleReason: reason || "Parameters changed",
    });
    renderCards(state.results);
    validateForm();
  }

  function onScopeMaybeChanged() {
    fillGeoBreakdown();
    var key = currentScopeKey();
    if (state.scopeKey && state.scopeKey !== key) {
      if (isActiveGeneration()) cancelGeneration(false);
      if (hasPriorResults() && state.lastSuccessScopeKey && state.lastSuccessScopeKey !== key) {
        markResultsStale("Parameters changed");
        state.scopeKey = key;
        return;
      }
    }
    state.scopeKey = key;
    if (!isActiveGeneration() && state.genPhase !== GEN.ERROR && state.genPhase !== GEN.TIMED_OUT && state.genPhase !== GEN.CANCELLED) {
      setGenPhase(deriveIdlePhase());
    } else if (!isActiveGeneration()) {
      applyGenUi();
    }
    validateForm();
  }

  function renderStatusActions() {
    var host = $("hcsc-status-actions");
    if (!host) return;
    var phase = state.genPhase;
    var buttons = [];
    if (phase === GEN.GENERATING || phase === GEN.SLOW) {
      buttons.push('<button type="button" class="btn btn-sm" data-gen-action="cancel">Cancel</button>');
    } else if (phase === GEN.SUCCESS_STALE) {
      buttons.push('<button type="button" class="btn btn-sm btn-primary" data-gen-action="generate-latest">Generate Latest</button>');
      buttons.push('<button type="button" class="btn btn-sm" data-gen-action="refresh">Refresh</button>');
    } else if (phase === GEN.SUCCESS_CACHED) {
      buttons.push('<button type="button" class="btn btn-sm" data-gen-action="refresh">Refresh</button>');
    } else if (phase === GEN.ERROR || phase === GEN.TIMED_OUT || phase === GEN.CANCELLED) {
      buttons.push('<button type="button" class="btn btn-sm btn-primary" data-gen-action="retry">Retry</button>');
      buttons.push('<button type="button" class="btn btn-sm" data-gen-action="copy-diagnostics">Copy Diagnostics</button>');
    }
    // Keep the actions row mounted for stable status-card height.
    host.hidden = false;
    host.innerHTML = buttons.length
      ? buttons.join(" ")
      : '<span class="hcsc-status-actions-spacer" aria-hidden="true"></span>';
  }

  function applyGenUi(explicitMsg) {
    var phase = state.genPhase || GEN.AWAITING;
    var strip = $("hcsc-status-strip");
    var el = $("hcsc-status");
    var badge = $("hcsc-status-badge");
    var meta = $("hcsc-status-meta");
    var pe = selectedPeriod();
    var ou = selectedOu();
    var texts = statusTextsForPhase(phase, explicitMsg || "");
    var badgeText = texts.badge;
    var msg = texts.helper;
    var tone = texts.tone;

    if (strip) {
      strip.setAttribute("data-gen-phase", phase);
      strip.className = "hcsc-status-strip is-" + tone;
    }
    if (badge) {
      badge.className = "hcsc-status-badge is-" + tone;
      // Animate only while a request is actively in flight.
      if (isActiveGeneration()) {
        badge.innerHTML =
          '<span class="hcsc-progress-spin" aria-hidden="true"></span> ' +
          escapeHtml(badgeText);
      } else {
        badge.textContent = badgeText;
      }
    }
    if (el) {
      el.textContent = msg;
      el.className = "hcsc-status-value is-" + tone;
    }
    // Keep meta slot present but empty so status-card height stays stable.
    if (meta) {
      meta.textContent = "";
      meta.hidden = true;
    }
    renderStatusActions();

    var readyMark = $("hcsc-ou-ready");
    if (readyMark) readyMark.hidden = !ou;
    var clearBtn = $("hcsc-ou-clear");
    if (clearBtn) clearBtn.disabled = !ou;

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
        '<span class="hcsc-param-chip"><span class="hcsc-chip-k">Population Filter</span> ' +
        escapeHtml(selectedPopulationLabel()) +
        "</span>" +
        '<span class="hcsc-param-chip"><span class="hcsc-chip-k">Geographic Breakdown</span> ' +
        escapeHtml(selectedGeoLabel()) +
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
          if (phase === GEN.SUCCESS_CACHED) {
            runBadge.textContent = "Cached";
            runBadge.className = "hcsc-run-badge is-cached";
          } else if (phase === GEN.SUCCESS_STALE) {
            runBadge.textContent = "Stale";
            runBadge.className = "hcsc-run-badge is-stale";
          } else if (state.lastRunOk === false) {
            runBadge.textContent = "Error";
            runBadge.className = "hcsc-run-badge is-error";
          } else {
            runBadge.textContent = "Success";
            runBadge.className = "hcsc-run-badge is-success";
          }
        }
      }
    }

    var cards = $("hcsc-cards");
    if (cards) {
      cards.classList.toggle("is-updating", isActiveGeneration() && hasPriorResults());
      cards.classList.toggle("is-generating-empty", isActiveGeneration() && !hasPriorResults());
      cards.classList.toggle("is-stale", phase === GEN.SUCCESS_STALE);
      cards.setAttribute(
        "data-cards-mode",
        isActiveGeneration() && !hasPriorResults()
          ? "skeleton"
          : hasPriorResults()
            ? "results"
            : "placeholder"
      );
    }

    var run = $("hcsc-run");
    var refresh = $("hcsc-refresh");
    var peOk = !!pe;
    var ouOk = !!ou;
    if (run) {
      run.disabled = !(peOk && ouOk) || isActiveGeneration();
      run.innerHTML = isActiveGeneration()
        ? '<span class="hcsc-btn-ico hcsc-spin" aria-hidden="true"></span> Generating…'
        : phase === GEN.SUCCESS_STALE
          ? '<span class="hcsc-btn-ico" aria-hidden="true">▦</span> Generate Latest'
          : '<span class="hcsc-btn-ico" aria-hidden="true">▦</span> Generate Report';
    }
    if (refresh) {
      if (isActiveGeneration()) {
        refresh.disabled = false;
        refresh.innerHTML = '<span class="hcsc-btn-ico" aria-hidden="true">✕</span> Cancel';
        refresh.title = "Cancel generation";
        refresh.setAttribute("data-mode", "cancel");
      } else {
        refresh.disabled = false;
        refresh.innerHTML = '<span class="hcsc-btn-ico" aria-hidden="true">↻</span> Refresh';
        refresh.title = "Force refresh report";
        refresh.setAttribute("data-mode", "refresh");
      }
    }
  }

  function updateStatusStrip(explicitMsg, mode) {
    // Legacy bridge: map old mode strings onto the generation state machine.
    if (mode === "loading") {
      if (!isActiveGeneration()) setGenPhase(GEN.GENERATING, { explicitMsg: explicitMsg });
      else applyGenUi(explicitMsg || "");
      return;
    }
    if (mode === "error") {
      setGenPhase(GEN.ERROR, {
        explicitMsg: explicitMsg || state.errorMessage || "Error",
        errorMessage: explicitMsg || state.errorMessage || "Error",
      });
      return;
    }
    if (mode === "ready") {
      if (!isActiveGeneration()) setGenPhase(deriveIdlePhase(), { explicitMsg: explicitMsg });
      else applyGenUi(explicitMsg || "");
      return;
    }
    if (!isActiveGeneration()) setGenPhase(deriveIdlePhase(), { explicitMsg: explicitMsg });
    else applyGenUi(explicitMsg || "");
  }

  function validateForm(opts) {
    var options = opts || {};
    if (options.revealErrors) state.showFieldErrors = true;
    var pe = selectedPeriod();
    var ou = selectedOu();
    var peOk = !!pe;
    var ouOk = !!ou;
    if (state.showFieldErrors) {
      setFieldError("hcsc-period-error", peOk ? "" : "Select a valid quarter.");
      setFieldError("hcsc-ou-error", ouOk ? "" : "Select an organisation unit.");
    } else {
      setFieldError("hcsc-period-error", "");
      setFieldError("hcsc-ou-error", "");
    }
    if (!isActiveGeneration()) {
      var next = deriveIdlePhase();
      // Preserve terminal cancelled/error/timeout until user retries or regenerates,
      // unless selection became incomplete.
      if (!peOk || !ouOk) {
        setGenPhase(GEN.AWAITING);
      } else if (
        state.genPhase === GEN.ERROR ||
        state.genPhase === GEN.TIMED_OUT ||
        state.genPhase === GEN.CANCELLED
      ) {
        applyGenUi();
      } else if (next === GEN.SUCCESS_STALE) {
        if (state.genPhase !== GEN.SUCCESS_STALE) {
          setGenPhase(GEN.SUCCESS_STALE, {
            staleReason: state.staleReason || "Parameters changed",
          });
          renderCards(state.results);
        } else {
          applyGenUi();
        }
      } else {
        setGenPhase(next);
      }
    } else {
      applyGenUi();
    }
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
    var rateKeys = {
      convergence_rate: true,
      completion_validated_eligible_rate: true,
    };
    var byKey = {};
    (rows || []).forEach(function (r) {
      byKey[r.indicator_key] = r;
    });
    var hasData = CARD_KEYS.some(function (k) { return !!byKey[k]; });
    var activeEmpty = isActiveGeneration() && !hasData;
    var activeUpdating = isActiveGeneration() && hasData;
    var stale = state.genPhase === GEN.SUCCESS_STALE;
    var cached = state.genPhase === GEN.SUCCESS_CACHED;
    var prevBadge =
      (state.genPhase === GEN.ERROR ||
        state.genPhase === GEN.TIMED_OUT ||
        state.genPhase === GEN.CANCELLED) &&
      hasData;

    var freshnessBadge = "";
    if (activeUpdating) {
      freshnessBadge =
        '<span class="hcsc-freshness-badge is-updating">Updating in background</span>';
    } else if (stale) {
      freshnessBadge =
        '<span class="hcsc-freshness-badge is-stale">Stale result</span>';
    } else if (cached) {
      freshnessBadge =
        '<span class="hcsc-freshness-badge is-cached">Cached result</span>';
    } else if (prevBadge) {
      freshnessBadge =
        '<span class="hcsc-freshness-badge is-previous">Previous result</span>';
    }

    host.innerHTML =
      (freshnessBadge
        ? '<div class="hcsc-cards-banner">' + freshnessBadge + "</div>"
        : "") +
      CARD_KEYS.map(function (k, i) {
        var r = byKey[k];
        var title = CARD_TITLES[k] || (r && r.display_name) || k;
        if (activeEmpty) {
          return (
            '<article class="hcsc-card ' +
            tones[i] +
            ' hcsc-card-skeleton" aria-busy="true"><h3>' +
            escapeHtml(title) +
            '</h3><p class="hcsc-card-value hcsc-skel-active">&nbsp;</p>' +
            '<p class="hcsc-skel-active hcsc-skel-line">&nbsp;</p></article>'
          );
        }
        if (!hasData || !r) {
          var placeholder = rateKeys[k] ? "— %" : "—";
          return (
            '<article class="hcsc-card ' +
            tones[i] +
            ' hcsc-card-placeholder"><h3>' +
            escapeHtml(title) +
            '</h3><p class="hcsc-card-value">' +
            placeholder +
            '</p><p class="muted hcsc-card-foot">Last refreshed: —</p></article>'
          );
        }
        var refreshed =
          (r.last_updated && String(r.last_updated).replace("T", " ").slice(0, 19)) ||
          (r.freshness && String(r.freshness).replace("T", " ").slice(0, 19)) ||
          "—";
        return (
          '<article class="hcsc-card ' +
          tones[i] +
          (stale ? " is-stale-card" : "") +
          (activeUpdating ? " is-updating-card" : "") +
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
          '</div><p class="muted hcsc-card-foot">Last refreshed: ' +
          escapeHtml(refreshed) +
          "</p></article>"
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
    if (kind === "empty_result") {
      return (
        '<tr class="hcsc-empty-row"><td colspan="8"><div class="hcsc-empty">' +
        "<strong>Report returned no indicators</strong>" +
        '<p class="muted">The request succeeded, but no rows were returned for this scope.</p>' +
        "</div></td></tr>"
      );
    }
    return (
      '<tr class="hcsc-empty-row"><td colspan="8"><div class="hcsc-empty">' +
      '<div class="hcsc-empty-ico" aria-hidden="true">⌕</div>' +
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
    if (!state.generated) {
      tbody.innerHTML = emptyTableHtml("initial");
      applyColumnVisibility();
      return;
    }
    if (!state.results.length) {
      tbody.innerHTML = emptyTableHtml("empty_result");
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

  function scopeQuery(force, geoOverride) {
    var env = ($("hcsc-env") && $("hcsc-env").value) || "stage";
    var period = selectedPeriod();
    var ou = selectedOu();
    var disagg = ($("hcsc-disagg") && $("hcsc-disagg").value) || "none";
    var geo =
      geoOverride != null && geoOverride !== undefined
        ? geoOverride
        : selectedGeoBreakdown() || "none";
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
      encodeURIComponent(geo || "none") +
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
    if (isActiveGeneration()) {
      return;
    }
    var wantGeo = selectedGeoBreakdown();
    var est = state.geoEstimate;
    if (
      !force &&
      wantGeo &&
      wantGeo !== "none" &&
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
    if (!validateForm({ revealErrors: true })) {
      setGenPhase(GEN.AWAITING, {
        explicitMsg: "Select a valid quarter and organisation unit.",
      });
      return;
    }
    var ou = selectedOu();
    if (!isValidOuUid(ou)) {
      state.showFieldErrors = true;
      setGenPhase(GEN.ERROR, {
        errorMessage: "Organisation unit selection was lost. Select it again, then Generate Report.",
        explicitMsg: "Organisation unit selection was lost. Select it again, then Generate Report.",
      });
      validateForm();
      return;
    }

    var reportUrl = root.getAttribute("data-report-url") || root.getAttribute("data-overview-url");
    if (!reportUrl) {
      setGenPhase(GEN.ERROR, {
        errorMessage: "Report API URL is missing from the page.",
        explicitMsg: "Report API URL is missing from the page.",
      });
      return;
    }

    stopActiveRequest("superseded");
    state.requestSeq += 1;
    var requestId = state.requestSeq;
    state.activeRequestId = requestId;
    state.genStartedAt = Date.now();
    state.genCompletedAt = null;
    state.errorMessage = "";
    state.staleReason = "";
    state.scopeKey = currentScopeKey();
    state.abortController =
      typeof AbortController !== "undefined" ? new AbortController() : null;
    state.updatingBackground = hasPriorResults();
    state.genSubPhase = wantGeo && wantGeo !== "none" ? "parent" : "";
    setGenPhase(GEN.GENERATING, {
      updatingBackground: hasPriorResults(),
    });
    showShellBanner("");
    renderCards(state.results);
    applyGenUi();

    clearGenTimers();
    state.elapsedTimer = setInterval(function () {
      if (state.activeRequestId !== requestId) return;
      applyGenUi();
    }, 500);
    state.slowTimer = setTimeout(function () {
      if (state.activeRequestId !== requestId) return;
      if (state.genPhase === GEN.GENERATING) {
        setGenPhase(GEN.SLOW);
        renderCards(state.results);
      }
    }, SLOW_AFTER_MS);
    var timeoutMs =
      wantGeo && wantGeo !== "none"
        ? Math.max(CLIENT_TIMEOUT_MS, 180000)
        : CLIENT_TIMEOUT_MS;
    state.timeoutTimer = setTimeout(function () {
      if (state.activeRequestId !== requestId) return;
      stopActiveRequest("timeout");
      state.lastRunOk = false;
      state.errorMessage = "Request timed out";
      state.lastDiagnostics = buildDiagnostics();
      state.genSubPhase = "";
      setGenPhase(GEN.TIMED_OUT, {
        explicitMsg: "Request timed out.",
        errorMessage: "Request timed out",
      });
      renderCards(state.results);
      validateForm();
    }, timeoutMs);

    function fetchJson(url) {
      var fetchOpts = { credentials: "same-origin" };
      if (state.abortController) fetchOpts.signal = state.abortController.signal;
      return fetch(url, fetchOpts).then(function (r) {
        return r.json().then(function (body) {
          body._status = r.status;
          return body;
        });
      });
    }

    function applyParentSuccess(data) {
      showShellBanner("");
      state.results = data.results || [];
      state.sections = data.sections || [];
      state.retrieval = data.retrieval || data.query || null;
      state.lastPayload = data;
      state.generated = true;
      state.lastRunAt = data.freshness || new Date().toISOString();
      state.lastRunDurationMs =
        data.timings && data.timings.total_ms != null
          ? data.timings.total_ms
          : Date.now() - state.genStartedAt;
      state.lastRunOk = true;
      state.cacheHit = !!(data.cache && data.cache.hit);
      renderCards(state.results);
      renderTable();
      renderMapping();
      renderLineage();
      renderValidation();
      renderRetrieval();
      var fresh = $("hcsc-freshness");
      if (fresh) fresh.textContent = "Last updated: " + (data.freshness || "");
    }

    function finishReportSuccess(data) {
      clearGenTimers();
      state.abortController = null;
      state.activeRequestId = null;
      state.reportInFlight = false;
      state.updatingBackground = false;
      state.genCompletedAt = Date.now();
      state.genSubPhase = "";
      showShellBanner("");
      state.results = data.results || state.results || [];
      state.sections = data.sections || state.sections || [];
      state.retrieval = data.retrieval || data.query || state.retrieval;
      state.lastPayload = data;
      state.generated = true;
      state.lastRunAt = data.freshness || new Date().toISOString();
      state.lastRunDurationMs =
        data.timings && data.timings.total_ms != null
          ? data.timings.total_ms
          : state.genCompletedAt - state.genStartedAt;
      state.lastRunOk = true;
      state.cacheHit = !!(data.cache && data.cache.hit);
      state.lastSuccessScopeKey = currentScopeKey();
      state.scopeKey = state.lastSuccessScopeKey;
      state.staleReason = "";
      state.lastDiagnostics = buildDiagnostics();
      state.breakdown = data.geographic_breakdown || { mode: "none", children: [] };
      state.bdPage = 0;
      setGenPhase(state.cacheHit ? GEN.SUCCESS_CACHED : GEN.SUCCESS_FRESH, {
        cacheHit: state.cacheHit,
      });
      try {
        renderCards(state.results);
        renderTable();
        renderMapping();
        renderLineage();
        renderValidation();
        renderRetrieval();
        fillBdIndicators();
        renderBreakdown();
      } catch (renderErr) {
        state.lastRunOk = false;
        state.errorMessage =
          "Report response could not be rendered: " +
          ((renderErr && renderErr.message) || "unknown error");
        state.lastDiagnostics = buildDiagnostics();
        setGenPhase(GEN.ERROR, {
          explicitMsg: state.errorMessage,
          errorMessage: state.errorMessage,
        });
        validateForm();
        return;
      }
      var openSql = $("hcsc-open-sql");
      if (openSql) {
        var showSql =
          (state.retrieval && state.retrieval.open_sql_workspace) ||
          (state.results || []).some(function (r) {
            return r.approved_sql_query_id || r.approved_sql_reference;
          });
        openSql.hidden = !showSql;
      }
      var fresh = $("hcsc-freshness");
      if (fresh) fresh.textContent = "Last updated: " + (data.freshness || "");
      validateForm();
    }

    function failWhole(message) {
      clearGenTimers();
      state.abortController = null;
      state.activeRequestId = null;
      state.reportInFlight = false;
      state.updatingBackground = false;
      state.genCompletedAt = Date.now();
      state.genSubPhase = "";
      state.lastRunOk = false;
      state.errorMessage = message || "Report failed";
      state.lastDiagnostics = buildDiagnostics();
      setGenPhase(GEN.ERROR, {
        explicitMsg: state.errorMessage,
        errorMessage: state.errorMessage,
      });
      renderCards(state.results);
      validateForm();
    }

    function failBreakdownOnly(message) {
      clearGenTimers();
      state.abortController = null;
      state.activeRequestId = null;
      state.reportInFlight = false;
      state.updatingBackground = false;
      state.genCompletedAt = Date.now();
      state.genSubPhase = "";
      state.lastRunOk = true;
      state.lastSuccessScopeKey = currentScopeKey();
      state.scopeKey = state.lastSuccessScopeKey;
      state.breakdown = {
        mode: wantGeo,
        ok: false,
        loading: false,
        children: [],
        label: selectedGeoLabel(),
        error: message || "Breakdown generation failed",
      };
      state.lastDiagnostics = buildDiagnostics();
      setGenPhase(GEN.SUCCESS_FRESH);
      fillBdIndicators();
      renderBreakdown();
      applyGenUi();
      validateForm();
    }

    function handleAbort(err) {
      if (state.activeRequestId !== requestId) return;
      var aborted =
        (err && (err.name === "AbortError" || err.code === 20)) ||
        (state.abortController &&
          state.abortController.signal &&
          state.abortController.signal.aborted);
      clearGenTimers();
      state.abortController = null;
      state.activeRequestId = null;
      state.reportInFlight = false;
      state.updatingBackground = false;
      state.genCompletedAt = Date.now();
      state.genSubPhase = "";
      if (aborted) {
        if (state.genPhase === GEN.GENERATING || state.genPhase === GEN.SLOW) {
          setGenPhase(GEN.CANCELLED);
        }
        renderCards(state.results);
        validateForm();
        return;
      }
      failWhole("Report request failed. Check network or DHIS2 connectivity.");
    }

    if (!wantGeo || wantGeo === "none") {
      fetchJson(reportUrl + scopeQuery(force, "none"))
        .then(function (data) {
          if (state.activeRequestId !== requestId) return; // late / superseded
          if (!data.ok) {
            failWhole(data.error || "Report failed");
            return;
          }
          finishReportSuccess(data);
        })
        .catch(handleAbort);
      return;
    }

    // Two-phase: parent summary first, then breakdown while parent stays visible.
    fetchJson(reportUrl + scopeQuery(force, "none"))
      .then(function (parentData) {
        if (state.activeRequestId !== requestId) return; // late / superseded
        if (!parentData.ok) {
          failWhole(parentData.error || "Report failed");
          return null;
        }
        applyParentSuccess(parentData);
        state.breakdown = {
          mode: wantGeo,
          ok: true,
          loading: true,
          children: [],
          label: selectedGeoLabel(),
          child_count: (est && est.child_count) || null,
        };
        fillBdIndicators();
        renderBreakdown();
        state.genSubPhase = "breakdown";
        if (state.genPhase === GEN.SLOW) setGenPhase(GEN.SLOW);
        else setGenPhase(GEN.GENERATING);
        applyGenUi();
        return fetchJson(reportUrl + scopeQuery(force, wantGeo));
      })
      .then(function (data) {
        if (data == null) return;
        if (state.activeRequestId !== requestId) return; // late / superseded
        if (!data.ok) {
          failBreakdownOnly(data.error || "Breakdown generation failed");
          return;
        }
        var geo = data.geographic_breakdown || {};
        if (geo.ok === false) {
          // Parent payload from phase-2 is fine; keep phase-1 parent if needed.
          if (data.results && data.results.length) applyParentSuccess(data);
          failBreakdownOnly(geo.error || "Breakdown generation failed");
          return;
        }
        finishReportSuccess(data);
      })
      .catch(function (err) {
        if (state.activeRequestId !== requestId) return;
        if (state.generated && state.results && state.results.length) {
          var aborted =
            (err && (err.name === "AbortError" || err.code === 20)) ||
            (state.abortController &&
              state.abortController.signal &&
              state.abortController.signal.aborted);
          if (aborted) {
            handleAbort(err);
            return;
          }
          failBreakdownOnly("Breakdown request failed. Check network or DHIS2 connectivity.");
          return;
        }
        handleAbort(err);
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
    var geoBd = (
      qs.get("geographicBreakdown") ||
      qs.get("geographic_breakdown") ||
      ""
    ).trim();
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
    if (geoBd) {
      var geoSel = $("hcsc-geo-breakdown");
      if (geoSel) {
        // Options may be filled after OU level resolves; stash preferred value.
        state.geographicBreakdown = geoBd;
        var hasGeo = Array.prototype.some.call(geoSel.options || [], function (o) {
          return o.value === geoBd;
        });
        if (hasGeo && geoSel.value !== geoBd) {
          geoSel.value = geoBd;
          changed = true;
        }
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
      root: $("hcsc-controls") || $("hcsc-ou-picker"),
      hiddenEl: $("hcsc-ou"),
      pathEl: null,
      chipRow: $("hcsc-ou-selected-box"),
      chipLabel: $("hcsc-ou-chip-label"),
      clearBtn: $("hcsc-ou-clear"),
      retryBtn: $("hcsc-ou-retry"),
      refreshMetaBtn: $("hcsc-ou-refresh-meta"),
      errorEl: $("hcsc-ou-error"),
      syncEl: null,
      searchEl: $("hcsc-ou-search"),
      searchResultsEl: $("hcsc-ou-search-results"),
      apiUrl: root.getAttribute("data-org-units-url") || "",
      getEnvironment: function () {
        return ($("hcsc-env") && $("hcsc-env").value) || "stage";
      },
      storagePrefix: "centralhub.hcsc.ou.",
      idPrefix: "hcsc-ou-",
      onChange: function () {
        onScopeMaybeChanged();
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
    var envs = (boot && boot.environments) || [];
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
    var source = boot.population_filters || boot.disaggregations || [];
    var opts = source.filter(function (d) {
      return d && !d.disabled;
    });
    if (!opts.length) {
      sel.innerHTML = '<option value="none">All Households</option>';
      return;
    }
    sel.innerHTML = opts
      .map(function (d) {
        var label =
          d.id === "none" ? d.label || "All Households" : d.label || d.id;
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

  function fillBdIndicators() {
    var sel = $("hcsc-bd-indicator");
    if (!sel) return;
    var keys = boot.overview_keys || [];
    var labels = CARD_TITLES || {};
    var cur = sel.value;
    if (!keys.length) {
      sel.innerHTML = '<option value="eligible_households">Eligible Households</option>';
      return;
    }
    sel.innerHTML = keys
      .map(function (k) {
        return (
          '<option value="' +
          escapeHtml(k) +
          '">' +
          escapeHtml(labels[k] || k) +
          "</option>"
        );
      })
      .join("");
    if (cur) sel.value = cur;
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
    fillGeoBreakdown();
    try {
      wireOuSearch();
    } catch (ouErr) {
      showShellBanner(
        "Organisation unit picker failed to initialize: " +
          ((ouErr && ouErr.message) || "unknown error"),
        true
      );
    }
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
        ev.stopPropagation();
        try {
          loadReport(
            state.genPhase === GEN.SUCCESS_STALE || state.genPhase === GEN.SUCCESS_CACHED
          );
        } catch (genErr) {
          setGenPhase(GEN.ERROR, {
            errorMessage:
              "Generate failed to start: " +
              ((genErr && genErr.message) || "unknown error"),
            explicitMsg:
              "Generate failed to start: " +
              ((genErr && genErr.message) || "unknown error"),
          });
        }
      });
    }
    var refresh = $("hcsc-refresh");
    if (refresh) {
      refresh.addEventListener("click", function () {
        if (refresh.getAttribute("data-mode") === "cancel") {
          cancelGeneration(true);
          return;
        }
        loadReport(true);
      });
    }
    var statusActions = $("hcsc-status-actions");
    if (statusActions) {
      statusActions.addEventListener("click", function (ev) {
        var btn = ev.target.closest("[data-gen-action]");
        if (!btn) return;
        var action = btn.getAttribute("data-gen-action");
        if (action === "cancel") {
          cancelGeneration(true);
        } else if (action === "retry" || action === "refresh" || action === "generate-latest") {
          loadReport(true);
        } else if (action === "copy-diagnostics") {
          copyText(state.lastDiagnostics || buildDiagnostics());
        }
      });
    }
    var periodSel = $("hcsc-period");
    if (periodSel) {
      periodSel.addEventListener("change", function () {
        if (selectedPeriod()) saveRememberedQuarter(selectedPeriod());
        onScopeMaybeChanged();
      });
    }
    var disagg = $("hcsc-disagg");
    if (disagg) disagg.addEventListener("change", function () { onScopeMaybeChanged(); });
    var envSel = $("hcsc-env");
    if (envSel) {
      envSel.addEventListener("change", function () {
        fillPeriods();
        if (ouPicker && ouPicker.onEnvironmentChange) ouPicker.onEnvironmentChange();
        onScopeMaybeChanged();
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
    var bdPanel = $("hcsc-breakdown-panel");
    if (bdPanel) {
      bdPanel.addEventListener("click", function (ev) {
        var copyBtn = ev.target.closest("[data-bd-copy-uid]");
        if (copyBtn) {
          copyText(copyBtn.getAttribute("data-bd-copy-uid") || "");
          return;
        }
        var mapBtn = ev.target.closest("[data-bd-open-map]");
        if (mapBtn) {
          var mapKey = mapBtn.getAttribute("data-bd-open-map") || "";
          hideBdTip();
          setTab("mapping");
          if (mapKey) openDrawer(mapKey);
          return;
        }
        if (ev.target.closest("[data-bd-tip-close]")) {
          hideBdTip();
          return;
        }
        var info = ev.target.closest("[data-bd-tip]");
        if (info) {
          var tipRole = info.getAttribute("data-bd-tip");
          var part =
            (state.bdLineage && state.bdLineage[tipRole]) ||
            lineagePart(tipRole, selectedBreakdownMeta());
          showBdTip(info, part);
          return;
        }
      });
    }
    document.addEventListener("click", function (ev) {
      var tip = $("hcsc-bd-tip");
      if (!tip || tip.hidden) return;
      if (ev.target.closest("#hcsc-bd-tip") || ev.target.closest("[data-bd-tip]")) {
        return;
      }
      hideBdTip();
    });
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

    // Restore controls from URL if present — do NOT auto-run analytics.
    // Awaiting selection must not trigger report/analytics endpoints.
    hydrateFromQuery();
    validateForm();
  }

  wire();
})();
