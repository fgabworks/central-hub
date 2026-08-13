/**
 * Persistent Aira/AiriX assistant dock (VS Code / Cursor-style right rail).
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
      {
        open: false,
        pinned: true,
        minimized: false,
        width: 400,
        min_width: 300,
        max_width: 560,
        selected_repository_id: "",
        routing_mode: "smart",
        interaction_mode: "smart",
        selected_agent_id: "",
        selected_model_id: "",
        context_sources: [],
        dhis2_environment: "",
        direct_conversation_id: "",
        direct_session_fingerprint: "",
      },
      boot.prefs || {}
    );
    var profile = boot.profile || {};
    var apiBase = boot.api_base || "/api/assistants/okarun";
    var prefsUrl = boot.prefs_url || "/api/assistant-dock/prefs";
    var agentsLoaded = false;
    var agentsLoading = false;
    var selectedAgent = String(prefs.selected_agent_id || "");
    var selectedModel = String(prefs.selected_model_id || "");
    var selectedRepoIds = [];
    var reposLoaded = false;
    var reposLoading = false;
    var repoCatalog = [];
    var REPO_REQUIRED_AGENTS = {
      codex: true,
      "claude-code": true,
      "cursor-agent": true,
    };
    var shell = document.querySelector(".app-shell");
    var panel = $("ad-panel");
    var toggleBtn = $("ar-assistant");
    var topbarBtn = $("ad-topbar-toggle");
    var backdrop = $("ad-backdrop");
    var promptEl = $("ad-prompt");
    var agentSel = $("ad-agent");
    var modelSel = $("ad-model");
    var repoSel = $("ad-repo");
    var routingModeSel = $("ad-routing-mode-select");
    var directHint = $("ad-direct-hint");
    var messages = $("ad-messages");
    var output = $("ad-output");
    var contextBody = $("ad-context-body");
    var contextDrawer = $("ad-context-drawer");
    var contextBtn = $("ad-context-btn");
    var contextChoices = $("ad-context-choices");
    var contextDhis2Env = $("ad-context-dhis2-env");
    var saveTimer = null;
    var pollTimer = null;
    var agentPollDeadlineTimer = null;
    var activeRunId = null;
    var lastPrompt = "";
    var cancelBtn = $("ad-cancel");
    var retryBtn = $("ad-retry");
    var sendBtn = $("ad-send");
    var emptyEl = $("ad-empty");
    var moreBtn = $("ad-more");
    var menuPop = $("ad-menu-pop");
    var smartRouting = boot.smart_routing || {};
    var routingEnabled = !!smartRouting.enabled;
    var routingCard = $("ad-routing-card");
    var routingBody = $("ad-routing-body");
    var routingSettings = $("ad-routing-settings");
    var pendingRoute = null;
    var pendingPlan = null;
    var pendingPrompt = "";
    var skipRoutingOnce = false;
    var activeRouteExecutionId = null;
    var routePollTimer = null;
    var maximized = false;
    var maximizedWidth = 0;

    function isMobile() {
      return window.matchMedia(MOBILE_MQ).matches;
    }

    function expanded() {
      return !!prefs.open && !prefs.minimized;
    }

    function contentAreaWidth() {
      var rail = document.querySelector(".activity-rail");
      var sidebar = document.querySelector(".sidebar");
      var railW = rail ? rail.getBoundingClientRect().width : 48;
      var sideW = sidebar ? sidebar.getBoundingClientRect().width : 0;
      return Math.max(640, window.innerWidth - railW - sideW);
    }

    function computeMaximizedWidth() {
      /* ~45% of CLIMATE content area (between sidebar and activity rail). */
      var contentW = contentAreaWidth();
      var target = Math.round(contentW * 0.45);
      var rail = document.querySelector(".activity-rail");
      var railW = rail ? rail.getBoundingClientRect().width : 48;
      var sidebar = document.querySelector(".sidebar");
      var sideW = sidebar ? sidebar.getBoundingClientRect().width : 0;
      /* Leave a usable main workspace (~40% content / min 280px). */
      var maxAvailable = Math.max(
        prefs.min_width,
        window.innerWidth - railW - sideW - Math.max(280, Math.round(contentW * 0.4))
      );
      return Math.max(prefs.min_width, Math.min(maxAvailable, target));
    }

    function applyChrome() {
      if (!shell) return;
      var visible = !!prefs.open;
      var isMax = visible && !prefs.minimized && maximized && !isMobile();
      shell.classList.toggle("is-ad-open", visible);
      shell.classList.toggle("is-ad-minimized", visible && !!prefs.minimized);
      shell.classList.toggle("is-ad-mobile", isMobile());
      shell.classList.toggle("is-ad-maximized", isMax);
      var compactW = Math.max(prefs.min_width, Math.min(prefs.max_width, prefs.width));
      shell.style.setProperty("--ad-width", compactW + "px");
      maximizedWidth = computeMaximizedWidth();
      shell.style.setProperty("--ad-max-width", maximizedWidth + "px");
      var activeW = !visible
        ? 0
        : prefs.minimized
          ? 48
          : isMax
            ? maximizedWidth
            : compactW;
      shell.style.setProperty("--ad-active-width", activeW + "px");
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
      var titleEl = $("ad-title");
      if (titleEl) {
        titleEl.textContent = isMax
          ? "AI Assistant (Maximized)"
          : profile.name || "Assistant";
      }
      var maximizeBtn = $("ad-maximize");
      if (maximizeBtn) {
        maximizeBtn.setAttribute("aria-pressed", maximized ? "true" : "false");
        maximizeBtn.setAttribute("aria-label", maximized ? "Restore assistant" : "Maximize assistant");
        maximizeBtn.title = maximized ? "Restore assistant" : "Maximize assistant";
        maximizeBtn.textContent = maximized ? "⧉" : "□";
      }
    }

    function persistPrefs(immediate) {
      var payload = {
        open: !!prefs.open,
        pinned: !!prefs.pinned,
        minimized: !!prefs.minimized,
        width: prefs.width,
        selected_repository_id: prefs.selected_repository_id || "",
        routing_mode: currentInteractionMode() === "agent" ? "direct" : "smart",
        interaction_mode: currentInteractionMode(),
        selected_agent_id: selectedAgent || prefs.selected_agent_id || "",
        selected_model_id: selectedModel || prefs.selected_model_id || "",
        context_sources: currentContextSources(),
        dhis2_environment: (contextDhis2Env && contextDhis2Env.value) || prefs.dhis2_environment || "",
        direct_conversation_id: prefs.direct_conversation_id || "",
        direct_session_fingerprint: prefs.direct_session_fingerprint || "",
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

    function agentRequiresRepository(agentId) {
      return !!REPO_REQUIRED_AGENTS[String(agentId || "").toLowerCase()];
    }

    function activeWorkspaceRepositoryId() {
      var wc = document.getElementById("wc-term-repo");
      if (wc && wc.value) return String(wc.value).trim();
      return "";
    }

    function setSelectedRepository(repoId, persist) {
      var rid = String(repoId || "").trim();
      selectedRepoIds = rid ? [rid] : [];
      prefs.selected_repository_id = rid;
      if (repoSel) repoSel.value = rid;
      if (persist !== false) persistPrefs(true);
    }

    function currentRepositoryIds() {
      if (repoSel && repoSel.value) {
        selectedRepoIds = [repoSel.value];
        prefs.selected_repository_id = repoSel.value;
        return selectedRepoIds.slice();
      }
      if (prefs.selected_repository_id) {
        selectedRepoIds = [prefs.selected_repository_id];
        return selectedRepoIds.slice();
      }
      return selectedRepoIds.slice();
    }

    function currentInteractionMode() {
      if (routingModeSel && routingModeSel.value) {
        return routingModeSel.value === "direct" ? "agent" : routingModeSel.value;
      }
      return prefs.interaction_mode || (prefs.routing_mode === "direct" ? "agent" : "smart");
    }

    function currentRoutingMode() {
      return currentInteractionMode() === "agent" ? "direct" : "smart";
    }

    function currentContextSources() {
      if (!contextChoices) return Array.isArray(prefs.context_sources) ? prefs.context_sources.slice() : [];
      return Array.prototype.filter.call(contextChoices.querySelectorAll('input[type="checkbox"]'), function (box) {
        return box.checked;
      }).map(function (box) { return box.value; });
    }

    function applyRoutingModeChrome() {
      var mode = currentInteractionMode();
      prefs.interaction_mode = mode;
      prefs.routing_mode = mode === "agent" ? "direct" : "smart";
      if (routingModeSel && routingModeSel.value !== mode) {
        routingModeSel.value = mode;
      }
      if (directHint) directHint.hidden = mode !== "agent";
      var hint = document.querySelector(".ad-routing-hint");
      if (hint) {
        hint.hidden = mode !== "smart";
      }
      if (routingCard && mode !== "smart") {
        routingCard.hidden = true;
        pendingRoute = null;
        pendingPlan = null;
      }
      if (agentSel) {
        if (mode === "smart") {
          agentSel.value = "";
          agentSel.disabled = true;
        } else {
          agentSel.disabled = false;
          agentSel.value = selectedAgent || "";
        }
      }
      if (modelSel) {
        modelSel.disabled = mode === "smart";
        if (mode === "smart") modelSel.value = "";
        else if (selectedModel) modelSel.value = selectedModel;
      }
    }

    function setRoutingMode(mode) {
      prefs.interaction_mode = mode === "direct" ? "agent" : mode;
      prefs.routing_mode = prefs.interaction_mode === "agent" ? "direct" : "smart";
      if (prefs.interaction_mode !== "agent") {
        // Switching back to Smart clears Direct session resume handle.
        prefs.direct_conversation_id = "";
        prefs.direct_session_fingerprint = "";
      }
      applyRoutingModeChrome();
      persistPrefs();
    }

    function contextFingerprint(prompt) {
      var repoFields = repositoryPayloadFields();
      var bits = [
        currentRoutingMode(),
        currentInteractionMode(),
        selectedAgent || "",
        currentSelectedModel() || "",
        (repoFields.selected_repository_id || "") + "",
        currentContextSources().join(","),
        (contextDhis2Env && contextDhis2Env.value) || prefs.dhis2_environment || "",
      ];
      return bits.join("|").slice(0, 120);
    }

    function contextPayloadFields() {
      return {
        context_sources: currentContextSources(),
        dhis2_environment: (contextDhis2Env && contextDhis2Env.value) || prefs.dhis2_environment || null,
      };
    }

    function initializeContextControls() {
      var selected = Array.isArray(prefs.context_sources) ? prefs.context_sources : [];
      if (contextChoices) {
        contextChoices.querySelectorAll('input[type="checkbox"]').forEach(function (box) {
          box.checked = selected.indexOf(box.value) >= 0;
          box.addEventListener("change", function () {
            prefs.context_sources = currentContextSources();
            persistPrefs();
            if (promptEl && promptEl.value.trim()) previewContext(promptEl.value);
          });
        });
      }
      if (contextDhis2Env) {
        contextDhis2Env.value = prefs.dhis2_environment || "";
        contextDhis2Env.addEventListener("change", function () {
          prefs.dhis2_environment = contextDhis2Env.value;
          if (contextDhis2Env.value && contextChoices) {
            var box = contextChoices.querySelector('input[value="dhis2_environment"]');
            if (box) box.checked = true;
          }
          prefs.context_sources = currentContextSources();
          persistPrefs(true);
        });
      }
    }

    function repositoryPayloadFields() {
      var ids = currentRepositoryIds();
      return {
        repository_ids: ids,
        selected_repository_id: prefs.selected_repository_id || (ids[0] || "") || null,
        active_repository_id: activeWorkspaceRepositoryId() || null,
      };
    }

    function repositoryLabel(repoId) {
      var rid = String(repoId || "").trim();
      if (!rid) return "";
      for (var i = 0; i < repoCatalog.length; i++) {
        if (repoCatalog[i].id === rid) {
          return repoCatalog[i].name || repoCatalog[i].id;
        }
      }
      return rid;
    }

    function ensureRepositories() {
      if (!repoSel || !profile.repositories_allowed) return;
      if (reposLoaded || reposLoading) return;
      reposLoading = true;
      var url = boot.lazy_repositories_url || apiBase + "/repositories";
      fetch(url, { credentials: "same-origin", headers: { Accept: "application/json" } })
        .then(function (r) {
          return r.json();
        })
        .then(function (data) {
          var repos = (data.repositories || []).filter(function (r) {
            return r && r.id && r.selectable !== false;
          });
          repoCatalog = repos;
          repoSel.innerHTML = "";
          var placeholder = document.createElement("option");
          placeholder.value = "";
          placeholder.textContent =
            repos.length > 1 ? "Select repo…" : repos.length === 1 ? "Repository" : "No connected repos";
          repoSel.appendChild(placeholder);
          repos.forEach(function (repo) {
            var opt = document.createElement("option");
            opt.value = repo.id;
            opt.textContent = repo.name || repo.id;
            repoSel.appendChild(opt);
          });

          var persisted = String(prefs.selected_repository_id || "").trim();
          var active = activeWorkspaceRepositoryId();
          var pick = "";
          if (persisted && repos.some(function (r) { return r.id === persisted; })) {
            pick = persisted;
          } else if (active && repos.some(function (r) { return r.id === active; })) {
            pick = active;
          } else if (repos.length === 1) {
            pick = repos[0].id;
          }
          // Multiple repos with no persisted/active selection: leave blank (require user pick).
          setSelectedRepository(pick, false);
          if (pick && pick !== persisted) {
            prefs.selected_repository_id = pick;
            persistPrefs(true);
          }
          reposLoaded = true;
        })
        .catch(function () {
          selectedRepoIds = [];
          repoCatalog = [];
        })
        .finally(function () {
          reposLoading = false;
        });
    }

    function assertRepositoryReady(agentId) {
      if (!agentRequiresRepository(agentId)) return true;
      var ids = currentRepositoryIds();
      if (ids.length) return true;
      var selectable = repoCatalog.filter(function (r) {
        return r && r.id && r.selectable !== false;
      });
      if (selectable.length === 1) {
        setSelectedRepository(selectable[0].id, true);
        return true;
      }
      if (!selectable.length) {
        appendMessage(
          "assistant",
          "No connected repository is available. Connect a local repository before using " +
            escapeHtml(agentId) +
            "."
        );
      } else {
        appendMessage(
          "assistant",
          "Select a connected repository for <strong>" +
            escapeHtml(agentId) +
            "</strong> (multiple repos are connected — AiriX will not auto-pick)."
        );
        if (repoSel) repoSel.focus();
      }
      return false;
    }

    function setOpen(open) {
      prefs.open = !!open;
      if (prefs.open) prefs.minimized = false;
      else {
        prefs.minimized = false;
        maximized = false;
      }
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
      if (prefs.minimized) {
        prefs.open = true;
        maximized = false;
      }
      applyChrome();
      persistPrefs(true);
    }

    function setMaximized(max) {
      maximized = !!max;
      if (maximized) {
        prefs.open = true;
        prefs.minimized = false;
        maximizedWidth = computeMaximizedWidth();
      }
      applyChrome();
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
          var autoOpt = document.createElement("option");
          autoOpt.value = "";
          autoOpt.textContent = "Auto";
          autoOpt.setAttribute("data-runnable", "1");
          agentSel.appendChild(autoOpt);
          if (!agents.length) {
            autoOpt.textContent = "Auto (no providers configured)";
            return;
          }
          agents.forEach(function (agent) {
            var opt = document.createElement("option");
            opt.value = agent.id;
            var runnable = !!agent.runnable;
            var label = agent.label || agent.id;
            if (!runnable) {
              opt.disabled = true;
              label += " (unavailable)";
              if (agent.detail) opt.title = String(agent.detail);
            }
            opt.textContent = label;
            opt.setAttribute("data-runnable", runnable ? "1" : "0");
            agentSel.appendChild(opt);
          });
          var firstOk = Array.prototype.find.call(agentSel.options || [], function (o) {
            return !!o.value && o.getAttribute("data-runnable") === "1";
          });
          var persistedAgent = String(prefs.selected_agent_id || selectedAgent || "");
          var persistedOption = Array.prototype.find.call(agentSel.options || [], function (o) {
            return o.value === persistedAgent && o.getAttribute("data-runnable") !== "0";
          });
          selectedAgent = persistedOption ? persistedAgent : "";
          agentSel.value = currentInteractionMode() === "smart" ? "" : selectedAgent;
          agentsLoaded = true;
          if (selectedAgent) loadModels(selectedAgent, prefs.selected_model_id || "");
          ensureRepositories();
          applyRoutingModeChrome();
        })
        .catch(function () {
          agentSel.innerHTML = '<option value="">Providers unavailable</option>';
        })
        .finally(function () {
          agentsLoading = false;
        });
    }

    function currentSelectedModel() {
      if (currentInteractionMode() === "smart") return "";
      if (modelSel && !modelSel.hidden && modelSel.value) {
        selectedModel = modelSel.value;
        return modelSel.value;
      }
      return selectedModel || "";
    }

    function loadModels(agentId, preferredModel) {
      if (!modelSel || !agentId) return;
      var preserve = (preferredModel || selectedModel || "").trim();
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
          var ids = [];
          var autoModel = document.createElement("option");
          autoModel.value = "";
          autoModel.textContent = "Auto";
          modelSel.appendChild(autoModel);
          models.forEach(function (model) {
            var id = typeof model === "string" ? model : model.id || model.name;
            var label =
              typeof model === "string" ? model : model.display_name || model.label || model.name || id;
            if (!id) return;
            if (id === "__provider_default__") {
              label = label && label !== id ? label : "Provider configured default";
            }
            ids.push(id);
            var opt = document.createElement("option");
            opt.value = id;
            opt.textContent = label;
            modelSel.appendChild(opt);
          });
          // Prefer recommended/real model over bare __provider_default__ when both exist.
          var realIds = ids.filter(function (id) {
            return id && id.indexOf("__") !== 0;
          });
          var pick = "";
          if (preserve && ids.indexOf(preserve) >= 0) {
            pick = preserve;
          } else if (data.recommended_model && ids.indexOf(data.recommended_model) >= 0) {
            pick = data.recommended_model;
          } else if (data.default_model && ids.indexOf(data.default_model) >= 0) {
            pick = data.default_model;
          } else if (realIds.length) {
            pick = realIds[0];
          } else {
            pick = ids[0] || "";
          }
          selectedModel = pick;
          prefs.selected_model_id = pick;
          modelSel.value = currentInteractionMode() === "smart" ? "" : pick;
          modelSel.hidden = false;
        })
        .catch(function () {
          modelSel.hidden = true;
        });
    }

    function previewContext(prompt) {
      var repoFields = repositoryPayloadFields();
      return fetch(apiBase + "/context/preview", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        credentials: "same-origin",
        body: JSON.stringify({
          mode: currentInteractionMode() === "plan" ? "plan" : (currentInteractionMode() === "inspect" ? "find" : "ask"),
          prompt: prompt,
          agent_id: selectedAgent,
          model: currentSelectedModel(),
          tools: profile.default_tools || [],
          repository_ids: repoFields.repository_ids,
          context_sources: currentContextSources(),
          dhis2_environment: (contextDhis2Env && contextDhis2Env.value) || prefs.dhis2_environment || null,
        }),
      })
        .then(function (r) {
          return r.json();
        })
        .then(function (data) {
          if (contextBody) {
            var preview = data.preview || data;
            var repoIds = repoFields.repository_ids || preview.repository_ids || [];
            contextBody.textContent = JSON.stringify(
              {
                repository_ids: repoIds,
                repository: repoIds.map(repositoryLabel).filter(Boolean),
                interaction_mode: currentInteractionMode(),
                context_sources: currentContextSources(),
                dhis2_environment: (contextDhis2Env && contextDhis2Env.value) || prefs.dhis2_environment || null,
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

    function unwrapAgentRun(payload) {
      if (!payload || typeof payload !== "object") return null;
      if (payload.run && typeof payload.run === "object") return payload.run;
      if (payload.id || payload.status) return payload;
      return null;
    }

    function isAgentRunTerminal(status) {
      var s = String(status || "").toLowerCase();
      return (
        s === "completed" ||
        s === "succeeded" ||
        s === "failed" ||
        s === "cancelled" ||
        s === "canceled" ||
        s === "unavailable" ||
        s === "timed_out" ||
        s === "timeout" ||
        s === "paused_for_approval"
      );
    }

    function normalizeAgentRunStatus(status) {
      var s = String(status || "").toLowerCase();
      if (s === "succeeded" || s === "success") return "completed";
      if (s === "canceled") return "cancelled";
      if (s === "unavailable" || s === "error") return "failed";
      if (s === "timeout") return "timed_out";
      return s || "failed";
    }

    function stopAgentPoll() {
      if (pollTimer) {
        clearInterval(pollTimer);
        pollTimer = null;
      }
      if (agentPollDeadlineTimer) {
        clearTimeout(agentPollDeadlineTimer);
        agentPollDeadlineTimer = null;
      }
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
      run = unwrapAgentRun(run) || run || {};
      var status = normalizeAgentRunStatus(run.status);
      var answer = run.answer || run.error || "No answer yet.";
      var sourceBits = ((run.context || {}).included_sources || []).join(", ");
      if (!sourceBits && (run.agent_label || run.agent_id)) {
        sourceBits = run.agent_label || run.agent_id;
      }
      var repoBits = (run.repository_ids || []).map(repositoryLabel).filter(Boolean).join(", ");
      if (repoBits) {
        sourceBits = sourceBits ? sourceBits + " · repo " + repoBits : "repo " + repoBits;
      }
      var grounding = run.grounding || (run.context && run.context.grounding) || {};
      var groundingLine = "";
      if (grounding && (grounding.grounded_label || grounding.source || grounding.required)) {
        groundingLine =
          '<div class="ad-source">Source: ' +
          escapeHtml(String(grounding.source || sourceBits || "unknown")) +
          " · Grounded: " +
          escapeHtml(String(grounding.grounded_label || (grounding.grounded ? "Yes" : "No"))) +
          (grounding.grounded
            ? ""
            : grounding.reason
              ? " — " + escapeHtml(String(grounding.reason))
              : "") +
          "</div>";
      }
      var selectedM = (run.context && run.context.selected_model) || "";
      var resolvedM = run.model || (run.context && run.context.resolved_model) || "";
      var modelLine = "";
      if (selectedM || resolvedM || run.agent_id) {
        modelLine =
          '<div class="ad-source">Selected: ' +
          escapeHtml(String(run.agent_label || run.agent_id || "") + (selectedM ? " / " + selectedM : "")) +
          " · Resolved: " +
          escapeHtml(String(run.agent_label || run.agent_id || "") + (resolvedM ? " / " + resolvedM : "")) +
          "</div>";
      }
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
      if (status === "failed" || status === "timed_out") {
        appendMessage(
          "assistant",
          "<strong>Run " +
            escapeHtml(status) +
            "</strong>" +
            (run.error ? ": " + escapeHtml(run.error) : "") +
            (run.answer
              ? '<pre class="ad-run-answer">' + escapeHtml(run.answer) + "</pre>"
              : "")
        );
      } else if (status === "cancelled") {
        appendMessage("assistant", "Run cancelled.");
      } else if (status === "paused_for_approval") {
        appendMessage(
          "assistant",
          "Paused for approval. Choose an agent explicitly or approve Codex."
        );
      } else {
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
            (modelLine || "") +
            (groundingLine
              ? groundingLine
              : sourceBits
                ? '<div class="ad-source">Source: ' +
                  escapeHtml(sourceBits) +
                  " (read-only)</div>"
                : "")
        );
      }
      if (messages) {
        messages.querySelectorAll(".ad-insert-term").forEach(function (btn) {
          if (btn._bound) return;
          btn._bound = true;
          btn.addEventListener("click", function () {
            var idx = Number(btn.getAttribute("data-ad-insert-idx") || 0);
            var text = codeBlocks[idx] || "";
            if (!text) return;
            if (window.WCTerminal && window.WCTerminal.insertText) {
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
      stopAgentPoll();
      if (status === "failed" || status === "cancelled" || status === "timed_out") {
        setRunControls("failed");
      } else {
        setRunControls("idle");
      }
    }

    function pollRun(runId) {
      stopAgentPoll();
      activeRunId = runId;
      setRunControls("running");
      var started = Date.now();
      var maxMs = 120000;
      function tick() {
        if (document.visibilityState === "hidden") return;
        if (!expanded()) return;
        if (Date.now() - started > maxMs) {
          stopAgentPoll();
          appendMessage("assistant", "Run timed out waiting for provider status.");
          setRunControls("failed");
          return;
        }
        fetch(apiBase + "/runs/" + encodeURIComponent(runId), {
          credentials: "same-origin",
          headers: { Accept: "application/json" },
        })
          .then(function (r) {
            return r.json().then(function (data) {
              return { ok: r.ok, data: data };
            });
          })
          .then(function (res) {
            var run = unwrapAgentRun(res.data);
            if (!res.ok || !run || !run.id) return;
            if (isAgentRunTerminal(run.status)) {
              stopAgentPoll();
              renderRun(run);
            }
          })
          .catch(function () {});
      }
      pollTimer = setInterval(tick, 900);
      agentPollDeadlineTimer = setTimeout(function () {
        if (!pollTimer) return;
        stopAgentPoll();
        appendMessage("assistant", "Run timed out waiting for provider status.");
        setRunControls("failed");
      }, maxMs + 500);
      tick();
    }

    function cancelActiveRun() {
      if (activeRouteExecutionId) {
        cancelRouteExecution();
        return;
      }
      if (!activeRunId) return;
      fetch(apiBase + "/runs/" + encodeURIComponent(activeRunId) + "/cancel", {
        method: "POST",
        credentials: "same-origin",
      })
        .then(function (r) {
          return r.json();
        })
        .then(function (data) {
          stopAgentPoll();
          var run = unwrapAgentRun(data);
          if (run && run.id) renderRun(run);
          else setRunControls("failed");
        })
        .catch(function () {
          stopAgentPoll();
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
          var run = unwrapAgentRun(res.body) || {};
          if (!res.ok) {
            appendMessage(
              "assistant",
              escapeHtml((res.body && res.body.error) || "Retry failed.")
            );
            setRunControls("failed");
            return;
          }
          activeRunId = run.id || activeRunId;
          if (isAgentRunTerminal(run.status) || run.answer) {
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

    function hideRoutingCard() {
      if (routingCard) routingCard.hidden = true;
      pendingRoute = null;
      pendingPlan = null;
    }

    function stopRoutePoll() {
      if (routePollTimer) {
        clearTimeout(routePollTimer);
        routePollTimer = null;
      }
    }

    function showRoutingCard(rec, plan) {
      if (!routingCard || !routingBody || !rec) return;
      pendingRoute = rec;
      pendingPlan = plan || null;
      var alt = rec.alternative_label
        ? "<div class=\"ad-routing-line\"><span>Alternative</span><strong>" +
          escapeHtml(rec.alternative_label) +
          "</strong></div>"
        : "";
      var approval = rec.approval_required
        ? '<span class="ad-routing-badge is-warn">Approval required</span>'
        : '<span class="ad-routing-badge">No approval</span>';
      var modelLine =
        '<div class="ad-routing-line"><span>Model</span><strong>' +
        escapeHtml(
          (rec && rec.recommended_model) ||
            currentSelectedModel() ||
            "Provider default"
        ) +
        (rec && rec.recommended_model_reason
          ? " · " + escapeHtml(String(rec.recommended_model_reason).split(";")[0])
          : "") +
        "</strong></div>";
      var ctx = (plan && plan.context) || {};
      var repoIds = ctx.repository_ids || currentRepositoryIds() || [];
      var repoLine =
        '<div class="ad-routing-line"><span>Repository</span><strong>' +
        escapeHtml(
          repoIds.length
            ? repoIds.map(repositoryLabel).filter(Boolean).join(", ") || repoIds.join(", ")
            : agentRequiresRepository(mapRoutingAgent(rec.recommended_agent))
              ? "Required — select a repo"
              : "Not required"
        ) +
        "</strong></div>";
      var ctxLine =
        '<div class="ad-routing-line"><span>Context</span><strong>' +
        escapeHtml(ctx.strategy || "minimal") +
        " · " +
        escapeHtml(String((ctx.tool_ids || []).length)) +
        " tools · max " +
        escapeHtml(String(ctx.max_context_files || 0)) +
        " files</strong></div>" +
        modelLine +
        repoLine;
      var steps = (plan && plan.steps) || [];
      var planNote =
        steps.length > 0
          ? '<p class="ad-routing-reason muted">Plan: ' +
            escapeHtml(steps.slice(0, 3).join(" → ")) +
            (steps.length > 3 ? "…" : "") +
            "</p>"
          : "";
      var expl = rec.explanation || {};
      var histRate =
        expl.historical_success_rate != null
          ? Math.round(Number(expl.historical_success_rate) * 100) +
            "% (" +
            escapeHtml(String(expl.sample_size || 0)) +
            " runs)"
          : "insufficient history";
      var histLine =
        "<div class=\"ad-routing-line\"><span>History</span><strong>" +
        escapeHtml(histRate) +
        "</strong></div>" +
        "<div class=\"ad-routing-line\"><span>Expected retries</span><strong>" +
        escapeHtml(String(expl.expected_retries != null ? expl.expected_retries : rec.expected_retries || 0)) +
        "</strong></div>";
      var escLine = (expl.escalation_reason || rec.escalation_reason)
        ? '<p class="ad-routing-reason muted">Escalation: ' +
          escapeHtml(expl.escalation_reason || rec.escalation_reason) +
          "</p>"
        : "";
      var findings = (plan && plan.context && plan.context.prior_findings) || [];
      var findingsLine =
        findings.length > 0
          ? '<p class="ad-routing-reason muted">Prior findings: ' +
            escapeHtml(
              findings
                .map(function (f) {
                  return f.summary || "";
                })
                .filter(Boolean)
                .slice(0, 2)
                .join(" · ")
            ) +
            "</p>"
          : "";
      var roleLine = rec.role_id
        ? "<div class=\"ad-routing-line\"><span>Role</span><strong>" +
          escapeHtml(rec.role_id) +
          "</strong></div>"
        : "";
      var orch = (rec.orchestration || (plan && plan.orchestration) || []).map(function (s) {
        return s.label || s.id;
      });
      var orchLine =
        orch.length > 0
          ? '<p class="ad-routing-reason muted">Plan: ' +
            escapeHtml(orch.join(" → ")) +
            "</p>"
          : "";
      var budgetWarn =
        (expl.budget_warning || (rec.budget && !rec.budget.ok && rec.budget.blocked_reason))
          ? '<p class="ad-routing-reason muted">Budget: ' +
            escapeHtml(expl.budget_warning || rec.budget.blocked_reason) +
            "</p>"
          : "";
      var expensiveWarn = expl.expensive_warning
        ? '<p class="ad-routing-reason muted">' + escapeHtml(expl.expensive_warning) + "</p>"
        : "";
      routingBody.innerHTML =
        '<div class="ad-routing-grid">' +
        roleLine +
        "<div class=\"ad-routing-line\"><span>Task</span><strong>" +
        escapeHtml(rec.task_type) +
        "</strong></div>" +
        "<div class=\"ad-routing-line\"><span>Complexity</span><strong>" +
        escapeHtml(String(rec.complexity)) +
        "/100</strong></div>" +
        "<div class=\"ad-routing-line\"><span>Risk</span><strong>" +
        escapeHtml(rec.risk) +
        "</strong></div>" +
        "<div class=\"ad-routing-line\"><span>Recommended</span><strong>" +
        escapeHtml(rec.recommended_label || rec.recommended_agent) +
        " · " +
        escapeHtml(rec.recommended_tier || "") +
        "</strong></div>" +
        alt +
        "<div class=\"ad-routing-line\"><span>Confidence</span><strong>" +
        Math.round(Number(rec.confidence || 0) * 100) +
        "%</strong></div>" +
        "<div class=\"ad-routing-line\"><span>Usage</span><strong>" +
        escapeHtml(rec.estimated_usage || "") +
        "</strong></div>" +
        histLine +
        ctxLine +
        "<div class=\"ad-routing-line\">" +
        approval +
        "</div>" +
        '<p class="ad-routing-reason muted">' +
        escapeHtml(rec.reason || "") +
        "</p>" +
        escLine +
        orchLine +
        budgetWarn +
        expensiveWarn +
        findingsLine +
        planNote +
        '<p class="ad-routing-note muted">Phase 5: cost intelligence + RBAC + relevant findings; Codex still requires approval.</p>' +
        "</div>";
      routingCard.hidden = false;
    }

    function mapRoutingAgent(agentId) {
      var map = {
        deterministic: "",
        "low-cost": "hub-simulator",
        grok: "grok",
        codex: "codex",
        "openai-api": "openai-api",
        "claude-code": "claude-code",
        "cursor-agent": "cursor-agent",
      };
      return map[agentId] || agentId;
    }

    function applyRecommendedAgent(rec) {
      if (!rec) return;
      ensureAgents();
      var target = mapRoutingAgent(rec.recommended_agent);
      if (agentSel && target) {
        var opt = Array.prototype.find.call(agentSel.options || [], function (o) {
          return o.value === target;
        });
        if (opt) {
          agentSel.value = target;
          selectedAgent = target;
          loadModels(selectedAgent);
        }
      }
      if (promptEl && pendingPrompt) promptEl.value = pendingPrompt;
    }

    function isRouteTerminalStatus(status) {
      return (
        status === "completed" ||
        status === "failed" ||
        status === "cancelled" ||
        status === "paused_for_approval" ||
        status === "timed_out" ||
        status === "paused" ||
        status === "blocked" ||
        status === "unavailable"
      );
    }

    function isRouteActiveStatus(status) {
      return status === "queued" || status === "running" || status === "active";
    }

    function formatToolRuntimeFeedHtml(execution) {
      var feed = (execution && execution.tool_runtime_feed) || null;
      if (!feed || !Array.isArray(feed.steps) || !feed.steps.length) {
        return "";
      }
      var lines = feed.steps.slice(-8).map(function (s) {
        var tool = String((s && s.tool) || "");
        if (!tool || tool === "(final_answer)") return "";
        var mark = s && s.ok ? "ok" : String((s && s.result) || "err");
        var ms = s && s.duration_ms != null ? Math.round(Number(s.duration_ms)) : "—";
        return (
          "#" +
          String((s && s.step) || "?") +
          " " +
          tool +
          " [" +
          mark +
          " · " +
          ms +
          "ms]"
        );
      }).filter(Boolean);
      if (!lines.length) return "";
      return (
        "<div>Tool steps: " +
        escapeHtml(lines.join(" → ")) +
        (feed.step_count > lines.length ? " …" : "") +
        "</div>"
      );
    }

    function formatUsageTelemetryHtml(execution) {
      var t = (execution && execution.telemetry) || null;
      if (!t) {
        t = (execution && execution.usage) || null;
      }
      if (!t || (!t.execution_type && t.llm_invoked == null && t.total_ai_tokens == null && t.total_tokens == null)) {
        return "";
      }
      // Prefer event-sourced telemetry; never invent AI from missing fields.
      var isDeterministic =
        t.execution_type === "Deterministic" ||
        t.t0_pure === true ||
        (execution &&
          (execution.mode === "deterministic" ||
            execution.mode === "grounding_gate" ||
            execution.provider_id === "deterministic") &&
          !execution.agent_run_id);
      var llmInvoked = isDeterministic ? false : !!t.llm_invoked;
      var llm = llmInvoked ? "Yes" : "No";
      var execType =
        t.execution_type ||
        (isDeterministic ? "Deterministic" : llmInvoked ? "AI" : "Deterministic");
      var tier =
        t.routing_tier ||
        (execution && execution.tier) ||
        (isDeterministic ? "T0" : "");
      if (!tier || tier === "?" || tier === "T?") {
        tier = isDeterministic ? "T0" : String((execution && execution.tier) || "T2");
        if (!String(tier).startsWith("T")) tier = isDeterministic ? "T0" : "T2";
      }
      var src = String(t.usage_source || "");
      var total = isDeterministic
        ? 0
        : t.total_ai_tokens != null
          ? t.total_ai_tokens
          : t.total_tokens;
      var usageUnavailable =
        !isDeterministic &&
        (src === "unavailable" || (total == null && (src === "estimate" || src === "")));
      var totalLabel = total == null ? "—" : String(total);
      if (usageUnavailable) {
        totalLabel = "usage unavailable";
      } else if (!isDeterministic && src === "estimate") {
        totalLabel = total == null ? "usage unavailable" : totalLabel + " (est.)";
      }
      var tools = Array.isArray(t.tools_used) ? t.tools_used.join(", ") : "";
      var provider = isDeterministic
        ? "None"
        : t.provider != null && t.provider !== ""
          ? t.provider
          : "None";
      var model = isDeterministic
        ? "None"
        : t.model != null && t.model !== ""
          ? t.model
          : "None";
      var routePath =
        t.route_path ||
        (execution && execution.route_path) ||
        (execType === "Hybrid" && provider !== "None"
          ? "T0 → " + provider + (model !== "None" ? "/" + model : "")
          : "");
      var inTok = usageUnavailable
        ? "—"
        : String(isDeterministic ? 0 : t.input_tokens != null ? t.input_tokens : 0);
      var outTok = usageUnavailable
        ? "—"
        : String(isDeterministic ? 0 : t.output_tokens != null ? t.output_tokens : 0);
      var cachedTok = usageUnavailable
        ? "—"
        : String(isDeterministic ? 0 : t.cached_tokens != null ? t.cached_tokens : 0);      var ri =
        (t.repository_intelligence && typeof t.repository_intelligence === "object"
          ? t.repository_intelligence
          : execution && execution.repository_intelligence_diagnostics) || null;
      var riRepos = ri && Array.isArray(ri.repositories) ? ri.repositories : [];
      var riDetails = riRepos.map(function (item) {
        return (
          String(item.repository_id || "") +
          " @ " +
          String(item.indexed_commit || "-").slice(0, 12) +
          " / HEAD " +
          String(item.current_commit || "-").slice(0, 12)
        );
      }).join(", ");
      var riFreshness = ri ? ri.freshness : "";
      if (ri && ri.used && (!riFreshness || riFreshness === "not_learned")) {
        riFreshness = "current";
      }
      if (ri && !riFreshness) {
        riFreshness =
          (ri.repository_ids && ri.repository_ids.length) ||
          (ri.requested_repository_ids && ri.requested_repository_ids.length)
            ? "not_learned"
            : "none";
      }
      return (
        '<div class="ad-telemetry muted">' +
        "<div>Tier: " +
        escapeHtml(String(tier)) +
        " · Type: " +
        escapeHtml(String(execType)) +
        " · LLM: " +
        escapeHtml(llm) +
        (routePath
          ? " · Route: " + escapeHtml(String(routePath))
          : "") +
        "</div>" +
        "<div>Provider: " +
        escapeHtml(String(provider)) +
        " · Model: " +
        escapeHtml(String(model)) +
        "</div>" +
        "<div>Tokens in/out/cached/total: " +
        escapeHtml(inTok) +
        "/" +
        escapeHtml(outTok) +
        "/" +
        escapeHtml(cachedTok) +
        "/" +
        escapeHtml(totalLabel) +
        "</div>" +
        "<div>Tools: " +
        escapeHtml(tools || "None") +
        " · Runtime: " +
        escapeHtml(String(t.runtime_ms != null ? t.runtime_ms : "—")) +
        " ms · Child run: " +
        escapeHtml(String(isDeterministic ? "None" : t.child_ai_run_id || "None")) +
        "</div>" +
        formatToolRuntimeFeedHtml(execution) +
        (t.t0_failure_reason || t.next_capability || t.db_query_attempted || t.ai_escalation_occurred
          ? "<div>T0 failure: " +
            escapeHtml(String(t.t0_failure_reason || "None")) +
            " · Next: " +
            escapeHtml(String(t.next_capability || "None")) +
            " · DB query: " +
            escapeHtml(t.db_query_attempted ? "Yes" : "No") +
            " · AI escalate: " +
            escapeHtml(t.ai_escalation_occurred ? "Yes" : "No") +
            "</div>"
          : "") +
        (t.interaction_mode ||
        (execution && execution.interaction_mode) ||
        t.routing_mode ||
        (execution && execution.routing_mode) ||
        t.session_reused != null ||
        (execution && execution.session_reused) ||
        (Array.isArray(t.context_items) && t.context_items.length) ||
        (execution &&
          Array.isArray(execution.context_items) &&
          execution.context_items.length) ||
        t.context_chars != null ||
        (execution && execution.context_chars != null)
          ? "<div>Mode: " +
            escapeHtml(String(t.interaction_mode || (execution && execution.interaction_mode) || t.routing_mode || (execution && execution.routing_mode) || "smart")) +
            " · Selected: " +
            escapeHtml(
              String(
                (execution &&
                  (execution.selected_provider || execution.provider_id || "")) ||
                  provider
              )
            ) +
            ((execution && (execution.selected_model || execution.resolved_model)) ||
            model !== "None"
              ? " / " +
                escapeHtml(
                  String(
                    (execution &&
                      (execution.selected_model ||
                        execution.resolved_model ||
                        execution.model)) ||
                      model
                  )
                )
              : "") +
            " · Resolved: " +
            escapeHtml(
              String(
                (execution &&
                  (execution.resolved_provider ||
                    execution.adapter_id ||
                    execution.provider_id)) ||
                  provider
              )
            ) +
            ((execution && (execution.resolved_model || execution.model)) || model !== "None"
              ? " / " +
                escapeHtml(
                  String(
                    (execution && (execution.resolved_model || execution.model)) || model
                  )
                )
              : "") +
            " · Session reused: " +
            escapeHtml(
              t.session_reused || (execution && execution.session_reused) ? "Yes" : "No"
            ) +
            " · Context items: " +
            escapeHtml(
              Array.isArray(t.context_items) && t.context_items.length
                ? t.context_items.slice(0, 6).join(", ")
                : Array.isArray(execution && execution.context_items) &&
                    execution.context_items.length
                  ? execution.context_items.slice(0, 6).join(", ")
                  : "None"
            ) +
            (t.context_chars != null || (execution && execution.context_chars != null)
              ? " · Context chars: " +
                escapeHtml(
                  String(
                    t.context_chars != null
                      ? t.context_chars
                      : execution.context_chars
                  )
                )
              : "") +
            "</div>"
          : "") +
        (ri
          ? "<div>Repository Intelligence used: " +
            escapeHtml(ri.used ? "Yes" : "No") +
            " · Repository: " +
            escapeHtml(riDetails || (ri.repository_ids || []).join(", ") || "None") +
            " · Entries: " +
            escapeHtml(String(ri.knowledge_entries_used || 0)) +
            " · Status: " +
            escapeHtml(String(riFreshness || "none")) +
            " · Context chars: " +
            escapeHtml(String(ri.context_chars_contributed || 0)) +
            "</div>"
          : "") +
        "</div>"
      );
    }

    function formatGroundingLine(execution) {
      var g = (execution && execution.grounding) || {};
      // Prefer the execution grounding object as the single source of truth.
      var evidence =
        g.evidence_found_label != null
          ? g.evidence_found_label
          : g.evidence_found == null
            ? null
            : g.evidence_found
              ? "Yes"
              : "No";
      var solved =
        g.task_solved_label != null
          ? g.task_solved_label
          : g.task_solved == null
            ? null
            : g.task_solved
              ? "Yes"
              : "No";
      var grounded =
        g.grounded_label != null
          ? g.grounded_label
          : g.grounded == null
            ? null
            : g.grounded
              ? "Yes"
              : "No";
      if (evidence == null && solved == null && grounded == null) {
        return "";
      }
      var sources =
        (Array.isArray(g.sources_used) && g.sources_used.length
          ? g.sources_used.join(", ")
          : "") ||
        g.source ||
        "selected context";
      return (
        '<div class="ad-source">' +
        "Evidence Found: " +
        escapeHtml(String(evidence != null ? evidence : "No")) +
        " · Task Solved: " +
        escapeHtml(String(solved != null ? solved : "No")) +
        " · Grounded: " +
        escapeHtml(String(grounded != null ? grounded : "No")) +
        "<br>Sources used: " +
        escapeHtml(String(sources)) +
        (g.grounded || !g.reason ? "" : " — " + escapeHtml(String(g.reason))) +
        "</div>"
      );
    }

    function renderRouteExecution(execution) {
      if (!execution) return;
      var status = execution.status || "";
      var label =
        escapeHtml(execution.provider_id || "") +
        (execution.fallback_from
          ? " (fallback from " + escapeHtml(execution.fallback_from) + ")"
          : "");
      if (status === "completed" || status === "failed") {
        var gLine = formatGroundingLine(execution);
        var answerText = String(
          execution.answer ||
            execution.partial_summary ||
            execution.error ||
            "(no answer)"
        );
        // Prefer the structured grounding object — strip duplicated footer from answer body.
        if (gLine && /Evidence Found:/i.test(answerText)) {
          answerText = answerText.replace(
            /\n?—?\n?Evidence Found:[\s\S]*$/i,
            ""
          ).trim();
        }
        appendMessage(
          "assistant",
          "<strong>Route " +
            escapeHtml(status === "failed" ? "failed" : "complete") +
            "</strong> · " +
            label +
            '<pre class="ad-run-answer">' +
            escapeHtml(answerText || "(no answer)") +
            "</pre>" +
            gLine +
            formatUsageTelemetryHtml(execution)
        );
        setRunControls(status === "failed" ? "failed" : "idle");
        activeRouteExecutionId = null;
        stopRoutePoll();
        return;
      }
      if (status === "cancelled") {
        appendMessage("assistant", "Route execution cancelled.");
        setRunControls("idle");
        activeRouteExecutionId = null;
        stopRoutePoll();
        return;
      }
      if (status === "paused_for_approval" || status === "paused") {
        appendMessage(
          "assistant",
          "Paused for action approval: " +
            escapeHtml(
              execution.error ||
                execution.stopped_reason ||
                "A requested tool/action requires confirmation (not the provider)."
            )
        );
        setRunControls("idle");
        activeRouteExecutionId = null;
        stopRoutePoll();
        return;
      }
      if (status === "timed_out") {
        appendMessage(
          "assistant",
          "Route timed out: " + escapeHtml(execution.error || execution.stopped_reason || "timeout")
        );
        setRunControls("failed");
        activeRouteExecutionId = null;
        stopRoutePoll();
        return;
      }
      if (
        status === "failed" ||
        status === "unavailable" ||
        status === "blocked"
      ) {
        appendMessage(
          "assistant",
          "Route " +
            escapeHtml(status === "blocked" ? "failed" : status) +
            ": " +
            escapeHtml(execution.error || execution.stopped_reason || "unknown error")
        );
        setRunControls("failed");
        activeRouteExecutionId = null;
        stopRoutePoll();
        return;
      }
      if (isRouteActiveStatus(status)) {
        setRunControls("running");
        if (execution.agent_run_id) {
          activeRunId = execution.agent_run_id;
        }
        return;
      }
      // Unknown status — never leave the spinner spinning.
      appendMessage(
        "assistant",
        "Route ended with status: " + escapeHtml(status || "unknown")
      );
      setRunControls("idle");
      activeRouteExecutionId = null;
      stopRoutePoll();
    }

    function pollRouteExecution(executionId) {
      stopRoutePoll();
      var base = smartRouting.status_url || "/api/assistants/airix/routing/status";
      var url = base.indexOf("?") >= 0 ? base : base + "/" + encodeURIComponent(executionId);
      if (base.indexOf("/status") >= 0 && base.slice(-7) === "/status") {
        url = base + "/" + encodeURIComponent(executionId);
      }
      routePollTimer = setTimeout(function () {
        fetch(url, { headers: { Accept: "application/json" }, credentials: "same-origin" })
          .then(function (r) {
            return r.json().then(function (data) {
              return { ok: r.ok, data: data };
            });
          })
          .then(function (res) {
            if (!res.ok || !res.data || !res.data.execution) {
              setRunControls("failed");
              activeRouteExecutionId = null;
              stopRoutePoll();
              appendMessage("assistant", "Lost route execution status.");
              return;
            }
            var ex = res.data.execution;
            if (isRouteActiveStatus(ex.status)) {
              pollRouteExecution(executionId);
              return;
            }
            renderRouteExecution(ex);
          })
          .catch(function () {
            setRunControls("failed");
            activeRouteExecutionId = null;
            stopRoutePoll();
            appendMessage("assistant", "Could not poll route status.");
          });
      }, 900);
    }

    function executeDirectAgent(opts) {
      opts = opts || {};
      var url = smartRouting.execute_url;
      if (!url) {
        appendMessage("assistant", "Route execute URL unavailable.");
        return;
      }
      var prompt = pendingPrompt || lastPrompt || "";
      if (!prompt) {
        appendMessage("assistant", "No prompt to execute.");
        return;
      }
      if (!selectedAgent) {
        appendMessage("assistant", "Select a provider for Agent mode.");
        return;
      }
      if (
        (selectedAgent === "codex" ||
          selectedAgent === "claude-code" ||
          selectedAgent === "cursor-agent") &&
        !opts.approveCodex
      ) {
        var ok = window.confirm(
          "Direct Agent will run " +
            selectedAgent +
            " with your selected model. Continue?"
        );
        if (!ok) {
          appendMessage("assistant", "Direct Agent run cancelled.");
          return;
        }
        opts.approveCodex = true;
      }
      hideRoutingCard();
      setRunControls("running");
      appendMessage(
        "assistant",
        "Direct Agent — Efficient: selected provider/model <strong>" +
          escapeHtml(selectedAgent) +
          "</strong>" +
          (currentSelectedModel()
            ? " · <strong>" + escapeHtml(currentSelectedModel()) + "</strong>"
            : " · (provider default)") +
          " · routing bypassed · lightweight context prep only…"
      );
      var repoFields = repositoryPayloadFields();
      if (agentRequiresRepository(selectedAgent) && !(repoFields.repository_ids || []).length) {
        if (!assertRepositoryReady(selectedAgent)) {
          setRunControls("idle");
          return;
        }
        repoFields = repositoryPayloadFields();
      }
      var fp = contextFingerprint(prompt);
      var reusableConversation =
        prefs.direct_session_fingerprint === fp ? prefs.direct_conversation_id || null : null;
      var reused = !!reusableConversation;
      fetch(url, {
        method: "POST",
        headers: { "Content-Type": "application/json", Accept: "application/json" },
        credentials: "same-origin",
        body: JSON.stringify({
          prompt: prompt,
          approve_codex: !!opts.approveCodex,
          agent_override: selectedAgent,
          force: !!opts.force,
          repository_ids: repoFields.repository_ids,
          selected_repository_id: repoFields.selected_repository_id,
          active_repository_id: repoFields.active_repository_id,
          model: currentSelectedModel() || null,
          routing_mode: "direct",
          interaction_mode: "agent",
          conversation_id: reusableConversation,
          context_fingerprint: fp,
          context_sources: currentContextSources(),
          dhis2_environment: (contextDhis2Env && contextDhis2Env.value) || prefs.dhis2_environment || null,
        }),
      })
        .then(function (r) {
          return r.json().then(function (data) {
            return { ok: r.ok, status: r.status, data: data };
          });
        })
        .then(function (res) {
          if (!res.ok || !res.data || !res.data.ok) {
            var detail =
              (res.data && res.data.error) ||
              ((res.data && res.data.execution) || {}).error ||
              "Direct Agent execution failed.";
            appendMessage("assistant", escapeHtml(detail));
            setRunControls("failed");
            return;
          }
          var exec = (res.data && res.data.execution) || {};
          if (exec.conversation_id) {
            prefs.direct_conversation_id = exec.conversation_id;
            prefs.direct_session_fingerprint = fp;
            persistPrefs();
          }
          if (exec.id) {
            activeRouteExecutionId = exec.id;
            renderRouteExecution(exec);
            if (!isRouteActiveStatus(exec.status)) {
              // already terminal
            } else {
              pollRouteExecution(exec.id);
            }
          } else {
            renderRouteExecution(exec);
            setRunControls("idle");
          }
          // Surface session reuse note once when starting.
          if (reused || exec.session_reused) {
            appendMessage(
              "assistant",
              "Session reused: Yes · provider/model preserved for this Direct Agent thread."
            );
          }
        })
        .catch(function (err) {
          var msg =
            (err && err.message) ||
            "Direct Agent request failed (network or server error).";
          appendMessage("assistant", escapeHtml(String(msg)));
          setRunControls("failed");
        });
    }

    function executeRecommendedRoute(rec, opts) {
      opts = opts || {};
      var url = smartRouting.execute_url;
      if (!url) {
        appendMessage("assistant", "Route execute URL unavailable.");
        return;
      }
      var prompt = pendingPrompt || lastPrompt || "";
      if (!prompt) {
        appendMessage("assistant", "No prompt to execute.");
        return;
      }
      if (rec && rec.approval_required && !opts.approveCodex) {
        var ok = window.confirm(
          "This route includes an action that requires confirmation. Continue?"
        );
        if (!ok) {
          appendMessage("assistant", "Action approval declined — adjust the request or cancel.");
          return;
        }
        opts.approveCodex = true;
      }
      hideRoutingCard();
      setRunControls("running");
      var routeAgent = mapRoutingAgent((rec && rec.recommended_agent) || "");
      var overrideAgent = opts.agentOverride || null;
      appendMessage(
        "assistant",
        overrideAgent
          ? "Executing with selected provider: <strong>" +
              escapeHtml(overrideAgent) +
              "</strong>…"
          : "Executing recommended route: <strong>" +
              escapeHtml((rec && (rec.recommended_label || rec.recommended_agent)) || "") +
              "</strong>…"
      );
      var modelForRoute = "";
      // Preserve UI model only when it belongs to the provider about to run.
      if (overrideAgent) {
        if (!routeAgent || overrideAgent === selectedAgent || overrideAgent === routeAgent) {
          modelForRoute = currentSelectedModel();
        }
      } else if (routeAgent && routeAgent === selectedAgent) {
        modelForRoute = currentSelectedModel();
      }
      if (opts.model) modelForRoute = opts.model;
      if (!modelForRoute && rec && rec.recommended_model) {
        modelForRoute = rec.recommended_model;
      }
      var repoFields = repositoryPayloadFields();
      var interactionMode = opts.interactionMode || currentInteractionMode();
      var routeNeedsRepo =
        agentRequiresRepository(overrideAgent || routeAgent || mapRoutingAgent((rec && rec.recommended_agent) || ""));
      if (routeNeedsRepo && !(repoFields.repository_ids || []).length) {
        if (!assertRepositoryReady(overrideAgent || routeAgent || "codex")) {
          setRunControls("idle");
          return;
        }
        repoFields = repositoryPayloadFields();
      }
      fetch(url, {
        method: "POST",
        headers: { "Content-Type": "application/json", Accept: "application/json" },
        credentials: "same-origin",
        body: JSON.stringify({
          prompt: prompt,
          approve_codex: !!opts.approveCodex,
          agent_override: overrideAgent,
          force: !!opts.force,
          repository_ids: opts.repositoryIds || repoFields.repository_ids,
          selected_repository_id: repoFields.selected_repository_id,
          active_repository_id: repoFields.active_repository_id,
          model: modelForRoute || null,
          routing_mode: interactionMode === "agent" ? "direct" : "smart",
          interaction_mode: interactionMode,
          context_sources: currentContextSources(),
          dhis2_environment: (contextDhis2Env && contextDhis2Env.value) || prefs.dhis2_environment || null,
          conversation_id: prefs.direct_conversation_id || null,
          context_fingerprint: contextFingerprint(prompt),
        }),
      })
        .then(function (r) {
          return r.json().then(function (data) {
            return { ok: r.ok, status: r.status, data: data };
          });
        })
        .then(function (res) {
          if (!res.ok || !res.data || !res.data.ok) {
            var code = (res.data && res.data.code) || "";
            var exec = (res.data && res.data.execution) || {};
            var orch = (res.data && res.data.orchestration) || {};
            // Prefer detailed route/orchestration errors over a bare "failed".
            var detail =
              (res.data && res.data.error) ||
              exec.error ||
              orch.stopped_reason ||
              exec.stopped_reason ||
              "";
            if (code === "approval_required" || exec.status === "paused_for_approval") {
              appendMessage(
                "assistant",
                escapeHtml(
                  detail ||
                    "Action approval required for a requested tool/write (not the selected provider)."
                )
              );
              setRunControls("idle");
              return;
            }
            if (code === "repository_required" || code === "repository_unavailable") {
              appendMessage(
                "assistant",
                escapeHtml(detail || "Select a connected repository before running this agent.")
              );
              if (repoSel) repoSel.focus();
              setRunControls("idle");
              return;
            }
            if (code === "permission_denied") {
              appendMessage(
                "assistant",
                escapeHtml(detail || "Permission denied.")
              );
              setRunControls("idle");
              return;
            }
            if (code === "budget_exceeded" || /budget exceeded/i.test(String(detail))) {
              appendMessage(
                "assistant",
                "Route blocked by budget: " +
                  escapeHtml(detail || "AI budget exceeded.") +
                  " Raise Daily token budget in Settings (Work) or ⋯ → Smart Routing, or wait until tomorrow."
              );
              setRunControls("failed");
              return;
            }
            if (code === "duplicate_execution") {
              appendMessage(
                "assistant",
                "An execution for this prompt is already active. Cancel it first or wait."
              );
              setRunControls("idle");
              return;
            }
            // HTTP 200 with ok:false still carries a finished execution — render it.
            if (exec && exec.status && exec.status !== "running" && exec.status !== "queued") {
              activeRouteExecutionId = exec.id || null;
              renderRouteExecution(exec);
              if (detail && exec.status === "failed") {
                // renderRouteExecution already shows error; ensure budget/detail visible.
              }
              return;
            }
            appendMessage(
              "assistant",
              escapeHtml(detail || "Route execution failed.")
            );
            setRunControls("failed");
            return;
          }
          var execution = res.data.execution || {};
          activeRouteExecutionId = execution.id || null;
          if (execution.agent_run_id) activeRunId = execution.agent_run_id;
          if (isRouteActiveStatus(execution.status)) {
            appendMessage("assistant", "Route running…");
            if (activeRouteExecutionId) pollRouteExecution(activeRouteExecutionId);
            else setRunControls("running");
            return;
          }
          renderRouteExecution(execution);
        })
        .catch(function () {
          appendMessage("assistant", "Could not execute route.");
          setRunControls("failed");
        });
    }

    function cancelRouteExecution() {
      stopRoutePoll();
      if (!activeRouteExecutionId || !smartRouting.cancel_url) {
        hideRoutingCard();
        pendingPrompt = "";
        appendMessage("assistant", "Smart Routing cancelled.");
        setRunControls("idle");
        return;
      }
      fetch(smartRouting.cancel_url, {
        method: "POST",
        headers: { "Content-Type": "application/json", Accept: "application/json" },
        credentials: "same-origin",
        body: JSON.stringify({ execution_id: activeRouteExecutionId }),
      })
        .then(function (r) {
          return r.json();
        })
        .then(function (data) {
          activeRouteExecutionId = null;
          hideRoutingCard();
          appendMessage("assistant", "Route execution cancelled.");
          setRunControls("idle");
          if (data && data.execution) renderRouteExecution(data.execution);
        })
        .catch(function () {
          activeRouteExecutionId = null;
          setRunControls("idle");
          appendMessage("assistant", "Cancel requested.");
        });
    }

    function requestRoute(prompt) {
      var url = smartRouting.recommend_url;
      if (!url) return Promise.reject(new Error("routing unavailable"));
      var repoFields = repositoryPayloadFields();
      return fetch(url, {
        method: "POST",
        headers: { "Content-Type": "application/json", Accept: "application/json" },
        credentials: "same-origin",
        body: JSON.stringify({
          prompt: prompt,
          repository_ids: repoFields.repository_ids,
        }),
      }).then(function (r) {
        return r.json().then(function (data) {
          if (!r.ok || !data || !data.ok) {
            throw new Error((data && data.error) || "Routing failed");
          }
          return data;
        });
      });
    }

    function loadRoutingSettings() {
      if (!smartRouting.settings_url || !routingSettings) return;
      fetch(smartRouting.settings_url, {
        headers: { Accept: "application/json" },
        credentials: "same-origin",
      })
        .then(function (r) {
          return r.json();
        })
        .then(function (data) {
          if (!data || !data.settings) return;
          var s = data.settings;
          var mode = $("ad-routing-mode");
          if (mode) mode.value = s.mode || "balanced";
          var det = $("ad-routing-pref-det");
          if (det) det.checked = !!s.prefer_deterministic;
          var grok = $("ad-routing-pref-grok");
          if (grok) grok.checked = !!s.prefer_grok_for_routine;
          var codex = $("ad-routing-pref-codex");
          if (codex) codex.checked = !!s.require_approval_before_codex;
          var esc = $("ad-routing-pref-esc");
          if (esc) esc.checked = !!s.allow_escalation;
          var hist = $("ad-routing-pref-hist");
          if (hist) hist.checked = s.use_history !== false;
          var retries = $("ad-routing-retries");
          if (retries) retries.value = String(s.max_retries != null ? s.max_retries : 2);
        })
        .catch(function () {});
    }

    function isT0DeterministicRecommendation(rec) {
      if (!rec) return false;
      return (
        rec.recommended_agent === "deterministic" ||
        rec.recommended_tier === "T0"
      );
    }

    function sendPrompt(text, opts) {
      opts = opts || {};
      var prompt = String(text || "").trim();
      if (!prompt) return;

      // Direct Agent — Efficient: skip Smart Routing recommend / T0 / escalation.
      if (routingEnabled && currentRoutingMode() === "direct" && !opts.forceManual) {
        pendingPrompt = prompt;
        lastPrompt = prompt;
        if (!opts.suppressUserBubble) {
          appendMessage("user", escapeHtml(prompt));
        }
        if (promptEl) promptEl.value = "";
        if (!selectedAgent) {
          appendMessage(
            "assistant",
            "Direct Agent requires a selected provider. Pick one, then send again."
          );
          ensureAgents();
          return;
        }
        if (!assertRepositoryReady(selectedAgent)) {
          return;
        }
        executeDirectAgent({});
        return;
      }

      // skipRoutingOnce skips Smart Routing recommend once only — lifecycle
      // polling / terminal handling still always runs for the AgentCenter child.
      if (routingEnabled && !skipRoutingOnce && !opts.forceManual) {
        pendingPrompt = prompt;
        lastPrompt = prompt;
        appendMessage("user", escapeHtml(prompt));
        if (promptEl) promptEl.value = "";
        setRunControls("running");
        appendMessage("assistant", "Analyzing prompt for Smart Routing…");
        requestRoute(prompt)
          .then(function (data) {
            var rec = data.recommendation || {};
            pendingRoute = rec;
            pendingPlan = data.plan || null;
            var interactionMode = currentInteractionMode();
            if (interactionMode !== "smart") {
              setRunControls("idle");
              executeRecommendedRoute(rec, {
                interactionMode: interactionMode,
                agentOverride: selectedAgent || null,
                model: currentSelectedModel() || null,
              });
              return;
            }
            if (isT0DeterministicRecommendation(rec)) {
              setRunControls("idle");
              appendMessage(
                "assistant",
                "T0 tools can answer this — executing deterministic route directly…"
              );
              executeRecommendedRoute(rec, {});
              return;
            }
            setRunControls("idle");
            showRoutingCard(rec, data.plan || null);
            // Do NOT overwrite the user's agent/model selection with the
            // recommendation — execution requires explicit acceptance
            // (Use Recommended) or Choose Agent with the current selection.
            if (routingBody) {
              var keepNote = document.createElement("p");
              keepNote.className = "ad-routing-note muted";
              keepNote.textContent =
                "Your selected provider stays unchanged until you press Use Recommended or Choose Agent.";
              routingBody.appendChild(keepNote);
            }
          })
          .catch(function () {
            setRunControls("idle");
            appendMessage(
              "assistant",
              "Smart Routing unavailable — select an agent, then use Choose Agent / Send."
            );
            if (promptEl) promptEl.value = pendingPrompt;
          });
        return;
      }

      skipRoutingOnce = false;
      lastPrompt = prompt;
      setRunControls("running");
      if (!opts.suppressUserBubble) {
        appendMessage("user", escapeHtml(prompt));
      }
      if (promptEl) promptEl.value = "";
      if (!selectedAgent) {
        appendMessage(
          "assistant",
          "Select an available provider first. Unavailable CLIs (for example Cursor Agent without `agent` on PATH) are disabled."
        );
        ensureAgents();
        setRunControls("idle");
        return;
      }
      var selectedOpt =
        agentSel &&
        Array.prototype.find.call(agentSel.options || [], function (o) {
          return o.value === selectedAgent;
        });
      if (selectedOpt && selectedOpt.getAttribute("data-runnable") === "0") {
        appendMessage(
          "assistant",
          "Provider <strong>" +
            escapeHtml(selectedAgent) +
            "</strong> is unavailable. " +
            "Install its CLI or pick OpenAI / Grok / Codex / Hub Simulator. " +
            "Cursor Agent needs `agent` or `cursor-agent` on PATH (the IDE <code>cursor</code> binary is not valid)."
        );
        setRunControls("idle");
        return;
      }
      if (!assertRepositoryReady(selectedAgent)) {
        setRunControls("idle");
        return;
      }
      var repoFields = repositoryPayloadFields();
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
          model: currentSelectedModel(),
          tools: profile.default_tools || [],
          repository_ids: repoFields.repository_ids,
          selected_repository_id: repoFields.selected_repository_id,
          active_repository_id: repoFields.active_repository_id,
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
          var run = unwrapAgentRun(res.body) || {};
          activeRunId = run.id || null;
          if (isAgentRunTerminal(run.status) || run.answer) {
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
      if (routingModeSel) {
        routingModeSel.value = prefs.interaction_mode || (prefs.routing_mode === "direct" ? "agent" : "smart");
        routingModeSel.addEventListener("change", function () {
          setRoutingMode(routingModeSel.value);
        });
      }
      var closeBtn = $("ad-close");
      if (closeBtn) closeBtn.addEventListener("click", function () { setOpen(false); });
      if (backdrop) backdrop.addEventListener("click", function () { setOpen(false); });
      var maximizeBtn = $("ad-maximize");
      if (maximizeBtn) {
        maximizeBtn.addEventListener("click", function () {
          setMaximized(!maximized);
        });
      }
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
          prefs.selected_agent_id = selectedAgent;
          if (selectedAgent) loadModels(selectedAgent);
          else {
            selectedModel = "";
            prefs.selected_model_id = "";
            if (modelSel) modelSel.hidden = true;
          }
          ensureRepositories();
          persistPrefs();
        });
      }
      if (modelSel) {
        modelSel.addEventListener("change", function () {
          selectedModel = modelSel.value;
          prefs.selected_model_id = selectedModel;
          persistPrefs();
        });
      }
      if (repoSel) {
        repoSel.addEventListener("change", function () {
          setSelectedRepository(repoSel.value, true);
        });
      }
      var settings = $("ad-settings");
      var history = $("ad-history");
      function goCenter() {
        setMenuOpen(false);
        window.location.href = boot.center_url || "/work/airix";
      }
      if (settings) settings.addEventListener("click", goCenter);
      if (history) history.addEventListener("click", goCenter);
      var routingOpen = $("ad-routing-settings-open");
      if (routingOpen) {
        routingOpen.addEventListener("click", function () {
          setMenuOpen(false);
          if (routingSettings) {
            routingSettings.hidden = false;
            loadRoutingSettings();
          }
        });
      }
      var routingClose = $("ad-routing-settings-close");
      if (routingClose) {
        routingClose.addEventListener("click", function () {
          if (routingSettings) routingSettings.hidden = true;
        });
      }
      var routingForm = $("ad-routing-settings-form");
      if (routingForm && smartRouting.settings_url) {
        routingForm.addEventListener("submit", function (ev) {
          ev.preventDefault();
          var payload = {
            mode: ($("ad-routing-mode") && $("ad-routing-mode").value) || "balanced",
            prefer_deterministic: !!($("ad-routing-pref-det") && $("ad-routing-pref-det").checked),
            prefer_grok_for_routine: !!(
              $("ad-routing-pref-grok") && $("ad-routing-pref-grok").checked
            ),
            require_approval_before_codex: !!(
              $("ad-routing-pref-codex") && $("ad-routing-pref-codex").checked
            ),
            allow_escalation: !!(
              $("ad-routing-pref-esc") && $("ad-routing-pref-esc").checked
            ),
            use_history: !!(
              $("ad-routing-pref-hist") && $("ad-routing-pref-hist").checked
            ),
            max_retries: Number(($("ad-routing-retries") && $("ad-routing-retries").value) || 2),
          };
          fetch(smartRouting.settings_url, {
            method: "PUT",
            headers: { "Content-Type": "application/json", Accept: "application/json" },
            credentials: "same-origin",
            body: JSON.stringify(payload),
          })
            .then(function (r) {
              return r.json();
            })
            .then(function (data) {
              if (data && data.ok) {
                appendMessage("assistant", "Smart Routing settings saved.");
                if (routingSettings) routingSettings.hidden = true;
              }
            })
            .catch(function () {});
        });
      }
      var analyticsBtn = $("ad-routing-analytics-btn");
      var analyticsEl = $("ad-routing-analytics");
      if (analyticsBtn && smartRouting.analytics_url) {
        analyticsBtn.addEventListener("click", function () {
          fetch(smartRouting.analytics_url, {
            headers: { Accept: "application/json" },
            credentials: "same-origin",
          })
            .then(function (r) {
              return r.json();
            })
            .then(function (data) {
              if (!analyticsEl || !data || !data.analytics) return;
              var a = data.analytics;
              analyticsEl.hidden = false;
              analyticsEl.textContent =
                "Executions: " +
                (a.executions_total || 0) +
                "\nSuccess rate: " +
                (a.success_rate != null ? Math.round(a.success_rate * 100) + "%" : "—") +
                "\nRetries: " +
                (a.retries_total || 0) +
                "\nEscalations: " +
                (a.escalations_total || 0) +
                "\nAvg runtime: " +
                (a.average_runtime_ms != null ? a.average_runtime_ms + " ms" : "—") +
                "\nEst tokens: " +
                (a.estimated_tokens_total || 0) +
                "\nActual tokens: " +
                (a.actual_tokens_total || 0) +
                "\nT0 LLM avoided: " +
                (a.t0_llm_avoided || 0) +
                "\nFindings reused: " +
                (a.prior_findings_reused || 0) +
                "\nPermission blocked: " +
                (a.permission_blocked || 0) +
                "\nRBAC: " +
                ((a.permissions && a.permissions.role_id) || "—") +
                "\nBy tier: " +
                JSON.stringify(a.executions_by_tier || {}) +
                "\nBy provider: " +
                JSON.stringify(a.executions_by_provider || {});
            })
            .catch(function () {});
        });
      }
      var useRec = $("ad-routing-use");
      if (useRec) {
        useRec.addEventListener("click", function () {
          // Explicit acceptance of the Smart Routing recommendation.
          applyRecommendedAgent(pendingRoute);
          executeRecommendedRoute(pendingRoute, {});
        });
      }
      var chooseAgent = $("ad-routing-choose");
      if (chooseAgent) {
        chooseAgent.addEventListener("click", function () {
          ensureAgents();
          var prompt = pendingPrompt || lastPrompt || "";
          if (!prompt) {
            appendMessage("assistant", "No pending prompt for manual override.");
            return;
          }
          if (!selectedAgent) {
            skipRoutingOnce = true;
            if (promptEl) promptEl.value = prompt;
            if (agentSel) agentSel.focus();
            appendMessage(
              "assistant",
              "Select an agent in the dropdown, then Send once (routing stays skipped for that send)."
            );
            return;
          }
          // Explicit manual override — preserve selected provider/model; do not
          // substitute the recommendation (and never Hub Simulator unless chosen).
          var rec = pendingRoute || {
            recommended_agent: selectedAgent,
            recommended_label: selectedAgent,
            approval_required: /^(codex|claude-code|cursor-agent)$/.test(selectedAgent),
          };
          appendMessage(
            "assistant",
            "Manual override — running with <strong>" +
              escapeHtml(selectedAgent) +
              "</strong>…"
          );
          executeRecommendedRoute(rec, {
            agentOverride: selectedAgent,
            model: currentSelectedModel() || null,
            approveCodex: true,
          });
        });
      }
      var cancelRoute = $("ad-routing-cancel");
      if (cancelRoute) {
        cancelRoute.addEventListener("click", function () {
          cancelRouteExecution();
        });
      }
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
          if (maximized) {
            maximizedWidth = Math.max(
              prefs.min_width,
              Math.min(computeMaximizedWidth(), width)
            );
          } else {
            prefs.width = Math.max(prefs.min_width, Math.min(prefs.max_width, width));
          }
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

    initializeContextControls();
    bind();
    applyChrome();
    applyRoutingModeChrome();
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
      setMaximized: setMaximized,
    };
  }

  document.addEventListener("DOMContentLoaded", function () {
    var host = $("assistant-dock-host");
    if (!host) return;
    window.__assistantDock = createDockController(host);
    window.HubAssistantDock = window.__assistantDock;
  });
})();
