/**
 * Persistent Aira/Okarun assistant dock.
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
      { open: false, pinned: true, minimized: false, width: 380, min_width: 300, max_width: 560 },
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
    var openBtn = $("ad-open-btn");
    var backdrop = $("ad-backdrop");
    var promptEl = $("ad-prompt");
    var agentSel = $("ad-agent");
    var modelSel = $("ad-model");
    var messages = $("ad-messages");
    var output = $("ad-output");
    var contextBody = $("ad-context-body");
    var saveTimer = null;
    var pollTimer = null;

    function isMobile() {
      return window.matchMedia(MOBILE_MQ).matches;
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
      if (openBtn) {
        var expanded = visible && !prefs.minimized;
        openBtn.setAttribute("aria-expanded", expanded ? "true" : "false");
        openBtn.hidden = expanded;
      }
      if (backdrop) {
        backdrop.hidden = !(isMobile() && visible && !prefs.minimized);
      }
      var pin = $("ad-pin");
      if (pin) pin.setAttribute("aria-pressed", prefs.pinned ? "true" : "false");
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
      persistPrefs();
      if (prefs.open) ensureAgents();
    }

    function setMinimized(min) {
      prefs.minimized = !!min;
      if (prefs.minimized) prefs.open = true;
      applyChrome();
      persistPrefs(true);
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

    function appendMessage(role, html) {
      if (!messages) return;
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
      fetch(apiBase + "/agents", { credentials: "same-origin" })
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
            var label = typeof model === "string" ? model : model.label || model.name || id;
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

    function renderRun(run) {
      var answer = run.answer || run.error || "No answer yet.";
      var sourceBits = ((run.context || {}).included_sources || []).join(", ");
      appendMessage(
        "assistant",
        '<div class="ad-thought">Thought for run ' +
          escapeHtml(run.id || "") +
          "</div><div>" +
          escapeHtml(answer).replace(/\n/g, "<br>") +
          "</div>" +
          (sourceBits
            ? '<div class="ad-source">Source: ' + escapeHtml(sourceBits) + " (read-only)</div>"
            : "")
      );
      if (output) {
        output.innerHTML =
          "<pre class=\"ad-context-body\">" +
          escapeHtml(run.logs || "") +
          "</pre>" +
          (run.tool_activity && run.tool_activity.length
            ? "<p class=\"muted\">Tool activity: " + run.tool_activity.length + "</p>"
            : "");
      }
    }

    function pollRun(runId) {
      if (pollTimer) clearInterval(pollTimer);
      pollTimer = setInterval(function () {
        fetch(apiBase + "/runs/" + encodeURIComponent(runId), { credentials: "same-origin" })
          .then(function (r) {
            return r.json();
          })
          .then(function (run) {
            if (!run || run.error && !run.id) return;
            if (run.status === "succeeded" || run.status === "failed" || run.status === "cancelled") {
              clearInterval(pollTimer);
              pollTimer = null;
              renderRun(run);
            }
          })
          .catch(function () {});
      }, 1200);
    }

    function sendPrompt(text) {
      var prompt = String(text || "").trim();
      if (!prompt) return;
      appendMessage("user", escapeHtml(prompt));
      if (promptEl) promptEl.value = "";
      if (!selectedAgent) {
        appendMessage("assistant", "Select a provider first (lazy-loaded when the panel opens).");
        ensureAgents();
        return;
      }
      previewContext(prompt);
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
              escapeHtml((res.body && (res.body.error || res.body.message)) || "Run failed.")
            );
            return;
          }
          var run = res.body.run || res.body;
          if (run.status === "succeeded" || run.answer) {
            renderRun(run);
          } else if (run.id) {
            appendMessage("assistant", "Running…");
            pollRun(run.id);
          }
        })
        .catch(function () {
          appendMessage("assistant", "Could not start run. Check owner auth / provider connection.");
        });
    }

    function bind() {
      if (openBtn) openBtn.addEventListener("click", function () { setOpen(true); });
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
      var sendBtn = $("ad-send");
      if (sendBtn) sendBtn.addEventListener("click", function () { sendPrompt(promptEl && promptEl.value); });
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
      var center = $("ad-open-center");
      function goCenter() {
        window.location.href = boot.center_url || "/work/okarun";
      }
      if (settings) settings.addEventListener("click", goCenter);
      if (history) history.addEventListener("click", goCenter);
      if (center) center.addEventListener("click", function (ev) {
        /* normal navigation */
      });

      var handle = $("ad-resize");
      if (handle && shell) {
        var dragging = false;
        handle.addEventListener("mousedown", function (ev) {
          if (isMobile()) return;
          dragging = true;
          ev.preventDefault();
        });
        window.addEventListener("mousemove", function (ev) {
          if (!dragging) return;
          var rect = shell.getBoundingClientRect();
          var width = Math.round(rect.right - ev.clientX);
          prefs.width = Math.max(prefs.min_width, Math.min(prefs.max_width, width));
          applyChrome();
        });
        window.addEventListener("mouseup", function () {
          if (!dragging) return;
          dragging = false;
          persistPrefs(true);
        });
      }

      window.addEventListener("resize", function () {
        applyChrome();
      });
    }

    bind();
    applyChrome();
    if (prefs.open && !prefs.minimized) ensureAgents();
    return {
      prefs: prefs,
      setOpen: setOpen,
      ensureAgents: ensureAgents,
      applyChrome: applyChrome,
    };
  }

  document.addEventListener("DOMContentLoaded", function () {
    var host = $("assistant-dock-host");
    if (!host) return;
    window.__assistantDock = createDockController(host);
  });
})();
