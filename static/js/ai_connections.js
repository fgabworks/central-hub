(function () {
  "use strict";

  var fetchFn = (window.HubPerf && window.HubPerf.dedupeFetch) || fetch;

  function ynInstalled(value) {
    return value ? "Installed" : "Not Installed";
  }

  function ynConnected(connection) {
    return connection.authenticated && connection.state === "connected"
      ? "Connected"
      : "Not Connected";
  }

  function isCodingPage() {
    var root = document.getElementById("ai-connections");
    return !!(root && root.getAttribute("data-coding") === "1");
  }

  function formatChecked(value) {
    if (!value) return "Never";
    var t = Date.parse(value);
    if (isNaN(t)) return value;
    var seconds = Math.round((Date.now() - t) / 1000);
    if (seconds < 45) return "Just now";
    if (seconds < 3600) return Math.max(1, Math.round(seconds / 60)) + "m ago";
    if (seconds < 86400) return Math.max(1, Math.round(seconds / 3600)) + "h ago";
    return Math.max(1, Math.round(seconds / 86400)) + "d ago";
  }

  function applyChecked(el, value) {
    if (!el) return;
    var stamp = value || el.getAttribute("data-timestamp") || "";
    if (value) el.setAttribute("data-timestamp", value);
    el.textContent = formatChecked(stamp);
  }

  function modelCountLabel(models) {
    return models && models.length ? models.length + " available" : "—";
  }

  function fillModelSelect(selectEl, models, current) {
    if (!selectEl) return;
    var seen = {};
    var html = '<option value="">Auto (use provider default)</option>';
    (models || []).forEach(function (model) {
      var id = String(model);
      seen[id] = true;
      html +=
        '<option value="' +
        id.replace(/"/g, "&quot;") +
        '"' +
        (current === id ? " selected" : "") +
        ">" +
        id.replace(/</g, "&lt;") +
        "</option>";
    });
    if (current && !seen[current]) {
      html +=
        '<option value="' +
        String(current).replace(/"/g, "&quot;") +
        '" selected>' +
        String(current).replace(/</g, "&lt;") +
        "</option>";
    }
    selectEl.innerHTML = html;
    if (current && (seen[current] || current)) selectEl.value = current;
    else selectEl.value = "";
    if (window.ClimateSelect) window.ClimateSelect.sync(selectEl);
  }

  function codingActionsHtml(connection) {
    var installed = !!connection.installed;
    var connected = connection.state === "connected";
    var html = "";
    if (!installed) {
      html +=
        '<button type="button" class="btn btn-sm btn-primary" data-action="install_help">Install help</button>';
      html +=
        '<button type="button" class="btn btn-sm" data-action="refresh-status">Refresh Status</button>';
      return html;
    }
    html +=
      '<button type="button" class="btn btn-sm" data-action="test"><span aria-hidden="true">✓</span> Test Connection</button>';
    if (!connected) {
      html +=
        '<button type="button" class="btn btn-sm btn-primary" data-action="connect">Connect</button>';
    }
    html += '<details class="aic-overflow"><summary class="btn btn-sm aic-overflow-btn" aria-label="More actions">⋯</summary><div class="aic-overflow-menu">';
    html +=
      '<button type="button" class="aic-overflow-item" data-action="refresh-status">Refresh Status</button>';
    if (connected) {
      html +=
        '<button type="button" class="aic-overflow-item" data-action="refresh-models">Refresh Models</button>';
      html +=
        '<button type="button" class="aic-overflow-item" data-action="reauthenticate">Re-authenticate</button>';
    }
    html +=
      '<button type="button" class="aic-overflow-item" data-action="disconnect">Disconnect</button>';
    html += "</div></details>";
    return html;
  }

  function syncActions(row, connection) {
    var actions =
      row.querySelector(".connection-actions") ||
      row.querySelector(".ai-provider-actions");
    if (!actions) return;
    var installed = !!connection.installed;
    var connected = connection.state === "connected";
    var id = connection.id || row.getAttribute("data-provider-id") || "";
    var compact = !!row.closest("#ai-provider-compact");
    var codingPage = isCodingPage() && !!row.closest("#ai-connections");
    if (codingPage) {
      actions.innerHTML = codingActionsHtml(connection);
      return;
    }
    var sm = compact ? " btn-sm" : "";
    var html = "";
    if (!installed) {
      html +=
        '<button type="button" class="btn btn-primary' +
        sm +
        '" data-action="install_help">Install help</button>';
      html +=
        '<button type="button" class="btn' +
        sm +
        '" data-action="refresh-status">Refresh Status</button>';
    } else if (connected) {
      html +=
        '<button type="button" class="btn' +
        sm +
        '" data-action="refresh-status">Refresh Status</button>';
      if (!compact) {
        html +=
          '<button type="button" class="btn" data-action="refresh-models">Refresh Models</button>';
      }
      html +=
        '<button type="button" class="btn btn-primary' +
        sm +
        '" data-action="test">' +
        (compact ? "Test" : "Test Connection") +
        "</button>";
      html +=
        '<button type="button" class="btn' +
        sm +
        '" data-action="reauthenticate">Re-authenticate</button>';
      html +=
        '<button type="button" class="btn' +
        sm +
        '" data-action="disconnect">' +
        (compact ? "Disconnect" : "Sign out") +
        "</button>";
    } else {
      html +=
        '<button type="button" class="btn btn-primary' +
        sm +
        '" data-action="connect">Connect</button>';
      html +=
        '<button type="button" class="btn' +
        sm +
        '" data-action="refresh-status">Refresh Status</button>';
      if (!compact) {
        html +=
          '<button type="button" class="btn" data-action="test">Test Connection</button>';
      }
      html +=
        '<button type="button" class="btn' +
        sm +
        '" data-action="disconnect">' +
        (compact ? "Disconnect" : "Sign out") +
        "</button>";
    }
    if (id !== "codex" && installed && !compact) {
      html +=
        '<button type="button" class="btn" data-action="refresh-models">Refresh Models</button>';
    }
    actions.innerHTML = html;
  }

  function renderModels(row, connection) {
    var list = row.querySelector(".connection-models-list");
    if (list) {
      var models = connection.models || [];
      if (models.length) {
        list.textContent = models.join(", ");
      } else if (connection.models_error) {
        list.textContent = connection.models_error;
      } else if (connection.state === "connected") {
        list.textContent = "No models reported — try Refresh Models";
      } else {
        list.textContent = "Connect, then Refresh Status / Refresh Models";
      }
    }
    var count = row.querySelector(".connection-model-count");
    if (count) count.textContent = modelCountLabel(connection.models);
    var id = connection.id || row.getAttribute("data-provider-id") || "";
    var datalist = document.getElementById("models-" + id);
    if (datalist) {
      datalist.innerHTML = (connection.models || [])
        .map(function (m) {
          return "<option value=\"" + String(m).replace(/"/g, "&quot;") + "\"></option>";
        })
        .join("");
    }
    var wrap = document.querySelector('[data-default-model-for="' + id + '"]');
    var selectEl = wrap && wrap.querySelector("select");
    var input = wrap && wrap.querySelector("input");
    if (selectEl) fillModelSelect(selectEl, connection.models || [], input ? input.value.trim() : "");
  }

  function render(row, connection, result) {
    var status = row.querySelector(".connection-status");
    var detail =
      row.querySelector(".connection-detail") ||
      document.querySelector(
        '[data-provider-detail="' +
          (connection.id || row.getAttribute("data-provider-id") || "") +
          '"] .connection-detail'
      );
    var account = row.querySelector(".connection-account");
    var last = row.querySelector(".connection-last-check");
    var installed = row.querySelector(".connection-installed");
    var authenticated = row.querySelector(".connection-authenticated");
    var available = row.querySelector(".connection-available");
    var version = row.querySelector(".connection-version");
    var cli = row.querySelector(".connection-cli");
    var executable = row.querySelector(".connection-executable");
    var compact = !!row.closest("#ai-provider-compact");
    if (status) {
      status.textContent = connection.summary_label || connection.status || "Error";
      var stateClass = String(connection.state || "error").replace(/_/g, "-");
      if (!connection.installed && stateClass !== "error") stateClass = "unavailable";
      status.className = "status-pill connection-status status-" + stateClass;
    }
    if (detail) {
      var text = (result && result.detail) || connection.detail || "";
      if (connection.install_help && !connection.installed) {
        text = text + (text ? " — " : "") + connection.install_help;
      }
      detail.textContent = text;
    }
    if (account) account.textContent = connection.account_label || "—";
    applyChecked(last, connection.last_check || connection.last_successful_check || "");
    if (installed)
      installed.textContent = compact
        ? connection.installed
          ? "Installed"
          : "Missing"
        : ynInstalled(connection.installed);
    if (authenticated) authenticated.textContent = ynConnected(connection);
    if (available) available.textContent = connection.available ? "Yes" : "No";
    if (version) version.textContent = connection.version || "—";
    if (cli)
      cli.textContent = (connection.cli_commands || []).join(", ") || "—";
    if (executable)
      executable.textContent = connection.executable_path || "—";
    var runtime = row.querySelector(".connection-runtime-health");
    if (runtime) {
      var health = connection.runtime_health || "—";
      if (connection.discovery_source) health = health + " · " + connection.discovery_source;
      runtime.textContent = health;
    }
    if (detail && connection.executable_path && String(detail.textContent || "").indexOf(connection.executable_path) < 0) {
      detail.textContent = (detail.textContent ? detail.textContent + " · " : "") + connection.executable_path;
    }
    if (detail && connection.runtime_health && String(detail.textContent || "").indexOf("runtime ") < 0) {
      detail.textContent = (detail.textContent ? detail.textContent + " · " : "") + "runtime " + connection.runtime_health;
    }
    renderModels(row, connection);
    syncActions(row, connection);
  }

  function showInstallHelp(row, connection) {
    var provider = row.getAttribute("data-provider-id");
    var detail =
      row.querySelector(".connection-detail") ||
      document.querySelector(
        '[data-provider-detail="' + provider + '"] .connection-detail'
      );
    var help =
      (connection && connection.install_help) ||
      "Install the official provider CLI and ensure it is on PATH, then refresh.";
    if (detail) detail.textContent = help;
    window.alert(help);
  }

  function updateStatusSummary(connections) {
    var root = document.getElementById("aic-summary");
    if (!root || !connections) return;
    var connected = 0;
    var notConnected = 0;
    var unavailable = 0;
    var error = 0;
    var last = "";
    connections.forEach(function (item) {
      if (item.state === "connected") connected += 1;
      else if (item.state === "error") error += 1;
      else if (item.state === "unavailable") unavailable += 1;
      else notConnected += 1;
      if (item.last_check && item.last_check > last) last = item.last_check;
    });
    function chip(cls, label) {
      return (
        '<span class="aic-summary-chip ' +
        cls +
        '"><i aria-hidden="true"></i>' +
        label +
        "</span>"
      );
    }
    var html = chip("is-connected", connected + " Connected");
    if (notConnected) html += chip("is-idle", notConnected + " Not connected");
    if (unavailable) html += chip("is-offline", unavailable + " Unavailable");
    if (error) html += chip("is-error", error + " Error");
    html +=
      '<span class="aic-summary-checked">Last checked <time class="aic-last-global" datetime="' +
      String(last).replace(/"/g, "&quot;") +
      '">' +
      formatChecked(last) +
      "</time></span>";
    html +=
      '<button type="button" class="aic-icon-btn" id="aic-refresh-all" title="Refresh provider status" aria-label="Refresh provider status">↻</button>';
    root.innerHTML = html;
    bindRefreshAll();
  }

  function runAction(row, action) {
    var provider = row.getAttribute("data-provider-id");
    if (action === "install_help") {
      fetchFn("/api/ai-connections?refresh=1&coding=1", { credentials: "same-origin" })
        .then(function (response) {
          return response.json();
        })
        .then(function (data) {
          var match = (data.connections || []).find(function (c) {
            return c.id === provider;
          });
          showInstallHelp(row, match || {});
        })
        .catch(function () {
          showInstallHelp(row, {});
        });
      return;
    }
    return fetch(
      "/api/ai-connections/" +
        encodeURIComponent(provider) +
        "/" +
        encodeURIComponent(action),
      {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: "{}",
        credentials: "same-origin",
      }
    )
      .then(function (response) {
        return response.json();
      })
      .then(function (data) {
        if (data.connection) render(row, data.connection, data.result);
        else throw new Error(data.error || "Connection action failed");
      })
      .catch(function (error) {
        var detail = row.querySelector(".connection-detail");
        if (detail) detail.textContent = error.message;
      });
  }

  function refreshAll() {
    var codingRoot = document.getElementById("ai-connections");
    var coding = codingRoot && codingRoot.getAttribute("data-coding") === "1";
    var url = coding
      ? "/api/ai-connections?refresh=1&coding=1&models=1"
      : "/api/ai-connections?refresh=1";
    return fetchFn(url, { credentials: "same-origin" })
      .then(function (response) {
        return response.json();
      })
      .then(function (data) {
        (data.connections || []).forEach(function (connection) {
          var row = document.querySelector(
            '[data-provider-id="' + connection.id + '"]'
          );
          if (row) render(row, connection, connection);
        });
        if (coding) updateStatusSummary(data.connections || []);
      })
      .catch(function () {});
  }

  function bindRefreshAll() {
    var btn = document.getElementById("aic-refresh-all");
    if (!btn || btn._aiBound) return;
    btn._aiBound = true;
    btn.addEventListener("click", function () {
      btn.disabled = true;
      Promise.resolve(refreshAll()).finally(function () {
        btn.disabled = false;
      });
    });
  }

  function bindRows(root) {
    (root || document)
      .querySelectorAll("[data-provider-id]")
      .forEach(function (row) {
        if (row._aiBound) return;
        row._aiBound = true;
        row.addEventListener("click", function (event) {
          var button = event.target.closest("button[data-action]");
          if (!button || !row.contains(button)) return;
          var action = button.getAttribute("data-action");
          button.disabled = true;
          Promise.resolve(runAction(row, action)).finally(function () {
            button.disabled = false;
          });
        });
      });
  }

  function bindModelField(wrap) {
    if (!wrap || wrap._aiBound) return;
    wrap._aiBound = true;
    var selectEl = wrap.querySelector("select");
    var input = wrap.querySelector("input");
    if (selectEl && input) {
      selectEl.addEventListener("change", function () {
        input.value = selectEl.value;
      });
      input.addEventListener("input", function () {
        var value = input.value.trim();
        var match = Array.prototype.some.call(selectEl.options, function (opt) {
          return opt.value === value;
        });
        if (match) selectEl.value = value;
        else selectEl.value = "";
        if (window.ClimateSelect) window.ClimateSelect.sync(selectEl);
      });
    }
  }

  function applyDefaults(defaults) {
    var form = document.getElementById("coding-defaults-form");
    if (!form || !defaults) return;
    var provider = document.getElementById("coding-default-provider");
    if (provider) {
      provider.value = defaults.default_provider || "";
      if (window.ClimateSelect) window.ClimateSelect.sync(provider);
    }
    var models = defaults.default_models || {};
    form.querySelectorAll("[data-default-model-for]").forEach(function (wrap) {
      var id = wrap.getAttribute("data-default-model-for");
      var input = wrap.querySelector("input");
      var selectEl = wrap.querySelector("select");
      var value = models[id] || "";
      if (input) input.value = value;
      if (selectEl) fillModelSelect(selectEl, Array.prototype.map.call(selectEl.options, function (opt) {
        return opt.value;
      }).filter(Boolean), value);
    });
  }

  function bindDefaultsForm() {
    var form = document.getElementById("coding-defaults-form");
    if (!form || form._aiBound) return;
    form._aiBound = true;
    var status = document.getElementById("coding-defaults-status");
    form.querySelectorAll("[data-default-model-for]").forEach(bindModelField);
    var reset = document.getElementById("coding-defaults-reset");
    if (reset) {
      reset.addEventListener("click", function () {
        var card = document.getElementById("coding-defaults");
        var raw = card && card.getAttribute("data-defaults");
        var defaults = {};
        try {
          defaults = raw ? JSON.parse(raw) : {};
        } catch (_err) {
          defaults = {};
        }
        applyDefaults(defaults);
        if (status) status.textContent = "";
      });
    }
    form.addEventListener("submit", function (event) {
      event.preventDefault();
      var provider = document.getElementById("coding-default-provider");
      var models = {};
      form.querySelectorAll("[data-default-model-for]").forEach(function (label) {
        var id = label.getAttribute("data-default-model-for");
        var input = label.querySelector("input");
        if (id && input) models[id] = input.value.trim();
      });
      if (status) status.textContent = "Saving…";
      fetch("/api/ai-connections/coding-defaults", {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        credentials: "same-origin",
        body: JSON.stringify({
          default_provider: provider ? provider.value : "",
          default_models: models,
        }),
      })
        .then(function (response) {
          return response.json().then(function (data) {
            if (!response.ok || !data.ok) {
              throw new Error(data.error || "Save failed");
            }
            return data;
          });
        })
        .then(function (data) {
          if (status) status.textContent = "Saved";
          var card = document.getElementById("coding-defaults");
          if (card && data.defaults) {
            card.setAttribute("data-defaults", JSON.stringify(data.defaults));
          }
        })
        .catch(function (error) {
          if (status) status.textContent = error.message;
        });
    });
    if (window.ClimateSelect) {
      window.ClimateSelect.enhance(document.getElementById("coding-default-provider"));
      form.querySelectorAll(".aic-model-select").forEach(function (el) {
        window.ClimateSelect.enhance(el);
      });
    }
  }

  document.querySelectorAll(".connection-last-check, .aic-last-global").forEach(function (el) {
    applyChecked(el, el.getAttribute("data-timestamp") || el.getAttribute("datetime") || "");
  });

  bindRows(document);
  bindDefaultsForm();
  bindRefreshAll();
  if (window.HubPerf && window.HubPerf.whenVisible) window.HubPerf.whenVisible(refreshAll);
  else refreshAll();

  // Compact Settings panel shares the same action API.
  var compact = document.getElementById("ai-provider-compact");
  if (compact) {
    bindRows(compact);
    fetchFn("/api/ai-connections?refresh=1&coding=1", { credentials: "same-origin" })
      .then(function (r) {
        return r.json();
      })
      .then(function (data) {
        (data.connections || []).forEach(function (connection) {
          var row = compact.querySelector(
            'tr[data-provider-id="' + connection.id + '"]'
          );
          if (!row) return;
          render(row, connection, connection);
          var detail = compact.querySelector(
            '[data-provider-detail="' + connection.id + '"] .connection-detail'
          );
          if (detail) {
            detail.textContent =
              connection.detail +
              (connection.install_help && !connection.installed
                ? " — " + connection.install_help
                : "");
          }
        });
      })
      .catch(function () {});
  }
})();
