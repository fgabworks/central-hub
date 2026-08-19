(function () {
  "use strict";
  var shell = document.getElementById("climate-shell");
  if (!shell) return;
  var bootstrap = JSON.parse(shell.getAttribute("data-bootstrap") || "{}");
  var workspace = shell.getAttribute("data-workspace");
  var CLIMATE_SURFACE = shell.getAttribute("data-surface") || "workspace";
  var apiRoot = shell.getAttribute("data-api-root");
  var repoSelect = document.getElementById("climate-repository");
  var branchSelect = document.getElementById("climate-branch");
  var workbench = document.getElementById("climate-workbench");
  var center = workbench.querySelector(".climate-center");
  var treeEl = document.getElementById("climate-tree");
  var tabsEl = document.getElementById("climate-tabs");
  var fallback = document.getElementById("climate-editor-fallback");
  var welcome = document.getElementById("climate-welcome");
  var monacoHost = document.getElementById("climate-monaco");
  var mdPreview = document.getElementById("climate-md-preview");
  var fileUnavailable = document.getElementById("climate-file-unavailable");
  var fileEmptyEl = document.getElementById("climate-file-empty");
  var filePathEl = document.getElementById("climate-file-path");
  var fileModes = document.getElementById("climate-file-modes");
  var fileReadonly = document.getElementById("climate-file-readonly");
  var statusEl = document.getElementById("climate-status");
  var gitSummary = document.getElementById("climate-git-summary");
  var bottomBody = document.getElementById("climate-bottom-body");
  var feed = document.getElementById("climate-ai-feed");
  var providerSelect = document.getElementById("climate-provider");
  var modelSelect = document.getElementById("climate-model");
  var panelProviderSelect = document.getElementById("climate-provider-panel");
  var panelModelSelect = document.getElementById("climate-model-panel");
  var executionModeSelect = document.getElementById("climate-execution-mode");
  var executionModePill = document.getElementById("climate-mode-pill");
  var providerCards = document.getElementById("climate-provider-cards");
  var breadcrumb = document.getElementById("climate-breadcrumb");
  var providerState = document.getElementById("climate-provider-state");
  var providerDot = document.getElementById("climate-provider-dot");
  var chatTitleEl = document.getElementById("climate-chat-title");
  var historyPanel = document.getElementById("climate-chat-history-panel");
  var historyList = document.getElementById("climate-chat-history-list");
  var menuPanel = document.getElementById("climate-chat-menu-panel");
  var contextPanel = document.getElementById("climate-context-panel");
  var usagePanel = document.getElementById("climate-usage-panel");
  var tokenPill = document.getElementById("climate-token-pill");
  var tokenLabel = document.getElementById("climate-token-label");
  var promptEl = document.getElementById("climate-prompt");
  var sendBtn = document.getElementById("climate-send");
  var stopBtn = document.getElementById("climate-stop");
  var sendTopBtn = document.getElementById("climate-send-top");
  var stopTopBtn = document.getElementById("climate-stop-top");
  var proposalActions = document.getElementById("climate-proposal-actions");
  var state = {
    tabs: [], active: "", selectedFiles: [], attachedContext: [], showExcluded: false, panel: "problems",
    runId: "", run: null, streamText: "", git: null, gitPath: "", tePoll: {},
    chat: { activeId: "", sessions: [] }, streamingMsgId: "",
    activityExploreOpen: {},
    modelCache: {}, fetchCount: 0,
    codexRateLimits: null,
    codexRateLimitsPromise: null,
    stopRequested: false,
    fileOpenSeq: 0,
    pollTimer: null,
    runActive: false,
    problems: [],
    outputChannel: "climate",
    output: { climate: [], runs: [], git: [], system: [] },
    testsSummary: "",
    debugPayload: null,
    portsPayload: null,
    bottomPollTimer: null
  };
  var editor = null;
  var monacoReady = false;

  function repoId() { return repoSelect ? repoSelect.value : ""; }
  function storageKey() { return "climate:v1:" + workspace + ":" + (repoId() || "none"); }
  function endpoint(path) { return apiRoot + path; }
  function setStatus(text) { statusEl.textContent = text; }
  function escapeHtml(value) { return String(value == null ? "" : value).replace(/[&<>"']/g, function (c) { return {"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c]; }); }
  function jsonFetch(url, options) {
    return fetch(url, options).then(function (response) {
      return response.json().then(function (data) {
        if (!response.ok || data.ok === false) throw new Error(data.error || "Request failed");
        return data;
      });
    });
  }
  function loadPrefs() {
    try {
      var saved = JSON.parse(localStorage.getItem(storageKey()) || "{}");
      state.tabs = Array.isArray(saved.tabs) ? saved.tabs.slice(0, 16).map(function (path) { return {path:path, content:"", original:"", language:"plaintext", loaded:false, dirty:false}; }) : [];
      state.active = saved.active || (state.tabs[0] || {}).path || "";
      state.selectedFiles = Array.isArray(saved.selectedFiles) ? saved.selectedFiles.slice(0, 24) : [];
      state.attachedContext = normalizeAttachedList(saved.attachedContext, state.selectedFiles);
      state.showExcluded = !!saved.showExcluded;
      document.getElementById("climate-show-excluded").checked = state.showExcluded;
      if (saved.left) workbench.style.setProperty("--left", saved.left + "px");
      if (saved.right) workbench.style.setProperty("--right", Math.max(AI_MIN, saved.right) + "px");
      else workbench.style.setProperty("--right", AI_DEFAULT + "px");
      if (saved.bottom) workbench.style.setProperty("--bottom", saved.bottom + "px");
      if (saved.panel && ["problems","output","debug","terminal","ports","tests","git"].indexOf(saved.panel) >= 0) {
        state.panel = saved.panel;
      }
      if (saved.outputChannel && state.output[saved.outputChannel]) state.outputChannel = saved.outputChannel;
      if (saved.aiPrevRight) workbench.dataset.aiPrevRight = Math.max(AI_MIN, parseInt(saved.aiPrevRight, 10) || AI_DEFAULT) + "px";
      workbench.classList.toggle("is-left-closed", !!saved.leftClosed);
      workbench.classList.toggle("is-ai-closed", !!saved.aiClosed);
      workbench.classList.toggle("is-ai-collapsed", !!saved.aiCollapsed && !saved.aiClosed);
      workbench.classList.toggle("is-ai-maximized", !!saved.aiMaximized && !saved.aiClosed && !saved.aiCollapsed);
      if (saved.aiCollapsed && !saved.aiClosed) workbench.style.setProperty("--right", AI_RAIL + "px");
      center.classList.toggle("is-bottom-closed", !!saved.bottomClosed);
      syncAiMaximizeChrome();
      if (saved.provider) providerSelect.dataset.saved = saved.provider;
      if (saved.model) modelSelect.dataset.saved = saved.model;
      if (saved.executionMode && executionModeSelect) {
        executionModeSelect.value = saved.executionMode === "direct" ? "direct" : "climate_assisted";
      } else if (executionModeSelect) {
        var defaultMode = (workspaceSurfaceDefaults().default_mode === "direct") ? "direct" : "climate_assisted";
        executionModeSelect.value = defaultMode;
      }
      var scopeSelect = document.getElementById("climate-context-scope");
      if (scopeSelect) {
        if (saved.contextScope === "general" || saved.contextScope === "all") scopeSelect.value = saved.contextScope;
        else if (saved.contextScope && Array.prototype.some.call(scopeSelect.options, function (opt) { return opt.value === saved.contextScope; })) {
          scopeSelect.value = saved.contextScope;
        } else if (repoId() && Array.prototype.some.call(scopeSelect.options, function (opt) { return opt.value === repoId(); })) {
          scopeSelect.value = repoId();
        }
      }
      if (executionModePill) executionModePill.setAttribute("title", executionModeTooltip(currentExecutionMode()));
    } catch (_) {}
  }
  function savePrefs() {
    var css = getComputedStyle(workbench);
    var rightPx = workbench.classList.contains("is-ai-collapsed")
      ? (parseInt(workbench.dataset.aiPrevRight, 10) || AI_DEFAULT)
      : (parseInt(css.getPropertyValue("--right"), 10) || AI_DEFAULT);
    localStorage.setItem(storageKey(), JSON.stringify({
      tabs: state.tabs.map(function (tab) { return tab.path; }), active: state.active,
      selectedFiles: state.selectedFiles, attachedContext: state.attachedContext, showExcluded: state.showExcluded, provider: providerSelect.value, model: modelSelect.value,
      executionMode: currentExecutionMode(),
      contextScope: currentWorkspaceScope().scope === "repository" ? currentWorkspaceScope().repositoryId : currentWorkspaceScope().scope,
      left: parseInt(css.getPropertyValue("--left"), 10) || 230,
      right: Math.max(AI_MIN, rightPx),
      aiPrevRight: parseInt(workbench.dataset.aiPrevRight, 10) || Math.max(AI_MIN, rightPx),
      bottom: parseInt(css.getPropertyValue("--bottom"), 10) || 190,
      panel: state.panel,
      outputChannel: state.outputChannel,
      leftClosed: workbench.classList.contains("is-left-closed"),
      aiClosed: workbench.classList.contains("is-ai-closed"),
      aiCollapsed: workbench.classList.contains("is-ai-collapsed"),
      aiMaximized: workbench.classList.contains("is-ai-maximized"),
      bottomClosed: center.classList.contains("is-bottom-closed")
    }));
  }
  var AI_MIN = 340;
  var AI_DEFAULT = 360;
  var AI_RAIL = 48;
  function repoName(id) {
    var rid = String(id || "");
    if (!repoSelect || !rid) return rid;
    var match = Array.prototype.find.call(repoSelect.options || [], function (opt) { return opt.value === rid; });
    return (match && String(match.textContent || "").trim()) || rid;
  }
  function normalizeAttachedList(saved, legacyPaths) {
    var rows = [];
    (Array.isArray(saved) ? saved : []).forEach(function (item) {
      if (!item || !item.path) return;
      rows.push({
        repositoryId: String(item.repositoryId || item.repository_id || repoId() || ""),
        path: String(item.path).replace(/\\/g, "/"),
        startLine: parseInt(item.startLine || item.start_line || 0, 10) || 0,
        endLine: parseInt(item.endLine || item.end_line || 0, 10) || 0,
        kind: item.kind === "selection" ? "selection" : "file"
      });
    });
    if (!rows.length && Array.isArray(legacyPaths)) {
      legacyPaths.forEach(function (path) {
        if (!path) return;
        rows.push({ repositoryId: repoId(), path: String(path).replace(/\\/g, "/"), startLine: 0, endLine: 0, kind: "file" });
      });
    }
    return rows.slice(0, 12);
  }
  function attachedKey(item) {
    return [item.repositoryId || "", item.path || "", item.startLine || 0, item.endLine || 0].join(":");
  }
  function currentWorkspaceScope() {
    var sel = document.getElementById("climate-context-scope");
    var value = String((sel && sel.value) || "").trim();
    var raw = value.toLowerCase();
    if (!raw || raw === "general" || [
      "none", "null", "undefined", "n/a", "na", "-",
      "no-repository", "no_repository", "norepository",
      "work", "personal", "vanta", "arctic", "workspace"
    ].indexOf(raw) >= 0) {
      return { scope: "general", repositoryId: "" };
    }
    if (raw === "all" || raw === "all-repositories" || raw === "all_repositories") {
      return { scope: "all", repositoryId: "" };
    }
    return { scope: "repository", repositoryId: value };
  }
  function canAttachFromRepo(repositoryId) {
    var scope = currentWorkspaceScope();
    if (scope.scope === "general" || scope.scope === "all") return true;
    return !!repositoryId && repositoryId === scope.repositoryId;
  }
  function syncSelectedFilesFromAttached() {
    var rid = repoId();
    state.selectedFiles = state.attachedContext.filter(function (item) {
      return item.repositoryId === rid && item.kind !== "selection";
    }).map(function (item) { return item.path; });
    var countEl = document.getElementById("climate-context-count");
    if (countEl) countEl.textContent = String(state.attachedContext.length);
  }
  function renderAttached() {
    var wrap = document.getElementById("climate-attached");
    var chips = document.getElementById("climate-attached-chips");
    var countEl = document.getElementById("climate-attached-count");
    if (countEl) countEl.textContent = String(state.attachedContext.length);
    if (wrap) wrap.hidden = !state.attachedContext.length;
    if (chips) {
      chips.innerHTML = state.attachedContext.map(function (item) {
        var name = String(item.path || "").split("/").pop();
        var meta = [];
        if (item.startLine) meta.push("L" + item.startLine + (item.endLine && item.endLine !== item.startLine ? "-" + item.endLine : ""));
        if (item.repositoryId && item.repositoryId !== repoId()) meta.push(repoName(item.repositoryId));
        return '<span class="climate-chip" data-attached="' + escapeHtml(attachedKey(item)) + '">' +
          '<span class="climate-chip-name" title="' + escapeHtml(item.path) + '">' + escapeHtml(name) + "</span>" +
          (meta.length ? '<span class="climate-chip-meta">' + escapeHtml(meta.join(" · ")) + "</span>" : "") +
          '<button type="button" class="climate-chip-remove" data-remove-attached="' + escapeHtml(attachedKey(item)) + '" aria-label="Remove">×</button></span>';
      }).join("");
      chips.querySelectorAll("[data-remove-attached]").forEach(function (btn) {
        btn.addEventListener("click", function (event) {
          event.preventDefault();
          event.stopPropagation();
          removeAttached(btn.getAttribute("data-remove-attached"));
        });
      });
    }
    renderContextSummary();
    syncSelectedFilesFromAttached();
    highlightTree();
    renderAssistantContextBar();
  }
  function renderContextSummary() {
    var summary = document.getElementById("climate-context-summary");
    var list = document.getElementById("climate-context-file-list");
    var scope = currentWorkspaceScope();
    if (summary) {
      if (scope.scope === "general") summary.textContent = "General — no repository limitation.";
      else if (scope.scope === "all") summary.textContent = "All Repositories — relevant bounded hits only.";
      else summary.textContent = "Specific Repository: " + (repoName(scope.repositoryId) || scope.repositoryId) + " — only this repository is used.";
    }
    if (list) {
      list.innerHTML = state.attachedContext.length
        ? state.attachedContext.map(function (item) {
            var range = item.startLine ? (" L" + item.startLine + (item.endLine ? "-" + item.endLine : "")) : "";
            return "<div>" + escapeHtml(item.path) + escapeHtml(range) + "</div>";
          }).join("")
        : '<div class="climate-context-hint">No files attached.</div>';
    }
    renderAssistantContextBar();
  }
  function renderAssistantContextBar() {
    var scopeEl = document.getElementById("climate-assistant-context-scope");
    var fileEl = document.getElementById("climate-assistant-context-file");
    var selEl = document.getElementById("climate-assistant-context-sel");
    var attEl = document.getElementById("climate-assistant-context-attached");
    if (!scopeEl && !fileEl) return;
    var scope = currentWorkspaceScope();
    var scopeLabel = "General";
    if (scope.scope === "all") scopeLabel = "All repositories";
    else if (scope.scope === "repository") {
      scopeLabel = repoName(scope.repositoryId) || scope.repositoryId || "Repository";
    }
    if (scopeEl) {
      scopeEl.textContent = scopeLabel;
      scopeEl.title = scopeLabel;
    }
    var nameEl = document.getElementById("climate-current-file-name");
    var fileText = (nameEl && nameEl.textContent) || "No file open";
    if (fileEl) {
      fileEl.textContent = fileText.indexOf("/") >= 0 ? fileText.split("/").pop() : fileText;
      fileEl.title = fileText;
    }
    var selMeta = document.getElementById("climate-selection-meta");
    if (selEl) selEl.textContent = (selMeta && selMeta.textContent) || "No selection";
    if (attEl) {
      var n = (state.attachedContext || []).length;
      attEl.textContent = n ? (n + " attached") : "0 attached";
    }
  }
  function addAttached(item, opts) {
    opts = opts || {};
    var next = {
      repositoryId: String(item.repositoryId || repoId() || ""),
      path: String(item.path || "").replace(/\\/g, "/"),
      startLine: parseInt(item.startLine || 0, 10) || 0,
      endLine: parseInt(item.endLine || 0, 10) || 0,
      kind: item.kind === "selection" ? "selection" : "file"
    };
    if (!next.path) return false;
    if (!next.repositoryId) {
      setStatus("Open a repository in Explorer before attaching files");
      return false;
    }
    if (!canAttachFromRepo(next.repositoryId)) {
      setStatus("That file is outside the selected repository scope");
      return false;
    }
    var key = attachedKey(next);
    if (state.attachedContext.some(function (row) { return attachedKey(row) === key; })) {
      if (!opts.silent) setStatus("Already attached · " + next.path);
      return false;
    }
    if (state.attachedContext.length >= 12) {
      setStatus("Attachment limit is 12 files");
      return false;
    }
    state.attachedContext.push(next);
    renderAttached();
    savePrefs();
    if (!opts.silent) setStatus("Attached · " + next.path);
    return true;
  }
  function removeAttached(key) {
    state.attachedContext = state.attachedContext.filter(function (item) { return attachedKey(item) !== key; });
    renderAttached();
    savePrefs();
  }
  function clearAttached() {
    state.attachedContext = [];
    renderAttached();
    savePrefs();
    setStatus("Cleared attached context");
  }
  function addCurrentFileToChat() {
    var tab = currentTab();
    if (!tab || !tab.path) { setStatus("Open a file first"); return; }
    addAttached({ repositoryId: repoId(), path: tab.path, kind: "file" });
  }
  function addCurrentSelectionToChat() {
    var tab = currentTab();
    var text = currentSelection();
    if (!tab || !tab.path) { setStatus("Open a file first"); return; }
    if (!String(text || "").trim()) { setStatus("Select code in the editor first"); return; }
    var start = 0, end = 0;
    if (editor && editor.getSelection) {
      var sel = editor.getSelection();
      if (sel && !sel.isEmpty()) {
        start = sel.startLineNumber;
        end = sel.endLineNumber;
      }
    }
    addAttached({ repositoryId: repoId(), path: tab.path, startLine: start, endLine: end, kind: "selection" });
  }
  function pruneAttachedForScope() {
    var before = state.attachedContext.length;
    state.attachedContext = state.attachedContext.filter(function (item) {
      return canAttachFromRepo(item.repositoryId);
    });
    if (state.attachedContext.length !== before) {
      setStatus("Removed attachments outside the selected repository");
      renderAttached();
      savePrefs();
    } else {
      renderContextSummary();
    }
  }
  function knownFilePaths() {
    var paths = [];
    state.tabs.forEach(function (tab) { if (tab.path && paths.indexOf(tab.path) < 0) paths.push(tab.path); });
    if (treeEl) {
      treeEl.querySelectorAll(".climate-file-row[data-path]").forEach(function (row) {
        var path = row.getAttribute("data-path");
        if (path && paths.indexOf(path) < 0) paths.push(path);
      });
    }
    return paths;
  }
  function attachMentionsFromPrompt(prompt) {
    var known = knownFilePaths();
    (String(prompt || "").match(/@([A-Za-z0-9_./-]+)/g) || []).forEach(function (token) {
      var name = token.slice(1);
      var match = known.find(function (path) {
        return path === name || path.split("/").pop() === name || path.slice(-(name.length + 1)) === "/" + name;
      });
      if (match) addAttached({ repositoryId: repoId(), path: match, kind: "file" }, { silent: true });
    });
  }
  function mentionNeedle() {
    if (!promptEl) return null;
    var start = promptEl.selectionStart || 0;
    var slice = promptEl.value.slice(0, start);
    var match = slice.match(/@([A-Za-z0-9_./-]*)$/);
    return match ? match[1] : null;
  }
  function renderMentionMenu() {
    var menu = document.getElementById("climate-mention");
    if (!menu) return;
    var needle = mentionNeedle();
    if (needle == null) { menu.hidden = true; return; }
    var q = needle.toLowerCase();
    var matches = knownFilePaths().filter(function (path) {
      return !q || path.toLowerCase().indexOf(q) >= 0 || path.split("/").pop().toLowerCase().indexOf(q) >= 0;
    }).slice(0, 8);
    if (!matches.length) { menu.hidden = true; return; }
    menu.hidden = false;
    menu.innerHTML = matches.map(function (path) {
      return '<button type="button" data-mention="' + escapeHtml(path) + '">' + escapeHtml(path.split("/").pop()) + "<small>" + escapeHtml(path) + "</small></button>";
    }).join("");
    menu.querySelectorAll("[data-mention]").forEach(function (btn) {
      btn.addEventListener("click", function (event) {
        event.preventDefault();
        applyMention(btn.getAttribute("data-mention"));
      });
    });
  }
  function applyMention(path) {
    if (!promptEl || !path) return;
    var start = promptEl.selectionStart || 0;
    var value = promptEl.value;
    var slice = value.slice(0, start);
    var prefix = slice.replace(/@[A-Za-z0-9_./-]*$/, "@" + path.split("/").pop() + " ");
    promptEl.value = prefix + value.slice(start);
    promptEl.selectionStart = promptEl.selectionEnd = prefix.length;
    addAttached({ repositoryId: repoId(), path: path, kind: "file" }, { silent: true });
    var menu = document.getElementById("climate-mention");
    if (menu) menu.hidden = true;
    promptEl.focus();
  }
  function insertMentionTrigger() {
    if (!promptEl) return;
    var start = promptEl.selectionStart || promptEl.value.length;
    var value = promptEl.value;
    if (value.slice(Math.max(0, start - 1), start) !== "@") {
      promptEl.value = value.slice(0, start) + "@" + value.slice(start);
      promptEl.selectionStart = promptEl.selectionEnd = start + 1;
    }
    promptEl.focus();
    renderMentionMenu();
  }
  function currentExecutionMode() {
    var value = executionModeSelect && executionModeSelect.value === "direct" ? "direct" : "climate_assisted";
    return value;
  }
  function executionModeTooltip(mode) {
    return mode === "direct"
      ? "Direct — send the prompt to the selected provider/model with minimal CLIMATE orchestration."
      : "AiriX — CLIMATE orchestration, then the selected provider/model.";
  }
  function syncExecutionModeSwitch(mode) {
    var next = mode === "direct" ? "direct" : "climate_assisted";
    document.querySelectorAll("#climate-mode-pill [data-execution-mode]").forEach(function (btn) {
      btn.setAttribute("aria-pressed", btn.getAttribute("data-execution-mode") === next ? "true" : "false");
    });
  }
  function applyExecutionMode(mode, opts) {
    opts = opts || {};
    var next = mode === "direct" ? "direct" : "climate_assisted";
    if (executionModeSelect) {
      executionModeSelect.value = next;
      if (executionModeSelect._climateDd) syncClimateDropdown(executionModeSelect);
    }
    syncExecutionModeSwitch(next);
    if (executionModePill) executionModePill.setAttribute("title", executionModeTooltip(next));
    var session = activeSession();
    if (session && !opts.skipSession) {
      session.executionMode = next;
      saveChatStore();
    }
    if (!opts.skipPrefs) savePrefs();
  }
  function workbenchWidth() {
    return workbench.getBoundingClientRect().width || window.innerWidth;
  }
  function maximizeAiWidthPx() {
    var vw = window.innerWidth || workbenchWidth();
    // clamp(480px, 45vw, 720px)
    return Math.min(720, Math.max(480, Math.round(vw * 0.45)));
  }
  function currentAiWidthPx() {
    var raw = workbench.style.getPropertyValue("--right") || getComputedStyle(workbench).getPropertyValue("--right");
    var n = parseInt(raw, 10);
    return isNaN(n) ? AI_DEFAULT : n;
  }
  function rememberUsableAiWidth(px) {
    var usable = Math.max(AI_MIN, px || currentAiWidthPx());
    workbench.dataset.aiPrevRight = usable + "px";
  }
  /** Never leave a full AI panel narrower than AI_MIN (legacy prefs / race). */
  function normalizeAiPanelState() {
    if (workbench.classList.contains("is-ai-closed")) return;
    if (workbench.classList.contains("is-ai-collapsed")) {
      workbench.style.setProperty("--right", AI_RAIL + "px");
      return;
    }
    var w = currentAiWidthPx();
    if (w > 0 && w < AI_MIN) {
      collapseAiPanel(AI_DEFAULT);
      return;
    }
    if (!workbench.style.getPropertyValue("--right") && !workbench.classList.contains("is-ai-maximized")) {
      workbench.style.setProperty("--right", AI_DEFAULT + "px");
    }
  }
  function setAiExpandedWidth(px, opts) {
    opts = opts || {};
    workbench.classList.remove("is-ai-collapsed");
    if (!opts.keepMaximized) {
      workbench.classList.remove("is-ai-maximized");
      delete workbench.dataset.prevRight;
    }
    var width = Math.max(AI_MIN, Math.round(px));
    if (!opts.keepMaximized) {
      var maxCap = Math.max(AI_MIN, Math.round(workbenchWidth() * 0.55));
      width = Math.min(maxCap, width);
    }
    workbench.style.setProperty("--right", width + "px");
    rememberUsableAiWidth(width);
    syncAiMaximizeChrome();
    scheduleEditorLayout();
  }
  function collapseAiPanel(fromWidth) {
    if (fromWidth && fromWidth >= AI_MIN) rememberUsableAiWidth(fromWidth);
    else if (!workbench.dataset.aiPrevRight) rememberUsableAiWidth(currentAiWidthPx());
    workbench.classList.remove("is-ai-maximized");
    workbench.classList.add("is-ai-collapsed");
    workbench.classList.remove("is-ai-closed");
    workbench.style.setProperty("--right", AI_RAIL + "px");
    delete workbench.dataset.prevRight;
    syncAiMaximizeChrome();
    scheduleEditorLayout();
  }
  function expandAiPanel() {
    var prev = parseInt(workbench.dataset.aiPrevRight || workbench.dataset.prevRight || AI_DEFAULT, 10);
    if (isNaN(prev) || prev < AI_MIN) prev = AI_DEFAULT;
    workbench.classList.remove("is-ai-closed");
    setAiExpandedWidth(prev);
    fetchCodexRateLimits({ refresh: false });
  }
  function syncAiMaximizeChrome() {
    var btn = document.getElementById("climate-maximize-ai");
    var closed = workbench.classList.contains("is-ai-closed");
    var collapsed = workbench.classList.contains("is-ai-collapsed");
    var max = workbench.classList.contains("is-ai-maximized") && !closed && !collapsed;
    if (btn) {
      btn.setAttribute("aria-pressed", max ? "true" : "false");
      btn.title = max ? "Restore AI panel" : "Maximize AI panel";
      btn.textContent = max ? "⧉" : "□";
    }
    if (max) {
      if (!workbench.dataset.prevRight) {
        var before = parseInt(workbench.dataset.aiPrevRight, 10) || currentAiWidthPx();
        if (before < AI_MIN || before === AI_RAIL) before = AI_DEFAULT;
        workbench.dataset.prevRight = Math.max(AI_MIN, before) + "px";
      }
      workbench.style.setProperty("--right", maximizeAiWidthPx() + "px");
    }
  }
  function restoreAiFromMaximize() {
    if (!workbench.dataset.prevRight) return;
    var restore = Math.max(AI_MIN, parseInt(workbench.dataset.prevRight, 10) || AI_DEFAULT);
    delete workbench.dataset.prevRight;
    workbench.classList.remove("is-ai-maximized");
    workbench.style.setProperty("--right", restore + "px");
    rememberUsableAiWidth(restore);
    syncAiMaximizeChrome();
    scheduleEditorLayout();
  }
  function languageId(language, path) {
    var name = String(path || "").split("/").pop() || "";
    var lower = name.toLowerCase();
    if (lower.endsWith(".env.example")) return "ini";
    var map = {
      python: "python", javascript: "javascript", typescript: "typescript",
      tsx: "typescript", jsx: "javascript", json: "json", yaml: "yaml",
      markdown: "markdown", html: "html", css: "css", sql: "sql",
      shell: "shell", powershell: "powershell", toml: "ini", ini: "ini",
      scss: "scss", xml: "xml", batch: "bat", bash: "shell",
      dockerfile: "dockerfile", makefile: "plaintext", plaintext: "plaintext"
    };
    if (map[language]) return map[language];
    var ext = (lower.split(".").pop() || "").toLowerCase();
    return {
      py: "python", pyi: "python", js: "javascript", mjs: "javascript", cjs: "javascript",
      ts: "typescript", tsx: "typescript", jsx: "javascript",
      json: "json", yml: "yaml", yaml: "yaml", md: "markdown", markdown: "markdown",
      html: "html", htm: "html", css: "css", scss: "scss", sql: "sql",
      txt: "plaintext", toml: "ini", ini: "ini", cfg: "ini", conf: "ini",
      xml: "xml", sh: "shell", bash: "shell", bat: "bat", ps1: "powershell",
      go: "go", rs: "rust", java: "java", kt: "kotlin", c: "c", h: "c",
      cpp: "cpp", hpp: "cpp", cs: "csharp", php: "php", vue: "html",
      r: "r", rb: "ruby", csv: "plaintext", tsv: "plaintext"
    }[ext] || "plaintext";
  }
  function isMarkdownPath(path, language) {
    var lower = String(path || "").toLowerCase();
    return language === "markdown" || /\.(md|markdown)$/.test(lower) || languageId(language, path) === "markdown";
  }
  function renderMarkdownHtml(text) {
    if (window.ClimateMarkdown && typeof window.ClimateMarkdown.render === "function") {
      return window.ClimateMarkdown.render(text);
    }
    if (typeof window.renderClimateMarkdown === "function") {
      return window.renderClimateMarkdown(text);
    }
    return "<pre>" + escapeHtml(text) + "</pre>";
  }
  function enhanceMarkdown(rootEl) {
    if (window.ClimateMarkdown && typeof window.ClimateMarkdown.enhance === "function") {
      window.ClimateMarkdown.enhance(rootEl);
    }
  }
  function applyReadOnlyFile(tab, file) {
    tab = tab || {};
    file = file || {};
    tab.language = file.language || tab.language || "plaintext";
    tab.dirty = false;
    tab.loaded = true;
    if (file.binary) {
      tab.binary = true;
      tab.unavailable = true;
      tab.empty = false;
      tab.content = "";
      tab.original = "";
      tab.error = file.error || "Preview unavailable for this file type";
      return tab;
    }
    tab.binary = false;
    var content = file.content == null ? "" : String(file.content);
    var size = Number(file.size);
    if (file.error && !content) {
      tab.unavailable = true;
      tab.empty = false;
      tab.content = "";
      tab.original = "";
      tab.error = /^Unable to read file:/.test(String(file.error))
        ? String(file.error)
        : ("Unable to read file: " + file.error);
      return tab;
    }
    tab.unavailable = false;
    tab.content = content;
    tab.original = content;
    tab.empty = content.length === 0 && (!isFinite(size) || size === 0);
    tab.error = "";
    if (!tab.viewMode) tab.viewMode = "source";
    return tab;
  }
  function shouldShowFileResponse(activePath, requestedPath) {
    return String(activePath || "") === String(requestedPath || "");
  }
  function logViewerDiag(info) {
    try { console.debug("[climate-viewer]", info); } catch (_) {}
  }
  function monacoUriFor(path) {
    var rel = String(path || "").replace(/\\/g, "/").replace(/^\/+/, "");
    return window.monaco.Uri.from({
      scheme: "climate",
      authority: encodeURIComponent(String(workspace || "ws")),
      path: "/" + encodeURIComponent(String(repoId() || "repo")) + "/" + rel.split("/").map(encodeURIComponent).join("/")
    });
  }
  function currentTab() { return state.tabs.find(function (tab) { return tab.path === state.active; }) || null; }
  function editorValue() { return editor ? editor.getValue() : fallback.value; }
  function layoutEditor() {
    if (!editor) return;
    var el = monacoHost;
    if (!el || el.hidden) return;
    var width = el.clientWidth;
    var height = el.clientHeight;
    if (width > 0 && height > 0) editor.layout({ width: width, height: height });
    else editor.layout();
  }
  function scheduleEditorLayout() {
    requestAnimationFrame(function () {
      layoutEditor();
      requestAnimationFrame(layoutEditor);
    });
  }
  function saveTabViewState(tab) {
    if (!tab) return;
    if (editor && monacoHost && !monacoHost.hidden) {
      try { tab.viewState = editor.saveViewState(); } catch (_) {}
    }
    if (mdPreview && !mdPreview.hidden) tab.previewScroll = mdPreview.scrollTop;
  }
  function restoreTabViewState(tab) {
    if (!tab) return;
    if (editor && tab.viewState) {
      try { editor.restoreViewState(tab.viewState); } catch (_) {}
    }
    if (mdPreview && !mdPreview.hidden && typeof tab.previewScroll === "number") {
      mdPreview.scrollTop = tab.previewScroll;
    }
  }
  function setEditorValue(value, language, path) {
    var text = value == null ? "" : String(value);
    var lang = languageId(language, path);
    if (editor && window.monaco) {
      var uri = monacoUriFor(path);
      var model = window.monaco.editor.getModel(uri);
      if (!model) {
        model = window.monaco.editor.createModel(text, lang, uri);
      } else {
        if (model.getLanguageId && model.getLanguageId() !== lang && window.monaco.editor.setModelLanguage) {
          window.monaco.editor.setModelLanguage(model, lang);
        }
        if (model.getValue() !== text) model.setValue(text);
      }
      editor.updateOptions({ readOnly: true, domReadOnly: true });
      editor.setModel(model);
      refreshJsonParseProblem(model, path);
      logViewerDiag({
        repo: repoId(),
        path: path,
        language: lang,
        contentLength: text.length,
        monacoUri: String(model.uri),
        modelLength: model.getValue().length
      });
    } else {
      fallback.value = text;
      fallback.readOnly = true;
    }
    refreshProblems();
  }
  function hideEditorSurfaces() {
    if (monacoHost) monacoHost.hidden = true;
    if (mdPreview) { mdPreview.hidden = true; mdPreview.innerHTML = ""; }
    if (fileUnavailable) fileUnavailable.hidden = true;
    if (fileEmptyEl) fileEmptyEl.hidden = true;
    fallback.style.display = "none";
  }
  function showFileSurface(tab) {
    hideEditorSurfaces();
    if (!tab) return;
    if (fileReadonly) fileReadonly.hidden = false;
    if (tab.binary || tab.unavailable) {
      if (fileUnavailable) {
        fileUnavailable.hidden = false;
        fileUnavailable.textContent = tab.error || (tab.binary
          ? "Preview unavailable for this file type"
          : "Unable to read file: unknown error");
      }
      if (fileModes) fileModes.hidden = true;
      if (editor) editor.setModel(null);
      return;
    }
    if (fileEmptyEl) fileEmptyEl.hidden = !tab.empty;
    var markdown = isMarkdownPath(tab.path, tab.language);
    if (fileModes) {
      fileModes.hidden = !markdown;
      if (markdown) {
        fileModes.querySelectorAll("[data-file-mode]").forEach(function (btn) {
          btn.classList.toggle("is-active", btn.getAttribute("data-file-mode") === (tab.viewMode || "source"));
        });
      }
    }
    if (markdown && tab.viewMode === "preview") {
      if (mdPreview) {
        mdPreview.hidden = false;
        mdPreview.innerHTML = renderMarkdownHtml(tab.content || "");
        enhanceMarkdown(mdPreview);
        restoreTabViewState(tab);
      }
      return;
    }
    if (editor && monacoHost) {
      monacoHost.hidden = false;
      setEditorValue(tab.content, tab.language, tab.path);
      restoreTabViewState(tab);
      scheduleEditorLayout();
    } else {
      fallback.style.display = "block";
      fallback.readOnly = true;
      fallback.value = tab.content || "";
    }
  }
  function updateBreadcrumb(tab) {
    var label = tab ? tab.path : "Open a file from Explorer";
    if (filePathEl) {
      filePathEl.textContent = tab ? tab.path.split("/").join(" › ") : "Open a file from Explorer";
      filePathEl.title = label;
    } else if (breadcrumb && breadcrumb.querySelector("span")) {
      breadcrumb.querySelector("span").textContent = tab ? tab.path.split("/").join("  ›  ") : "Open a file from Explorer";
    }
    var nameEl = document.getElementById("climate-current-file-name");
    if (nameEl) nameEl.textContent = tab ? tab.path : "No file open";
    var langEl = document.getElementById("climate-language");
    if (langEl) langEl.textContent = tab && !tab.binary ? languageId(tab.language, tab.path) : "Plain Text";
    if (fileReadonly) fileReadonly.hidden = !tab;
    renderAssistantContextBar();
  }
  function captureActive() {
    saveTabViewState(currentTab());
  }
  function renderTabs() {
    if (!state.tabs.length) { tabsEl.innerHTML = '<div class="climate-empty-tab">Open a file from Explorer</div>'; return; }
    tabsEl.innerHTML = state.tabs.map(function (tab) {
      var name = tab.path.split("/").pop();
      return '<button class="climate-tab '+(tab.path===state.active?'is-active':'')+'" data-path="'+escapeHtml(tab.path)+'" title="'+escapeHtml(tab.path)+'"><span class="climate-tab-name">'+escapeHtml(name)+'</span><span class="climate-tab-close" data-close="'+escapeHtml(tab.path)+'">×</span></button>';
    }).join("");
    tabsEl.querySelectorAll(".climate-tab").forEach(function (button) {
      button.addEventListener("click", function (event) {
        if (event.target.hasAttribute("data-close")) return;
        activateTab(button.getAttribute("data-path"));
      });
    });
    tabsEl.querySelectorAll("[data-close]").forEach(function (button) {
      button.addEventListener("click", function (event) { event.stopPropagation(); closeTab(button.getAttribute("data-close")); });
    });
  }
  function closeTab(path) {
    captureActive();
    state.tabs = state.tabs.filter(function (item) { return item.path !== path; });
    if (window.monaco) { var uri = monacoUriFor(path); var model = window.monaco.editor.getModel(uri); if (model) model.dispose(); }
    if (state.active === path) state.active = (state.tabs[state.tabs.length - 1] || {}).path || "";
    renderTabs(); savePrefs();
    if (state.active) activateTab(state.active); else { if (editor) editor.setModel(null); hideEditorSurfaces(); fallback.value=""; welcome.hidden=false; if (fileReadonly) fileReadonly.hidden=true; updateBreadcrumb(null); }
  }
  function openFile(path, line, column, symbol) {
    path = normalizeRepoPath(path);
    if (!path) return;
    if (state.active && state.active !== path) captureActive();
    var existing = state.tabs.find(function (tab) { return tab.path === path; });
    if (!existing) {
      existing = {path:path,content:"",original:"",language:"plaintext",loaded:false,dirty:false,binary:false,unavailable:false,empty:false,error:"",viewMode:"source"};
      state.tabs.push(existing);
    }
    if (symbol || line) existing.viewMode = "source";
    state.active = path;
    state.fileOpenSeq = (state.fileOpenSeq || 0) + 1;
    var seq = state.fileOpenSeq;
    renderTabs(); savePrefs(); setStatus("Opening " + path + "…");
    function afterOpen() {
      if (!shouldShowFileResponse(state.active, path)) return;
      activateTab(path);
      if (existing.binary || existing.unavailable) return;
      var n = Number(line) || 0;
      var col = Number(column) || 1;
      if (!n && symbol) n = findSymbolLine(existing.content, symbol);
      if (n) {
        revealEditorLine(n, col);
        return;
      }
      if (symbol) {
        locateSymbolInRepo(path, symbol);
        return;
      }
      if (editor) editor.focus();
    }
    if (existing.loaded) return afterOpen();
    jsonFetch(endpoint("/repositories/"+encodeURIComponent(repoId())+"/file?path="+encodeURIComponent(path))).then(function (data) {
      applyReadOnlyFile(existing, data.file || {});
      logViewerDiag({
        repo: repoId(),
        path: path,
        status: existing.unavailable ? "error" : "ok",
        contentLength: (existing.content || "").length,
        language: existing.language,
        seq: seq
      });
      if (!shouldShowFileResponse(state.active, path)) return;
      afterOpen();
      if (existing.unavailable) setStatus(existing.error);
      else setStatus("Read-only · " + path + (existing.empty ? " · Empty file" : (" · " + (existing.content || "").length + " chars")));
    }).catch(function (error) {
      applyReadOnlyFile(existing, { error: error.message, content: "", binary: false });
      if (!shouldShowFileResponse(state.active, path)) return;
      afterOpen();
      setStatus(existing.error || error.message);
      addProblem({ severity:"error", source:"io", path:path, line:1, message:error.message });
    });
  }
  function revealEditorLine(line, column) {
    var n = Number(line);
    if (!n || !editor) return;
    var pos = { lineNumber: n, column: Number(column) || 1 };
    editor.setPosition(pos);
    editor.revealLineInCenter(n);
    editor.focus();
  }
  function normalizeRepoPath(path) {
    return String(path || "").replace(/\\/g, "/").replace(/^\/+/, "").trim();
  }
  function findSymbolLine(content, symbol) {
    var name = String(symbol || "").trim();
    if (!name || content == null || content === "") return 0;
    var escaped = name.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
    var lines = String(content).split(/\r?\n/);
    var defRe = new RegExp(
      "^\\s*(?:(?:public|private|protected|internal|static|async|export|abstract|sealed)\\s+)*(?:def|function|class|interface|type|enum|fn|const|let|var)\\s+" + escaped + "\\b"
    );
    var assignRe = new RegExp("^\\s*" + escaped + "\\s*[=:(]");
    var i;
    for (i = 0; i < lines.length; i++) {
      if (defRe.test(lines[i]) || assignRe.test(lines[i])) return i + 1;
    }
    var wordRe = new RegExp("\\b" + escaped + "\\b");
    for (i = 0; i < lines.length; i++) {
      if (wordRe.test(lines[i])) return i + 1;
    }
    return 0;
  }
  function pickSearchLine(matches, path, symbol) {
    var rel = normalizeRepoPath(path);
    var name = String(symbol || "").trim();
    var hits = (matches || []).filter(function (row) {
      return row && normalizeRepoPath(row.path) === rel && row.line;
    });
    if (!hits.length) return 0;
    if (name) {
      var defRe = new RegExp("\\b(?:def|function|class|fn)\\s+" + name.replace(/[.*+?^${}()|[\]\\]/g, "\\$&") + "\\b");
      var def = hits.find(function (row) { return defRe.test(row.snippet || ""); });
      if (def) return Number(def.line) || 0;
    }
    return Number(hits[0].line) || 0;
  }
  function locateSymbolInRepo(path, symbol) {
    if (!symbol || !repoId()) {
      if (editor) editor.focus();
      return;
    }
    jsonFetch(endpoint("/repositories/" + encodeURIComponent(repoId()) + "/search?mode=content&q=" + encodeURIComponent(symbol))).then(function (data) {
      if (!shouldShowFileResponse(state.active, path)) return;
      var n = pickSearchLine(data.matches, path, symbol);
      if (n) revealEditorLine(n, 1);
      else if (editor) editor.focus();
    }).catch(function () {
      if (editor) editor.focus();
    });
  }
  function activateTab(path) {
    if (state.active && state.active !== path) captureActive();
    state.active = path;
    var tab = currentTab(); renderTabs(); savePrefs();
    if (!tab) return;
    if (!tab.loaded) return openFile(path);
    welcome.hidden = true;
    showFileSurface(tab);
    updateBreadcrumb(tab);
    highlightTree();
    if (tab.unavailable || tab.binary) setStatus(tab.error || "Unavailable · " + tab.path);
    else setStatus("Read-only · " + tab.path + (tab.empty ? " · Empty file" : (" · " + (tab.content || "").length + " chars")));
  }
  function markDirty() {
    return;
  }
  function setFileViewMode(mode) {
    var tab = currentTab();
    if (!tab || !isMarkdownPath(tab.path, tab.language)) return;
    captureActive();
    tab.viewMode = mode === "preview" ? "preview" : "source";
    showFileSurface(tab);
  }
  function renderTree(nodes, depth) {
    depth = depth || 0;
    return (nodes || []).map(function (node) {
      if (node.type === "dir") {
        return '<details data-folder="' + escapeHtml(node.path || node.name) + '" ' + (depth < 1 ? "open" : "") + '><summary style="padding-left:' + (7 + depth * 10) + 'px">▸ ' + escapeHtml(node.name) +
          '<button type="button" class="climate-add-chat" data-add-folder="' + escapeHtml(node.path || node.name) + '" title="Add folder to Chat">Add</button></summary>' +
          renderTree(node.children || [], depth + 1) + "</details>";
      }
      var attached = state.attachedContext.some(function (item) { return item.repositoryId === repoId() && item.path === node.path; });
      var mark = node.git_status && node.git_status !== "clean" ? '<span class="climate-git-mark">' + escapeHtml(node.git_status.charAt(0).toUpperCase()) + "</span>" : "";
      return '<div class="climate-file-row' + (attached ? " is-attached" : "") + '" data-path="' + escapeHtml(node.path) + '" style="padding-left:' + (9 + depth * 11) + 'px" role="treeitem">' +
        '<span class="climate-file-name">' + escapeHtml(node.name) + "</span>" + mark +
        '<button type="button" class="climate-add-chat" data-add-chat="' + escapeHtml(node.path) + '" title="Add to Chat">Add</button></div>';
    }).join("");
  }
  function bindFileRows(container) {
    container.querySelectorAll(".climate-file-row").forEach(function (row) {
      row.addEventListener("click", function (event) {
        if (event.target.closest("[data-add-chat]")) return;
        openFile(row.getAttribute("data-path"));
      });
      row.addEventListener("contextmenu", function (event) {
        event.preventDefault();
        showTreeMenu(event.clientX, event.clientY, row.getAttribute("data-path"), false);
      });
    });
    container.querySelectorAll("[data-add-chat]").forEach(function (btn) {
      btn.addEventListener("click", function (event) {
        event.preventDefault();
        event.stopPropagation();
        addAttached({ repositoryId: repoId(), path: btn.getAttribute("data-add-chat"), kind: "file" });
      });
    });
    container.querySelectorAll("[data-add-folder]").forEach(function (btn) {
      btn.addEventListener("click", function (event) {
        event.preventDefault();
        event.stopPropagation();
        addFolderToChat(btn.getAttribute("data-add-folder"), btn.closest("details"));
      });
    });
  }
  function addFolderToChat(folderPath, detailsEl) {
    var paths = [];
    if (detailsEl) {
      detailsEl.querySelectorAll(".climate-file-row[data-path]").forEach(function (row) {
        var path = row.getAttribute("data-path");
        if (path) paths.push(path);
      });
    }
    var added = 0;
    paths.slice(0, 8).forEach(function (path) {
      if (addAttached({ repositoryId: repoId(), path: path, kind: "file" }, { silent: true })) added += 1;
    });
    setStatus(added ? ("Attached " + added + " file" + (added === 1 ? "" : "s") + " from " + folderPath) : ("No files added from " + folderPath));
  }
  function showTreeMenu(x, y, path, isFolder) {
    var menu = document.getElementById("climate-tree-menu");
    if (!menu || !path) return;
    menu.hidden = false;
    menu.style.left = Math.max(8, x) + "px";
    menu.style.top = Math.max(8, y) + "px";
    menu.dataset.path = path;
    menu.dataset.folder = isFolder ? "1" : "";
  }
  function toggleContext(path, enabled) {
    if (enabled) addAttached({ repositoryId: repoId(), path: path, kind: "file" });
    else {
      var key = attachedKey({ repositoryId: repoId(), path: path, startLine: 0, endLine: 0 });
      removeAttached(key);
    }
  }
  function highlightTree() {
    treeEl.querySelectorAll(".climate-file-row").forEach(function (row) {
      var path = row.getAttribute("data-path");
      row.classList.toggle("is-active", path === state.active);
      row.classList.toggle("is-attached", state.attachedContext.some(function (item) {
        return item.repositoryId === repoId() && item.path === path;
      }));
    });
  }
  function loadTree() {
    if (!repoId()) { treeEl.innerHTML='<div class="climate-ai-empty">No repository in this workspace.</div>'; return; }
    treeEl.textContent="Loading…";
    jsonFetch(endpoint("/repositories/"+encodeURIComponent(repoId())+"/tree?show_excluded="+(state.showExcluded?"1":"0"))).then(function (data) {
      treeEl.innerHTML=renderTree(data.entries || [],0); bindFileRows(treeEl); highlightTree(); renderAttached();
    }).catch(function (error) { treeEl.textContent=error.message; });
  }
  function runSearch() {
    var q=document.getElementById("climate-search").value.trim(), results=document.getElementById("climate-search-results"); if(!q){results.hidden=true;treeEl.hidden=false;return;}
    jsonFetch(endpoint("/repositories/"+encodeURIComponent(repoId())+"/search?mode=content&q="+encodeURIComponent(q))).then(function(data){
      results.innerHTML='<div class="climate-context-note">'+data.count+' results</div>'+(data.matches||[]).map(function(m){return '<button class="climate-file-row" data-path="'+escapeHtml(m.path)+'"><span class="climate-file-name">'+escapeHtml(m.path)+(m.line?' :'+m.line:'')+'</span></button>';}).join('');
      bindFileRows(results);results.hidden=false;treeEl.hidden=true;
    }).catch(function(error){setStatus(error.message);});
  }
  function saveFile() {
    setStatus("Read-only viewer — saving is not available yet.");
  }
  function confirmSave() {
    setStatus("Read-only viewer — saving is not available yet.");
  }
  function loadGit() {
    if(!repoId())return;
    jsonFetch(endpoint("/repositories/"+encodeURIComponent(repoId())+"/git/status")).then(function(data){
      state.git=data;var count=(data.files||[]).length;gitSummary.textContent=(data.branch||"Git")+(count?" · "+count+" changes":" · clean");
      if(state.panel==="git") renderGitWorkspace(data,state.gitPath);
      var option=repoSelect.options[repoSelect.selectedIndex];branchSelect.innerHTML='<option>'+escapeHtml(data.branch||option&&option.dataset.branch||'—')+'</option>';
    }).catch(function(error){gitSummary.textContent="Git · "+error.message;pushOutput("git", error.message);});
  }
  function diffSides(diff) {
    var before=[],after=[],oldLine=0,newLine=0;
    String(diff||"").split("\n").forEach(function(line){
      var match=line.match(/^@@ -(\d+)/);if(match){oldLine=parseInt(match[1],10);var next=line.match(/\+(\d+)/);newLine=next?parseInt(next[1],10):newLine;return;}
      if(line.indexOf("---")===0||line.indexOf("+++")===0||line.indexOf("diff ")===0||line.indexOf("index ")===0)return;
      if(line.charAt(0)==="-"){before.push(String(oldLine++)+"  "+line.slice(1));}
      else if(line.charAt(0)==="+"){after.push(String(newLine++)+"  "+line.slice(1));}
      else {var body=line.charAt(0)===" "?line.slice(1):line;before.push(String(oldLine++)+"  "+body);after.push(String(newLine++)+"  "+body);}
    });
    return {before:before.join("\n"),after:after.join("\n")};
  }
  function renderGitWorkspace(data, activePath) {
    var files=data.files||[];activePath=activePath||((files[0]||{}).path||"");state.gitPath=activePath;
    bottomBody.innerHTML='<div class="climate-git-workspace"><aside class="climate-git-changes"><div class="climate-git-title">Changes <span class="count">'+files.length+'</span></div>'+files.map(function(file){return '<button class="climate-git-file '+(file.path===activePath?'is-active':'')+'" data-git-path="'+escapeHtml(file.path)+'"><span>◆</span><span>'+escapeHtml(file.path)+'</span><b>'+escapeHtml((file.xy||'M').trim()||'M')+'</b></button>';}).join('')+'</aside><section class="climate-git-review" id="climate-git-review"><div class="climate-git-review-head"><span>'+(activePath?escapeHtml(activePath):'Working tree clean')+'</span><select disabled><option>Unified</option></select></div><div class="climate-diff-split"><div class="climate-diff-column is-before"><h4>Original</h4><pre>'+(activePath?'Loading diff…':escapeHtml(data.detail||'Clean working tree.'))+'</pre></div><div class="climate-diff-column is-after"><h4>Modified</h4><pre></pre></div></div><div class="climate-git-actions"><span class="climate-spacer"></span></div></section></div>';
    bottomBody.querySelectorAll("[data-git-path]").forEach(function(button){button.addEventListener("click",function(){renderGitWorkspace(data,button.getAttribute("data-git-path"));});});
    if(activePath) loadDiff(activePath);
  }
  function loadDiff(path) {
    jsonFetch(endpoint("/repositories/"+encodeURIComponent(repoId())+"/git/diff?path="+encodeURIComponent(path))).then(function(data){
      var sides=diffSides(data.diff||"");var review=document.getElementById("climate-git-review");if(!review)return;
      review.querySelector(".is-before pre").textContent=sides.before||"No original content available.";
      review.querySelector(".is-after pre").textContent=sides.after||"No modified content available.";
    }).catch(function(error){var review=document.getElementById("climate-git-review");if(review)review.querySelector(".is-before pre").textContent=error.message;});
  }
  function chatStorageKey() { return "climate:workspace:v1:" + workspace; }
  function legacyChatStorageKey() { return "climate:chat:v1:" + workspace; }
  function conversationQuery(extra) {
    var parts = ["surface=" + encodeURIComponent(CLIMATE_SURFACE)];
    if (repoId()) parts.push("repository_id=" + encodeURIComponent(repoId()));
    if (extra) parts.push(extra);
    return parts.join("&");
  }
  function defaultSessionTitle() { return "New session"; }
  function isDefaultSessionTitle(title) {
    var t = String(title || "").trim();
    return !t || t === "New session" || t === "New chat";
  }
  function uid(prefix) { return (prefix || "id") + "-" + Date.now().toString(36) + Math.random().toString(36).slice(2, 7); }
  function providerLabel(id) {
    var row = (bootstrap.providers || []).find(function (p) { return p.id === id; });
    return (row && row.label) || id || "Assistant";
  }
  function assistantRoleLabel(msg) {
    if (!msg || msg.role === "user") return "You";
    if (msg.assistant_label) return msg.assistant_label;
    var mode = msg.executionMode || msg.execution_mode || "";
    if (mode === "direct") {
      return providerLabel(msg.provider || (activeSession() || {}).provider);
    }
    return "AiriX";
  }
  function identityLogoSrc(msg) {
    if (!msg || msg.role === "user") return "";
    if (assistantRoleLabel(msg) === "AiriX") {
      var shell = document.getElementById("climate-shell");
      return (shell && shell.getAttribute("data-brand-icon")) || "/static/img/climate-mark.png";
    }
    var id = String(msg.provider || (activeSession() || {}).provider || "").toLowerCase();
    var map = {
      gemini: "/static/img/providers/gemini.svg",
      codex: "/static/img/providers/codex.svg",
      "claude-code": "/static/img/providers/claude-code.svg",
      claude: "/static/img/providers/claude-code.svg",
      "cursor-agent": "/static/img/providers/cursor-agent.svg",
      cursor: "/static/img/providers/cursor-agent.svg"
    };
    return map[id] || "";
  }
  function providerGlyph(id) {
    return id === "codex" ? "◎" : id === "claude-code" ? "✹" : id === "cursor-agent" ? "⬡" : "✦";
  }
  function formatClock(ts) {
    try {
      return new Date(ts || Date.now()).toLocaleTimeString([], { hour: "numeric", minute: "2-digit" });
    } catch (_) { return ""; }
  }
  function formatElapsed(ms) {
    var n = Math.max(0, Number(ms) || 0);
    if (n < 1000) return "<1s";
    var sec = Math.round(n / 1000);
    if (sec < 60) return sec + "s";
    var m = Math.floor(sec / 60); var s = sec % 60;
    return m + "m" + (s ? (" " + s + "s") : "");
  }
  function emptyUsage() {
    return {
      input: 0, output: 0, cached: 0, total: 0, currentRun: 0,
      byProvider: { codex: 0, "claude-code": 0, "cursor-agent": 0 },
      source: "unavailable",
      since: Date.now()
    };
  }
  function ensureUsage(session) {
    if (!session.usage) session.usage = emptyUsage();
    if (!session.usage.byProvider) session.usage.byProvider = { codex: 0, "claude-code": 0, "cursor-agent": 0 };
    return session.usage;
  }
  function parseUsagePayload(raw) {
    raw = raw && typeof raw === "object" ? raw : {};
    var input = raw.input_tokens != null ? Number(raw.input_tokens) : (raw.prompt_tokens != null ? Number(raw.prompt_tokens) : null);
    var output = raw.output_tokens != null ? Number(raw.output_tokens) : (raw.completion_tokens != null ? Number(raw.completion_tokens) : null);
    var cached = raw.cached_tokens != null ? Number(raw.cached_tokens)
      : (raw.cached_input_tokens != null ? Number(raw.cached_input_tokens)
        : (raw.cache_read_input_tokens != null ? Number(raw.cache_read_input_tokens) : null));
    var total = raw.total_tokens != null ? Number(raw.total_tokens) : (raw.total != null ? Number(raw.total) : null);
    if (total == null && (input != null || output != null)) total = (input || 0) + (output || 0);
    var explicit = String(raw.usage_source || "").toLowerCase();
    var source = "unavailable";
    if (total != null && !isNaN(total)) {
      if (explicit === "actual" || explicit === "exact") source = "exact";
      else if (explicit === "estimate" || explicit === "estimated") source = "estimated";
      else source = "exact";
    } else if (explicit === "estimate" || explicit === "estimated") {
      source = "estimated";
    }
    return {
      input: input != null && !isNaN(input) ? input : 0,
      output: output != null && !isNaN(output) ? output : 0,
      cached: cached != null && !isNaN(cached) ? cached : 0,
      total: total != null && !isNaN(total) ? total : 0,
      source: source,
      exact: source === "exact"
    };
  }
  function formatTokenExact(value) {
    if (value == null || value === "" || isNaN(Number(value))) return "Unavailable";
    return Math.round(Number(value)).toLocaleString();
  }
  function formatMs(ms) {
    if (ms == null || isNaN(Number(ms))) return "Unavailable";
    return formatElapsed(Number(ms));
  }
  function tokenEfficiencyStatus(te) {
    return String((te && te.status) || "Not measured");
  }
  function formatTokenCount(n) {
    n = Math.max(0, Number(n) || 0);
    if (n < 1000) return String(Math.round(n));
    if (n < 10000) return (n / 1000).toFixed(1).replace(/\.0$/, "") + "k";
    if (n < 1000000) return (n / 1000).toFixed(1).replace(/\.0$/, "") + "k";
    return (n / 1000000).toFixed(2).replace(/\.00$/, "") + "M";
  }
  function resolveCodexQuotaRemaining() {
    var payload = state.codexRateLimits;
    if (!payload || !payload.available) return null;
    var pct = payload.remainingPercent;
    if (pct == null || isNaN(Number(pct))) return null;
    return Math.max(0, Math.min(100, Number(pct)));
  }
  function formatQuotaMeter(pct) {
    if (pct == null || isNaN(pct)) {
      return { available: false, bar: "▱▱▱▱▱", label: "—", value: 0 };
    }
    var p = Math.max(0, Math.min(100, Math.round(Number(pct))));
    var filled = Math.round(p / 20);
    var bar = "";
    for (var i = 0; i < 5; i++) bar += (i < filled ? "▰" : "▱");
    return { available: true, bar: bar, label: p + "%", value: p };
  }
  function formatResetAt(ts) {
    var n = Number(ts);
    if (!n || isNaN(n)) return "";
    try {
      return new Date(n * 1000).toLocaleString(undefined, {
        month: "short", day: "numeric", hour: "numeric", minute: "2-digit"
      });
    } catch (_err) {
      return "";
    }
  }
  function formatDurationMins(mins) {
    var n = Number(mins);
    if (!n || isNaN(n) || n <= 0) return "";
    if (n < 60) return n + " min window";
    if (n % 60 === 0) {
      var hours = n / 60;
      if (hours % 24 === 0) {
        var days = hours / 24;
        return days + (days === 1 ? " day window" : " day window");
      }
      return hours + (hours === 1 ? " hour window" : " hour window");
    }
    return n + " min window";
  }
  function applyCodexRateLimits(payload) {
    state.codexRateLimits = payload && typeof payload === "object" ? payload : null;
    renderUsageChrome(activeSession());
  }
  function fetchCodexRateLimits(opts) {
    opts = opts || {};
    var refresh = !!opts.refresh;
    if (workspace !== "work") {
      applyCodexRateLimits({
        available: false,
        message: "Codex limit unavailable",
        buckets: [],
        remainingPercent: null
      });
      return Promise.resolve(state.codexRateLimits);
    }
    if (!refresh && state.codexRateLimitsPromise) return state.codexRateLimitsPromise;
    var url = endpoint("/providers/codex/rate-limits" + (refresh ? "?refresh=1" : ""));
    state.codexRateLimitsPromise = jsonFetch(url).then(function (data) {
      applyCodexRateLimits(data);
      return data;
    }).catch(function () {
      applyCodexRateLimits({
        available: false,
        message: "Codex limit unavailable",
        buckets: [],
        remainingPercent: null
      });
      return state.codexRateLimits;
    }).finally(function () {
      state.codexRateLimitsPromise = null;
    });
    return state.codexRateLimitsPromise;
  }
  function renderCodexLimitsPopover(payload) {
    var statusEl = document.getElementById("climate-usage-quota-status");
    var limitsEl = document.getElementById("climate-usage-limits");
    var creditsEl = document.getElementById("climate-usage-credits");
    payload = payload || state.codexRateLimits;
    if (!payload || !payload.available) {
      if (statusEl) {
        statusEl.hidden = false;
        statusEl.textContent = (payload && payload.message) || "Codex limit unavailable";
      }
      if (limitsEl) {
        limitsEl.hidden = true;
        limitsEl.innerHTML = "";
      }
      if (creditsEl) {
        creditsEl.hidden = true;
        creditsEl.textContent = "";
      }
      return;
    }
    var buckets = Array.isArray(payload.buckets) ? payload.buckets : [];
    if (statusEl) {
      statusEl.hidden = buckets.length > 0;
      statusEl.textContent = buckets.length ? "" : "Codex limit unavailable";
    }
    if (limitsEl) {
      if (!buckets.length) {
        limitsEl.hidden = true;
        limitsEl.innerHTML = "";
      } else {
        limitsEl.hidden = false;
        limitsEl.innerHTML = buckets.map(function (bucket) {
          var left = bucket.remainingPercent != null ? (Math.round(Number(bucket.remainingPercent) * 10) / 10) + "% left" : "—";
          var meta = [];
          var duration = formatDurationMins(bucket.windowDurationMins);
          if (duration) meta.push(duration);
          var reset = formatResetAt(bucket.resetsAt);
          if (reset) meta.push("resets " + reset);
          return '<div class="climate-usage-limit-row">'
            + '<div class="climate-usage-limit-top"><span>' + escapeHtml(bucket.limitName || bucket.limitId || "limit") + '</span><b>' + escapeHtml(left) + '</b></div>'
            + (meta.length ? '<div class="climate-usage-limit-meta">' + escapeHtml(meta.join(" · ")) + '</div>' : "")
            + '</div>';
        }).join("");
      }
    }
    if (creditsEl) {
      var credits = payload.credits;
      if (credits && (credits.unlimited || credits.hasCredits || credits.balance != null)) {
        creditsEl.hidden = false;
        if (credits.unlimited) creditsEl.textContent = "Credits: unlimited";
        else if (credits.balance != null && String(credits.balance).trim() !== "") {
          creditsEl.textContent = "Credits: " + String(credits.balance);
        } else if (credits.hasCredits) creditsEl.textContent = "Credits available";
        else creditsEl.textContent = "Credits: none";
      } else {
        creditsEl.hidden = true;
        creditsEl.textContent = "";
      }
    }
  }
  function applyUsageFromRun(session, providerId, usageRaw) {
    var usage = ensureUsage(session);
    var parsed = parseUsagePayload(usageRaw);
    usage.currentRun = parsed.total;
    if (!parsed.exact && parsed.source !== "estimated") {
      usage.source = usage.total > 0 ? usage.source : "unavailable";
      renderUsageChrome(session);
      return parsed;
    }
    usage.input += parsed.input;
    usage.output += parsed.output;
    usage.cached += parsed.cached;
    usage.total += parsed.total;
    var key = providerId || "codex";
    if (!(key in usage.byProvider)) usage.byProvider[key] = 0;
    usage.byProvider[key] += parsed.total;
    if (parsed.exact) usage.source = "exact";
    else if (usage.source !== "exact") usage.source = "estimated";
    renderUsageChrome(session);
    return parsed;
  }
  function renderUsageChrome(session) {
    session = session || activeSession();
    var usage = session ? ensureUsage(session) : emptyUsage();
    if (tokenLabel) tokenLabel.textContent = formatTokenCount(usage.total) + " tokens";
    var quota = formatQuotaMeter(resolveCodexQuotaRemaining());
    var quotaWrap = document.getElementById("climate-token-quota");
    var meterEl = document.getElementById("climate-token-meter");
    var quotaPctEl = document.getElementById("climate-token-quota-pct");
    if (quotaWrap) {
      if (quota.available) {
        quotaWrap.hidden = false;
        if (meterEl) meterEl.textContent = quota.bar;
        if (quotaPctEl) quotaPctEl.textContent = quota.label;
        quotaWrap.title = "Codex remaining capacity " + quota.label;
      } else {
        quotaWrap.hidden = true;
        if (meterEl) meterEl.textContent = "▱▱▱▱▱";
        if (quotaPctEl) quotaPctEl.textContent = "—";
        quotaWrap.title = "Codex limit unavailable";
      }
    }
    if (!usagePanel) return;
    var sinceEl = document.getElementById("climate-usage-since");
    if (sinceEl) sinceEl.textContent = "Since " + formatClock(usage.since || Date.now());
    var totalEl = document.getElementById("climate-usage-total");
    if (totalEl) totalEl.textContent = (usage.total || 0).toLocaleString();
    var set = function (id, val) { var el = document.getElementById(id); if (el) el.textContent = (val || 0).toLocaleString(); };
    set("climate-usage-input", usage.input);
    set("climate-usage-output", usage.output);
    set("climate-usage-cached", usage.cached);
    set("climate-usage-run", usage.currentRun);
    var sourceEl = document.getElementById("climate-usage-source");
    if (sourceEl) {
      var src = usage.source || "unavailable";
      sourceEl.textContent = src === "exact" ? "exact" : (src === "estimated" ? "estimated" : "unavailable");
    }
    var host = document.getElementById("climate-usage-providers");
    if (host) {
      var rows = [
        ["codex", "Codex"],
        ["claude-code", "Claude"],
        ["cursor-agent", "Cursor"]
      ];
      host.innerHTML = rows.map(function (row) {
        return '<div><span>' + row[1] + '</span><b>' + ((usage.byProvider[row[0]] || 0).toLocaleString()) + '</b></div>';
      }).join("");
    }
    renderCodexLimitsPopover(state.codexRateLimits);
  }
  function lineDeltaFromProposal(proposal) {
    var plus = 0, minus = 0;
    ((proposal && proposal.edits) || []).forEach(function (edit) {
      String(edit.diff || "").split(/\r?\n/).forEach(function (line) {
        if (/^\+[^+]/.test(line)) plus += 1;
        else if (/^-[^-]/.test(line)) minus += 1;
      });
    });
    return { plus: plus, minus: minus };
  }
  function testsFromText(text, explicit) {
    if (explicit && typeof explicit === "object") return explicit;
    var m = String(text || "").match(/(\d+)\s+passed/i);
    if (m) return { passed: true, count: Number(m[1]), label: m[1] + " passed", suite: "tests" };
    if (/tests?\s+passed/i.test(text || "")) return { passed: true, count: null, label: "passed", suite: "tests" };
    return null;
  }
  function compactHandoffPrompt(userPrompt, session) {
    var recent = (session.messages || []).slice(-6).filter(function (msg) {
      return msg.role === "user" || msg.role === "assistant";
    });
    var files = [];
    recent.forEach(function (msg) {
      (msg.changedFiles || []).forEach(function (path) {
        if (files.indexOf(path) < 0) files.push(path);
      });
    });
    var summaryBits = [];
    recent.forEach(function (msg) {
      (msg.summary || []).slice(0, 3).forEach(function (item) {
        if (summaryBits.indexOf(item) < 0) summaryBits.push(item);
      });
    });
    var lines = [
      "[CLIMATE cross-provider handoff]",
      "Task: " + (session.title || titleFromPrompt(userPrompt)),
      "Prior provider: " + providerLabel(session.lastRunProvider || session.provider || ""),
      "Summary: " + (summaryBits.slice(0, 5).join("; ") || "Continue the coding task with minimal replay."),
      "Relevant files: " + (files.slice(0, 8).join(", ") || "(none listed)"),
      "Recent turns:"
    ];
    recent.slice(-4).forEach(function (msg) {
      var who = msg.role === "user" ? "User" : providerLabel(msg.provider || session.lastRunProvider);
      var text = String(msg.text || "").replace(/\s+/g, " ").trim().slice(0, 220);
      if (text) lines.push("- " + who + ": " + text);
    });
    var tab = currentTab();
    if (tab) lines.push("Current file: " + tab.path);
    var selection = currentSelection();
    if (selection) lines.push("Selected code:\n" + selection.slice(0, 1200));
    lines.push("Current ask: " + userPrompt);
    return lines.join("\n");
  }
  function isRawProviderLine(line) {
    var t = String(line || "").trim();
    if (!t) return false;
    if (/^(thread|turn|response|item|event|tool|function_call|output_item)\.?/i.test(t)) return true;
    if (/^(thread\.started|turn\.started|turn\.completed|response\.|item\.|tool_call|function_call)/i.test(t)) return true;
    if (/\[(thread\.started|turn\.started|turn\.completed|response\.|item\.|tool_call|function_call|message)\]/i.test(t)) return true;
    if (/^\{[\s\S]*"type"\s*:\s*"(thread|turn|response|item|event|tool)/i.test(t)) return true;
    if (/^\{[\s\S]*"type"\s*:\s*"[^"]+\.[^"]+"/i.test(t)) return true;
    if (/^(delta|reasoning|output_text|content_block|message_start|message_stop)\b/i.test(t)) return true;
    return false;
  }
  function looksLikeEditsJson(text) {
    var t = String(text || "").trim();
    if (!t) return false;
    if (/^\{\s*"edits"\s*:/.test(t)) return true;
    if (/```(?:json)?\s*\{\s*"edits"\s*:/i.test(t)) return true;
    if (/"edits"\s*:\s*\[/.test(t) && /"path"\s*:/.test(t) && /"content"\s*:/.test(t)) return true;
    return false;
  }
  function classifyTaskMode(prompt, explicit) {
    var mode = String(explicit || "").trim().toLowerCase();
    if (mode === "ask" || mode === "edit") return mode;
    var text = String(prompt || "").trim();
    if (!text) return "ask";
    var lower = text.toLowerCase();
    if (/\b(?:do\s+not|don't|dont|never)\s+(?:edit|modify|change|write)\b|\bno\s+(?:file\s+)?edits?\b/.test(lower)) return "ask";
    var askScore = 0;
    var editScore = 0;
    if (text.indexOf("?") >= 0) askScore += 2;
    if (/\b(explain|describe|summarize|summary|clarify|overview|what(?:'s|\s+is|\s+are|\s+does|\s+do)|how(?:\s+does|\s+do|\s+is|\s+are|\s+can|\s+should)?|why(?:\s+does|\s+do|\s+is|\s+are)?|where(?:\s+is|\s+are|\s+does)?|which|who|when|tell\s+me|show\s+me|list|find|search|look\s+up|compare|difference|derived|derivation|meaning\s+of)\b/i.test(text)) askScore += 2;
    if (/\b(edit|fix|change|modify|update|refactor|implement|patch|apply|add|remove|delete|rename|replace|write|create|insert|migrate|generate\s+code|make\s+changes?|update\s+the\s+file)\b/i.test(text)) editScore += 2;
    if (/^(please\s+)?(explain|describe|summarize|what|how|why|where|which|who|when)\b/.test(lower)) askScore += 3;
    if (/^(please\s+)?(fix|edit|change|update|implement|add|remove|delete|refactor|create|write|patch)\b/.test(lower)) editScore += 3;
    if (askScore > editScore) return "ask";
    if (editScore > askScore) return "edit";
    return "ask";
  }
  function extractEditsContents(text) {
    var raw = String(text || "");
    var parts = [];
    var fence = raw.match(/```(?:json)?\s*(\{[\s\S]*?\})\s*```/i);
    var blob = fence ? fence[1] : (/^\s*\{/.test(raw) ? raw : "");
    if (!blob) return parts;
    try {
      var payload = JSON.parse(blob);
      var edits = payload && payload.edits;
      if (!Array.isArray(edits)) return parts;
      edits.forEach(function (item) {
        if (item && typeof item.content === "string" && item.content.trim()) parts.push(item.content.trim());
      });
    } catch (_) {}
    return parts;
  }
  function humanizeAnswer(answer, taskMode) {
    var raw = String(answer || "");
    if (!raw.trim()) return { text: "", diagnostics: "" };
    var mode = taskMode === "edit" ? "edit" : "ask";
    if (!looksLikeEditsJson(raw) && !isRawProviderLine(raw) && !looksLikeProtocolDump(raw)) {
      return { text: raw.trim(), diagnostics: "" };
    }
    var diag = raw;
    var stripped = raw
      .replace(/```(?:json)?\s*\{[\s\S]*?"edits"[\s\S]*?\}\s*```/ig, "")
      .replace(/\{\s*"edits"\s*:\s*\[[\s\S]*\]\s*\}/g, "")
      .trim();
    if (stripped && !looksLikeEditsJson(stripped)) {
      return { text: stripped, diagnostics: diag };
    }
    var contents = extractEditsContents(raw);
    if (mode === "ask" && contents.length) {
      return { text: contents.join("\n\n"), diagnostics: diag };
    }
    if (mode === "edit") {
      return { text: "Proposed changes are ready for review.", diagnostics: diag };
    }
    return { text: "", diagnostics: diag };
  }
  function looksLikeProtocolDump(text) {
    var t = String(text || "").trim();
    if (!t) return false;
    var lines = t.split(/\r?\n/).filter(Boolean);
    if (!lines.length) return false;
    var raw = lines.filter(isRawProviderLine).length;
    return raw >= Math.ceil(lines.length * 0.5) || (lines.length <= 3 && raw > 0);
  }
  function splitRunOutput(logs, answer, taskMode) {
    var diag = [];
    String(logs || "").split(/\r?\n/).forEach(function (line) {
      if (!line) return;
      // Provider stdout/stderr and protocol events are diagnostics-only. The
      // normal chat body comes exclusively from the runner's final answer.
      diag.push(line);
    });
    var body = String(answer || "").trim();
    var humanized = humanizeAnswer(body, taskMode || "ask");
    if (humanized.diagnostics) {
      diag.push(humanized.diagnostics);
      body = humanized.text;
    } else if (body && (isRawProviderLine(body) || looksLikeProtocolDump(body) || looksLikeEditsJson(body))) {
      diag.push(body);
      body = "";
      var again = humanizeAnswer(body, taskMode || "ask");
      if (again.diagnostics) {
        diag.push(again.diagnostics);
        body = again.text;
      } else {
        body = again.text || body;
      }
    } else {
      body = humanized.text || body;
    }
    return { text: body || "", diagnostics: diag.join("\n") };
  }
  function filterOutputLines(text) {
    var kept = [];
    String(text || "").split(/\r?\n/).forEach(function (line) {
      var t = String(line || "");
      if (!t.trim()) return;
      if (isRawProviderLine(t) || looksLikeProtocolDump(t) || looksLikeEditsJson(t)) return;
      kept.push(t);
    });
    return kept.join("\n");
  }
  function parseDiagnosticLines(text, source) {
    var rows = [];
    String(text || "").split(/\r?\n/).forEach(function (line) {
      var t = String(line || "").trim();
      if (!t || isRawProviderLine(t) || looksLikeProtocolDump(t)) return;
      var py = t.match(/File "([^"]+)", line (\d+)/);
      if (py) {
        rows.push({
          path: py[1].replace(/\\/g, "/"),
          line: Number(py[2]),
          column: 1,
          message: t,
          severity: "error",
          source: source || "runtime"
        });
        return;
      }
      var loc = t.match(/^([\w./\\-]+\.\w+):(\d+)(?::(\d+))?:\s*(error|warning|info)[:\s]+(.+)$/i);
      if (loc) {
        rows.push({
          path: loc[1].replace(/\\/g, "/"),
          line: Number(loc[2]),
          column: Number(loc[3] || 1),
          message: loc[5],
          severity: String(loc[4] || "error").toLowerCase(),
          source: source || "runtime"
        });
      }
    });
    return rows;
  }
  function collectSources(msg, run, activity) {
    var files = [];
    function add(path) {
      var p = String(path || "").replace(/\\/g, "/").replace(/^["'`]+|["'`]+$/g, "").trim();
      if (!p || p.length > 220) return;
      if (files.indexOf(p) >= 0) return;
      files.push(p);
    }
    ((run && run.sources) || []).forEach(add);
    ((msg && msg.sources) || []).forEach(add);
    ((activity && activity.explore && activity.explore.files) || []).forEach(add);
    ((msg && msg.changedFiles) || []).forEach(add);
    return files.slice(0, 24);
  }
  function activityBaseName(path) {
    var parts = String(path || "").replace(/\\/g, "/").split("/");
    return parts[parts.length - 1] || path;
  }
  function collectActivityFiles(blob) {
    var files = [];
    function add(path) {
      var p = String(path || "").replace(/\\/g, "/").replace(/^["'`]+|["'`]+$/g, "").trim();
      if (!p || p.length > 220) return;
      if (p.indexOf("://") >= 0) return;
      if (!/[A-Za-z0-9_.\-]+\/[A-Za-z0-9_./\-]+\.[A-Za-z0-9]+/.test(p) && !/\.[A-Za-z0-9]{1,12}$/.test(p)) return;
      if (files.indexOf(p) < 0) files.push(p);
    }
    String(blob || "").replace(/(?:path[=:\s]+|Reading\s+|read(?:_file)?\s*\(\s*|file[=:\s]+)["']?([A-Za-z0-9_.\-]+\/[A-Za-z0-9_./\-]+\.[A-Za-z0-9]+)/gi, function (_, p) {
      add(p);
      return _;
    });
    String(blob || "").replace(/\b([A-Za-z0-9_.\-]+\/[A-Za-z0-9_./\-]+\.[A-Za-z0-9]{1,12})\b/g, function (_, p) {
      add(p);
      return _;
    });
    String(blob || "").replace(/(^|[^A-Za-z0-9_./\-])([A-Za-z0-9_.\-]+\.(?:py|js|ts|tsx|jsx|css|html|md|json|ya?ml|toml|sql|sh|ps1))\b/gi, function (_, prefix, p) {
      add(p);
      return _;
    });
    return files;
  }
  function extractReadPaths(command) {
    var files = collectActivityFiles(command);
    String(command || "").replace(/-(?:Literal)?Path\s+(?:"([^"]+)"|'([^']+)'|(\S+))/gi, function (_, a, b, c) {
      collectActivityFiles(String(a || b || c || "")).forEach(function (p) {
        if (files.indexOf(p) < 0) files.push(p);
      });
      return _;
    });
    return files;
  }
  function isSearchCommand(cmd) {
    return /(?:^|[^\w-])(?:rg(?:\.exe)?|ripgrep|grep|git\s+grep|findstr|select-string)\b/i.test(String(cmd || ""));
  }
  function isReadCommand(cmd) {
    var text = String(cmd || "");
    if (isSearchCommand(text)) return false;
    return /(?:^|[^\w-])(?:get-content|gc|cat|sed|less|more|head|tail|nl|type)\b/i.test(text);
  }
  function commandFailed(item) {
    var status = String((item && item.status) || "").toLowerCase();
    if (status === "failed" || status === "error" || status === "errored") return true;
    return /cannot find path|wildcard|parameter cannot be found|is not recognized|no such file/i.test(String((item && item.detail) || ""));
  }
  function providerInvestigationFiles(run) {
    var files = [];
    function add(path) {
      extractReadPaths(String(path || "").replace(/\\/g, "/")).forEach(function (p) {
        if (files.indexOf(p) < 0) files.push(p);
      });
    }
    ((run && run.tool_activity) || []).forEach(function (item) {
      if (!item || (item.type && item.type !== "command_execution")) return;
      if (!isReadCommand(item.name || item.command) || commandFailed(item)) return;
      add(item.name || item.command);
    });
    String((run && run.logs) || "").split(/\r?\n/).forEach(function (line) {
      if (/^\[tool\]/i.test(line) && isReadCommand(line) && !isSearchCommand(line)) add(line);
    });
    return files;
  }
  /**
   * Build activity steps from runtime/tool log evidence only — never invent unseen steps.
   */
  function parseActivityEvidence(source, opts) {
    opts = opts || {};
    var blob = String(source || "");
    var running = !!opts.running;
    var readLines = blob.split(/\r?\n/).filter(function (line) {
      return /\[tool\].*(?:get-content|gc|cat|sed|type)\b/i.test(line) || /Reading repository file/i.test(line);
    });
    var files = collectActivityFiles(readLines.join("\n"));
    var exploreCount = (typeof opts.filesInspected === "number" && isFinite(opts.filesInspected) && opts.filesInspected >= 0)
      ? opts.filesInspected
      : files.length;
    if (isNaN(exploreCount) || exploreCount < 0) exploreCount = files.length;
    var steps = [];
    function has(re) { return re.test(blob); }
    function addStep(id, label) {
      if (steps.some(function (s) { return s.id === id; })) {
        var existing = steps.find(function (s) { return s.id === id; });
        if (label && existing) existing.label = label;
        return;
      }
      steps.push({ id: id, label: label, state: "done" });
    }
    if (exploreCount > 0 || has(/\bexplor(?:e|ing|ed)\b/i) || files.length) {
      var n = Math.max(exploreCount, files.length);
      if (n > 0) addStep("explore", "Exploring " + n + " file" + (n === 1 ? "" : "s"));
    }
    if (has(/\bResolving repo\b/i)) addStep("resolve", "Resolving repo");
    if (has(/\bLoading instructions\b/i)) addStep("instructions", "Loading instructions");
    if (has(/\b(Matching skill|Finding relevant skill)\b/i)) addStep("skill", "Matching skill");
    if (has(/\bExpanding local search\b/i)) addStep("expand", "Expanding local search");
    if (has(/\b(search(?:ing)?\s+repository|Searching repo|grep|glob|ripgrep|find_files?|codebase_search|workspace.?search)\b/i)) {
      addStep("search", has(/Codex searching repository/i) ? "Codex searching repository" : "Searching repo");
    }
    var foundMatch = blob.match(/Found\s+(\d+)\s+sources?/i);
    if (foundMatch) addStep("sources", "Found " + foundMatch[1] + " sources");
    if (has(/\bBuilding context\b/i)) addStep("context", "Building context");
    if (has(/\bAsking\s+(Codex|Claude|Cursor|Gemini)\b/i)) {
      var ask = blob.match(/Asking\s+(Codex|Claude|Cursor|Gemini)/i);
      addStep("ask", ask ? ("Asking " + ask[1]) : "Asking provider");
    }
    if (has(/\b(No model invoked|Model not invoked|Not enough repository evidence)\b/i)) {
      addStep("gate", "No model invoked · 0 tokens");
    }
    if (has(/\b(Reading\s+|read_file|\[tool\]\s*read\b|tool_call\s+read\b|open_file|view_file)\b/i) || files.length) {
      var last = files.length ? files[files.length - 1] : "";
      addStep("read", last ? ("Reading " + activityBaseName(last)) : "Reading file");
    }
    if (has(/\b(checking related logic|related logic|analyz(?:e|ing)|compliance rules|inspect(?:ing)? logic)\b/i)) {
      addStep("logic", "Checking related logic");
    }
    if (has(/\b(running tests?|pytest|unittest|npm test|tests?\s+(?:passed|failed|ran)|\[tool\]\s*test)\b/i)) {
      addStep("tests", "Running tests");
    }
    if (has(/\b(reviewing changes|prepared?\s+\d+\s+file|proposal|diff\b|apply_patch|edit_file)\b/i) || opts.hasProposal) {
      addStep("review", "Reviewing changes");
    }
    // Avoid treating raw protocol turn/response events as user-visible steps.
    if (has(/\bpreparing response\b/i) || opts.hasAnswer) {
      addStep("prepare", "Preparing response");
    }
    if (running && steps.length) {
      steps.forEach(function (s) { s.state = "done"; });
      steps[steps.length - 1].state = "current";
    }
    var testsInfo = testsFromText(blob, opts.tests || null);
    var testsRan = 0;
    if (testsInfo && typeof testsInfo.count === "number") testsRan = testsInfo.count;
    else if (testsInfo && testsInfo.label) {
      var tm = String(testsInfo.label).match(/(\d+)/);
      testsRan = tm ? parseInt(tm[1], 10) : (testsInfo.passed ? 1 : 0);
    } else {
      var ran = blob.match(/(?:Ran|ran)\s+(\d+)\s+tests?/i) || blob.match(/(\d+)\s+tests?\s+(?:passed|failed|ran)/i);
      if (ran) testsRan = parseInt(ran[1], 10) || 0;
    }
    var issues = 0;
    var issueMatch = blob.match(/(\d+)\s+issues?\s+found/i);
    if (issueMatch) issues = parseInt(issueMatch[1], 10) || 0;
    else if (has(/\b(error|failed|exception)\b/i) && opts.status === "failed") issues = 1;
    var linesAnalyzed = 0;
    var lineMatch = blob.match(/(\d+)\s+lines?\s+(?:analyzed|read|scanned)/i);
    if (lineMatch) linesAnalyzed = parseInt(lineMatch[1], 10) || 0;
    var startedAt = Number(opts.startedAt || 0);
    var elapsedMs = Number(opts.elapsedMs || 0);
    if (!elapsedMs && startedAt) elapsedMs = Math.max(0, Date.now() - startedAt);
    return {
      steps: steps,
      explore: {
        files: files.slice(0, 24),
        count: Math.max(exploreCount, files.length),
        scanned: Math.max(exploreCount, files.length),
        lines: linesAnalyzed,
        elapsedMs: elapsedMs
      },
      testsRan: testsRan,
      issues: issues,
      planning: false
    };
  }
  function renderActivityProgress(msg) {
    var running = msg.status === "running";
    var activity = msg.activity || parseActivityEvidence((msg.diagnostics || "") + "\n" + (msg.text || ""), {
      running: running,
      startedAt: msg.startedAt
    });
    if (!running && activity && activity.steps) {
      activity.steps.forEach(function (step) { step.state = "done"; });
    }
    var steps = activity.steps || [];
    var html = '<section class="climate-activity-progress" data-activity-root="' + escapeHtml(msg.id) + '">';
    html += '<div class="climate-activity-head"><span class="climate-activity-title">Working on your request…</span>';
    html += '<button type="button" class="climate-activity-chevron" data-activity-collapse aria-label="Collapse activity">▾</button></div>';
    if (steps.length) {
      html += '<ol class="climate-activity-steps">';
      steps.forEach(function (step) {
        var stateCls = step.state || "done";
        html += '<li class="climate-activity-step is-' + escapeHtml(stateCls) + (step.id === "explore" ? " is-explore" : "") + '" data-step="' + escapeHtml(step.id) + '">';
        if (stateCls === "done") html += '<span class="climate-activity-mark" aria-hidden="true">✓</span>';
        else if (stateCls === "current") html += '<span class="climate-activity-mark is-pulse" aria-hidden="true"><i></i><i></i><i></i></span>';
        else html += '<span class="climate-activity-mark is-pending" aria-hidden="true"></span>';
        if (step.id === "explore") {
          html += '<button type="button" class="climate-activity-step-label is-explore" data-activity-explore="' + escapeHtml(msg.id) + '" aria-expanded="false">' + escapeHtml(step.label) + ' <span aria-hidden="true">›</span></button>';
        } else {
          html += '<span class="climate-activity-step-label">' + escapeHtml(step.label) + '</span>';
        }
        html += '</li>';
      });
      html += '</ol>';
      var explore = activity.explore || {};
      if ((explore.files && explore.files.length) || explore.count) {
        html += '<div class="climate-activity-explore" data-explore-panel="' + escapeHtml(msg.id) + '" hidden>';
        html += '<div class="climate-activity-explore-head"><span>Exploring ' + escapeHtml(String(explore.count || explore.files.length || 0)) + ' file' + ((explore.count || explore.files.length) === 1 ? "" : "s") + '</span>';
        html += '<button type="button" class="climate-activity-chevron" data-activity-explore="' + escapeHtml(msg.id) + '" aria-label="Collapse files">▴</button></div>';
        html += '<ul class="climate-activity-explore-files">';
        (explore.files || []).forEach(function (path) {
          html += '<li><i aria-hidden="true"></i><span>' + escapeHtml(path) + '</span></li>';
        });
        html += '</ul>';
        html += '<div class="climate-activity-explore-stats">';
        html += '<div><span>Files scanned</span><b>' + escapeHtml(String(explore.scanned || explore.files.length || 0)) + '</b></div>';
        html += '<div><span>Lines analyzed</span><b>' + escapeHtml(String(explore.lines || "—")) + '</b></div>';
        html += '<div><span>Time elapsed</span><b>' + escapeHtml(formatElapsed(explore.elapsedMs || 0)) + '</b></div>';
        html += '</div>';
        html += '<div class="climate-activity-wave" aria-hidden="true"></div>';
        html += '</div>';
      }
    }
    html += '</section>';
    return html;
  }
  function renderTokenEfficiency(msg) {
    var te = msg.tokenEfficiency;
    var provider = msg.provider || "";
    if ((provider && provider !== "codex") || (!provider && !(te && te.eligible))) return "";
    if (msg.status !== "completed" && !(te && te.status)) return "";
    te = te || { status: "Not measured", climate: {}, direct: null, comparison: null };
    var status = tokenEfficiencyStatus(te);
    var cmp = te.comparison || {};
    var tone = cmp.tone || (status === "Measuring…" ? "wait" : (status === "Measured" ? "neutral" : "muted"));
    if (status === "Measured" && !cmp.tone) {
      if (cmp.difference > 0) tone = "increase";
      else if (cmp.difference < 0) tone = "savings";
    }
    if (status === "Measuring…") tone = "wait";
    if (status === "Not measured" || status === "Benchmark unavailable" || status === "Not comparable" || status === "Failed" || status === "Cancelled") {
      tone = "muted";
    }
    var climateTotal = te.climate && te.climate.total;
    var directTotal = te.direct && te.direct.usage ? te.direct.usage.total_tokens : null;
    var html = '<section class="climate-token-efficiency" data-te-run="' + escapeHtml(msg.runId || "") + '" data-te-status="' + escapeHtml(status) + '" data-te-tone="' + escapeHtml(tone) + '">';
    html += '<div class="climate-te-head"><span>Token Efficiency</span>';
    if (status === "Measured") html += '<small class="climate-te-status is-save">✓ Measured</small>';
    else if (status === "Measuring…") html += '<small class="climate-te-status is-wait">● Measuring</small>';
    else html += '<small class="climate-te-status is-muted">' + escapeHtml(status) + '</small>';
    html += '</div>';
    if (status === "Measured" && (cmp.primary || cmp.headline)) {
      if (!cmp.primary) cmp.primary = cmp.headline;
      html += renderTokenEfficiencyMeasured(te, msg, cmp, tone, climateTotal, directTotal);
    } else if (status === "Measured") {
      html += '<div class="climate-te-result is-muted">Token comparison unavailable</div>';
    } else if (status === "Measuring…") {
      html += renderTokenEfficiencyMeasuring(te);
    } else {
      html += '<div class="climate-te-stack">';
      html += '<div class="climate-te-kv"><span>' + escapeHtml((te && te.execution_mode === "direct") ? "Direct run total" : "CLIMATE run total") + '</span><b>' + escapeHtml(formatTokenExact((te && te.execution_mode === "direct") ? (te.direct && te.direct.usage && te.direct.usage.total_tokens) : climateTotal)) + '</b></div>';
      html += '<div class="climate-te-kv"><span>Preflight estimate</span><b title="CLIMATE Context Resolver estimate (not provider usage)">' + escapeHtml(formatTokenExact(te.climate && te.climate.preflight_tokens_est)) + '</b></div>';
      html += '<div class="climate-te-kv"><span>Direct baseline</span><b class="climate-te-status is-muted">' + escapeHtml(status) + '</b></div>';
      html += '</div>';
      if (te.reason) html += '<div class="climate-te-note">' + escapeHtml(te.reason) + '</div>';
    }
    html += renderTokenEfficiencyDetails(te, msg, status);
    html += '<div class="climate-te-actions">';
    if (status === "Measuring…") {
      html += '<button type="button" class="climate-btn" data-te-action="cancel" data-msg-id="' + escapeHtml(msg.id) + '">Cancel</button>';
    }     else if (status !== "Measured" && msg.runId) {
      var compareLabel = (te && te.compare_label) || (msg.executionMode === "direct" ? "Compare with CLIMATE" : "Compare with Direct");
      html += '<button type="button" class="climate-btn climate-btn-primary" data-te-action="evaluate" data-msg-id="' + escapeHtml(msg.id) + '">' + escapeHtml(compareLabel) + '</button>';
    }
    html += "</div></section>";
    return html;
  }
  function formatSignedTokens(value) {
    if (value == null || value === "" || isNaN(Number(value))) return "Unavailable";
    var n = Math.round(Number(value));
    var formatted = Math.abs(n).toLocaleString();
    if (n > 0) return "+" + formatted;
    if (n < 0) return "−" + formatted;
    return formatted;
  }
  function formatChangePct(value) {
    if (value == null || isNaN(Number(value))) return "Unavailable";
    var n = Number(value);
    var body = (n > 0 ? "+" : (n < 0 ? "−" : "")) + Math.abs(n).toFixed(1) + "%";
    return body;
  }
  function tokenEfficiencyElapsed(te) {
    var start = te && te.direct && te.direct.started_at;
    if (!start) return "";
    var t = Date.parse(start);
    if (!isFinite(t)) return "";
    return formatElapsed(Date.now() - t);
  }
  function renderTokenEfficiencyMeasured(te, msg, cmp, tone, climateTotal, directTotal) {
    var toneClass = tone === "savings" ? "is-save" : (tone === "increase" ? "is-cost" : "is-muted");
    var html = '<table class="climate-te-compare"><thead><tr><th></th><th>CLIMATE</th><th>DIRECT</th></tr></thead><tbody>';
    html += '<tr><th>Total</th><td>' + escapeHtml(formatTokenExact(climateTotal)) + '</td><td>' + escapeHtml(formatTokenExact(directTotal)) + '</td></tr>';
    html += '<tr><th>Provider runtime</th><td>' + escapeHtml(formatMs(te.climate && te.climate.runtime_ms)) + '</td><td>' + escapeHtml(formatMs(te.direct && te.direct.runtime_ms)) + '</td></tr>';
    html += '<tr><th>Files inspected</th><td>' + escapeHtml(te.climate && te.climate.files_inspected != null ? String(te.climate.files_inspected) : "Unavailable") + '</td><td>' + escapeHtml(te.direct && te.direct.files_inspected != null ? String(te.direct.files_inspected) : "Unavailable") + '</td></tr>';
    html += '</tbody></table>';
    html += '<div class="climate-te-result ' + toneClass + '">' + escapeHtml(cmp.primary) + '</div>';
    if (cmp.secondary) html += '<div class="climate-te-secondary">' + escapeHtml(cmp.secondary) + '</div>';
    html += '<div class="climate-te-kpis">';
    html += '<div class="climate-te-kv"><span>Difference</span><b class="' + toneClass + '">' + escapeHtml(formatSignedTokens(cmp.difference)) + '</b></div>';
    html += '<div class="climate-te-kv"><span>Change</span><b class="' + toneClass + '">' + escapeHtml(formatChangePct(cmp.percent)) + '</b></div>';
    html += '<div class="climate-te-kv"><span>Relative usage</span><b>' + escapeHtml(cmp.relative != null ? (Number(cmp.relative).toFixed(2) + "× Direct") : "Unavailable") + '</b></div>';
    html += '</div>';
    if (te.comparability_note) html += '<div class="climate-te-note">' + escapeHtml(te.comparability_note) + '</div>';
    return html;
  }
  function renderTokenEfficiencyMeasuring(te) {
    var elapsed = tokenEfficiencyElapsed(te) || formatMs(te.direct && te.direct.runtime_ms) || "0s";
    var html = '<div class="climate-te-split">';
    html += '<div class="climate-te-col"><div class="climate-te-col-label">CLIMATE</div>';
    html += '<div class="climate-te-kv"><span>Run total</span><b>' + escapeHtml(formatTokenExact(te.climate && te.climate.total)) + '</b></div>';
    html += '<div class="climate-te-kv"><span>Preflight estimate</span><b>' + escapeHtml(formatTokenExact(te.climate && te.climate.preflight_tokens_est)) + '</b></div>';
    html += '</div>';
    html += '<div class="climate-te-col"><div class="climate-te-col-label">DIRECT</div>';
    html += '<div class="climate-te-kv"><span>Fresh read-only benchmark running…</span><b class="climate-te-status is-wait">' + escapeHtml(elapsed) + '</b></div>';
    html += '</div></div>';
    html += '<div class="climate-te-note">Same repo · commit · model · read-only</div>';
    return html;
  }
  function renderTokenEfficiencyDetails(te, msg, status) {
    var html = '<details class="climate-te-details"><summary>Benchmark details</summary><table>';
    html += '<thead><tr><th></th><th>CLIMATE</th><th>DIRECT</th></tr></thead><tbody>';
    var rows = [
      ["Input", te.climate && te.climate.input, te.direct && te.direct.usage && te.direct.usage.input_tokens, "tokens"],
      ["↳ Cached portion", te.climate && te.climate.cached_input, te.direct && te.direct.usage && te.direct.usage.cached_input_tokens, "subset"],
      ["Output", te.climate && te.climate.output, te.direct && te.direct.usage && te.direct.usage.output_tokens, "tokens"],
      ["Total", te.climate && te.climate.total, te.direct && te.direct.usage && te.direct.usage.total_tokens, "tokens"],
      ["Provider runtime", te.climate && te.climate.runtime_ms, te.direct && te.direct.runtime_ms, "runtime"],
      ["Files actually inspected", te.climate && te.climate.files_inspected, te.direct && te.direct.files_inspected, "count"],
      ["Search-matched files", te.climate && te.climate.search_matched_files, te.direct && te.direct.search_matched_files, "count"],
      ["Tool calls", te.climate && te.climate.tool_calls, te.direct && te.direct.tool_executions, "count"]
    ];
    rows.forEach(function (row) {
      var kind = row[3];
      var climateVal = kind === "runtime" ? formatMs(row[1]) : (kind === "count" ? (row[1] == null ? "Unavailable" : String(row[1])) : formatTokenExact(row[1]));
      var directVal;
      if (row[2] == null && status === "Not measured") directVal = "Not measured";
      else if (kind === "runtime") directVal = formatMs(row[2]);
      else if (kind === "count") directVal = row[2] == null ? (status === "Not measured" ? "Not measured" : "Unavailable") : String(row[2]);
      else directVal = formatTokenExact(row[2]);
      html += '<tr class="' + (kind === "subset" ? "is-subset" : "") + '"><th>' + escapeHtml(row[0]) + "</th><td>" + escapeHtml(climateVal) + "</td><td>" + escapeHtml(directVal) + "</td></tr>";
    });
    html += '<tr><th>Success</th><td>' + escapeHtml(msg.status === "completed" ? "Yes" : "No") + "</td><td>" + escapeHtml(te.direct && te.direct.success ? "Yes" : (te.direct ? "No" : "—")) + "</td></tr>";
    html += '<tr><th>Candidate sources</th><td>' + escapeHtml(te.climate && te.climate.source_candidates != null ? String(te.climate.source_candidates) : "Unavailable") + "</td><td>—</td></tr>";
    html += '<tr><th>CLIMATE session</th><td>' + escapeHtml(te.climate && te.climate.session_reused ? "Resumed" : (te.climate && te.climate.session_fresh ? "Fresh" : "Unavailable")) + '</td><td>Fresh ephemeral</td></tr>';
    html += "</tbody></table>";
    html += '<p class="climate-te-legend">Cached portion is a subset of Input, not an extra addend. Candidate sources are Context Resolver hints. Search-matched files are unique paths in successful repository search output. Files actually inspected are files whose contents were opened/read. Failed commands are not inspections. Preflight estimate is local and is not provider usage. Missing fields stay Unavailable. Provider runtime is the provider started/finished span; chat End-to-end runtime is the browser wall clock for the CLIMATE run.</p>';
    html += "</details>";
    return html;
  }
  function renderActivityComplete(msg) {
    var taskMode = msg.taskMode || "ask";
    var isEdit = taskMode === "edit" && !!(msg.proposal && msg.proposal.state === "pending" || (msg.changedFiles || []).length);
    var activity = msg.activity || parseActivityEvidence((msg.diagnostics || "") + "\n" + (msg.text || ""), {
      running: false,
      elapsedMs: msg.elapsedMs,
      tests: msg.tests,
      hasProposal: !!(msg.proposal && msg.proposal.state === "pending"),
      status: msg.status
    });
    // Candidates/sources are preflight hints, not proof that the provider read a file.
    var exploreCount = Number(msg.filesInspected);
    if (!isFinite(exploreCount) || exploreCount < 0) exploreCount = 0;
    var testsRan = activity.testsRan || (msg.tests && msg.tests.count) || 0;
    if (!testsRan && msg.tests && msg.tests.label) {
      var tm = String(msg.tests.label).match(/(\d+)/);
      testsRan = tm ? parseInt(tm[1], 10) : 0;
    }
    var issues = typeof activity.issues === "number" ? activity.issues : 0;
    var parts = [];
    if (msg.elapsedMs > 0) {
      parts.push("End-to-end runtime " + formatElapsed(msg.elapsedMs));
    }
    if (exploreCount > 0) parts.push("Explored " + exploreCount + " file" + (exploreCount === 1 ? "" : "s"));
    var searchMatches = Number(msg.searchMatchedFiles);
    if (isFinite(searchMatches) && searchMatches > 0 && searchMatches !== exploreCount) {
      parts.push(searchMatches + " search match" + (searchMatches === 1 ? "" : "es"));
    }
    var candidateCount = (msg.sources || []).length;
    if (candidateCount > 0 && candidateCount !== exploreCount) {
      parts.push(candidateCount + " candidate source" + (candidateCount === 1 ? "" : "s"));
    }
    if (isEdit && testsRan > 0) parts.push("Ran " + testsRan + " test" + (testsRan === 1 ? "" : "s"));
    if (isEdit && (activity.explore || activity.steps || testsRan || exploreCount)) {
      parts.push(issues + " issue" + (issues === 1 ? "" : "s") + " found");
    }
    if (!parts.length && !(msg.elapsedMs > 0)) return "";
    var html = '<section class="climate-activity-complete">';
    html += '<div class="climate-ask-runline">';
    html += '<span>' + escapeHtml(parts.join(" · ")) + '</span>';
    html += '<div class="climate-activity-complete-actions">';
    if ((msg.sources || []).length) {
      html += '<button type="button" class="climate-btn climate-activity-btn" data-sources-toggle="' + escapeHtml(msg.id) + '">Sources</button>';
    }
    if (msg.diagnostics) {
      html += '<button type="button" class="climate-btn climate-activity-btn" data-activity-details="' + escapeHtml(msg.id) + '">Details</button>';
    }
    if (isEdit && msg.proposal && msg.proposal.state === "pending") {
      html += '<button type="button" class="climate-btn climate-activity-btn is-accent" data-chat-action="review" data-msg-id="' + escapeHtml(msg.id) + '">Show Changes</button>';
    }
    html += '</div></div>';
    html += '</section>';
    return html;
  }
  function extractSummary(text, proposal) {
    var summary = [];
    String(text || "").split(/\r?\n/).forEach(function (line) {
      var m = line.match(/^\s*(?:[-*•]|✓|✔|\d+[.)])\s+(.+)/);
      if (m && m[1] && m[1].length < 120) summary.push(m[1].trim());
    });
    var edits = (proposal && proposal.edits) || [];
    if (edits.length) summary.push("Prepared " + edits.length + " file change" + (edits.length === 1 ? "" : "s"));
    return summary.slice(0, 8);
  }
  function changedFilesFrom(proposal, text) {
    var files = [];
    ((proposal && proposal.edits) || []).forEach(function (edit) {
      if (edit && edit.path && files.indexOf(edit.path) < 0) files.push(edit.path);
    });
    String(text || "").replace(/(?:^|\s)([A-Za-z0-9_.\-]+\/[A-Za-z0-9_./\-]+\.[A-Za-z0-9]+)/g, function (_, path) {
      if (files.indexOf(path) < 0 && files.length < 12) files.push(path);
      return _;
    });
    return files;
  }
  function activeSession() {
    return state.chat.sessions.find(function (s) { return s.id === state.chat.activeId; }) || null;
  }
  function loadChatStore() {
    try {
      var raw = localStorage.getItem(chatStorageKey());
      if (!raw) {
        var legacy = localStorage.getItem(legacyChatStorageKey());
        if (legacy) {
          raw = legacy;
          localStorage.setItem(chatStorageKey(), legacy);
        }
      }
      var saved = JSON.parse(raw || "{}");
      state.chat.sessions = Array.isArray(saved.sessions) ? saved.sessions.slice(0, 40) : [];
      state.chat.sessions.forEach(function (session) {
        ensureUsage(session);
        if (isDefaultSessionTitle(session.title)) session.title = defaultSessionTitle();
      });
      state.chat.activeId = saved.activeId || (state.chat.sessions[0] && state.chat.sessions[0].id) || "";
      if (!state.chat.activeId) newChatSession(true);
      else renderChat();
    } catch (_) {
      state.chat = { activeId: "", sessions: [] };
      newChatSession(true);
    }
  }
  function saveChatStore() {
    localStorage.setItem(chatStorageKey(), JSON.stringify({
      activeId: state.chat.activeId,
      sessions: state.chat.sessions.slice(0, 40)
    }));
  }
  function captureContextPrefs() {
    var scope = currentWorkspaceScope();
    return {
      contextScope: scope.scope,
      repositoryId: scope.repositoryId,
      attachedContext: state.attachedContext.slice(0, 12),
      selectedFilePaths: state.selectedFiles.slice(0, 24),
      activeFile: state.active || ""
    };
  }
  function applyContextPrefs(ctx) {
    if (!ctx) return;
    var scopeSelect = document.getElementById("climate-context-scope");
    if (scopeSelect) {
      if (ctx.contextScope === "general" || ctx.contextScope === "all") scopeSelect.value = ctx.contextScope;
      else if (ctx.repositoryId) scopeSelect.value = ctx.repositoryId;
      if (scopeSelect._climateDd && window.ClimateSelect) window.ClimateSelect.sync(scopeSelect);
    }
    if (Array.isArray(ctx.attachedContext)) {
      state.attachedContext = normalizeAttachedList(ctx.attachedContext, []);
      renderAttached();
    }
  }
  function newChatSession(silent) {
    var session = {
      id: uid("session"),
      title: defaultSessionTitle(),
      createdAt: Date.now(),
      updatedAt: Date.now(),
      provider: providerSelect.value || "",
      model: modelSelect.value || "",
      executionMode: currentExecutionMode(),
      lastRunProvider: "",
      workspace: workspace,
      repositoryId: repoId(),
      branch: (branchSelect && branchSelect.value) || "",
      context: captureContextPrefs(),
      messages: [],
      usage: emptyUsage()
    };
    state.chat.sessions.unshift(session);
    state.chat.activeId = session.id;
    state.streamingMsgId = "";
    saveChatStore();
    renderChat();
    renderUsageChrome(session);
    if (!silent) setStatus("New session");
    return session;
  }
  function titleFromPrompt(prompt) {
    var t = String(prompt || "").replace(/\s+/g, " ").trim();
    if (!t) return defaultSessionTitle();
    return t.length > 52 ? (t.slice(0, 49) + "…") : t;
  }
  function closeChatPopovers() {
    if (historyPanel) historyPanel.hidden = true;
    if (menuPanel) menuPanel.hidden = true;
    if (contextPanel) contextPanel.hidden = true;
    if (usagePanel) usagePanel.hidden = true;
    if (tokenPill) tokenPill.setAttribute("aria-expanded", "false");
  }
  function closeClimateDropdowns(except) {
    document.querySelectorAll(".climate-dd.is-open").forEach(function (dd) {
      if (except && dd === except) return;
      dd.classList.remove("is-open");
      var trigger = dd.querySelector(".climate-dd-trigger");
      if (trigger) trigger.setAttribute("aria-expanded", "false");
      var menu = dd._menu || dd.querySelector(".climate-dd-menu");
      if (!menu) return;
      menu.hidden = true;
      menu.classList.remove("is-portal", "is-up");
      menu.style.position = "";
      menu.style.top = "";
      menu.style.left = "";
      menu.style.right = "";
      menu.style.bottom = "";
      menu.style.width = "";
      menu.style.maxHeight = "";
      menu.style.zIndex = "";
      if (menu.parentNode !== dd) dd.appendChild(menu);
    });
  }
  function positionClimateDropdownMenu(wrap, menu) {
    var trigger = wrap.querySelector(".climate-dd-trigger");
    if (!trigger || !menu) return;
    var rect = trigger.getBoundingClientRect();
    var gap = 4;
    var maxH = 240;
    var viewportH = window.innerHeight || document.documentElement.clientHeight;
    var viewportW = window.innerWidth || document.documentElement.clientWidth;
    var spaceBelow = Math.max(0, viewportH - rect.bottom - gap);
    var spaceAbove = Math.max(0, rect.top - gap);
    var preferUp = spaceBelow < Math.min(maxH, 160) && spaceAbove > spaceBelow;
    if (menu.parentNode !== document.body) document.body.appendChild(menu);
    menu.classList.add("is-portal");
    var shell = document.querySelector(".climate-shell");
    if (shell) {
      var accent = getComputedStyle(shell).getPropertyValue("--cl-accent").trim();
      var border = getComputedStyle(shell).getPropertyValue("--cl-border").trim();
      if (accent) menu.style.setProperty("--cl-accent", accent);
      if (border) menu.style.setProperty("--cl-border", border);
    }
    menu.hidden = false;
    menu.style.position = "fixed";
    menu.style.zIndex = "10050";
    menu.style.right = "auto";
    menu.style.bottom = "auto";
    var width = Math.max(rect.width, 148);
    var left = rect.left;
    if (left + width > viewportW - 8) left = Math.max(8, viewportW - width - 8);
    if (left < 8) left = 8;
    menu.style.left = Math.round(left) + "px";
    menu.style.width = Math.round(width) + "px";
    var avail = preferUp ? spaceAbove : spaceBelow;
    var capped = Math.max(96, Math.min(maxH, avail || maxH));
    menu.style.maxHeight = capped + "px";
    // Measure after paint constraints
    var menuH = Math.min(menu.scrollHeight || capped, capped);
    if (!preferUp && spaceBelow < Math.min(menuH, 120) && spaceAbove > spaceBelow) {
      preferUp = true;
      capped = Math.max(96, Math.min(maxH, spaceAbove));
      menu.style.maxHeight = capped + "px";
      menuH = Math.min(menu.scrollHeight || capped, capped);
    }
    if (preferUp) {
      menu.classList.add("is-up");
      menu.style.top = Math.round(Math.max(8, rect.top - gap - menuH)) + "px";
    } else {
      menu.classList.remove("is-up");
      menu.style.top = Math.round(rect.bottom + gap) + "px";
      // Clamp if near bottom edge
      if (rect.bottom + gap + menuH > viewportH - 8) {
        menu.style.maxHeight = Math.max(96, Math.min(maxH, viewportH - rect.bottom - gap - 8)) + "px";
      }
    }
  }
  function openClimateDropdown(selectEl) {
    if (!selectEl || !selectEl._climateDd) return;
    var wrap = selectEl._climateDd;
    var menu = wrap._menu || wrap.querySelector(".climate-dd-menu");
    var trigger = wrap.querySelector(".climate-dd-trigger");
    if (!menu || !trigger) return;
    syncClimateDropdown(selectEl);
    wrap.classList.add("is-open");
    trigger.setAttribute("aria-expanded", "true");
    positionClimateDropdownMenu(wrap, menu);
  }
  function syncClimateDropdown(selectEl) {
    if (!selectEl || !selectEl._climateDd) return;
    var dd = selectEl._climateDd;
    var valueEl = dd.querySelector(".climate-dd-value");
    var menu = dd._menu || dd.querySelector(".climate-dd-menu");
    if (!menu || !valueEl) return;
    var selected = selectEl.options[selectEl.selectedIndex];
    valueEl.textContent = selected ? selected.textContent : (selectEl.value || "Select");
    menu.innerHTML = "";
    Array.prototype.forEach.call(selectEl.options, function (opt) {
      var btn = document.createElement("button");
      btn.type = "button";
      btn.className = "climate-dd-option" + (opt.selected ? " is-selected" : "") + (opt.disabled ? " is-disabled" : "");
      btn.setAttribute("role", "option");
      btn.setAttribute("data-value", opt.value);
      if (opt.selected) btn.setAttribute("aria-selected", "true");
      if (opt.disabled) btn.disabled = true;
      btn.textContent = opt.textContent;
      btn.addEventListener("click", function (event) {
        event.preventDefault();
        event.stopPropagation();
        if (opt.disabled) return;
        selectEl.value = opt.value;
        selectEl.dispatchEvent(new Event("change", { bubbles: true }));
        syncClimateDropdown(selectEl);
        closeClimateDropdowns();
      });
      menu.appendChild(btn);
    });
  }
  function enhanceClimateSelect(selectEl) {
    if (!selectEl || selectEl._climateDd) {
      if (selectEl && selectEl._climateDd) syncClimateDropdown(selectEl);
      return;
    }
    var wrap = document.createElement("div");
    wrap.className = "climate-dd";
    wrap.setAttribute("data-for", selectEl.id || "");
    selectEl.parentNode.insertBefore(wrap, selectEl);
    wrap.appendChild(selectEl);
    selectEl.classList.add("climate-dd-native");
    var trigger = document.createElement("button");
    trigger.type = "button";
    trigger.className = "climate-dd-trigger";
    trigger.setAttribute("aria-haspopup", "listbox");
    trigger.setAttribute("aria-expanded", "false");
    trigger.innerHTML = '<span class="climate-dd-value"></span><span class="climate-dd-caret" aria-hidden="true">▾</span>';
    var menu = document.createElement("div");
    menu.className = "climate-dd-menu";
    menu.setAttribute("role", "listbox");
    menu.hidden = true;
    wrap.appendChild(trigger);
    wrap.appendChild(menu);
    wrap._menu = menu;
    menu._ownerDd = wrap;
    selectEl._climateDd = wrap;
    trigger.addEventListener("click", function (event) {
      event.preventDefault();
      event.stopPropagation();
      if (selectEl.disabled) return;
      var willOpen = menu.hidden || !wrap.classList.contains("is-open");
      closeClimateDropdowns();
      if (willOpen) openClimateDropdown(selectEl);
    });
    selectEl.addEventListener("change", function () { syncClimateDropdown(selectEl); });
    syncClimateDropdown(selectEl);
  }
  function enhanceProviderModelDropdowns() {
    ["climate-provider", "climate-model", "climate-provider-panel", "climate-model-panel"].forEach(function (id) {
      var el = document.getElementById(id);
      if (el) enhanceClimateSelect(el);
    });
    if (window.ClimateSelect) {
      window.ClimateSelect.enhance(document.getElementById("climate-context-scope"));
    }
  }
  function renderHistoryList() {
    if (!historyList) return;
    if (!state.chat.sessions.length) {
      historyList.innerHTML = '<div class="climate-chat-history-item"><span>No saved sessions yet</span></div>';
      return;
    }
    historyList.innerHTML = state.chat.sessions.map(function (session) {
      return '<button type="button" class="climate-chat-history-item ' + (session.id === state.chat.activeId ? "is-active" : "") + '" data-chat-id="' + escapeHtml(session.id) + '"><span>' + escapeHtml(session.title || "Untitled") + '</span><small>' + escapeHtml(providerLabel(session.provider) + (session.model ? (" · " + session.model) : "")) + '</small></button>';
    }).join("");
    historyList.querySelectorAll("[data-chat-id]").forEach(function (button) {
      button.addEventListener("click", function () {
        restoreChatSession(button.getAttribute("data-chat-id"));
        closeChatPopovers();
      });
    });
  }
  function serverConversationSession(row) {
    var updated = Date.parse(row.updated_at || row.created_at || "") || Date.now();
    return {
      id: "server:" + row.id,
      title: row.title || defaultSessionTitle(),
      createdAt: Date.parse(row.created_at || "") || updated,
      updatedAt: updated,
      provider: "",
      model: "",
      executionMode: currentExecutionMode(),
      lastRunProvider: "",
      workspace: workspace,
      repositoryId: repoId(),
      branch: (branchSelect && branchSelect.value) || "",
      context: captureContextPrefs(),
      messages: [],
      usage: emptyUsage(),
      agentConversationId: row.id,
      serverBacked: true,
      serverHydrated: false
    };
  }
  function hydrateServerHistory() {
    jsonFetch(endpoint("/conversations?" + conversationQuery("limit=50"))).then(function (data) {
      (data.conversations || []).forEach(function (row) {
        var existing = state.chat.sessions.find(function (session) {
          return session.agentConversationId === row.id;
        });
        if (existing) {
          if (isDefaultSessionTitle(existing.title)) existing.title = row.title || existing.title;
          existing.updatedAt = Date.parse(row.updated_at || "") || existing.updatedAt;
          existing.serverBacked = true;
          return;
        }
        state.chat.sessions.push(serverConversationSession(row));
      });
      state.chat.sessions.sort(function (a, b) { return Number(b.updatedAt || 0) - Number(a.updatedAt || 0); });
      state.chat.sessions = state.chat.sessions.slice(0, 40);
      saveChatStore();
      renderHistoryList();
    }).catch(function () {
      // Local chat remains usable when server history is unavailable.
    });
  }
  function hydrateServerSession(session) {
    if (!session || !session.agentConversationId || session.serverHydrated) return Promise.resolve(session);
    return jsonFetch(endpoint("/conversations/" + encodeURIComponent(session.agentConversationId) + "?" + conversationQuery())).then(function (data) {
      var conversation = data.conversation || {};
      var messages = [];
      (conversation.runs || []).forEach(function (run) {
        var ts = Date.parse(run.created_at || "") || Date.now();
        if (run.prompt) messages.push({ id: uid("msg"), role: "user", text: run.prompt, ts: ts });
        if (run.answer || run.error || run.status) {
          messages.push({
            id: uid("msg"),
            role: run.error ? "error" : "assistant",
            text: run.error || run.answer || "Run " + run.status,
            ts: Date.parse(run.finished_at || "") || ts,
            status: run.status || "completed",
            provider: run.provider || "",
            model: run.model || "",
            runId: run.id || "",
            taskMode: run.mode || "ask",
            executionMode: run.execution_mode || "",
            assistant_label: run.assistant_label || "",
            usage: parseUsagePayload(run.usage || {})
          });
          session.provider = run.provider || session.provider;
          session.model = run.model || session.model;
          session.lastRunProvider = run.provider || session.lastRunProvider;
          if (run.execution_mode) session.executionMode = run.execution_mode;
          applyUsageFromRun(session, run.provider || "unknown", run.usage || {});
        }
      });
      session.title = conversation.title || session.title;
      session.messages = messages;
      session.serverHydrated = true;
      saveChatStore();
      if (session.id === state.chat.activeId) {
        renderChat();
        renderUsageChrome(session);
      }
      return session;
    }).catch(function () { return session; });
  }
  function restoreChatSession(id) {
    var session = state.chat.sessions.find(function (row) { return row.id === id; });
    if (!session) return;
    state.chat.activeId = session.id;
    state.streamingMsgId = "";
    if (session.provider) {
      providerSelect.value = session.provider;
      if (panelProviderSelect) panelProviderSelect.value = session.provider;
      providerSelect.dataset.saved = session.provider;
    }
    if (session.model) {
      modelSelect.dataset.saved = session.model;
      modelSelect.value = session.model;
      panelModelSelect.value = session.model;
    }
    applyContextPrefs(session.context);
    applyExecutionMode(session.executionMode || currentExecutionMode(), { skipSession: true });
    saveChatStore();
    selectProvider(session.provider || providerSelect.value, { refresh: false, preserveModel: session.model });
    renderChat();
    renderUsageChrome(session);
    setStatus("Restored session");
    hydrateTokenEfficiency(session);
    hydrateServerSession(session);
  }
  function renderChat() {
    var session = activeSession();
    if (chatTitleEl) {
      var title = (session && session.title) || defaultSessionTitle();
      chatTitleEl.textContent = title;
      chatTitleEl.title = title;
    }
    renderUsageChrome(session);
    if (!session || !session.messages.length) {
      feed.innerHTML = '<div class="climate-ai-empty"><strong>AiriX · Code Assistant</strong><span>Ask about the active repository, attached files, or a code selection. Nothing is sent until you add it.</span></div>';
      proposalActions.hidden = true;
      return;
    }
    feed.innerHTML = session.messages.map(function (msg) { return renderChatMessage(msg); }).join("");
    enhanceMarkdown(feed);
    feed.querySelectorAll("[data-chat-action]").forEach(function (button) {
      button.addEventListener("click", function () {
        var action = button.getAttribute("data-chat-action");
        var msgId = button.getAttribute("data-msg-id");
        if (action === "undo") proposalAction("reject", msgId);
        else if (action === "keep") proposalAction("accept", msgId);
        else if (action === "review") {
          var msg = session.messages.find(function (row) { return row.id === msgId; });
          if (msg && msg.proposal) renderProposalReview(msg.proposal);
          else if (state.run && state.run.proposal) renderProposalReview(state.run.proposal);
        }
      });
    });
    feed.querySelectorAll("[data-te-action]").forEach(function (button) {
      button.addEventListener("click", function () {
        var action = button.getAttribute("data-te-action");
        var msgId = button.getAttribute("data-msg-id");
        if (action === "evaluate") evaluateTokenSavings(msgId);
        else if (action === "cancel") cancelTokenSavings(msgId);
      });
    });
    feed.querySelectorAll("[data-activity-explore]").forEach(function (button) {
      button.addEventListener("click", function (event) {
        event.preventDefault();
        event.stopPropagation();
        var id = button.getAttribute("data-activity-explore");
        var panel = feed.querySelector('[data-explore-panel="' + id + '"]');
        if (!panel) return;
        var open = panel.hasAttribute("hidden");
        state.activityExploreOpen[id] = open;
        if (open) panel.removeAttribute("hidden");
        else panel.setAttribute("hidden", "");
        feed.querySelectorAll('[data-activity-explore="' + id + '"]').forEach(function (el) {
          el.setAttribute("aria-expanded", open ? "true" : "false");
        });
        var step = feed.querySelector('.climate-activity-step[data-step="explore"]');
        if (step) step.classList.toggle("is-expanded", open);
      });
    });
    Object.keys(state.activityExploreOpen || {}).forEach(function (id) {
      if (!state.activityExploreOpen[id]) return;
      var panel = feed.querySelector('[data-explore-panel="' + id + '"]');
      if (!panel) return;
      panel.removeAttribute("hidden");
      feed.querySelectorAll('[data-activity-explore="' + id + '"]').forEach(function (el) {
        el.setAttribute("aria-expanded", "true");
      });
      var step = feed.querySelector('.climate-activity-step[data-step="explore"]');
      if (step) step.classList.add("is-expanded");
    });
    feed.querySelectorAll("[data-activity-details]").forEach(function (button) {
      button.addEventListener("click", function () {
        var id = button.getAttribute("data-activity-details");
        var details = document.getElementById("climate-details-" + id) || feed.querySelector("#climate-details-" + id);
        if (details) {
          details.open = true;
          details.scrollIntoView({ block: "nearest" });
        }
      });
    });
    feed.querySelectorAll("[data-activity-collapse]").forEach(function (button) {
      button.addEventListener("click", function () {
        var root = button.closest(".climate-activity-progress");
        if (root) root.classList.toggle("is-collapsed");
      });
    });
    feed.scrollTop = feed.scrollHeight;
    var pending = session.messages.slice().reverse().find(function (msg) {
      return msg.role === "assistant" && msg.proposal && msg.proposal.state === "pending";
    });
    proposalActions.hidden = !pending;
  }
  function renderChatMessage(msg) {
    var isUser = msg.role === "user";
    var isError = msg.role === "error" || msg.status === "failed";
    var isRunning = !isUser && (msg.status === "running" || msg.status === "stopping");
    var isStopped = !isUser && (msg.status === "cancelled" || msg.stoppedByUser);
    var isComplete = !isUser && msg.status === "completed";
    var taskMode = msg.taskMode || "ask";
    var hasPendingProposal = !!(msg.proposal && msg.proposal.state === "pending") && !isStopped && msg.status !== "stopping";
    var isEditRun = !isUser && taskMode === "edit" && (hasPendingProposal || (msg.changedFiles || []).length) && !isStopped;
    var name = isUser ? "You" : assistantRoleLabel(msg);
    var logo = isUser ? "" : identityLogoSrc(msg);
    var avatar = isUser ? "Y" : (logo ? "" : providerGlyph(msg.provider || (activeSession() || {}).provider));
    var bodyText = msg.text || "";
    if (looksLikeEditsJson(bodyText) || isRawProviderLine(bodyText) || looksLikeProtocolDump(bodyText)) {
      var cleaned = humanizeAnswer(bodyText, taskMode);
      bodyText = cleaned.text || "";
    }
    if (isRunning && (!bodyText || bodyText === "Working…" || bodyText === "Working...")) bodyText = "";
    var body = "";
    if (bodyText) {
      if (isUser) body = escapeHtml(bodyText).replace(/\n/g, "<br>");
      else body = renderMarkdownHtml(bodyText);
    }
    var files = Array.isArray(msg.changedFiles) ? msg.changedFiles : [];
    var sources = Array.isArray(msg.sources) ? msg.sources : [];
    var tests = testsFromText(msg.text, msg.tests);
    var lines = msg.lines || lineDeltaFromProposal(msg.proposal);
    var showRunCard = isComplete && isEditRun;
    var html = '<article class="climate-assistant-msg ' + (isUser ? "is-user" : "is-assistant") + (isError ? " is-error" : "") + (isRunning ? " is-stream" : "") + '" data-msg-id="' + escapeHtml(msg.id) + '" data-task-mode="' + escapeHtml(taskMode) + '" data-surface="workspace">';
    if (logo) {
      html += '<div class="climate-assistant-avatar" aria-hidden="true"><img src="' + escapeHtml(logo) + '" alt=""></div>';
    } else {
      html += '<div class="climate-assistant-avatar" aria-hidden="true">' + escapeHtml(avatar) + '</div>';
    }
    html += '<div class="climate-assistant-meta"><strong>' + escapeHtml(name) + '</strong><time>' + escapeHtml(formatClock(msg.ts)) + '</time>';
    if (isComplete) html += '<span class="climate-assistant-status-pill is-ok">✓ Completed</span>';
    else if (msg.status === "stopping") html += '<span class="climate-assistant-status-pill is-stop">Stopping…</span>';
    else if (isStopped) html += '<span class="climate-assistant-status-pill is-stop">Stopped</span>';
    html += '</div>';
    html += '<div class="climate-assistant-body">';
    if (msg.stopNotice) html += '<div class="climate-stop-notice">' + escapeHtml(msg.stopNotice) + '</div>';
    if (msg.status === "running") {
      html += renderActivityProgress(msg);
    } else if (msg.status === "stopping" && msg.activity) {
      html += renderActivityProgress(Object.assign({}, msg, { status: "stopping" }));
    } else if ((isComplete || isStopped) && !showRunCard) {
      html += renderActivityComplete(msg);
    } else if (!isUser && msg.elapsedMs && !showRunCard) {
      html += '<button type="button" class="climate-chat-elapsed">End-to-end runtime ' + escapeHtml(formatElapsed(msg.elapsedMs)) + ' ▾</button>';
    }
    if (body) html += '<div class="climate-assistant-text' + (isUser ? "" : " climate-md") + '">' + body + '</div>';
    if ((isComplete || isStopped) && sources.length) {
      html += '<details class="climate-sources"><summary>Candidate Sources · ' + sources.length + ' file' + (sources.length === 1 ? "" : "s") + '</summary>';
      html += '<ul>' + sources.map(function (path) {
        return '<li><button type="button" class="climate-assistant-file" data-open-file="' + escapeHtml(path) + '">' + escapeHtml(path) + '</button></li>';
      }).join("") + '</ul></details>';
    }
    if (showRunCard) {
      html += '<section class="climate-run-summary">';
      html += '<div class="climate-run-summary-head"><span class="climate-run-summary-title">▣ Run Summary</span><span class="climate-run-status is-ok">Completed ✓</span></div>';
      if (msg.proposal && msg.proposal.large_diff) {
        html += '<div class="climate-run-warning" role="status">' + escapeHtml(msg.proposal.warning || "Large or destructive replacement detected. Review the diff before Keep All.") + '</div>';
      }
      html += '<div class="climate-run-stats">';
      html += '<div><small>End-to-end runtime</small><b>' + escapeHtml(formatElapsed(msg.elapsedMs || 0)) + '</b></div>';
      html += '<div><small>Files changed</small><b>' + files.length + '</b></div>';
      html += '<div><small>Tests</small><b class="' + (tests && tests.passed ? "is-ok" : "") + '">' + escapeHtml(tests ? (tests.label || (tests.passed ? "passed" : "—")) : "—") + '</b></div>';
      html += '<div><small>Lines changed</small><b><span class="is-plus">+' + (lines.plus || 0) + '</span> <span class="is-minus">-' + (lines.minus || 0) + '</span></b></div>';
      html += '</div>';
      html += '<div class="climate-run-columns">';
      html += '<div><div class="climate-chat-files-title">Changed files (' + files.length + ')</div>';
      if (files.length) {
        html += files.map(function (path) {
          return '<button type="button" class="climate-assistant-file" data-open-file="' + escapeHtml(path) + '">' + escapeHtml(path) + '</button>';
        }).join("");
      } else html += '<span class="climate-muted">No file edits</span>';
      html += '</div><div><div class="climate-chat-files-title">Tests</div>';
      if (tests) html += '<div class="climate-run-test-row"><span>' + escapeHtml(tests.suite || "tests") + '</span><b class="is-ok">' + escapeHtml(tests.label || "passed") + ' ›</b></div>';
      else html += '<span class="climate-muted">No test summary</span>';
      html += '</div></div>';
      if (hasPendingProposal) {
        html += '<div class="climate-assistant-msg-actions">';
        html += '<button type="button" class="climate-btn" data-chat-action="undo" data-msg-id="' + escapeHtml(msg.id) + '">Undo All</button>';
        html += '<button type="button" class="climate-btn" data-chat-action="keep" data-msg-id="' + escapeHtml(msg.id) + '">Keep All</button>';
        html += '<button type="button" class="climate-btn climate-btn-primary" data-chat-action="review" data-msg-id="' + escapeHtml(msg.id) + '">Review Changes</button>';
        html += '</div>';
      }
      html += '</section>';
    }
    if (isComplete && !isUser) {
      html += renderTokenEfficiency(msg);
    }
    if (!isUser && msg.diagnostics) {
      html += '<details class="climate-assistant-details" id="climate-details-' + escapeHtml(msg.id) + '"><summary>Details / Diagnostics</summary><pre class="mono">' + escapeHtml(msg.diagnostics) + '</pre></details>';
    }
    html += '</div></article>';
    return html;
  }
  function upsertAssistantMessage(patch) {
    var session = activeSession();
    if (!session) session = newChatSession(true);
    var msg = session.messages.find(function (row) { return row.id === (patch.id || state.streamingMsgId); });
    if (!msg) {
      msg = {
        id: patch.id || uid("msg"),
        role: "assistant",
        provider: patch.provider || providerSelect.value,
        model: patch.model || modelSelect.value,
        text: "",
        ts: Date.now(),
        status: "running",
        summary: [],
        changedFiles: [],
        diagnostics: "",
        proposal: null
      };
      session.messages.push(msg);
      state.streamingMsgId = msg.id;
    }
    Object.keys(patch).forEach(function (key) { if (key !== "id") msg[key] = patch[key]; });
    session.updatedAt = Date.now();
    session.provider = providerSelect.value;
    session.model = modelSelect.value;
    session.repositoryId = repoId();
    session.branch = (branchSelect && branchSelect.value) || "";
    session.context = captureContextPrefs();
    saveChatStore();
    renderChat();
    return msg;
  }
  function codingDefaults() {
    return bootstrap.coding_defaults || {};
  }
  function workspaceSurfaceDefaults() {
    var defaults = codingDefaults();
    return defaults.workspace || {
      default_provider: defaults.default_provider || "",
      default_model: ""
    };
  }
  function preferredWorkspaceModel(providerId) {
    var defaults = codingDefaults();
    var surface = workspaceSurfaceDefaults();
    if (surface.default_provider === providerId && surface.default_model) return surface.default_model;
    return (defaults.default_models || {})[providerId] || "";
  }
  function listedModelOrAuto(models, preferred) {
    var list = models || [];
    if (preferred && list.indexOf(preferred) >= 0) return preferred;
    if (!preferred && list.indexOf("__provider_default__") >= 0) return "__provider_default__";
    return "";
  }
  function renderProviders() {
    var providers = bootstrap.providers || []; var defaults = codingDefaults();
    var options = providers.map(function (p) {
      return '<option value="' + escapeHtml(p.id) + '" ' + (p.state !== "connected" ? 'data-unavailable="1"' : "") + '>' + escapeHtml(p.label) + '</option>';
    }).join("");
    providerSelect.innerHTML = options;
    if (panelProviderSelect) panelProviderSelect.innerHTML = options;
    if (providerCards) {
      providerCards.innerHTML = providers.map(function (p) {
        var icon = providerGlyph(p.id);
        return '<button class="climate-provider-card ' + (p.state === "connected" ? "is-connected" : "is-unavailable") + '" data-provider="' + escapeHtml(p.id) + '" title="' + escapeHtml(p.detail || p.status || "") + '"><i></i><strong>' + icon + '</strong><span>' + escapeHtml(p.label) + '</span></button>';
      }).join("");
      providerCards.querySelectorAll("[data-provider]").forEach(function (card) {
        card.addEventListener("click", function () {
          providerSelect.value = card.getAttribute("data-provider");
          if (panelProviderSelect) panelProviderSelect.value = providerSelect.value;
          selectProvider(providerSelect.value, { refresh: false });
        });
      });
    }
    var surface = workspaceSurfaceDefaults();
    var saved = providerSelect.dataset.saved;
    var preferred = saved || surface.default_provider || defaults.default_provider || "";
    if (preferred && providers.some(function (p) { return p.id === preferred; })) providerSelect.value = preferred;
    else if (!saved && !surface.default_provider) {
      var firstConnected = providers.find(function (p) { return p.state === "connected"; });
      if (firstConnected) providerSelect.value = firstConnected.id;
    }
    if (panelProviderSelect) panelProviderSelect.value = providerSelect.value;
    enhanceProviderModelDropdowns();
    selectProvider(providerSelect.value, { refresh: true });
  }
  function applyModelOptions(providerId, models, preferred) {
    var options = '<option value="">Select exact model</option>' + (models || []).map(function (m) {
      var label = m === "__provider_default__" ? "Provider default" : m;
      return '<option value="' + escapeHtml(m) + '">' + escapeHtml(label) + '</option>';
    }).join("");
    modelSelect.innerHTML = options;
    panelModelSelect.innerHTML = options;
    var pick = preferred || modelSelect.dataset.saved || "";
    if (pick && (models || []).indexOf(pick) >= 0) {
      modelSelect.value = pick;
      panelModelSelect.value = pick;
    }
    if (!state.runActive) sendBtn.disabled = !modelSelect.value;
    enhanceProviderModelDropdowns();
  }
  function selectProvider(providerId, opts) {
    opts = opts || {};
    var refresh = !!opts.refresh;
    var p = (bootstrap.providers || []).find(function (row) { return row.id === providerId; });
    if (providerId) providerSelect.value = providerId;
    if (panelProviderSelect) panelProviderSelect.value = providerSelect.value;
    if (providerCards) providerCards.querySelectorAll("[data-provider]").forEach(function (card) {
      card.classList.toggle("is-active", card.getAttribute("data-provider") === providerSelect.value);
    });
    var connected = !!(p && p.state === "connected");
    var statusText = connected ? "Connected" : (p ? (p.status || p.state) : "Unavailable");
    var statusTextEl = providerState.querySelector(".climate-provider-state-text");
    if (statusTextEl) statusTextEl.textContent = statusText;
    else providerState.textContent = statusText;
    providerState.className = "climate-provider-state " + (connected ? "is-ok" : "is-error");
    var titleBits = [statusText];
    if (p && p.executable_path) titleBits.push(p.executable_path);
    if (p && p.runtime_health) titleBits.push("runtime " + p.runtime_health);
    if (p && p.discovery_source) titleBits.push(p.discovery_source);
    providerState.title = titleBits.join(" · ");
    if (providerDot) providerDot.className = "climate-chat-pill-dot " + (connected ? "is-ok" : "is-error");
    if (!state.runActive) sendBtn.disabled = !connected;
    var session = activeSession();
    if (session) {
      session.provider = providerSelect.value;
      saveChatStore();
    }
    if (providerSelect.value === "codex" && connected) {
      fetchCodexRateLimits({ refresh: !!opts.refreshLimits });
    }
    if (!connected) {
      var unavailable = '<option value="">' + escapeHtml((p && p.detail) || "Provider unavailable") + '</option>';
      modelSelect.innerHTML = unavailable;
      panelModelSelect.innerHTML = unavailable;
      enhanceProviderModelDropdowns();
      return;
    }
    var cached = state.modelCache[providerSelect.value];
    var preferred = opts.preserveModel || modelSelect.dataset.saved || preferredWorkspaceModel(providerSelect.value) || "";
    if (cached && !refresh) {
      applyModelOptions(providerSelect.value, cached.models, listedModelOrAuto(cached.models, preferred));
      enhanceProviderModelDropdowns();
      return;
    }
    if (!refresh && !cached) {
      modelSelect.innerHTML = '<option value="">Select model (refresh to load)</option>';
      panelModelSelect.innerHTML = modelSelect.innerHTML;
      sendBtn.disabled = true;
      enhanceProviderModelDropdowns();
      return;
    }
    state.fetchCount += 1;
    jsonFetch(endpoint("/providers/" + encodeURIComponent(providerSelect.value) + "/models" + (refresh ? "?refresh=1" : ""))).then(function (data) {
      state.modelCache[providerSelect.value] = {
        models: data.models || [],
        recommended: data.recommended_model || ""
      };
      applyModelOptions(providerSelect.value, data.models || [], listedModelOrAuto(data.models || [], preferred));
      if (session) { session.model = modelSelect.value; saveChatStore(); }
      savePrefs();
      enhanceProviderModelDropdowns();
    }).catch(function (error) {
      var failed = '<option value="">' + escapeHtml(error.message) + '</option>';
      modelSelect.innerHTML = failed;
      panelModelSelect.innerHTML = failed;
      sendBtn.disabled = true;
      enhanceProviderModelDropdowns();
    });
  }
  function updateProvider(refresh) {
    selectProvider(providerSelect.value, { refresh: !!refresh });
  }
  function appendFeed(text, cls) {
    var session = activeSession() || newChatSession(true);
    if (cls === "is-user") {
      session.messages.push({ id: uid("msg"), role: "user", text: text, ts: Date.now() });
      var userCount = session.messages.filter(function (m) { return m.role === "user"; }).length;
      if (userCount === 1 && isDefaultSessionTitle(session.title)) session.title = titleFromPrompt(text);
    } else if (cls === "is-error") {
      session.messages.push({ id: uid("msg"), role: "error", text: text, ts: Date.now(), status: "failed" });
    } else {
      upsertAssistantMessage({ text: text, status: "completed" });
      saveChatStore();
      renderChat();
      return;
    }
    session.updatedAt = Date.now();
    saveChatStore();
    renderChat();
  }
  function currentSelection() { if(editor){var sel=editor.getSelection();return sel?editor.getModel().getValueInRange(sel):"";}var start=fallback.selectionStart||0,end=fallback.selectionEnd||0;return fallback.value.slice(start,end); }
  function clearPollTimer() {
    if (state.pollTimer) {
      window.clearTimeout(state.pollTimer);
      state.pollTimer = null;
    }
  }
  function setRunControls(mode) {
    var idle = mode === "idle";
    var stopping = mode === "stopping";
    if (sendBtn) {
      sendBtn.hidden = !idle;
      sendBtn.disabled = idle ? !modelSelect.value : true;
    }
    if (stopBtn) {
      stopBtn.hidden = idle;
      stopBtn.disabled = stopping;
      stopBtn.textContent = stopping ? "Stopping…" : "■ Stop";
    }
    if (sendTopBtn) sendTopBtn.hidden = !idle;
    if (stopTopBtn) {
      stopTopBtn.hidden = idle;
      stopTopBtn.disabled = stopping;
      stopTopBtn.title = stopping ? "Stopping…" : "Stop active run";
    }
  }
  function freezeActivity(activity) {
    if (!activity) return null;
    var copy;
    try { copy = JSON.parse(JSON.stringify(activity)); }
    catch (_) { copy = activity; }
    (copy.steps || []).forEach(function (step) { step.state = "done"; });
    copy.planning = false;
    return copy;
  }
  function sendRun() {
    var prompt = promptEl.value.trim();
    var scope = currentWorkspaceScope();
    if (!prompt || !providerSelect.value || !modelSelect.value) return;
    if (scope.scope === "repository" && !(scope.repositoryId || repoId())) return;
    if (state.runActive || state.runId || state.stopRequested) {
      setStatus("A run is already active — Stop it first");
      return;
    }
    captureActive();
    state.streamText = "";
    state.streamingMsgId = "";
    state.stopRequested = false;
    state.runActive = true;
    var session = activeSession() || newChatSession(true);
    var priorProvider = session.lastRunProvider || "";
    var crossProvider = !!(priorProvider && priorProvider !== providerSelect.value);
    var outboundPrompt = crossProvider ? compactHandoffPrompt(prompt, session) : prompt;
    var taskMode = classifyTaskMode(prompt);
    var priorUserCount = (session.messages || []).filter(function (m) { return m.role === "user"; }).length;
    session.messages.push({ id: uid("msg"), role: "user", text: prompt, ts: Date.now() });
    if (priorUserCount === 0 && isDefaultSessionTitle(session.title)) {
      session.title = titleFromPrompt(prompt);
    }
    session.provider = providerSelect.value;
    session.model = modelSelect.value;
    session.executionMode = currentExecutionMode();
    session.repositoryId = repoId();
    session.branch = (branchSelect && branchSelect.value) || "";
    session.context = captureContextPrefs();
    session.updatedAt = Date.now();
    attachMentionsFromPrompt(prompt);
    saveChatStore();
    renderChat();
    promptEl.value = "";
    setRunControls("running");
    proposalActions.hidden = true;
    setStatus(crossProvider ? "AI run started (handoff)" : "AI run started");
    upsertAssistantMessage({
      status: "running",
      text: "",
      provider: providerSelect.value,
      model: modelSelect.value,
      startedAt: Date.now(),
      taskMode: taskMode,
      executionMode: currentExecutionMode(),
      assistant_label: currentExecutionMode() === "direct"
        ? providerLabel(providerSelect.value)
        : "AiriX",
      sources: [],
      stopNotice: "",
      stoppedByUser: false,
      proposal: null
    });
    var targetRepo = scope.scope === "repository" ? (scope.repositoryId || repoId()) : "";
    var runPath = targetRepo
      ? "/repositories/" + encodeURIComponent(targetRepo) + "/runs"
      : "/workspace/runs";
    var attachedFiles = state.attachedContext.map(function (item) {
      return {
        repository_id: item.repositoryId,
        path: item.path,
        start_line: item.startLine || 0,
        end_line: item.endLine || 0
      };
    });
    jsonFetch(endpoint(runPath), {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        provider: providerSelect.value,
        model: modelSelect.value,
        prompt: outboundPrompt,
        display_prompt: prompt,
        task_mode: taskMode,
        context_scope: scope.scope,
        repository_id: scope.repositoryId,
        attached_files: attachedFiles,
        current_file: "",
        selection: "",
        selected_files: attachedFiles.filter(function (item) {
          return targetRepo && item.repository_id === targetRepo;
        }).map(function (item) { return item.path; }),
        include_repo_context: false,
        execution_mode: currentExecutionMode(),
        surface: CLIMATE_SURFACE,
        handoff: crossProvider,
        reuse_session: !crossProvider,
        conversation_id: session.agentConversationId || ""
      })
    }).then(function (data) {
      if (data.run && data.run.conversation_id) {
        session.agentConversationId = data.run.conversation_id;
        saveChatStore();
      }
      if (state.stopRequested) {
        state.runId = data.run.id;
        state.run = data.run;
        requestStop(data.run.id);
        return;
      }
      state.runId = data.run.id;
      state.run = data.run;
      pushOutput("climate", "Run started · " + (data.run.provider || providerSelect.value) + " / " + (data.run.model || modelSelect.value));
      if (data.run && data.run.preflight && data.run.preflight.activity) {
        var prefLog = "[climate_context_resolver]\n" + (data.run.preflight.activity || []).join("\n");
        var preflightDiagnostics = prefLog + ((data.run.logs || "") ? ("\n" + data.run.logs) : "");
        upsertAssistantMessage({
          status: data.run.status === "completed" && data.run.provider_invoked === false ? "completed" : "running",
          diagnostics: preflightDiagnostics,
          activity: parseActivityEvidence(prefLog + "\n" + (data.run.logs || ""), {
            running: data.run.provider_invoked !== false && data.run.status === "running",
            startedAt: Date.now(),
            filesInspected: data.run.files_inspected
          }),
          sources: data.run.sources || [],
          text: data.run.provider_invoked === false ? (data.run.answer || "") : "",
          provider: data.run.provider,
          model: data.run.model,
          taskMode: data.run.task_mode || taskMode
        });
        if (data.run.provider_invoked === false) {
          applyUsageFromRun(activeSession(), data.run.provider || providerSelect.value, data.run.usage || {
            total_tokens: 0, usage_source: "exact"
          });
          if (data.run.answer) pushOutput("climate", data.run.answer);
          finishRun();
          return;
        }
      }
      pollRun();
    }).catch(function (error) {
      upsertAssistantMessage({ status: "failed", text: error.message, role: "error", stopNotice: "" });
      addProblem({ severity:"error", source:"runtime", path: (currentTab()||{}).path || "", line:1, message:error.message });
      pushOutput("system", error.message);
      finishRun();
    });
  }
  function requestStop(runId) {
    var id = runId || state.runId;
    if (!id) return;
    setRunControls("stopping");
    setStatus("Stopping…");
    var session = activeSession();
    var streaming = session && session.messages.find(function (m) { return m.id === state.streamingMsgId; });
    upsertAssistantMessage({
      status: "stopping",
      stopNotice: "Stopping…",
      activity: freezeActivity(streaming && streaming.activity),
      proposal: null
    });
    jsonFetch(endpoint("/runs/" + encodeURIComponent(id) + "/cancel"), { method: "POST" })
      .then(function (data) {
        state.run = data.run || state.run;
        if (data.run && ["completed", "failed", "cancelled", "unavailable"].indexOf(data.run.status) >= 0) {
          finalizeStoppedRun(data.run);
        } else {
          pollRun();
        }
      })
      .catch(function (error) {
        upsertAssistantMessage({
          status: "cancelled",
          stoppedByUser: true,
          stopNotice: "Stopped by user",
          text: (streaming && streaming.text) || "",
          proposal: null
        });
        appendFeed(error.message || "Stop request failed", "is-error");
        finishRun();
      });
  }
  function stopRun() {
    if (!state.runActive && !state.runId) return;
    if (state.stopRequested) return;
    state.stopRequested = true;
    clearPollTimer();
    if (state.runId) requestStop(state.runId);
    else {
      setRunControls("stopping");
      setStatus("Stopping…");
      upsertAssistantMessage({ status: "stopping", stopNotice: "Stopping…", proposal: null });
    }
  }
  function finalizeStoppedRun(run) {
    if (!state.runActive && !state.streamingMsgId && !state.stopRequested) return;
    var session = activeSession();
    var streaming = session && session.messages.find(function (m) { return m.id === state.streamingMsgId; });
    var taskMode = (run && run.task_mode) || (streaming && streaming.taskMode) || "ask";
    var partial = (streaming && streaming.text) || "";
    var parsed = splitRunOutput((run && run.logs) || "", (run && run.answer) || partial, taskMode);
    var text = parsed.text || partial;
    if (looksLikeEditsJson(text)) text = humanizeAnswer(text, taskMode).text || partial;
    var started = Number((streaming && streaming.startedAt) || (run && run.created_at) || Date.now());
    if (String(started).length < 13) started = Date.now() - 1000;
    var elapsedMs = Math.max(0, Date.now() - started);
    var frozen = freezeActivity(streaming && streaming.activity);
    var sources = collectSources(streaming, run, frozen);
    if (session) session.lastRunProvider = (run && run.provider) || providerSelect.value;
    upsertAssistantMessage({
      status: "cancelled",
      stoppedByUser: true,
      stopNotice: "Stopped by user",
      text: text,
      diagnostics: parsed.diagnostics || (streaming && streaming.diagnostics) || "",
      activity: frozen,
      sources: sources,
      filesInspected: (run && run.files_inspected != null) ? Number(run.files_inspected) : providerInvestigationFiles(run).length,
      searchMatchedFiles: run && run.search_matched_files,
      proposal: null,
      changedFiles: [],
      elapsedMs: elapsedMs,
      runId: (run && run.id) || state.runId,
      taskMode: taskMode
    });
    proposalActions.hidden = true;
    finishRun();
  }
  function pollRun() {
    if (!state.runId) return;
    var pollId = state.runId;
    jsonFetch(endpoint("/runs/" + encodeURIComponent(state.runId))).then(function (data) {
      if (pollId !== state.runId) return;
      state.run = data.run;
      var session = activeSession();
      var streaming = session && session.messages.find(function (m) { return m.id === state.streamingMsgId; });
      var taskMode = (data.run && data.run.task_mode) || (streaming && streaming.taskMode) || "ask";
      var terminal = ["completed", "failed", "cancelled", "unavailable"].indexOf(data.run.status) >= 0;

      if (state.stopRequested) {
        if (!terminal) {
          state.pollTimer = window.setTimeout(pollRun, 400);
          return;
        }
        finalizeStoppedRun(data.run);
        return;
      }

      var parsed = splitRunOutput(data.run.logs, "", taskMode);
      var evidence = ((parsed.diagnostics || "") + "\n" + (data.run.logs || "") + "\n" + (parsed.text || "")).trim();
      var activity = parseActivityEvidence(evidence, {
        running: !terminal,
        startedAt: streaming && streaming.startedAt,
        hasAnswer: !!(parsed.text && parsed.text.length > 20),
        hasProposal: !!(data.run.proposal) && taskMode === "edit",
        filesInspected: data.run.files_inspected
      });
      if (data.run.logs && data.run.logs !== state.streamText) {
        state.streamText = data.run.logs;
        upsertAssistantMessage({
          status: "running",
          text: parsed.text || "",
          diagnostics: parsed.diagnostics,
          activity: activity,
          taskMode: taskMode,
          sources: collectSources(streaming, data.run, activity),
          provider: data.run.provider,
          model: data.run.model,
          stopNotice: ""
        });
      } else if (streaming && streaming.status === "running") {
        var prev = JSON.stringify(streaming.activity || {});
        var next = JSON.stringify(activity || {});
        if (prev !== next) {
          upsertAssistantMessage({
            status: "running",
            activity: activity,
            taskMode: taskMode,
            sources: collectSources(streaming, data.run, activity),
            diagnostics: parsed.diagnostics || streaming.diagnostics || ""
          });
        } else if (streaming.activity && streaming.activity.explore) {
          streaming.activity.explore.elapsedMs = activity.explore.elapsedMs;
        }
      }
      if (parsed.text) pushOutput("climate", parsed.text);
      ingestRunProblems(data.run, parsed);
      if (!terminal) {
        state.pollTimer = window.setTimeout(pollRun, 650);
        return;
      }
      var finalParsed = splitRunOutput(data.run.logs, data.run.answer, taskMode);
      var proposal = (data.run.status === "completed" && taskMode === "edit") ? (data.run.proposal || null) : null;
      var summary = extractSummary(finalParsed.text, proposal);
      var files = proposal ? changedFilesFrom(proposal, finalParsed.text) : [];
      var tests = testsFromText(finalParsed.text, null);
      if (tests) {
        state.testsSummary = tests.label ? (tests.suite ? tests.suite + " · " : "") + tests.label : JSON.stringify(tests);
        pushOutput("runs", state.testsSummary);
        renderTestsPane();
      }
      ingestRunProblems(data.run, finalParsed);
      if (finalParsed.text) pushOutput("climate", finalParsed.text);
      if (data.run.error) {
        pushOutput("runs", data.run.error);
        pushOutput("system", data.run.error);
      }
      if (data.run.status) pushOutput("climate", "Run " + data.run.status + (data.run.error ? (": " + data.run.error) : ""));
      var lines = lineDeltaFromProposal(proposal);
      var started = Number((streaming && streaming.startedAt) || data.run.created_at || Date.now());
      var finished = Number(data.run.finished_at || Date.now());
      if (String(started).length < 13) started = Date.now() - 1000;
      if (String(finished).length < 13) finished = Date.now();
      var elapsedMs = Math.max(0, finished - started);
      var usageParsed = parseUsagePayload(data.run.usage);
      var diagnostics = finalParsed.diagnostics || "";
      if (data.run.raw_answer && diagnostics.indexOf(data.run.raw_answer) < 0) {
        diagnostics = (diagnostics ? diagnostics + "\n\n" : "") + "[provider_raw_answer]\n" + data.run.raw_answer;
      }
      var finalActivity = parseActivityEvidence(((diagnostics || "") + "\n" + (data.run.logs || "") + "\n" + (finalParsed.text || "")).trim(), {
        running: false,
        startedAt: started,
        elapsedMs: elapsedMs,
        hasAnswer: !!(finalParsed.text),
        hasProposal: !!proposal,
        tests: tests,
        status: data.run.status,
        filesInspected: data.run.files_inspected
      });
      var sources = collectSources({ sources: data.run.sources || (streaming && streaming.sources) || [] }, data.run, finalActivity);
      if (session) {
        session.lastRunProvider = data.run.provider || providerSelect.value;
        applyUsageFromRun(session, data.run.provider || providerSelect.value, data.run.usage);
      }
      if (data.run.status === "completed" && (data.run.provider || providerSelect.value) === "codex") {
        fetchCodexRateLimits({ refresh: true });
      }
      if (data.run.status === "cancelled") {
        finalizeStoppedRun(data.run);
        return;
      }
      var displayText = data.run.error ? data.run.error : (finalParsed.text || (proposal ? "Proposed changes are ready for review." : "Run finished."));
      if (looksLikeEditsJson(displayText)) {
        displayText = humanizeAnswer(displayText, taskMode).text || (proposal ? "Proposed changes are ready for review." : "Run finished.");
      }
      upsertAssistantMessage({
        status: data.run.status,
        text: displayText,
        diagnostics: diagnostics,
        summary: summary,
        changedFiles: files,
        sources: sources,
        filesInspected: (data.run.files_inspected != null) ? Number(data.run.files_inspected) : providerInvestigationFiles(data.run).length,
        searchMatchedFiles: data.run.search_matched_files,
        tests: tests,
        lines: lines,
        proposal: proposal,
        taskMode: taskMode,
        activity: finalActivity,
        elapsedMs: elapsedMs,
        runId: data.run.id,
        usage: usageParsed,
        tokenEfficiency: data.run.token_efficiency || null,
        executionMode: data.run.execution_mode || currentExecutionMode(),
        assistant_label: data.run.assistant_label || "",
        stopNotice: "",
        stoppedByUser: false
      });
      if (data.run.error && !finalParsed.text) {
        var msg = session && session.messages.find(function (row) { return row.id === state.streamingMsgId; });
        if (msg) msg.role = "error";
        saveChatStore();
        renderChat();
      }
      renderProposal(proposal);
      finishRun();
    }).catch(function (error) {
      if (state.stopRequested) {
        finalizeStoppedRun(state.run || { id: state.runId, status: "cancelled", logs: "", answer: "" });
        return;
      }
      upsertAssistantMessage({ status: "failed", text: error.message });
      finishRun();
    });
  }
  function applyTokenEfficiency(msgId, payload, sessionId) {
    var session = sessionId
      ? state.chat.sessions.find(function (row) { return row.id === sessionId; })
      : activeSession();
    if (!session) return;
    var msg = session.messages.find(function (row) { return row.id === msgId; });
    if (!msg) return;
    msg.tokenEfficiency = payload;
    saveChatStore();
    if (session.id === (activeSession() || {}).id) renderChat();
  }
  function tokenEfficiencyEndpoint(runId, suffix) {
    return endpoint("/runs/" + encodeURIComponent(runId) + "/token-efficiency" + (suffix || ""));
  }
  function pollTokenEfficiency(msgId, runId) {
    if (state.tePoll[runId]) window.clearTimeout(state.tePoll[runId]);
    jsonFetch(tokenEfficiencyEndpoint(runId)).then(function (data) {
      var te = data.token_efficiency;
      applyTokenEfficiency(msgId, te);
      var status = tokenEfficiencyStatus(te);
      if (status === "Measuring…") {
        state.tePoll[runId] = window.setTimeout(function () { pollTokenEfficiency(msgId, runId); }, 1200);
      } else {
        delete state.tePoll[runId];
      }
    }).catch(function (error) {
      applyTokenEfficiency(msgId, { status: "Failed", reason: error.message || "Benchmark failed", climate: {} });
      delete state.tePoll[runId];
    });
  }
  function evaluateTokenSavings(msgId) {
    var session = activeSession();
    var msg = session && session.messages.find(function (row) { return row.id === msgId; });
    if (!msg || !msg.runId) return;
    applyTokenEfficiency(msgId, Object.assign({}, msg.tokenEfficiency || {}, { status: "Measuring…", reason: "" }));
    jsonFetch(tokenEfficiencyEndpoint(msg.runId, "/evaluate"), { method: "POST", body: "{}" }).then(function (data) {
      applyTokenEfficiency(msgId, data.token_efficiency);
      if (tokenEfficiencyStatus(data.token_efficiency) === "Measuring…") pollTokenEfficiency(msgId, msg.runId);
    }).catch(function (error) {
      applyTokenEfficiency(msgId, { status: "Failed", reason: error.message || "Benchmark failed", climate: msg.tokenEfficiency && msg.tokenEfficiency.climate });
    });
  }
  function cancelTokenSavings(msgId) {
    var session = activeSession();
    var msg = session && session.messages.find(function (row) { return row.id === msgId; });
    if (!msg || !msg.runId) return;
    jsonFetch(tokenEfficiencyEndpoint(msg.runId, "/cancel"), { method: "POST", body: "{}" }).then(function (data) {
      applyTokenEfficiency(msgId, data.token_efficiency);
      delete state.tePoll[msg.runId];
    }).catch(function (error) {
      applyTokenEfficiency(msgId, { status: "Cancelled", reason: error.message || "Cancelled", climate: msg.tokenEfficiency && msg.tokenEfficiency.climate });
    });
  }
  function hydrateTokenEfficiency(session) {
    if (!session) return;
    (session.messages || []).forEach(function (msg) {
      if (!msg.runId || (msg.provider && msg.provider !== "codex")) return;
      if (msg.tokenEfficiency && tokenEfficiencyStatus(msg.tokenEfficiency) === "Measured") return;
      jsonFetch(tokenEfficiencyEndpoint(msg.runId)).then(function (data) {
        if (!data.token_efficiency) return;
        applyTokenEfficiency(msg.id, data.token_efficiency, session.id);
        if (tokenEfficiencyStatus(data.token_efficiency) === "Measuring…") pollTokenEfficiency(msg.id, msg.runId);
      }).catch(function () {});
    });
  }
  function finishRun(){
    clearPollTimer();
    state.runActive = false;
    state.stopRequested = false;
    state.runId = "";
    state.streamingMsgId = "";
    setRunControls("idle");
    setStatus("Ready");
  }
  function renderProposal(proposal){
    if(!proposal||proposal.state!=="pending"){proposalActions.hidden=true;return;}
    proposalActions.hidden=false;
    renderChat();
  }
  function renderProposalReview(proposal){
    switchPanel("git", { skipGitRender: true });
    var edits=proposal.edits||[],active=edits[0]||{},sides=diffSides(active.diff||"");
    var warn = proposal.large_diff ? ('<div class="climate-run-warning" role="status">'+escapeHtml(proposal.warning||"Large or destructive replacement detected. Review the diff before Keep All.")+'</div>') : "";
    bottomBody.innerHTML='<div class="climate-git-workspace"><aside class="climate-git-changes"><div class="climate-git-title">Proposed changes <span class="count">'+edits.length+'</span></div>'+edits.map(function(edit,index){return '<button class="climate-git-file '+(index===0?'is-active':'')+'" data-proposal-index="'+index+'"><span>◆</span><span>'+escapeHtml(edit.path)+'</span><b>M</b></button>';}).join('')+'</aside><section class="climate-git-review"><div class="climate-git-review-head"><span>'+escapeHtml(active.path||'Proposed edit')+'</span><select disabled><option>Unified</option></select></div>'+warn+'<div class="climate-diff-split"><div class="climate-diff-column is-before"><h4>Original</h4><pre>'+escapeHtml(sides.before||'No original content')+'</pre></div><div class="climate-diff-column is-after"><h4>Modified</h4><pre>'+escapeHtml(sides.after||'No modified content')+'</pre></div></div><div class="climate-git-actions"><button class="climate-btn" id="climate-reject-bottom">Undo All</button><button class="climate-btn climate-btn-primary" id="climate-accept-bottom">Keep All</button></div></section></div>';
    bottomBody.querySelectorAll("[data-proposal-index]").forEach(function(button){button.addEventListener("click",function(){var edit=edits[parseInt(button.getAttribute("data-proposal-index"),10)];var split=diffSides(edit.diff||"");bottomBody.querySelectorAll(".climate-git-file").forEach(function(row){row.classList.toggle("is-active",row===button);});bottomBody.querySelector(".climate-git-review-head span").textContent=edit.path;bottomBody.querySelector(".is-before pre").textContent=split.before;bottomBody.querySelector(".is-after pre").textContent=split.after;});});
    document.getElementById("climate-reject-bottom").addEventListener("click",function(){proposalAction("reject");});document.getElementById("climate-accept-bottom").addEventListener("click",function(){proposalAction("accept");});savePrefs();
  }
  function proposalAction(action, msgId){
    var runId = state.runId;
    var session = activeSession();
    var msg = session && session.messages.find(function (row) { return row.id === (msgId || state.streamingMsgId); });
    if (msg && msg.runId) runId = msg.runId;
    if(!runId)return;
    var proposal = (msg && msg.proposal) || (state.run && state.run.proposal) || null;
    if (action === "accept" && proposal && proposal.large_diff) {
      var ok = window.confirm(proposal.warning || "Large or destructive replacement detected. Apply these edits anyway?");
      if (!ok) return;
    }
    jsonFetch(endpoint("/runs/"+encodeURIComponent(runId)+"/"+action),{method:"POST"}).then(function(data){
      proposalActions.hidden=true;
      if (msg && msg.proposal) msg.proposal.state = action === "accept" ? "accepted" : "rejected";
      upsertAssistantMessage({
        id: msg && msg.id,
        text: action==="accept" ? ("Kept " + (data.applied||[]).length + " file edit(s).") : "Undid proposed edits.",
        proposal: msg && msg.proposal,
        status: "completed"
      });
      if(action==="accept"){var tab=currentTab();if(tab){tab.loaded=false;tab.dirty=false;openFile(tab.path);}loadTree();loadGit();}
    }).catch(function(error){appendFeed(error.message,"is-error");});
  }
  function problemId(item) {
    return [item.source || "", item.path || "", item.line || "", item.column || "", item.message || ""].join("|");
  }
  function addProblem(item) {
    if (!item || !item.message) return;
    var row = {
      id: item.id || problemId(item),
      severity: item.severity || "error",
      source: item.source || "runtime",
      path: item.path || "",
      line: Number(item.line) || 0,
      column: Number(item.column) || 1,
      message: String(item.message)
    };
    if (state.problems.some(function (p) { return p.id === row.id; })) return;
    state.problems.push(row);
    refreshProblems();
  }
  function clearProblemsBySource(source) {
    state.problems = state.problems.filter(function (p) { return p.source !== source; });
    refreshProblems();
  }
  function refreshJsonParseProblem(model, path) {
    if (!window.monaco || !model) return;
    var lang = model.getLanguageId ? model.getLanguageId() : "";
    var owner = "climate-json";
    if (lang !== "json") {
      window.monaco.editor.setModelMarkers(model, owner, []);
      return;
    }
    try {
      JSON.parse(model.getValue() || "");
      window.monaco.editor.setModelMarkers(model, owner, []);
    } catch (err) {
      var line = 1, column = 1;
      var m = String(err.message || "").match(/position\s+(\d+)/i);
      if (m) {
        var pos = model.getPositionAt(Math.max(0, Number(m[1]) - 1));
        line = pos.lineNumber;
        column = pos.column;
      } else {
        var lm = String(err.message || "").match(/line\s+(\d+)/i);
        if (lm) line = Number(lm[1]) || 1;
      }
      window.monaco.editor.setModelMarkers(model, owner, [{
        severity: window.monaco.MarkerSeverity.Error,
        message: String(err.message || "Invalid JSON"),
        startLineNumber: line,
        startColumn: column,
        endLineNumber: line,
        endColumn: column + 1
      }]);
    }
  }
  function collectEditorProblems() {
    var rows = [];
    if (window.monaco && window.monaco.editor.getModelMarkers) {
      window.monaco.editor.getModelMarkers({}).forEach(function (marker) {
        if (!marker || marker.severity < window.monaco.MarkerSeverity.Warning) return;
        var severity = "info";
        if (marker.severity >= window.monaco.MarkerSeverity.Error) severity = "error";
        else if (marker.severity >= window.monaco.MarkerSeverity.Warning) severity = "warning";
        var res = marker.resource;
        var path = "";
        if (res) {
          var raw = String((res.path || res.fsPath || (res.toString && res.toString()) || "")).replace(/\\/g, "/");
          raw = raw.replace(/^climate:\/\//, "").replace(/^\/+/, "");
          var parts = raw.split("/").filter(Boolean);
          if (parts[0] === workspace) parts.shift();
          if (parts[0] === repoId()) parts.shift();
          path = parts.join("/");
        }
        rows.push({
          id: "monaco|" + path + "|" + marker.startLineNumber + "|" + marker.message,
          severity: severity,
          source: "editor",
          path: path,
          line: marker.startLineNumber || 1,
          column: marker.startColumn || 1,
          message: marker.message || "Editor diagnostic"
        });
      });
    } else {
      state.tabs.forEach(function (tab) {
        if (!tab.loaded || languageId(tab.language, tab.path) !== "json") return;
        try { JSON.parse(tab.content || ""); }
        catch (err) {
          rows.push({
            id: "json|" + tab.path + "|" + err.message,
            severity: "error",
            source: "editor",
            path: tab.path,
            line: 1,
            column: 1,
            message: String(err.message || "Invalid JSON")
          });
        }
      });
    }
    return rows;
  }
  function ingestRunProblems(run, parsed) {
    clearProblemsBySource("runtime");
    clearProblemsBySource("test");
    var blob = [run && run.error, parsed && parsed.text, parsed && parsed.diagnostics].filter(Boolean).join("\n");
    var filtered = filterOutputLines(blob);
    parseDiagnosticLines(filtered, run && run.error ? "runtime" : "test").forEach(function (row) {
      addProblem(row);
    });
    if (run && run.error && !parseDiagnosticLines(run.error, "runtime").length) {
      addProblem({
        severity: "error",
        source: "runtime",
        path: (currentTab() || {}).path || "",
        line: 1,
        message: String(run.error)
      });
    }
  }
  function allProblems() {
    var seen = {};
    var rows = collectEditorProblems().concat(state.problems);
    return rows.filter(function (row) {
      var id = row.id || problemId(row);
      if (seen[id]) return false;
      seen[id] = true;
      return true;
    });
  }
  function refreshProblems() {
    var rows = allProblems();
    var errors = rows.filter(function (r) { return r.severity === "error"; }).length;
    var warnings = rows.filter(function (r) { return r.severity === "warning"; }).length;
    var countEl = document.getElementById("climate-problems-count");
    if (countEl) {
      countEl.textContent = String(rows.length);
      countEl.classList.toggle("is-error", errors > 0);
    }
    var hint = document.getElementById("climate-problems-hint");
    if (hint) hint.textContent = errors + " error" + (errors === 1 ? "" : "s") + ", " + warnings + " warning" + (warnings === 1 ? "" : "s");
    if (state.panel !== "problems") return;
    var list = document.getElementById("climate-problems-list");
    if (!list) return;
    if (!rows.length) {
      list.innerHTML = '<div class="climate-empty-pane">No problems detected</div>';
      return;
    }
    list.innerHTML = rows.map(function (row) {
      var loc = (row.path || "") + (row.line ? (":" + row.line) : "");
      return '<button type="button" class="climate-problem-row is-'+escapeHtml(row.severity)+'" data-path="'+escapeHtml(row.path||"")+'" data-line="'+escapeHtml(row.line||"")+'" data-column="'+escapeHtml(row.column||1)+'"><span class="climate-problem-sev">'+(row.severity==="error"?"●":row.severity==="warning"?"▲":"ℹ")+'</span><span class="climate-problem-main"><strong>'+escapeHtml(row.message)+'</strong><span class="climate-problem-meta">'+escapeHtml((row.source||"")+(loc?" · "+loc:""))+'</span></span></button>';
    }).join("");
    list.querySelectorAll("[data-path]").forEach(function (btn) {
      btn.addEventListener("click", function () {
        var path = btn.getAttribute("data-path");
        if (!path) return;
        openFile(path, Number(btn.getAttribute("data-line") || 0), Number(btn.getAttribute("data-column") || 1));
      });
    });
  }
  function pushOutput(channel, text, ts) {
    var cleaned = filterOutputLines(text);
    if (!cleaned) return;
    if (!state.output[channel]) channel = "climate";
    var stamp = ts || new Date().toISOString();
    var clock;
    try { clock = new Date(stamp).toLocaleTimeString([], { hour: "numeric", minute: "2-digit", second: "2-digit" }); }
    catch (_) { clock = ""; }
    var last = state.output[channel][state.output[channel].length - 1];
    if (last && last.text === cleaned) return;
    state.output[channel].push({ ts: stamp, clock: clock, text: cleaned });
    if (state.output[channel].length > 400) state.output[channel] = state.output[channel].slice(-400);
    if (state.panel === "output" && state.outputChannel === channel) renderOutputPane();
  }
  function renderOutputPane() {
    var log = document.getElementById("climate-output-log");
    var select = document.getElementById("climate-output-channel");
    if (select && select.value !== state.outputChannel) select.value = state.outputChannel;
    if (!log) return;
    var rows = state.output[state.outputChannel] || [];
    if (!rows.length) {
      log.textContent = "No output in this channel yet.";
      return;
    }
    log.textContent = rows.map(function (row) {
      return (row.clock ? ("[" + row.clock + "] ") : "") + row.text;
    }).join("\n");
    log.scrollTop = log.scrollHeight;
  }
  function renderTestsPane() {
    var el = document.getElementById("climate-tests-body");
    if (!el) return;
    if (state.testsSummary) {
      el.textContent = state.testsSummary;
      return;
    }
    el.innerHTML = '<div class="climate-empty-pane">No test run selected. CLIMATE does not expose unrestricted shell execution. Test summaries appear here when a run reports pass/fail counts.</div>';
  }
  function renderDebugPane() {
    var el = document.getElementById("climate-debug-body");
    if (!el) return;
    var data = state.debugPayload;
    if (!data) {
      el.innerHTML = '<div class="climate-empty-pane">Loading debug session…</div>';
      return;
    }
    if (!data.active || !data.session) {
      el.innerHTML = '<div class="climate-empty-pane">No active debug session</div>';
      return;
    }
    var session = data.session;
    var logs = (data.logs || []).map(function (row) {
      var text = typeof row === "string" ? row : (row.text || "");
      var stream = (row && row.stream) || "";
      return (stream ? ("[" + stream + "] ") : "") + text;
    }).join("\n");
    el.innerHTML = '<div class="climate-debug-session"><b>'+escapeHtml(session.profile_id || session.run_id || "Run")+'</b> · '+escapeHtml(session.status || "")+(session.pid ? (" · pid "+escapeHtml(session.pid)) : "")+(session.port ? (" · :"+escapeHtml(session.port)) : "")+(session.error ? (" · "+escapeHtml(session.error)) : "")+'</div><pre class="climate-bottom-log mono">'+(logs ? escapeHtml(logs) : "No stdout/stderr yet.")+'</pre>';
  }
  function renderPortsPane() {
    var el = document.getElementById("climate-ports-list");
    var countEl = document.getElementById("climate-ports-count");
    var data = state.portsPayload;
    if (countEl) {
      var n = data && Array.isArray(data.ports) ? data.ports.length : 0;
      countEl.textContent = String(n);
      countEl.hidden = n === 0;
    }
    if (state.panel !== "ports" || !el) return;
    if (!data) {
      el.innerHTML = '<div class="climate-empty-pane">Loading ports…</div>';
      return;
    }
    if (data.error) {
      el.innerHTML = '<div class="climate-bottom-error">'+escapeHtml(data.error)+'</div>';
      return;
    }
    var rows = data.ports || [];
    if (!rows.length) {
      el.innerHTML = '<div class="climate-empty-pane">No listening ports discovered for this repository.</div>';
      return;
    }
    el.innerHTML = '<table class="climate-ports-table"><thead><tr><th>Port</th><th>Process / PID</th><th>Source</th><th>URL</th><th></th></tr></thead><tbody>'+rows.map(function(row){
      var url = row.open_url || (row.port ? ("http://127.0.0.1:"+row.port) : "");
      var proc = (row.process || "") + (row.pid ? (" · "+row.pid) : "");
      var src = [row.source, row.session, row.terminal_name].filter(Boolean).join(" · ");
      return '<tr><td>'+escapeHtml(row.port)+'</td><td>'+escapeHtml(proc)+'</td><td>'+escapeHtml(src)+'</td><td>'+(url?'<a href="'+escapeHtml(url)+'" target="_blank" rel="noopener">'+escapeHtml(url)+'</a>':'')+'</td><td>'+(url?'<button type="button" class="climate-btn" data-copy-url="'+escapeHtml(url)+'">Copy</button>':'')+'</td></tr>';
    }).join("")+'</tbody></table>';
    el.querySelectorAll("[data-copy-url]").forEach(function (btn) {
      btn.addEventListener("click", function () {
        var url = btn.getAttribute("data-copy-url");
        if (navigator.clipboard && navigator.clipboard.writeText) navigator.clipboard.writeText(url);
        setStatus("Copied " + url);
      });
    });
  }
  function loadDebugPane() {
    if (!repoId()) {
      state.debugPayload = { active: false, message: "No active debug session" };
      renderDebugPane();
      return;
    }
    jsonFetch(endpoint("/repositories/"+encodeURIComponent(repoId())+"/debug")).then(function (data) {
      state.debugPayload = data;
      renderDebugPane();
    }).catch(function (error) {
      state.debugPayload = { active: false, message: "No active debug session", error: error.message };
      var el = document.getElementById("climate-debug-body");
      if (el && state.panel === "debug") el.innerHTML = '<div class="climate-bottom-error">'+escapeHtml(error.message)+'</div><div class="climate-empty-pane">No active debug session</div>';
    });
  }
  function loadPortsPane() {
    if (!repoId()) {
      state.portsPayload = { ports: [], count: 0 };
      renderPortsPane();
      return;
    }
    jsonFetch(endpoint("/repositories/"+encodeURIComponent(repoId())+"/ports")).then(function (data) {
      state.portsPayload = data;
      renderPortsPane();
    }).catch(function (error) {
      state.portsPayload = { ports: [], error: error.message };
      renderPortsPane();
    });
  }
  function startBottomPolling() {
    if (state.bottomPollTimer) window.clearInterval(state.bottomPollTimer);
    state.bottomPollTimer = window.setInterval(function () {
      if (center.classList.contains("is-bottom-closed")) return;
      if (state.panel === "debug") loadDebugPane();
      if (state.panel === "ports") loadPortsPane();
      if (state.panel === "problems") refreshProblems();
    }, state.panel === "debug" ? 3000 : 8000);
  }
  function showBottomPane(panel) {
    document.querySelectorAll(".climate-bottom-pane, #climate-terminal-panel").forEach(function (pane) {
      var name = pane.getAttribute("data-pane") || (pane.id === "climate-terminal-panel" ? "terminal" : "");
      pane.hidden = name !== panel;
    });
  }
  function switchPanel(panel, opts) {
    opts = opts || {};
    state.panel = panel;
    if (!opts.keepClosed) center.classList.remove("is-bottom-closed");
    document.querySelectorAll(".climate-bottom-tabs [data-panel]").forEach(function (btn) {
      btn.classList.toggle("is-active", btn.dataset.panel === panel);
    });
    showBottomPane(panel);
    var showTerm = panel === "terminal";
    if (window.WCTerminal) {
      window.WCTerminal.setRenderingPaused(!showTerm);
      if (showTerm) {
        ensureClimateTerminal().then(function () {
          if (window.WCTerminal.scheduleFit) window.WCTerminal.scheduleFit();
        });
      }
    }
    if (panel === "problems") refreshProblems();
    else if (panel === "output") renderOutputPane();
    else if (panel === "debug") loadDebugPane();
    else if (panel === "ports") loadPortsPane();
    else if (panel === "tests") renderTestsPane();
    else if (panel === "git" && !opts.skipGitRender) {
      if (state.git) renderGitWorkspace(state.git, state.gitPath);
      else loadGit();
    }
    startBottomPolling();
    savePrefs();
  }
  var climateTermReady = null;
  function syncWcTerminalRepo() {
    var repoSel = document.getElementById("wc-term-repo");
    if (!repoSel || !repoId()) return;
    if (repoSel.value !== repoId()) {
      repoSel.value = repoId();
      if (repoSel.onchange) repoSel.onchange();
    }
  }
  function ensureClimateTerminal() {
    if (!window.WCTerminal) return Promise.resolve(false);
    if (climateTermReady) {
      syncWcTerminalRepo();
      return climateTermReady;
    }
    climateTermReady = fetch("/api/workspace-console/terminal", { credentials: "same-origin" })
      .then(function (r) { return r.json(); })
      .then(function (data) {
        if (window.WCTerminal.fillCatalog) window.WCTerminal.fillCatalog(data);
        syncWcTerminalRepo();
        return window.WCTerminal.init({});
      })
      .then(function () {
        syncWcTerminalRepo();
        return true;
      })
      .catch(function () {
        climateTermReady = null;
        return false;
      });
    return climateTermReady;
  }
  function setupResize() {
    document.querySelectorAll("[data-resize]").forEach(function (sash) {
      sash.addEventListener("pointerdown", function (ev) {
        var kind = sash.dataset.resize;
        var startX = ev.clientX;
        var startY = ev.clientY;
        var css = getComputedStyle(workbench);
        var left = parseInt(css.getPropertyValue("--left"), 10);
        var right = parseInt(css.getPropertyValue("--right"), 10);
        var bottom = parseInt(css.getPropertyValue("--bottom"), 10);
        var collapsed = workbench.classList.contains("is-ai-collapsed");
        sash.setPointerCapture(ev.pointerId);
        sash.classList.add("is-dragging");
        function move(e) {
          if (kind === "left") {
            workbench.style.setProperty("--left", Math.max(150, Math.min(420, left + e.clientX - startX)) + "px");
          }
          if (kind === "right") {
            var next = right - (e.clientX - startX);
            if (collapsed) {
              if (next >= AI_MIN) {
                collapsed = false;
                setAiExpandedWidth(next);
              }
            } else if (next < AI_MIN) {
              collapseAiPanel(Math.max(AI_MIN, right));
              collapsed = true;
              right = AI_RAIL;
              startX = e.clientX;
            } else {
              workbench.classList.remove("is-ai-maximized");
              delete workbench.dataset.prevRight;
              setAiExpandedWidth(next);
            }
          }
          if (kind === "bottom") {
            workbench.style.setProperty("--bottom", Math.max(90, Math.min(420, bottom - e.clientY + startY)) + "px");
          }
          layoutEditor();
        }
        function up() {
          sash.classList.remove("is-dragging");
          window.removeEventListener("pointermove", move);
          window.removeEventListener("pointerup", up);
          savePrefs();
        }
        window.addEventListener("pointermove", move);
        window.addEventListener("pointerup", up);
      });
    });
  }
  function initMonaco() {
    var mount = monacoHost || document.getElementById("climate-editor-host");
    if(typeof window.require!=="function" || !mount){fallback.style.display=state.active?"block":"none";fallback.readOnly=true;return;}
    window.require.config({paths:{vs:"https://cdn.jsdelivr.net/npm/monaco-editor@0.52.2/min/vs"}});
    window.require(["vs/editor/editor.main"],function(){
      monacoReady=true;
      editor=window.monaco.editor.create(mount,{
        theme:"vs-dark",
        automaticLayout:true,
        fontSize:12,
        fontFamily:"JetBrains Mono, Cascadia Code, Consolas, monospace",
        minimap:{enabled:true},
        scrollBeyondLastLine:false,
        wordWrap:"off",
        readOnly:true,
        domReadOnly:true,
        scrollbar:{ vertical:"visible", horizontal:"auto", verticalScrollbarSize:12, horizontalScrollbarSize:12, alwaysConsumeMouseWheel:true },
        renderWhitespace:"selection",
        padding:{top:8},
        tabSize:4,
        contextmenu:true,
        quickSuggestions:false,
        suggestOnTriggerCharacters:false,
        find:{seedSearchStringFromSelection:"selection"},
        model:null
      });
      try { var blank = editor.getModel(); if (blank) blank.dispose(); editor.setModel(null); } catch (_) {}
      editor.onDidChangeCursorPosition(function(e){document.getElementById("climate-line").textContent=e.position.lineNumber;document.getElementById("climate-column").textContent=e.position.column;});
      editor.onDidChangeCursorSelection(function(e){var count=Math.abs(e.selection.endLineNumber-e.selection.startLineNumber)+1;document.getElementById("climate-selection-meta").textContent=e.selection.isEmpty()?"No selection":count+" line"+(count===1?"":"s");renderAssistantContextBar();});
      try{window.monaco.languages.json.jsonDefaults.setDiagnosticsOptions({validate:true,allowComments:true});}catch(_){ }
      if(window.monaco.editor.onDidChangeMarkers)window.monaco.editor.onDidChangeMarkers(function(){refreshProblems();});
      fallback.style.display="none";
      fallback.readOnly=true;
      if(state.active)activateTab(state.active);
      refreshProblems();
    });
  }
  repoSelect.addEventListener("change",function(){captureActive();savePrefs();window.location.assign((workspace==="work"?"/work/climate":"/personal/climate")+"?repo="+encodeURIComponent(repoId()));});
  providerSelect.addEventListener("change",function(){if(panelProviderSelect)panelProviderSelect.value=providerSelect.value;selectProvider(providerSelect.value,{refresh:false});});
  if(panelProviderSelect){panelProviderSelect.addEventListener("change",function(){providerSelect.value=panelProviderSelect.value;selectProvider(providerSelect.value,{refresh:false});});}
  modelSelect.addEventListener("change",function(){panelModelSelect.value=modelSelect.value;if(!state.runActive)sendBtn.disabled=!modelSelect.value;var session=activeSession();if(session){session.model=modelSelect.value;saveChatStore();}savePrefs();});
  panelModelSelect.addEventListener("change",function(){modelSelect.value=panelModelSelect.value;if(!state.runActive)sendBtn.disabled=!modelSelect.value;var session=activeSession();if(session){session.model=modelSelect.value;saveChatStore();}savePrefs();});
  if(executionModeSelect){
    executionModeSelect.addEventListener("change",function(){
      applyExecutionMode(executionModeSelect.value);
    });
  }
  document.querySelectorAll("#climate-mode-pill [data-execution-mode]").forEach(function(btn){
    btn.addEventListener("click",function(){
      applyExecutionMode(btn.getAttribute("data-execution-mode"));
    });
  });
  syncExecutionModeSwitch(currentExecutionMode());
  document.getElementById("climate-model-refresh").addEventListener("click",function(){selectProvider(providerSelect.value,{refresh:true});});
  document.getElementById("climate-chat-new").addEventListener("click",function(){closeChatPopovers();newChatSession(false);promptEl.focus();});
  function renameActiveChat(){
    var session=activeSession();
    if(!session)return;
    var next=window.prompt("Rename session", session.title||defaultSessionTitle());
    if(next&&next.trim()){
      session.title=next.trim();session.updatedAt=Date.now();saveChatStore();renderChat();
      if(session.agentConversationId){
        jsonFetch(endpoint("/conversations/"+encodeURIComponent(session.agentConversationId)+"?"+conversationQuery()),{
          method:"PUT",headers:{"Content-Type":"application/json"},body:JSON.stringify({title:session.title,surface:CLIMATE_SURFACE})
        }).catch(function(error){pushOutput("system","Session rename was saved locally only: "+error.message);});
      }
    }
  }
  var renameBtn=document.getElementById("climate-chat-rename");
  if(renameBtn)renameBtn.addEventListener("click",renameActiveChat);
  if(tokenPill){
    tokenPill.addEventListener("click",function(event){
      event.stopPropagation();
      historyPanel.hidden=true;menuPanel.hidden=true;contextPanel.hidden=true;
      var open=usagePanel.hidden;
      usagePanel.hidden=!open;
      tokenPill.setAttribute("aria-expanded", open?"true":"false");
      if(open){
        renderUsageChrome(activeSession());
        fetchCodexRateLimits({ refresh: false });
      }
    });
  }
  var usageRefreshBtn=document.getElementById("climate-usage-refresh");
  if(usageRefreshBtn){
    usageRefreshBtn.addEventListener("click",function(event){
      event.stopPropagation();
      usageRefreshBtn.disabled=true;
      fetchCodexRateLimits({ refresh: true }).finally(function(){
        usageRefreshBtn.disabled=false;
      });
    });
  }
  document.getElementById("climate-chat-history").addEventListener("click",function(){
    menuPanel.hidden=true;contextPanel.hidden=true;if(usagePanel)usagePanel.hidden=true;
    historyPanel.hidden=!historyPanel.hidden;
    if(!historyPanel.hidden)renderHistoryList();
  });
  document.getElementById("climate-chat-menu").addEventListener("click",function(){
    historyPanel.hidden=true;contextPanel.hidden=true;if(usagePanel)usagePanel.hidden=true;
    menuPanel.hidden=!menuPanel.hidden;
  });
  menuPanel.querySelectorAll("[data-chat-menu]").forEach(function(button){
    button.addEventListener("click",function(){
      var action=button.getAttribute("data-chat-menu");
      var session=activeSession();
      if(action==="new"){newChatSession(false);promptEl.focus();}
      else if(action==="history"){historyPanel.hidden=false;renderHistoryList();}
      else if(action==="rename") renameActiveChat();
      else if(action==="context"){contextPanel.hidden=false;}
      else if(action==="clear"&&session){session.messages=[];session.title=defaultSessionTitle();session.updatedAt=Date.now();saveChatStore();renderChat();renderUsageChrome(session);}
      menuPanel.hidden=true;
    });
  });
  document.getElementById("climate-ctx-mention").addEventListener("click",function(){
    historyPanel.hidden=true;menuPanel.hidden=true;contextPanel.hidden=true;
    insertMentionTrigger();
  });
  document.getElementById("climate-ctx-files").addEventListener("click",function(){addCurrentFileToChat();});
  document.getElementById("climate-ctx-attach").addEventListener("click",function(){historyPanel.hidden=true;menuPanel.hidden=true;contextPanel.hidden=!contextPanel.hidden;renderContextSummary();});
  var attachedAdd=document.getElementById("climate-attached-add");
  if(attachedAdd) attachedAdd.addEventListener("click",function(){contextPanel.hidden=!contextPanel.hidden;renderContextSummary();});
  var attachedClear=document.getElementById("climate-attached-clear");
  if(attachedClear) attachedClear.addEventListener("click",function(){clearAttached();});
  var addCurrentBtn=document.getElementById("climate-context-add-current");
  if(addCurrentBtn) addCurrentBtn.addEventListener("click",addCurrentFileToChat);
  var addSelBtn=document.getElementById("climate-context-add-selection");
  if(addSelBtn) addSelBtn.addEventListener("click",addCurrentSelectionToChat);
  var scopeSelect=document.getElementById("climate-context-scope");
  if(scopeSelect){
    scopeSelect.addEventListener("change",function(){
      pruneAttachedForScope();
      renderContextSummary();
      savePrefs();
    });
  }
  var treeMenu=document.getElementById("climate-tree-menu");
  if(treeMenu){
    treeMenu.querySelectorAll("[data-tree-menu]").forEach(function(btn){
      btn.addEventListener("click",function(){
        var path=treeMenu.dataset.path||"";
        var action=btn.getAttribute("data-tree-menu");
        treeMenu.hidden=true;
        if(!path) return;
        if(action==="open") openFile(path);
        else if(action==="add") addAttached({ repositoryId: repoId(), path: path, kind: "file" });
      });
    });
  }
  document.getElementById("climate-review").addEventListener("click",function(){
    var session=activeSession();
    var pending=session&&session.messages.slice().reverse().find(function(msg){return msg.proposal&&msg.proposal.state==="pending";});
    if(pending&&pending.proposal)renderProposalReview(pending.proposal);
    else if(state.run&&state.run.proposal)renderProposalReview(state.run.proposal);
  });
  document.addEventListener("click",function(event){
    var ai=document.getElementById("climate-ai");
    if(!event.target.closest(".climate-dd") && !event.target.closest(".climate-dd-menu")) closeClimateDropdowns();
    if(!ai||ai.contains(event.target)||event.target.closest(".climate-dd-menu")||event.target.closest(".climate-tree-menu"))return;
    closeChatPopovers();
    var treeMenu=document.getElementById("climate-tree-menu");
    if(treeMenu) treeMenu.hidden=true;
  });
  document.addEventListener("keydown",function(event){
    if(event.key==="Escape") closeClimateDropdowns();
  });
  window.addEventListener("resize", function () { closeClimateDropdowns(); });
  document.addEventListener("scroll", function (event) {
    if (!document.querySelector(".climate-dd.is-open")) return;
    // Keep open on menu self-scroll; close on outer scroll that would desync fixed coords.
    if (event.target && event.target.closest && event.target.closest(".climate-dd-menu")) return;
    closeClimateDropdowns();
  }, true);
  document.getElementById("climate-refresh-tree").addEventListener("click",function(){loadTree();loadGit();});document.getElementById("climate-search-run").addEventListener("click",runSearch);document.getElementById("climate-search").addEventListener("keydown",function(e){if(e.key==="Enter")runSearch();});
  document.getElementById("climate-show-excluded").addEventListener("change",function(){state.showExcluded=this.checked;savePrefs();loadTree();});
  document.getElementById("climate-send").addEventListener("click",sendRun);
  promptEl.addEventListener("keydown",function(e){
    if((e.ctrlKey||e.metaKey)&&e.key==="Enter"){
      if(state.runActive||state.runId) return;
      sendRun();
    }
  });
  promptEl.addEventListener("input", function () { renderMentionMenu(); });
  promptEl.addEventListener("click", function () { renderMentionMenu(); });
  if(stopBtn) stopBtn.addEventListener("click", stopRun);
  if(sendTopBtn) sendTopBtn.addEventListener("click", sendRun);
  if(stopTopBtn) stopTopBtn.addEventListener("click", stopRun);
  setRunControls("idle");
  document.getElementById("climate-accept").addEventListener("click",function(){proposalAction("accept");});document.getElementById("climate-reject").addEventListener("click",function(){proposalAction("reject");});
  document.getElementById("climate-confirm-save").addEventListener("click",function(e){e.preventDefault();document.getElementById("climate-save-dialog").close();});
  document.getElementById("climate-save").addEventListener("click",saveFile);
  if (fileModes) {
    fileModes.querySelectorAll("[data-file-mode]").forEach(function (btn) {
      btn.addEventListener("click", function () {
        setFileViewMode(btn.getAttribute("data-file-mode"));
      });
    });
  }
  fallback.addEventListener("select",function(){var text=currentSelection();document.getElementById("climate-selection-meta").textContent=text?text.split("\n").length+" line(s)":"No selection";});
  document.addEventListener("keydown",function(e){var key=e.key.toLowerCase();if((e.ctrlKey||e.metaKey)&&key==="s"){e.preventDefault();saveFile();}else if((e.ctrlKey||e.metaKey)&&key==="b"){e.preventDefault();document.getElementById("climate-toggle-left").click();}else if((e.ctrlKey||e.metaKey)&&key==="j"){e.preventDefault();document.getElementById("climate-toggle-bottom").click();}else if((e.ctrlKey||e.metaKey)&&key==="p"){e.preventDefault();workbench.classList.remove("is-left-closed");document.getElementById("climate-search").focus();}else if((e.ctrlKey||e.metaKey)&&e.shiftKey&&key==="a"){e.preventDefault();workbench.classList.remove("is-ai-closed");promptEl.focus();}});
  fallback.readOnly = true;
  shell.addEventListener("click", function (event) {
    var btn = event.target.closest("[data-open-file]");
    if (!btn || !shell.contains(btn)) return;
    event.preventDefault();
    openFile(
      btn.getAttribute("data-open-file"),
      btn.getAttribute("data-open-line"),
      1,
      btn.getAttribute("data-open-symbol")
    );
  });
  document.querySelectorAll(".climate-bottom-tabs [data-panel]").forEach(function(btn){btn.addEventListener("click",function(){switchPanel(btn.dataset.panel);});});document.getElementById("climate-bottom-close").addEventListener("click",function(){center.classList.add("is-bottom-closed");scheduleEditorLayout();savePrefs();});
  var outputChannel=document.getElementById("climate-output-channel");
  if(outputChannel) outputChannel.addEventListener("change",function(){state.outputChannel=outputChannel.value;savePrefs();renderOutputPane();});
  var outputClear=document.getElementById("climate-output-clear");
  if(outputClear) outputClear.addEventListener("click",function(){state.output[state.outputChannel]=[];renderOutputPane();});
  var problemsRefresh=document.getElementById("climate-problems-refresh");
  if(problemsRefresh) problemsRefresh.addEventListener("click",function(){refreshProblems();});
  var debugRefresh=document.getElementById("climate-debug-refresh");
  if(debugRefresh) debugRefresh.addEventListener("click",function(){loadDebugPane();});
  var portsRefresh=document.getElementById("climate-ports-refresh");
  if(portsRefresh) portsRefresh.addEventListener("click",function(){loadPortsPane();});
  document.getElementById("climate-toggle-ai").addEventListener("click",function(){
    if(workbench.classList.contains("is-ai-closed")){
      workbench.classList.remove("is-ai-closed");
      if(workbench.classList.contains("is-ai-collapsed")) expandAiPanel();
      else if(currentAiWidthPx() < AI_MIN) setAiExpandedWidth(AI_DEFAULT);
      fetchCodexRateLimits({ refresh: false });
    } else {
      workbench.classList.add("is-ai-closed");
      workbench.classList.remove("is-ai-maximized");
      workbench.classList.remove("is-ai-collapsed");
    }
    syncAiMaximizeChrome();
    scheduleEditorLayout();
    savePrefs();
  });
  document.getElementById("climate-toggle-bottom").addEventListener("click",function(){center.classList.toggle("is-bottom-closed");scheduleEditorLayout();savePrefs();});
  var maximizeAiBtn=document.getElementById("climate-maximize-ai");
  if(maximizeAiBtn){
    maximizeAiBtn.addEventListener("click",function(){
      if(workbench.classList.contains("is-ai-closed")||workbench.classList.contains("is-ai-collapsed")){
        expandAiPanel();
      }
      if(workbench.classList.contains("is-ai-maximized")){
        restoreAiFromMaximize();
      } else {
        workbench.classList.add("is-ai-maximized");
        workbench.classList.remove("is-ai-collapsed");
        syncAiMaximizeChrome();
        scheduleEditorLayout();
      }
      savePrefs();
    });
  }
  document.getElementById("climate-ai-close").addEventListener("click",function(){
    rememberUsableAiWidth(currentAiWidthPx());
    workbench.classList.add("is-ai-closed");
    workbench.classList.remove("is-ai-maximized");
    workbench.classList.remove("is-ai-collapsed");
    syncAiMaximizeChrome();
    scheduleEditorLayout();
    savePrefs();
  });
  var expandAiBtn=document.getElementById("climate-ai-expand");
  if(expandAiBtn){
    expandAiBtn.addEventListener("click",function(){
      expandAiPanel();
      savePrefs();
      promptEl.focus();
    });
  }
  document.getElementById("climate-toggle-left").addEventListener("click",function(){workbench.classList.toggle("is-left-closed");scheduleEditorLayout();savePrefs();});
  document.querySelectorAll("[data-activity]").forEach(function(button){button.addEventListener("click",function(){var activity=button.dataset.activity;if(activity==="explorer"){workbench.classList.remove("is-left-closed");}else if(activity==="search"){workbench.classList.remove("is-left-closed");document.getElementById("climate-search").focus();}else if(activity==="git"||activity==="tests"){switchPanel(activity);}else if(activity==="ai"){workbench.classList.remove("is-ai-closed");if(workbench.classList.contains("is-ai-collapsed"))expandAiPanel();promptEl.focus();fetchCodexRateLimits({ refresh: false });}else{setStatus("Workspace settings remain in CLIMATE Settings.");}document.querySelectorAll("[data-activity]").forEach(function(item){item.classList.toggle("is-active",item===button);});scheduleEditorLayout();savePrefs();});});
  window.addEventListener("beforeunload",function(e){captureActive();if(state.tabs.some(function(tab){return tab.dirty;})){e.preventDefault();e.returnValue="";}});
  window.addEventListener("resize", function () {
    if (workbench.classList.contains("is-ai-maximized") && !workbench.classList.contains("is-ai-collapsed") && !workbench.classList.contains("is-ai-closed")) {
      syncAiMaximizeChrome();
    }
    scheduleEditorLayout();
  });
  loadPrefs();normalizeAiPanelState();syncAiMaximizeChrome();renderTabs();renderProviders();enhanceProviderModelDropdowns();renderAttached();loadChatStore();hydrateServerHistory();
  (function syncModeAfterLoad(){
    var session=activeSession();
    applyExecutionMode((session&&session.executionMode)||currentExecutionMode(),{skipSession:true,skipPrefs:true});
  })();
  loadTree();loadGit();setupResize();initMonaco();if(state.active)openFile(state.active);
  switchPanel(state.panel,{ keepClosed: center.classList.contains("is-bottom-closed") });
  if(!workbench.classList.contains("is-ai-closed")) fetchCodexRateLimits({ refresh: false });
}());
