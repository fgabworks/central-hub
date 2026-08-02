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

  function refreshCentralHubProcesses() {
    var tbody = qs("#hub-processes-body");
    if (!tbody) return Promise.resolve(null);
    return fetch("/api/health/central-hub-processes", { credentials: "same-origin" })
      .then(function (response) { return response.json(); })
      .then(function (data) {
        var rows = (data && data.instances) || [];
        if (!rows.length) {
          tbody.innerHTML = '<tr class="empty-row"><td colspan="7">No verified Central Hub instance found.</td></tr>';
          return data;
        }
        tbody.innerHTML = rows.map(function (item) {
          return (
            "<tr><td class=\"mono\">" + escapeHtml(item.pid) +
            "</td><td>" + escapeHtml(item.status) +
            "</td><td class=\"mono\">" + escapeHtml(item.port) +
            "</td><td>" + (item.registered ? "Yes" : "No") +
            "</td><td>" + (item.owns_port ? "Yes" : "No") +
            "</td><td class=\"muted\">" + escapeHtml((item.verification_reasons || []).join(", ")) +
            "</td><td class=\"muted mono\">" + escapeHtml(item.command_redacted) + "</td></tr>"
          );
        }).join("");
        var status = qs("#hub-process-action-status");
        if (status) status.textContent = rows.length + " verified instance(s); current PID " + (data.current_pid || "unavailable") + ".";
        return data;
      })
      .catch(function () {
        tbody.innerHTML = '<tr class="empty-row"><td colspan="7">Central Hub process scan unavailable.</td></tr>';
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
          if (status) status.textContent = "Restart complete. New PID " + data.new_pid + "; health check passed.";
          return refreshCentralHubProcesses();
        }
        if (data.status === "failed") throw new Error(data.error || "Clean restart failed.");
        if (status) status.textContent = "Restart in progress; waiting for one healthy listenerâ€¦";
        return new Promise(function (resolve) {
          window.setTimeout(function () { resolve(pollHubAction(actionId, attempts - 1)); }, 1000);
        });
      })
      .catch(function (error) {
        if (attempts <= 1) {
          if (status) status.textContent = error.message || "Restart status unavailable.";
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
    var status = qs("#hub-process-action-status");
    if (stale) stale.addEventListener("click", function () {
      if (!window.confirm("Stop only verified stale Central Hub instances?")) return;
      postHubProcessAction("/api/health/central-hub-processes/stop-stale", { confirm: true })
        .then(function (data) {
          if (status) status.textContent = "Stopped " + data.count + " stale instance(s).";
          return refreshCentralHubProcesses();
        })
        .catch(function (error) { if (status) status.textContent = error.message; });
    });
    if (restart) restart.addEventListener("click", function () {
      if (!window.confirm("Restart Central Hub cleanly and verify one healthy listener?")) return;
      postHubProcessAction("/api/health/central-hub-processes/restart", { confirm: true })
        .then(function (data) {
          if (status) status.textContent = "Restart queued for verified PID(s): " + data.target_pids.join(", ") + ".";
          return pollHubAction(data.action_id, 45);
        })
        .catch(function (error) { if (status) status.textContent = error.message; });
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
