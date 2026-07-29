(function () {
  var root = document.getElementById("rw-run");
  if (!root) return;

  var profileEl = document.getElementById("rw-profile");
  var envEl = document.getElementById("rw-env");
  var portEl = document.getElementById("rw-port");
  var portWrap = document.getElementById("rw-port-wrap");
  var findPortBtn = document.getElementById("rw-find-port");
  var portNote = document.getElementById("rw-port-note");
  var metaEl = document.getElementById("rw-meta");
  var previewEl = document.getElementById("rw-cmd-preview");
  var statusEl = document.getElementById("rw-run-status");
  var envNamesEl = document.getElementById("rw-env-names");
  var liveBox = document.getElementById("rw-live-confirm");
  var liveGate = document.getElementById("rw-live-gate");
  var liveChk = document.getElementById("rw-confirm-live");
  var startBtn = document.getElementById("rw-start");
  var stopBtn = document.getElementById("rw-stop");
  var restartBtn = document.getElementById("rw-restart");
  var retryBtn = document.getElementById("rw-retry");
  var openApp = document.getElementById("rw-open-app");
  var viewLogs = document.getElementById("rw-view-logs");
  var activeRun = null;
  var statusTimer = null;
  var scanTimer = null;
  var statusIntervalMs = parseInt(root.getAttribute("data-status-interval-ms") || "4000", 10) || 4000;
  var scanIntervalMs = parseInt(root.getAttribute("data-scan-interval-ms") || "15000", 10) || 15000;
  var scanTimeoutMs = parseInt(root.getAttribute("data-scan-timeout-ms") || "10000", 10) || 10000;
  var scanInFlight = false;
  var scanAbort = null;
  var lastPollKey = "";
  var scanBtn = document.getElementById("rw-proc-scan");
  var scanSpinner = document.getElementById("rw-proc-spinner");
  var lastScannedEl = document.getElementById("rw-proc-last-scanned");
  var procPanel = document.getElementById("rw-proc-panel") || document.getElementById("repository-processes");

  function selectedOption() {
    return profileEl.options[profileEl.selectedIndex];
  }

  function isLiveSelection() {
    var opt = selectedOption();
    return (
      (opt && opt.getAttribute("data-live") === "1") ||
      (opt && opt.getAttribute("data-write") === "1" && envEl.value === "live") ||
      envEl.value === "live"
    );
  }

  function portMode() {
    var opt = selectedOption();
    return (opt && opt.getAttribute("data-port-mode")) || "argument";
  }

  function setStatus(msg, isError) {
    statusEl.textContent = msg || "";
    statusEl.className = isError ? "rw-run-status banner banner-error" : "rw-run-status muted";
  }

  function syncPortControls() {
    var mode = portMode();
    var opt = selectedOption();
    if (mode === "none") {
      portWrap.hidden = true;
      findPortBtn.hidden = true;
      portNote.hidden = false;
      portNote.textContent = "This profile does not expose a configurable port.";
    } else if (mode === "fixed") {
      portWrap.hidden = false;
      portEl.readOnly = true;
      portEl.value = (opt && (opt.getAttribute("data-port") || "")) || portEl.value;
      findPortBtn.hidden = true;
      portNote.hidden = false;
      portNote.textContent =
        "Fixed port " + portEl.value + " — startup blocks if occupied; Find available port is disabled.";
    } else {
      portWrap.hidden = false;
      portEl.readOnly = false;
      findPortBtn.hidden = false;
      portNote.hidden = true;
      if (opt && opt.getAttribute("data-port")) portEl.value = opt.getAttribute("data-port");
    }
  }

  function syncProfile() {
    var opt = selectedOption();
    if (!opt || !opt.value) return;
    envNamesEl.textContent =
      "Env vars (names only): " + (opt.getAttribute("data-env-names") || "—");
    metaEl.textContent =
      "cwd: " +
      (opt.getAttribute("data-cwd") || "—") +
      " · local: " +
      (opt.getAttribute("data-local") || "—") +
      " · health: " +
      (opt.getAttribute("data-health") || "—") +
      " · port mode: " +
      (opt.getAttribute("data-port-mode") || "—");
    var envs = (opt.getAttribute("data-envs") || "development").split(",");
    Array.prototype.forEach.call(envEl.options, function (o) {
      o.disabled = envs.indexOf(o.value) < 0;
    });
    if (envEl.selectedOptions[0] && envEl.selectedOptions[0].disabled) {
      envEl.value = envs[0] || "development";
    }
    var liveHint = document.getElementById("rw-live-env-hint");
    if (liveHint) {
      if (envs.indexOf("live") < 0) {
        liveHint.hidden = false;
        liveHint.textContent =
          "This profile has no Live environment. Choose a Live / API-providing profile to run Live.";
      } else if (root.getAttribute("data-live-allowed") !== "1") {
        liveHint.hidden = false;
        liveHint.textContent =
          "Live is available on this profile after REPO_WS_ALLOW_LIVE_RUNS=true in .env (restart the hub).";
      } else {
        liveHint.hidden = true;
      }
    }
    syncPortControls();
    updateLiveGate();
    applyActiveActions();
  }

  function updateLiveGate() {
    var live = isLiveSelection();
    if (liveGate) liveGate.hidden = !live;
    if (liveBox) liveBox.hidden = !live;
    if (!live && liveChk) liveChk.checked = false;
  }

  function payload() {
    var mode = portMode();
    var body = {
      profile_id: profileEl.value,
      environment: envEl.value,
      confirm_live: !!(liveChk && liveChk.checked),
    };
    if (mode !== "none") {
      body.port = parseInt(portEl.value, 10);
    }
    return body;
  }

  function setText(id, value) {
    var el = document.getElementById(id);
    if (el) el.textContent = value == null || value === "" ? "—" : String(value);
  }

  function renderActive(run) {
    activeRun = run || null;
    var a = run || {
      display_status: "Stopped",
      display_tone: "gray",
      process_state: "stopped",
      health_state: "none",
      health_detail: "—",
      actions: ["start"],
      auto_refresh: false,
    };
    var badge = document.getElementById("rw-active-badge");
    if (badge) {
      badge.textContent = a.display_status || "Stopped";
      badge.className = "rw-status-badge tone-" + (a.display_tone || "gray");
    }
    setText("rw-facet-process", a.process_state || "stopped");
    var healthLabel = "—";
    if (a.health_state === "stale" || a.health_state === "none") {
      healthLabel = "—";
    } else if (a.health_detail) {
      healthLabel = a.health_detail;
    }
    setText("rw-facet-health", healthLabel);
    setText("rw-meta-profile", a.profile_id || "—");
    setText("rw-meta-env", a.environment || "—");
    setText("rw-meta-pid", a.pid || "—");
    setText("rw-meta-port", a.port || "—");
    setText("rw-meta-uptime", a.uptime || "—");
    var urlEl = document.getElementById("rw-meta-url");
    if (urlEl) {
      if (a.local_url) {
        urlEl.innerHTML =
          '<a href="' +
          a.local_url.replace(/"/g, "&quot;") +
          '" target="_blank" rel="noopener">' +
          escapeHtml(a.local_url) +
          "</a>";
      } else {
        urlEl.textContent = "—";
      }
    }
    setText(
      "rw-meta-health",
      a.health_state === "stale" || a.health_state === "none" ? "—" : a.health_detail || "—"
    );

    var warn = document.getElementById("rw-active-warning");
    var warnText = document.getElementById("rw-active-warning-text");
    var warnLink = document.getElementById("rw-warning-processes");
    if (warn && warnText) {
      if (a.warning) {
        warn.hidden = false;
        warnText.textContent = a.warning;
        if (warnLink) warnLink.hidden = a.warning_code !== "port_orphan";
      } else {
        warn.hidden = true;
        warnText.textContent = "";
        if (warnLink) warnLink.hidden = true;
      }
    }

    if (a.run_id && viewLogs) {
      viewLogs.href =
        root.getAttribute("data-logs-page") + "?run=" + encodeURIComponent(a.run_id);
    } else if (viewLogs) {
      viewLogs.href = root.getAttribute("data-logs-page");
    }
    if (openApp) {
      if (a.local_url) {
        openApp.href = a.local_url;
      } else {
        openApp.href = "#";
      }
    }

    root.setAttribute("data-auto-refresh", a.auto_refresh ? "1" : "0");
    applyActiveActions();
    lastPollKey = "";
    syncPolling();
  }

  function applyActiveActions() {
    var actions = (activeRun && activeRun.actions) || ["start"];
    var live = isLiveSelection();
    var liveBlocked = live && root.getAttribute("data-live-allowed") !== "1";
    var needsConfirm = live && liveChk && !liveChk.checked;

    function showAction(el, name, asButton) {
      if (!el) return;
      var allowed = actions.indexOf(name) >= 0;
      if (asButton) {
        el.hidden = !allowed && name === "retry";
        if (name !== "retry") el.hidden = false;
        el.disabled = !allowed;
        if (name === "start" || name === "retry") {
          if (!allowed) {
            el.disabled = true;
          } else if (liveBlocked || needsConfirm) {
            el.disabled = true;
          } else {
            el.disabled = false;
          }
        }
      } else {
        el.hidden = !allowed;
      }
    }

    showAction(startBtn, "start", true);
    showAction(stopBtn, "stop", true);
    showAction(restartBtn, "restart", true);
    showAction(retryBtn, "retry", true);
    if (retryBtn) {
      retryBtn.hidden = actions.indexOf("retry") < 0;
      retryBtn.disabled = actions.indexOf("retry") < 0;
    }
    if (openApp) openApp.hidden = actions.indexOf("open_app") < 0 || !activeRun || !activeRun.local_url;
    if (viewLogs) viewLogs.hidden = actions.indexOf("view_logs") < 0;

    if ((actions.indexOf("start") >= 0 || actions.indexOf("retry") >= 0) && needsConfirm && !liveBlocked) {
      setStatus("Check “Confirm Live profile”, then Start.");
    }
  }

  function renderHistory(history) {
    var tbody = document.getElementById("rw-history-body");
    var countEl = document.getElementById("rw-history-count");
    var rows = (history || []).slice(0, 5);
    if (countEl) countEl.textContent = String(rows.length);
    if (!tbody) return;
    if (!rows.length) {
      tbody.innerHTML =
        '<tr><td colspan="8" class="muted empty-compact">No previous runs yet.</td></tr>';
      return;
    }
    tbody.innerHTML = rows
      .map(function (run) {
        var health =
          run.health_state === "stale" || run.health_state === "none"
            ? "—"
            : escapeHtml(run.health_detail || "—");
        return (
          '<tr class="rw-history-row" data-run-id="' +
          escapeHtml(run.run_id || "") +
          '" tabindex="0">' +
          '<td><span class="rw-status-badge tone-' +
          escapeHtml(run.display_tone || "gray") +
          '">' +
          escapeHtml(run.display_status || run.status || "") +
          "</span></td>" +
          "<td>" +
          escapeHtml(run.process_state || "") +
          "</td>" +
          '<td class="muted">' +
          health +
          "</td>" +
          "<td>" +
          escapeHtml(run.profile_id || "") +
          "</td>" +
          "<td>" +
          escapeHtml(run.environment || "") +
          "</td>" +
          '<td class="mono">' +
          escapeHtml(run.port || "—") +
          "</td>" +
          '<td class="mono">' +
          escapeHtml(run.pid || "—") +
          "</td>" +
          '<td class="muted">' +
          escapeHtml(((run.started_at || "").slice(0, 19) || "").replace("T", " ") || "—") +
          "</td></tr>"
        );
      })
      .join("");
    bindHistoryRows(rows);
  }

  function bindHistoryRows(history) {
    var detail = document.getElementById("rw-history-detail");
    document.querySelectorAll(".rw-history-row").forEach(function (row) {
      row.onclick = function () {
        var id = row.getAttribute("data-run-id");
        var run = (history || []).find(function (x) {
          return x.run_id === id;
        });
        if (!run || !detail) return;
        detail.textContent =
          (run.display_status || run.status) +
          " · process " +
          (run.process_state || "—") +
          " · health " +
          (run.health_state === "stale" || run.health_state === "none"
            ? "—"
            : run.health_detail || "—") +
          (run.error ? " · " + run.error : "") +
          (run.warning ? " · " + run.warning : "");
        if (run.run_id && viewLogs) {
          viewLogs.href =
            root.getAttribute("data-logs-page") + "?run=" + encodeURIComponent(run.run_id);
        }
      };
    });
  }

  function refreshDashboard() {
    if (document.visibilityState === "hidden") {
      return Promise.resolve();
    }
    return fetch(root.getAttribute("data-runs-url"))
      .then(function (r) {
        return r.json();
      })
      .then(function (data) {
        if (!data.ok) return;
        renderActive(data.active);
        renderHistory(data.history || []);
        var hint = document.getElementById("rw-active-refresh-hint");
        if (hint) {
          hint.textContent = data.auto_refresh
            ? "Auto-refreshing status every " + Math.round(statusIntervalMs / 1000) + "s"
            : "Process and health are shown separately";
        }
      });
  }

  function tabVisible() {
    return document.visibilityState !== "hidden";
  }

  function panelVisible() {
    if (!procPanel || typeof procPanel.getBoundingClientRect !== "function") {
      return true;
    }
    var rect = procPanel.getBoundingClientRect();
    var vh = window.innerHeight || document.documentElement.clientHeight || 0;
    return rect.bottom > 0 && rect.top < vh;
  }

  function shouldPollStatus() {
    if (!tabVisible()) return false;
    return root.getAttribute("data-auto-refresh") === "1";
  }

  function shouldAutoScanProcesses() {
    if (!tabVisible() || !panelVisible()) return false;
    return root.getAttribute("data-auto-refresh") === "1";
  }

  function clearStatusTimer() {
    if (statusTimer) {
      clearInterval(statusTimer);
      statusTimer = null;
    }
  }

  function clearScanTimer() {
    if (scanTimer) {
      clearInterval(scanTimer);
      scanTimer = null;
    }
  }

  function syncPolling() {
    var wantStatus = shouldPollStatus();
    var wantScan = shouldAutoScanProcesses();
    var key = (wantStatus ? "1" : "0") + "|" + (wantScan ? "1" : "0");
    if (key === lastPollKey) return;
    lastPollKey = key;
    clearStatusTimer();
    clearScanTimer();
    if (wantStatus) {
      statusTimer = setInterval(function () {
        if (!shouldPollStatus()) {
          lastPollKey = "";
          syncPolling();
          return;
        }
        refreshDashboard();
      }, statusIntervalMs);
    }
    if (wantScan) {
      scanTimer = setInterval(function () {
        if (!shouldAutoScanProcesses()) {
          lastPollKey = "";
          syncPolling();
          return;
        }
        scanProcesses({ reason: "auto" });
      }, scanIntervalMs);
    }
  }

  function setScanUi(busy, message, isError) {
    if (scanBtn) scanBtn.disabled = !!busy;
    if (scanSpinner) scanSpinner.hidden = !busy;
    if (procStatus) {
      procStatus.textContent = message || "";
      procStatus.className = isError ? "banner banner-error" : "muted";
    }
  }

  function formatScannedAt(date) {
    try {
      return date.toLocaleString(undefined, {
        hour: "2-digit",
        minute: "2-digit",
        second: "2-digit",
      });
    } catch (e) {
      return date.toISOString().slice(11, 19);
    }
  }

  document.getElementById("rw-preview").onclick = function () {
    fetch(root.getAttribute("data-preview-url"), {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload()),
    })
      .then(function (r) {
        return r.json();
      })
      .then(function (data) {
        if (!data.ok) {
          previewEl.textContent = data.error || "Preview failed";
          return;
        }
        previewEl.textContent =
          (data.command_preview || []).join(" ") +
          "\ncwd: " +
          data.cwd +
          "\nport mode: " +
          (data.port_mode || "—") +
          (data.port != null ? " · port " + data.port : "") +
          "\nlocal: " +
          (data.local_url || "—") +
          "\nhealth: " +
          (data.health_url || "—") +
          "\nenv names: " +
          (data.env_names || []).join(", ");
      });
  };

  document.getElementById("rw-find-port").onclick = function () {
    if (portMode() === "fixed" || portMode() === "none") {
      setStatus("Find available port is disabled for this port mode", true);
      return;
    }
    fetch(root.getAttribute("data-find-port-url"), {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ port: parseInt(portEl.value, 10) }),
    })
      .then(function (r) {
        return r.json();
      })
      .then(function (data) {
        if (data.port) portEl.value = data.port;
        setStatus(
          data.available
            ? "Preferred port is available"
            : "Using alternate port " + data.port
        );
      });
  };

  function doStart(isRetry) {
    var live = isLiveSelection();
    if (live && root.getAttribute("data-live-allowed") !== "1") {
      setStatus("Live runs are blocked by REPO_WS_ALLOW_LIVE_RUNS", true);
      return;
    }
    if (live && !liveChk.checked) {
      setStatus("Check “Confirm Live profile” above, then click Start.", true);
      if (liveGate) liveGate.scrollIntoView({ behavior: "smooth", block: "center" });
      return;
    }
    if (startBtn) startBtn.disabled = true;
    if (retryBtn) retryBtn.disabled = true;
    setStatus(isRetry ? "Retrying…" : "Starting…");
    fetch(root.getAttribute("data-start-url"), {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload()),
    })
      .then(function (r) {
        return r.json().then(function (data) {
          return { data: data };
        });
      })
      .then(function (result) {
        var data = result.data || {};
        if (!data.ok) {
          applyActiveActions();
          if (data.code === "process_conflict" || data.code === "port_occupied") {
            setStatus(
              (data.error || "Port/process conflict") +
                " — open Repository Processes below.",
              true
            );
            var anchor = document.getElementById("repository-processes");
            if (anchor) anchor.scrollIntoView({ behavior: "smooth", block: "start" });
            scanProcesses({ reason: "conflict" });
            return;
          }
          setStatus(data.error || "Start failed", true);
          return;
        }
        setStatus("Started");
        refreshDashboard().then(function () {
          scanProcesses({ reason: "after_start" });
          if (data.run && data.run.local_url) {
            try {
              window.open(data.run.local_url, "_blank", "noopener");
            } catch (e) {}
          }
        });
      })
      .catch(function (err) {
        applyActiveActions();
        setStatus("Start failed: " + (err && err.message ? err.message : "network error"), true);
      });
  }

  startBtn.onclick = function () {
    doStart(false);
  };
  if (retryBtn) {
    retryBtn.onclick = function () {
      doStart(true);
    };
  }

  stopBtn.onclick = function () {
    if (!activeRun || !activeRun.run_id) return;
    var url = root
      .getAttribute("data-stop-base")
      .replace("__ID__", encodeURIComponent(activeRun.run_id));
    setStatus("Stopping…");
    fetch(url, { method: "POST", headers: { "Content-Type": "application/json" }, body: "{}" })
      .then(function (r) {
        return r.json();
      })
      .then(function () {
        refreshDashboard();
        scanProcesses({ reason: "after_stop" });
      });
  };

  restartBtn.onclick = function () {
    if (!activeRun || !activeRun.run_id) return;
    var url = root
      .getAttribute("data-restart-base")
      .replace("__ID__", encodeURIComponent(activeRun.run_id));
    setStatus("Restarting…");
    fetch(url, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ confirm_live: !!(liveChk && liveChk.checked) }),
    })
      .then(function (r) {
        return r.json();
      })
      .then(function (data) {
        if (!data.ok) {
          setStatus(data.error || "Restart failed", true);
          return;
        }
        refreshDashboard();
        scanProcesses({ reason: "after_restart" });
      });
  };

  var procStatus = document.getElementById("rw-proc-status");
  var procCache = [];

  function escapeHtml(s) {
    return String(s || "")
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;");
  }

  function renderProcesses(list) {
    var tbody = document.querySelector("#rw-proc-table tbody");
    if (!tbody) return;
    procCache = list || [];
    if (!procCache.length) {
      tbody.innerHTML =
        '<tr><td colspan="9" class="muted empty-compact">No repository-related processes detected.</td></tr>';
      return;
    }
    tbody.innerHTML = procCache
      .map(function (p, idx) {
        var kind = escapeHtml(p.process_kind || (p.managed_by_hub ? "Managed" : "External"));
        var linked = p.linked_to_active
          ? ' <span class="badge">Active Application</span>'
          : "";
        var actions = [];
        actions.push(
          "<button type='button' class='btn btn-sm rw-proc-copy' data-idx='" +
            idx +
            "'>Copy Command</button>"
        );
        if (p.view_only) {
          actions.push("<span class='muted'>view-only</span>");
        } else {
          actions.push(
            "<button type='button' class='btn btn-sm rw-proc-stop' data-idx='" +
              idx +
              "' data-force='0'>Stop Gracefully</button>"
          );
          actions.push(
            "<button type='button' class='btn btn-sm rw-proc-stop' data-idx='" +
              idx +
              "' data-force='1'>Force Stop</button>"
          );
        }
        return (
          "<tr>" +
          "<td class='mono'>" +
          p.pid +
          "</td>" +
          "<td>" +
          kind +
          linked +
          "</td>" +
          "<td class='mono'>" +
          escapeHtml(p.executable || "") +
          "</td>" +
          "<td class='mono muted'>" +
          escapeHtml(p.command_redacted || "") +
          "</td>" +
          "<td class='mono'>" +
          (p.port || "—") +
          "</td>" +
          "<td class='muted'>" +
          escapeHtml(((p.started_at || "").slice(0, 19) || "").replace("T", " ") || "—") +
          "</td>" +
          "<td class='muted'>" +
          escapeHtml((p.detection_reasons || []).join(", ")) +
          "</td>" +
          "<td>" +
          escapeHtml(p.confidence || "") +
          "</td>" +
          "<td class='repo-actions'>" +
          actions.join(" ") +
          "</td></tr>"
        );
      })
      .join("");
    bindProcButtons();
  }

  function scanProcesses(opts) {
    opts = opts || {};
    var url = root.getAttribute("data-processes-url");
    if (!url) return Promise.resolve();
    if (scanInFlight) {
      return Promise.resolve({ skipped: true, reason: "in_flight" });
    }
    scanInFlight = true;
    scanAbort = typeof AbortController !== "undefined" ? new AbortController() : null;
    var timedOut = false;
    var timeoutId = setTimeout(function () {
      timedOut = true;
      if (scanAbort) {
        try {
          scanAbort.abort();
        } catch (e2) {}
      }
    }, scanTimeoutMs);

    setScanUi(true, "", false);

    var fetchOpts = {};
    if (scanAbort) fetchOpts.signal = scanAbort.signal;

    return fetch(url, fetchOpts)
      .then(function (r) {
        return r.json();
      })
      .then(function (data) {
        clearTimeout(timeoutId);
        scanInFlight = false;
        setScanUi(false, "", false);
        if (!data.ok) {
          setScanUi(false, data.error || "Scan failed", true);
          return data;
        }
        renderProcesses(data.processes || []);
        if (lastScannedEl) {
          lastScannedEl.textContent = "Last scanned: " + formatScannedAt(new Date());
        }
        setScanUi(false, (data.count || 0) + " process(es)", false);
        return data;
      })
      .catch(function (err) {
        clearTimeout(timeoutId);
        scanInFlight = false;
        var msg = timedOut
          ? "Process scan timed out after " + Math.round(scanTimeoutMs / 1000) + "s"
          : err && err.name === "AbortError"
            ? "Process scan timed out after " + Math.round(scanTimeoutMs / 1000) + "s"
            : "Scan failed";
        setScanUi(false, msg, true);
        return { ok: false, error: msg };
      });
  }

  function bindProcButtons() {
    document.querySelectorAll(".rw-proc-copy").forEach(function (btn) {
      btn.onclick = function () {
        var p = procCache[parseInt(btn.getAttribute("data-idx"), 10)];
        if (!p) return;
        var text = p.command_redacted || p.executable || "";
        if (navigator.clipboard && navigator.clipboard.writeText) {
          navigator.clipboard.writeText(text);
          setScanUi(false, "Copied command for PID " + p.pid, false);
        }
      };
    });
    document.querySelectorAll(".rw-proc-stop").forEach(function (btn) {
      btn.onclick = function () {
        var p = procCache[parseInt(btn.getAttribute("data-idx"), 10)];
        if (!p) return;
        var force = btn.getAttribute("data-force") === "1";
        if (
          !window.confirm(
            (force ? "Force stop" : "Stop gracefully") +
              (p.managed_by_hub ? " hub-managed" : " external") +
              " PID " +
              p.pid +
              "?"
          )
        ) {
          return;
        }
        var typed = "";
        if (p.requires_typed_confirm) {
          typed = window.prompt(
            'Type "' + (p.typed_confirm_phrase || "STOP PROCESS " + p.pid) + '" to confirm:',
            ""
          );
          if (typed == null) return;
        }
        setScanUi(false, (force ? "Force stopping" : "Stopping") + " PID " + p.pid + "…", false);
        fetch(root.getAttribute("data-process-stop-url"), {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            pid: p.pid,
            identity_token: p.identity_token,
            force: force,
            confirm: true,
            typed_confirm: typed,
            run_id: p.run_id || null,
            port: p.port || null,
          }),
        })
          .then(function (r) {
            return r.json();
          })
          .then(function (data) {
            if (!data.ok) {
              setScanUi(false, data.error || "Stop blocked", true);
              return;
            }
            setScanUi(
              false,
              "PID " +
                p.pid +
                (data.ended ? " ended" : " may still be alive") +
                (data.port_released === true
                  ? "; port released"
                  : data.port_released === false
                    ? "; port still occupied"
                    : ""),
              false
            );
            refreshDashboard();
            scanProcesses({ reason: "after_stop" });
          });
      };
    });
  }

  if (scanBtn) {
    scanBtn.onclick = function () {
      scanProcesses({ reason: "manual" });
    };
  }

  document.addEventListener("visibilitychange", function () {
    syncPolling();
  });
  window.addEventListener(
    "scroll",
    function () {
      syncPolling();
    },
    { passive: true }
  );
  window.addEventListener("resize", function () {
    syncPolling();
  });

  profileEl.addEventListener("change", syncProfile);
  envEl.addEventListener("change", function () {
    updateLiveGate();
    applyActiveActions();
  });
  if (liveChk) {
    liveChk.addEventListener("change", function () {
      updateLiveGate();
      applyActiveActions();
    });
  }

  syncProfile();
  refreshDashboard().then(function () {
    // One immediate scan on open; automatic cadence only while active + visible.
    scanProcesses({ reason: "page_load" });
    syncPolling();
  });
})();
