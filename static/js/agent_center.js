(function () {
  "use strict";

  var bootstrapEl = document.getElementById("agent-bootstrap");
  var bootstrap = {};
  try {
    bootstrap = JSON.parse(bootstrapEl ? bootstrapEl.textContent || "{}" : "{}");
  } catch (e) {
    bootstrap = {};
  }

  var agents = bootstrap.agents || [];
  var profileId = (bootstrap.profile && bootstrap.profile.id) || "okarun";
  var apiBase = "/api/assistants/" + encodeURIComponent(profileId);
  var select = document.getElementById("agent-select");
  var modelSelect = document.getElementById("agent-model");
  var modelMeta = document.getElementById("agent-model-meta");
  var effortWrap = document.getElementById("agent-effort-wrap");
  var effortSelect = document.getElementById("agent-effort");
  var detail = document.getElementById("agent-detail");
  var lastModelPayload = null;
  var userModelOverride = false;
  var promptEl = document.getElementById("agent-prompt");
  var statusEl = document.getElementById("agent-status");
  var answerEl = document.getElementById("agent-answer");
  var logsEl = document.getElementById("agent-logs");
  var errorEl = document.getElementById("agent-error");
  var refsEl = document.getElementById("agent-refs");
  var toolsEl = document.getElementById("agent-tools");
  var usageEl = document.getElementById("agent-usage");
  var metaEl = document.getElementById("agent-run-meta");
  var previewBody = document.getElementById("agent-preview-body");
  var cancelBtn = document.getElementById("agent-cancel");
  var retryBtn = document.getElementById("agent-retry");
  var runBtn = document.getElementById("agent-run");
  var pollTimer = null;
  var activeRunId = null;

  function selectedMode() {
    var el = document.querySelector('input[name="agent-mode"]:checked');
    return el ? el.value : "ask";
  }

  function selectedRepos() {
    return Array.prototype.slice
      .call(document.querySelectorAll('input[name="repo"]:checked'))
      .map(function (el) {
        return el.value;
      });
  }

  function selectedTools() {
    return Array.prototype.slice
      .call(document.querySelectorAll('input[name="agent-tool"]:checked'))
      .map(function (el) {
        return el.value;
      });
  }

  function currentAgent() {
    var id = select ? select.value : "";
    for (var i = 0; i < agents.length; i++) {
      if (agents[i].id === id) return agents[i];
    }
    return null;
  }

  function fillModelsFromPayload(data, preferred) {
    if (!modelSelect) return;
    modelSelect.innerHTML = "";
    lastModelPayload = data || null;
    var groups = (data && data.groups) || {};
    var groupOrder = ["Recommended", "Advanced", "Balanced", "Fast", "Pro"];
    var added = {};
    var count = 0;

    function addOption(m, optgroup) {
      if (!m || !m.id || added[m.id]) return;
      added[m.id] = m;
      var opt = document.createElement("option");
      opt.value = m.id;
      opt.textContent =
        (m.display_name || m.id) +
        (m.tier ? " · " + m.tier : "") +
        (m.is_pro ? " · Pro" : "");
      optgroup.appendChild(opt);
      count += 1;
    }

    if (groups && Object.keys(groups).length) {
      groupOrder.forEach(function (name) {
        var rows = groups[name] || [];
        if (!rows.length) return;
        var og = document.createElement("optgroup");
        og.label = name;
        rows.forEach(function (m) {
          addOption(m, og);
        });
        if (og.children.length) modelSelect.appendChild(og);
      });
    } else {
      var models = (data && data.models) || [];
      models.forEach(function (id) {
        var opt = document.createElement("option");
        opt.value = id;
        opt.textContent = id;
        modelSelect.appendChild(opt);
        added[id] = { id: id, display_name: id };
        count += 1;
      });
    }

    if (!count) {
      var empty = document.createElement("option");
      empty.value = "";
      empty.textContent = "No accessible models";
      modelSelect.appendChild(empty);
    }

    var pick =
      preferred ||
      (data && data.recommended_model) ||
      (data && data.default_model) ||
      "";
    if (pick && added[pick]) {
      modelSelect.value = pick;
    }
    updateModelMeta();
  }

  function updateModelMeta() {
    var id = modelSelect ? modelSelect.value : "";
    var detailRow = null;
    if (lastModelPayload && lastModelPayload.model_details) {
      for (var i = 0; i < lastModelPayload.model_details.length; i++) {
        if (lastModelPayload.model_details[i].id === id) {
          detailRow = lastModelPayload.model_details[i];
          break;
        }
      }
    }
    if (modelMeta) {
      if (!detailRow) {
        modelMeta.textContent = "";
      } else {
        modelMeta.textContent =
          (detailRow.description || "") +
          (detailRow.recommended_uses && detailRow.recommended_uses.length
            ? " · Uses: " + detailRow.recommended_uses.join(", ")
            : "");
      }
    }
    var supports = !!(detailRow && detailRow.supports_reasoning_effort);
    if (effortWrap) effortWrap.hidden = !supports;
    if (!supports && effortSelect) effortSelect.value = "medium";
  }

  function fillModels(agent, preferred) {
    fillModelsFromPayload(
      { models: (agent && agent.models) || [], groups: {}, model_details: [] },
      preferred
    );
  }

  function setStatus(text) {
    if (statusEl) {
      statusEl.textContent = text;
      statusEl.className = "status-pill status-" + String(text || "idle").replace(/\s+/g, "-");
    }
  }

  function selectFirstRunnableAgent() {
    if (!select) return;
    var options = Array.prototype.slice.call(select.options || []);
    var runnable = options.filter(function (o) {
      return o.getAttribute("data-runnable") === "1" && !o.disabled;
    });
    var warn = document.getElementById("agent-none-warn");
    if (!runnable.length) {
      if (warn) warn.hidden = false;
      if (runBtn) runBtn.disabled = true;
      return;
    }
    if (warn) warn.hidden = true;
    var current = select.options[select.selectedIndex];
    if (!current || current.disabled || current.getAttribute("data-runnable") !== "1") {
      select.value = runnable[0].value;
    }
  }

  function refreshAgentDetail() {
    var agent = currentAgent();
    if (detail) detail.textContent = agent ? agent.detail : "";
    if (runBtn) runBtn.disabled = !(agent && agent.runnable);
    if (!agent) {
      fillModels(null);
      return;
    }
    var mode = selectedMode();
    fetch(
      apiBase + "/agents/" +
        encodeURIComponent(agent.id) +
        "/models?mode=" +
        encodeURIComponent(mode)
    )
      .then(function (r) {
        return r.json();
      })
      .then(function (data) {
        agent.models = data.models || agent.models || [];
        agent.models_source = data.models_source || agent.models_source;
        agent.runnable = !!data.runnable;
        var preferred = userModelOverride
          ? modelSelect && modelSelect.value
          : data.recommended_model || data.default_model || "";
        fillModelsFromPayload(data, preferred);
        if (runBtn) runBtn.disabled = !agent.runnable;
        if (detail) {
          detail.textContent =
            (agent.detail || "") +
            (data.models_source ? " · models=" + data.models_source : "") +
            (data.recommended_model
              ? " · recommended=" + data.recommended_model
              : "");
        }
        if (data.error && errorEl) errorEl.textContent = data.error;
      })
      .catch(function () {
        fillModels(agent);
      });
  }

  function renderRefs(files) {
    if (!refsEl) return;
    refsEl.innerHTML = "";
    if (!files || !files.length) {
      var empty = document.createElement("li");
      empty.className = "muted";
      empty.textContent = "—";
      refsEl.appendChild(empty);
      return;
    }
    files.forEach(function (f) {
      var li = document.createElement("li");
      li.textContent = (f.repo_id || "") + ":" + (f.path || "");
      refsEl.appendChild(li);
    });
  }

  function payloadBase() {
    var payload = {
      mode: selectedMode(),
      repository_ids: selectedRepos(),
      tool_ids: selectedTools(),
      profile_id: profileId,
      agent_id: select ? select.value : "",
      model: modelSelect ? modelSelect.value : "",
      prompt: promptEl ? promptEl.value : "",
    };
    if (effortWrap && !effortWrap.hidden && effortSelect) {
      payload.reasoning_effort = effortSelect.value;
    }
    return payload;
  }

  function showRun(run) {
    if (!run) return;
    activeRunId = run.id;
    setStatus(run.status || "unknown");
    if (metaEl) {
      metaEl.textContent =
        (run.agent_label || run.agent_id || "") +
        " · " +
        (run.model || "default") +
        " · " +
        (run.id || "");
    }
    if (answerEl) answerEl.textContent = run.answer || "—";
    if (logsEl) logsEl.textContent = run.logs || "—";
    if (errorEl) errorEl.textContent = run.error || "—";
    if (toolsEl) {
      var activity = run.tool_activity || [];
      toolsEl.textContent = activity.length
        ? JSON.stringify(activity, null, 2)
        : "—";
    }
    if (usageEl) {
      var usage = run.usage || {};
      usageEl.textContent = Object.keys(usage).length
        ? JSON.stringify(usage, null, 2)
        : "—";
    }
    renderRefs(run.referenced_files || (run.context && run.context.files) || []);
    var terminal = ["completed", "failed", "cancelled", "unavailable"].indexOf(run.status) >= 0;
    if (cancelBtn) cancelBtn.disabled = !!terminal;
    if (retryBtn) retryBtn.disabled = !terminal || run.status === "unavailable";
    if (!terminal) startPoll(run.id);
    else stopPoll();
  }

  function startPoll(runId) {
    stopPoll();
    pollTimer = setInterval(function () {
      fetch(apiBase + "/runs/" + encodeURIComponent(runId))
        .then(function (r) {
          return r.json();
        })
        .then(function (data) {
          if (data.run) showRun(data.run);
        })
        .catch(function () {});
    }, 1000);
  }

  function stopPoll() {
    if (pollTimer) {
      clearInterval(pollTimer);
      pollTimer = null;
    }
  }

  if (select) {
    selectFirstRunnableAgent();
    select.addEventListener("change", function () {
      userModelOverride = false;
      refreshAgentDetail();
    });
    refreshAgentDetail();
  }

  document.querySelectorAll('input[name="agent-mode"]').forEach(function (radio) {
    radio.addEventListener("change", function () {
      userModelOverride = false;
      refreshAgentDetail();
    });
  });

  if (modelSelect) {
    modelSelect.addEventListener("change", function () {
      userModelOverride = true;
      updateModelMeta();
    });
  }

  var previewBtn = document.getElementById("agent-preview");
  if (previewBtn) {
    previewBtn.addEventListener("click", function () {
      var payload = payloadBase();
      fetch(apiBase + "/context/preview", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload),
      })
        .then(function (r) {
          return r.json();
        })
        .then(function (data) {
          previewBody.textContent = JSON.stringify(data, null, 2);
          var box = document.getElementById("agent-preview-box");
          if (box) box.open = true;
        })
        .catch(function (err) {
          previewBody.textContent = String(err);
        });
    });
  }

  if (runBtn) {
    runBtn.addEventListener("click", function () {
      var payload = payloadBase();
      if (!payload.agent_id) {
        setStatus("error");
        errorEl.textContent = "Select a runnable agent (Hub Simulator works without external CLIs).";
        return;
      }
      setStatus("submitting");
      errorEl.textContent = "—";
      fetch(apiBase + "/runs", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload),
      })
        .then(function (r) {
          return r.json().then(function (data) {
            return { ok: r.ok, status: r.status, data: data };
          });
        })
        .then(function (res) {
          if (!res.ok) {
            setStatus("error");
            errorEl.textContent =
              (res.data && res.data.error) ||
              "Run failed (HTTP " + res.status + ")";
            return;
          }
          showRun(res.data.run);
        })
        .catch(function (err) {
          setStatus("error");
          errorEl.textContent = String(err);
        });
    });
  }

  if (cancelBtn) {
    cancelBtn.addEventListener("click", function () {
      if (!activeRunId) return;
      fetch(apiBase + "/runs/" + encodeURIComponent(activeRunId) + "/cancel", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
      })
        .then(function (r) {
          return r.json();
        })
        .then(function (data) {
          if (data.run) showRun(data.run);
        });
    });
  }

  if (retryBtn) {
    retryBtn.addEventListener("click", function () {
      if (!activeRunId) return;
      retryBtn.disabled = true;
      setStatus("retrying");
      fetch(apiBase + "/runs/" + encodeURIComponent(activeRunId) + "/retry", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
      })
        .then(function (r) { return r.json(); })
        .then(function (data) {
          if (data.run) showRun(data.run);
          else {
            setStatus("error");
            errorEl.textContent = data.error || "Retry failed.";
          }
        });
    });
  }

  var saveBtn = document.getElementById("agent-prompt-save");
  if (saveBtn) {
    saveBtn.addEventListener("click", function () {
      var title = window.prompt("Prompt title", "Saved prompt");
      if (!title) return;
      fetch(apiBase + "/prompts", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          title: title,
          body: promptEl ? promptEl.value : "",
          mode: selectedMode(),
        }),
      }).then(function () {
        window.location.reload();
      });
    });
  }

  document.querySelectorAll(".agent-prompt-item").forEach(function (btn) {
    btn.addEventListener("click", function () {
      if (promptEl) promptEl.value = btn.getAttribute("data-body") || "";
      var mode = btn.getAttribute("data-mode");
      var radio = document.querySelector('input[name="agent-mode"][value="' + mode + '"]');
      if (radio) radio.checked = true;
    });
  });

  document.querySelectorAll(".agent-history-item").forEach(function (btn) {
    btn.addEventListener("click", function () {
      var id = btn.getAttribute("data-id");
      if (!id) return;
      fetch(apiBase + "/runs/" + encodeURIComponent(id))
        .then(function (r) {
          return r.json();
        })
        .then(function (data) {
          if (data.run) {
            if (promptEl) promptEl.value = data.run.prompt || "";
            showRun(data.run);
          }
        });
    });
  });
})();
