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
  function titleFromPrompt(prompt) {
    var t = String(prompt || "").replace(/\s+/g, " ").trim();
    if (!t) return "New chat";
    return t.length > 52 ? (t.slice(0, 49) + "…") : t;
  }
  function resizePrompt() {
    promptEl.style.height = "auto";
    promptEl.style.height = Math.min(180, Math.max(44, promptEl.scrollHeight)) + "px";
  }
  function setRunControls(mode) {
    var idle = mode === "idle";
    var stopping = mode === "stopping";
    sendBtn.hidden = !idle;
    sendBtn.disabled = idle ? !modelSelect.value : true;
    stopBtn.hidden = idle;
    stopBtn.disabled = stopping;
    stopBtn.textContent = stopping ? "Stopping…" : "■ Stop";
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
    var pick = preferred || "";
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
    var preferred = opts.preserveModel || modelSelect.value || "";
    if (cached && !opts.refresh) {
      applyModelOptions(cached.models, preferred || cached.recommended);
      return;
    }
    jsonFetch(endpoint("/providers/" + encodeURIComponent(providerSelect.value) + "/models" + (opts.refresh ? "?refresh=1" : ""))).then(function (data) {
      state.modelCache[providerSelect.value] = {
        models: data.models || [],
        recommended: data.recommended_model || ""
      };
      applyModelOptions(data.models || [], preferred || data.recommended_model || "");
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
      var cls = "ax-msg is-" + msg.role + (msg.error ? " is-error" : "");
      var role = msg.role === "user" ? "You" : "AiriX";
      var body = msg.role === "assistant" && !msg.error
        ? '<div class="ax-msg-body climate-md" data-md="1"></div>'
        : '<div class="ax-msg-body">' + escapeHtml(msg.text || "") + "</div>";
      var details = "";
      if (msg.role === "assistant" && (msg.provider || msg.model || msg.status)) {
        details = '<details class="ax-msg-details"><summary>Details</summary>' +
          escapeHtml([msg.provider, msg.model, msg.status].filter(Boolean).join(" · ")) +
          (msg.stopNotice ? "<div>" + escapeHtml(msg.stopNotice) + "</div>" : "") +
          "</details>";
      }
      return '<article class="' + cls + '" data-id="' + escapeHtml(msg.id) + '">' +
        '<div class="ax-msg-role">' + role + "</div>" + body + details + "</article>";
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
      var text = run.answer || run.logs || run.error || "";
      if (text || run.status) {
        messages.push({
          id: run.id || uid("a"),
          role: "assistant",
          text: text,
          status: run.status,
          provider: run.provider,
          model: run.model,
          error: run.status === "failed" || run.status === "unavailable"
        });
      }
    });
    state.messages = messages;
    saveActive();
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
      var text = run.answer || run.logs || "";
      if (state.stopRequested && !terminal) {
        state.pollTimer = window.setTimeout(pollRun, 400);
        return;
      }
      upsertAssistant({
        text: text,
        status: run.status,
        provider: run.provider,
        model: run.model,
        error: run.status === "failed" || run.status === "unavailable",
        stopNotice: run.status === "cancelled" ? "Stopped by user" : "",
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
      upsertAssistant({ text: err.message || "Run failed", status: "failed", error: true, sealed: true });
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
      model: modelSelect.value
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
        reuse_session: true
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
      upsertAssistant({ text: err.message || "Request failed", status: "failed", error: true, sealed: true });
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
    if (window.ClimateSelect) window.ClimateSelect.enhanceAll([providerSelect, modelSelect]);
  }

  (bootstrap.providers || []).forEach(function (row) {
    var opt = document.createElement("option");
    opt.value = row.id;
    opt.textContent = row.label || row.id;
    if (row.state !== "connected") opt.setAttribute("data-unavailable", "1");
    providerSelect.appendChild(opt);
  });
  var preferred = (bootstrap.providers || []).find(function (row) {
    return row.id === "gemini" && row.state === "connected";
  }) || (bootstrap.providers || []).find(function (row) { return row.state === "connected"; });
  if (preferred) providerSelect.value = preferred.id;
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
    selectProvider(providerSelect.value, { refresh: true });
  });
  modelSelect.addEventListener("change", function () {
    if (!state.runActive) sendBtn.disabled = !modelSelect.value;
  });
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
