(function () {
  "use strict";

  var root = document.getElementById("branding-settings");
  if (!root) return;

  var form = document.getElementById("branding-form");
  var fileInput = document.getElementById("branding-file");
  var previewHeader = document.getElementById("branding-preview-header");
  var previewAvatar = document.getElementById("branding-preview-avatar");
  var previewLockup = root.querySelector(".branding-preview-lockup");
  var previewNote = document.getElementById("branding-preview-note");
  var errorEl = document.getElementById("branding-error");
  var okEl = document.getElementById("branding-ok");
  var resetBtn = document.getElementById("branding-reset");
  var objectUrl = "";

  function defaultIcon() { return root.getAttribute("data-default-icon") || "/static/img/climate-mark.png"; }
  function defaultFull() { return root.getAttribute("data-default-full") || "/static/img/climate-logo.png"; }
  function selectedDisplay() {
    var el = form.querySelector('input[name="display"]:checked');
    return (el && el.value) || "wordmark";
  }
  function selectedFit() {
    var el = form.querySelector('input[name="fit"]:checked');
    return (el && el.value) || "contain";
  }
  function showError(msg) {
    if (!errorEl) return;
    errorEl.textContent = msg || "";
    errorEl.hidden = !msg;
    if (okEl) okEl.hidden = true;
  }
  function showOk(msg) {
    if (!okEl) return;
    okEl.textContent = msg || "";
    okEl.hidden = !msg;
    if (errorEl) errorEl.hidden = true;
  }
  function revokePreview() {
    if (objectUrl) {
      URL.revokeObjectURL(objectUrl);
      objectUrl = "";
    }
  }
  function pendingFileUrl() {
    if (fileInput && fileInput.files && fileInput.files[0]) return objectUrl || "";
    return "";
  }
  function headerAssetUrl() {
    var pending = pendingFileUrl();
    if (pending) return pending;
    if (root.getAttribute("data-custom") === "1") {
      return root.getAttribute("data-full-url") || root.getAttribute("data-icon-url") || defaultIcon();
    }
    return selectedDisplay() === "full" ? defaultFull() : defaultIcon();
  }
  function avatarAssetUrl() {
    var pending = pendingFileUrl();
    if (pending) return pending;
    if (root.getAttribute("data-custom") === "1") {
      return root.getAttribute("data-avatar-url") || root.getAttribute("data-icon-url") || defaultIcon();
    }
    return defaultIcon();
  }
  function cacheBust(url) {
    if (!url) return url;
    return url + (url.indexOf("?") >= 0 ? "&" : "?") + "cb=" + Date.now();
  }
  function syncPreview() {
    if (previewLockup) {
      previewLockup.setAttribute("data-brand-display", selectedDisplay());
      previewLockup.setAttribute("data-brand-fit", selectedFit());
    }
    var headerSrc = headerAssetUrl();
    var avatarSrc = avatarAssetUrl();
    if (previewHeader && headerSrc) previewHeader.src = headerSrc;
    if (previewAvatar && avatarSrc) previewAvatar.src = avatarSrc;
    if (previewNote && !pendingFileUrl()) {
      previewNote.textContent = root.getAttribute("data-custom") === "1"
        ? "Custom logo (unsaved options shown in preview)"
        : "Default CLIMATE logo";
    }
  }

  if (fileInput) {
    fileInput.addEventListener("change", function () {
      revokePreview();
      var file = fileInput.files && fileInput.files[0];
      if (!file) {
        syncPreview();
        return;
      }
      var name = String(file.name || "").toLowerCase();
      if (!/\.(png|svg|webp)$/.test(name) && !/^image\/(png|svg\+xml|webp)$/.test(file.type || "")) {
        showError("Logo must be PNG, SVG, or WEBP");
        fileInput.value = "";
        syncPreview();
        return;
      }
      objectUrl = URL.createObjectURL(file);
      if (previewNote) previewNote.textContent = "Preview only — not saved yet · " + file.name;
      showError("");
      syncPreview();
    });
  }

  form.querySelectorAll('input[name="display"], input[name="fit"]').forEach(function (input) {
    input.addEventListener("change", syncPreview);
  });

  form.addEventListener("submit", function (event) {
    event.preventDefault();
    var body = new FormData();
    body.append("display", selectedDisplay());
    body.append("fit", selectedFit());
    body.append("avatar_display", "icon");
    if (fileInput && fileInput.files && fileInput.files[0]) body.append("logo", fileInput.files[0]);
    fetch(root.getAttribute("data-endpoint") || "/api/settings/branding", {
      method: "POST",
      body: body,
      credentials: "same-origin"
    }).then(function (resp) { return resp.json().then(function (data) { return { ok: resp.ok, data: data }; }); })
      .then(function (result) {
        if (!result.ok || !result.data.ok) {
          showError((result.data && result.data.error) || "Could not save branding");
          return;
        }
        var branding = result.data.branding || {};
        root.setAttribute("data-custom", branding.custom ? "1" : "0");
        root.setAttribute("data-icon-url", branding.icon_url || defaultIcon());
        root.setAttribute("data-avatar-url", branding.avatar_url || branding.icon_url || defaultIcon());
        root.setAttribute("data-full-url", branding.full_url || defaultFull());
        if (fileInput) fileInput.value = "";
        revokePreview();
        if (previewHeader && branding.logo_url) previewHeader.src = cacheBust(branding.logo_url);
        if (previewAvatar && (branding.avatar_url || branding.icon_url)) {
          previewAvatar.src = cacheBust(branding.avatar_url || branding.icon_url);
        }
        if (previewNote) {
          previewNote.textContent = branding.custom
            ? ("Custom logo · " + (branding.original_name || branding.filename || "saved"))
            : "Default CLIMATE logo";
        }
        showOk("Branding saved. Reload other open tabs to refresh the shell logo.");
        syncPreview();
      })
      .catch(function () { showError("Could not save branding"); });
  });

  if (resetBtn) {
    resetBtn.addEventListener("click", function () {
      if (!window.confirm("Reset to the default CLIMATE logo and wordmark?")) return;
      fetch(root.getAttribute("data-reset") || "/api/settings/branding/reset", {
        method: "POST",
        credentials: "same-origin"
      }).then(function (resp) { return resp.json().then(function (data) { return { ok: resp.ok, data: data }; }); })
        .then(function (result) {
          if (!result.ok || !result.data.ok) {
            showError((result.data && result.data.error) || "Could not reset branding");
            return;
          }
          var branding = result.data.branding || {};
          root.setAttribute("data-custom", "0");
          root.setAttribute("data-icon-url", branding.icon_url || defaultIcon());
          root.setAttribute("data-avatar-url", branding.avatar_url || defaultIcon());
          root.setAttribute("data-full-url", branding.full_url || defaultFull());
          var wordmark = form.querySelector('input[name="display"][value="wordmark"]');
          var contain = form.querySelector('input[name="fit"][value="contain"]');
          if (wordmark) wordmark.checked = true;
          if (contain) contain.checked = true;
          if (fileInput) fileInput.value = "";
          revokePreview();
          if (previewHeader) previewHeader.src = defaultIcon();
          if (previewAvatar) previewAvatar.src = defaultIcon();
          if (previewNote) previewNote.textContent = "Default CLIMATE logo";
          showOk("Branding reset to default.");
          syncPreview();
        })
        .catch(function () { showError("Could not reset branding"); });
    });
  }

  window.addEventListener("beforeunload", revokePreview);
  syncPreview();
})();
