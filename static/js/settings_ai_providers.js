(function () {
  "use strict";

  var root = document.getElementById("ai-provider-settings");
  if (!root) return;

  var dialog = document.getElementById("ai-provider-key-dialog");
  var form = document.getElementById("ai-provider-key-form");
  var input = document.getElementById("ai-provider-key-input");
  var help = document.getElementById("ai-provider-key-help");
  var title = document.getElementById("ai-provider-key-title");
  var cancel = document.getElementById("ai-provider-key-cancel");
  var activeId = "";

  function endpoint(id, suffix) {
    return "/api/settings/ai-providers/" + encodeURIComponent(id) + "/" + suffix;
  }

  function clearKeyInput() {
    if (!input) return;
    input.value = "";
  }

  function closeOverflow(card) {
    var open = card && card.querySelector(".ai-provider-overflow[open]");
    if (open) open.removeAttribute("open");
  }

  function credentialIcon(status) {
    if (status === "Configured") return "✓";
    if (status === "Missing") return "!";
    return "○";
  }

  function applyCard(card, provider) {
    if (!card || !provider) return;
    var configured = !!provider.configured;
    card.setAttribute("data-configured", configured ? "1" : "0");
    card.setAttribute("data-credential-type", provider.credential_type || "");
    card.setAttribute("data-display-name", provider.display_name || "");
    var status = card.querySelector(".connection-status");
    if (status) {
      status.textContent = provider.status_label || "";
      status.className =
        "status-pill connection-status status-" +
        String(provider.state || "").replace(/_/g, "-");
    }
    var credential = card.querySelector(".js-credential");
    if (credential) {
      credential.classList.toggle("is-missing", provider.credential_status === "Missing");
      credential.classList.toggle("is-configured", provider.credential_status === "Configured");
      var ico = credential.querySelector(".ai-provider-stat-ico");
      if (ico) ico.textContent = credentialIcon(provider.credential_status);
      var credText = credential.querySelector(".js-credential-text");
      if (credText) credText.textContent = provider.credential_status || "";
    }
    var models = card.querySelector(".js-models");
    if (models) {
      models.textContent = provider.models_label || "—";
      models.classList.toggle("is-ready", !!provider.models_count);
    }
    var last = card.querySelector(".js-last-check");
    if (last) last.textContent = provider.last_check_label || "—";
    var summary = card.querySelector(".js-test-summary");
    if (summary) summary.textContent = provider.test_summary || "";
    var blurb = card.querySelector(".js-blurb");
    if (blurb) {
      blurb.textContent = provider.blurb || provider.help || "";
      blurb.hidden = provider.status_label === "Connected";
    }
    var error = card.querySelector(".js-error");
    if (error) {
      error.textContent = provider.last_error || "";
      error.hidden = !provider.last_error;
    }
    var setBtn = card.querySelector('[data-action="set-key"]');
    if (setBtn) setBtn.textContent = configured ? "Replace Key" : "Add Key";
    var removeBtn = card.querySelector('[data-action="remove-key"]');
    if (removeBtn) removeBtn.disabled = !configured;
    var testBtn = card.querySelector('[data-action="test"]');
    if (testBtn) testBtn.disabled = !configured;
  }

  function showRequestError(card, message) {
    var error = card && card.querySelector(".js-error");
    if (!error) return;
    error.textContent = message || "Request failed";
    error.hidden = false;
  }

  function request(url, options) {
    return fetch(url, options).then(function (resp) {
      return resp.json().then(function (body) {
        if (!resp.ok) {
          var err = new Error((body && body.error) || "Request failed");
          err.body = body;
          throw err;
        }
        return body;
      });
    });
  }

  function openKeyDialog(card) {
    activeId = card.getAttribute("data-provider-id") || "";
    var name = card.getAttribute("data-display-name") || "provider";
    if (title) title.textContent = "Configure " + name;
    if (help) {
      help.textContent =
        "The key is stored only on the local CLIMATE server and will not be displayed again after saving.";
    }
    clearKeyInput();
    if (dialog && dialog.showModal) dialog.showModal();
    if (input) input.focus();
  }

  root.addEventListener("click", function (event) {
    var button = event.target.closest("[data-action]");
    if (!button || !root.contains(button)) return;
    var card = button.closest(".ai-provider-card");
    if (!card) return;
    var id = card.getAttribute("data-provider-id") || "";
    var action = button.getAttribute("data-action");
    if (action === "set-key") {
      openKeyDialog(card);
      return;
    }
    if (action === "remove-key") {
      if (!window.confirm("Remove the stored API key for this provider from the server?")) return;
      button.disabled = true;
      request(endpoint(id, "key"), { method: "DELETE" })
        .then(function (body) {
          applyCard(card, body.provider);
          closeOverflow(card);
        })
        .catch(function (err) {
          showRequestError(card, err.message);
        })
        .finally(function () {
          button.disabled = card.getAttribute("data-configured") !== "1";
        });
      return;
    }
    if (action === "test") {
      button.disabled = true;
      request(endpoint(id, "test"), { method: "POST" })
        .then(function (body) {
          applyCard(card, body.provider);
        })
        .catch(function (err) {
          showRequestError(card, err.message);
        })
        .finally(function () {
          button.disabled = card.getAttribute("data-configured") !== "1";
        });
    }
  });

  if (cancel && dialog) {
    cancel.addEventListener("click", function () {
      clearKeyInput();
      dialog.close();
    });
  }

  if (form) {
    form.addEventListener("submit", function (event) {
      event.preventDefault();
      var id = activeId;
      var card = root.querySelector('.ai-provider-card[data-provider-id="' + id + '"]');
      var value = input ? String(input.value || "") : "";
      clearKeyInput();
      if (!id) return;
      request(endpoint(id, "key"), {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ api_key: value }),
      })
        .then(function (body) {
          applyCard(card, body.provider);
          if (dialog) dialog.close();
        })
        .catch(function (err) {
          showRequestError(card, err.message);
          if (dialog) dialog.close();
        });
    });
  }

  if (dialog) {
    dialog.addEventListener("close", clearKeyInput);
  }
})();
