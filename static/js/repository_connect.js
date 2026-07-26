(function () {
  var root = document.getElementById("rw-connect");
  if (!root) return;
  var pathEl = document.getElementById("rw-connect-path");
  var statusEl = document.getElementById("rw-connect-status");
  var preview = document.getElementById("rw-connect-preview");
  var facts = document.getElementById("rw-connect-facts");
  var warnings = document.getElementById("rw-connect-warnings");
  var profilesEl = document.getElementById("rw-connect-profiles");
  var editName = document.getElementById("rw-edit-name");
  var editPath = document.getElementById("rw-edit-path");
  var editGit = document.getElementById("rw-edit-git");
  var chkSave = document.getElementById("rw-chk-save");
  var chkMismatch = document.getElementById("rw-chk-mismatch");
  var chkReplace = document.getElementById("rw-chk-replace");
  var mismatchBox = document.getElementById("rw-confirm-mismatch");
  var replaceBox = document.getElementById("rw-confirm-replace");
  var saveBtn = document.getElementById("rw-connect-save");
  var lastScan = null;

  function setStatus(msg) {
    statusEl.textContent = msg || "";
  }

  function syncSaveEnabled() {
    var ok = chkSave.checked;
    if (!mismatchBox.hidden && !chkMismatch.checked) ok = false;
    if (!replaceBox.hidden && !chkReplace.checked) ok = false;
    saveBtn.disabled = !ok || !lastScan;
  }

  function renderFacts(scan) {
    var rows = [
      ["Resolved path", scan.path],
      ["Folder name", scan.folder_name],
      ["Git repository", scan.is_git ? "yes" : "no"],
      ["Git remote", scan.git_remote_url || "—"],
      ["Branch", scan.git_branch || "—"],
      ["Registered URL", scan.registered_git_url || "—"],
      ["Remote match", scan.remote_matches_registered == null ? "—" : scan.remote_matches_registered ? "yes" : "mismatch"],
      ["Languages", (scan.languages || []).join(", ") || "—"],
      ["Frameworks", (scan.frameworks || []).join(", ") || "—"],
      ["Package managers", (scan.package_managers || []).join(", ") || "—"],
      ["README", (scan.readme_files || []).join(", ") || "—"],
      ["AI instructions", (scan.ai_instruction_files || []).join(", ") || "—"],
      ["Entry points", (scan.entry_points || []).join(", ") || "—"],
      ["Informational", (scan.informational_files || []).join(", ") || "—"],
      ["Suggested ports", (scan.suggested_ports || []).join(", ") || "—"],
    ];
    facts.innerHTML = rows
      .map(function (r) {
        return "<div><dt>" + r[0] + "</dt><dd><code>" + escapeHtml(String(r[1])) + "</code></dd></div>";
      })
      .join("");
  }

  function escapeHtml(s) {
    return s
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;");
  }

  function renderProfiles(list) {
    if (!list || !list.length) {
      profilesEl.innerHTML = '<p class="muted">No run-profile suggestions.</p>';
      return;
    }
    profilesEl.innerHTML = list
      .map(function (p, idx) {
        return (
          '<div class="rw-profile-card" data-idx="' +
          idx +
          '">' +
          '<label class="rw-profile-select"><input type="checkbox" class="rw-profile-chk" data-idx="' +
          idx +
          '"> Add this profile</label>' +
          "<div class='muted'>" +
          escapeHtml(p.rationale || "") +
          " · untrusted suggestion</div>" +
          "<label>Name<input type='text' class='rw-p-name' value='" +
          escapeHtml(p.name || "") +
          "'></label>" +
          "<label>Executable<input type='text' class='rw-p-exe' value='" +
          escapeHtml(p.executable || "") +
          "'></label>" +
          "<label>Args (JSON array)<input type='text' class='rw-p-args' value='" +
          escapeHtml(JSON.stringify(p.args || [])) +
          "'></label>" +
          "<label>Working directory<input type='text' class='rw-p-cwd' value='" +
          escapeHtml(p.working_directory || "{repository_path}") +
          "'></label>" +
          "<label>Port<input type='number' class='rw-p-port' value='" +
          (p.default_port || 8000) +
          "'></label>" +
          "<pre class='mono rw-p-preview'>" +
          escapeHtml((p.command_preview || []).join(" ")) +
          "</pre>" +
          '<input type="hidden" class="rw-p-id" value="' +
          escapeHtml(p.suggestion_id || p.id || "") +
          '">' +
          '<input type="hidden" class="rw-p-envnames" value="' +
          escapeHtml(JSON.stringify(p.allowed_env_names || [])) +
          '">' +
          '<input type="hidden" class="rw-p-portenv" value="' +
          escapeHtml(p.port_env || "") +
          '">' +
          '<input type="hidden" class="rw-p-local" value="' +
          escapeHtml(p.local_url || "") +
          '">' +
          '<input type="hidden" class="rw-p-health" value="' +
          escapeHtml(p.health_url || "") +
          '">' +
          "</div>"
        );
      })
      .join("");
  }

  function collectProfiles() {
    var out = [];
    profilesEl.querySelectorAll(".rw-profile-card").forEach(function (card) {
      var chk = card.querySelector(".rw-profile-chk");
      if (!chk || !chk.checked) return;
      var argsRaw = card.querySelector(".rw-p-args").value;
      var args;
      try {
        args = JSON.parse(argsRaw);
      } catch (e) {
        throw new Error("Profile args must be a JSON array");
      }
      if (!Array.isArray(args)) throw new Error("Profile args must be a JSON array");
      out.push({
        id: card.querySelector(".rw-p-id").value,
        suggestion_id: card.querySelector(".rw-p-id").value,
        name: card.querySelector(".rw-p-name").value,
        executable: card.querySelector(".rw-p-exe").value,
        args: args,
        working_directory: card.querySelector(".rw-p-cwd").value,
        default_port: parseInt(card.querySelector(".rw-p-port").value, 10) || 8000,
        environments: [document.getElementById("rw-edit-env").value || "development"],
        allowed_env_names: JSON.parse(card.querySelector(".rw-p-envnames").value || "[]"),
        port_env: card.querySelector(".rw-p-portenv").value || null,
        local_url: card.querySelector(".rw-p-local").value || "http://127.0.0.1:{port}/",
        health_url: card.querySelector(".rw-p-health").value || null,
        rationale: "Reviewed during Connect Local Workspace",
      });
    });
    return out;
  }

  document.getElementById("rw-connect-scan").onclick = function () {
    setStatus("Scanning (read-only)…");
    preview.hidden = true;
    lastScan = null;
    syncSaveEnabled();
    fetch(root.getAttribute("data-scan-url"), {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ path: pathEl.value }),
    })
      .then(function (r) {
        return r.json();
      })
      .then(function (data) {
        if (!data.ok) {
          setStatus(data.error || "Scan failed");
          return;
        }
        lastScan = data;
        var scan = data.scan;
        setStatus(scan.note || "Scan complete.");
        warnings.innerHTML = (scan.warnings || [])
          .map(function (w) {
            return '<div class="banner">' + escapeHtml(w) + "</div>";
          })
          .join("");
        renderFacts(scan);
        editPath.value = scan.path;
        if (scan.git_remote_url && !editGit.value) editGit.value = scan.git_remote_url;
        renderProfiles((data.editable && data.editable.profiles) || scan.suggested_profiles || []);
        mismatchBox.hidden = !scan.remote_mismatch;
        replaceBox.hidden = !scan.replacing_existing_path;
        chkMismatch.checked = false;
        chkReplace.checked = false;
        chkSave.checked = false;
        preview.hidden = false;
        syncSaveEnabled();
      })
      .catch(function (err) {
        setStatus(String(err));
      });
  };

  [chkSave, chkMismatch, chkReplace].forEach(function (el) {
    el.addEventListener("change", syncSaveEnabled);
  });

  saveBtn.onclick = function () {
    if (!lastScan) return;
    var profiles;
    try {
      profiles = collectProfiles();
    } catch (e) {
      setStatus(e.message || String(e));
      return;
    }
    setStatus("Saving…");
    fetch(root.getAttribute("data-save-url"), {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        path: editPath.value,
        name: editName.value,
        git_url: editGit.value,
        confirm_save: chkSave.checked,
        confirm_remote_mismatch: chkMismatch.checked,
        confirm_replace_path: chkReplace.checked,
        selected_profiles: profiles,
      }),
    })
      .then(function (r) {
        return r.json();
      })
      .then(function (data) {
        if (!data.ok) {
          setStatus(data.error || "Save failed");
          return;
        }
        setStatus("Saved. Refreshing…");
        window.location.href = root.getAttribute("data-overview-url");
      })
      .catch(function (err) {
        setStatus(String(err));
      });
  };
})();
