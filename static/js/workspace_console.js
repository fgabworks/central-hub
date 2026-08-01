/**
 * VS Code-style Workspace Console (bottom dock).
 * Lazy-loads tab data; pauses polling when hidden; works with the right AI dock.
 */
(function () {
  "use strict";

  var host = document.getElementById("workspace-console-host");
  if (!host) return;

  var bootstrap = {};
  try {
    bootstrap = JSON.parse(host.getAttribute("data-wc-bootstrap") || "{}");
  } catch (e) {
    bootstrap = {};
  }

  var prefs = Object.assign(
    { open: false, minimized: false, maximized: false, height: 280, tab: "problems", min_height: 160, max_height: 640 },
    (bootstrap && bootstrap.prefs) || {}
  );

  var shell = document.querySelector(".app-shell");
  var panel = document.getElementById("wc-panel");
  var toggleBtn = document.getElementById("wc-toggle");
  var resizeHandle = document.getElementById("wc-resize");
  var fetchFn = (window.HubPerf && window.HubPerf.dedupeFetch) || fetch;
  var persistTimer = null;
  var pollTimer = null;
  var loaded = {};
  var inFlight = {};
  var terminalCatalog = null;
  var MOBILE_MQ = "(max-width: 960px)";

  function isMobile() {
    return window.matchMedia(MOBILE_MQ).matches;
  }

  function visibleHeight() {
    if (!prefs.open) return 0;
    if (prefs.minimized) return 36;
    if (prefs.maximized) return Math.max(prefs.min_height, window.innerHeight - 80);
    return prefs.height;
  }

  function applyChrome() {
    if (!shell) return;
    var open = !!prefs.open;
    var minimized = !!prefs.minimized;
    var maximized = !!prefs.maximized;
    var height = visibleHeight();
    shell.classList.toggle("is-wc-open", open && !minimized);
    shell.classList.toggle("is-wc-minimized", open && minimized);
    shell.classList.toggle("is-wc-maximized", open && maximized);
    shell.classList.toggle("is-wc-mobile", isMobile());
    shell.style.setProperty("--wc-height", height + "px");
    host.hidden = !open;
    if (toggleBtn) {
      toggleBtn.setAttribute("aria-expanded", open && !minimized ? "true" : "false");
      toggleBtn.classList.toggle("is-active", open);
    }
    var topbar = document.getElementById("wc-topbar-toggle");
    if (topbar) {
      topbar.setAttribute("aria-expanded", open && !minimized ? "true" : "false");
      topbar.classList.toggle("is-active", open);
    }
    var rail = document.getElementById("ar-console");
    if (rail) {
      rail.setAttribute("aria-expanded", open && !minimized ? "true" : "false");
      rail.classList.toggle("is-active", open && !minimized);
    }
    document.querySelectorAll(".wc-tab").forEach(function (btn) {
      var active = btn.getAttribute("data-wc-tab") === prefs.tab;
      btn.classList.toggle("is-active", active);
      btn.setAttribute("aria-selected", active ? "true" : "false");
    });
    document.querySelectorAll(".wc-pane").forEach(function (pane) {
      var show = pane.getAttribute("data-wc-pane") === prefs.tab;
      pane.hidden = !show;
    });
    if (open && !minimized) ensureTab(prefs.tab);
    else stopPolling();
    // Pause xterm rendering when console hidden/minimized; PTY keeps running.
    if (window.WCTerminal && window.WCTerminal.setRenderingPaused) {
      window.WCTerminal.setRenderingPaused(!open || minimized || prefs.tab !== "terminal" || document.hidden);
    }
    if (open && !minimized && prefs.tab === "terminal" && window.WCTerminal && window.WCTerminal.fit) {
      setTimeout(function () { window.WCTerminal.fit(); }, 30);
    }
  }

  function persist(immediate) {
    clearTimeout(persistTimer);
    var run = function () {
      fetch(bootstrap.prefs_url || "/api/workspace-console/prefs", {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        credentials: "same-origin",
        body: JSON.stringify({
          open: prefs.open,
          minimized: prefs.minimized,
          maximized: prefs.maximized,
          height: prefs.height,
          tab: prefs.tab,
          terminal_session_id: prefs.terminal_session_id || "",
          terminal_split: !!prefs.terminal_split && !!(prefs.terminal_split_session_id || ""),
          terminal_split_session_id: prefs.terminal_split ? (prefs.terminal_split_session_id || "") : "",
        }),
      }).catch(function () {});
    };
    if (immediate) run();
    else persistTimer = setTimeout(run, 250);
  }

  function setOpen(next) {
    prefs.open = !!next;
    if (!prefs.open) {
      prefs.minimized = false;
      prefs.maximized = false;
    }
    applyChrome();
    persist(true);
  }

  function toggle() {
    if (prefs.open && !prefs.minimized) setOpen(false);
    else {
      prefs.minimized = false;
      setOpen(true);
    }
  }

  function setTab(tab) {
    prefs.tab = tab;
    loaded[tab] = false;
    applyChrome();
    persist(false);
  }

  function stopPolling() {
    if (pollTimer) {
      clearInterval(pollTimer);
      pollTimer = null;
    }
  }

  function startPollingIfNeeded() {
    stopPolling();
    if (!prefs.open || prefs.minimized) return;
    if (document.hidden) return;
    if (prefs.tab !== "output" && prefs.tab !== "ports") return;
    var interval = prefs.tab === "ports" ? 15000 : 8000;
    pollTimer = setInterval(function () {
      if (!prefs.open || prefs.minimized || document.hidden) return;
      loaded[prefs.tab] = false;
      ensureTab(prefs.tab);
    }, interval);
  }

  function gate(key) {
    if (inFlight[key]) return false;
    inFlight[key] = true;
    return true;
  }

  function release(key) {
    inFlight[key] = false;
  }

  function ensureTab(tab) {
    if (!prefs.open || prefs.minimized) return;
    if (loaded[tab]) {
      startPollingIfNeeded();
      return;
    }
    if (tab === "problems") loadProblems();
    else if (tab === "output") loadOutput();
    else if (tab === "debug") loadDebug();
    else if (tab === "terminal") loadTerminal();
    else if (tab === "ports") loadPorts();
  }

  function loadProblems() {
    if (!gate("problems")) return;
    fetchFn(bootstrap.problems_url || "/api/workspace-console/problems", { credentials: "same-origin" })
      .then(function (r) { return r.json(); })
      .then(function (data) {
        var el = document.getElementById("wc-problems");
        var badge = document.getElementById("wc-badge-problems");
        var rows = (data && data.problems) || [];
        if (badge) {
          badge.hidden = !rows.length;
          badge.textContent = String(rows.length);
        }
        if (!el) return;
        if (!rows.length) {
          el.innerHTML = '<p class="muted">No problems reported.</p>';
        } else {
          el.innerHTML = rows
            .map(function (row) {
              return (
                '<article class="wc-problem severity-' +
                (row.severity || "warning") +
                '"><strong>' +
                escapeHtml(row.title || "") +
                '</strong><span class="wc-meta">' +
                escapeHtml(row.source || "") +
                (row.repository_id ? " · " + escapeHtml(row.repository_id) : "") +
                '</span><p>' +
                escapeHtml(row.detail || "") +
                "</p></article>"
              );
            })
            .join("");
        }
        loaded.problems = true;
      })
      .catch(function () {})
      .then(function () { release("problems"); startPollingIfNeeded(); });
  }

  function loadOutput() {
    if (!gate("output")) return;
    var source = (document.getElementById("wc-output-source") || {}).value || "all";
    var url = (bootstrap.output_url || "/api/workspace-console/output") + "?source=" + encodeURIComponent(source) + "&limit=200";
    fetchFn(url, { credentials: "same-origin" })
      .then(function (r) { return r.json(); })
      .then(function (data) {
        var el = document.getElementById("wc-output");
        if (!el) return;
        var lines = (data && data.lines) || [];
        el.textContent = lines
          .map(function (line) {
            return "[" + (line.source || "?") + "] " + (line.text || "");
          })
          .join("\n") || "No output.";
        loaded.output = true;
      })
      .catch(function () {})
      .then(function () { release("output"); startPollingIfNeeded(); });
  }

  function loadDebug() {
    if (!gate("debug")) return;
    fetchFn(bootstrap.debug_url || "/api/workspace-console/debug", { credentials: "same-origin" })
      .then(function (r) { return r.json(); })
      .then(function (data) {
        var el = document.getElementById("wc-debug");
        if (!el) return;
        el.textContent = JSON.stringify(
          {
            diagnostics: (data && data.diagnostics) || {},
            events: (data && data.events) || [],
          },
          null,
          2
        );
        loaded.debug = true;
      })
      .catch(function () {})
      .then(function () { release("debug"); });
  }

  function loadTerminal() {
    if (!gate("terminal")) return;
    fetchFn(bootstrap.terminal_url || "/api/workspace-console/terminal", { credentials: "same-origin" })
      .then(function (r) { return r.json(); })
      .then(function (data) {
        terminalCatalog = data;
        var initTerm = window.WCTerminal
          ? window.WCTerminal.init({
              activeId: prefs.terminal_session_id || "",
              splitId: prefs.terminal_split_session_id || "",
              // Only attempt restore when both ids were saved; JS validates live sessions.
              split: !!(prefs.terminal_split && prefs.terminal_split_session_id),
            })
          : Promise.resolve();
        return initTerm.then(function () {
          if (window.WCTerminal && window.WCTerminal.fillCatalog) {
            window.WCTerminal.fillCatalog(data);
          }
          if (window.WCTerminal) {
            window.WCTerminal.onSessionChange = function (id, meta) {
              prefs.terminal_session_id = id || "";
              var splitOn = !!(meta && meta.split && meta.splitId);
              prefs.terminal_split = splitOn;
              prefs.terminal_split_session_id = splitOn ? String(meta.splitId || "") : "";
              persist(false);
            };
          }
          var out = document.getElementById("wc-terminal-out");
          if (out) {
            out.hidden = false;
            out.textContent = (data && data.message) || "Interactive repository terminal ready.";
          }
          loaded.terminal = true;
        });
      })
      .catch(function () {})
      .then(function () { release("terminal"); });
  }

  function fillProfiles() {
    var repoSel = document.getElementById("wc-term-repo");
    var profileSel = document.getElementById("wc-term-profile");
    if (!repoSel || !profileSel || !terminalCatalog) return;
    var repo = ((terminalCatalog.repositories || []).find(function (r) { return r.id === repoSel.value; })) || null;
    var profiles = (repo && repo.profiles) || [];
    profileSel.innerHTML = profiles
      .map(function (p) {
        return '<option value="' + escapeAttr(p.id) + '">' + escapeHtml(p.label || p.id) + "</option>";
      })
      .join("") || '<option value="">No approved profiles</option>';
  }

  function startProfile() {
    var repoId = (document.getElementById("wc-term-repo") || {}).value;
    var profileId = (document.getElementById("wc-term-profile") || {}).value;
    var env = (document.getElementById("wc-term-env") || {}).value || "development";
    var out = document.getElementById("wc-terminal-out");
    if (!repoId || !profileId) {
      if (out) out.textContent = "Select a repository and approved profile.";
      return;
    }
    if (env === "live" && !window.confirm("Start a live profile? This requires explicit confirmation.")) return;
    if (out) out.textContent = "Starting approved profile…";
    fetch("/api/workspace-console/terminal/start", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      credentials: "same-origin",
      body: JSON.stringify({
        repository_id: repoId,
        profile_id: profileId,
        environment: env,
        confirm_live: env === "live",
      }),
    })
      .then(function (r) { return r.json().then(function (data) { return { ok: r.ok, data: data }; }); })
      .then(function (res) {
        if (out) out.textContent = JSON.stringify(res.data, null, 2);
        if (res.ok) {
          prefs.tab = "output";
          loaded.output = false;
          applyChrome();
        }
      })
      .catch(function (err) {
        if (out) out.textContent = String(err && err.message ? err.message : err);
      });
  }

  function loadPorts() {
    if (!gate("ports")) return;
    fetchFn(bootstrap.ports_url || "/api/workspace-console/ports", { credentials: "same-origin" })
      .then(function (r) { return r.json(); })
      .then(function (data) {
        var el = document.getElementById("wc-ports");
        var badge = document.getElementById("wc-badge-ports");
        var rows = (data && data.ports) || [];
        if (badge) {
          badge.hidden = !rows.length;
          badge.textContent = String(rows.length);
        }
        if (!el) return;
        if (!rows.length) {
          el.innerHTML = '<p class="muted">No listening ports detected for connected repositories.</p>';
        } else {
          el.innerHTML =
            '<table class="wc-table"><thead><tr><th>Port</th><th>PID</th><th>Process</th><th>Repository</th><th>Terminal</th><th>Health</th><th>State</th><th></th></tr></thead><tbody>' +
            rows
              .map(function (row) {
                var state = row.managed_by_hub ? "managed" : "external";
                if (row.terminal_owned) state += " · terminal";
                var termCell = row.terminal_name
                  ? escapeHtml(row.terminal_name)
                  : row.terminal_session_id
                    ? escapeHtml(String(row.terminal_session_id).slice(0, 8))
                    : "—";
                var actions = "";
                if (row.open_url) {
                  actions +=
                    '<a class="btn btn-sm wc-control-btn" href="' +
                    escapeAttr(row.open_url) +
                    '" target="_blank" rel="noopener">Open URL</a> ';
                }
                if (row.run_id) {
                  actions +=
                    '<button type="button" class="btn btn-sm" data-wc-view-logs data-run="' +
                    escapeAttr(row.run_id) +
                    '" data-repo="' +
                    escapeAttr(row.repository_id || "") +
                    '">View Logs</button> ';
                }
                if (row.can_stop) {
                  actions +=
                    '<button type="button" class="btn btn-sm" data-wc-stop ' +
                    'data-repo="' + escapeAttr(row.repository_id) + '" ' +
                    'data-pid="' + escapeAttr(row.pid) + '" ' +
                    'data-token="' + escapeAttr(row.identity_token) + '" ' +
                    'data-run="' + escapeAttr(row.run_id || "") + '" ' +
                    'data-port="' + escapeAttr(row.port || "") + '" ' +
                    'data-managed="' + (row.managed_by_hub ? "1" : "0") + '" ' +
                    'data-typed="' + (row.requires_typed_confirm ? "1" : "0") + '" ' +
                    'data-phrase="' + escapeAttr(row.typed_confirm_phrase || "") + '">Stop Process</button>';
                } else {
                  actions += '<span class="muted">view-only</span>';
                }
                return (
                  "<tr><td>" +
                  escapeHtml(row.port || "—") +
                  "</td><td>" +
                  escapeHtml(row.pid || "") +
                  "</td><td>" +
                  escapeHtml(row.process || "") +
                  "</td><td>" +
                  escapeHtml(row.repository_name || row.repository_id || "") +
                  "</td><td>" +
                  termCell +
                  "</td><td>" +
                  escapeHtml(row.health || row.confidence || "") +
                  "</td><td>" +
                  escapeHtml(state) +
                  '</td><td class="wc-row-actions">' +
                  actions +
                  "</td></tr>"
                );
              })
              .join("") +
            "</tbody></table>";
        }
        loaded.ports = true;
      })
      .catch(function () {})
      .then(function () { release("ports"); startPollingIfNeeded(); });
  }

  function stopPort(btn) {
    var repo = btn.getAttribute("data-repo");
    var pid = btn.getAttribute("data-pid");
    var token = btn.getAttribute("data-token");
    var typed = btn.getAttribute("data-typed") === "1";
    var phrase = btn.getAttribute("data-phrase") || "";
    var managed = btn.getAttribute("data-managed") === "1";
    if (!repo || !pid || !token) return;
    if (!window.confirm("Stop process PID " + pid + " for " + repo + "?")) return;
    var typedConfirm = "";
    if (typed) {
      typedConfirm = window.prompt('Type "' + phrase + '" to stop this external process') || "";
      if (typedConfirm !== phrase) {
        window.alert("Typed confirmation did not match.");
        return;
      }
    }
    btn.disabled = true;
    fetch("/api/workspace-console/ports/stop", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      credentials: "same-origin",
      body: JSON.stringify({
        repository_id: repo,
        pid: Number(pid),
        identity_token: token,
        confirm: true,
        typed_confirm: typedConfirm,
        run_id: btn.getAttribute("data-run") || null,
        port: btn.getAttribute("data-port") ? Number(btn.getAttribute("data-port")) : null,
        managed_by_hub: managed,
        force: false,
      }),
    })
      .then(function (r) { return r.json(); })
      .then(function () {
        loaded.ports = false;
        ensureTab("ports");
      })
      .catch(function () {})
      .then(function () { btn.disabled = false; });
  }

  function escapeHtml(value) {
    return String(value == null ? "" : value)
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;");
  }

  function escapeAttr(value) {
    return escapeHtml(value).replace(/'/g, "&#39;");
  }

  // Events
  if (toggleBtn) toggleBtn.addEventListener("click", toggle);
  var topbarToggle = document.getElementById("wc-topbar-toggle");
  if (topbarToggle) topbarToggle.addEventListener("click", toggle);
  var railConsole = document.getElementById("ar-console");
  if (railConsole) railConsole.addEventListener("click", toggle);
  var minBtn = document.getElementById("wc-minimize");
  var maxBtn = document.getElementById("wc-maximize");
  var closeBtn = document.getElementById("wc-close");
  if (minBtn) {
    minBtn.addEventListener("click", function () {
      prefs.open = true;
      prefs.minimized = !prefs.minimized;
      if (prefs.minimized) prefs.maximized = false;
      applyChrome();
      persist(true);
    });
  }
  if (maxBtn) {
    maxBtn.addEventListener("click", function () {
      prefs.open = true;
      prefs.maximized = !prefs.maximized;
      prefs.minimized = false;
      applyChrome();
      persist(true);
    });
  }
  if (closeBtn) {
    closeBtn.addEventListener("click", function () {
      setOpen(false);
    });
  }

  document.querySelectorAll(".wc-tab").forEach(function (btn) {
    btn.addEventListener("click", function () {
      setTab(btn.getAttribute("data-wc-tab"));
    });
  });

  document.querySelectorAll("[data-wc-refresh]").forEach(function (btn) {
    btn.addEventListener("click", function () {
      var tab = btn.getAttribute("data-wc-refresh");
      loaded[tab] = false;
      ensureTab(tab);
    });
  });

  var sourceSel = document.getElementById("wc-output-source");
  if (sourceSel) {
    sourceSel.addEventListener("change", function () {
      loaded.output = false;
      ensureTab("output");
    });
  }

  var repoSel = document.getElementById("wc-term-repo");
  if (repoSel) repoSel.addEventListener("change", fillProfiles);
  var startBtn = document.getElementById("wc-term-start");
  if (startBtn) startBtn.addEventListener("click", startProfile);

  host.addEventListener("click", function (event) {
    var btn = event.target.closest("[data-wc-stop]");
    if (btn) stopPort(btn);
    var logs = event.target.closest("[data-wc-view-logs]");
    if (logs) {
      prefs.tab = "output";
      loaded.output = false;
      applyChrome();
    }
  });

  document.addEventListener("keydown", function (event) {
    if ((event.ctrlKey || event.metaKey) && String(event.key).toLowerCase() === "j") {
      var tag = (event.target && event.target.tagName) || "";
      // Allow Ctrl+J from page chrome; skip when typing in inputs (xterm uses textarea).
      if (tag === "INPUT" || tag === "SELECT" || (event.target && event.target.isContentEditable)) {
        return;
      }
      if (tag === "TEXTAREA" && !(event.target.classList && event.target.classList.contains("xterm-helper-textarea"))) {
        return;
      }
      event.preventDefault();
      toggle();
    }
  });

  document.addEventListener("visibilitychange", function () {
    if (document.hidden) {
      stopPolling();
      if (window.WCTerminal && window.WCTerminal.setRenderingPaused) {
        window.WCTerminal.setRenderingPaused(true);
      }
    } else {
      startPollingIfNeeded();
      applyChrome();
    }
  });

  window.addEventListener("resize", function () {
    applyChrome();
  });

  // Drag resize
  if (resizeHandle) {
    var dragging = false;
    var startY = 0;
    var startH = 0;
    resizeHandle.addEventListener("mousedown", function (event) {
      if (!prefs.open || prefs.minimized) return;
      dragging = true;
      prefs.maximized = false;
      startY = event.clientY;
      startH = prefs.height;
      event.preventDefault();
    });
    window.addEventListener("mousemove", function (event) {
      if (!dragging) return;
      var next = startH + (startY - event.clientY);
      prefs.height = Math.max(prefs.min_height || 160, Math.min(prefs.max_height || 640, next));
      applyChrome();
    });
    window.addEventListener("mouseup", function () {
      if (!dragging) return;
      dragging = false;
      persist(true);
    });
  }

  applyChrome();
})();
