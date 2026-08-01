/**
 * HCSC Indicator Summary & Data Lineage — NPMO (Phase 0–1 UI).
 * Read-only: displays registry + batched analytics results. No formula engine.
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
    query: null,
    lastPayload: null,
    selectedOu: null,
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

  function fmtNum(v) {
    if (v == null || v === "") return "—";
    var n = Number(v);
    if (!isFinite(n)) return escapeHtml(String(v));
    if (Math.abs(n - Math.round(n)) < 1e-9) return String(Math.round(n));
    return n.toFixed(2);
  }

  function fmtPct(v) {
    if (v == null || v === "") return "—";
    var n = Number(v);
    if (!isFinite(n)) return "—";
    return n.toFixed(2) + "%";
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

  function valueCell(row) {
    if (row.unresolved) return '<span class="muted">Unresolved</span>';
    if (row.result_type === "count") {
      return '<span title="Count">' + fmtNum(row.count) + "</span>";
    }
    if (row.result_type === "numerator_denominator_percentage" || row.result_type === "percentage") {
      return '<span title="Percentage">' + fmtPct(row.percentage) + "</span>";
    }
    return "—";
  }

  function uidButton(row) {
    var uid = row.source_uid || (row.dhis2_uids && row.dhis2_uids.value) || "";
    var tip =
      "UID: " +
      (uid || "unresolved") +
      " · Type: " +
      (row.source_type || row.result_type || "") +
      " · Owner: " +
      (row.source_owner || "") +
      " · Object: " +
      (row.source_table_view_reference || "");
    return (
      '<button type="button" class="hcsc-uid-btn" data-key="' +
      escapeHtml(row.indicator_key) +
      '" title="' +
      escapeHtml(tip) +
      '" aria-label="Indicator details">ℹ</button>'
    );
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
    var byKey = {};
    rows.forEach(function (r) {
      byKey[r.indicator_key] = r;
    });
    host.innerHTML = keys
      .map(function (k) {
        var r = byKey[k];
        if (!r) return "";
        var main =
          r.result_type === "count" ? fmtNum(r.count) : fmtPct(r.percentage);
        return (
          '<article class="hcsc-card">' +
          "<h3>" +
          escapeHtml(r.display_name) +
          " " +
          uidButton(r) +
          "</h3>" +
          '<p class="hcsc-card-value">' +
          main +
          "</p>" +
          '<p class="muted">' +
          escapeHtml(r.validation_status || "") +
          "</p>" +
          "</article>"
        );
      })
      .join("");
  }

  function renderTable(rows) {
    var tbody = $("hcsc-tbody");
    if (!tbody) return;
    var q = (($("hcsc-filter") && $("hcsc-filter").value) || "").trim().toLowerCase();
    var filtered = rows.filter(function (r) {
      if (!q) return true;
      return (
        (r.display_name || "").toLowerCase().indexOf(q) >= 0 ||
        (r.indicator_key || "").toLowerCase().indexOf(q) >= 0 ||
        (r.category || "").toLowerCase().indexOf(q) >= 0
      );
    });
    if (!filtered.length) {
      tbody.innerHTML = '<tr><td colspan="8" class="muted">No matching indicators.</td></tr>';
      return;
    }
    tbody.innerHTML = filtered
      .map(function (r) {
        var numLabel = r.numerator_label ? '<div class="muted">' + escapeHtml(r.numerator_label) + "</div>" : "";
        var denLabel = r.denominator_label ? '<div class="muted">' + escapeHtml(r.denominator_label) + "</div>" : "";
        var countLabel = r.result_type === "count" ? '<div class="muted">Count</div>' : "";
        var formula = r.percentage_formula_reference
          ? '<div class="muted hcsc-formula">' + escapeHtml(r.percentage_formula_reference) + "</div>"
          : "";
        return (
          "<tr data-key=\"" +
          escapeHtml(r.indicator_key) +
          '">' +
          "<td><strong>" +
          escapeHtml(r.display_name) +
          "</strong> " +
          uidButton(r) +
          '<div class="muted">' +
          escapeHtml(r.category || "") +
          "</div></td>" +
          "<td>" +
          escapeHtml(r.result_type || "") +
          "</td>" +
          "<td>" +
          countLabel +
          valueCell(r) +
          formula +
          "</td>" +
          "<td>" +
          numLabel +
          (r.result_type === "numerator_denominator_percentage" ? fmtNum(r.numerator) : "—") +
          "</td>" +
          "<td>" +
          denLabel +
          (r.result_type === "numerator_denominator_percentage" ? fmtNum(r.denominator) : "—") +
          "</td>" +
          "<td>" +
          (r.result_type === "numerator_denominator_percentage" || r.result_type === "percentage"
            ? fmtPct(r.percentage)
            : "—") +
          "</td>" +
          "<td><div>" +
          escapeHtml(r.source_owner || "") +
          '</div><div class="muted">' +
          escapeHtml(r.source_table_view_reference || "") +
          "</div></td>" +
          "<td><span class=\"hcsc-val hcsc-val-" +
          escapeHtml(String(r.validation_status || "").replace(/\s+/g, "-").toLowerCase()) +
          '">' +
          escapeHtml(r.validation_status || "Not Yet Validated") +
          "</span></td>" +
          "</tr>"
        );
      })
      .join("");
  }

  function renderQuery(query) {
    var body = $("hcsc-query-body");
    if (!body) return;
    if (!query) {
      body.innerHTML = '<p class="muted">No query yet.</p>';
      return;
    }
    body.innerHTML =
      "<p><strong>Retrieval method:</strong> " +
      escapeHtml(query.retrieval_method || "") +
      "</p>" +
      "<p><strong>Endpoint:</strong> <code>" +
      escapeHtml(query.endpoint || "") +
      "</code></p>" +
      "<p><strong>Period:</strong> " +
      escapeHtml(query.period || "") +
      " · <strong>OU:</strong> " +
      escapeHtml(query.organisation_unit || "") +
      "</p>" +
      "<p><strong>Indicator UIDs:</strong> <code>" +
      escapeHtml((query.indicator_uids || []).join("; ")) +
      "</code></p>" +
      "<p><strong>Aggregation:</strong> " +
      escapeHtml(query.aggregation_request || "") +
      "</p>" +
      "<p>" +
      escapeHtml(query.readable || "") +
      "</p>" +
      "<pre class=\"hcsc-query-pre\">" +
      escapeHtml(query.query_string || "") +
      "</pre>" +
      '<p class="muted">' +
      escapeHtml(query.note || "") +
      "</p>";
  }

  function openDrawer(key) {
    var url = (root.getAttribute("data-detail-url") || "").replace("__KEY__", encodeURIComponent(key));
    var drawer = $("hcsc-drawer");
    var body = $("hcsc-drawer-body");
    var title = $("hcsc-drawer-title");
    if (!drawer || !body) return;
    drawer.hidden = false;
    body.innerHTML = '<p class="muted">Loading…</p>';
    fetch(url, { credentials: "same-origin" })
      .then(function (r) {
        return r.json();
      })
      .then(function (data) {
        var ind = (data && data.indicator) || {};
        var uids = ind.dhis2_uids || {};
        if (title) title.textContent = ind.display_name || "Data Mapping & Details";
        var uidList = Object.keys(uids)
          .map(function (k) {
            return k + ": " + uids[k];
          })
          .join("\n");
        body.innerHTML =
          "<dl class=\"hcsc-dl\">" +
          "<dt>Result type</dt><dd>" +
          escapeHtml(ind.result_type || "") +
          "</dd>" +
          "<dt>UID(s)</dt><dd><pre>" +
          escapeHtml(uidList || "(none — unresolved)") +
          '</pre><button type="button" class="btn btn-sm" id="hcsc-copy-uid" data-uid="' +
          escapeHtml(uids.value || "") +
          '">Copy UID</button></dd>' +
          "<dt>Source owner</dt><dd>" +
          escapeHtml(ind.source_owner || "") +
          "</dd>" +
          "<dt>Source table/view</dt><dd>" +
          escapeHtml(ind.source_table_view_reference || "") +
          "</dd>" +
          "<dt>Numerator</dt><dd>" +
          escapeHtml(ind.numerator_label || "—") +
          "</dd>" +
          "<dt>Denominator</dt><dd>" +
          escapeHtml(ind.denominator_label || "—") +
          "</dd>" +
          "<dt>Percentage formula</dt><dd>" +
          escapeHtml(ind.percentage_formula_reference || "—") +
          "</dd>" +
          "<dt>Population</dt><dd>" +
          escapeHtml(ind.population_definition_reference || "—") +
          "</dd>" +
          "<dt>Age range</dt><dd>" +
          escapeHtml(ind.age_range || "—") +
          "</dd>" +
          "<dt>Quarter rule</dt><dd>" +
          escapeHtml(ind.quarter_rule_reference || "—") +
          "</dd>" +
          "<dt>Organisation-unit rule</dt><dd>" +
          escapeHtml(ind.organisation_unit_rule || "—") +
          "</dd>" +
          "<dt>IP / non-IP rule</dt><dd>" +
          escapeHtml(ind.ip_non_ip_rule || "—") +
          "</dd>" +
          "<dt>Repository reference</dt><dd>" +
          escapeHtml(ind.repository_file_reference || "—") +
          "</dd>" +
          "<dt>Confidence</dt><dd>" +
          escapeHtml(ind.confidence || "") +
          "</dd>" +
          "<dt>Unresolved notes</dt><dd>" +
          escapeHtml(ind.notes || "—") +
          "</dd>" +
          "<dt>Design element</dt><dd>" +
          escapeHtml(data.design_element_id || "—") +
          "</dd>" +
          "</dl>";
        var copyBtn = $("hcsc-copy-uid");
        if (copyBtn) {
          copyBtn.onclick = function () {
            var uid = copyBtn.getAttribute("data-uid") || "";
            if (!uid) return;
            if (navigator.clipboard && navigator.clipboard.writeText) {
              navigator.clipboard.writeText(uid);
            }
          };
        }
      })
      .catch(function () {
        body.innerHTML = '<p class="muted">Failed to load details.</p>';
      });
  }

  function loadOverview(force) {
    var env = ($("hcsc-env") && $("hcsc-env").value) || "stage";
    var period = ($("hcsc-period") && $("hcsc-period").value) || "";
    var ou = ($("hcsc-ou") && $("hcsc-ou").value) || "";
    var disagg = ($("hcsc-disagg") && $("hcsc-disagg").value) || "none";
    if (!ou) {
      setStatus("Organisation unit is required.", true);
      return;
    }
    setStatus(force ? "Refreshing…" : "Loading Overview…");
    var url =
      root.getAttribute("data-overview-url") +
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
          setStatus(data.error || "Overview failed", true);
          return;
        }
        state.results = data.results || [];
        state.query = data.query || null;
        state.lastPayload = data;
        renderCards(state.results);
        renderTable(state.results);
        renderQuery(state.query);
        var t = data.timings || {};
        var cache = data.cache || {};
        setStatus(
          "Loaded " +
            state.results.length +
            " indicators · " +
            (t.total_ms != null ? t.total_ms + " ms" : "") +
            (cache.hit ? " (cache)" : "") +
            " · HTTP requests: " +
            (t.http_requests != null ? t.http_requests : "?")
        );
        var fresh = $("hcsc-freshness");
        if (fresh) fresh.textContent = "Freshness: " + (data.freshness || "");
        // Background refresh after cache hit
        if (cache.hit && !force) {
          setTimeout(function () {
            loadOverview(true);
          }, 50);
        }
      })
      .catch(function () {
        setStatus("Overview request failed.", true);
      });
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
            var rows = (data && (data.org_units || data.results || data.items)) || [];
            if (!rows.length) {
              list.innerHTML = '<li class="muted">No matches</li>';
              list.hidden = false;
              return;
            }
            list.innerHTML = rows
              .map(function (ou) {
                var id = ou.id || ou.uid || "";
                var name = ou.displayName || ou.name || id;
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
    if (refresh) {
      refresh.addEventListener("click", function () {
        loadOverview(true);
      });
    }
    var filter = $("hcsc-filter");
    if (filter) {
      filter.addEventListener("input", function () {
        renderTable(state.results);
      });
    }
    root.addEventListener("click", function (ev) {
      var btn = ev.target.closest(".hcsc-uid-btn");
      if (btn) {
        openDrawer(btn.getAttribute("data-key"));
      }
    });
    var close = $("hcsc-drawer-close");
    if (close) {
      close.addEventListener("click", function () {
        var d = $("hcsc-drawer");
        if (d) d.hidden = true;
      });
    }
    var copyQ = $("hcsc-copy-query");
    if (copyQ) {
      copyQ.addEventListener("click", function () {
        var text = (state.query && (state.query.copy_text || state.query.query_string)) || "";
        if (text && navigator.clipboard && navigator.clipboard.writeText) {
          navigator.clipboard.writeText(text);
        }
      });
    }
  }

  wire();
})();
