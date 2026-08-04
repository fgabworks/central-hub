/* TODAY Mission Control - compact dashboard panel. */
(function () {
  var root = document.getElementById("mc-dash-widget");
  if (!root) return;

  var listEl = root.querySelector("[data-mc-list]");
  var progressLabel = root.querySelector("[data-mc-progress-label]");
  var progressRing = root.querySelector("[data-mc-progress-ring]");
  var progressPercent = root.querySelector("[data-mc-progress-percent]");
  var allDoneEl = root.querySelector("[data-mc-all-done]");
  var reminderEl = root.querySelector("[data-mc-reminder]");
  var openBtn = root.querySelector("[data-mc-open-btn]");
  var clearBtn = root.querySelector("[data-mc-clear-completed]");
  var addForm = root.querySelector("[data-mc-add-form]");
  var addInput = root.querySelector("[data-mc-add-input]");

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

  function setCardState(p) {
    var allDone = p.done > 0 && p.pending === 0 && p.overdue === 0;
    var hasCarry = p.overdue > 0;

    root.classList.remove("is-success", "is-carry-warn", "is-active");
    if (allDone) root.classList.add("is-success");
    else if (hasCarry) root.classList.add("is-carry-warn");
    else root.classList.add("is-active");

    if (allDoneEl) allDoneEl.hidden = !allDone;
    if (openBtn) openBtn.classList.toggle("is-success", !!allDone);
    if (clearBtn) clearBtn.disabled = !(p.done > 0);
  }

  function renderProgress(p) {
    var percent = Number(p.percent || 0);
    if (progressLabel) {
      progressLabel.textContent = p.done + "/" + p.total + " Completed";
    }
    if (progressRing) {
      progressRing.style.setProperty("--mc-progress", percent + "%");
      progressRing.setAttribute(
        "aria-label",
        p.done + " of " + p.total + " completed"
      );
    }
    if (progressPercent) progressPercent.textContent = percent + "%";
  }

  function renderMission(m) {
    var done = m.status === "done";
    var carry = !!m.is_overdue_carry;
    var state = carry ? "is-carry" : done ? "is-done" : "is-pending";
    var statusClass = carry ? "mc-chip-carry" : done ? "mc-chip-done" : "mc-chip-pending";
    var statusLabel = carry ? "Carry-over" : done ? "Completed" : "Pending";
    var priority = esc(m.priority_label || m.priority || "Medium");

    return (
      '<li class="mc-command-item ' +
      state +
      '" data-mission-id="' +
      esc(m.id) +
      '">' +
      '<label class="mc-command-check">' +
      '<input type="checkbox" class="mc-widget-checkbox"' +
      (done ? " checked" : "") +
      ' data-mission-id="' +
      esc(m.id) +
      '" aria-label="' +
      esc((done ? "Reopen " : "Complete ") + (m.title || "mission")) +
      '">' +
      '<span class="mc-command-main">' +
      '<span class="mc-command-title">' +
      esc(m.title || "Untitled mission") +
      "</span>" +
      '<span class="mc-command-meta">' +
      '<span class="mc-chip mc-chip-priority">' +
      priority +
      '</span><span class="mc-meta-sep" aria-hidden="true">&bull;</span>' +
      '<span class="mc-chip ' +
      statusClass +
      '">' +
      statusLabel +
      "</span></span></span></label></li>"
    );
  }

  function applyWidget(widget) {
    if (!widget || !widget.progress) return;
    var p = widget.progress;
    setCardState(p);
    renderProgress(p);

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
      listEl.innerHTML = rows.length
        ? rows.map(renderMission).join("")
        : '<li class="mc-command-empty muted" data-mc-empty>No missions today.</li>';
    }
    if (widget.today) root.setAttribute("data-today", widget.today);
  }

  function postJson(url, body) {
    return fetch(url, {
      method: "POST",
      headers: { "Content-Type": "application/json", Accept: "application/json" },
      body: JSON.stringify(body || {}),
    }).then(function (r) {
      return r.json().then(function (data) {
        if (!r.ok || !data || !data.ok) {
          throw new Error((data && data.error) || "Request failed");
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

  if (addForm) {
    addForm.addEventListener("submit", function (ev) {
      ev.preventDefault();
      if (!addInput) return;
      var title = String(addInput.value || "").trim();
      if (!title) {
        addInput.focus();
        return;
      }
      var submitBtn = addForm.querySelector(".mc-quick-btn");
      if (submitBtn) submitBtn.disabled = true;
      postJson(root.getAttribute("data-create-url"), {
        title: title,
        priority: "medium",
        due_date: root.getAttribute("data-today") || "",
      })
        .then(function (data) {
          addInput.value = "";
          if (data.widget) applyWidget(data.widget);
          else return refreshWidget();
        })
        .catch(function () {})
        .then(function () {
          if (submitBtn) submitBtn.disabled = false;
          addInput.focus();
        });
    });
  }

  root.addEventListener("change", function (ev) {
    var box = ev.target;
    if (!box || !box.classList || !box.classList.contains("mc-widget-checkbox")) return;
    var id = box.getAttribute("data-mission-id");
    if (!id) return;
    box.disabled = true;
    postJson(box.checked ? completeUrl(id) : reopenUrl(id), {})
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

  root.addEventListener("click", function (ev) {
    var t = ev.target;
    if (!t || !t.closest) return;
    var btn = t.closest("[data-mc-clear-completed]");
    if (!btn || btn.disabled) return;
    btn.disabled = true;
    postJson(root.getAttribute("data-clear-url"), {
      mode: "completed",
      confirm: "clear-completed",
    })
      .then(function (data) {
        if (data.widget) applyWidget(data.widget);
        else return refreshWidget();
      })
      .catch(function () {
        btn.disabled = false;
      });
  });
})();
