/**
 * Async secondary panels after dashboard / health / repositories shell render.
 */
(function () {
  "use strict";

  var fetchFn = (window.HubPerf && window.HubPerf.dedupeFetch) || fetch;

  function qs(sel, root) {
    return (root || document).querySelector(sel);
  }

  function refreshHealth(fresh) {
    var url = "/api/health" + (fresh ? "?fresh=1" : "");
    return fetchFn(url, { credentials: "same-origin" })
      .then(function (r) { return r.json(); })
      .then(function (data) {
        if (!data || !data.ok && !data.results) return data;
        var results = data.results || [];
        var healthy = results.filter(function (item) { return item.ok; }).length;
        var enabled = results.filter(function (item) { return item.enabled; }).length;
        var card = document.querySelector('[data-async-id="card-repo-health"] .stat-sub');
        if (card) card.textContent = enabled + " enabled · " + healthy + " healthy";
        var tbody = qs("#dash-repos-body") || qs("#repo-health-body") || qs("#health-results-body");
        if (tbody && results.length) {
          /* Pages that opt in can re-render; otherwise leave SSR cache rows. */
          tbody.setAttribute("data-health-refreshed", "1");
        }
        document.querySelectorAll("[data-health-count]").forEach(function (el) {
          el.textContent = String(results.length);
        });
        return data;
      })
      .catch(function () { return null; });
  }

  function refreshCalendarUpcoming() {
    var panel = qs("#dash-upcoming-list");
    var cardVal = qs('[data-async-id="card-upcoming-count"] .stat-value');
    return fetchFn("/api/calendar/upcoming?workspace=personal&limit=5&refresh=1", {
      credentials: "same-origin",
    })
      .then(function (r) { return r.json(); })
      .then(function (data) {
        var events = (data && data.events) || [];
        if (cardVal) cardVal.textContent = String(events.length);
        if (!panel) return;
        if (!events.length) {
          panel.innerHTML =
            '<div class="panel-empty"><p class="muted">No upcoming events.</p>' +
            '<a class="quiet" href="/system/google-connections">Connect Calendar</a></div>';
          var section = panel.closest(".panel-upcoming");
          if (section) section.classList.add("is-empty");
          return;
        }
        var section = panel.closest(".panel-upcoming");
        if (section) section.classList.remove("is-empty");
        panel.innerHTML =
          "<ul class=\"cal-upcoming-list\">" +
          events
            .map(function (ev) {
              var when = ev.all_day
                ? (ev.start && ev.start.date) + " (all day)"
                : (ev.start && (ev.start.date_time || ev.start.date)) || "";
              var href =
                "/calendar/accounts/" +
                encodeURIComponent(ev.account_id) +
                "/calendars/" +
                encodeURIComponent(ev.calendar_id) +
                "/events/" +
                encodeURIComponent(ev.id);
              return (
                "<li><a href=\"" +
                href +
                "\"><strong>" +
                escapeHtml(ev.summary || "Event") +
                "</strong><span class=\"muted\">" +
                escapeHtml(when) +
                " · " +
                escapeHtml(ev.calendar_summary || ev.account_email || "") +
                "</span></a></li>"
              );
            })
            .join("") +
          "</ul>";
      })
      .catch(function () {
        if (cardVal && cardVal.textContent === "…") cardVal.textContent = "0";
      });
  }

  function escapeHtml(text) {
    return String(text || "")
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;");
  }

  function refreshLocalProcesses() {
    var tbody = qs("#health-processes-body");
    if (!tbody) return Promise.resolve();
    tbody.innerHTML =
      '<tr class="empty-row"><td colspan="8" class="muted">Scanning local processes…</td></tr>';
    return fetchFn("/api/health/local-processes", { credentials: "same-origin" })
      .then(function (r) { return r.json(); })
      .then(function (data) {
        var rows = (data && data.processes) || [];
        if (!rows.length) {
          tbody.innerHTML =
            '<tr class="empty-row"><td colspan="8">No repository-related processes detected.</td></tr>';
          return;
        }
        tbody.innerHTML = rows
          .map(function (p) {
            return (
              "<tr><td><div class=\"cell-title\">" +
              escapeHtml(p.repository_name || p.repo_id) +
              "</div><div class=\"cell-sub\"><code>" +
              escapeHtml(p.repo_id) +
              "</code></div></td><td class=\"mono\">" +
              escapeHtml(p.pid) +
              "</td><td class=\"mono\">" +
              escapeHtml(p.executable) +
              "</td><td class=\"mono\">" +
              escapeHtml(p.port || "—") +
              "</td><td>" +
              (p.managed_by_hub ? "Yes" : "No") +
              "</td><td>" +
              escapeHtml(p.confidence) +
              "</td><td class=\"muted\">" +
              escapeHtml((p.detection_reasons || []).join(", ")) +
              "</td><td class=\"muted mono\">" +
              escapeHtml(p.command_redacted) +
              "</td></tr>"
            );
          })
          .join("");
      })
      .catch(function () {
        tbody.innerHTML =
          '<tr class="empty-row"><td colspan="8" class="muted">Process scan unavailable.</td></tr>';
      });
  }

  function hubProcessRow(item) {
    var actions = "—";
    if (item.hub_owned && item.stoppable) {
      actions =
        '<button type="button" class="btn btn-sm hub-proc-stop" data-pid="' + escapeHtml(item.pid) +
        '" data-identity="' + escapeHtml(item.identity_token || "") +
        '" data-ownership="' + escapeHtml(item.ownership_token || "") +
        '">Stop</button> ';
      if (item.role === "server" || item.current) {
        actions +=
          '<button type="button" class="btn btn-sm hub-proc-restart" data-pid="' + escapeHtml(item.pid) +
          '" data-identity="' + escapeHtml(item.identity_token || "") +
          '" data-ownership="' + escapeHtml(item.ownership_token || "") +
          '">Restart</button>';
      }
    } else {
      actions = '<span class="muted">Not stoppable</span>';
    }
    return (
      "<tr>" +
      "<td><div class=\"cell-title\">" + escapeHtml(item.label || "Python") + "</div>" +
      "<div class=\"cell-sub\">" + escapeHtml(item.role || "") + (item.orphan ? " · orphan" : "") + "</div></td>" +
      "<td>" + escapeHtml(item.status || "—") + "<div class=\"cell-sub\">" + escapeHtml(item.health || "") + "</div></td>" +
      "<td class=\"mono\">" + escapeHtml(item.pid) + "<div class=\"cell-sub\">ppid " + escapeHtml(item.ppid == null ? "—" : item.ppid) + "</div></td>" +
      "<td class=\"mono\">" + escapeHtml(item.script_module || "—") + "</td>" +
      "<td class=\"mono\">" + escapeHtml(item.listening_port == null ? "—" : item.listening_port) + "</td>" +
      "<td class=\"mono muted\" style=\"max-width:12rem;word-break:break-word\">" + escapeHtml(item.cwd || "—") + "</td>" +
      "<td class=\"muted\">" + escapeHtml(item.started_at || "—") + "<div class=\"cell-sub\">" + escapeHtml(item.runtime_label || "—") + "</div></td>" +
      "<td>" + (item.hub_owned ? "Yes" : "No") + "</td>" +
      "<td class=\"muted mono\" style=\"max-width:18rem;word-break:break-word\">" + escapeHtml(item.command_redacted || "") + "</td>" +
      "<td>" + actions + "</td>" +
      "</tr>"
    );
  }

  function refreshCentralHubProcesses() {
    var tbody = qs("#hub-processes-body");
    var otherBody = qs("#hub-other-python-body");
    if (!tbody) return Promise.resolve(null);
    return fetch("/api/health/central-hub-processes", { credentials: "same-origin" })
      .then(function (response) { return response.json(); })
      .then(function (data) {
        var hubRows = (data && data.hub_processes) || (data && data.instances) || [];
        var otherRows = (data && data.other_python) || [];
        tbody.innerHTML = hubRows.length
          ? hubRows.map(hubProcessRow).join("")
          : '<tr class="empty-row"><td colspan="10">No CLIMATE-owned process found.</td></tr>';
        if (otherBody) {
          otherBody.innerHTML = otherRows.length
            ? otherRows.map(hubProcessRow).join("")
            : '<tr class="empty-row"><td colspan="10">No other Python processes detected.</td></tr>';
        }
        var status = qs("#hub-process-action-status");
        if (status) {
          status.textContent =
            hubRows.length + " CLIMATE process(es), " + otherRows.length +
            " other Python; current PID " + (data.current_pid || "unavailable") + ".";
        }
        return data;
      })
      .catch(function () {
        tbody.innerHTML = '<tr class="empty-row"><td colspan="10">CLIMATE process scan unavailable.</td></tr>';
        if (otherBody) {
          otherBody.innerHTML = '<tr class="empty-row"><td colspan="10">Other Python scan unavailable.</td></tr>';
        }
        return null;
      });
  }

  function postHubProcessAction(path, payload) {
    return fetch(path, {
      method: "POST",
      credentials: "same-origin",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload || {}),
    }).then(function (response) {
      return response.json().then(function (data) {
        if (!response.ok) throw new Error((data && data.error) || "Process action failed.");
        return data;
      });
    });
  }

  function pollHubAction(actionId, attempts) {
    var status = qs("#hub-process-action-status");
    if (!attempts) return Promise.resolve(null);
    return fetch("/api/health/central-hub-processes/actions/" + encodeURIComponent(actionId), {
      credentials: "same-origin",
      cache: "no-store",
    })
      .then(function (response) { return response.json(); })
      .then(function (data) {
        if (data.status === "completed") {
          if (status) {
            status.textContent = data.action === "restart"
              ? ("Restart complete. New PID " + data.new_pid + "; health check passed.")
              : ("Action complete for PID(s): " + ((data.target_pids || []).join(", ") || "n/a") + ".");
          }
          return refreshCentralHubProcesses();
        }
        if (data.status === "failed") throw new Error(data.error || "Process action failed.");
        if (status) status.textContent = "Action in progress…";
        return new Promise(function (resolve) {
          window.setTimeout(function () { resolve(pollHubAction(actionId, attempts - 1)); }, 1000);
        });
      })
      .catch(function (error) {
        if (attempts <= 1) {
          if (status) status.textContent = error.message || "Action status unavailable.";
          return null;
        }
        return new Promise(function (resolve) {
          window.setTimeout(function () { resolve(pollHubAction(actionId, attempts - 1)); }, 1000);
        });
      });
  }

  function bindCentralHubProcessActions() {
    var stale = qs("#hub-stop-stale");
    var restart = qs("#hub-restart-clean");
    var stopAll = qs("#hub-stop-all");
    var stopHub = qs("#hub-stop-central");
    var refresh = qs("#hub-refresh-processes");
    var status = qs("#hub-process-action-status");
    var root = qs("[data-central-hub-processes]");
    if (refresh) refresh.addEventListener("click", function () { refreshCentralHubProcesses(); });
    if (stale) stale.addEventListener("click", function () {
      if (!window.confirm("Stop only verified stale CLIMATE instances?")) return;
      postHubProcessAction("/api/health/central-hub-processes/stop-stale", { confirm: true })
        .then(function (data) {
          if (status) status.textContent = "Stopped " + data.count + " stale instance(s).";
          return refreshCentralHubProcesses();
        })
        .catch(function (error) { if (status) status.textContent = error.message; });
    });
    if (restart) restart.addEventListener("click", function () {
      if (!window.confirm("Restart CLIMATE cleanly and verify one healthy listener?")) return;
      postHubProcessAction("/api/health/central-hub-processes/restart", { confirm: true })
        .then(function (data) {
          if (status) status.textContent = "Restart queued for verified PID(s): " + data.target_pids.join(", ") + ".";
          return pollHubAction(data.action_id, 45);
        })
        .catch(function (error) { if (status) status.textContent = error.message; });
    });
    if (stopHub) stopHub.addEventListener("click", function () {
      var phrase = window.prompt('Type "STOP CENTRAL HUB" to terminate the complete owned process tree.');
      if (phrase === null) return;
      postHubProcessAction("/api/health/central-hub-processes/stop-central-hub", {
        typed_confirmation: phrase,
      }).then(function (data) {
        if (status) status.textContent = "Stop CLIMATE queued for PID(s): " + data.target_pids.join(", ") + ".";
        return pollHubAction(data.action_id, 45);
      }).catch(function (error) { if (status) status.textContent = error.message; });
    });
    if (stopAll) stopAll.addEventListener("click", function () {
      var phrase = window.prompt('Type "STOP ALL CENTRAL HUB INSTANCES" to continue.');
      if (phrase === null) return;
      postHubProcessAction("/api/health/central-hub-processes/stop-all", {
        typed_confirmation: phrase,
      }).then(function (data) {
        if (status) status.textContent = "Stop-all queued for verified PID(s): " + data.target_pids.join(", ") + ".";
      }).catch(function (error) { if (status) status.textContent = error.message; });
    });
    if (root) root.addEventListener("click", function (event) {
      var stopBtn = event.target.closest(".hub-proc-stop");
      var restartBtn = event.target.closest(".hub-proc-restart");
      if (stopBtn) {
        if (!window.confirm("Stop CLIMATE-owned PID " + stopBtn.getAttribute("data-pid") + "?")) return;
        postHubProcessAction("/api/health/central-hub-processes/stop", {
          confirm: true,
          pid: Number(stopBtn.getAttribute("data-pid")),
          identity_token: stopBtn.getAttribute("data-identity"),
          ownership_token: stopBtn.getAttribute("data-ownership"),
        }).then(function (data) {
          if (data.queued) {
            if (status) status.textContent = "Stop queued for PID " + stopBtn.getAttribute("data-pid") + ".";
            return pollHubAction(data.action_id, 45);
          }
          if (status) status.textContent = data.ok ? ("Stopped PID " + stopBtn.getAttribute("data-pid") + ".") : "Stop failed.";
          return refreshCentralHubProcesses();
        }).catch(function (error) { if (status) status.textContent = error.message; });
      }
      if (restartBtn) {
        if (!window.confirm("Restart CLIMATE Server PID " + restartBtn.getAttribute("data-pid") + "?")) return;
        postHubProcessAction("/api/health/central-hub-processes/restart-one", {
          confirm: true,
          pid: Number(restartBtn.getAttribute("data-pid")),
          identity_token: restartBtn.getAttribute("data-identity"),
          ownership_token: restartBtn.getAttribute("data-ownership"),
        }).then(function (data) {
          if (status) status.textContent = "Restart queued.";
          return pollHubAction(data.action_id, 45);
        }).catch(function (error) { if (status) status.textContent = error.message; });
      }
    });
  }

  document.addEventListener("DOMContentLoaded", function () {
    var root = document.body;
    if (!root) return;
    var run = function () {
      if (root.getAttribute("data-health-async") === "1" || qs("[data-health-async]")) {
        refreshHealth(true);
      }
      if (root.getAttribute("data-calendar-async") === "1" || qs("[data-calendar-async]")) {
        refreshCalendarUpcoming();
      }
      if (root.getAttribute("data-processes-async") === "1" || qs("[data-processes-async]")) {
        refreshLocalProcesses();
      }
      if (qs("[data-central-hub-processes]")) {
        refreshCentralHubProcesses();
        bindCentralHubProcessActions();
      }
    };
    if (window.HubPerf && window.HubPerf.whenVisible) window.HubPerf.whenVisible(run);
    else run();
  });
})();
