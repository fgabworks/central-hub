(function () {
  "use strict";

  function escapeHtml(value) {
    return String(value == null ? "" : value)
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;");
  }

  function shortCommit(value) {
    var text = String(value || "").trim();
    return text ? text.slice(0, 12) : "—";
  }

  function formatUpdated(value) {
    var text = String(value || "").trim();
    return text ? text.replace("T", " ").slice(0, 19) : "—";
  }

  function rootFor(el) {
    return el.closest("[data-ri-repo]");
  }

  function setBusy(button, busy, label) {
    if (!button) return;
    button.disabled = !!busy;
    if (label) button.textContent = label;
  }

  function renderScanTelemetry(root, telemetry) {
    var target = root.querySelector("[data-ri-scan-telemetry]");
    if (!target || !telemetry) return;
    target.innerHTML =
      "<h3>Latest scan telemetry</h3>" +
      '<dl class="ri-detail-grid ri-scan-metrics">' +
      "<div><dt>Execution</dt><dd>" + escapeHtml(telemetry.execution_type || "Deterministic") + "</dd></div>" +
      "<div><dt>LLM Invoked</dt><dd>" + escapeHtml(telemetry.llm_invoked ? "Yes" : "No") + "</dd></div>" +
      "<div><dt>Provider / Model</dt><dd>" + escapeHtml(telemetry.provider || "None") + " / " + escapeHtml(telemetry.model || "None") + "</dd></div>" +
      "<div><dt>AI tokens in/out/cached/total</dt><dd>" +
        [telemetry.input_tokens || 0, telemetry.output_tokens || 0, telemetry.cached_tokens || 0, telemetry.total_ai_tokens || 0].join("/") +
      "</dd></div>" +
      "<div><dt>Files scanned/indexed/changed</dt><dd>" +
        [telemetry.files_scanned || 0, telemetry.files_indexed || 0, telemetry.files_changed || 0].join("/") +
      "</dd></div>" +
      "<div><dt>Runtime</dt><dd>" + escapeHtml(String(telemetry.runtime_ms || 0)) + " ms</dd></div>" +
      '<div><dt>Indexed commit</dt><dd class="mono">' + escapeHtml(shortCommit(telemetry.indexed_commit)) + "</dd></div>" +
      "<div><dt>Analysis</dt><dd>Standard deterministic</dd></div>" +
      "</dl>";
  }

  function renderStatus(root, status) {
    var badges = root.querySelectorAll(".ri-status, [data-ri-status-badge]");
    badges.forEach(function (badge) {
      badge.className = "badge ri-status ri-status-" + (status.status || "failed");
      badge.textContent = status.status_label || status.status || "Failed";
    });
    var scan = root.querySelector("[data-ri-last-scan]");
    var commit = root.querySelector("[data-ri-commit]");
    var changed = root.querySelector("[data-ri-changed]");
    var updated = root.querySelector("[data-ri-updated]");
    var files = root.querySelector("[data-ri-files]");
    var categories = root.querySelector("[data-ri-categories]");
    var error = root.querySelector("[data-ri-error]");
    var activity = root.querySelector("[data-ri-activity]");
    if (scan) scan.textContent = status.last_scan || "Never";
    if (commit) commit.textContent = shortCommit(status.indexed_commit);
    if (changed) changed.textContent = String((status.changed_files || []).length);
    if (updated) updated.textContent = formatUpdated(status.updated_at || status.last_scan);
    if (files) {
      var count = ((status.profile || {}).file_count);
      if (count != null) files.textContent = String(count);
    }
    if (categories) {
      var cats = status.categories || [];
      categories.innerHTML = cats.length
        ? cats.map(function (value) {
            return '<span class="badge">' + escapeHtml(String(value).replace(/_/g, " ")) + "</span>";
          }).join("")
        : '<span class="muted">None yet</span>';
    }
    if (error) {
      error.hidden = !status.last_error;
      error.textContent = status.last_error || "";
      error.className = status.last_error ? "banner banner-error ri-error" : "ri-error";
    }
    if (activity) {
      var lines = [];
      if (status.last_scan) lines.push("Last scan completed · " + escapeHtml(status.last_scan));
      if ((status.changed_files || []).length) {
        lines.push(
          "Pending changes · " +
            escapeHtml((status.changed_files || []).slice(0, 12).join(", ")) +
            ((status.changed_files || []).length > 12 ? "…" : "")
        );
      }
      if (status.last_error) {
        lines.push('<span class="ri-activity-error">Last error · ' + escapeHtml(status.last_error) + "</span>");
      }
      if (!lines.length) {
        lines.push('<span class="muted">No intelligence activity yet. Run Scan &amp; Learn to build a profile.</span>');
      }
      activity.innerHTML = lines.map(function (line) { return "<li>" + line + "</li>"; }).join("");
    }
    renderScanTelemetry(root, status.last_scan_telemetry || null);

    var actions = root.querySelector(".ri-actions, .ri-detail-actions");
    if (actions && actions.classList.contains("ri-actions")) {
      var repo = root.getAttribute("data-ri-repo");
      var more = actions.querySelector(".ri-more");
      var moreHtml = more ? more.outerHTML : "";
      if (status.status === "not_learned") {
        actions.innerHTML =
          '<button type="button" class="btn btn-sm" data-ri-action="scan">Scan &amp; Learn</button>' +
          moreHtml;
      } else {
        actions.innerHTML =
          '<a class="btn btn-sm" href="/repositories/' + encodeURIComponent(repo) + '/intelligence">View Knowledge</a> ' +
          '<button type="button" class="btn btn-sm" data-ri-action="refresh">Refresh</button>' +
          moreHtml;
      }
    } else if (actions && actions.classList.contains("ri-detail-actions")) {
      var leading =
        status.status === "not_learned" || !status.status
          ? '<button type="button" class="btn btn-sm" data-ri-action="scan">Scan &amp; Learn</button>'
          : '<button type="button" class="btn btn-sm" data-ri-action="view">View Knowledge</button> ' +
            '<button type="button" class="btn btn-sm" data-ri-action="refresh">Refresh Intelligence</button>';
      var trailing = actions.querySelector('a[href*="intelligence"]');
      actions.innerHTML =
        leading +
        ' <button type="button" class="btn btn-sm" disabled title="Future capability; not implemented">Deep AI Analysis</button>' +
        (trailing ? " " + trailing.outerHTML : "");
    }
  }

  function renderKnowledge(root, payload) {
    var target = root.querySelector("[data-ri-knowledge]");
    if (!target) {
      // Table row: open detail page.
      var repo = root.getAttribute("data-ri-repo");
      if (repo) window.location.href = "/repositories/" + encodeURIComponent(repo) + "/intelligence";
      return;
    }
    var entries = payload.entries || [];
    var changed = ((payload.status || {}).changed_files || []);
    target.innerHTML =
      "<h3>Knowledge entries</h3>" +
      (changed.length
        ? "<p><strong>Changed files:</strong> " + escapeHtml(changed.slice(0, 20).join(", ")) + "</p>"
        : "") +
      '<div class="ri-entry-list">' +
      entries.slice(0, 80).map(function (entry) {
        return (
          '<div class="ri-entry"><span class="badge">' +
          escapeHtml(entry.category) +
          '</span><strong class="mono">' +
          escapeHtml(entry.path) +
          "</strong><p>" +
          escapeHtml(entry.summary) +
          "</p></div>"
        );
      }).join("") +
      "</div>";
    target.hidden = false;
  }

  document.addEventListener("click", function (event) {
    var button = event.target.closest("[data-ri-action]");
    if (!button) return;
    var root = rootFor(button);
    if (!root) return;
    var repo = root.getAttribute("data-ri-repo");
    var action = button.getAttribute("data-ri-action");
    if (!repo || !action) return;

    // Close more menu after choosing an action.
    var details = button.closest("details.ri-more");
    if (details) details.open = false;

    if (action === "view") {
      fetch("/api/repositories/" + encodeURIComponent(repo) + "/intelligence", {
        credentials: "same-origin",
        headers: { Accept: "application/json" },
      })
        .then(function (response) { return response.json(); })
        .then(function (payload) {
          if (payload && payload.ok === false) throw new Error(payload.error || "Failed to load knowledge");
          renderKnowledge(root, payload);
          if (payload.status) renderStatus(root, payload.status);
        })
        .catch(function (error) {
          renderStatus(root, {
            status: "failed",
            status_label: "Failed",
            last_error: String(error && error.message ? error.message : error),
          });
        });
      return;
    }

    var idleLabel = action === "scan" ? "Scan & Learn" : "Refresh";
    setBusy(button, true, action === "scan" ? "Learning…" : "Refreshing…");
    fetch("/api/repositories/" + encodeURIComponent(repo) + "/intelligence/" + action, {
      method: "POST",
      credentials: "same-origin",
      headers: { Accept: "application/json" },
    })
      .then(function (response) { return response.json().then(function (data) { return { ok: response.ok, data: data }; }); })
      .then(function (res) {
        var status = (res.data && res.data.status) || {
          status: "failed",
          status_label: "Failed",
          last_error: (res.data && res.data.error) || "Scan failed",
        };
        renderStatus(root, status);
        if (root.id === "ri-detail" && status.status && status.status !== "not_learned" && status.status !== "failed") {
          // Refresh knowledge list after a successful scan/refresh on detail page.
          fetch("/api/repositories/" + encodeURIComponent(repo) + "/intelligence", {
            credentials: "same-origin",
            headers: { Accept: "application/json" },
          })
            .then(function (response) { return response.json(); })
            .then(function (payload) { renderKnowledge(root, payload); });
        }
      })
      .catch(function (error) {
        renderStatus(root, {
          status: "failed",
          status_label: "Failed",
          last_error: String(error && error.message ? error.message : error),
        });
      })
      .finally(function () {
        setBusy(button, false, idleLabel);
      });
  });
})();
