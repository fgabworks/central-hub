/**
 * Quick Notepad — scoped Personal / Work client.
 * Expects a host element #qn-host with data-qn-*-url and data-qn-scope.
 * Markdown mode: Edit/Preview (safe HTML via /api/notebook/preview).
 */
(function () {
  function initQuickNotepad() {
    var workspace = document.getElementById("qn-host");
    var panel = document.getElementById("qn-panel");
    var bodyEl = document.getElementById("qn-body");
    var formatEl = document.getElementById("qn-format");
    var statusEl = document.getElementById("qn-status");
    var openBtn = document.getElementById("qn-open-btn");
    var collapseBtn = document.getElementById("qn-collapse");
    var backdrop = document.getElementById("qn-backdrop");
    var resizeHandle = document.getElementById("qn-resize");
    var revList = document.getElementById("qn-rev-list");
    var mdBar = document.getElementById("qn-md-bar");
    var previewEl = document.getElementById("qn-preview");
    var modeEditBtn = document.getElementById("qn-mode-edit");
    var modePreviewBtn = document.getElementById("qn-mode-preview");
    if (!workspace || !panel || !bodyEl || !statusEl) return;

    var scope = workspace.getAttribute("data-qn-scope") || "personal";
    var urls = {
      save: workspace.getAttribute("data-qn-save-url") || "",
      clear: workspace.getAttribute("data-qn-clear-url") || "",
      convert: workspace.getAttribute("data-qn-convert-url") || "",
      restore: workspace.getAttribute("data-qn-restore-url") || "",
      preview:
        workspace.getAttribute("data-qn-preview-url") || "/api/notebook/preview",
    };
    var saveTimer = null;
    var previewTimer = null;
    var lastSaved = bodyEl.value;
    var lastFormat = formatEl ? formatEl.value : "plain";
    var viewMode = "edit"; // edit | preview (markdown only)
    var minW = 240;
    var maxW = 560;

    function setStatus(state, label) {
      statusEl.dataset.state = state;
      statusEl.textContent = label;
    }

    function isOpen() {
      return workspace.classList.contains("is-qn-open");
    }

    function isMarkdown() {
      return !!(formatEl && String(formatEl.value || "").toLowerCase() === "markdown");
    }

    function currentWidth() {
      var w = parseInt(workspace.style.getPropertyValue("--qn-width"), 10);
      if (!w || isNaN(w)) {
        w = parseInt(workspace.getAttribute("data-qn-width"), 10) || 320;
      }
      return Math.max(minW, Math.min(maxW, w));
    }

    function applyOpen(open, persist) {
      workspace.classList.toggle("is-qn-open", !!open);
      workspace.setAttribute("data-qn-open", open ? "1" : "0");
      if (openBtn) openBtn.setAttribute("aria-expanded", open ? "true" : "false");
      if (backdrop) {
        var isGlobal = workspace.classList.contains("qn-global-host");
        if (
          open &&
          (isGlobal || window.matchMedia("(max-width: 980px)").matches)
        ) {
          backdrop.hidden = false;
        } else {
          backdrop.hidden = true;
        }
      }
      if (persist) {
        queueSave({ panel_open: !!open, panel_width: currentWidth() }, true);
      }
    }

    function setViewMode(mode) {
      viewMode = mode === "preview" ? "preview" : "edit";
      if (modeEditBtn) {
        modeEditBtn.classList.toggle("is-active", viewMode === "edit");
        modeEditBtn.setAttribute("aria-pressed", viewMode === "edit" ? "true" : "false");
      }
      if (modePreviewBtn) {
        modePreviewBtn.classList.toggle("is-active", viewMode === "preview");
        modePreviewBtn.setAttribute(
          "aria-pressed",
          viewMode === "preview" ? "true" : "false"
        );
      }
      syncEditorChrome();
      if (viewMode === "preview" && isMarkdown()) {
        refreshPreview(true);
      }
    }

    function syncEditorChrome() {
      var md = isMarkdown();
      if (mdBar) {
        if (md) mdBar.removeAttribute("hidden");
        else mdBar.setAttribute("hidden", "");
      }
      var showPreview = md && viewMode === "preview";
      if (showPreview) bodyEl.setAttribute("hidden", "");
      else bodyEl.removeAttribute("hidden");
      if (previewEl) {
        if (showPreview) previewEl.removeAttribute("hidden");
        else previewEl.setAttribute("hidden", "");
      }
      panel.classList.toggle("qn-is-markdown", md);
      panel.classList.toggle("qn-is-preview", showPreview);
    }

    function refreshPreview(force) {
      if (!previewEl || !isMarkdown()) return;
      clearTimeout(previewTimer);
      var run = function () {
        fetch(urls.preview, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ markdown: bodyEl.value || "" }),
        })
          .then(function (r) {
            return r.json();
          })
          .then(function (data) {
            if (!data || !data.ok) {
              previewEl.innerHTML =
                '<p class="muted">Preview unavailable.</p>';
              return;
            }
            previewEl.innerHTML = data.html || "";
          })
          .catch(function () {
            previewEl.innerHTML = '<p class="muted">Preview failed.</p>';
          });
      };
      if (force) run();
      else previewTimer = setTimeout(run, 250);
    }

    function renderRevisions(revisions) {
      if (!revList) return;
      var items = revisions || [];
      var summary = document.querySelector("#qn-history summary span");
      if (summary) summary.textContent = "(" + items.length + ")";
      if (!items.length) {
        revList.innerHTML =
          '<li class="muted qn-rev-empty">No revisions yet. Clearing or converting saves a snapshot.</li>';
        return;
      }
      revList.innerHTML = items
        .map(function (rev) {
          var when = (rev.created_at || "").slice(0, 19).replace("T", " ");
          var preview = (rev.preview || "")
            .replace(/</g, "&lt;")
            .replace(/>/g, "&gt;");
          var reason = (rev.reason || "snapshot").replace(/</g, "&lt;");
          var id = (rev.id || "").replace(/"/g, "");
          return (
            "<li>" +
            '<div class="qn-rev-meta"><span class="mono muted">' +
            when +
            "</span>" +
            '<span class="badge">' +
            reason +
            "</span></div>" +
            '<div class="qn-rev-preview muted">' +
            preview +
            "</div>" +
            '<button type="button" class="btn btn-sm qn-restore" data-rev="' +
            id +
            '">Restore</button></li>'
          );
        })
        .join("");
    }

    function applyNotepad(pad) {
      if (!pad) return;
      if (
        typeof pad.content === "string" &&
        pad.content !== bodyEl.value &&
        document.activeElement !== bodyEl
      ) {
        bodyEl.value = pad.content;
      }
      if (formatEl && pad.content_format) formatEl.value = pad.content_format;
      if (typeof pad.panel_width === "number") {
        workspace.style.setProperty("--qn-width", pad.panel_width + "px");
        workspace.setAttribute("data-qn-width", String(pad.panel_width));
      }
      if (typeof pad.panel_open === "boolean") {
        applyOpen(pad.panel_open, false);
      }
      lastSaved = bodyEl.value;
      lastFormat = formatEl ? formatEl.value : "plain";
      if (!isMarkdown()) {
        viewMode = "edit";
      }
      syncEditorChrome();
      renderRevisions(pad.revisions);
      setStatus("saved", "Saved");
    }

    function queueSave(extra, force) {
      clearTimeout(saveTimer);
      var delay = force ? 0 : 450;
      saveTimer = setTimeout(function () {
        doSave(extra || {});
      }, delay);
    }

    function doSave(extra) {
      var payload = Object.assign(
        {
          scope: scope,
          content: bodyEl.value,
          content_format: formatEl ? formatEl.value : "plain",
          panel_open: isOpen(),
          panel_width: currentWidth(),
        },
        extra || {}
      );
      setStatus("saving", "Saving…");
      fetch(urls.save, {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload),
      })
        .then(function (r) {
          return r.json().then(function (data) {
            return { ok: r.ok, data: data };
          });
        })
        .then(function (res) {
          if (!res.ok || !res.data || !res.data.ok) {
            setStatus("error", "Save failed");
            return;
          }
          lastSaved = payload.content;
          lastFormat = payload.content_format;
          if (res.data.notepad) {
            renderRevisions(res.data.notepad.revisions);
          }
          setStatus("saved", "Saved");
        })
        .catch(function () {
          setStatus("error", "Save failed");
        });
    }

    bodyEl.addEventListener("input", function () {
      setStatus("saving", "Saving…");
      queueSave({});
      if (isMarkdown() && viewMode === "preview") {
        refreshPreview(false);
      }
    });
    if (formatEl) {
      formatEl.addEventListener("change", function () {
        // Preserve scratchpad content; only metadata changes.
        if (String(formatEl.value || "").toLowerCase() !== "markdown") {
          setViewMode("edit");
        }
        syncEditorChrome();
        setStatus("saving", "Saving…");
        queueSave({ content_format: formatEl.value }, true);
      });
    }

    if (modeEditBtn) {
      modeEditBtn.addEventListener("mousedown", function (ev) {
        ev.preventDefault();
      });
      modeEditBtn.addEventListener("click", function () {
        setViewMode("edit");
        bodyEl.focus();
      });
    }
    if (modePreviewBtn) {
      modePreviewBtn.addEventListener("click", function () {
        setViewMode("preview");
        if (previewEl) previewEl.focus();
      });
    }

    if (collapseBtn) {
      collapseBtn.addEventListener("click", function () {
        applyOpen(false, true);
      });
    }
    if (openBtn) {
      openBtn.addEventListener("click", function () {
        applyOpen(true, true);
      });
    }
    if (backdrop) {
      backdrop.addEventListener("click", function () {
        applyOpen(false, true);
      });
    }
    document.addEventListener("keydown", function (ev) {
      if (ev.key === "Escape" && isOpen()) {
        applyOpen(false, true);
        if (openBtn) openBtn.focus();
      }
    });

    var copyBtn = document.getElementById("qn-copy");
    if (copyBtn) {
      copyBtn.addEventListener("click", function () {
        var text = bodyEl.value || "";
        if (navigator.clipboard && navigator.clipboard.writeText) {
          navigator.clipboard
            .writeText(text)
            .then(function () {
              setStatus("saved", "Copied");
              setTimeout(function () {
                setStatus("saved", "Saved");
              }, 900);
            })
            .catch(function () {
              fallbackCopy(text);
            });
        } else {
          fallbackCopy(text);
        }
      });
    }
    function fallbackCopy(text) {
      bodyEl.focus();
      bodyEl.select();
      try {
        document.execCommand("copy");
        setStatus("saved", "Copied");
      } catch (e) {
        setStatus("error", "Save failed");
      }
      setTimeout(function () {
        setStatus("saved", "Saved");
      }, 900);
    }

    var clearBtn = document.getElementById("qn-clear");
    if (clearBtn) {
      clearBtn.addEventListener("click", function () {
        if (
          !window.confirm(
            "Clear Quick Notepad? A revision snapshot will be kept."
          )
        ) {
          return;
        }
        setStatus("saving", "Saving…");
        fetch(urls.clear, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ scope: scope }),
        })
          .then(function (r) {
            return r.json();
          })
          .then(function (data) {
            if (!data || !data.ok) {
              setStatus("error", "Save failed");
              return;
            }
            bodyEl.value = "";
            applyNotepad(data.notepad);
          })
          .catch(function () {
            setStatus("error", "Save failed");
          });
      });
    }

    var convertBtn = document.getElementById("qn-convert");
    if (convertBtn) {
      convertBtn.addEventListener("click", function () {
        if (!(bodyEl.value || "").trim()) {
          setStatus("error", "Empty");
          setTimeout(function () {
            setStatus("saved", "Saved");
          }, 900);
          return;
        }
        setStatus("saving", "Saving…");
        fetch(urls.convert, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ scope: scope }),
        })
          .then(function (r) {
            return r.json().then(function (data) {
              return { ok: r.ok, data: data };
            });
          })
          .then(function (res) {
            if (!res.ok || !res.data || !res.data.ok) {
              setStatus("error", "Save failed");
              return;
            }
            window.location.href = res.data.redirect;
          })
          .catch(function () {
            setStatus("error", "Save failed");
          });
      });
    }

    if (revList) {
      revList.addEventListener("click", function (ev) {
        var btn = ev.target.closest(".qn-restore");
        if (!btn) return;
        var rid = btn.getAttribute("data-rev");
        if (!rid) return;
        if (!window.confirm("Restore this revision into Quick Notepad?")) return;
        setStatus("saving", "Saving…");
        fetch(urls.restore, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ revision_id: rid, scope: scope }),
        })
          .then(function (r) {
            return r.json();
          })
          .then(function (data) {
            if (!data || !data.ok) {
              setStatus("error", "Save failed");
              return;
            }
            bodyEl.value = data.notepad.content || "";
            applyNotepad(data.notepad);
          })
          .catch(function () {
            setStatus("error", "Save failed");
          });
      });
    }

    if (resizeHandle) {
      var dragging = false;
      function onMove(ev) {
        if (!dragging) return;
        var rect = workspace.getBoundingClientRect();
        var width = Math.round(rect.right - ev.clientX);
        width = Math.max(minW, Math.min(maxW, width));
        workspace.style.setProperty("--qn-width", width + "px");
        workspace.setAttribute("data-qn-width", String(width));
      }
      function onUp() {
        if (!dragging) return;
        dragging = false;
        document.body.classList.remove("qn-resizing");
        queueSave({ panel_width: currentWidth(), panel_open: isOpen() }, true);
      }
      resizeHandle.addEventListener("mousedown", function (ev) {
        if (window.matchMedia("(max-width: 980px)").matches) return;
        dragging = true;
        document.body.classList.add("qn-resizing");
        ev.preventDefault();
      });
      window.addEventListener("mousemove", onMove);
      window.addEventListener("mouseup", onUp);
    }

    window.addEventListener("resize", function () {
      if (backdrop) {
        backdrop.hidden = !(
          isOpen() && window.matchMedia("(max-width: 980px)").matches
        );
      }
    });

    syncEditorChrome();
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", initQuickNotepad);
  } else {
    initQuickNotepad();
  }
})();
