(function () {
  var root = document.getElementById("rw-run");
  if (!root) return;
  var profileEl = document.getElementById("rw-profile");
  var envEl = document.getElementById("rw-env");
  var portEl = document.getElementById("rw-port");
  var previewEl = document.getElementById("rw-cmd-preview");
  var statusEl = document.getElementById("rw-run-status");
  var envNamesEl = document.getElementById("rw-env-names");
  var liveBox = document.getElementById("rw-live-confirm");
  var liveChk = document.getElementById("rw-confirm-live");
  var openApp = document.getElementById("rw-open-app");
  var selectedRunId = null;

  function selectedOption() {
    return profileEl.options[profileEl.selectedIndex];
  }

  function syncProfile() {
    var opt = selectedOption();
    if (!opt || !opt.value) return;
    portEl.value = opt.getAttribute("data-port") || portEl.value;
    envNamesEl.textContent =
      "Env vars (names only): " + (opt.getAttribute("data-env-names") || "—");
    var envs = (opt.getAttribute("data-envs") || "development").split(",");
    Array.prototype.forEach.call(envEl.options, function (o) {
      o.disabled = envs.indexOf(o.value) < 0;
    });
    if (envEl.selectedOptions[0] && envEl.selectedOptions[0].disabled) {
      envEl.value = envs[0] || "development";
    }
    updateLiveGate();
  }

  function updateLiveGate() {
    var opt = selectedOption();
    var live =
      (opt && opt.getAttribute("data-live") === "1") || envEl.value === "live";
    liveBox.hidden = !live;
    if (!live) liveChk.checked = false;
  }

  function payload() {
    return {
      profile_id: profileEl.value,
      environment: envEl.value,
      port: parseInt(portEl.value, 10),
      confirm_live: !!(liveChk && liveChk.checked),
    };
  }

  function setStatus(msg) {
    statusEl.textContent = msg;
  }

  function refreshRuns() {
    return fetch(root.getAttribute("data-runs-url"))
      .then(function (r) {
        return r.json();
      })
      .then(function (data) {
        if (!data.ok) return;
        var tbody = document.querySelector("#rw-runs-table tbody");
        if (!tbody) return;
        if (!data.runs.length) {
          tbody.innerHTML =
            '<tr><td colspan="8" class="muted empty-compact">No hub-managed runs yet.</td></tr>';
          return;
        }
        tbody.innerHTML = data.runs
          .map(function (run) {
            return (
              "<tr data-run-id='" +
              run.run_id +
              "'><td><span class='badge'>" +
              run.status +
              "</span></td><td>" +
              run.profile_id +
              "</td><td>" +
              run.environment +
              "</td><td class='mono'>" +
              run.port +
              "</td><td class='mono'>" +
              (run.pid || "—") +
              "</td><td class='muted'>" +
              ((run.started_at || "").slice(0, 19).replace("T", " ") || "—") +
              "</td><td class='muted'>" +
              (run.last_health_detail || "—") +
              "</td><td><button type='button' class='btn btn-sm rw-select-run' data-run-id='" +
              run.run_id +
              "'>Select</button>" +
              (run.local_url
                ? " <a class='btn btn-sm' href='" +
                  run.local_url +
                  "' target='_blank' rel='noopener'>Open</a>"
                : "") +
              "</td></tr>"
            );
          })
          .join("");
        bindSelectButtons();
      });
  }

  function selectRun(run) {
    selectedRunId = run.run_id;
    document.getElementById("rw-stop").disabled = false;
    document.getElementById("rw-restart").disabled = false;
    if (run.local_url) {
      openApp.hidden = false;
      openApp.href = run.local_url;
    }
    document.getElementById("rw-view-logs").href =
      root.getAttribute("data-logs-page") + "?run=" + encodeURIComponent(run.run_id);
    setStatus(
      run.status +
        " · pid " +
        (run.pid || "—") +
        " · port " +
        run.port +
        " · " +
        (run.last_health_detail || "no health yet")
    );
  }

  function bindSelectButtons() {
    document.querySelectorAll(".rw-select-run").forEach(function (btn) {
      btn.onclick = function () {
        var id = btn.getAttribute("data-run-id");
        fetch(root.getAttribute("data-runs-url"))
          .then(function (r) {
            return r.json();
          })
          .then(function (data) {
            var run = (data.runs || []).find(function (x) {
              return x.run_id === id;
            });
            if (run) selectRun(run);
          });
      };
    });
  }

  document.getElementById("rw-preview").onclick = function () {
    fetch(root.getAttribute("data-preview-url"), {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload()),
    })
      .then(function (r) {
        return r.json();
      })
      .then(function (data) {
        if (!data.ok) {
          previewEl.textContent = data.error || "Preview failed";
          return;
        }
        previewEl.textContent =
          (data.command_preview || []).join(" ") +
          "\ncwd: " +
          data.cwd +
          "\nenv names: " +
          (data.env_names || []).join(", ");
      });
  };

  document.getElementById("rw-find-port").onclick = function () {
    fetch(root.getAttribute("data-find-port-url"), {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ port: parseInt(portEl.value, 10) }),
    })
      .then(function (r) {
        return r.json();
      })
      .then(function (data) {
        if (data.port) portEl.value = data.port;
        setStatus(
          data.available
            ? "Preferred port is available"
            : "Using alternate port " + data.port
        );
      });
  };

  document.getElementById("rw-start").onclick = function () {
    var opt = selectedOption();
    var live =
      (opt && opt.getAttribute("data-live") === "1") || envEl.value === "live";
    if (live && root.getAttribute("data-live-allowed") !== "1") {
      setStatus("Live runs are blocked by REPO_WS_ALLOW_LIVE_RUNS");
      return;
    }
    if (live && !liveChk.checked) {
      setStatus("Confirm Live profile before starting");
      return;
    }
    fetch(root.getAttribute("data-start-url"), {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload()),
    })
      .then(function (r) {
        return r.json();
      })
      .then(function (data) {
        if (!data.ok) {
          setStatus(data.error || "Start failed");
          return;
        }
        selectRun(data.run);
        refreshRuns();
      });
  };

  document.getElementById("rw-stop").onclick = function () {
    if (!selectedRunId) return;
    var url = root
      .getAttribute("data-stop-base")
      .replace("__ID__", encodeURIComponent(selectedRunId));
    fetch(url, { method: "POST", headers: { "Content-Type": "application/json" }, body: "{}" })
      .then(function (r) {
        return r.json();
      })
      .then(function (data) {
        if (data.run) selectRun(data.run);
        refreshRuns();
      });
  };

  document.getElementById("rw-restart").onclick = function () {
    if (!selectedRunId) return;
    var url = root
      .getAttribute("data-restart-base")
      .replace("__ID__", encodeURIComponent(selectedRunId));
    fetch(url, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ confirm_live: !!(liveChk && liveChk.checked) }),
    })
      .then(function (r) {
        return r.json();
      })
      .then(function (data) {
        if (!data.ok) {
          setStatus(data.error || "Restart failed");
          return;
        }
        selectRun(data.run);
        refreshRuns();
      });
  };

  profileEl.addEventListener("change", syncProfile);
  envEl.addEventListener("change", updateLiveGate);
  syncProfile();
  bindSelectButtons();
  setInterval(refreshRuns, 4000);
})();
