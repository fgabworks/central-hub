(function () {
  "use strict";
  var shell = document.getElementById("climate-shell");
  if (!shell) return;
  var bootstrap = JSON.parse(shell.getAttribute("data-bootstrap") || "{}");
  var workspace = shell.getAttribute("data-workspace");
  var apiRoot = shell.getAttribute("data-api-root");
  var repoSelect = document.getElementById("climate-repository");
  var branchSelect = document.getElementById("climate-branch");
  var workbench = document.getElementById("climate-workbench");
  var center = workbench.querySelector(".climate-center");
  var treeEl = document.getElementById("climate-tree");
  var tabsEl = document.getElementById("climate-tabs");
  var fallback = document.getElementById("climate-editor-fallback");
  var welcome = document.getElementById("climate-welcome");
  var statusEl = document.getElementById("climate-status");
  var gitSummary = document.getElementById("climate-git-summary");
  var bottomBody = document.getElementById("climate-bottom-body");
  var feed = document.getElementById("climate-ai-feed");
  var providerSelect = document.getElementById("climate-provider");
  var modelSelect = document.getElementById("climate-model");
  var panelProviderSelect = document.getElementById("climate-provider-panel");
  var panelModelSelect = document.getElementById("climate-model-panel");
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
  var cancelBtn = document.getElementById("climate-cancel");
  var proposalActions = document.getElementById("climate-proposal-actions");
  var state = {
    tabs: [], active: "", selectedFiles: [], showExcluded: false, panel: "problems",
    runId: "", run: null, streamText: "", git: null, gitPath: "",
    chat: { activeId: "", sessions: [] }, streamingMsgId: "",
    activityExploreOpen: {},
    modelCache: {}, fetchCount: 0
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
      state.showExcluded = !!saved.showExcluded;
      document.getElementById("climate-show-excluded").checked = state.showExcluded;
      if (saved.left) workbench.style.setProperty("--left", saved.left + "px");
      if (saved.right) workbench.style.setProperty("--right", Math.max(AI_MIN, saved.right) + "px");
      else workbench.style.setProperty("--right", AI_DEFAULT + "px");
      if (saved.bottom) workbench.style.setProperty("--bottom", saved.bottom + "px");
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
    } catch (_) {}
  }
  function savePrefs() {
    var css = getComputedStyle(workbench);
    var rightPx = workbench.classList.contains("is-ai-collapsed")
      ? (parseInt(workbench.dataset.aiPrevRight, 10) || AI_DEFAULT)
      : (parseInt(css.getPropertyValue("--right"), 10) || AI_DEFAULT);
    localStorage.setItem(storageKey(), JSON.stringify({
      tabs: state.tabs.map(function (tab) { return tab.path; }), active: state.active,
      selectedFiles: state.selectedFiles, showExcluded: state.showExcluded, provider: providerSelect.value, model: modelSelect.value,
      left: parseInt(css.getPropertyValue("--left"), 10) || 230,
      right: Math.max(AI_MIN, rightPx),
      aiPrevRight: parseInt(workbench.dataset.aiPrevRight, 10) || Math.max(AI_MIN, rightPx),
      bottom: parseInt(css.getPropertyValue("--bottom"), 10) || 190,
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
    if (editor) editor.layout();
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
    if (editor) editor.layout();
  }
  function expandAiPanel() {
    var prev = parseInt(workbench.dataset.aiPrevRight || workbench.dataset.prevRight || AI_DEFAULT, 10);
    if (isNaN(prev) || prev < AI_MIN) prev = AI_DEFAULT;
    workbench.classList.remove("is-ai-closed");
    setAiExpandedWidth(prev);
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
    if (editor) editor.layout();
  }
  function languageId(language, path) {
    var map = {python:"python",javascript:"javascript",typescript:"typescript",json:"json",yaml:"yaml",markdown:"markdown",html:"html",css:"css",sql:"sql",shell:"shell"};
    if (map[language]) return map[language];
    var ext = (path.split(".").pop() || "").toLowerCase();
    return {py:"python",js:"javascript",ts:"typescript",json:"json",yml:"yaml",yaml:"yaml",md:"markdown",html:"html",css:"css",sql:"sql",ps1:"powershell"}[ext] || "plaintext";
  }
  function currentTab() { return state.tabs.find(function (tab) { return tab.path === state.active; }) || null; }
  function editorValue() { return editor ? editor.getValue() : fallback.value; }
  function setEditorValue(value, language, path) {
    if (editor) {
      var uri = window.monaco.Uri.parse("climate://" + workspace + "/" + repoId() + "/" + path);
      var model = window.monaco.editor.getModel(uri) || window.monaco.editor.createModel(value, languageId(language, path), uri);
      if (model.getValue() !== value) model.setValue(value);
      editor.setModel(model);
    } else fallback.value = value;
  }
  function captureActive() {
    var tab = currentTab();
    if (!tab || !tab.loaded) return;
    tab.content = editorValue();
    tab.dirty = tab.content !== tab.original;
  }
  function renderTabs() {
    if (!state.tabs.length) { tabsEl.innerHTML = '<div class="climate-empty-tab">Open a file from Explorer</div>'; return; }
    tabsEl.innerHTML = state.tabs.map(function (tab) {
      var name = tab.path.split("/").pop();
      return '<button class="climate-tab '+(tab.path===state.active?'is-active':'')+'" data-path="'+escapeHtml(tab.path)+'" title="'+escapeHtml(tab.path)+'"><span class="climate-tab-name">'+escapeHtml(name)+'</span><span class="climate-tab-dirty">'+(tab.dirty?'●':'')+'</span><span class="climate-tab-close" data-close="'+escapeHtml(tab.path)+'">×</span></button>';
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
    var tab = state.tabs.find(function (item) { return item.path === path; });
    if (tab && tab.dirty && !window.confirm("Close this file and discard unsaved changes?")) return;
    state.tabs = state.tabs.filter(function (item) { return item.path !== path; });
    if (window.monaco) { var uri = window.monaco.Uri.parse("climate://"+workspace+"/"+repoId()+"/"+path); var model = window.monaco.editor.getModel(uri); if (model) model.dispose(); }
    if (state.active === path) state.active = (state.tabs[state.tabs.length - 1] || {}).path || "";
    renderTabs(); savePrefs();
    if (state.active) activateTab(state.active); else { if (editor) editor.setModel(null); fallback.value=""; fallback.style.display="none"; welcome.hidden=false; }
  }
  function openFile(path) {
    captureActive();
    var existing = state.tabs.find(function (tab) { return tab.path === path; });
    if (!existing) { existing = {path:path,content:"",original:"",language:"plaintext",loaded:false,dirty:false}; state.tabs.push(existing); }
    state.active = path; renderTabs(); savePrefs(); setStatus("Opening " + path + "…");
    if (existing.loaded) return activateTab(path);
    jsonFetch(endpoint("/repositories/"+encodeURIComponent(repoId())+"/file?path="+encodeURIComponent(path))).then(function (data) {
      var file = data.file; existing.content = file.content || ""; existing.original = existing.content; existing.language = file.language || "plaintext"; existing.loaded = true; existing.dirty = false;
      activateTab(path); setStatus("Opened " + path);
    }).catch(function (error) { setStatus(error.message); });
  }
  function activateTab(path) {
    captureActive(); state.active = path;
    var tab = currentTab(); renderTabs(); savePrefs();
    if (!tab) return;
    if (!tab.loaded) return openFile(path);
    welcome.hidden = true; fallback.style.display = editor ? "none" : "block";
    setEditorValue(tab.content, tab.language, tab.path);
    breadcrumb.querySelector("span:first-child").textContent = tab.path.split("/").join("  ›  ");
    document.getElementById("climate-current-file-name").textContent = tab.path;
    document.getElementById("climate-language").textContent = languageId(tab.language, tab.path);
    highlightTree(); setStatus((tab.dirty ? "Unsaved · " : "") + tab.path);
  }
  function markDirty() {
    var tab = currentTab(); if (!tab || !tab.loaded) return;
    tab.content = editorValue(); tab.dirty = tab.content !== tab.original; renderTabs();
  }
  function renderTree(nodes, depth) {
    depth = depth || 0;
    return (nodes || []).map(function (node) {
      if (node.type === "dir") return '<details '+(depth<1?'open':'')+'><summary style="padding-left:'+(7+depth*10)+'px">▸ '+escapeHtml(node.name)+'</summary>'+renderTree(node.children || [],depth+1)+'</details>';
      var checked = state.selectedFiles.indexOf(node.path) >= 0 ? " checked" : "";
      var mark = node.git_status && node.git_status !== "clean" ? '<span class="climate-git-mark">'+escapeHtml(node.git_status.charAt(0).toUpperCase())+'</span>' : "";
      return '<button class="climate-file-row" data-path="'+escapeHtml(node.path)+'" style="padding-left:'+(9+depth*11)+'px"><input type="checkbox" data-context="'+escapeHtml(node.path)+'" aria-label="Include '+escapeHtml(node.name)+' in AI context"'+checked+'><span class="climate-file-name">'+escapeHtml(node.name)+'</span>'+mark+'</button>';
    }).join("");
  }
  function bindFileRows(container) {
    container.querySelectorAll(".climate-file-row").forEach(function (row) {
      row.addEventListener("click", function (event) {
        if (event.target.hasAttribute("data-context")) { event.stopPropagation(); toggleContext(event.target.getAttribute("data-context"), event.target.checked); return; }
        openFile(row.getAttribute("data-path"));
      });
    });
  }
  function toggleContext(path, enabled) {
    state.selectedFiles = state.selectedFiles.filter(function (item) { return item !== path; });
    if (enabled) state.selectedFiles.push(path);
    document.getElementById("climate-context-count").textContent = state.selectedFiles.length;
    document.getElementById("climate-related-count").textContent = state.selectedFiles.length; savePrefs();
  }
  function highlightTree() { treeEl.querySelectorAll(".climate-file-row").forEach(function (row) { row.classList.toggle("is-active",row.getAttribute("data-path")===state.active); }); }
  function loadTree() {
    if (!repoId()) { treeEl.innerHTML='<div class="climate-ai-empty">No repository in this workspace.</div>'; return; }
    treeEl.textContent="Loading…";
    jsonFetch(endpoint("/repositories/"+encodeURIComponent(repoId())+"/tree?show_excluded="+(state.showExcluded?"1":"0"))).then(function (data) {
      treeEl.innerHTML=renderTree(data.entries || [],0); bindFileRows(treeEl); highlightTree(); document.getElementById("climate-context-count").textContent=state.selectedFiles.length; document.getElementById("climate-related-count").textContent=state.selectedFiles.length;
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
    captureActive(); var tab=currentTab(); if(!tab||!tab.dirty)return;
    jsonFetch(endpoint("/repositories/"+encodeURIComponent(repoId())+"/preview-save"),{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({path:tab.path,content:tab.content})}).then(function(data){
      document.getElementById("climate-save-diff").textContent=data.diff||"No changes";document.getElementById("climate-save-dialog").showModal();
    }).catch(function(error){setStatus(error.message);});
  }
  function confirmSave() {
    captureActive(); var tab=currentTab(); if(!tab)return;
    jsonFetch(endpoint("/repositories/"+encodeURIComponent(repoId())+"/save"),{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({path:tab.path,content:tab.content,confirm:true})}).then(function(){tab.original=tab.content;tab.dirty=false;renderTabs();loadTree();loadGit();setStatus("Saved "+tab.path);}).catch(function(error){setStatus(error.message);});
  }
  function loadGit() {
    if(!repoId())return;
    jsonFetch(endpoint("/repositories/"+encodeURIComponent(repoId())+"/git/status")).then(function(data){
      state.git=data;var count=(data.files||[]).length;gitSummary.textContent=(data.branch||"Git")+(count?" · "+count+" changes":" · clean");
      if(state.panel==="git") renderGitWorkspace(data,state.gitPath);
      var option=repoSelect.options[repoSelect.selectedIndex];branchSelect.innerHTML='<option>'+escapeHtml(data.branch||option&&option.dataset.branch||'—')+'</option>';
    }).catch(function(error){gitSummary.textContent="Git · "+error.message;});
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
  function chatStorageKey() { return "climate:chat:v1:" + workspace; }
  function uid(prefix) { return (prefix || "id") + "-" + Date.now().toString(36) + Math.random().toString(36).slice(2, 7); }
  function providerLabel(id) {
    var row = (bootstrap.providers || []).find(function (p) { return p.id === id; });
    return (row && row.label) || id || "Assistant";
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
  function formatTokenCount(n) {
    n = Math.max(0, Number(n) || 0);
    if (n < 1000) return String(Math.round(n));
    if (n < 10000) return (n / 1000).toFixed(1).replace(/\.0$/, "") + "k";
    if (n < 1000000) return (n / 1000).toFixed(1).replace(/\.0$/, "") + "k";
    return (n / 1000000).toFixed(2).replace(/\.00$/, "") + "M";
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
    if (sourceEl) sourceEl.textContent = usage.source || "unavailable";
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
    if (/^\{[\s\S]*"type"\s*:\s*"(thread|turn|response|item|event|tool)/i.test(t)) return true;
    if (/^\{[\s\S]*"type"\s*:\s*"[^"]+\.[^"]+"/i.test(t)) return true;
    if (/^(delta|reasoning|output_text|content_block|message_start|message_stop)\b/i.test(t)) return true;
    return false;
  }
  function looksLikeProtocolDump(text) {
    var t = String(text || "").trim();
    if (!t) return false;
    var lines = t.split(/\r?\n/).filter(Boolean);
    if (!lines.length) return false;
    var raw = lines.filter(isRawProviderLine).length;
    return raw >= Math.ceil(lines.length * 0.5) || (lines.length <= 3 && raw > 0);
  }
  function splitRunOutput(logs, answer) {
    var diag = [];
    var clean = [];
    String(logs || "").split(/\r?\n/).forEach(function (line) {
      if (!line) return;
      if (isRawProviderLine(line)) diag.push(line);
      else clean.push(line);
    });
    var body = String(answer || "").trim();
    if (!body) body = clean.join("\n").trim();
    if (body && (isRawProviderLine(body) || looksLikeProtocolDump(body))) {
      diag.push(body);
      body = clean.filter(function (line) { return !isRawProviderLine(line); }).join("\n").trim();
    }
    if (!body && clean.length) {
      var residual = clean.filter(function (line) { return !isRawProviderLine(line); }).join("\n").trim();
      body = residual;
    }
    return { text: body || "", diagnostics: diag.join("\n") };
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
    return files;
  }
  /**
   * Build activity steps from runtime/tool log evidence only — never invent unseen steps.
   */
  function parseActivityEvidence(source, opts) {
    opts = opts || {};
    var blob = String(source || "");
    var running = !!opts.running;
    var files = collectActivityFiles(blob);
    var exploreMatch = blob.match(/Explor(?:ing|ed)\s+(\d+)\s+files?/i);
    var exploreCount = exploreMatch ? parseInt(exploreMatch[1], 10) : files.length;
    if (isNaN(exploreCount)) exploreCount = files.length;
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
    if (has(/\b(search(?:ing)?\s+repository|grep|glob|ripgrep|find_files?|codebase_search|workspace.?search)\b/i)) {
      addStep("search", "Searching repository");
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
    if (has(/\b(preparing response|turn\.completed|response\.completed|final answer)\b/i) || opts.hasAnswer) {
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
      planning: running && steps.length > 0
    };
  }
  function renderActivityProgress(msg) {
    var activity = msg.activity || parseActivityEvidence((msg.diagnostics || "") + "\n" + (msg.text || ""), {
      running: true,
      startedAt: msg.startedAt
    });
    var steps = activity.steps || [];
    var html = '<section class="climate-activity-progress" data-activity-root="' + escapeHtml(msg.id) + '">';
    html += '<div class="climate-activity-head"><span class="climate-activity-title">Working on your request…</span>';
    html += '<button type="button" class="climate-activity-chevron" data-activity-collapse aria-label="Collapse activity">▾</button></div>';
    if (steps.length) {
      html += '<ol class="climate-activity-steps">';
      steps.forEach(function (step) {
        var state = step.state || "done";
        html += '<li class="climate-activity-step is-' + escapeHtml(state) + (step.id === "explore" ? " is-explore" : "") + '" data-step="' + escapeHtml(step.id) + '">';
        if (state === "done") html += '<span class="climate-activity-mark" aria-hidden="true">✓</span>';
        else if (state === "current") html += '<span class="climate-activity-mark is-pulse" aria-hidden="true"><i></i><i></i><i></i></span>';
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
      if (activity.planning) {
        html += '<div class="climate-activity-planning"><span aria-hidden="true">✦</span> Planning next moves</div>';
      }
    }
    html += '</section>';
    return html;
  }
  function renderActivityComplete(msg) {
    var activity = msg.activity || parseActivityEvidence((msg.diagnostics || "") + "\n" + (msg.text || ""), {
      running: false,
      elapsedMs: msg.elapsedMs,
      tests: msg.tests,
      hasProposal: !!(msg.proposal && msg.proposal.state === "pending"),
      status: msg.status
    });
    var exploreCount = (activity.explore && activity.explore.count) || (msg.changedFiles || []).length || msg.filesInspected || 0;
    var testsRan = activity.testsRan || (msg.tests && msg.tests.count) || 0;
    if (!testsRan && msg.tests && msg.tests.label) {
      var tm = String(msg.tests.label).match(/(\d+)/);
      testsRan = tm ? parseInt(tm[1], 10) : 0;
    }
    var issues = typeof activity.issues === "number" ? activity.issues : 0;
    var parts = [];
    if (exploreCount > 0) parts.push('<span class="is-ok">✓ Explored ' + escapeHtml(String(exploreCount)) + ' file' + (exploreCount === 1 ? "" : "s") + '</span>');
    if (testsRan > 0) parts.push('<span>Ran ' + escapeHtml(String(testsRan)) + ' test' + (testsRan === 1 ? "" : "s") + '</span>');
    if (activity.explore || activity.steps || testsRan || exploreCount) {
      parts.push('<span>' + escapeHtml(String(issues)) + ' issue' + (issues === 1 ? "" : "s") + ' found</span>');
    }
    if (!parts.length && !(msg.elapsedMs > 0)) return "";
    var html = '<section class="climate-activity-complete">';
    if (msg.elapsedMs > 0) {
      html += '<button type="button" class="climate-chat-elapsed" data-activity-complete-toggle>Worked for ' + escapeHtml(formatElapsed(msg.elapsedMs)) + ' <span aria-hidden="true">▾</span></button>';
    }
    if (parts.length) {
      html += '<div class="climate-activity-complete-bar">';
      html += '<div class="climate-activity-complete-parts">' + parts.join('<span class="climate-activity-dot" aria-hidden="true">·</span>') + '</div>';
      html += '<div class="climate-activity-complete-actions">';
      if (msg.diagnostics) {
        html += '<button type="button" class="climate-btn climate-activity-btn" data-activity-details="' + escapeHtml(msg.id) + '">View Details</button>';
      }
      if (msg.proposal && msg.proposal.state === "pending") {
        html += '<button type="button" class="climate-btn climate-activity-btn is-accent" data-chat-action="review" data-msg-id="' + escapeHtml(msg.id) + '">Show Changes</button>';
      }
      html += '</div></div>';
    }
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
      var saved = JSON.parse(localStorage.getItem(chatStorageKey()) || "{}");
      state.chat.sessions = Array.isArray(saved.sessions) ? saved.sessions.slice(0, 40) : [];
      state.chat.sessions.forEach(function (session) { ensureUsage(session); });
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
    return {
      currentFile: !!document.getElementById("climate-current-file").checked,
      selection: !!document.getElementById("climate-selection").checked,
      selectedFiles: !!document.getElementById("climate-selected-files").checked,
      repoContext: !!document.getElementById("climate-repo-context").checked,
      selectedFilePaths: state.selectedFiles.slice(0, 24),
      activeFile: state.active || ""
    };
  }
  function applyContextPrefs(ctx) {
    if (!ctx) return;
    document.getElementById("climate-current-file").checked = ctx.currentFile !== false;
    document.getElementById("climate-selection").checked = ctx.selection !== false;
    document.getElementById("climate-selected-files").checked = !!ctx.selectedFiles;
    document.getElementById("climate-repo-context").checked = !!ctx.repoContext;
  }
  function newChatSession(silent) {
    var session = {
      id: uid("chat"),
      title: "New chat",
      createdAt: Date.now(),
      updatedAt: Date.now(),
      provider: providerSelect.value || "",
      model: modelSelect.value || "",
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
    if (!silent) setStatus("New chat");
    return session;
  }
  function titleFromPrompt(prompt) {
    var t = String(prompt || "").replace(/\s+/g, " ").trim();
    if (!t) return "New chat";
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
  }
  function renderHistoryList() {
    if (!historyList) return;
    if (!state.chat.sessions.length) {
      historyList.innerHTML = '<div class="climate-chat-history-item"><span>No saved chats yet</span></div>';
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
    saveChatStore();
    selectProvider(session.provider || providerSelect.value, { refresh: false, preserveModel: session.model });
    renderChat();
    renderUsageChrome(session);
    setStatus("Restored chat");
  }
  function renderChat() {
    var session = activeSession();
    if (chatTitleEl) {
      var title = (session && session.title) || "New chat";
      chatTitleEl.textContent = title;
      chatTitleEl.title = title;
    }
    renderUsageChrome(session);
    if (!session || !session.messages.length) {
      feed.innerHTML = '<div class="climate-ai-empty"><strong>CLIMATE coding chat</strong><span>Ask a follow-up about the active repository. Raw provider events stay in Details.</span></div>';
      proposalActions.hidden = true;
      return;
    }
    feed.innerHTML = session.messages.map(function (msg) { return renderChatMessage(msg); }).join("");
    feed.querySelectorAll("[data-open-file]").forEach(function (button) {
      button.addEventListener("click", function () { openFile(button.getAttribute("data-open-file")); });
    });
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
    var isRunning = !isUser && msg.status === "running";
    var isComplete = !isUser && msg.status === "completed";
    var name = isUser ? "You" : providerLabel(msg.provider || (activeSession() || {}).provider);
    var avatar = isUser ? "Y" : providerGlyph(msg.provider || (activeSession() || {}).provider);
    var bodyText = msg.text || "";
    if (isRunning && (!bodyText || bodyText === "Working…" || bodyText === "Working...")) bodyText = "";
    var body = escapeHtml(bodyText).replace(/\n/g, "<br>");
    var files = Array.isArray(msg.changedFiles) ? msg.changedFiles : [];
    var tests = testsFromText(msg.text, msg.tests);
    var lines = msg.lines || lineDeltaFromProposal(msg.proposal);
    var showRunCard = !isUser && msg.status === "completed" && (files.length || tests || (lines.plus || lines.minus) || (msg.summary && msg.summary.length));
    var html = '<article class="climate-chat-msg ' + (isUser ? "is-user" : "is-assistant") + (isError ? " is-error" : "") + (isRunning ? " is-stream" : "") + '" data-msg-id="' + escapeHtml(msg.id) + '">';
    html += '<div class="climate-chat-avatar" aria-hidden="true">' + escapeHtml(avatar) + '</div>';
    html += '<div class="climate-chat-meta"><strong>' + escapeHtml(name) + '</strong><time>' + escapeHtml(formatClock(msg.ts)) + '</time>';
    if (isComplete) html += '<span class="climate-chat-status-pill is-ok">✓ Completed</span>';
    html += '</div>';
    html += '<div class="climate-chat-body">';
    if (isRunning) {
      html += renderActivityProgress(msg);
    } else if (isComplete) {
      html += renderActivityComplete(msg);
    } else if (!isUser && msg.elapsedMs) {
      html += '<button type="button" class="climate-chat-elapsed">Worked for ' + escapeHtml(formatElapsed(msg.elapsedMs)) + ' ▾</button>';
    }
    if (body && !showRunCard) html += '<div class="climate-chat-text">' + body + '</div>';
    else if (body && (msg.text || "").length && !isRunning) html += '<div class="climate-chat-text">' + body + '</div>';
    if (showRunCard) {
      html += '<section class="climate-run-summary">';
      html += '<div class="climate-run-summary-head"><span class="climate-run-summary-title">▣ Run Summary</span><span class="climate-run-status is-ok">Completed ✓</span></div>';
      html += '<div class="climate-run-stats">';
      html += '<div><small>Worked for</small><b>' + escapeHtml(formatElapsed(msg.elapsedMs || 0)) + '</b></div>';
      html += '<div><small>Files changed</small><b>' + files.length + '</b></div>';
      html += '<div><small>Tests</small><b class="' + (tests && tests.passed ? "is-ok" : "") + '">' + escapeHtml(tests ? (tests.label || (tests.passed ? "passed" : "—")) : "—") + '</b></div>';
      html += '<div><small>Lines changed</small><b><span class="is-plus">+' + (lines.plus || 0) + '</span> <span class="is-minus">-' + (lines.minus || 0) + '</span></b></div>';
      html += '</div>';
      html += '<div class="climate-run-columns">';
      html += '<div><div class="climate-chat-files-title">Changed files (' + files.length + ')</div>';
      if (files.length) {
        html += files.map(function (path) {
          return '<button type="button" class="climate-chat-file" data-open-file="' + escapeHtml(path) + '">' + escapeHtml(path) + '</button>';
        }).join("");
      } else html += '<span class="climate-muted">No file edits</span>';
      html += '</div><div><div class="climate-chat-files-title">Tests</div>';
      if (tests) html += '<div class="climate-run-test-row"><span>' + escapeHtml(tests.suite || "tests") + '</span><b class="is-ok">' + escapeHtml(tests.label || "passed") + ' ›</b></div>';
      else html += '<span class="climate-muted">No test summary</span>';
      html += '</div></div>';
      if (msg.proposal && msg.proposal.state === "pending") {
        html += '<div class="climate-chat-msg-actions">';
        html += '<button type="button" class="climate-btn" data-chat-action="undo" data-msg-id="' + escapeHtml(msg.id) + '">Undo All</button>';
        html += '<button type="button" class="climate-btn" data-chat-action="keep" data-msg-id="' + escapeHtml(msg.id) + '">Keep All</button>';
        html += '<button type="button" class="climate-btn climate-btn-primary" data-chat-action="review" data-msg-id="' + escapeHtml(msg.id) + '">Review Changes</button>';
        html += '</div>';
      }
      html += '</section>';
    } else if (!isUser && !isComplete && msg.proposal && msg.proposal.state === "pending") {
      html += '<div class="climate-chat-msg-actions">';
      html += '<button type="button" class="climate-btn" data-chat-action="undo" data-msg-id="' + escapeHtml(msg.id) + '">Undo All</button>';
      html += '<button type="button" class="climate-btn" data-chat-action="keep" data-msg-id="' + escapeHtml(msg.id) + '">Keep All</button>';
      html += '<button type="button" class="climate-btn climate-btn-primary" data-chat-action="review" data-msg-id="' + escapeHtml(msg.id) + '">Review Changes</button>';
      html += '</div>';
    }
    if (!isUser && msg.diagnostics) {
      html += '<details class="climate-chat-details" id="climate-details-' + escapeHtml(msg.id) + '"><summary>Details / Diagnostics</summary><pre class="mono">' + escapeHtml(msg.diagnostics) + '</pre></details>';
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
  function renderProviders() {
    var providers = bootstrap.providers || []; var defaults = bootstrap.coding_defaults || {};
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
    var saved = providerSelect.dataset.saved;
    var preferred = saved || defaults.default_provider || "";
    if (preferred && providers.some(function (p) { return p.id === preferred; })) providerSelect.value = preferred;
    else {
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
    sendBtn.disabled = !modelSelect.value;
    enhanceProviderModelDropdowns();
  }
  function selectProvider(providerId, opts) {
    opts = opts || {};
    var refresh = !!opts.refresh;
    var p = (bootstrap.providers || []).find(function (row) { return row.id === providerId; });
    var defaults = bootstrap.coding_defaults || {};
    var defaultModels = defaults.default_models || {};
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
    providerState.title = statusText;
    if (providerDot) providerDot.className = "climate-chat-pill-dot " + (connected ? "is-ok" : "is-error");
    sendBtn.disabled = !connected;
    var session = activeSession();
    if (session) {
      session.provider = providerSelect.value;
      saveChatStore();
    }
    if (!connected) {
      var unavailable = '<option value="">' + escapeHtml((p && p.detail) || "Provider unavailable") + '</option>';
      modelSelect.innerHTML = unavailable;
      panelModelSelect.innerHTML = unavailable;
      enhanceProviderModelDropdowns();
      return;
    }
    var cached = state.modelCache[providerSelect.value];
    var preferred = opts.preserveModel || modelSelect.dataset.saved || defaultModels[providerSelect.value] || "";
    if (cached && !refresh) {
      applyModelOptions(providerSelect.value, cached.models, preferred || cached.recommended);
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
      applyModelOptions(providerSelect.value, data.models || [], preferred || data.recommended_model || "");
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
      if (!session.title || session.title === "New chat") session.title = titleFromPrompt(text);
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
  function sendRun() {
    var prompt = promptEl.value.trim();
    if (!prompt || !repoId() || !providerSelect.value || !modelSelect.value) return;
    captureActive();
    state.streamText = "";
    state.streamingMsgId = "";
    var session = activeSession() || newChatSession(true);
    var priorProvider = session.lastRunProvider || "";
    var crossProvider = !!(priorProvider && priorProvider !== providerSelect.value);
    var outboundPrompt = crossProvider ? compactHandoffPrompt(prompt, session) : prompt;
    session.messages.push({ id: uid("msg"), role: "user", text: prompt, ts: Date.now() });
    if (!session.title || session.title === "New chat") session.title = titleFromPrompt(prompt);
    session.provider = providerSelect.value;
    session.model = modelSelect.value;
    session.repositoryId = repoId();
    session.branch = (branchSelect && branchSelect.value) || "";
    session.context = captureContextPrefs();
    session.updatedAt = Date.now();
    saveChatStore();
    renderChat();
    promptEl.value = "";
    sendBtn.disabled = true;
    cancelBtn.hidden = false;
    document.getElementById("climate-cancel-top").disabled = false;
    proposalActions.hidden = true;
    setStatus(crossProvider ? "AI run started (handoff)" : "AI run started");
    upsertAssistantMessage({
      status: "running",
      text: "",
      provider: providerSelect.value,
      model: modelSelect.value,
      startedAt: Date.now()
    });
    var tab = currentTab(), files = document.getElementById("climate-selected-files").checked ? state.selectedFiles : [];
    jsonFetch(endpoint("/repositories/" + encodeURIComponent(repoId()) + "/runs"), {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        provider: providerSelect.value,
        model: modelSelect.value,
        prompt: outboundPrompt,
        current_file: document.getElementById("climate-current-file").checked && tab ? tab.path : "",
        selection: document.getElementById("climate-selection").checked ? currentSelection() : "",
        selected_files: files,
        include_repo_context: document.getElementById("climate-repo-context").checked,
        handoff: crossProvider,
        reuse_session: !crossProvider
      })
    }).then(function (data) {
      state.runId = data.run.id;
      state.run = data.run;
      pollRun();
    }).catch(function (error) {
      upsertAssistantMessage({ status: "failed", text: error.message, role: "error" });
      finishRun();
    });
  }
  function pollRun() {
    if (!state.runId) return;
    jsonFetch(endpoint("/runs/" + encodeURIComponent(state.runId))).then(function (data) {
      state.run = data.run;
      var parsed = splitRunOutput(data.run.logs, "");
      var session = activeSession();
      var streaming = session && session.messages.find(function (m) { return m.id === state.streamingMsgId; });
      var evidence = ((parsed.diagnostics || "") + "\n" + (data.run.logs || "") + "\n" + (parsed.text || "")).trim();
      var activity = parseActivityEvidence(evidence, {
        running: ["completed", "failed", "cancelled", "unavailable"].indexOf(data.run.status) < 0,
        startedAt: streaming && streaming.startedAt,
        hasAnswer: !!(parsed.text && parsed.text.length > 20),
        hasProposal: !!(data.run.proposal)
      });
      if (data.run.logs && data.run.logs !== state.streamText) {
        state.streamText = data.run.logs;
        upsertAssistantMessage({
          status: "running",
          text: parsed.text || "",
          diagnostics: parsed.diagnostics,
          activity: activity,
          provider: data.run.provider,
          model: data.run.model
        });
      } else if (streaming && streaming.status === "running") {
        // Refresh activity/elapsed without forcing scroll jump when logs unchanged.
        var prev = JSON.stringify(streaming.activity || {});
        var next = JSON.stringify(activity || {});
        if (prev !== next) {
          upsertAssistantMessage({
            status: "running",
            activity: activity,
            diagnostics: parsed.diagnostics || streaming.diagnostics || ""
          });
        } else if (streaming.activity && streaming.activity.explore) {
          streaming.activity.explore.elapsedMs = activity.explore.elapsedMs;
          var elapsedEl = feed.querySelector('[data-explore-panel="' + streaming.id + '"] .climate-activity-explore-stats b:last-child');
          // keep stored; full re-render only when evidence changes
        }
      }
      if (state.panel === "output") bottomBody.textContent = data.run.logs || data.run.answer || "Waiting for output…";
      if (["completed", "failed", "cancelled", "unavailable"].indexOf(data.run.status) < 0) {
        window.setTimeout(pollRun, 650);
        return;
      }
      var finalParsed = splitRunOutput(data.run.logs, data.run.answer);
      var proposal = data.run.proposal || null;
      var summary = extractSummary(finalParsed.text, proposal);
      var files = changedFilesFrom(proposal, finalParsed.text);
      var tests = testsFromText(finalParsed.text, null);
      var lines = lineDeltaFromProposal(proposal);
      var started = Number((streaming && streaming.startedAt) || data.run.created_at || Date.now());
      var finished = Number(data.run.finished_at || Date.now());
      if (String(started).length < 13) started = Date.now() - 1000;
      if (String(finished).length < 13) finished = Date.now();
      var elapsedMs = Math.max(0, finished - started);
      var usageParsed = parseUsagePayload(data.run.usage);
      var diagnostics = finalParsed.diagnostics || "";
      if (usageParsed.source !== "unavailable") {
        diagnostics = (diagnostics ? diagnostics + "\n\n" : "") + "usage_source=" + usageParsed.source + " total=" + usageParsed.total;
      } else {
        diagnostics = (diagnostics ? diagnostics + "\n\n" : "") + "usage_source=unavailable";
      }
      var finalActivity = parseActivityEvidence(((diagnostics || "") + "\n" + (data.run.logs || "") + "\n" + (finalParsed.text || "")).trim(), {
        running: false,
        startedAt: started,
        elapsedMs: elapsedMs,
        hasAnswer: !!(finalParsed.text),
        hasProposal: !!proposal,
        tests: tests,
        status: data.run.status
      });
      if (session) {
        session.lastRunProvider = data.run.provider || providerSelect.value;
        applyUsageFromRun(session, data.run.provider || providerSelect.value, data.run.usage);
      }
      upsertAssistantMessage({
        status: data.run.status,
        text: data.run.error ? data.run.error : (finalParsed.text || (proposal ? "Proposed changes are ready for review." : "Run finished.")),
        diagnostics: diagnostics,
        summary: summary,
        changedFiles: files,
        filesInspected: Math.max(files.length, (finalActivity.explore && finalActivity.explore.count) || 0),
        tests: tests,
        lines: lines,
        proposal: proposal,
        activity: finalActivity,
        elapsedMs: elapsedMs,
        runId: data.run.id,
        usage: usageParsed
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
      upsertAssistantMessage({ status: "failed", text: error.message });
      finishRun();
    });
  }
  function finishRun(){cancelBtn.hidden=true;document.getElementById("climate-cancel-top").disabled=true;sendBtn.disabled=!modelSelect.value;setStatus("Ready");state.streamingMsgId="";}
  function renderProposal(proposal){
    if(!proposal||proposal.state!=="pending"){proposalActions.hidden=true;return;}
    proposalActions.hidden=false;
    renderChat();
  }
  function renderProposalReview(proposal){
    state.panel="git";center.classList.remove("is-bottom-closed");document.querySelectorAll(".climate-bottom-tabs [data-panel]").forEach(function(btn){btn.classList.toggle("is-active",btn.dataset.panel==="git");});
    var edits=proposal.edits||[],active=edits[0]||{},sides=diffSides(active.diff||"");
    bottomBody.innerHTML='<div class="climate-git-workspace"><aside class="climate-git-changes"><div class="climate-git-title">Proposed changes <span class="count">'+edits.length+'</span></div>'+edits.map(function(edit,index){return '<button class="climate-git-file '+(index===0?'is-active':'')+'" data-proposal-index="'+index+'"><span>◆</span><span>'+escapeHtml(edit.path)+'</span><b>M</b></button>';}).join('')+'</aside><section class="climate-git-review"><div class="climate-git-review-head"><span>'+escapeHtml(active.path||'Proposed edit')+'</span><select disabled><option>Unified</option></select></div><div class="climate-diff-split"><div class="climate-diff-column is-before"><h4>Original</h4><pre>'+escapeHtml(sides.before||'No original content')+'</pre></div><div class="climate-diff-column is-after"><h4>Modified</h4><pre>'+escapeHtml(sides.after||'No modified content')+'</pre></div></div><div class="climate-git-actions"><button class="climate-btn" id="climate-reject-bottom">Undo All</button><button class="climate-btn climate-btn-primary" id="climate-accept-bottom">Keep All</button></div></section></div>';
    bottomBody.querySelectorAll("[data-proposal-index]").forEach(function(button){button.addEventListener("click",function(){var edit=edits[parseInt(button.getAttribute("data-proposal-index"),10)];var split=diffSides(edit.diff||"");bottomBody.querySelectorAll(".climate-git-file").forEach(function(row){row.classList.toggle("is-active",row===button);});bottomBody.querySelector(".climate-git-review-head span").textContent=edit.path;bottomBody.querySelector(".is-before pre").textContent=split.before;bottomBody.querySelector(".is-after pre").textContent=split.after;});});
    document.getElementById("climate-reject-bottom").addEventListener("click",function(){proposalAction("reject");});document.getElementById("climate-accept-bottom").addEventListener("click",function(){proposalAction("accept");});savePrefs();
  }
  function proposalAction(action, msgId){
    var runId = state.runId;
    var session = activeSession();
    var msg = session && session.messages.find(function (row) { return row.id === (msgId || state.streamingMsgId); });
    if (msg && msg.runId) runId = msg.runId;
    if(!runId)return;
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
  function switchPanel(panel) {
    state.panel = panel;
    center.classList.remove("is-bottom-closed");
    document.querySelectorAll(".climate-bottom-tabs [data-panel]").forEach(function (btn) {
      btn.classList.toggle("is-active", btn.dataset.panel === panel);
    });
    var termPanel = document.getElementById("climate-terminal-panel");
    var showTerm = panel === "terminal";
    bottomBody.hidden = showTerm;
    if (termPanel) termPanel.hidden = !showTerm;
    if (window.WCTerminal) {
      window.WCTerminal.setRenderingPaused(!showTerm);
      if (showTerm) {
        ensureClimateTerminal().then(function () {
          if (window.WCTerminal.scheduleFit) window.WCTerminal.scheduleFit();
        });
        savePrefs();
        return;
      }
    }
    if (showTerm) {
      savePrefs();
      return;
    }
    if (panel === "problems") bottomBody.textContent = "No problems detected by CLIMATE.";
    else if (panel === "tests") bottomBody.textContent = "No test run selected. CLIMATE v1 does not expose unrestricted shell execution.";
    else if (panel === "output") bottomBody.textContent = state.run ? (state.run.logs || state.run.answer || "Waiting for output…") : "No AI or repository output yet.";
    else if (state.git) renderGitWorkspace(state.git, state.gitPath);
    else loadGit();
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
          if (editor) editor.layout();
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
    if(typeof window.require!=="function"){fallback.style.display=state.active?"block":"none";return;}
    window.require.config({paths:{vs:"https://cdn.jsdelivr.net/npm/monaco-editor@0.52.2/min/vs"}});
    window.require(["vs/editor/editor.main"],function(){monacoReady=true;editor=window.monaco.editor.create(document.getElementById("climate-editor-host"),{theme:"vs-dark",automaticLayout:true,fontSize:12,fontFamily:"JetBrains Mono, Cascadia Code, Consolas, monospace",minimap:{enabled:true},scrollBeyondLastLine:false,wordWrap:"off",renderWhitespace:"selection",padding:{top:8},tabSize:4});editor.onDidChangeModelContent(markDirty);editor.onDidChangeCursorPosition(function(e){document.getElementById("climate-line").textContent=e.position.lineNumber;document.getElementById("climate-column").textContent=e.position.column;});editor.onDidChangeCursorSelection(function(e){var count=Math.abs(e.selection.endLineNumber-e.selection.startLineNumber)+1;document.getElementById("climate-selection-meta").textContent=e.selection.isEmpty()?"No selection":count+" line"+(count===1?"":"s");});fallback.style.display="none";if(state.active)activateTab(state.active);});
  }
  repoSelect.addEventListener("change",function(){captureActive();savePrefs();window.location.assign((workspace==="work"?"/work/climate":"/personal/climate")+"?repo="+encodeURIComponent(repoId()));});
  providerSelect.addEventListener("change",function(){if(panelProviderSelect)panelProviderSelect.value=providerSelect.value;selectProvider(providerSelect.value,{refresh:false});});
  if(panelProviderSelect){panelProviderSelect.addEventListener("change",function(){providerSelect.value=panelProviderSelect.value;selectProvider(providerSelect.value,{refresh:false});});}
  modelSelect.addEventListener("change",function(){panelModelSelect.value=modelSelect.value;sendBtn.disabled=!modelSelect.value;var session=activeSession();if(session){session.model=modelSelect.value;saveChatStore();}savePrefs();});
  panelModelSelect.addEventListener("change",function(){modelSelect.value=panelModelSelect.value;sendBtn.disabled=!modelSelect.value;var session=activeSession();if(session){session.model=modelSelect.value;saveChatStore();}savePrefs();});
  document.getElementById("climate-model-refresh").addEventListener("click",function(){selectProvider(providerSelect.value,{refresh:true});});
  document.getElementById("climate-chat-new").addEventListener("click",function(){closeChatPopovers();newChatSession(false);promptEl.focus();});
  function renameActiveChat(){
    var session=activeSession();
    if(!session)return;
    var next=window.prompt("Rename chat", session.title||"New chat");
    if(next&&next.trim()){session.title=next.trim();session.updatedAt=Date.now();saveChatStore();renderChat();}
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
      if(open)renderUsageChrome(activeSession());
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
      else if(action==="clear"&&session){session.messages=[];session.updatedAt=Date.now();saveChatStore();renderChat();}
      menuPanel.hidden=true;
    });
  });
  document.getElementById("climate-ctx-mention").addEventListener("click",function(){historyPanel.hidden=true;menuPanel.hidden=true;contextPanel.hidden=!contextPanel.hidden;});
  document.getElementById("climate-ctx-files").addEventListener("click",function(){document.getElementById("climate-selected-files").checked=true;contextPanel.hidden=false;});
  document.getElementById("climate-ctx-attach").addEventListener("click",function(){contextPanel.hidden=!contextPanel.hidden;});
  document.getElementById("climate-review").addEventListener("click",function(){
    var session=activeSession();
    var pending=session&&session.messages.slice().reverse().find(function(msg){return msg.proposal&&msg.proposal.state==="pending";});
    if(pending&&pending.proposal)renderProposalReview(pending.proposal);
    else if(state.run&&state.run.proposal)renderProposalReview(state.run.proposal);
  });
  document.addEventListener("click",function(event){
    var ai=document.getElementById("climate-ai");
    if(!event.target.closest(".climate-dd") && !event.target.closest(".climate-dd-menu")) closeClimateDropdowns();
    if(!ai||ai.contains(event.target)||event.target.closest(".climate-dd-menu"))return;
    closeChatPopovers();
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
  document.getElementById("climate-send").addEventListener("click",sendRun);promptEl.addEventListener("keydown",function(e){if((e.ctrlKey||e.metaKey)&&e.key==="Enter")sendRun();});cancelBtn.addEventListener("click",function(){if(!state.runId)return;jsonFetch(endpoint("/runs/"+encodeURIComponent(state.runId)+"/cancel"),{method:"POST"}).then(pollRun).catch(function(e){appendFeed(e.message,"is-error");});});
  document.getElementById("climate-send-top").addEventListener("click",sendRun);document.getElementById("climate-cancel-top").disabled=true;document.getElementById("climate-cancel-top").addEventListener("click",function(){cancelBtn.click();});
  document.getElementById("climate-accept").addEventListener("click",function(){proposalAction("accept");});document.getElementById("climate-reject").addEventListener("click",function(){proposalAction("reject");});
  document.getElementById("climate-confirm-save").addEventListener("click",function(e){e.preventDefault();document.getElementById("climate-save-dialog").close();confirmSave();});
  document.getElementById("climate-save").addEventListener("click",saveFile);
  document.addEventListener("keydown",function(e){var key=e.key.toLowerCase();if((e.ctrlKey||e.metaKey)&&key==="s"){e.preventDefault();saveFile();}else if((e.ctrlKey||e.metaKey)&&key==="b"){e.preventDefault();document.getElementById("climate-toggle-left").click();}else if((e.ctrlKey||e.metaKey)&&key==="j"){e.preventDefault();document.getElementById("climate-toggle-bottom").click();}else if((e.ctrlKey||e.metaKey)&&key==="p"){e.preventDefault();workbench.classList.remove("is-left-closed");document.getElementById("climate-search").focus();}else if((e.ctrlKey||e.metaKey)&&e.shiftKey&&key==="a"){e.preventDefault();workbench.classList.remove("is-ai-closed");promptEl.focus();}});fallback.addEventListener("input",markDirty);fallback.addEventListener("select",function(){var text=currentSelection();document.getElementById("climate-selection-meta").textContent=text?text.split("\n").length+" line(s)":"No selection";});
  document.querySelectorAll(".climate-bottom-tabs [data-panel]").forEach(function(btn){btn.addEventListener("click",function(){switchPanel(btn.dataset.panel);});});document.getElementById("climate-bottom-close").addEventListener("click",function(){center.classList.add("is-bottom-closed");if(editor)editor.layout();savePrefs();});
  document.getElementById("climate-toggle-ai").addEventListener("click",function(){
    if(workbench.classList.contains("is-ai-closed")){
      workbench.classList.remove("is-ai-closed");
      if(workbench.classList.contains("is-ai-collapsed")) expandAiPanel();
      else if(currentAiWidthPx() < AI_MIN) setAiExpandedWidth(AI_DEFAULT);
    } else {
      workbench.classList.add("is-ai-closed");
      workbench.classList.remove("is-ai-maximized");
      workbench.classList.remove("is-ai-collapsed");
    }
    syncAiMaximizeChrome();
    if(editor)editor.layout();
    savePrefs();
  });
  document.getElementById("climate-toggle-bottom").addEventListener("click",function(){center.classList.toggle("is-bottom-closed");if(editor)editor.layout();savePrefs();});
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
        if(editor)editor.layout();
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
    if(editor)editor.layout();
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
  document.getElementById("climate-toggle-left").addEventListener("click",function(){workbench.classList.toggle("is-left-closed");if(editor)editor.layout();savePrefs();});
  document.querySelectorAll("[data-activity]").forEach(function(button){button.addEventListener("click",function(){var activity=button.dataset.activity;if(activity==="explorer"){workbench.classList.remove("is-left-closed");}else if(activity==="search"){workbench.classList.remove("is-left-closed");document.getElementById("climate-search").focus();}else if(activity==="git"||activity==="tests"){switchPanel(activity);}else if(activity==="ai"){workbench.classList.remove("is-ai-closed");if(workbench.classList.contains("is-ai-collapsed"))expandAiPanel();promptEl.focus();}else{setStatus("Workspace settings remain in CLIMATE Settings.");}document.querySelectorAll("[data-activity]").forEach(function(item){item.classList.toggle("is-active",item===button);});if(editor)editor.layout();savePrefs();});});
  window.addEventListener("beforeunload",function(e){captureActive();if(state.tabs.some(function(tab){return tab.dirty;})){e.preventDefault();e.returnValue="";}});
  window.addEventListener("resize", function () {
    if (workbench.classList.contains("is-ai-maximized") && !workbench.classList.contains("is-ai-collapsed") && !workbench.classList.contains("is-ai-closed")) {
      syncAiMaximizeChrome();
      if (editor) editor.layout();
    }
  });
  loadPrefs();normalizeAiPanelState();syncAiMaximizeChrome();renderTabs();renderProviders();enhanceProviderModelDropdowns();loadChatStore();loadTree();loadGit();setupResize();initMonaco();if(state.active)openFile(state.active);
}());
