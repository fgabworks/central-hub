/**
 * Interactive repository terminal (xterm.js ↔ PTY WebSocket).
 * AI cannot execute — Insert into Terminal fills text without Enter.
 *
 * Split rules:
 * - Default = single full-width terminal.
 * - Split only after explicit Split click creates/attaches a second session.
 * - Never show an empty second pane; collapse on second-pane WS failure.
 * - Persist split only when two distinct live session ids exist.
 */
(function (global) {
  "use strict";

  var API = {
    sessions: "/api/workspace-console/terminal/sessions",
    insert: "/api/workspace-console/terminal/insert",
  };

  var SHELL_LABELS = {
    powershell: "PowerShell",
    cmd: "CMD",
    bash: "bash",
    sh: "sh",
  };

  var state = {
    sessions: [],
    activeId: "",
    splitId: "",
    splitEnabled: false,
    splitCreating: false,
    pendingSplitId: "",
    wantSplitRestore: false,
    catalog: null,
    views: { a: null, b: null },
    pendingInsert: null,
    hiddenActivity: 0,
    renderingPaused: false,
    initialized: false,
    fitTimer: null,
    resizeObserver: null,
    activePane: "a",
  };

  function $(id) {
    return document.getElementById(id);
  }

  function jsonFetch(url, opts) {
    opts = opts || {};
    opts.credentials = "same-origin";
    opts.headers = Object.assign({ "Content-Type": "application/json" }, opts.headers || {});
    return fetch(url, opts).then(function (r) {
      return r.json().then(function (body) {
        body._status = r.status;
        return body;
      });
    });
  }

  function wsUrl(sessionId, ticket) {
    var proto = location.protocol === "https:" ? "wss:" : "ws:";
    return proto + "//" + location.host + "/ws/workspace-console/terminal/" + sessionId + "?ticket=" + encodeURIComponent(ticket);
  }

  function ensureXterm() {
    return new Promise(function (resolve, reject) {
      if (global.Terminal) return resolve();
      // Suspend AMD (Monaco loader) so UMD xterm scripts attach to window.
      var savedDefine = global.define;
      if (typeof savedDefine === "function") {
        try { global.define = undefined; } catch (_) {}
      }
      function restoreAmd() {
        if (typeof savedDefine === "function") {
          try { global.define = savedDefine; } catch (_) {}
        }
      }
      if (!document.querySelector('link[data-wc-xterm="1"]')) {
        var link = document.createElement("link");
        link.rel = "stylesheet";
        link.href = "/static/vendor/xterm/xterm.css";
        link.setAttribute("data-wc-xterm", "1");
        document.head.appendChild(link);
      }
      var s1 = document.createElement("script");
      s1.src = "/static/vendor/xterm/xterm.js";
      s1.onload = function () {
        var s2 = document.createElement("script");
        s2.src = "/static/vendor/xterm/addon-fit.js";
        s2.onload = function () {
          restoreAmd();
          if (!global.Terminal) reject(new Error("xterm failed to load"));
          else resolve();
        };
        s2.onerror = function () { restoreAmd(); reject(new Error("xterm fit addon failed")); };
        document.head.appendChild(s2);
      };
      s1.onerror = function () { restoreAmd(); reject(new Error("xterm failed to load")); };
      document.head.appendChild(s1);
    });
  }

  function FitCtor() {
    if (global.FitAddon && global.FitAddon.FitAddon) return global.FitAddon.FitAddon;
    if (global.FitAddon) return global.FitAddon;
    return null;
  }

  function createView(slot, hostEl) {
    var term = new global.Terminal({
      cursorBlink: true,
      fontSize: 13,
      fontFamily: 'Consolas, "Courier New", monospace',
      theme: { background: "#0e1116", foreground: "#d7dde5" },
      scrollback: 5000,
      allowProposedApi: true,
    });
    var Fit = FitCtor();
    var fit = Fit ? new Fit() : null;
    if (fit) term.loadAddon(fit);
    term.open(hostEl);
    var view = {
      slot: slot,
      term: term,
      fit: fit,
      host: hostEl,
      sessionId: "",
      ws: null,
      disposed: false,
      intentionalClose: false,
    };
    term.onData(function (data) {
      if (!view.ws || view.ws.readyState !== 1) return;
      view.ws.send(JSON.stringify({ type: "in", data: data }));
    });
    term.attachCustomKeyEventHandler(function () {
      return true;
    });
    var origPaste = term.paste ? term.paste.bind(term) : null;
    if (origPaste) {
      term.paste = function (data) {
        var text = String(data || "");
        if (text.indexOf("\n") >= 0 || text.indexOf("\r") >= 0) {
          if (!window.confirm("Paste multiline command into the terminal?\n\nThis may run multiple statements if you press Enter.")) {
            return;
          }
        }
        origPaste(text);
      };
    }
    var pane = hostEl.closest ? hostEl.closest(".wc-term-view") : null;
    if (pane) {
      pane.addEventListener("mousedown", function () {
        setActivePane(slot);
      });
    }
    return view;
  }

  function scheduleFit() {
    if (state.fitTimer) clearTimeout(state.fitTimer);
    state.fitTimer = setTimeout(function () {
      state.fitTimer = null;
      requestAnimationFrame(function () {
        fitAll();
        requestAnimationFrame(fitAll);
      });
    }, 16);
  }

  function fitAll() {
    fitView(state.views.a);
    if (state.splitEnabled && state.splitId) fitView(state.views.b);
  }

  function fitView(view) {
    if (!view || !view.fit || state.renderingPaused) return;
    var pane = view.host && view.host.closest ? view.host.closest(".wc-term-view") : null;
    if (pane && pane.hidden) return;
    try {
      view.fit.fit();
      if (view.ws && view.ws.readyState === 1) {
        view.ws.send(
          JSON.stringify({ type: "resize", cols: view.term.cols, rows: view.term.rows })
        );
      }
    } catch (e) {}
  }

  function failEl(slot) {
    return $("wc-term-fail-" + slot);
  }

  function hideFail(slot) {
    var el = failEl(slot);
    if (el) el.hidden = true;
  }

  function showFail(slot, message) {
    var el = failEl(slot);
    if (!el) return;
    var msg = el.querySelector(".wc-term-ws-fail-msg");
    if (msg) msg.textContent = message || "WebSocket disconnected";
    el.hidden = false;
  }

  function disconnectView(view, opts) {
    opts = opts || {};
    if (!view) return;
    view.intentionalClose = !!opts.intentional;
    if (view.ws) {
      try {
        if (opts.sendClose !== false) {
          view.ws.send(JSON.stringify({ type: "close" }));
        }
        view.ws.close();
      } catch (e) {}
      view.ws = null;
    }
    if (!opts.keepSessionId) view.sessionId = "";
    if (opts.hideFail) hideFail(view.slot);
  }

  function attachSession(view, sessionId) {
    if (!view || !sessionId) return Promise.resolve(false);
    if (view.sessionId === sessionId && view.ws && view.ws.readyState === 1) {
      hideFail(view.slot);
      fitView(view);
      return Promise.resolve(true);
    }
    disconnectView(view, { intentional: true, hideFail: true });
    view.sessionId = sessionId;
    view.intentionalClose = false;
    view.term.reset();
    return jsonFetch(API.sessions + "/" + sessionId + "/ticket", { method: "POST", body: "{}" }).then(
      function (body) {
        if (!body.ok) {
          showFail(view.slot, body.error || "Could not open terminal ticket");
          handleConnectionLost(view, "ticket");
          return false;
        }
        var sock = new WebSocket(wsUrl(sessionId, body.ticket));
        view.ws = sock;
        sock.onmessage = function (ev) {
          var msg;
          try {
            msg = JSON.parse(ev.data);
          } catch (e) {
            return;
          }
          if (msg.type === "out") {
            if (state.renderingPaused) {
              state.hiddenActivity += 1;
              updateBadges();
              return;
            }
            view.term.write(msg.data || "");
            markActivity();
          } else if (msg.type === "ready") {
            hideFail(view.slot);
            fitView(view);
          } else if (msg.type === "exit") {
            view.term.writeln("\r\n\x1b[33m[process exited " + (msg.exit_code == null ? "" : msg.exit_code) + "]\x1b[0m");
            refreshSessions();
          } else if (msg.type === "error") {
            view.term.writeln("\r\n\x1b[31m" + (msg.error || "error") + "\x1b[0m");
          }
        };
        sock.onerror = function () {
          if (view.intentionalClose) return;
          showFail(
            view.slot,
            "WebSocket error — restart the hub with .venv\\Scripts\\python.exe app.py after pip install -r requirements.txt."
          );
          handleConnectionLost(view, "error");
        };
        sock.onclose = function (ev) {
          if (view.ws === sock) view.ws = null;
          if (view.intentionalClose) return;
          var msg =
            ev && ev.code === 1006
              ? "WebSocket closed unexpectedly (often missing flask-sock on the hub Python)."
              : "WebSocket disconnected";
          showFail(view.slot, msg);
          handleConnectionLost(view, "close");
        };
        sock.onopen = function () {
          hideFail(view.slot);
          fitView(view);
          if (state.pendingInsert && state.pendingInsert.sessionId === sessionId) {
            insertText(state.pendingInsert.text, false);
            state.pendingInsert = null;
          }
        };
        return true;
      }
    ).catch(function () {
      showFail(view.slot, "Could not connect terminal WebSocket");
      handleConnectionLost(view, "fetch");
      return false;
    });
  }

  function handleConnectionLost(view, reason) {
    if (!view) return;
    // Second pane must never remain as an empty/broken split.
    if (view.slot === "b" && state.splitEnabled) {
      collapseSplit({ reason: reason || "ws-fail" });
      return;
    }
    showFail(view.slot);
  }

  function sessionById(id) {
    return state.sessions.find(function (s) {
      return s.id === id;
    });
  }

  function sessionIsLive(id) {
    var s = sessionById(id);
    if (!s) return false;
    return !!(s.alive || s.status === "running" || s.has_active_process);
  }

  function sessionExists(id) {
    return !!sessionById(id);
  }

  function markActivity() {
    var paneActive =
      document.querySelector('.wc-pane[data-wc-pane="terminal"]:not([hidden])') &&
      document.querySelector(".app-shell.is-wc-open:not(.is-wc-minimized)");
    if (!paneActive || document.hidden) {
      state.hiddenActivity += 1;
      updateBadges();
    }
  }

  function updateBadges() {
    var badge = $("wc-badge-terminal");
    var statusBadge = $("wc-badge-status");
    var active = state.sessions.filter(function (s) {
      return s.alive || s.status === "running";
    }).length;
    var n = state.hiddenActivity || active;
    if (badge) {
      if (n > 0) {
        badge.hidden = false;
        badge.textContent = String(Math.min(99, n));
      } else {
        badge.hidden = true;
      }
    }
    if (statusBadge) {
      if (active > 0 && (!document.querySelector(".app-shell.is-wc-open") || document.querySelector(".app-shell.is-wc-minimized"))) {
        statusBadge.hidden = false;
        statusBadge.textContent = String(active);
      } else {
        statusBadge.hidden = true;
      }
    }
  }

  function shellLabel(shell) {
    return SHELL_LABELS[shell] || shell || "Terminal";
  }

  function tabLabel(s) {
    if (s.name && String(s.name).indexOf(" — ") >= 0) return s.name;
    return shellLabel(s.shell) + " — " + (s.repository_name || s.repository_id || "repo");
  }

  function updatePaneTitles() {
    var titleA = $("wc-term-title-a");
    var titleB = $("wc-term-title-b");
    var a = sessionById(state.activeId);
    var b = sessionById(state.splitId);
    if (titleA) titleA.textContent = a ? tabLabel(a) : "Terminal";
    if (titleB) titleB.textContent = b ? tabLabel(b) : "Terminal";
  }

  function setActivePane(slot) {
    state.activePane = slot === "b" ? "b" : "a";
    var viewA = $("wc-term-view-a");
    var viewB = $("wc-term-view-b");
    if (viewA) viewA.classList.toggle("is-active", state.activePane === "a");
    if (viewB) viewB.classList.toggle("is-active", state.activePane === "b");
  }

  function applySplitLayout(enabled) {
    var stage = $("wc-term-stage");
    var viewB = $("wc-term-view-b");
    var on = !!enabled && !!state.splitId && state.splitId !== state.activeId;
    state.splitEnabled = on;
    if (!on) {
      state.splitId = "";
      if (viewB) viewB.hidden = true;
      if (stage) stage.setAttribute("data-split", "0");
      setActivePane("a");
    } else {
      if (viewB) viewB.hidden = false;
      if (stage) stage.setAttribute("data-split", "1");
    }
    updatePaneTitles();
    updateActionButtons();
    scheduleFit();
  }

  function collapseSplit(opts) {
    opts = opts || {};
    state.splitCreating = false;
    disconnectView(state.views.b, { intentional: true, hideFail: true });
    state.splitId = "";
    state.splitEnabled = false;
    applySplitLayout(false);
    hideFail("b");
    notifySessionChange(state.activeId);
    scheduleFit();
    return opts;
  }

  function updateEmptyState() {
    var empty = $("wc-term-empty");
    var stage = $("wc-term-stage");
    var has = state.sessions.length > 0 && !!state.activeId;
    if (empty) empty.hidden = has;
    if (stage) stage.hidden = !has;
  }

  function updateActionButtons() {
    var has = !!state.activeId;
    var active = sessionById(state.activeId);
    var running = !!(active && (active.alive || active.status === "running" || active.has_active_process));
    var split = $("wc-term-split");
    var restart = $("wc-term-restart");
    var kill = $("wc-term-kill");
    if (split) {
      split.disabled = !has || state.splitCreating;
      split.textContent = state.splitEnabled ? "Unsplit" : "Split";
      split.title = state.splitCreating
        ? "Creating split…"
        : state.splitEnabled
          ? "Collapse to a single terminal"
          : "Split view (opens a second terminal)";
    }
    if (restart) restart.disabled = !has;
    if (kill) {
      kill.disabled = !has;
      kill.classList.toggle("btn-danger", true);
      kill.title = running
        ? "Kill session and terminate its process tree"
        : "Close terminal session";
    }
    var dot = $("wc-term-repo-dot");
    if (dot) {
      dot.classList.toggle("is-ready", has && running);
      dot.classList.toggle("is-empty", !has);
    }
  }

  function renderSessionTabs() {
    var host = $("wc-term-session-tabs");
    if (!host) return;
    host.innerHTML = "";
    state.sessions.forEach(function (s) {
      var btn = document.createElement("button");
      btn.type = "button";
      btn.className = "wc-term-tab" + (s.id === state.activeId ? " is-active" : "");
      btn.setAttribute("data-session-id", s.id);
      btn.setAttribute("role", "tab");
      btn.setAttribute("aria-selected", s.id === state.activeId ? "true" : "false");
      btn.title =
        tabLabel(s) +
        " · " +
        (s.cwd || "") +
        " · PID " +
        (s.pid || "—") +
        " · " +
        (s.environment || "development");
      var alive = s.alive || s.status === "running";
      btn.innerHTML =
        '<span class="wc-term-tab-label">' +
        escapeHtml(tabLabel(s)) +
        "</span>" +
        (alive ? '<span class="wc-term-dot" title="running" aria-hidden="true"></span>' : "") +
        '<span class="wc-term-tab-close" data-close="' +
        s.id +
        '" title="Close" role="button">×</span>';
      btn.addEventListener("click", function (ev) {
        var closeId = ev.target && ev.target.getAttribute && ev.target.getAttribute("data-close");
        if (closeId) {
          ev.stopPropagation();
          closeSession(closeId);
          return;
        }
        selectSession(s.id);
      });
      btn.addEventListener("dblclick", function () {
        var next = window.prompt("Rename terminal", s.name || tabLabel(s));
        if (next == null) return;
        jsonFetch(API.sessions + "/" + s.id, {
          method: "PATCH",
          body: JSON.stringify({ name: next }),
        }).then(refreshSessions);
      });
      host.appendChild(btn);
    });
    updateEmptyState();
    updateActionButtons();
    updatePaneTitles();
    updateBadges();
  }

  function escapeHtml(s) {
    return String(s || "")
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;");
  }

  function selectSession(id) {
    state.activeId = id || "";
    state.hiddenActivity = 0;
    renderSessionTabs();
    if (!id) {
      disconnectView(state.views.a, { intentional: true, hideFail: true });
      collapseSplit();
      updateEmptyState();
      updateActionButtons();
      notifySessionChange("");
      return;
    }
    // Selecting the split session into the primary pane collapses split (tabs select primary).
    if (state.splitEnabled && id === state.splitId) {
      state.splitId = "";
      applySplitLayout(false);
      disconnectView(state.views.b, { intentional: true, hideFail: true });
    }
    attachSession(state.views.a, id).then(function () {
      if (state.splitEnabled && state.splitId && state.splitId !== id) {
        attachSession(state.views.b, state.splitId);
      }
      scheduleFit();
    });
    notifySessionChange(id);
  }

  function notifySessionChange(id) {
    if (global.WCTerminal && global.WCTerminal.onSessionChange) {
      global.WCTerminal.onSessionChange(id, {
        split: !!(state.splitEnabled && state.splitId && state.splitId !== id),
        splitId: state.splitEnabled ? state.splitId || "" : "",
      });
    }
  }

  function reconcileSplitAfterRefresh() {
    if (state.splitCreating) return;
    if (state.wantSplitRestore && state.pendingSplitId) {
      var splitCandidate = state.pendingSplitId;
      state.wantSplitRestore = false;
      state.pendingSplitId = "";
      if (
        state.activeId &&
        splitCandidate &&
        splitCandidate !== state.activeId &&
        sessionIsLive(state.activeId) &&
        sessionIsLive(splitCandidate)
      ) {
        state.splitId = splitCandidate;
        applySplitLayout(true);
        attachSession(state.views.b, state.splitId).then(function (ok) {
          if (!ok) collapseSplit({ reason: "restore-attach-failed" });
          else {
            notifySessionChange(state.activeId);
            scheduleFit();
          }
        });
        return;
      }
      // Invalid saved split — stay full width and clear persistence.
      state.splitId = "";
      applySplitLayout(false);
      notifySessionChange(state.activeId);
      return;
    }
    if (state.splitEnabled) {
      if (!state.splitId || state.splitId === state.activeId || !sessionExists(state.splitId)) {
        collapseSplit({ reason: "missing-split-session" });
      }
    } else {
      applySplitLayout(false);
    }
  }

  function refreshSessions() {
    return jsonFetch(API.sessions).then(function (body) {
      state.sessions = (body && body.sessions) || [];
      if (!state.activeId && state.sessions.length) state.activeId = state.sessions[0].id;
      if (state.activeId && !state.sessions.some(function (s) { return s.id === state.activeId; })) {
        state.activeId = state.sessions[0] ? state.sessions[0].id : "";
      }
      renderSessionTabs();
      if (state.activeId) {
        attachSession(state.views.a, state.activeId);
        reconcileSplitAfterRefresh();
        if (state.splitEnabled && state.splitId) {
          attachSession(state.views.b, state.splitId);
        }
      } else {
        disconnectView(state.views.a, { intentional: true, hideFail: true });
        collapseSplit();
      }
      updateEmptyState();
      updateActionButtons();
      scheduleFit();
      return state.sessions;
    });
  }

  function fillCatalog(catalog) {
    state.catalog = catalog;
    var repoSel = $("wc-term-repo");
    var shellSel = $("wc-term-shell");
    var profileSel = $("wc-term-profile");
    if (!repoSel || !shellSel) return;
    var prevRepo = repoSel.value;
    var prevShell = shellSel.value;
    repoSel.innerHTML = "";
    (catalog.repositories || []).forEach(function (r) {
      var opt = document.createElement("option");
      opt.value = r.id;
      opt.textContent = r.name || r.id;
      opt.title = (r.name || r.id) + (r.path ? " — " + r.path : "");
      repoSel.appendChild(opt);
    });
    if (!repoSel.options.length) {
      var empty = document.createElement("option");
      empty.value = "";
      empty.textContent = "No local repositories";
      repoSel.appendChild(empty);
    }
    if (prevRepo) repoSel.value = prevRepo;
    shellSel.innerHTML = "";
    var shells = catalog.shells || [{ id: "powershell", label: "PowerShell" }];
    shells.forEach(function (sh) {
      var opt = document.createElement("option");
      opt.value = sh.id;
      opt.textContent = sh.label || sh.id;
      shellSel.appendChild(opt);
    });
    if (prevShell) shellSel.value = prevShell;
    function fillProfiles() {
      if (!profileSel) return;
      profileSel.innerHTML = "";
      var repo = (catalog.repositories || []).find(function (r) {
        return r.id === repoSel.value;
      });
      ((repo && repo.profiles) || []).forEach(function (p) {
        var opt = document.createElement("option");
        opt.value = p.id;
        opt.textContent = p.label || p.id;
        profileSel.appendChild(opt);
      });
      if (!profileSel.options.length) {
        var none = document.createElement("option");
        none.value = "";
        none.textContent = "No approved profiles";
        profileSel.appendChild(none);
      }
    }
    repoSel.onchange = fillProfiles;
    fillProfiles();
    updateActionButtons();
  }

  function newTerminal() {
    var repoSel = $("wc-term-repo");
    var shellSel = $("wc-term-shell");
    if (!repoSel || !repoSel.value) {
      window.alert("Select a connected repository with a local path.");
      return;
    }
    jsonFetch(API.sessions, {
      method: "POST",
      body: JSON.stringify({
        repository_id: repoSel.value,
        shell: shellSel ? shellSel.value : "powershell",
        environment: ($("wc-term-env") && $("wc-term-env").value) || "development",
      }),
    }).then(function (body) {
      if (!body.ok) {
        window.alert(body.error || "Failed to create terminal");
        return;
      }
      state.activeId = body.session.id;
      refreshSessions().then(function () {
        selectSession(state.activeId);
      });
    });
  }

  function closeSession(id) {
    var s = sessionById(id);
    if (s && (s.alive || s.status === "running" || s.has_active_process)) {
      if (!window.confirm("Close terminal and terminate its process tree?")) return;
    }
    jsonFetch(API.sessions + "/" + id + "?confirm=1", {
      method: "DELETE",
      body: JSON.stringify({ confirm: true }),
    }).then(function () {
      if (state.splitId === id) {
        collapseSplit({ reason: "session-closed" });
      }
      if (state.activeId === id) {
        if (state.splitEnabled && state.splitId) {
          // Promote remaining split session to full width without killing it.
          var keep = state.splitId;
          disconnectView(state.views.b, { intentional: true, hideFail: true });
          state.activeId = keep;
          state.splitId = "";
          applySplitLayout(false);
        } else {
          state.activeId = "";
        }
      }
      refreshSessions();
    });
  }

  function restartActive() {
    if (!state.activeId) return;
    var s = sessionById(state.activeId);
    if (s && (s.alive || s.status === "running" || s.has_active_process)) {
      if (!window.confirm("Restart terminal and terminate the current process tree?")) return;
    }
    jsonFetch(API.sessions + "/" + state.activeId + "/restart", {
      method: "POST",
      body: JSON.stringify({ confirm: true }),
    }).then(function (body) {
      if (body.ok && body.session) state.activeId = body.session.id;
      refreshSessions();
    });
  }

  function killActive() {
    if (!state.activeId) return;
    var s = sessionById(state.activeId);
    var running = !!(s && (s.alive || s.status === "running" || s.has_active_process));
    if (running) {
      if (!window.confirm("Kill this terminal and terminate its process tree?\n\nThis cannot be undone.")) {
        return;
      }
    }
    var id = state.activeId;
    jsonFetch(API.sessions + "/" + id + "?confirm=1", {
      method: "DELETE",
      body: JSON.stringify({ confirm: true }),
    }).then(function () {
      if (state.splitId === id) collapseSplit({ reason: "killed-split" });
      if (state.activeId === id) {
        if (state.splitEnabled && state.splitId) {
          var keep = state.splitId;
          disconnectView(state.views.b, { intentional: true, hideFail: true });
          state.activeId = keep;
          state.splitId = "";
          applySplitLayout(false);
        } else {
          state.activeId = "";
        }
      }
      refreshSessions();
    });
  }

  function beginSplit() {
    if (!state.activeId || state.splitCreating || state.splitEnabled) return;
    state.splitCreating = true;
    updateActionButtons();
    jsonFetch(API.sessions + "/" + state.activeId + "/duplicate", { method: "POST", body: "{}" })
      .then(function (body) {
        if (!body.ok || !body.session || !body.session.id) {
          state.splitCreating = false;
          updateActionButtons();
          window.alert((body && body.error) || "Failed to create split terminal");
          applySplitLayout(false);
          scheduleFit();
          return;
        }
        state.splitId = body.session.id;
        return refreshSessions().then(function () {
          return attachSession(state.views.b, state.splitId).then(function (ok) {
            state.splitCreating = false;
            if (!ok) {
              collapseSplit({ reason: "attach-failed" });
              return;
            }
            applySplitLayout(true);
            setActivePane("b");
            notifySessionChange(state.activeId);
            scheduleFit();
          });
        });
      })
      .catch(function () {
        state.splitCreating = false;
        collapseSplit({ reason: "duplicate-failed" });
        window.alert("Failed to create split terminal");
      });
  }

  function toggleSplit() {
    if (!state.activeId || state.splitCreating) return;
    if (state.splitEnabled) {
      // Unsplit: hide second pane without killing its session.
      disconnectView(state.views.b, { intentional: true, hideFail: true });
      state.splitId = "";
      applySplitLayout(false);
      notifySessionChange(state.activeId);
      scheduleFit();
      return;
    }
    beginSplit();
  }

  /**
   * Close a split pane layout without killing the other terminal session.
   */
  function closePane(slot) {
    hideFail(slot);
    if (!state.splitEnabled) {
      // Single pane: dismiss failure UI only; session stays in tabs.
      if (slot === "a") {
        disconnectView(state.views.a, { intentional: true, keepSessionId: true, hideFail: true });
        showFail("a", "Terminal pane closed — use Reconnect or pick a session tab.");
      }
      return;
    }
    if (slot === "b") {
      disconnectView(state.views.b, { intentional: true, hideFail: true });
      state.splitId = "";
      applySplitLayout(false);
      notifySessionChange(state.activeId);
      scheduleFit();
      return;
    }
    // Close primary pane while split: promote B to full width.
    var keep = state.splitId;
    disconnectView(state.views.a, { intentional: true, hideFail: true });
    disconnectView(state.views.b, { intentional: true, hideFail: true });
    state.activeId = keep || state.activeId;
    state.splitId = "";
    applySplitLayout(false);
    if (state.activeId) {
      attachSession(state.views.a, state.activeId);
      renderSessionTabs();
      notifySessionChange(state.activeId);
    }
    scheduleFit();
  }

  function reconnectPane(slot) {
    var view = slot === "b" ? state.views.b : state.views.a;
    var sid = view && view.sessionId;
    if (!sid) {
      if (slot === "a") sid = state.activeId;
      if (slot === "b") sid = state.splitId;
    }
    if (!view || !sid) return;
    hideFail(slot);
    attachSession(view, sid).then(function (ok) {
      if (!ok && slot === "b") collapseSplit({ reason: "reconnect-failed" });
      scheduleFit();
    });
  }

  /**
   * Insert text into the active terminal without executing (no trailing CR/LF).
   */
  function insertText(text, notifyServer) {
    var cleaned = String(text || "").replace(/\r?\n$/, "");
    var view = state.activePane === "b" && state.splitEnabled ? state.views.b : state.views.a;
    var sid = view && view.sessionId ? view.sessionId : state.activeId;
    if (!view || !view.ws || view.ws.readyState !== 1) {
      state.pendingInsert = { sessionId: sid, text: cleaned };
      if (!sid) {
        window.alert("Open a terminal session first, then use Insert into Terminal.");
      }
      return false;
    }
    while (cleaned.endsWith("\n") || cleaned.endsWith("\r")) {
      cleaned = cleaned.slice(0, -1);
    }
    view.ws.send(JSON.stringify({ type: "in", data: cleaned }));
    if (notifyServer !== false) {
      jsonFetch(API.insert, {
        method: "POST",
        body: JSON.stringify({ session_id: sid, executed: false }),
      }).catch(function () {});
    }
    return true;
  }

  function setRenderingPaused(paused) {
    state.renderingPaused = !!paused;
    if (!paused) {
      state.hiddenActivity = 0;
      updateBadges();
      scheduleFit();
    } else {
      updateBadges();
    }
  }

  function wireButtons() {
    var btnNew = $("wc-term-new");
    var btnAdd = $("wc-term-add");
    var btnEmpty = $("wc-term-empty-new");
    var btnSplit = $("wc-term-split");
    var btnRestart = $("wc-term-restart");
    var btnKill = $("wc-term-kill");
    if (btnNew) btnNew.onclick = newTerminal;
    if (btnAdd) btnAdd.onclick = newTerminal;
    if (btnEmpty) btnEmpty.onclick = newTerminal;
    if (btnSplit) btnSplit.onclick = toggleSplit;
    if (btnRestart) btnRestart.onclick = restartActive;
    if (btnKill) btnKill.onclick = killActive;

    document.querySelectorAll("[data-close-pane]").forEach(function (btn) {
      btn.addEventListener("click", function (ev) {
        ev.preventDefault();
        ev.stopPropagation();
        closePane(btn.getAttribute("data-close-pane"));
      });
    });
    document.querySelectorAll("[data-ws-reconnect]").forEach(function (btn) {
      btn.addEventListener("click", function (ev) {
        ev.preventDefault();
        reconnectPane(btn.getAttribute("data-ws-reconnect"));
      });
    });
    document.querySelectorAll("[data-ws-close-pane]").forEach(function (btn) {
      btn.addEventListener("click", function (ev) {
        ev.preventDefault();
        closePane(btn.getAttribute("data-ws-close-pane"));
      });
    });
  }

  function init(opts) {
    opts = opts || {};
    if (opts.activeId) state.activeId = opts.activeId;
    // Never enable split chrome from a bare boolean — require a second session id,
    // then validate both are live after refreshSessions.
    state.pendingSplitId = opts.splitId || opts.splitSessionId || "";
    state.wantSplitRestore = !!(opts.split && state.pendingSplitId && state.pendingSplitId !== state.activeId);
    state.splitEnabled = false;
    state.splitId = "";
    return ensureXterm().then(function () {
      var a = $("wc-xterm-a");
      var b = $("wc-xterm-b");
      if (a && !state.views.a) state.views.a = createView("a", a);
      if (b && !state.views.b) state.views.b = createView("b", b);
      if (!state.initialized) {
        wireButtons();
        window.addEventListener("resize", scheduleFit);
        var stage = $("wc-term-stage");
        if (stage && typeof ResizeObserver !== "undefined") {
          state.resizeObserver = new ResizeObserver(function () {
            scheduleFit();
          });
          state.resizeObserver.observe(stage);
        }
        state.initialized = true;
      }
      applySplitLayout(false);
      updateEmptyState();
      updateActionButtons();
      return refreshSessions();
    });
  }

  global.WCTerminal = {
    init: init,
    fillCatalog: fillCatalog,
    refreshSessions: refreshSessions,
    selectSession: selectSession,
    newTerminal: newTerminal,
    insertText: insertText,
    setRenderingPaused: setRenderingPaused,
    toggleSplit: toggleSplit,
    beginSplit: beginSplit,
    collapseSplit: collapseSplit,
    closePane: closePane,
    scheduleFit: scheduleFit,
    getActiveId: function () {
      return state.activeId;
    },
    getSplitId: function () {
      return state.splitEnabled ? state.splitId : "";
    },
    isSplitEnabled: function () {
      return !!(state.splitEnabled && state.splitId);
    },
    isSplitCreating: function () {
      return !!state.splitCreating;
    },
    getSessions: function () {
      return state.sessions.slice();
    },
    /** Test/helper: snapshot layout flags without touching the DOM heavily. */
    getLayoutState: function () {
      var stage = $("wc-term-stage");
      var viewB = $("wc-term-view-b");
      return {
        splitEnabled: !!state.splitEnabled,
        splitId: state.splitId || "",
        activeId: state.activeId || "",
        splitCreating: !!state.splitCreating,
        dataSplit: stage ? stage.getAttribute("data-split") : null,
        viewBHidden: viewB ? !!viewB.hidden : true,
      };
    },
    fit: function () {
      scheduleFit();
    },
    onSessionChange: null,
  };
})(window);
