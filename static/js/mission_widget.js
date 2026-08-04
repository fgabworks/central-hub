/* TODAY Mission Control — daily task execution panel (dashboard). */
(function () {
  var root = document.getElementById("mc-dash-widget");
  if (!root) return;

  var listEl = root.querySelector("[data-mc-list]");
  var progressEl = root.querySelector("[data-mc-progress]");
  var progressLabel = root.querySelector("[data-mc-progress-label]");
  var dotsEl = root.querySelector("[data-mc-dots]");
  var emptyMsgEl = root.querySelector("[data-mc-empty-msg]");
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

  function renderDots(done, total) {
    if (!dotsEl) return;
    if (!total) {
      dotsEl.innerHTML = '<span class="mc-dot is-empty-slot"></span>';
      dotsEl.setAttribute("aria-label", "0 of 0 completed");
      return;
    }
    var html = "";
    for (var i = 0; i < total; i++) {
      html += '<span class="mc-dot' + (i < done ? " is-filled" : "") + '"></span>';
    }
    dotsEl.innerHTML = html;
    dotsEl.setAttribute("aria-label", done + " of " + total + " completed");
  }

  function setCardState(p) {
    var allDone = p.done > 0 && p.pending === 0 && p.overdue === 0;
    var hasCarry = p.overdue > 0;
    var isEmpty = !(p.total_all > 0);

    root.classList.remove("is-success", "is-carry-warn", "is-active");
    if (allDone) root.classList.add("is-success");
    else if (hasCarry) root.classList.add("is-carry-warn");
    else root.classList.add("is-active");

    if (emptyMsgEl) emptyMsgEl.hidden = !isEmpty;
    if (progressEl) progressEl.hidden = isEmpty;
    if (allDoneEl) allDoneEl.hidden = !allDone;

    if (openBtn) openBtn.classList.toggle("is-success", !!allDone);
    if (clearBtn) clearBtn.disabled = !(p.done > 0);
  }

  function renderMission(m) {
    var done = m.status === "done";
    var carry = !!m.is_overdue_carry;
    var state = carry ? "is-carry" : done ? "is-done" : "is-pending";
    var priority = esc(m.priority_label || m.priority || "Medium");
    var meta = "";
    if (carry) {
      meta =
        '<span class="mc-chip mc-chip-priority">' +
        priority +
        '</span><span class="mc-meta-sep" aria-hidden="true">•</span>' +
        '<span class="mc-chip mc-chip-carry">Carry-over</span>';
    } else if (done) {
      meta =
        '<span class="mc-chip mc-chip-priority">' +
        priority +
        '</span><span class="mc-meta-sep" aria-hidden="true">&bull;</span>' +
        '<span class="mc-chip mc-chip-done">Completed</span>';
    } else {
      meta =
        '<span class="mc-chip mc-chip-priority">' +
        priority +
        '</span><span class="mc-meta-sep" aria-hidden="true">•</span>' +
        '<span class="mc-chip mc-chip-pending">Pending</span>';
    }
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
      meta +
      "</span></span></label></li>"
    );
  }

  function applyWidget(widget) {
    if (!widget || !widget.progress) return;
    var p = widget.progress;
    setCardState(p);
    if (progressLabel) progressLabel.textContent = p.done + "/" + p.total + " Completed";
    renderDots(p.done || 0, p.total || 0);
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
      var rows = widget.missions || widget.top_missions || [];
      listEl.innerHTML = rows.length ? rows.map(renderMission).join("") : "";
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
      })
      .then(function () {
        /* disabled state refreshed by applyWidget */
      });
  });
})();
