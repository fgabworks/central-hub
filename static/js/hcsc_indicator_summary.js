/**
 * HCSC Indicator Summary & Data Lineage — NPMO UI
 * Read-only presentation of registry + batched analytics. No formula engine.
 */
(function () {
  "use strict";

  var root = document.getElementById("hcsc-root");
  if (!root) return;

  var boot = {};
  try {
    boot = JSON.parse(root.getAttribute("data-bootstrap") || "{}");
  } catch (e) {
    boot = {};
  }

  var state = {
    results: [],
    sections: [],
    retrieval: null,
    lastPayload: null,
    activeTab: "summary",
    activeRtab: "retrieval_request",
  };

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

  function setStatus(msg, isError) {
    var el = $("hcsc-status");
    if (!el) return;
    el.textContent = msg || "";
    el.classList.toggle("is-error", !!isError);
  }

  function fillPeriods() {
    var sel = $("hcsc-period");
    if (!sel) return;
    var quarters = (boot.periods && boot.periods.quarters) || [];
    var def = (boot.periods && boot.periods.default_period) || "";
    sel.innerHTML = quarters
      .map(function (q) {
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
    var main = escapeHtml(row.value_text || "—");
    var basis = row.calculation_basis
      ? '<div class="hcsc-basis">' + escapeHtml(row.calculation_basis) + "</div>"
      : "";
    var popHint =
      row.display_result_type === "Count" && row.population_definition_reference
        ? '<div class="muted hcsc-basis">' + escapeHtml(row.population_definition_reference) + "</div>"
        : "";
    // Count population goes in Population/Scope column; avoid duplicating unless basis empty.
    if (row.display_result_type === "Count") popHint = "";
    return "<div class=\"hcsc-value\">" + main + basis + popHint + "</div>";
  }

  function renderCards(rows) {
    var host = $("hcsc-cards");
    if (!host) return;
    var keys = [
      "eligible_households",
      "approved_eligible_households",
      "convergent_households",
      "convergence_rate",
      "completion_validated_eligible_rate",
    ];
    var tones = ["is-blue", "is-green", "is-purple", "is-amber", "is-teal"];
    var byKey = {};
    rows.forEach(function (r) {
      byKey[r.indicator_key] = r;
    });
    host.innerHTML = keys
      .map(function (k, i) {
        var r = byKey[k];
        if (!r) return "";
        return (
          '<article class="hcsc-card ' +
          tones[i] +
          '">' +
          "<h3>" +
          escapeHtml(r.display_name) +
          "</h3>" +
          '<p class="hcsc-card-value">' +
          escapeHtml(r.value_text || "—") +
          "</p>" +
          '<p class="muted">' +
          escapeHtml(r.validation_status || "") +
          "</p>" +
          "</article>"
        );
      })
      .join("");
  }

  function filteredRows() {
    var q = (($("hcsc-filter") && $("hcsc-filter").value) || "").trim().toLowerCase();
    var pctOnly = $("hcsc-pct-only") && $("hcsc-pct-only").checked;
    return state.results.filter(function (r) {
      if (pctOnly && r.display_result_type !== "Percentage" && r.display_result_type !== "Ratio") {
        return false;
      }
      if (!q) return true;
      return (
        (r.display_name || "").toLowerCase().indexOf(q) >= 0 ||
        (r.indicator_key || "").toLowerCase().indexOf(q) >= 0 ||
        (r.category || "").toLowerCase().indexOf(q) >= 0 ||
        (r.source_uid || "").toLowerCase().indexOf(q) >= 0
      );
    });
  }

  function renderTable() {
    var tbody = $("hcsc-tbody");
    if (!tbody) return;
    var rows = filteredRows();
    if (!rows.length) {
      tbody.innerHTML = '<tr><td colspan="7" class="muted">No matching indicators.</td></tr>';
      return;
    }
    var sections = state.sections && state.sections.length
      ? state.sections
      : [{ id: "_all", label: "Indicators", results: rows }];
    var html = [];
    sections.forEach(function (sec) {
      var secRows = rows.filter(function (r) {
        return !sec.id || sec.id === "_all" || r.section === sec.id;
      });
      if (!secRows.length) return;
      html.push(
        '<tr class="hcsc-section-row"><td colspan="7"><strong>' +
          escapeHtml(sec.label || sec.id) +
          "</strong> <span class=\"muted\">(" +
          secRows.length +
          ")</span></td></tr>"
      );
      secRows.forEach(function (r) {
        var def = r.definition || r.population_definition_reference || "";
        html.push(
          "<tr data-key=\"" +
            escapeHtml(r.indicator_key) +
            '">' +
            "<td><strong>" +
            escapeHtml(r.display_name) +
            "</strong>" +
            (r.unresolved
              ? ' <span class="hcsc-type-pill is-status">Unresolved</span>'
              : "") +
            (def
              ? '<div class="muted hcsc-ind-def">' + escapeHtml(def) + "</div>"
              : "") +
            "</td>" +
            "<td>" +
            typePill(r.display_result_type) +
            "</td>" +
            "<td>" +
            valueCell(r) +
            "</td>" +
            "<td><div class=\"hcsc-scope\">" +
            escapeHtml(r.population_scope || "—") +
            "</div></td>" +
            "<td>" +
            sourceBadge(r.source_badge, r.source_badge_label) +
            ' <span class="muted">' +
            escapeHtml(r.source_badge_label || r.source_type || "") +
            "</span></td>" +
            "<td>" +
            uidCell(r) +
            "</td>" +
            "<td class=\"muted\">" +
            escapeHtml((r.last_updated || r.freshness || "").replace("T", " ").slice(0, 19)) +
            "</td>" +
            "</tr>"
        );
      });
    });
    tbody.innerHTML = html.join("") || '<tr><td colspan="7" class="muted">No matching indicators.</td></tr>';
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

  function renderValidation() {
    var tbody = $("hcsc-validation-tbody");
    if (!tbody) return;
    if (!state.results.length) {
      tbody.innerHTML = '<tr><td colspan="3" class="muted">No validation yet.</td></tr>';
      return;
    }
    tbody.innerHTML = state.results
      .map(function (r) {
        return (
          "<tr>" +
          "<td>" +
          escapeHtml(r.display_name) +
          "</td>" +
          "<td><span class=\"hcsc-val\">" +
          escapeHtml(r.validation_status || "Not Yet Validated") +
          "</span></td>" +
          "<td class=\"muted\">" +
          escapeHtml(r.validation_note || "") +
          "</td>" +
          "</tr>"
        );
      })
      .join("");
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
    var env = ($("hcsc-env") && $("hcsc-env").value) || "stage";
    var period = ($("hcsc-period") && $("hcsc-period").value) || "";
    var ou = ($("hcsc-ou") && $("hcsc-ou").value) || "";
    var disagg = ($("hcsc-disagg") && $("hcsc-disagg").value) || "none";
    if (!ou) {
      setStatus("Organisation unit is required.", true);
      return;
    }
    setStatus(force ? "Refreshing…" : "Generating report…");
    var reportUrl = root.getAttribute("data-report-url") || root.getAttribute("data-overview-url");
    var url =
      reportUrl +
      "?environment=" +
      encodeURIComponent(env) +
      "&period=" +
      encodeURIComponent(period) +
      "&orgUnit=" +
      encodeURIComponent(ou) +
      "&disaggregation=" +
      encodeURIComponent(disagg) +
      (force ? "&fresh=1" : "");
    fetch(url, { credentials: "same-origin" })
      .then(function (r) {
        return r.json().then(function (body) {
          body._status = r.status;
          return body;
        });
      })
      .then(function (data) {
        if (!data.ok) {
          setStatus(data.error || "Report failed", true);
          return;
        }
        state.results = data.results || [];
        state.sections = data.sections || [];
        state.retrieval = data.retrieval || data.query || null;
        state.lastPayload = data;
        var ouLabel = ($("hcsc-ou-label") && $("hcsc-ou-label").textContent) || ou;
        var sub = $("hcsc-subtitle");
        if (sub) {
          sub.textContent =
            "Report (" +
            ouLabel +
            ") · " +
            period +
            " · " +
            env +
            " — read-only registry + batched adapters (Phase " +
            ((boot && boot.phase) || "0-2") +
            ").";
        }
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
        var t = data.timings || {};
        var cache = data.cache || {};
        setStatus(
          "Loaded " +
            state.results.length +
            " indicators · " +
            (t.total_ms != null ? t.total_ms + " ms" : "") +
            (cache.hit ? " (cache)" : "") +
            " · HTTP: " +
            (t.http_requests != null ? t.http_requests : "?") +
            (data.adapters_used && data.adapters_used.length
              ? " · adapters: " + data.adapters_used.join(", ")
              : "")
        );
        var fresh = $("hcsc-freshness");
        if (fresh) fresh.textContent = "Last updated: " + (data.freshness || "");
      })
      .catch(function () {
        setStatus("Report request failed.", true);
      });
  }

  // Backward-compatible alias used by older hooks/tests.
  function loadOverview(force) {
    loadReport(force);
  }

  function wireOuSearch() {
    var input = $("hcsc-ou-search");
    var list = $("hcsc-ou-results");
    var hidden = $("hcsc-ou");
    var label = $("hcsc-ou-label");
    if (!input || !list || !hidden) return;
    var timer = null;
    input.addEventListener("input", function () {
      clearTimeout(timer);
      timer = setTimeout(function () {
        var q = input.value.trim();
        var env = ($("hcsc-env") && $("hcsc-env").value) || "stage";
        if (q.length < 2) {
          list.hidden = true;
          list.innerHTML = "";
          return;
        }
        var url =
          root.getAttribute("data-org-units-url") +
          "?environment=" +
          encodeURIComponent(env) +
          "&q=" +
          encodeURIComponent(q) +
          "&limit=20";
        fetch(url, { credentials: "same-origin" })
          .then(function (r) {
            return r.json();
          })
          .then(function (data) {
            var rows = (data && data.org_units) || [];
            if (!rows.length) {
              list.innerHTML = '<li class="muted">No matches</li>';
              list.hidden = false;
              return;
            }
            list.innerHTML = rows
              .map(function (ou) {
                var id = ou.id || "";
                var name = ou.name || id;
                return (
                  '<li><button type="button" data-id="' +
                  escapeHtml(id) +
                  '" data-name="' +
                  escapeHtml(name) +
                  '">' +
                  escapeHtml(name) +
                  " <code>" +
                  escapeHtml(id) +
                  "</code></button></li>"
                );
              })
              .join("");
            list.hidden = false;
          })
          .catch(function () {
            list.hidden = true;
          });
      }, 250);
    });
    list.addEventListener("click", function (ev) {
      var btn = ev.target.closest("button[data-id]");
      if (!btn) return;
      hidden.value = btn.getAttribute("data-id") || "";
      if (label) label.textContent = btn.getAttribute("data-name") || "";
      input.value = btn.getAttribute("data-name") || "";
      list.hidden = true;
    });
  }

  function wire() {
    fillPeriods();
    wireOuSearch();
    var form = $("hcsc-controls");
    if (form) {
      form.addEventListener("submit", function (ev) {
        ev.preventDefault();
        loadOverview(false);
      });
    }
    var refresh = $("hcsc-refresh");
    if (refresh) refresh.addEventListener("click", function () { loadOverview(true); });
    var filter = $("hcsc-filter");
    if (filter) filter.addEventListener("input", renderTable);
    var pctOnly = $("hcsc-pct-only");
    if (pctOnly) pctOnly.addEventListener("change", renderTable);

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
  }

  wire();
})();
