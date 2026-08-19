(function () {
  "use strict";

  var root = document.getElementById("branding-settings");
  if (!root) return;

  var form = document.getElementById("branding-form");
  var fileInput = document.getElementById("branding-file");
  var avatarInput = document.getElementById("branding-avatar-file");
  var previewHeader = document.getElementById("branding-preview-header");
  var previewAvatars = root.querySelectorAll(".branding-preview-avatar-img");
  var previewLockup = root.querySelector(".branding-preview-lockup");
  var logoMeta = document.getElementById("branding-logo-meta");
  var avatarMeta = document.getElementById("branding-avatar-meta");
  var errorEl = document.getElementById("branding-error");
  var okEl = document.getElementById("branding-ok");
  var resetBtn = document.getElementById("branding-reset");
  var logoReplace = document.getElementById("branding-logo-replace");
  var avatarReplace = document.getElementById("branding-avatar-replace");
  var logoRemove = document.getElementById("branding-logo-remove");
  var avatarRemove = document.getElementById("branding-avatar-remove");
  var logoObjectUrl = "";
  var avatarObjectUrl = "";
  var pendingRemoveLogo = false;
  var pendingRemoveAvatar = false;

  function defaultIcon() { return root.getAttribute("data-default-icon") || "/static/img/climate-mark.png"; }
  function defaultFull() { return root.getAttribute("data-default-full") || "/static/img/climate-logo.png"; }
  function selectedDisplay() {
    var el = form.querySelector('input[name="display"]:checked');
    return (el && el.value) || "wordmark";
  }
  function setDisplay(value) {
    var el = form.querySelector('input[name="display"][value="' + value + '"]');
    if (el) el.checked = true;
  }
  function showError(msg) {
    if (!errorEl) return;
    errorEl.textContent = msg || "";
    errorEl.hidden = !msg;
    if (msg && okEl) okEl.hidden = true;
  }
  function showOk(msg) {
    if (!okEl) return;
    okEl.textContent = msg || "";
    okEl.hidden = !msg;
    if (msg && errorEl) errorEl.hidden = true;
  }
  function revoke(url) {
    if (url) URL.revokeObjectURL(url);
  }
  function pendingLogoUrl() {
    if (pendingRemoveLogo) return "";
    if (fileInput && fileInput.files && fileInput.files[0]) return logoObjectUrl || "";
    return "";
  }
  function pendingAvatarUrl() {
    if (pendingRemoveAvatar) return "";
    if (avatarInput && avatarInput.files && avatarInput.files[0]) return avatarObjectUrl || "";
    return "";
  }
  function headerAssetUrl() {
    var pending = pendingLogoUrl();
    if (selectedDisplay() === "full") {
      if (pending) return pending;
      if (!pendingRemoveLogo && root.getAttribute("data-custom") === "1") {
        return root.getAttribute("data-full-url") || defaultFull();
      }
      return defaultFull();
    }
    return defaultIcon();
  }
  function avatarAssetUrl() {
    var pending = pendingAvatarUrl();
    if (pending) return pending;
    if (!pendingRemoveAvatar && root.getAttribute("data-custom-avatar") === "1") {
      return root.getAttribute("data-avatar-url") || defaultIcon();
    }
    return defaultIcon();
  }
  function cacheBust(url) {
    if (!url) return url;
    return url + (url.indexOf("?") >= 0 ? "&" : "?") + "cb=" + Date.now();
  }
  function setAvatarSrc(url) {
    previewAvatars.forEach(function (img) {
      if (url) img.src = url;
    });
  }
  function kindFromFile(file) {
    var name = String((file && file.name) || "").toLowerCase();
    var type = String((file && file.type) || "").toLowerCase();
    if (name.endsWith(".svg") || type.indexOf("svg") >= 0) return "SVG";
    if (name.endsWith(".webp") || type.indexOf("webp") >= 0) return "WEBP";
    return "PNG";
  }
  function formatMeta(name, width, height, kind, isDefault, pending) {
    var parts = [];
    if (pending) parts.push("Preview");
    else if (isDefault) parts.push("Default");
    parts.push(name || "saved file");
    if (width && height) parts.push(width + " × " + height);
    if (kind) parts.push(kind);
    return parts.join(" · ");
  }
  function savedLogoMeta() {
    return formatMeta(
      root.getAttribute("data-logo-name") || "climate-logo.png",
      root.getAttribute("data-logo-width"),
      root.getAttribute("data-logo-height"),
      root.getAttribute("data-logo-kind") || "PNG",
      root.getAttribute("data-custom") !== "1",
      false
    );
  }
  function savedAvatarMeta() {
    return formatMeta(
      root.getAttribute("data-avatar-name") || "climate-mark.png",
      root.getAttribute("data-avatar-width"),
      root.getAttribute("data-avatar-height"),
      root.getAttribute("data-avatar-kind") || "PNG",
      root.getAttribute("data-custom-avatar") !== "1",
      false
    );
  }
  function updateRemoveButtons() {
    if (logoRemove) {
      logoRemove.disabled = root.getAttribute("data-custom") !== "1" && !pendingLogoUrl() && !pendingRemoveLogo;
    }
    if (avatarRemove) {
      avatarRemove.disabled = root.getAttribute("data-custom-avatar") !== "1" && !pendingAvatarUrl() && !pendingRemoveAvatar;
    }
  }
  function applyFileMeta(file, url, metaEl, fallbackName) {
    if (!metaEl) return;
    if (!file) {
      metaEl.textContent = fallbackName;
      return;
    }
    var kind = kindFromFile(file);
    metaEl.textContent = formatMeta(file.name, "", "", kind, false, true);
    if (!url) return;
    var probe = new Image();
    probe.onload = function () {
      if (probe.naturalWidth && probe.naturalHeight) {
        metaEl.textContent = formatMeta(file.name, probe.naturalWidth, probe.naturalHeight, kind, false, true);
      }
    };
    probe.src = url;
  }
  function syncPreview() {
    if (previewLockup) {
      previewLockup.setAttribute("data-brand-display", selectedDisplay());
      previewLockup.setAttribute("data-brand-fit", "contain");
    }
    var headerSrc = headerAssetUrl();
    var avatarSrc = avatarAssetUrl();
    if (previewHeader && headerSrc) previewHeader.src = headerSrc;
    setAvatarSrc(avatarSrc);
    if (pendingLogoUrl() && fileInput.files[0]) {
      applyFileMeta(fileInput.files[0], logoObjectUrl, logoMeta, savedLogoMeta());
    } else if (pendingRemoveLogo) {
      if (logoMeta) logoMeta.textContent = "Will restore default · climate-logo.png";
    } else if (logoMeta) {
      logoMeta.textContent = savedLogoMeta();
    }
    if (pendingAvatarUrl() && avatarInput.files[0]) {
      applyFileMeta(avatarInput.files[0], avatarObjectUrl, avatarMeta, savedAvatarMeta());
    } else if (pendingRemoveAvatar) {
      if (avatarMeta) avatarMeta.textContent = "Will restore default · climate-mark.png";
    } else if (avatarMeta) {
      avatarMeta.textContent = savedAvatarMeta();
    }
    updateRemoveButtons();
  }
  function acceptImageFile(input, kind) {
    var file = input && input.files && input.files[0];
    if (!file) return "";
    var name = String(file.name || "").toLowerCase();
    if (!/\.(png|svg|webp)$/.test(name) && !/^image\/(png|svg\+xml|webp)$/.test(file.type || "")) {
      showError((kind || "Image") + " must be PNG, SVG, or WEBP");
      input.value = "";
      return "";
    }
    showError("");
    return URL.createObjectURL(file);
  }
  function applyBranding(branding) {
    root.setAttribute("data-custom", branding.custom_logo ? "1" : "0");
    root.setAttribute("data-custom-avatar", branding.custom_avatar ? "1" : "0");
    root.setAttribute("data-icon-url", branding.icon_url || defaultIcon());
    root.setAttribute("data-avatar-url", branding.avatar_url || defaultIcon());
    root.setAttribute("data-full-url", branding.full_url || defaultFull());
    root.setAttribute("data-logo-name", branding.original_name || branding.filename || "climate-logo.png");
    root.setAttribute("data-avatar-name", branding.avatar_original_name || branding.avatar_filename || "climate-mark.png");
    root.setAttribute("data-logo-width", branding.logo_width || "");
    root.setAttribute("data-logo-height", branding.logo_height || "");
    root.setAttribute("data-avatar-width", branding.avatar_width || "");
    root.setAttribute("data-avatar-height", branding.avatar_height || "");
    root.setAttribute("data-logo-kind", branding.logo_kind || "PNG");
    root.setAttribute("data-avatar-kind", branding.avatar_kind || "PNG");
    setDisplay(branding.display === "full" ? "full" : "wordmark");
  }
  function postBranding(extra) {
    var body = new FormData();
    body.append("display", selectedDisplay());
    body.append("fit", "contain");
    extra = extra || {};
    if (extra.removeLogo) body.append("remove_logo", "1");
    if (extra.removeAvatar) body.append("remove_avatar", "1");
    if (!extra.removeLogo && fileInput && fileInput.files && fileInput.files[0]) body.append("logo", fileInput.files[0]);
    if (!extra.removeAvatar && avatarInput && avatarInput.files && avatarInput.files[0]) body.append("avatar", avatarInput.files[0]);
    return fetch(root.getAttribute("data-endpoint") || "/api/settings/branding", {
      method: "POST",
      body: body,
      credentials: "same-origin"
    }).then(function (resp) { return resp.json().then(function (data) { return { ok: resp.ok, data: data }; }); });
  }
  function afterSave(branding, message) {
    applyBranding(branding);
    pendingRemoveLogo = false;
    pendingRemoveAvatar = false;
    if (fileInput) fileInput.value = "";
    if (avatarInput) avatarInput.value = "";
    revoke(logoObjectUrl);
    revoke(avatarObjectUrl);
    logoObjectUrl = "";
    avatarObjectUrl = "";
    if (previewHeader && branding.logo_url) previewHeader.src = cacheBust(branding.logo_url);
    setAvatarSrc(cacheBust(branding.avatar_url || defaultIcon()));
    showOk(message);
    syncPreview();
  }

  if (logoReplace && fileInput) {
    logoReplace.addEventListener("click", function () { fileInput.click(); });
  }
  if (avatarReplace && avatarInput) {
    avatarReplace.addEventListener("click", function () { avatarInput.click(); });
  }
  if (fileInput) {
    fileInput.addEventListener("change", function () {
      revoke(logoObjectUrl);
      pendingRemoveLogo = false;
      logoObjectUrl = acceptImageFile(fileInput, "Logo");
      if (logoObjectUrl) setDisplay("full");
      syncPreview();
    });
  }
  if (avatarInput) {
    avatarInput.addEventListener("change", function () {
      revoke(avatarObjectUrl);
      pendingRemoveAvatar = false;
      avatarObjectUrl = acceptImageFile(avatarInput, "AiriX icon");
      syncPreview();
    });
  }
  if (logoRemove) {
    logoRemove.addEventListener("click", function () {
      if (fileInput) fileInput.value = "";
      revoke(logoObjectUrl);
      logoObjectUrl = "";
      pendingRemoveLogo = true;
      syncPreview();
    });
  }
  if (avatarRemove) {
    avatarRemove.addEventListener("click", function () {
      if (avatarInput) avatarInput.value = "";
      revoke(avatarObjectUrl);
      avatarObjectUrl = "";
      pendingRemoveAvatar = true;
      syncPreview();
    });
  }

  form.querySelectorAll('input[name="display"]').forEach(function (input) {
    input.addEventListener("change", syncPreview);
  });

  form.addEventListener("submit", function (event) {
    event.preventDefault();
    postBranding({ removeLogo: pendingRemoveLogo, removeAvatar: pendingRemoveAvatar })
      .then(function (result) {
        if (!result.ok || !result.data.ok) {
          showError((result.data && result.data.error) || "Could not save branding");
          return;
        }
        afterSave(result.data.branding || {}, "Branding saved. Reload other open tabs to refresh the shell.");
      })
      .catch(function () { showError("Could not save branding"); });
  });

  if (resetBtn) {
    resetBtn.addEventListener("click", function () {
      if (!window.confirm("Reset to the default CLIMATE logo and AiriX icon?")) return;
      fetch(root.getAttribute("data-reset") || "/api/settings/branding/reset", {
        method: "POST",
        credentials: "same-origin"
      }).then(function (resp) { return resp.json().then(function (data) { return { ok: resp.ok, data: data }; }); })
        .then(function (result) {
          if (!result.ok || !result.data.ok) {
            showError((result.data && result.data.error) || "Could not reset branding");
            return;
          }
          afterSave(result.data.branding || {}, "Branding reset to defaults.");
        })
        .catch(function () { showError("Could not reset branding"); });
    });
  }

  window.addEventListener("beforeunload", function () {
    revoke(logoObjectUrl);
    revoke(avatarObjectUrl);
  });
  syncPreview();
})();
