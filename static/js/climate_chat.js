(function () {
  "use strict";

  var root = document.getElementById("ax-chat");
  if (!root) return;

  var workspace = root.getAttribute("data-workspace") || "work";
  var apiRoot = root.getAttribute("data-api-root") || ("/api/climate/" + workspace);
  var bootstrap = {};
  try { bootstrap = JSON.parse(root.getAttribute("data-bootstrap") || "{}"); }
  catch (_) { bootstrap = {}; }

  var historyEl = document.getElementById("ax-chat-history");
  var feedEl = document.getElementById("ax-chat-feed");
  var emptyEl = document.getElementById("ax-chat-empty");
  var promptEl = document.getElementById("ax-prompt");
  var sendBtn = document.getElementById("ax-send");
  var stopBtn = document.getElementById("ax-stop");
  var providerSelect = document.getElementById("ax-provider");
  var modelSelect = document.getElementById("ax-model");
  var providerState = document.getElementById("ax-provider-state");
  var providerDot = document.getElementById("ax-provider-dot");
  var titleEl = document.getElementById("ax-chat-title");
  var storeKey = "ax-climate-chat:" + workspace;
  var selectionKey = "ax-climate-chat-selection:" + workspace;

  var state = {
    conversations: [],
    activeId: "",
    messages: [],
    title: "New chat",
    runId: "",
    runActive: false,
    stopRequested: false,
    pollTimer: 0,
    streamText: "",
    modelCache: {}
  };

  function endpoint(path) { return apiRoot + path; }
  function uid(prefix) { return prefix + "-" + Math.random().toString(36).slice(2, 10); }
  function escapeHtml(value) {
    return String(value == null ? "" : value).replace(/[&<>"']/g, function (c) {
      return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c];
    });
  }
  function jsonFetch(url, opts) {
    return fetch(url, opts).then(function (res) {
      return res.json().then(function (data) {
        if (!res.ok || data.ok === false) {
          var err = new Error(data.error || res.statusText || "Request failed");
          err.code = data.code || "";
          throw err;
        }
        return data;
      });
    });
  }
  function connectedProvider(id) {
    return (bootstrap.providers || []).find(function (row) { return row.id === id; });
  }
  function saveActive() {
    try { sessionStorage.setItem(storeKey, state.activeId || ""); } catch (_) {}
  }
  function loadActive() {
    try { return sessionStorage.getItem(storeKey) || ""; } catch (_) { return ""; }
  }
  function saveChatSelection(sel) {
    try {
      sessionStorage.setItem(selectionKey, JSON.stringify({
        provider: (sel && sel.provider) || "",
        model: (sel && sel.model) || "",
        mode: (sel && sel.mode) || currentChatMode(),
        repositoryId: (sel && sel.repositoryId) || currentChatRepo(),
        contextScope: (sel && sel.contextScope) || currentChatScope().scope
      }));
    } catch (_) {}
  }
  function loadChatSelection() {
    try { return JSON.parse(sessionStorage.getItem(selectionKey) || "{}") || {}; }
    catch (_) { return {}; }
  }
  function chatSurfaceDefaults() {
    var defaults = bootstrap.coding_defaults || {};
    return defaults.chat || { default_provider: "", default_model: "", default_mode: "climate_assisted" };
  }
  function currentChatMode() {
    var sel = document.getElementById("ax-execution-mode");
    return sel && sel.value === "direct" ? "direct" : "climate_assisted";
  }
  function currentChatScope() {
    var sel = document.getElementById("ax-context-scope");
    var value = String((sel && sel.value) || "general").trim();
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
  function currentChatRepo() {
    return currentChatScope().repositoryId;
  }
  function preferredChatMode() {
    var sel = loadChatSelection();
    if (sel.mode === "direct" || sel.mode === "climate_assisted") return sel.mode;
    return chatSurfaceDefaults().default_mode === "direct" ? "direct" : "climate_assisted";
  }
  function applyChatMode(mode) {
    var next = mode === "direct" ? "direct" : "climate_assisted";
    var hidden = document.getElementById("ax-execution-mode");
    if (hidden) hidden.value = next;
    document.querySelectorAll(".ax-pill-mode [data-execution-mode]").forEach(function (btn) {
      btn.setAttribute("aria-pressed", btn.getAttribute("data-execution-mode") === next ? "true" : "false");
    });
    var pill = document.querySelector(".ax-pill-mode");
    if (pill) {
      pill.setAttribute(
        "title",
        next === "direct"
          ? "Direct — send the prompt to the selected provider/model with minimal CLIMATE orchestration."
          : "AiriX — CLIMATE orchestration, then the selected provider/model."
      );
    }
  }
  function preferredChatProvider() {
    var sel = loadChatSelection();
    if (sel.provider) return sel.provider;
    var chat = chatSurfaceDefaults();
    if (chat.default_provider) return chat.default_provider;
    var first = (bootstrap.providers || []).find(function (row) { return row.state === "connected"; });
    return (first || {}).id || "";
  }
  function preferredChatModel(providerId) {
    var sel = loadChatSelection();
    if (sel.provider === providerId && sel.model) return sel.model;
    var defaults = bootstrap.coding_defaults || {};
    var chat = chatSurfaceDefaults();
    if (chat.default_provider === providerId && chat.default_model) return chat.default_model;
    return (defaults.default_models || {})[providerId] || "";
  }
  function listedModelOrAuto(models, preferred) {
    var list = models || [];
    if (preferred && list.indexOf(preferred) >= 0) return preferred;
    if (!preferred && list.indexOf("__provider_default__") >= 0) return "__provider_default__";
    return "";
  }
  function providerLabel(providerId) {
    var p = connectedProvider(providerId || providerSelect.value);
    if (p && (p.label || p.name)) return p.label || p.name;
    var opt = providerSelect.options[providerSelect.selectedIndex];
    return (opt && opt.textContent) || providerId || "Provider";
  }
  function currentThinkingLabel() {
    if (currentChatMode() !== "direct") return "AiriX is thinking…";
    return providerLabel(providerSelect.value) + " is thinking…";
  }
  function compactError(value) {
    var line = String(value || "Request failed").trim().split(/\r?\n/)[0].trim();
    if (!line) line = "Request failed";
    return line.length > 180 ? (line.slice(0, 177) + "…") : line;
  }
  function streamTextFromLogs(logs) {
    var raw = String(logs || "");
    var cut = raw.search(/\n\s*\[[a-z][\w:-]*\]/i);
    if (cut >= 0) raw = raw.slice(0, cut);
    if (/^\s*\[[a-z][\w:-]*\]/i.test(raw)) return "";
    return raw;
  }
  function displayTextFromRun(run) {
    var answer = String((run && run.answer) || "");
    if (answer.trim()) return answer;
    return streamTextFromLogs((run && run.logs) || "");
  }
  function diagnosticsFromRun(run) {
    var parts = [];
    var error = String((run && run.error) || "").trim();
    if (error) parts.push(error);
    var logs = String((run && run.logs) || "");
    var tagged = logs.match(/\[[a-z][\w:-]*\][\s\S]*$/i);
    if (tagged) parts.push(tagged[0].trim());
    else if (/^\s*\[[a-z][\w:-]*\]/i.test(logs)) parts.push(logs.trim());
    var seen = {};
    return parts.filter(function (part) {
      if (seen[part]) return false;
      seen[part] = true;
      return !!part;
    }).join("\n\n");
  }
  function assistantPhase(msg) {
    if (msg.error || msg.status === "failed" || msg.status === "unavailable") return "error";
    if (msg.status === "cancelled" || msg.stopNotice) return "cancelled";
    if (msg.status === "completed" || msg.sealed) return "completed";
    if (String(msg.text || "").trim()) return "streaming";
    return "thinking";
  }
  function titleFromPrompt(prompt) {
    var t = String(prompt || "").replace(/\s+/g, " ").trim();
    if (!t) return "New chat";
    return t.length > 52 ? (t.slice(0, 49) + "…") : t;
  }
  function resizePrompt() {
    promptEl.style.height = "auto";
    promptEl.style.height = Math.min(180, Math.max(44, promptEl.scrollHeight)) + "px";
  }
  function setSessionBusy(busy) {
    var controls = document.querySelector(".ax-chat-controls");
    if (controls) controls.classList.toggle("is-busy", !!busy);
    root.classList.toggle("is-busy", !!busy);
    [providerSelect, modelSelect, document.getElementById("ax-context-scope")].forEach(function (el) {
      if (el) el.disabled = !!busy;
    });
    document.querySelectorAll(".ax-pill-mode [data-execution-mode]").forEach(function (btn) {
      btn.disabled = !!busy;
    });
    var refresh = document.getElementById("ax-model-refresh");
    if (refresh) refresh.disabled = !!busy;
    if (busy) {
      document.querySelectorAll(".climate-dd.is-open").forEach(function (dd) {
        dd.classList.remove("is-open");
        var trigger = dd.querySelector(".climate-dd-trigger");
        if (trigger) trigger.setAttribute("aria-expanded", "false");
        var menu = dd._menu || dd.querySelector(".climate-dd-menu");
        if (menu) {
          menu.hidden = true;
          menu.classList.remove("is-portal", "is-up");
        }
      });
    }
    enhanceChatSelects();
  }
  function setRunControls(mode) {
    var idle = mode === "idle";
    var stopping = mode === "stopping";
    setSessionBusy(!idle);
    sendBtn.hidden = !idle;
    sendBtn.disabled = idle ? !modelSelect.value : true;
    stopBtn.hidden = idle;
    stopBtn.disabled = stopping;
    stopBtn.textContent = stopping ? "Stopping…" : "Stop";
  }
  function setProviderChrome(providerId) {
    var p = connectedProvider(providerId);
    var connected = !!(p && p.state === "connected");
    var statusText = connected ? "Connected" : (p ? (p.status || p.state) : "Unavailable");
    var label = providerState.querySelector("span");
    if (label) label.textContent = statusText;
    providerState.className = "ax-chat-state " + (connected ? "is-ok" : "is-error");
    providerDot.className = "ax-pill-dot " + (connected ? "is-ok" : "is-error");
    if (!state.runActive) sendBtn.disabled = !(connected && modelSelect.value);
  }
  function applyModelOptions(models, preferred) {
    var options = '<option value="" disabled>Select exact model</option>' + (models || []).map(function (m) {
      return '<option value="' + escapeHtml(m) + '">' + escapeHtml(m) + "</option>";
    }).join("");
    modelSelect.innerHTML = options;
    var pick = listedModelOrAuto(models, preferred);
    if (pick && (models || []).indexOf(pick) >= 0) modelSelect.value = pick;
    else modelSelect.selectedIndex = 0;
    if (!state.runActive) sendBtn.disabled = !modelSelect.value;
    enhanceChatSelects();
  }
  function selectProvider(providerId, opts) {
    opts = opts || {};
    var p = connectedProvider(providerId);
    if (providerId) providerSelect.value = providerId;
    setProviderChrome(providerSelect.value);
    enhanceChatSelects();
    if (!p || p.state !== "connected") {
      modelSelect.innerHTML = '<option value="" disabled>' + escapeHtml((p && p.detail) || "Provider unavailable") + "</option>";
      sendBtn.disabled = true;
      enhanceChatSelects();
      return;
    }
    var cached = state.modelCache[providerSelect.value];
    var preferred = opts.preserveModel || preferredChatModel(providerSelect.value) || "";
    if (cached && !opts.refresh) {
      applyModelOptions(cached.models, listedModelOrAuto(cached.models, preferred));
      return;
    }
    jsonFetch(endpoint("/providers/" + encodeURIComponent(providerSelect.value) + "/models" + (opts.refresh ? "?refresh=1" : ""))).then(function (data) {
      state.modelCache[providerSelect.value] = {
        models: data.models || [],
        recommended: data.recommended_model || ""
      };
      applyModelOptions(data.models || [], listedModelOrAuto(data.models || [], preferred));
    }).catch(function (err) {
      modelSelect.innerHTML = '<option value="" disabled>' + escapeHtml(err.message || "Models unavailable") + "</option>";
      sendBtn.disabled = true;
      enhanceChatSelects();
    });
  }
  function renderHistory() {
    if (!state.conversations.length) {
      historyEl.innerHTML = '<div class="ax-chat-history-empty">No conversations yet</div>';
      return;
    }
    historyEl.innerHTML = state.conversations.map(function (row) {
      var active = row.id === state.activeId ? " is-active" : "";
      return '<button type="button" class="ax-chat-item' + active + '" data-id="' + escapeHtml(row.id) + '">' +
        escapeHtml(row.title || "New chat") + "</button>";
    }).join("");
  }
  function renderFeed() {
    titleEl.textContent = state.title || "New chat";
    if (!state.messages.length) {
      feedEl.innerHTML = "";
      feedEl.appendChild(emptyEl);
      emptyEl.hidden = false;
      return;
    }
    emptyEl.hidden = true;
    feedEl.innerHTML = state.messages.map(function (msg) {
      var phase = msg.role === "assistant" ? assistantPhase(msg) : "";
      var cls = "ax-msg is-" + msg.role + (msg.error ? " is-error" : "") + (phase ? " is-" + phase : "");
      var role = msg.role === "user" ? "You" : "AiriX";
      var statusHtml = "";
      var body = "";
      if (msg.role === "assistant" && phase === "thinking") {
        statusHtml =
          '<div class="ax-run-status is-thinking" role="status">' +
            '<span class="ax-run-spinner" aria-hidden="true"></span>' +
            '<div class="ax-run-status-copy">' +
              "<strong>" + escapeHtml(msg.thinkingLabel || "AiriX is thinking…") + "</strong>" +
              "<small>This may take a few seconds.</small>" +
            "</div>" +
          "</div>";
      } else if (msg.role === "assistant" && phase === "streaming") {
        statusHtml =
          '<div class="ax-run-stream">' +
            '<span class="ax-run-typing" aria-hidden="true"><i></i><i></i><i></i></span>' +
            '<div class="ax-msg-body climate-md is-streaming" data-md="1"></div>' +
          "</div>";
        body = "";
      } else if (msg.role === "assistant" && phase === "cancelled") {
        statusHtml =
          '<div class="ax-run-status is-cancelled" role="status">' +
            '<span class="ax-run-cancelled-icon" aria-hidden="true">×</span>' +
            '<div class="ax-run-status-copy">' +
              "<strong>Response cancelled</strong>" +
              "<small>You stopped this request.</small>" +
            "</div>" +
          "</div>";
        if (String(msg.text || "").trim()) {
          body = '<div class="ax-msg-body climate-md" data-md="1"></div>';
        }
      } else if (msg.role === "assistant" && phase === "error") {
        statusHtml =
          '<div class="ax-run-status is-error" role="status">' +
            '<span class="ax-run-error-icon" aria-hidden="true">!</span>' +
            '<div class="ax-run-status-copy">' +
              "<strong>Request failed</strong>" +
              "<small>" + escapeHtml(msg.errorMessage || compactError(msg.text) || "Request failed") + "</small>" +
            "</div>" +
          "</div>";
      } else if (msg.role === "assistant") {
        body = '<div class="ax-msg-body climate-md" data-md="1"></div>';
      } else {
        body = '<div class="ax-msg-body">' + escapeHtml(msg.text || "") + "</div>";
      }
      var details = "";
      if (msg.role === "assistant" && phase !== "thinking" && phase !== "streaming" && (msg.provider || msg.model || msg.status || msg.diagnostics)) {
        details = '<details class="ax-msg-details"><summary>Details</summary>' +
          "<div>" + escapeHtml([msg.provider, msg.model, msg.status].filter(Boolean).join(" · ")) + "</div>" +
          (msg.diagnostics ? "<pre>" + escapeHtml(msg.diagnostics) + "</pre>" : "") +
          "</details>";
      }
      return '<article class="' + cls + '" data-id="' + escapeHtml(msg.id) + '">' +
        '<div class="ax-msg-role">' + role + "</div>" + statusHtml + body + details + "</article>";
    }).join("");
    feedEl.querySelectorAll("[data-md]").forEach(function (el) {
      var article = el.closest(".ax-msg");
      var msg = state.messages.find(function (item) { return item.id === (article && article.getAttribute("data-id")); });
      if (window.ClimateMarkdown) window.ClimateMarkdown.mount(el, (msg && msg.text) || "");
      else el.textContent = (msg && msg.text) || "";
    });
    feedEl.scrollTop = feedEl.scrollHeight;
  }
  function newChat() {
    state.activeId = "";
    state.messages = [];
    state.title = "New chat";
    state.runId = "";
    state.runActive = false;
    state.stopRequested = false;
    saveActive();
    renderHistory();
    renderFeed();
    setRunControls("idle");
    promptEl.focus();
  }
  function hydrateConversation(conversation) {
    state.activeId = conversation.id || "";
    state.title = conversation.title || "New chat";
    var messages = [];
    (conversation.runs || []).forEach(function (run) {
      if (run.prompt) {
        messages.push({ id: uid("u"), role: "user", text: run.prompt, ts: run.created_at });
      }
      var text = displayTextFromRun(run);
      if (text || run.status) {
        messages.push({
          id: run.id || uid("a"),
          role: "assistant",
          text: (run.status === "failed" || run.status === "unavailable") ? "" : text,
          diagnostics: diagnosticsFromRun(run),
          status: run.status,
          provider: run.provider,
          model: run.model,
          error: run.status === "failed" || run.status === "unavailable",
          errorMessage: (run.status === "failed" || run.status === "unavailable")
            ? compactError(run.error || text)
            : "",
          stopNotice: run.status === "cancelled" ? "You stopped this request." : "",
          sealed: true
        });
      }
    });
    state.messages = messages;
    saveActive();
    var last = null;
    for (var i = messages.length - 1; i >= 0; i -= 1) {
      if (messages[i] && messages[i].provider) {
        last = messages[i];
        break;
      }
    }
    if (last && last.provider) {
      saveChatSelection({ provider: last.provider, model: last.model || "" });
      selectProvider(last.provider, { refresh: true, preserveModel: last.model || "" });
    }
    renderHistory();
    renderFeed();
  }
  function loadConversations(preferredId) {
    return jsonFetch(endpoint("/conversations?surface=chat&limit=50")).then(function (data) {
      state.conversations = data.conversations || [];
      renderHistory();
      var want = preferredId || state.activeId || loadActive();
      if (want && state.conversations.some(function (row) { return row.id === want; })) {
        return openConversation(want);
      }
    });
  }
  function openConversation(id) {
    return jsonFetch(endpoint("/conversations/" + encodeURIComponent(id) + "?surface=chat")).then(function (data) {
      hydrateConversation(data.conversation || {});
    });
  }
  function upsertAssistant(fields) {
    var last = state.messages[state.messages.length - 1];
    if (!last || last.role !== "assistant" || last.sealed) {
      last = { id: uid("a"), role: "assistant", text: "", status: "running" };
      state.messages.push(last);
    }
    Object.keys(fields || {}).forEach(function (key) { last[key] = fields[key]; });
    renderFeed();
    return last;
  }
  function pollRun() {
    if (!state.runId) return;
    var pollId = state.runId;
    jsonFetch(endpoint("/runs/" + encodeURIComponent(state.runId))).then(function (data) {
      if (pollId !== state.runId) return;
      var run = data.run || {};
      var terminal = ["completed", "failed", "cancelled", "unavailable"].indexOf(run.status) >= 0;
      var text = displayTextFromRun(run);
      var failed = run.status === "failed" || run.status === "unavailable";
      upsertAssistant({
        text: failed ? "" : text,
        diagnostics: diagnosticsFromRun(run),
        status: run.status,
        provider: run.provider,
        model: run.model,
        error: failed,
        errorMessage: failed ? compactError(run.error || text) : "",
        stopNotice: run.status === "cancelled" ? "You stopped this request." : "",
        sealed: terminal
      });
      if (!terminal) {
        state.pollTimer = window.setTimeout(pollRun, 400);
        return;
      }
      state.runActive = false;
      state.runId = "";
      state.stopRequested = false;
      setRunControls("idle");
      loadConversations(state.activeId);
    }).catch(function (err) {
      upsertAssistant({
        text: "",
        status: "failed",
        error: true,
        errorMessage: compactError(err.message || "Run failed"),
        sealed: true
      });
      state.runActive = false;
      state.runId = "";
      setRunControls("idle");
    });
  }
  function sendRun() {
    var prompt = promptEl.value.trim();
    if (!prompt || !providerSelect.value || !modelSelect.value || state.runActive) return;
    state.runActive = true;
    state.stopRequested = false;
    state.streamText = "";
    if (!state.title || state.title === "New chat") state.title = titleFromPrompt(prompt);
    state.messages.push({ id: uid("u"), role: "user", text: prompt });
    upsertAssistant({
      text: "",
      status: "running",
      provider: providerSelect.value,
      model: modelSelect.value,
      thinkingLabel: currentThinkingLabel(),
      diagnostics: ""
    });
    promptEl.value = "";
    resizePrompt();
    setRunControls("running");
    jsonFetch(endpoint("/chat/runs"), {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        provider: providerSelect.value,
        model: modelSelect.value,
        prompt: prompt,
        display_prompt: prompt,
        task_mode: "ask",
        conversation_id: state.activeId || "",
        reuse_session: true,
        execution_mode: currentChatMode(),
        context_scope: currentChatScope().scope,
        repository_id: currentChatScope().repositoryId,
        include_repo_context: currentChatScope().scope === "repository"
      })
    }).then(function (data) {
      var run = data.run || {};
      if (run.conversation_id) {
        state.activeId = run.conversation_id;
        saveActive();
      }
      if (state.stopRequested) {
        state.runId = run.id;
        requestStop(run.id);
        return;
      }
      state.runId = run.id;
      pollRun();
    }).catch(function (err) {
      upsertAssistant({
        text: "",
        status: "failed",
        error: true,
        errorMessage: compactError(err.message || "Request failed"),
        sealed: true
      });
      state.runActive = false;
      setRunControls("idle");
    });
  }
  function requestStop(runId) {
    setRunControls("stopping");
    jsonFetch(endpoint("/runs/" + encodeURIComponent(runId) + "/cancel"), { method: "POST" }).then(function () {
      state.pollTimer = window.setTimeout(pollRun, 250);
    }).catch(function () {
      state.pollTimer = window.setTimeout(pollRun, 250);
    });
  }

  function enhanceChatSelects() {
    if (window.ClimateSelect) {
      window.ClimateSelect.enhanceAll([providerSelect, modelSelect, document.getElementById("ax-context-scope")]);
    }
  }

  (bootstrap.providers || []).forEach(function (row) {
    var opt = document.createElement("option");
    opt.value = row.id;
    opt.textContent = row.label || row.id;
    if (row.state !== "connected") opt.setAttribute("data-unavailable", "1");
    providerSelect.appendChild(opt);
  });
  var preferred = preferredChatProvider();
  if (preferred) providerSelect.value = preferred;
  applyChatMode(preferredChatMode());
  var savedSel = loadChatSelection();
  var scopeSelect = document.getElementById("ax-context-scope");
  if (scopeSelect) {
    if (savedSel.contextScope === "all") scopeSelect.value = "all";
    else if (savedSel.repositoryId) scopeSelect.value = savedSel.repositoryId;
    else scopeSelect.value = "general";
    if (!scopeSelect.value) scopeSelect.value = "general";
  }
  enhanceChatSelects();
  selectProvider(providerSelect.value, { refresh: true });

  document.getElementById("ax-chat-new").addEventListener("click", newChat);
  document.getElementById("ax-model-refresh").addEventListener("click", function () {
    selectProvider(providerSelect.value, { refresh: true, preserveModel: modelSelect.value });
  });
  document.getElementById("ax-chat-rename").addEventListener("click", function () {
    if (!state.activeId) return;
    var next = window.prompt("Rename chat", state.title || "");
    if (next == null) return;
    jsonFetch(endpoint("/conversations/" + encodeURIComponent(state.activeId)), {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ title: next, surface: "chat" })
    }).then(function (data) {
      state.title = (data.conversation && data.conversation.title) || next;
      loadConversations(state.activeId);
      renderFeed();
    });
  });
  historyEl.addEventListener("click", function (event) {
    var btn = event.target.closest("[data-id]");
    if (!btn || state.runActive) return;
    openConversation(btn.getAttribute("data-id"));
  });
  providerSelect.addEventListener("change", function () {
    saveChatSelection({ provider: providerSelect.value, model: "", mode: currentChatMode(), repositoryId: currentChatRepo(), contextScope: currentChatScope().scope });
    selectProvider(providerSelect.value, { refresh: true });
  });
  modelSelect.addEventListener("change", function () {
    saveChatSelection({ provider: providerSelect.value, model: modelSelect.value, mode: currentChatMode(), repositoryId: currentChatRepo(), contextScope: currentChatScope().scope });
    if (!state.runActive) sendBtn.disabled = !modelSelect.value;
  });
  document.querySelectorAll(".ax-pill-mode [data-execution-mode]").forEach(function (btn) {
    btn.addEventListener("click", function () {
      if (state.runActive) return;
      applyChatMode(btn.getAttribute("data-execution-mode"));
      saveChatSelection({ provider: providerSelect.value, model: modelSelect.value, mode: currentChatMode(), repositoryId: currentChatRepo(), contextScope: currentChatScope().scope });
    });
  });
  if (document.getElementById("ax-context-scope")) {
    document.getElementById("ax-context-scope").addEventListener("change", function () {
      saveChatSelection({ provider: providerSelect.value, model: modelSelect.value, mode: currentChatMode(), repositoryId: currentChatRepo(), contextScope: currentChatScope().scope });
    });
  }
  sendBtn.addEventListener("click", sendRun);
  stopBtn.addEventListener("click", function () {
    if (!state.runActive) return;
    state.stopRequested = true;
    if (state.runId) requestStop(state.runId);
    else setRunControls("stopping");
  });
  promptEl.addEventListener("input", resizePrompt);
  promptEl.addEventListener("keydown", function (event) {
    if (event.key === "Enter" && !event.shiftKey) {
      event.preventDefault();
      sendRun();
    }
  });
  loadConversations();
  setRunControls("idle");
})();
