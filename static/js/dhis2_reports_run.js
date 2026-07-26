(function () {
  var root = document.getElementById("dr-run");
  if (!root) return;
  var statusEl = document.getElementById("dr-status");
  var previewEl = document.getElementById("dr-preview");
  var liveBox = document.getElementById("dr-live-box");
  var openBtn = document.getElementById("dr-open");
  var openDhis2 = document.getElementById("dr-open-dhis2");
  var downloadBtn = document.getElementById("dr-download");
  var last = { url: "", view: "", download: "", type: "" };

  function payload() {
    var liveChk = document.getElementById("dr-confirm-live");
    return {
      report_id: document.getElementById("dr-report").value,
      environment: document.getElementById("dr-env").value,
      period: document.getElementById("dr-period").value,
      org_unit: document.getElementById("dr-ou").value,
      output_format: document.getElementById("dr-format").value,
      confirm_live: !!(liveChk && liveChk.checked),
      parameters: {},
    };
  }

  function syncLive() {
    liveBox.hidden = document.getElementById("dr-env").value !== "live";
  }

  function setActions(opts) {
    last = {
      url: opts.url || "",
      view: opts.view || "",
      download: opts.download || "",
      type: opts.type || "",
    };
    if (last.url) {
      openDhis2.hidden = false;
      openDhis2.href = last.url;
      openDhis2.removeAttribute("aria-disabled");
    } else {
      openDhis2.hidden = true;
      openDhis2.href = "#";
    }
    if (last.view || last.url) {
      openBtn.hidden = false;
      openBtn.href = last.view || last.url;
      openBtn.removeAttribute("aria-disabled");
    } else {
      openBtn.hidden = true;
      openBtn.href = "#";
    }
    if (last.download) {
      downloadBtn.hidden = false;
      downloadBtn.href = last.download;
      downloadBtn.removeAttribute("aria-disabled");
    } else {
      downloadBtn.hidden = true;
      downloadBtn.href = "#";
    }
  }

  document.getElementById("dr-env").addEventListener("change", syncLive);
  syncLive();

  // Prefer active hub DHIS2 instance when provided
  var preferred = root.getAttribute("data-preferred-env");
  if (preferred === "live" || preferred === "stage") {
    document.getElementById("dr-env").value = preferred;
    syncLive();
  }

  document.getElementById("dr-preview-btn").onclick = function () {
    var body = payload();
    if (!body.report_id) {
      statusEl.textContent = "Select a report first.";
      return;
    }
    statusEl.textContent = "Previewing…";
    fetch(root.getAttribute("data-preview-url"), {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    })
      .then(function (r) {
        return r.json();
      })
      .then(function (data) {
        if (!data.ok) {
          previewEl.textContent = data.error || "Preview failed";
          statusEl.textContent = data.error || "Preview failed";
          return;
        }
        var resolved = data.resolved || {};
        var report = data.report || {};
        previewEl.textContent = JSON.stringify(
          {
            environment: resolved.environment,
            parameters: resolved.parameters,
            resolved_url: resolved.resolved_url,
            command_preview: resolved.command_preview,
            warnings: resolved.warnings,
          },
          null,
          2
        );
        statusEl.textContent = "Preview ready.";
        if (resolved.resolved_url) {
          setActions({
            url: resolved.resolved_url,
            view: report.type === "dhis2_standard" ? resolved.resolved_url : "",
            type: report.type || "",
          });
        }
      })
      .catch(function (err) {
        statusEl.textContent = String(err);
      });
  };

  document.getElementById("dr-generate").onclick = function () {
    var body = payload();
    if (!body.report_id) {
      statusEl.textContent = "Select a report first.";
      return;
    }
    if (body.environment === "live" && !body.confirm_live) {
      statusEl.textContent = "Confirm Live before generating.";
      liveBox.hidden = false;
      return;
    }
    statusEl.textContent = "Generating…";
    fetch(root.getAttribute("data-generate-url"), {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    })
      .then(function (r) {
        return r.json();
      })
      .then(function (data) {
        if (!data.ok) {
          statusEl.textContent = data.error || "Generate failed";
          return;
        }
        var run = data.run || {};
        statusEl.textContent = "Status: " + run.status + (run.error ? " — " + run.error : "");
        var view = "";
        var download = "";
        if (run.output_path || (run.id && run.report_type && run.report_type !== "dhis2_standard")) {
          view = "/dhis2/reports/view?run=" + encodeURIComponent(run.id);
          download = "/api/dhis2/reports/download/" + encodeURIComponent(run.id);
        }
        if (run.output_url && run.report_type === "dhis2_standard") {
          view = run.output_url;
          download = "/api/dhis2/reports/download/" + encodeURIComponent(run.id);
        }
        setActions({
          url: run.output_url || "",
          view: view || run.output_url || "",
          download: download,
          type: run.report_type || "",
        });
      })
      .catch(function (err) {
        statusEl.textContent = String(err);
      });
  };

  openBtn.addEventListener("click", function (ev) {
    if (openBtn.hidden || !openBtn.getAttribute("href") || openBtn.getAttribute("href") === "#") {
      ev.preventDefault();
      statusEl.textContent = "Preview or Generate first.";
    }
  });
  openDhis2.addEventListener("click", function (ev) {
    if (openDhis2.hidden || !openDhis2.getAttribute("href") || openDhis2.getAttribute("href") === "#") {
      ev.preventDefault();
      statusEl.textContent = "Preview or Generate first to resolve the DHIS2 URL.";
    }
  });
  downloadBtn.addEventListener("click", function (ev) {
    if (downloadBtn.hidden || !downloadBtn.getAttribute("href") || downloadBtn.getAttribute("href") === "#") {
      ev.preventDefault();
      statusEl.textContent = "Generate a run before downloading.";
    }
  });

  document.getElementById("dr-save-preset").onclick = function () {
    var body = payload();
    if (!body.report_id) {
      statusEl.textContent = "Select a report first.";
      return;
    }
    var name = window.prompt("Preset name");
    if (!name) return;
    body.name = name;
    fetch(root.getAttribute("data-preset-url"), {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    })
      .then(function (r) {
        return r.json();
      })
      .then(function (data) {
        statusEl.textContent = data.ok ? "Preset saved." : data.error || "Save failed";
      });
  };
})();
