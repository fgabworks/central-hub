(function () {
  "use strict";

  var fetchFn = (window.HubPerf && window.HubPerf.dedupeFetch) || fetch;

  function yn(value) {
    return value ? "Yes" : "No";
  }

  function render(row, connection, result) {
    var status = row.querySelector(".connection-status");
    var detail = row.querySelector(".connection-detail");
    var account = row.querySelector(".connection-account");
    var last = row.querySelector(".connection-last-check");
    var installed = row.querySelector(".connection-installed");
    var authenticated = row.querySelector(".connection-authenticated");
    var version = row.querySelector(".connection-version");
    if (status) {
      status.textContent = connection.status || "Error";
      status.className = "status-pill connection-status status-" + String(connection.state || "error").replace(/_/g, "-");
    }
    if (detail) detail.textContent = (result && result.detail) || connection.detail || "";
    if (account) account.textContent = connection.account_label || "Not exposed";
    if (last) last.textContent = connection.last_check || connection.last_successful_check || "Never";
    if (installed) installed.textContent = yn(connection.installed);
    if (authenticated) authenticated.textContent = yn(connection.authenticated);
    if (version) version.textContent = connection.version || "—";
    var connect = row.querySelector('[data-action="connect"], [data-action="reconnect"]');
    if (connect) {
      connect.setAttribute("data-action", connection.state === "connected" ? "reconnect" : "connect");
      connect.textContent = connection.state === "connected" ? "Reconnect" : "Connect";
    }
  }

  function refreshAll() {
    return fetchFn("/api/ai-connections?refresh=1", { credentials: "same-origin" })
      .then(function (response) { return response.json(); })
      .then(function (data) {
        (data.connections || []).forEach(function (connection) {
          var row = document.querySelector('[data-provider-id="' + connection.id + '"]');
          if (row) render(row, connection, connection);
        });
      })
      .catch(function () {});
  }

  document.querySelectorAll("[data-provider-id]").forEach(function (row) {
    row.addEventListener("click", function (event) {
      var button = event.target.closest("button[data-action]");
      if (!button) return;
      var provider = row.getAttribute("data-provider-id");
      var action = button.getAttribute("data-action");
      button.disabled = true;
      fetch("/api/ai-connections/" + encodeURIComponent(provider) + "/" + encodeURIComponent(action), {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: "{}",
      })
        .then(function (response) { return response.json(); })
        .then(function (data) {
          if (data.connection) render(row, data.connection, data.result);
          else throw new Error(data.error || "Connection action failed");
        })
        .catch(function (error) {
          var detail = row.querySelector(".connection-detail");
          if (detail) detail.textContent = error.message;
        })
        .then(function () { button.disabled = false; });
    });
  });

  // Show cached SSR status immediately; refresh providers in the background.
  if (window.HubPerf && window.HubPerf.whenVisible) window.HubPerf.whenVisible(refreshAll);
  else refreshAll();
})();
