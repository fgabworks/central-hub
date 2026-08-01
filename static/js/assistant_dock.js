/**
 * Persistent Aira/Okarun assistant dock (VS Code / Cursor-style right rail).
 * Lazy-loads providers only after open; never blocks navigation.
 */
(function () {
  "use strict";

  var MOBILE_MQ = "(max-width: 960px)";

  function $(id) {
    return document.getElementById(id);
  }

  function parseBootstrap(host) {
    try {
      return JSON.parse(host.getAttribute("data-ad-bootstrap") || "{}");
    } catch (_err) {
      return {};
    }
  }

  function createDockController(host) {
    if (!host) return null;
    var boot = parseBootstrap(host);
    var prefs = Object.assign(
      { open: false, pinned: true, minimized: false, width: 400, min_width: 300, max_width: 560 },
      boot.prefs || {}
    );
    var profile = boot.profile || {};
    var apiBase = boot.api_base || "/api/assistants/okarun";
    var prefsUrl = boot.prefs_url || "/api/assistant-dock/prefs";
    var agentsLoaded = false;
    var agentsLoading = false;
    var selectedAgent = "";
    var selectedModel = "";
    var shell = document.querySelector(".app-shell");
    var panel = $("ad-panel");
    var toggleBtn = $("ar-assistant");
    var topbarBtn = $("ad-topbar-toggle");
    var backdrop = $("ad-backdrop");
    var promptEl = $("ad-prompt");
    var agentSel = $("ad-agent");
    var modelSel = $("ad-model");
    var messages = $("ad-messages");
    var output = $("ad-output");
    var contextBody = $("ad-context-body");
    var contextDrawer = $("ad-context-drawer");
    var contextBtn = $("ad-context-btn");
    var saveTimer = null;
    var pollTimer = null;
    var activeRunId = null;
    var lastPrompt = "";
    var cancelBtn = $("ad-cancel");
    var retryBtn = $("ad-retry");
    var sendBtn = $("ad-send");
    var emptyEl = $("ad-empty");
    var moreBtn = $("ad-more");
    var menuPop = $("ad-menu-pop");

    function isMobile() {
      return window.matchMedia(MOBILE_MQ).matches;
    }

    function expanded() {
      return !!prefs.open && !prefs.minimized;
    }

    function applyChrome() {
      if (!shell) return;
      var visible = !!prefs.open;
      shell.classList.toggle("is-ad-open", visible);
      shell.classList.toggle("is-ad-minimized", visible && !!prefs.minimized);
      shell.classList.toggle("is-ad-mobile", isMobile());
      shell.style.setProperty(
        "--ad-width",
        Math.max(prefs.min_width, Math.min(prefs.max_width, prefs.width)) + "px"
      );
      if (panel) panel.hidden = !expanded();
      function syncToggle(btn) {
        if (!btn) return;
        btn.setAttribute("aria-expanded", expanded() ? "true" : "false");
        btn.title =
          (expanded() ? "Hide " : "Open ") + (profile.name || "Assistant");
        btn.classList.toggle("is-active", expanded());
      }
      syncToggle(toggleBtn);
      syncToggle(topbarBtn);
      if (backdrop) {
        backdrop.hidden = !(isMobile() && expanded());
      }
      var pin = $("ad-pin");
      if (pin) {
        pin.setAttribute("aria-pressed", prefs.pinned ? "true" : "false");
        pin.classList.toggle("is-active", !!prefs.pinned);
      }
    }

    function persistPrefs(immediate) {
      var payload = {
        open: !!prefs.open,
        pinned: !!prefs.pinned,
        minimized: !!prefs.minimized,
        width: prefs.width,
      };
      function send() {
        fetch(prefsUrl, {
          method: "PUT",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(payload),
          credentials: "same-origin",
        }).catch(function () {});
      }
      if (immediate) {
        send();
        return;
      }
      if (saveTimer) clearTimeout(saveTimer);
      saveTimer = setTimeout(send, 250);
    }

    function setOpen(open) {
      prefs.open = !!open;
      if (prefs.open) prefs.minimized = false;
      else prefs.minimized = false;
      applyChrome();
      persistPrefs(true);
      if (prefs.open) ensureAgents();
    }

    function toggle() {
      if (prefs.minimized) {
        setMinimized(false);
        return;
      }
      setOpen(!prefs.open);
    }

    function setMinimized(min) {
      prefs.minimized = !!min;
      if (prefs.minimized) prefs.open = true;
      applyChrome();
      persistPrefs(true);
    }

    function setContextOpen(open) {
      if (!contextDrawer || !contextBtn) return;
      contextDrawer.hidden = !open;
      contextBtn.setAttribute("aria-expanded", open ? "true" : "false");
    }

    function switchTab(name) {
      document.querySelectorAll(".ad-tab").forEach(function (tab) {
        var active = tab.getAttribute("data-ad-tab") === name;
        tab.classList.toggle("is-active", active);
        tab.setAttribute("aria-selected", active ? "true" : "false");
      });
      document.querySelectorAll(".ad-pane").forEach(function (pane) {
        var active = pane.id === "ad-pane-" + name;
        pane.classList.toggle("is-active", active);
        pane.hidden = !active;
      });
    }

    function setMenuOpen(open) {
      if (!menuPop || !moreBtn) return;
      menuPop.hidden = !open;
      moreBtn.setAttribute("aria-expanded", open ? "true" : "false");
    }

    function clearEmptyState() {
      if (!messages) return;
      if (emptyEl && emptyEl.parentNode === messages) {
        emptyEl.remove();
      }
      messages.removeAttribute("data-ad-empty");
    }

    function setRunControls(state) {
      /* state: idle | running | failed */
      var running = state === "running";
      var failed = state === "failed";
      if (cancelBtn) {
        cancelBtn.hidden = !running;
        cancelBtn.disabled = !running;
      }
      if (retryBtn) {
        retryBtn.hidden = !failed;
        retryBtn.disabled = !failed;
      }
      if (sendBtn) {
        sendBtn.disabled = running;
        sendBtn.hidden = false;
      }
    }

    function appendMessage(role, html) {
      if (!messages) return;
      clearEmptyState();
      var div = document.createElement("div");
      div.className = "ad-msg ad-msg-" + role;
      div.innerHTML = html;
      messages.appendChild(div);
      messages.scrollTop = messages.scrollHeight;
    }

    function escapeHtml(text) {
      return String(text || "")
        .replace(/&/g, "&amp;")
        .replace(/</g, "&lt;")
        .replace(/>/g, "&gt;")
        .replace(/"/g, "&quot;");
    }

    function ensureAgents() {
      if (agentsLoaded || agentsLoading || !agentSel) return;
      agentsLoading = true;
      var url = apiBase + "/agents?probe=1";
      var req =
        window.HubPerf && window.HubPerf.dedupeFetch
          ? window.HubPerf.dedupeFetch(url, { credentials: "same-origin" })
          : fetch(url, { credentials: "same-origin" });
      req
        .then(function (r) {
          return r.json();
        })
        .then(function (data) {
          var agents = data.agents || [];
          agentSel.innerHTML = "";
          if (!agents.length) {
            agentSel.innerHTML = '<option value="">No providers configured</option>';
            return;
          }
          agents.forEach(function (agent, idx) {
            var opt = document.createElement("option");
            opt.value = agent.id;
            opt.textContent = agent.label || agent.id;
            if (idx === 0) selectedAgent = agent.id;
            agentSel.appendChild(opt);
          });
          agentSel.value = selectedAgent;
          agentsLoaded = true;
          loadModels(selectedAgent);
        })
        .catch(function () {
          agentSel.innerHTML = '<option value="">Providers unavailable</option>';
        })
        .finally(function () {
          agentsLoading = false;
        });
    }

    function loadModels(agentId) {
      if (!modelSel || !agentId) return;
      fetch(apiBase + "/agents/" + encodeURIComponent(agentId) + "/models", {
        credentials: "same-origin",
      })
        .then(function (r) {
          return r.json();
        })
        .then(function (data) {
          var models = data.models || data.items || [];
          modelSel.innerHTML = "";
          if (!models.length) {
            modelSel.hidden = true;
            selectedModel = "";
            return;
          }
          models.forEach(function (model, idx) {
            var id = typeof model === "string" ? model : model.id || model.name;
            var label =
              typeof model === "string" ? model : model.label || model.name || id;
            var opt = document.createElement("option");
            opt.value = id;
            opt.textContent = label;
            if (idx === 0) selectedModel = id;
            modelSel.appendChild(opt);
          });
          modelSel.value = selectedModel;
          modelSel.hidden = false;
        })
        .catch(function () {
          modelSel.hidden = true;
        });
    }

    function previewContext(prompt) {
      return fetch(apiBase + "/context/preview", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        credentials: "same-origin",
        body: JSON.stringify({
          mode: profile.default_mode || "ask",
          prompt: prompt,
          agent_id: selectedAgent,
          model: selectedModel,
          tools: profile.default_tools || [],
          repository_ids: [],
        }),
      })
        .then(function (r) {
          return r.json();
        })
        .then(function (data) {
          if (contextBody) {
            var preview = data.preview || data;
            contextBody.textContent = JSON.stringify(
              {
                included_sources: preview.included_sources || [],
                excluded_sources: preview.excluded_sources || [],
                excluded_secrets: preview.excluded_secrets || [],
                roots: (preview.roots || []).slice(0, 8),
              },
              null,
              2
            );
          }
          return data;
        })
        .catch(function () {
          return null;
        });
    }

    function extractCodeBlocks(text) {
      var blocks = [];
      var re = /```(?:[a-zA-Z0-9_-]+)?\n([\s\S]*?)```/g;
      var m;
      while ((m = re.exec(String(text || ""))) !== null) {
        if (m[1] && m[1].trim()) blocks.push(m[1].replace(/\s+$/, ""));
      }
      return blocks;
    }

    function renderRun(run) {
      var answer = run.answer || run.error || "No answer yet.";
      var sourceBits = ((run.context || {}).included_sources || []).join(", ");
      var codeBlocks = extractCodeBlocks(answer);
      var insertBtns = codeBlocks
        .map(function (code, idx) {
          return (
            '<button type="button" class="btn btn-sm ad-insert-term" data-ad-insert-idx="' +
            idx +
            '" title="Fill terminal prompt only — does not execute">Insert into Terminal</button>'
          );
        })
        .join(" ");
      appendMessage(
        "assistant",
        '<div class="ad-thought">Thought for run ' +
          escapeHtml(run.id || "") +
          "</div><div>" +
          escapeHtml(answer).replace(/\n/g, "<br>") +
          "</div>" +
          (insertBtns
            ? '<div class="ad-insert-row muted">Suggestion only — you must press Enter in the terminal to run. ' +
              insertBtns +
              "</div>"
            : "") +
          (sourceBits
            ? '<div class="ad-source">Source: ' +
              escapeHtml(sourceBits) +
              " (read-only)</div>"
            : "")
      );
      if (messages) {
        messages.querySelectorAll(".ad-insert-term").forEach(function (btn) {
          if (btn._bound) return;
          btn._bound = true;
          btn.addEventListener("click", function () {
            var idx = Number(btn.getAttribute("data-ad-insert-idx") || 0);
            var text = codeBlocks[idx] || "";
            if (!text) return;
            if (window.WCTerminal && window.WCTerminal.insertText) {
              // Never auto-execute: insertText strips trailing newlines.
              window.WCTerminal.insertText(text, true);
            } else {
              window.alert("Open Workspace Console → Terminal first.");
            }
          });
        });
      }
      if (output) {
        output.innerHTML =
          '<pre class="ad-context-body">' +
          escapeHtml(run.logs || "") +
          "</pre>" +
          (run.tool_activity && run.tool_activity.length
            ? '<p class="muted">Tool activity: ' + run.tool_activity.length + "</p>"
            : "");
      }
      activeRunId = run.id || activeRunId;
      if (run.status === "failed" || run.status === "cancelled") {
        setRunControls("failed");
      } else {
        setRunControls("idle");
      }
    }

    function pollRun(runId) {
      if (pollTimer) clearInterval(pollTimer);
      activeRunId = runId;
      setRunControls("running");
      function tick() {
        if (document.visibilityState === "hidden") return;
        if (!expanded()) return;
        fetch(apiBase + "/runs/" + encodeURIComponent(runId), {
          credentials: "same-origin",
        })
          .then(function (r) {
            return r.json();
          })
          .then(function (run) {
            if (!run || (run.error && !run.id)) return;
            if (
              run.status === "succeeded" ||
              run.status === "failed" ||
              run.status === "cancelled"
            ) {
              clearInterval(pollTimer);
              pollTimer = null;
              renderRun(run);
            }
          })
          .catch(function () {});
      }
      pollTimer = setInterval(tick, 1200);
      tick();
    }

    function cancelActiveRun() {
      if (!activeRunId) return;
      fetch(apiBase + "/runs/" + encodeURIComponent(activeRunId) + "/cancel", {
        method: "POST",
        credentials: "same-origin",
      })
        .then(function (r) {
          return r.json();
        })
        .then(function (run) {
          if (pollTimer) {
            clearInterval(pollTimer);
            pollTimer = null;
          }
          if (run && run.id) renderRun(run);
          else setRunControls("failed");
        })
        .catch(function () {
          setRunControls("failed");
        });
    }

    function retryActiveRun() {
      if (!activeRunId) {
        if (lastPrompt) sendPrompt(lastPrompt);
        return;
      }
      setRunControls("running");
      fetch(apiBase + "/runs/" + encodeURIComponent(activeRunId) + "/retry", {
        method: "POST",
        credentials: "same-origin",
      })
        .then(function (r) {
          return r.json().then(function (body) {
            return { ok: r.ok, body: body };
          });
        })
        .then(function (res) {
          var run = (res.body && (res.body.run || res.body)) || {};
          if (!res.ok) {
            appendMessage(
              "assistant",
              escapeHtml((res.body && res.body.error) || "Retry failed.")
            );
            setRunControls("failed");
            return;
          }
          activeRunId = run.id || activeRunId;
          if (run.status === "succeeded" || run.answer) {
            renderRun(run);
          } else if (run.id) {
            appendMessage("assistant", "Retrying…");
            pollRun(run.id);
          } else {
            setRunControls("idle");
          }
        })
        .catch(function () {
          appendMessage("assistant", "Retry failed.");
          setRunControls("failed");
        });
    }

    function sendPrompt(text) {
      var prompt = String(text || "").trim();
      if (!prompt) return;
      lastPrompt = prompt;
      setRunControls("running");
      appendMessage("user", escapeHtml(prompt));
      if (promptEl) promptEl.value = "";
      if (!selectedAgent) {
        appendMessage(
          "assistant",
          "Select a provider first (lazy-loaded when the panel opens)."
        );
        ensureAgents();
        setRunControls("idle");
        return;
      }
      previewContext(prompt).then(function () {
        setContextOpen(false);
      });
      fetch(apiBase + "/runs", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        credentials: "same-origin",
        body: JSON.stringify({
          mode: profile.default_mode || "ask",
          prompt: prompt,
          agent_id: selectedAgent,
          model: selectedModel,
          tools: profile.default_tools || [],
          repository_ids: [],
        }),
      })
        .then(function (r) {
          return r.json().then(function (body) {
            return { ok: r.ok, body: body };
          });
        })
        .then(function (res) {
          if (!res.ok) {
            appendMessage(
              "assistant",
              escapeHtml(
                (res.body && (res.body.error || res.body.message)) || "Run failed."
              )
            );
            setRunControls("failed");
            return;
          }
          var run = res.body.run || res.body;
          activeRunId = run.id || null;
          if (run.status === "succeeded" || run.answer) {
            renderRun(run);
          } else if (run.id) {
            appendMessage("assistant", "Running…");
            pollRun(run.id);
          } else {
            setRunControls("idle");
          }
        })
        .catch(function () {
          appendMessage(
            "assistant",
            "Could not start run. Check owner auth / provider connection."
          );
          setRunControls("failed");
        });
    }

    function bind() {
      if (toggleBtn) toggleBtn.addEventListener("click", toggle);
      if (topbarBtn) topbarBtn.addEventListener("click", toggle);
      var closeBtn = $("ad-close");
      if (closeBtn) closeBtn.addEventListener("click", function () { setOpen(false); });
      if (backdrop) backdrop.addEventListener("click", function () { setOpen(false); });
      var minBtn = $("ad-minimize");
      if (minBtn) {
        minBtn.addEventListener("click", function () {
          setMinimized(!prefs.minimized);
        });
      }
      var pinBtn = $("ad-pin");
      if (pinBtn) {
        pinBtn.addEventListener("click", function () {
          prefs.pinned = !prefs.pinned;
          applyChrome();
          persistPrefs(true);
        });
      }
      if (contextBtn) {
        contextBtn.addEventListener("click", function () {
          var open = contextDrawer && contextDrawer.hidden;
          setContextOpen(!!open);
          if (open && promptEl && promptEl.value.trim()) previewContext(promptEl.value);
        });
      }
      var contextClose = $("ad-context-close");
      if (contextClose) {
        contextClose.addEventListener("click", function () {
          setContextOpen(false);
        });
      }
      document.querySelectorAll(".ad-tab").forEach(function (tab) {
        tab.addEventListener("click", function () {
          switchTab(tab.getAttribute("data-ad-tab"));
        });
      });
      document.querySelectorAll("[data-ad-suggestion]").forEach(function (btn) {
        btn.addEventListener("click", function () {
          sendPrompt(btn.getAttribute("data-ad-suggestion"));
        });
      });
      if (sendBtn) {
        sendBtn.addEventListener("click", function () {
          sendPrompt(promptEl && promptEl.value);
        });
      }
      if (cancelBtn) cancelBtn.addEventListener("click", cancelActiveRun);
      if (retryBtn) retryBtn.addEventListener("click", retryActiveRun);
      if (promptEl) {
        promptEl.addEventListener("keydown", function (ev) {
          if (ev.key === "Enter" && (ev.metaKey || ev.ctrlKey)) {
            ev.preventDefault();
            sendPrompt(promptEl.value);
          }
        });
      }
      if (agentSel) {
        agentSel.addEventListener("change", function () {
          selectedAgent = agentSel.value;
          loadModels(selectedAgent);
        });
      }
      if (modelSel) {
        modelSel.addEventListener("change", function () {
          selectedModel = modelSel.value;
        });
      }
      var settings = $("ad-settings");
      var history = $("ad-history");
      function goCenter() {
        setMenuOpen(false);
        window.location.href = boot.center_url || "/work/okarun";
      }
      if (settings) settings.addEventListener("click", goCenter);
      if (history) history.addEventListener("click", goCenter);
      if (moreBtn) {
        moreBtn.addEventListener("click", function (ev) {
          ev.stopPropagation();
          setMenuOpen(menuPop ? menuPop.hidden : true);
        });
      }
      document.addEventListener("click", function (ev) {
        if (!menuPop || menuPop.hidden) return;
        var menu = $("ad-menu");
        if (menu && !menu.contains(ev.target)) setMenuOpen(false);
      });
      setRunControls("idle");

      var handle = $("ad-resize");
      if (handle && shell) {
        var dragging = false;
        handle.addEventListener("mousedown", function (ev) {
          if (isMobile()) return;
          dragging = true;
          document.body.style.userSelect = "none";
          ev.preventDefault();
        });
        window.addEventListener("mousemove", function (ev) {
          if (!dragging) return;
          var rail = document.querySelector(".activity-rail");
          var railW = rail ? rail.getBoundingClientRect().width : 48;
          var width = Math.round(window.innerWidth - railW - ev.clientX);
          prefs.width = Math.max(prefs.min_width, Math.min(prefs.max_width, width));
          applyChrome();
        });
        window.addEventListener("mouseup", function () {
          if (!dragging) return;
          dragging = false;
          document.body.style.userSelect = "";
          persistPrefs(true);
        });
      }

      window.addEventListener("resize", function () {
        applyChrome();
      });
    }

    bind();
    applyChrome();
    if (expanded()) ensureAgents();
    function openWithPrompt(text) {
      setOpen(true);
      setMinimized(false);
      if (promptEl && text) promptEl.value = text;
      ensureAgents();
    }
    return {
      prefs: prefs,
      setOpen: setOpen,
      toggle: toggle,
      ensureAgents: ensureAgents,
      applyChrome: applyChrome,
      openWithPrompt: openWithPrompt,
    };
  }

  document.addEventListener("DOMContentLoaded", function () {
    var host = $("assistant-dock-host");
    if (!host) return;
    window.__assistantDock = createDockController(host);
    window.HubAssistantDock = window.__assistantDock;
  });
})();
