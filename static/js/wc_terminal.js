/**
 * Interactive repository terminal (xterm.js ↔ PTY WebSocket).
 * AI cannot execute — Insert into Terminal fills text without Enter.
 */
(function (global) {
  "use strict";

  var API = {
    sessions: "/api/workspace-console/terminal/sessions",
    insert: "/api/workspace-console/terminal/insert",
  };

  var state = {
    sessions: [],
    activeId: "",
    splitId: "",
    splitEnabled: false,
    catalog: null,
    views: { a: null, b: null },
    pendingInsert: null,
    hiddenActivity: 0,
    renderingPaused: false,
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
      var link = document.createElement("link");
      link.rel = "stylesheet";
      link.href = "/static/vendor/xterm/xterm.css";
      document.head.appendChild(link);
      var s1 = document.createElement("script");
      s1.src = "/static/vendor/xterm/xterm.js";
      s1.onload = function () {
        var s2 = document.createElement("script");
        s2.src = "/static/vendor/xterm/addon-fit.js";
        s2.onload = function () {
          resolve();
        };
        s2.onerror = reject;
        document.head.appendChild(s2);
      };
      s1.onerror = reject;
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
    };
    term.onData(function (data) {
      if (!view.ws || view.ws.readyState !== 1) return;
      view.ws.send(JSON.stringify({ type: "in", data: data }));
    });
    term.attachCustomKeyEventHandler(function () {
      return true;
    });
    // Multiline paste warning
    term.onData; // keep
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
    return view;
  }

  function fitView(view) {
    if (!view || !view.fit || state.renderingPaused) return;
    try {
      view.fit.fit();
      if (view.ws && view.ws.readyState === 1) {
        view.ws.send(
          JSON.stringify({ type: "resize", cols: view.term.cols, rows: view.term.rows })
        );
      }
    } catch (e) {}
  }

  function disconnectView(view) {
    if (!view) return;
    if (view.ws) {
      try {
        view.ws.send(JSON.stringify({ type: "close" }));
        view.ws.close();
      } catch (e) {}
      view.ws = null;
    }
    view.sessionId = "";
  }

  function attachSession(view, sessionId) {
    if (!view || !sessionId) return Promise.resolve();
    if (view.sessionId === sessionId && view.ws && view.ws.readyState === 1) {
      fitView(view);
      return Promise.resolve();
    }
    disconnectView(view);
    view.sessionId = sessionId;
    view.term.reset();
    return jsonFetch(API.sessions + "/" + sessionId + "/ticket", { method: "POST", body: "{}" }).then(
      function (body) {
        if (!body.ok) {
          view.term.writeln("\r\n\x1b[31m" + (body.error || "Ticket failed") + "\x1b[0m");
          return;
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
            fitView(view);
          } else if (msg.type === "exit") {
            view.term.writeln("\r\n\x1b[33m[process exited " + (msg.exit_code == null ? "" : msg.exit_code) + "]\x1b[0m");
          } else if (msg.type === "error") {
            view.term.writeln("\r\n\x1b[31m" + (msg.error || "error") + "\x1b[0m");
          }
        };
        sock.onclose = function () {
          if (view.ws === sock) view.ws = null;
        };
        sock.onopen = function () {
          fitView(view);
          if (state.pendingInsert && state.pendingInsert.sessionId === sessionId) {
            insertText(state.pendingInsert.text, false);
            state.pendingInsert = null;
          }
        };
      }
    );
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

  function renderSessionTabs() {
    var host = $("wc-term-session-tabs");
    if (!host) return;
    host.innerHTML = "";
    state.sessions.forEach(function (s) {
      var btn = document.createElement("button");
      btn.type = "button";
      btn.className = "wc-term-tab" + (s.id === state.activeId ? " is-active" : "");
      btn.setAttribute("data-session-id", s.id);
      btn.title = (s.repository_name || s.repository_id) + " · " + s.shell + " · PID " + (s.pid || "—");
      btn.innerHTML =
        '<span class="wc-term-tab-label">' +
        escapeHtml(s.name || s.id) +
        "</span>" +
        (s.alive ? '<span class="wc-term-dot" title="running"></span>' : "") +
        '<span class="wc-term-tab-close" data-close="' +
        s.id +
        '" title="Close">×</span>';
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
        var next = window.prompt("Rename terminal", s.name || "");
        if (next == null) return;
        jsonFetch(API.sessions + "/" + s.id, {
          method: "PATCH",
          body: JSON.stringify({ name: next }),
        }).then(refreshSessions);
      });
      host.appendChild(btn);
    });
    updateMeta();
    updateBadges();
  }

  function escapeHtml(s) {
    return String(s || "")
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;");
  }

  function updateMeta() {
    var el = $("wc-term-meta");
    if (!el) return;
    var s = state.sessions.find(function (x) {
      return x.id === state.activeId;
    });
    if (!s) {
      el.textContent = "No session";
      return;
    }
    el.textContent =
      (s.repository_name || s.repository_id) +
      " · " +
      s.cwd +
      " · " +
      s.shell +
      " · PID " +
      (s.pid || "—") +
      " · " +
      (s.environment || "development") +
      " · " +
      s.status;
  }

  function selectSession(id) {
    state.activeId = id;
    state.hiddenActivity = 0;
    renderSessionTabs();
    var view = state.views.a;
    attachSession(view, id).then(function () {
      if (state.splitEnabled && state.splitId) {
        attachSession(state.views.b, state.splitId);
      }
    });
    if (global.WCTerminal && global.WCTerminal.onSessionChange) {
      global.WCTerminal.onSessionChange(id);
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
      if (state.activeId) attachSession(state.views.a, state.activeId);
      return state.sessions;
    });
  }

  function fillCatalog(catalog) {
    state.catalog = catalog;
    var repoSel = $("wc-term-repo");
    var shellSel = $("wc-term-shell");
    var profileSel = $("wc-term-profile");
    if (!repoSel || !shellSel) return;
    repoSel.innerHTML = "";
    (catalog.repositories || []).forEach(function (r) {
      var opt = document.createElement("option");
      opt.value = r.id;
      opt.textContent = r.name + " (" + r.id + ")";
      repoSel.appendChild(opt);
    });
    shellSel.innerHTML = "";
    var shells = catalog.shells || [{ id: "powershell", label: "PowerShell" }];
    shells.forEach(function (sh) {
      var opt = document.createElement("option");
      opt.value = sh.id;
      opt.textContent = sh.label || sh.id;
      shellSel.appendChild(opt);
    });
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
    }
    repoSel.onchange = fillProfiles;
    fillProfiles();
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
      refreshSessions();
    });
  }

  function closeSession(id) {
    var s = state.sessions.find(function (x) {
      return x.id === id;
    });
    if (s && (s.alive || s.status === "running")) {
      if (!window.confirm("Close terminal and terminate its process tree?")) return;
    }
    jsonFetch(API.sessions + "/" + id + "?confirm=1", {
      method: "DELETE",
      body: JSON.stringify({ confirm: true }),
    }).then(function () {
      if (state.activeId === id) state.activeId = "";
      if (state.splitId === id) state.splitId = "";
      refreshSessions();
    });
  }

  function restartActive() {
    if (!state.activeId) return;
    if (!window.confirm("Restart terminal and terminate the current process tree?")) return;
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
    closeSession(state.activeId);
  }

  function toggleSplit() {
    state.splitEnabled = !state.splitEnabled;
    var stage = $("wc-term-stage");
    var viewB = $("wc-term-view-b");
    if (stage) stage.setAttribute("data-split", state.splitEnabled ? "1" : "0");
    if (viewB) viewB.hidden = !state.splitEnabled;
    if (state.splitEnabled) {
      if (!state.splitId) {
        // Duplicate active into split
        if (!state.activeId) return;
        jsonFetch(API.sessions + "/" + state.activeId + "/duplicate", { method: "POST", body: "{}" }).then(
          function (body) {
            if (body.ok) {
              state.splitId = body.session.id;
              refreshSessions().then(function () {
                attachSession(state.views.b, state.splitId);
                fitView(state.views.a);
                fitView(state.views.b);
              });
            }
          }
        );
      } else {
        attachSession(state.views.b, state.splitId);
      }
    } else {
      disconnectView(state.views.b);
    }
    if (global.WCTerminal && global.WCTerminal.onSessionChange) {
      global.WCTerminal.onSessionChange(state.activeId, { split: state.splitEnabled });
    }
  }

  /**
   * Insert text into the active terminal without executing (no trailing CR/LF).
   */
  function insertText(text, notifyServer) {
    var cleaned = String(text || "").replace(/\r?\n$/, "");
    var view = state.views.a;
    if (!view || !view.ws || view.ws.readyState !== 1) {
      state.pendingInsert = { sessionId: state.activeId, text: cleaned };
      if (!state.activeId) {
        window.alert("Open a terminal session first, then use Insert into Terminal.");
      }
      return false;
    }
    // Strip final newlines so Enter is never auto-sent.
    while (cleaned.endsWith("\n") || cleaned.endsWith("\r")) {
      cleaned = cleaned.slice(0, -1);
    }
    view.ws.send(JSON.stringify({ type: "in", data: cleaned }));
    if (notifyServer !== false) {
      jsonFetch(API.insert, {
        method: "POST",
        body: JSON.stringify({ session_id: state.activeId, executed: false }),
      }).catch(function () {});
    }
    return true;
  }

  function setRenderingPaused(paused) {
    state.renderingPaused = !!paused;
    if (!paused) {
      state.hiddenActivity = 0;
      updateBadges();
      fitView(state.views.a);
      if (state.splitEnabled) fitView(state.views.b);
    } else {
      updateBadges();
    }
  }

  function init(opts) {
    opts = opts || {};
    if (opts.activeId) state.activeId = opts.activeId;
    if (opts.split) state.splitEnabled = !!opts.split;
    return ensureXterm().then(function () {
      var a = $("wc-xterm-a");
      var b = $("wc-xterm-b");
      if (a && !state.views.a) state.views.a = createView("a", a);
      if (b && !state.views.b) state.views.b = createView("b", b);
      var btnNew = $("wc-term-new");
      var btnSplit = $("wc-term-split");
      var btnRestart = $("wc-term-restart");
      var btnKill = $("wc-term-kill");
      if (btnNew) btnNew.onclick = newTerminal;
      if (btnSplit) btnSplit.onclick = toggleSplit;
      if (btnRestart) btnRestart.onclick = restartActive;
      if (btnKill) btnKill.onclick = killActive;
      window.addEventListener("resize", function () {
        fitView(state.views.a);
        if (state.splitEnabled) fitView(state.views.b);
      });
      var stage = $("wc-term-stage");
      var viewB = $("wc-term-view-b");
      if (stage) stage.setAttribute("data-split", state.splitEnabled ? "1" : "0");
      if (viewB) viewB.hidden = !state.splitEnabled;
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
    getActiveId: function () {
      return state.activeId;
    },
    getSessions: function () {
      return state.sessions.slice();
    },
    fit: function () {
      fitView(state.views.a);
      if (state.splitEnabled) fitView(state.views.b);
    },
    onSessionChange: null,
  };
})(window);
