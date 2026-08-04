/* TODAY Mission Control — Work Dashboard widget (shared MissionControl APIs). */
(function () {
  var root = document.getElementById("mc-dash-widget");
  if (!root) return;

  var listEl = root.querySelector("[data-mc-list]");
  var badgesEl = root.querySelector("[data-mc-badges]");
  var progressLabel = root.querySelector("[data-mc-progress-label]");
  var progressTrack = root.querySelector("[data-mc-progress-track]");
  var progressBar = root.querySelector("[data-mc-progress-bar]");
  var reminderEl = root.querySelector("[data-mc-reminder]");
  var viewAllEl = root.querySelector("[data-mc-view-all]");
  var clearDialog = root.querySelector("[data-mc-clear-dialog]");
  var missionsHref = root.getAttribute("data-missions-href") || "/work/notebook?view=missions";

  function esc(text) {
    return String(text == null ? "" : text)
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;");
  }

  function completeUrl(id) {
    return (root.getAttribute("data-complete-url-template") || "").replace("__ID__", encodeURIComponent(id));
  }

  function reopenUrl(id) {
    return (root.getAttribute("data-reopen-url-template") || "").replace("__ID__", encodeURIComponent(id));
  }

  function renderMission(m) {
    var done = m.status === "done";
    var carry = !!m.is_overdue_carry;
    var classes = "mc-widget-item";
    if (carry) classes += " is-carry";
    if (done) classes += " is-done";
    var meta = "";
    if (carry) {
      meta += '<span class="mc-chip mc-chip-carry">Carry-over</span>';
    } else if (m.show_priority_badge) {
      meta += '<span class="mc-chip mc-chip-priority">' + esc(m.priority_label || m.priority) + "</span>";
    }
    if (done && m.completed_time_label) {
      meta += '<span class="muted">' + esc(m.completed_time_label) + "</span>";
    }
    return (
      '<li class="' +
      classes +
      '" data-mission-id="' +
      esc(m.id) +
      '">' +
      '<label class="mc-widget-check-label">' +
      '<input type="checkbox" class="mc-widget-checkbox"' +
      (done ? " checked" : "") +
      ' data-mission-id="' +
      esc(m.id) +
      '" aria-label="' +
      esc((done ? "Reopen " : "Complete ") + (m.title || "mission")) +
      '">' +
      '<span class="mc-widget-main">' +
      '<span class="mc-widget-title">' +
      esc(m.title || "Untitled mission") +
      "</span>" +
      (meta ? '<span class="mc-widget-meta">' + meta + "</span>" : "") +
      "</span></label></li>"
    );
  }

  function applyWidget(widget) {
    if (!widget || !widget.progress) return;
    var p = widget.progress;
    if (badgesEl) {
      badgesEl.innerHTML =
        '<span class="mc-badge">' +
        esc(p.total_all) +
        " total</span>" +
        '<span class="mc-badge mc-badge-done">' +
        esc(p.done) +
        " completed</span>" +
        '<span class="mc-badge' +
        (p.overdue ? " mc-badge-carry" : "") +
        '">' +
        esc(p.overdue) +
        " carry-over</span>";
    }
    if (progressLabel) {
      progressLabel.textContent = p.done + "/" + p.total + " Completed";
    }
    if (progressTrack) {
      progressTrack.setAttribute("aria-valuemax", String(p.total || 0));
      progressTrack.setAttribute("aria-valuenow", String(p.done || 0));
    }
    if (progressBar) {
      progressBar.style.width = String(p.percent || 0) + "%";
    }
    if (reminderEl) {
      if (widget.reminder && widget.reminder.active) {
        reminderEl.hidden = false;
        reminderEl.textContent =
          "Reminder: " + widget.reminder.count + " unfinished before 5 PM";
      } else {
        reminderEl.hidden = true;
        reminderEl.textContent = "";
      }
    }
    if (listEl) {
      var rows = widget.top_missions || [];
      if (!rows.length) {
        listEl.innerHTML =
          '<li class="muted mc-widget-empty" data-mc-empty>No missions for today. Add one in Mission Control.</li>';
      } else {
        listEl.innerHTML = rows.map(renderMission).join("");
      }
    }
    if (viewAllEl) {
      viewAllEl.hidden = !widget.has_more;
      viewAllEl.href = missionsHref;
    }
  }

  function postJson(url, body) {
    return fetch(url, {
      method: "POST",
      headers: { "Content-Type": "application/json", Accept: "application/json" },
      body: JSON.stringify(body || {}),
    }).then(function (r) {
      return r.json().then(function (data) {
        if (!r.ok || !data || !data.ok) {
          var err = (data && data.error) || "Request failed";
          throw new Error(err);
        }
        return data;
      });
    });
  }

  function refreshWidget() {
    var url = root.getAttribute("data-widget-url");
    if (!url) return Promise.resolve(null);
    return fetch(url, { headers: { Accept: "application/json" } })
      .then(function (r) {
        return r.json();
      })
      .then(function (data) {
        if (data && data.ok && data.widget) applyWidget(data.widget);
        return data;
      });
  }

  root.addEventListener("change", function (ev) {
    var box = ev.target;
    if (!box || !box.classList || !box.classList.contains("mc-widget-checkbox")) return;
    var id = box.getAttribute("data-mission-id");
    if (!id) return;
    box.disabled = true;
    var url = box.checked ? completeUrl(id) : reopenUrl(id);
    postJson(url, {})
      .then(function (data) {
        if (data.widget) applyWidget(data.widget);
        else return refreshWidget();
      })
      .catch(function () {
        box.checked = !box.checked;
      })
      .then(function () {
        box.disabled = false;
      });
  });

  function openClear() {
    if (clearDialog) clearDialog.hidden = false;
  }
  function closeClear() {
    if (clearDialog) clearDialog.hidden = true;
  }

  root.addEventListener("click", function (ev) {
    var t = ev.target;
    if (!t) return;
    if (t.closest && t.closest("[data-mc-clear-open]")) {
      openClear();
      return;
    }
    if (t.closest && t.closest("[data-mc-clear-cancel]")) {
      closeClear();
      return;
    }
    if (t === clearDialog) {
      closeClear();
      return;
    }
    var modeBtn = t.closest && t.closest("[data-mc-clear-mode]");
    if (!modeBtn) return;
    var mode = modeBtn.getAttribute("data-mc-clear-mode") || "completed";
    var confirm = mode === "all" ? "clear-all" : "clear-completed";
    modeBtn.disabled = true;
    postJson(root.getAttribute("data-clear-url"), { mode: mode, confirm: confirm })
      .then(function (data) {
        if (data.widget) applyWidget(data.widget);
        else return refreshWidget();
      })
      .catch(function () {})
      .then(function () {
        modeBtn.disabled = false;
        closeClear();
      });
  });
})();
