/* Calendar Center — FullCalendar grid (read-only) + right-side event drawer. */
(function (global) {
  "use strict";

  var VIEW_MAP = {
    month: "dayGridMonth",
    week: "timeGridWeek",
    day: "timeGridDay",
    agenda: "listWeek",
    upcoming: "listMonth",
  };

  var VIEW_REVERSE = {
    dayGridMonth: "month",
    timeGridWeek: "week",
    timeGridDay: "day",
    listWeek: "agenda",
    listMonth: "upcoming",
  };

  function browserTimeZone() {
    try {
      return Intl.DateTimeFormat().resolvedOptions().timeZone || "UTC";
    } catch (e) {
      return "UTC";
    }
  }

  function isSmallScreen() {
    return global.matchMedia && global.matchMedia("(max-width: 720px)").matches;
  }

  function escapeHtml(text) {
    return String(text == null ? "" : text)
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;");
  }

  function fillUrl(template, calendarId, eventId) {
    return String(template || "")
      .replace("__CAL__", encodeURIComponent(calendarId))
      .replace("__EVT__", encodeURIComponent(eventId));
  }

  function renderSection(section) {
    var emptyClass = section.empty ? " is-empty" : "";
    var body = "";
    if (section.kind === "attendees") {
      var list = section.value || [];
      if (!list.length) {
        body = '<p class="muted cal-section-empty">No attendees</p>';
      } else {
        body =
          "<ul class=\"cal-attendee-list\">" +
          list
            .map(function (a) {
              return (
                "<li><span class=\"cal-attendee-name\">" +
                escapeHtml(a.display_name || a.email || "") +
                '</span> <span class="muted">' +
                escapeHtml(a.response_status || "") +
                "</span></li>"
              );
            })
            .join("") +
          "</ul>";
      }
    } else if (section.kind === "html") {
      body = section.value
        ? '<div class="cal-desc-html">' + section.value + "</div>"
        : '<p class="muted cal-section-empty">No description</p>';
    } else if (section.kind === "link") {
      body = section.value
        ? '<a class="cal-meet-link" href="' +
          escapeHtml(section.value) +
          '" target="_blank" rel="noopener">' +
          escapeHtml(section.label || "Open link") +
          "</a>"
        : '<p class="muted cal-section-empty">No Meet link</p>';
    } else {
      body =
        '<p class="cal-section-text">' +
        escapeHtml(section.value || "—") +
        "</p>";
    }
    return (
      '<section class="cal-drawer-section' +
      emptyClass +
      '" data-section="' +
      escapeHtml(section.id) +
      '">' +
      "<h3 class=\"cal-section-title\">" +
      escapeHtml(section.title) +
      "</h3>" +
      body +
      "</section>"
    );
  }

  function renderBody(ev) {
    var sections = ev.sections;
    if (!sections || !sections.length) {
      // Fallback if API is older
      sections = [
        { id: "when", title: "Date & time", kind: "text", value: ev.when || "—", empty: false },
        {
          id: "calendar",
          title: "Calendar",
          kind: "text",
          value: ev.calendar_summary || ev.calendar_id || "—",
          empty: false,
        },
        {
          id: "timezone",
          title: "Time zone",
          kind: "text",
          value: (ev.start && ev.start.time_zone) || "—",
          empty: false,
        },
        {
          id: "location",
          title: "Location",
          kind: "text",
          value: ev.location || "No location",
          empty: !ev.location,
        },
        {
          id: "attendees",
          title: "Attendees",
          kind: "attendees",
          value: ev.attendees || [],
          empty: !(ev.attendees || []).length,
        },
        {
          id: "description",
          title: "Description",
          kind: "html",
          value: ev.description_html || "",
          empty: !ev.description_html,
        },
        {
          id: "meet",
          title: "Meet",
          kind: "link",
          value: ev.hangout_link || "",
          label: "Join meeting",
          empty: !ev.hangout_link,
        },
      ];
    }
    return sections.map(renderSection).join("");
  }

  function renderFooter(ev, cfg) {
    var actions = ev.actions || {};
    var calId = ev.calendar_id || "";
    var evtId = ev.id || "";
    var parts = ['<div class="cal-action-group" role="group" aria-label="Event actions">'];

    if (actions.convert_note !== false) {
      parts.push(
        '<form method="post" action="' +
          escapeHtml(fillUrl(cfg.convertNoteUrlTemplate, calId, evtId)) +
          '" class="cal-action-form">' +
          '<button type="submit" class="btn btn-sm btn-primary">Convert to Note</button></form>'
      );
    }
    if (actions.create_task !== false) {
      parts.push(
        '<form method="post" action="' +
          escapeHtml(fillUrl(cfg.convertTaskUrlTemplate, calId, evtId)) +
          '" class="cal-action-form">' +
          '<button type="submit" class="btn btn-sm">Create Task</button></form>'
      );
    }
    if (actions.link_repository) {
      var repos = actions.registry_repos || cfg.registryRepos || [];
      parts.push(
        '<form method="post" action="' +
          escapeHtml(fillUrl(cfg.linkRepoUrlTemplate, calId, evtId)) +
          '" class="cal-action-form cal-action-link-repo">' +
          '<label class="cal-action-label" for="cal-repo-select">Repository</label>' +
          '<select name="repository_id" id="cal-repo-select" required aria-label="Repository">' +
          '<option value="">Link repository…</option>' +
          repos
            .map(function (r) {
              return (
                '<option value="' +
                escapeHtml(r.id) +
                '">' +
                escapeHtml(r.label) +
                "</option>"
              );
            })
            .join("") +
          "</select>" +
          '<button type="submit" class="btn btn-sm">Link Repository</button></form>'
      );
    }
    if (actions.open_in_google && ev.html_link) {
      parts.push(
        '<a class="btn btn-sm cal-action-google" href="' +
          escapeHtml(ev.html_link) +
          '" target="_blank" rel="noopener">Open in Google</a>'
      );
    }
    parts.push("</div>");
    parts.push(
      '<p class="muted cal-readonly-hint">Read-only · Edit / Delete / RSVP unavailable</p>'
    );
    return parts.join("");
  }

  function init(cfg) {
    var root = document.getElementById("calendar-grid");
    if (!root || !global.FullCalendar) return;

    var tzInput = document.getElementById("cal-tz");
    var timeZone = (cfg.timeZone || "").trim() || browserTimeZone();
    if (tzInput && !tzInput.value) {
      tzInput.value = timeZone;
    } else if (tzInput && tzInput.value) {
      timeZone = tzInput.value.trim() || timeZone;
    }
    var tzLabel = document.getElementById("cal-tz-label");
    if (tzLabel) tzLabel.textContent = timeZone;

    var initialHubView = cfg.initialView || "month";
    if (isSmallScreen() && (initialHubView === "month" || initialHubView === "week")) {
      initialHubView = "agenda";
    }
    var fcView = VIEW_MAP[initialHubView] || "dayGridMonth";

    var drawer = document.getElementById("cal-drawer");
    var drawerBody = document.getElementById("cal-drawer-body");
    var drawerFoot = document.getElementById("cal-drawer-foot");
    var drawerTitle = document.getElementById("cal-drawer-title");
    var apiError = document.getElementById("cal-api-error");
    var emptyHint = document.getElementById("cal-empty");
    var titleEl = document.getElementById("cal-title");
    var viewInput = document.getElementById("cal-view-input");
    var anchorInput = document.getElementById("cal-anchor-input");
    var refreshView = document.getElementById("cal-refresh-view");

    function setApiError(msg) {
      if (!apiError) return;
      if (msg) {
        apiError.hidden = false;
        apiError.textContent = msg;
      } else {
        apiError.hidden = true;
        apiError.textContent = "";
      }
    }

    function openDrawerPanel() {
      if (!drawer) return;
      document.documentElement.classList.add("cal-drawer-open");
      if (typeof drawer.showModal === "function") drawer.showModal();
      else drawer.setAttribute("open", "open");
      var closeBtn = document.getElementById("cal-drawer-close");
      if (closeBtn) closeBtn.focus();
    }

    function closeDrawer() {
      if (!drawer) return;
      document.documentElement.classList.remove("cal-drawer-open");
      if (typeof drawer.close === "function" && drawer.open) drawer.close();
      else drawer.removeAttribute("open");
    }

    function populateDrawer(detail) {
      if (!drawer || !drawerBody) return;
      var ev = detail.event || detail;
      if (drawerTitle) {
        drawerTitle.textContent = ev.summary || "Event";
        drawerTitle.title = ev.summary || "Event";
      }
      drawerBody.innerHTML = renderBody(ev);
      if (drawerFoot) drawerFoot.innerHTML = renderFooter(ev, cfg);
      openDrawerPanel();
    }

    var closeBtn = document.getElementById("cal-drawer-close");
    if (closeBtn) closeBtn.addEventListener("click", closeDrawer);
    if (drawer) {
      // Backdrop click (dialog itself is the click target outside the panel).
      drawer.addEventListener("click", function (e) {
        if (e.target === drawer) closeDrawer();
      });
      drawer.addEventListener("cancel", function (e) {
        e.preventDefault();
        closeDrawer();
      });
      var panel = drawer.querySelector(".cal-drawer-panel");
      if (panel) {
        panel.addEventListener("click", function (e) {
          e.stopPropagation();
        });
      }
    }
    document.addEventListener("keydown", function (e) {
      if (e.key === "Escape" && drawer && drawer.open) {
        closeDrawer();
      }
    });

    var calendar = new global.FullCalendar.Calendar(root, {
      initialView: fcView,
      initialDate: cfg.initialAnchor || undefined,
      timeZone: timeZone,
      height: "auto",
      headerToolbar: false,
      nowIndicator: true,
      editable: false,
      selectable: false,
      selectMirror: false,
      eventStartEditable: false,
      eventDurationEditable: false,
      eventResizableFromStart: false,
      droppable: false,
      dayMaxEvents: true,
      navLinks: true,
      weekNumbers: false,
      slotMinTime: "06:00:00",
      slotMaxTime: "22:00:00",
      allDaySlot: true,
      allDayText: "All day",
      // Month: all-day = solid bars; timed = list-item (dot + title).
      eventDisplay: "auto",
      displayEventTime: true,
      displayEventEnd: false,
      views: {
        dayGridMonth: {
          eventDisplay: "auto",
          dayMaxEvents: 4,
          moreLinkClick: "popover",
        },
        timeGridWeek: {
          eventDisplay: "block",
        },
        timeGridDay: {
          eventDisplay: "block",
        },
      },
      eventOrder: "start,-duration,allDay,title",
      eventDidMount: function (info) {
        var title = info.event.title || "";
        if (title) {
          info.el.setAttribute("title", title);
        }
        // Reinforce month separation even if a CDN build ignores view eventDisplay.
        if (info.view.type === "dayGridMonth") {
          if (info.event.allDay) {
            info.el.classList.add("fc-daygrid-block-event");
            info.el.classList.remove("fc-daygrid-dot-event");
          } else {
            info.el.classList.add("fc-daygrid-dot-event");
            info.el.classList.remove("fc-daygrid-block-event");
          }
        }
      },
      events: function (info, successCallback, failureCallback) {
        var qEl = document.getElementById("cal-q");
        var calEl = document.getElementById("cal-calendar");
        var params = new URLSearchParams({
          start: info.startStr,
          end: info.endStr,
          tz: timeZone,
        });
        if (qEl && qEl.value) params.set("q", qEl.value);
        if (calEl && calEl.value) params.set("calendar", calEl.value);
        else if (cfg.calendarId) params.set("calendar", cfg.calendarId);
        setApiError("");
        fetch(cfg.eventsUrl + "?" + params.toString(), {
          headers: { Accept: "application/json" },
          credentials: "same-origin",
        })
          .then(function (res) {
            return res.json().then(function (body) {
              if (!res.ok || !body.ok) {
                throw new Error((body && body.error) || "Failed to load events");
              }
              return body;
            });
          })
          .then(function (body) {
            var viewType = calendar && calendar.view ? calendar.view.type : "dayGridMonth";
            var events = (body.events || []).map(function (ev) {
              var copy = Object.assign({}, ev);
              if (viewType.indexOf("dayGrid") === 0) {
                // Explicit display avoids timed events rendering as solid bars that
                // fuse into multi-day all-day chips across week boundaries.
                copy.display = copy.allDay ? "block" : "list-item";
              } else if (viewType.indexOf("timeGrid") === 0) {
                copy.display = "block";
              }
              return copy;
            });
            if (emptyHint) emptyHint.hidden = events.length > 0;
            successCallback(events);
          })
          .catch(function (err) {
            setApiError(err.message || "Calendar API error");
            if (emptyHint) emptyHint.hidden = true;
            failureCallback(err);
          });
      },
      datesSet: function (arg) {
        if (titleEl) titleEl.textContent = arg.view.title;
        var hubView = VIEW_REVERSE[arg.view.type] || "month";
        if (viewInput) viewInput.value = hubView;
        if (refreshView) refreshView.value = hubView;
        if (anchorInput && arg.start) {
          var d = arg.view.currentStart || arg.start;
          anchorInput.value = d.toISOString().slice(0, 10);
        }
        document.querySelectorAll("#cal-view-nav .email-view").forEach(function (btn) {
          var active = btn.getAttribute("data-view") === hubView;
          btn.classList.toggle("is-active", active);
          btn.setAttribute("aria-pressed", active ? "true" : "false");
        });
        try {
          var url = new URL(global.location.href);
          url.searchParams.set("view", hubView);
          url.searchParams.set("account", cfg.accountId);
          if (anchorInput && anchorInput.value) {
            url.searchParams.set("anchor", anchorInput.value);
          }
          if (tzInput && tzInput.value) url.searchParams.set("tz", tzInput.value);
          global.history.replaceState({}, "", url.toString());
        } catch (e) {
          /* ignore */
        }
      },
      eventClick: function (info) {
        info.jsEvent.preventDefault();
        var xp = info.event.extendedProps || {};
        var calendarId = xp.calendar_id;
        var eventId = xp.event_id || info.event.id;
        if (!calendarId || !eventId) return;
        var url = fillUrl(cfg.eventDetailUrlTemplate, calendarId, eventId);
        url += (url.indexOf("?") >= 0 ? "&" : "?") + "tz=" + encodeURIComponent(timeZone);
        if (drawerTitle) drawerTitle.textContent = "Loading…";
        if (drawerBody) drawerBody.innerHTML = '<p class="muted">Loading…</p>';
        if (drawerFoot) drawerFoot.innerHTML = "";
        openDrawerPanel();
        fetch(url, { headers: { Accept: "application/json" }, credentials: "same-origin" })
          .then(function (res) {
            return res.json().then(function (body) {
              if (!res.ok || !body.ok) {
                throw new Error((body && body.error) || "Failed to load event");
              }
              return body;
            });
          })
          .then(function (body) {
            populateDrawer(body);
          })
          .catch(function (err) {
            if (drawerBody) {
              drawerBody.innerHTML =
                '<p class="banner banner-error">' + escapeHtml(err.message) + "</p>";
            }
          });
      },
    });

    calendar.render();

    var todayBtn = document.getElementById("cal-today");
    var prevBtn = document.getElementById("cal-prev");
    var nextBtn = document.getElementById("cal-next");
    if (todayBtn) todayBtn.addEventListener("click", function () { calendar.today(); });
    if (prevBtn) prevBtn.addEventListener("click", function () { calendar.prev(); });
    if (nextBtn) nextBtn.addEventListener("click", function () { calendar.next(); });

    document.querySelectorAll("#cal-view-nav .email-view").forEach(function (btn) {
      btn.addEventListener("click", function () {
        var hub = btn.getAttribute("data-view");
        var mapped = VIEW_MAP[hub];
        if (!mapped) return;
        calendar.changeView(mapped);
      });
    });

    var filterForm = document.getElementById("cal-filter-form");
    if (filterForm) {
      filterForm.addEventListener("submit", function (e) {
        e.preventDefault();
        if (tzInput) {
          timeZone = tzInput.value.trim() || browserTimeZone();
          tzInput.value = timeZone;
          calendar.setOption("timeZone", timeZone);
          if (tzLabel) tzLabel.textContent = timeZone;
        }
        calendar.refetchEvents();
      });
    }
  }

  global.CentralHubCalendar = {
    init: init,
    renderBody: renderBody,
    renderFooter: renderFooter,
  };
})(window);
