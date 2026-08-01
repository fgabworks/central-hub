/**
 * Quick Notepad — scoped Personal / Work client.
 * Expects a host element #qn-host with data-qn-*-url and data-qn-scope.
 * Markdown mode: Edit/Preview (safe HTML via /api/notebook/preview).
 * Window sizes: normal | expanded | maximized (+ Minimize / Escape).
 */
(function () {
  var SIZE_NORMAL = "normal";
  var SIZE_EXPANDED = "expanded";
  var SIZE_MAXIMIZED = "maximized";

  function normalizePanelSize(value) {
    var raw = String(value || SIZE_NORMAL).toLowerCase();
    if (raw === SIZE_EXPANDED || raw === SIZE_MAXIMIZED) return raw;
    return SIZE_NORMAL;
  }

  /**
   * Escape: Minimize first when Expanded/Maximized; close only from Normal.
   */
  function resolveEscapeAction(sizeMode, open) {
    if (!open) return null;
    if (normalizePanelSize(sizeMode) !== SIZE_NORMAL) return "minimize";
    return "close";
  }

  if (typeof window !== "undefined") {
    window.__qnNormalizePanelSize = normalizePanelSize;
    window.__qnResolveEscapeAction = resolveEscapeAction;
  }

  function initQuickNotepad() {
    var workspace = document.getElementById("qn-host");
    var panel = document.getElementById("qn-panel");
    var bodyEl = document.getElementById("qn-body");
    var formatEl = document.getElementById("qn-format");
    var statusEl = document.getElementById("qn-status");
    var openBtn = document.getElementById("qn-open-btn");
    var closeBtn = document.getElementById("qn-close");
    var expandBtn = document.getElementById("qn-expand");
    var maximizeBtn = document.getElementById("qn-maximize");
    var minimizeBtn = document.getElementById("qn-minimize");
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
      snapshot: workspace.getAttribute("data-qn-snapshot-url") || "",
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
    var sizeMode = normalizePanelSize(
      workspace.getAttribute("data-qn-size") ||
        panel.getAttribute("data-qn-size") ||
        SIZE_NORMAL
    );

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

    function storedWidth() {
      var w = parseInt(workspace.getAttribute("data-qn-width"), 10);
      if (!w || isNaN(w)) {
        w = parseInt(workspace.style.getPropertyValue("--qn-width"), 10) || 320;
      }
      return Math.max(minW, Math.min(maxW, w));
    }

    function captureCaret() {
      return {
        start: bodyEl.selectionStart,
        end: bodyEl.selectionEnd,
        scroll: bodyEl.scrollTop,
        focused: document.activeElement === bodyEl,
      };
    }

    function restoreCaret(caret) {
      if (!caret) return;
      try {
        if (typeof caret.start === "number" && typeof caret.end === "number") {
          bodyEl.setSelectionRange(caret.start, caret.end);
        }
        if (typeof caret.scroll === "number") {
          bodyEl.scrollTop = caret.scroll;
        }
        if (caret.focused) bodyEl.focus();
      } catch (err) {
        /* ignore */
      }
    }

    function syncPageScrollLock() {
      var lock = isOpen() && sizeMode === SIZE_MAXIMIZED;
      document.documentElement.classList.toggle("qn-maximized", lock);
      document.body.classList.toggle("qn-maximized", lock);
    }

    function syncSizeChrome() {
      workspace.setAttribute("data-qn-size", sizeMode);
      panel.setAttribute("data-qn-size", sizeMode);
      panel.classList.toggle("qn-size-normal", sizeMode === SIZE_NORMAL);
      panel.classList.toggle("qn-size-expanded", sizeMode === SIZE_EXPANDED);
      panel.classList.toggle("qn-size-maximized", sizeMode === SIZE_MAXIMIZED);
      workspace.classList.toggle("qn-size-normal", sizeMode === SIZE_NORMAL);
      workspace.classList.toggle("qn-size-expanded", sizeMode === SIZE_EXPANDED);
      workspace.classList.toggle("qn-size-maximized", sizeMode === SIZE_MAXIMIZED);

      if (expandBtn) {
        expandBtn.disabled = sizeMode === SIZE_EXPANDED;
        expandBtn.setAttribute(
          "aria-pressed",
          sizeMode === SIZE_EXPANDED ? "true" : "false"
        );
        expandBtn.hidden = sizeMode === SIZE_MAXIMIZED;
      }
      if (maximizeBtn) {
        maximizeBtn.disabled = sizeMode === SIZE_MAXIMIZED;
        maximizeBtn.setAttribute(
          "aria-pressed",
          sizeMode === SIZE_MAXIMIZED ? "true" : "false"
        );
        maximizeBtn.hidden = sizeMode === SIZE_MAXIMIZED;
      }
      if (minimizeBtn) {
        if (sizeMode !== SIZE_NORMAL) minimizeBtn.removeAttribute("hidden");
        else minimizeBtn.setAttribute("hidden", "");
      }
      if (resizeHandle) {
        resizeHandle.hidden = sizeMode !== SIZE_NORMAL;
        resizeHandle.setAttribute(
          "aria-disabled",
          sizeMode === SIZE_NORMAL ? "false" : "true"
        );
      }
      syncPageScrollLock();
      syncBackdrop();
    }

    function syncBackdrop() {
      if (!backdrop) return;
      var isGlobal = workspace.classList.contains("qn-global-host");
      var narrow = window.matchMedia("(max-width: 980px)").matches;
      // Maximized covers the viewport itself; no dimmed page behind it.
      var needBackdrop =
        isOpen() &&
        sizeMode !== SIZE_MAXIMIZED &&
        (isGlobal || narrow || sizeMode === SIZE_EXPANDED);
      backdrop.hidden = !needBackdrop;
    }

    function setSizeMode(mode, persist) {
      var next = normalizePanelSize(mode);
      var caret = captureCaret();
      sizeMode = next;
      syncSizeChrome();
      restoreCaret(caret);
      if (persist) {
        queueSave(
          {
            panel_size: sizeMode,
            panel_width: storedWidth(),
            panel_open: isOpen(),
          },
          true
        );
      }
    }

    /** Minimize always returns to Normal size (drawer stays open). */
    function minimizeSize(persist) {
      setSizeMode(SIZE_NORMAL, persist);
    }

    function applyOpen(open, persist) {
      workspace.classList.toggle("is-qn-open", !!open);
      workspace.setAttribute("data-qn-open", open ? "1" : "0");
      if (openBtn) openBtn.setAttribute("aria-expanded", open ? "true" : "false");
      var railNotepadBtn = document.getElementById("ar-notepad");
      if (railNotepadBtn) {
        railNotepadBtn.classList.toggle("is-active", !!open);
        railNotepadBtn.setAttribute("aria-expanded", open ? "true" : "false");
      }
      syncSizeChrome();
      if (persist) {
        queueSave(
          {
            panel_open: !!open,
            panel_width: storedWidth(),
            panel_size: sizeMode,
          },
          true
        );
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
      if (pad.panel_size) {
        sizeMode = normalizePanelSize(pad.panel_size);
        syncSizeChrome();
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
          panel_width: storedWidth(),
          panel_size: sizeMode,
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

    if (expandBtn) {
      expandBtn.addEventListener("click", function () {
        if (!isOpen()) applyOpen(true, false);
        setSizeMode(SIZE_EXPANDED, true);
      });
    }
    if (maximizeBtn) {
      maximizeBtn.addEventListener("click", function () {
        if (!isOpen()) applyOpen(true, false);
        setSizeMode(SIZE_MAXIMIZED, true);
      });
    }
    if (minimizeBtn) {
      minimizeBtn.addEventListener("click", function () {
        minimizeSize(true);
      });
    }
    if (closeBtn) {
      closeBtn.addEventListener("click", function () {
        applyOpen(false, true);
        var railFocus = document.getElementById("ar-notepad") || openBtn;
        if (railFocus) railFocus.focus();
      });
    }
    if (openBtn) {
      openBtn.addEventListener("click", function () {
        applyOpen(true, true);
      });
    }
    var railNotepad = document.getElementById("ar-notepad");
    if (railNotepad) {
      railNotepad.addEventListener("click", function () {
        applyOpen(!isOpen(), true);
      });
    }
    if (backdrop) {
      backdrop.addEventListener("click", function () {
        if (sizeMode === SIZE_EXPANDED) {
          minimizeSize(true);
          return;
        }
        applyOpen(false, true);
      });
    }
    document.addEventListener("keydown", function (ev) {
      if (ev.key !== "Escape") return;
      var action = resolveEscapeAction(sizeMode, isOpen());
      if (!action) return;
      ev.preventDefault();
      if (action === "minimize") {
        minimizeSize(true);
        return;
      }
      applyOpen(false, true);
      var railFocus = document.getElementById("ar-notepad") || openBtn;
      if (railFocus) railFocus.focus();
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

    var saveRevisionBtn = document.getElementById("qn-save");
    if (saveRevisionBtn) {
      saveRevisionBtn.addEventListener("click", function () {
        if (!(bodyEl.value || "").trim()) {
          setStatus("error", "Empty");
          setTimeout(function () {
            setStatus("saved", "Saved");
          }, 900);
          return;
        }
        setStatus("saving", "Saving…");
        fetch(urls.snapshot || "/api/notebook/notepad/snapshot", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            scope: scope,
            content: bodyEl.value,
            content_format: formatEl ? formatEl.value : "plain",
          }),
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
            applyNotepad(res.data.notepad);
            setStatus("saved", "Revision saved");
            setTimeout(function () {
              if (statusEl.textContent.indexOf("Revision") === 0) {
                setStatus("saved", "Saved");
              }
            }, 1200);
          })
          .catch(function () {
            setStatus("error", "Save failed");
          });
      });
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
        if (!dragging || sizeMode !== SIZE_NORMAL) return;
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
        queueSave(
          {
            panel_width: storedWidth(),
            panel_open: isOpen(),
            panel_size: sizeMode,
          },
          true
        );
      }
      resizeHandle.addEventListener("mousedown", function (ev) {
        if (sizeMode !== SIZE_NORMAL) return;
        if (window.matchMedia("(max-width: 980px)").matches) return;
        dragging = true;
        document.body.classList.add("qn-resizing");
        ev.preventDefault();
      });
      window.addEventListener("mousemove", onMove);
      window.addEventListener("mouseup", onUp);
    }

    window.addEventListener("resize", function () {
      syncBackdrop();
    });

    syncSizeChrome();
    syncEditorChrome();
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", initQuickNotepad);
  } else {
    initQuickNotepad();
  }
})();
