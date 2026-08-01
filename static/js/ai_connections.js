(function () {
  "use strict";

  function render(row, connection, result) {
    var status = row.querySelector(".connection-status");
    var detail = row.querySelector(".connection-detail");
    var account = row.querySelector(".connection-account");
    var last = row.querySelector(".connection-last-success");
    if (status) {
      status.textContent = connection.status || "Error";
      status.className = "status-pill connection-status status-" + String(connection.state || "error").replace(/_/g, "-");
    }
    if (detail) detail.textContent = (result && result.detail) || connection.detail || "";
    if (account) account.textContent = connection.account_label || "Not exposed";
    if (last) last.textContent = connection.last_successful_check || "Never";
    var connect = row.querySelector('[data-action="connect"], [data-action="reconnect"]');
    if (connect) {
      connect.setAttribute("data-action", connection.state === "connected" ? "reconnect" : "connect");
      connect.textContent = connection.state === "connected" ? "Reconnect" : "Connect";
    }
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
})();
