(function () {
  var root = document.getElementById("rw-logs");
  if (!root) return;
  var select = document.getElementById("rw-log-run");
  var view = document.getElementById("rw-log-view");
  var meta = document.getElementById("rw-log-meta");
  var follow = document.getElementById("rw-log-follow");
  var offset = 0;

  function logsUrl(runId) {
    return root
      .getAttribute("data-logs-base")
      .replace("__ID__", encodeURIComponent(runId));
  }

  function load(reset) {
    var runId = select.value;
    if (!runId) {
      view.textContent = "No runs available.";
      return;
    }
    if (reset) offset = 0;
    fetch(logsUrl(runId) + "?offset=" + offset + "&limit=400")
      .then(function (r) {
        return r.json();
      })
      .then(function (data) {
        if (!data.ok) {
          view.textContent = data.error || "Unable to load logs";
          return;
        }
        var lines = data.lines || [];
        if (reset) view.textContent = lines.join("\n");
        else if (lines.length) view.textContent += (view.textContent ? "\n" : "") + lines.join("\n");
        offset = data.next_offset || offset;
        meta.textContent =
          (data.run
            ? data.run.status + " · port " + data.run.port + " · pid " + (data.run.pid || "—") + " · "
            : "") +
          "lines " +
          (data.total_lines || 0);
        if (follow.checked) view.scrollTop = view.scrollHeight;
      });
  }

  select.addEventListener("change", function () {
    load(true);
  });
  if (root.getAttribute("data-selected")) {
    select.value = root.getAttribute("data-selected");
  }
  load(true);
  setInterval(function () {
    if (document.visibilityState === "hidden") return;
    if (follow.checked) load(false);
  }, 2000);
})();
