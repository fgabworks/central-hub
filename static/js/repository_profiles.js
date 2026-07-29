(function () {
  var root = document.getElementById("run-profiles");
  if (!root) return;

  var statusEl = document.getElementById("rpb-status");
  var editor = document.getElementById("rpb-editor");
  var previewEl = document.getElementById("rpb-preview");
  var bootstrap = {};
  try {
    bootstrap = JSON.parse(document.getElementById("rpb-bootstrap").textContent || "{}");
  } catch (e) {
    bootstrap = {};
  }
  var cache = {
    templates: bootstrap.templates || [],
    approved: bootstrap.approved || [],
    suggestions: bootstrap.suggestions || [],
  };

  function setStatus(msg) {
    statusEl.textContent = msg || "";
  }

  function allProfiles() {
    return []
      .concat(cache.approved || [])
      .concat(cache.suggestions || [])
      .concat(cache.templates || []);
  }

  function findProfile(id) {
    return allProfiles().find(function (p) {
      return p.id === id;
    });
  }

  function csv(list) {
    return (list || []).join(", ");
  }

  function parseCsv(text) {
    return String(text || "")
      .split(",")
      .map(function (s) {
        return s.trim();
      })
      .filter(Boolean);
  }

  function argsFromTextarea() {
    return document
      .getElementById("rpb-args")
      .value.split(/\r?\n/)
      .map(function (s) {
        return s.trimEnd();
      })
      .filter(function (s) {
        return s.length > 0;
      });
  }

  function syncPortFields() {
    var mode = document.getElementById("rpb-port-mode").value;
    document.getElementById("rpb-fixed-wrap").hidden = mode !== "fixed";
    document.getElementById("rpb-default-wrap").hidden = mode === "none" || mode === "fixed";
    document.getElementById("rpb-port-arg-wrap").hidden = mode !== "argument";
    document.getElementById("rpb-port-env-wrap").hidden = mode !== "environment_variable";
    var live = document.getElementById("rpb-live").checked;
    document.getElementById("rpb-live-confirm-wrap").hidden = !live;
    if (!live) document.getElementById("rpb-confirm-live").checked = false;
  }

  function fillForm(p, opts) {
    opts = opts || {};
    document.getElementById("rpb-editor-title").textContent = opts.title || "Edit Run Profile";
    document.getElementById("rpb-id").value = p.id || "";
    document.getElementById("rpb-id").readOnly = !!opts.lockId;
    document.getElementById("rpb-name").value = p.name || "";
    document.getElementById("rpb-enabled").value = p.enabled === false ? "0" : "1";
    var envs = p.environments || ["development"];
    document.getElementById("rpb-environment").value = envs[0] || "development";
    document.getElementById("rpb-environments").value = csv(envs);
    document.getElementById("rpb-exe").value = p.executable || "";
    document.getElementById("rpb-args").value = (p.args_template || p.args || []).join("\n");
    document.getElementById("rpb-cwd").value = p.working_directory || "{repository_path}";
    document.getElementById("rpb-timeout").value = p.startup_timeout_seconds || 30;
    document.getElementById("rpb-port-mode").value = p.port_mode || "argument";
    document.getElementById("rpb-fixed-port").value = p.fixed_port || p.default_port || "";
    document.getElementById("rpb-default-port").value = p.default_port || 8000;
    document.getElementById("rpb-port-arg").value = p.port_arg || "--port {port}";
    document.getElementById("rpb-port-env").value = p.port_env || "PORT";
    document.getElementById("rpb-local-url").value =
      p.local_url_template || p.local_url || "http://127.0.0.1:{port}/";
    document.getElementById("rpb-health-url").value =
      p.health_url_template || p.health_url || "";
    document.getElementById("rpb-env-names").value = csv(p.allowed_env_names);
    document.getElementById("rpb-provides-api").checked = !!p.provides_api;
    document.getElementById("rpb-live").checked = !!p.live_profile;
    document.getElementById("rpb-write").checked = !!p.write_capable;
    document.getElementById("rpb-confirm-live").checked = false;
    previewEl.textContent = "Save or Test to preview.";
    syncPortFields();
    editor.hidden = false;
    editor.scrollIntoView({ behavior: "smooth", block: "nearest" });
  }

  function blankProfile() {
    return {
      id: "",
      name: "",
      enabled: true,
      environments: ["development"],
      executable: "python",
      args_template: [],
      working_directory: "{repository_path}",
      startup_timeout_seconds: 30,
      port_mode: "argument",
      default_port: 8000,
      port_arg: "--port {port}",
      port_env: "PORT",
      local_url_template: "http://127.0.0.1:{port}/",
      health_url_template: "",
      allowed_env_names: [],
      provides_api: false,
      live_profile: false,
      write_capable: false,
    };
  }

  function collectPayload() {
    var envPrimary = document.getElementById("rpb-environment").value;
    var envs = parseCsv(document.getElementById("rpb-environments").value);
    if (envs.indexOf(envPrimary) < 0) envs.unshift(envPrimary);
    if (!envs.length) envs = [envPrimary];
    var mode = document.getElementById("rpb-port-mode").value;
    var payload = {
      id: document.getElementById("rpb-id").value.trim(),
      name: document.getElementById("rpb-name").value.trim(),
      enabled: document.getElementById("rpb-enabled").value === "1",
      environment: envPrimary,
      environments: envs,
      executable: document.getElementById("rpb-exe").value.trim(),
      args: argsFromTextarea(),
      working_directory: document.getElementById("rpb-cwd").value.trim() || "{repository_path}",
      startup_timeout_seconds: parseFloat(document.getElementById("rpb-timeout").value || "30"),
      port_mode: mode,
      local_url: document.getElementById("rpb-local-url").value.trim(),
      health_url: document.getElementById("rpb-health-url").value.trim() || null,
      allowed_env_names: parseCsv(document.getElementById("rpb-env-names").value),
      provides_api: document.getElementById("rpb-provides-api").checked,
      live_profile: document.getElementById("rpb-live").checked,
      write_capable: document.getElementById("rpb-write").checked,
    };
    if (mode === "fixed") {
      payload.fixed_port = parseInt(document.getElementById("rpb-fixed-port").value, 10) || null;
      payload.default_port = payload.fixed_port;
      payload.port_arg = null;
      payload.port_env = null;
    } else if (mode === "none") {
      payload.fixed_port = null;
      payload.default_port = null;
      payload.port_arg = null;
      payload.port_env = null;
      if (!payload.local_url) payload.local_url = "http://127.0.0.1/";
    } else if (mode === "argument") {
      payload.default_port = parseInt(document.getElementById("rpb-default-port").value, 10) || 8000;
      payload.port_arg = document.getElementById("rpb-port-arg").value.trim() || "--port {port}";
      payload.port_env = null;
      payload.fixed_port = null;
    } else {
      payload.default_port = parseInt(document.getElementById("rpb-default-port").value, 10) || 8000;
      payload.port_env = document.getElementById("rpb-port-env").value.trim() || "PORT";
      payload.port_arg = null;
      payload.fixed_port = null;
    }
    return payload;
  }

  function renderRows() {
    function portLabel(p) {
      return (
        (p.port_mode || "—") +
        (p.fixed_port || p.default_port ? " · " + (p.fixed_port || p.default_port) : "")
      );
    }
    function approvedRows() {
      var list = cache.approved || [];
      if (!list.length) {
        return '<tr><td colspan="7" class="muted empty-compact">No repository-specific approved profiles yet.</td></tr>';
      }
      return list
        .map(function (p) {
          return (
            "<tr data-profile-id='" +
            p.id +
            "'><td>" +
            escapeHtml(p.name) +
            "</td><td class='mono'>" +
            escapeHtml(p.id) +
            "</td><td>" +
            escapeHtml((p.environments || []).join(", ")) +
            "</td><td class='mono'>" +
            escapeHtml(portLabel(p)) +
            "</td><td>" +
            (p.enabled ? "yes" : "no") +
            "</td><td>" +
            escapeHtml(p.source || "user") +
            "</td><td class='btn-row'>" +
            "<button type='button' class='btn btn-sm rpb-edit' data-id='" +
            escapeHtml(p.id) +
            "'>Edit</button> " +
            "<button type='button' class='btn btn-sm rpb-dup' data-id='" +
            escapeHtml(p.id) +
            "'>Duplicate</button> " +
            "<button type='button' class='btn btn-sm rpb-toggle' data-id='" +
            escapeHtml(p.id) +
            "' data-enabled='" +
            (p.enabled ? "0" : "1") +
            "'>" +
            (p.enabled ? "Disable" : "Enable") +
            "</button> " +
            "<button type='button' class='btn btn-sm rpb-del' data-id='" +
            escapeHtml(p.id) +
            "'>Delete</button></td></tr>"
          );
        })
        .join("");
    }
    function templateRows() {
      var list = cache.templates || [];
      if (!list.length) {
        return '<tr><td colspan="5" class="muted empty-compact">No YAML templates apply to this repository.</td></tr>';
      }
      return list
        .map(function (p) {
          return (
            "<tr><td>" +
            escapeHtml(p.name) +
            "</td><td class='mono'>" +
            escapeHtml(p.id) +
            "</td><td>" +
            escapeHtml((p.environments || []).join(", ")) +
            "</td><td class='mono'>" +
            escapeHtml(p.port_mode || "") +
            "</td><td class='btn-row'>" +
            "<button type='button' class='btn btn-sm rpb-edit' data-id='" +
            escapeHtml(p.id) +
            "'>Customize</button> " +
            "<button type='button' class='btn btn-sm rpb-dup' data-id='" +
            escapeHtml(p.id) +
            "'>Duplicate</button></td></tr>"
          );
        })
        .join("");
    }
    function suggestionRows() {
      var list = cache.suggestions || [];
      if (!list.length) {
        return '<tr><td colspan="4" class="muted empty-compact">No pending suggestions. Connect a local workspace to detect starters.</td></tr>';
      }
      return list
        .map(function (p) {
          return (
            "<tr><td>" +
            escapeHtml(p.name) +
            " <span class='badge badge-warning'>untrusted</span></td><td class='mono'>" +
            escapeHtml(p.id) +
            "</td><td class='mono'>" +
            escapeHtml(p.port_mode || "") +
            "</td><td class='btn-row'>" +
            "<button type='button' class='btn btn-sm rpb-edit' data-id='" +
            escapeHtml(p.id) +
            "'>Review &amp; approve</button> " +
            "<button type='button' class='btn btn-sm rpb-del' data-id='" +
            escapeHtml(p.id) +
            "'>Discard</button></td></tr>"
          );
        })
        .join("");
    }
    document.querySelector("#rpb-approved-table tbody").innerHTML = approvedRows();
    document.querySelector("#rpb-templates-table tbody").innerHTML = templateRows();
    document.querySelector("#rpb-suggestions-table tbody").innerHTML = suggestionRows();
    bindRowButtons();
  }

  function escapeHtml(s) {
    return String(s)
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;");
  }

  function refresh() {
    return fetch(root.getAttribute("data-list-url"))
      .then(function (r) {
        return r.json();
      })
      .then(function (data) {
        if (!data.ok) {
          setStatus(data.error || "Refresh failed");
          return;
        }
        cache.templates = data.templates || [];
        cache.approved = data.approved || [];
        cache.suggestions = data.suggestions || [];
        renderRows();
        setStatus("Profiles refreshed");
      });
  }

  function bindRowButtons() {
    document.querySelectorAll(".rpb-edit").forEach(function (btn) {
      btn.onclick = function () {
        var p = findProfile(btn.getAttribute("data-id"));
        if (!p) return;
        fillForm(p, {
          title: p.approved === false ? "Review & approve suggestion" : "Edit Run Profile",
          lockId: p.source !== "yaml" && !!p.id,
        });
      };
    });
    document.querySelectorAll(".rpb-dup").forEach(function (btn) {
      btn.onclick = function () {
        var id = btn.getAttribute("data-id");
        var url = root.getAttribute("data-dup-base").replace("__ID__", encodeURIComponent(id));
        fetch(url, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: "{}",
        })
          .then(function (r) {
            return r.json();
          })
          .then(function (data) {
            if (!data.ok) {
              setStatus(data.error || "Duplicate failed");
              return;
            }
            setStatus("Duplicated as " + data.profile.id);
            refresh().then(function () {
              fillForm(data.profile, { title: "Edit duplicate", lockId: true });
            });
          });
      };
    });
    document.querySelectorAll(".rpb-toggle").forEach(function (btn) {
      btn.onclick = function () {
        var id = btn.getAttribute("data-id");
        var enabled = btn.getAttribute("data-enabled") === "1";
        var url = root
          .getAttribute("data-enable-base")
          .replace("__ID__", encodeURIComponent(id));
        fetch(url, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ enabled: enabled }),
        })
          .then(function (r) {
            return r.json();
          })
          .then(function (data) {
            if (!data.ok) {
              setStatus(data.error || "Toggle failed");
              return;
            }
            setStatus((enabled ? "Enabled " : "Disabled ") + id);
            refresh();
          });
      };
    });
    document.querySelectorAll(".rpb-del").forEach(function (btn) {
      btn.onclick = function () {
        var id = btn.getAttribute("data-id");
        if (!window.confirm("Delete profile " + id + "? This cannot be undone.")) return;
        var url = root
          .getAttribute("data-delete-base")
          .replace("__ID__", encodeURIComponent(id));
        fetch(url, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ confirm: true }),
        })
          .then(function (r) {
            return r.json();
          })
          .then(function (data) {
            if (!data.ok) {
              setStatus(data.error || "Delete failed");
              return;
            }
            setStatus("Deleted " + id);
            editor.hidden = true;
            refresh();
          });
      };
    });
  }

  document.getElementById("rpb-add").onclick = function () {
    fillForm(blankProfile(), { title: "Add Run Profile", lockId: false });
  };
  document.getElementById("rpb-refresh").onclick = function () {
    refresh();
  };
  document.getElementById("rpb-cancel").onclick = function () {
    editor.hidden = true;
  };
  document.getElementById("rpb-port-mode").addEventListener("change", syncPortFields);
  document.getElementById("rpb-live").addEventListener("change", syncPortFields);

  document.getElementById("rpb-test").onclick = function () {
    var payload = collectPayload();
    if (payload.live_profile && !document.getElementById("rpb-confirm-live").checked) {
      setStatus("Confirm Live profile before testing");
      return;
    }
    fetch(root.getAttribute("data-test-url"), {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        profile: payload,
        port: payload.default_port || payload.fixed_port || null,
        confirm_live: !!document.getElementById("rpb-confirm-live").checked,
      }),
    })
      .then(function (r) {
        return r.json();
      })
      .then(function (data) {
        if (!data.ok) {
          previewEl.textContent = data.error || "Test failed";
          setStatus(data.error || "Test failed");
          return;
        }
        previewEl.textContent =
          (data.command_preview || []).join(" ") +
          "\ncwd: " +
          data.cwd +
          "\nport mode: " +
          data.port_mode +
          (data.port != null ? " · port " + data.port : "") +
          "\nlocal: " +
          (data.local_url || "—") +
          "\nhealth: " +
          (data.health_url || "—") +
          "\nenv names: " +
          (data.env_names || []).join(", ");
        setStatus("Configuration valid");
      });
  };

  document.getElementById("rpb-save").onclick = function () {
    var payload = collectPayload();
    if (!payload.id || !payload.executable) {
      setStatus("Profile ID and executable are required");
      return;
    }
    if (payload.live_profile && !document.getElementById("rpb-confirm-live").checked) {
      setStatus("Confirm Live profile before saving");
      return;
    }
    fetch(root.getAttribute("data-save-url"), {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        profile: payload,
        approve: true,
        update: true,
        confirm_live: !!document.getElementById("rpb-confirm-live").checked,
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
        setStatus("Saved & approved " + data.profile.id);
        editor.hidden = true;
        refresh();
      });
  };

  // Prefill from Connect / hash query ?profile= or #run-profiles&suggest=
  var params = new URLSearchParams(window.location.search);
  var prefillId = params.get("profile") || params.get("suggest");
  if (prefillId) {
    var found = findProfile(prefillId);
    if (found) fillForm(found, { title: "Review suggestion", lockId: true });
  }

  bindRowButtons();
  syncPortFields();
})();
