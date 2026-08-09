(function () {
  "use strict";

  var fetchFn = (window.HubPerf && window.HubPerf.dedupeFetch) || fetch;

  function yn(value) {
    return value ? "Yes" : "No";
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
    var sm = compact ? " btn-sm" : "";
    var html = "";
    if (!installed) {
      html +=
        '<button type="button" class="btn btn-primary' +
        sm +
        '" data-action="install_help">Install help</button>';
    } else if (connected) {
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
        '" data-action="disconnect">Sign out</button>';
    } else {
      html +=
        '<button type="button" class="btn btn-primary' +
        sm +
        '" data-action="connect">Connect</button>';
      html +=
        '<button type="button" class="btn' +
        sm +
        '" data-action="test">' +
        (compact ? "Test" : "Test Connection") +
        "</button>";
      if (!compact) {
        html +=
          '<button type="button" class="btn" data-action="disconnect">Sign out</button>';
      }
    }
    if (id !== "codex" && installed && !compact) {
      html +=
        '<button type="button" class="btn" data-action="refresh-models">Refresh Models</button>';
    }
    actions.innerHTML = html;
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
    var compact = !!row.closest("#ai-provider-compact");
    if (status) {
      status.textContent = connection.summary_label || connection.status || "Error";
      status.className =
        "status-pill connection-status status-" +
        String(connection.state || "error").replace(/_/g, "-");
    }
    if (detail) {
      var text = (result && result.detail) || connection.detail || "";
      if (connection.install_help && !connection.installed) {
        text = text + (text ? " — " : "") + connection.install_help;
      }
      detail.textContent = text;
    }
    if (account) account.textContent = connection.account_label || "Not exposed";
    if (last)
      last.textContent =
        connection.last_check || connection.last_successful_check || "Never";
    if (installed)
      installed.textContent = compact
        ? connection.installed
          ? "Installed"
          : "Missing"
        : yn(connection.installed);
    if (authenticated) authenticated.textContent = yn(connection.authenticated);
    if (available) available.textContent = yn(connection.available);
    if (version) version.textContent = connection.version || "—";
    if (cli)
      cli.textContent = (connection.cli_commands || []).join(", ") || "—";
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

  function runAction(row, action) {
    var provider = row.getAttribute("data-provider-id");
    if (action === "install_help") {
      fetchFn("/api/ai-connections?refresh=1", { credentials: "same-origin" })
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
    fetch(
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
    return fetchFn("/api/ai-connections?refresh=1", { credentials: "same-origin" })
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
      })
      .catch(function () {});
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
          Promise.resolve(runAction(row, action)).then(function () {
            button.disabled = false;
          });
        });
      });
  }

  bindRows(document);
  if (window.HubPerf && window.HubPerf.whenVisible) window.HubPerf.whenVisible(refreshAll);
  else refreshAll();

  // Compact Settings panel shares the same action API.
  var compact = document.getElementById("ai-provider-compact");
  if (compact) {
    bindRows(compact);
    fetchFn("/api/ai-connections?refresh=1", { credentials: "same-origin" })
      .then(function (r) {
        return r.json();
      })
      .then(function (data) {
        (data.connections || []).forEach(function (connection) {
          var row = compact.querySelector(
            'tr[data-provider-id="' + connection.id + '"]'
          );
          if (!row) return;
          var status = row.querySelector(".connection-status");
          if (status) {
            status.textContent =
              connection.summary_label || connection.status || "Error";
            status.className =
              "status-pill connection-status status-" +
              String(connection.state || "error").replace(/_/g, "-");
          }
          var installed = row.querySelector(".connection-installed");
          if (installed)
            installed.textContent = connection.installed ? "Installed" : "Missing";
          var version = row.querySelector(".connection-version");
          if (version) version.textContent = connection.version || "—";
          var last = row.querySelector(".connection-last-check");
          if (last)
            last.textContent =
              connection.last_check ||
              connection.last_successful_check ||
              "Never";
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
