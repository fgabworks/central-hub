/**
 * Repository Workspace Files tab — browse / search / safe edit.
 */
(function () {
  var shell = document.getElementById("rw-shell");
  if (!shell) return;

  var urls = {
    tree: shell.getAttribute("data-tree-url"),
    file: shell.getAttribute("data-file-url"),
    search: shell.getAttribute("data-search-url"),
    previewSave: shell.getAttribute("data-preview-save-url"),
    save: shell.getAttribute("data-save-url"),
    revert: shell.getAttribute("data-revert-url"),
    create: shell.getAttribute("data-create-url"),
    rename: shell.getAttribute("data-rename-url"),
    delete: shell.getAttribute("data-delete-url"),
    open: shell.getAttribute("data-open-url"),
  };

  var treeEl = document.getElementById("rw-tree");
  var resultsEl = document.getElementById("rw-search-results");
  var editor = document.getElementById("rw-editor");
  var linenums = document.getElementById("rw-linenums");
  var pathLabel = document.getElementById("rw-path-label");
  var fileInfo = document.getElementById("rw-file-info");
  var statusEl = document.getElementById("rw-status");
  var saveBtn = document.getElementById("rw-save");
  var revertBtn = document.getElementById("rw-revert");
  var renameBtn = document.getElementById("rw-rename");
  var deleteBtn = document.getElementById("rw-delete");
  var diffDialog = document.getElementById("rw-diff-dialog");
  var diffBody = document.getElementById("rw-diff-body");

  var state = {
    path: "",
    original: "",
    dirty: false,
    editable: false,
  };

  function setStatus(msg) {
    statusEl.textContent = msg;
  }

  function updateLineNumbers() {
    var n = editor.value.split("\n").length;
    var html = "";
    for (var i = 1; i <= n; i++) html += i + "\n";
    linenums.textContent = html;
  }

  function setDirty(flag) {
    state.dirty = !!flag;
    saveBtn.disabled = !state.editable || !state.dirty;
    revertBtn.disabled = !state.editable || !state.dirty;
    document.title = (state.dirty ? "• " : "") + (state.path || "Files");
  }

  function renderTree(nodes, depth) {
    depth = depth || 0;
    var html = "";
    (nodes || []).forEach(function (node) {
      if (node.type === "dir") {
        html +=
          '<details class="rw-dir" style="padding-left:' +
          depth * 0.75 +
          'rem"' +
          (depth < 1 ? " open" : "") +
          "><summary>" +
          escapeHtml(node.name) +
          "</summary>" +
          renderTree(node.children || [], depth + 1) +
          "</details>";
      } else {
        var st = node.git_status && node.git_status !== "clean" ? " [" + node.git_status + "]" : "";
        html +=
          '<button type="button" class="rw-file" style="padding-left:' +
          (depth * 0.75 + 0.35) +
          'rem" data-path="' +
          escapeAttr(node.path) +
          '">' +
          escapeHtml(node.name) +
          '<span class="muted">' +
          escapeHtml(st) +
          "</span></button>";
      }
    });
    return html;
  }

  function escapeHtml(s) {
    return String(s)
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;");
  }
  function escapeAttr(s) {
    return escapeHtml(s).replace(/'/g, "&#39;");
  }

  function loadTree() {
    setStatus("Loading tree…");
    return fetch(urls.tree)
      .then(function (r) {
        return r.json();
      })
      .then(function (data) {
        if (!data.ok) {
          treeEl.innerHTML = '<p class="muted">' + escapeHtml(data.error || "Failed") + "</p>";
          return;
        }
        treeEl.innerHTML = renderTree(data.entries || []);
        treeEl.querySelectorAll(".rw-file").forEach(function (btn) {
          btn.addEventListener("click", function () {
            openFile(btn.getAttribute("data-path"));
          });
        });
        setStatus(data.truncated ? "Tree loaded (truncated)" : "Tree loaded");
      })
      .catch(function () {
        setStatus("Tree load failed");
      });
  }

  function openFile(path) {
    if (state.dirty && !window.confirm("Discard unsaved changes?")) return;
    setStatus("Loading " + path + "…");
    fetch(urls.file + "?path=" + encodeURIComponent(path))
      .then(function (r) {
        return r.json();
      })
      .then(function (data) {
        if (!data.ok) {
          setStatus(data.error || "Unable to open");
          return;
        }
        var file = data.file;
        state.path = file.path;
        state.original = file.content || "";
        state.editable = !!file.editable && !file.binary && !file.error;
        pathLabel.textContent = file.path;
        fileInfo.textContent = [
          file.language || "",
          file.size != null ? file.size + " B" : "",
          file.modified_at ? String(file.modified_at).slice(0, 19).replace("T", " ") : "",
          file.git_status || "",
        ]
          .filter(Boolean)
          .join(" · ");
        editor.value = file.content || file.error || "";
        editor.readOnly = !state.editable;
        renameBtn.disabled = !state.editable;
        deleteBtn.disabled = !file.path;
        setDirty(false);
        updateLineNumbers();
        setStatus(file.error || (state.editable ? "Editable" : "Preview only"));
      });
  }

  function runSearch() {
    var fq = document.getElementById("rw-filename-q").value.trim();
    var cq = document.getElementById("rw-content-q").value.trim();
    var mode = cq ? "content" : "filename";
    var q = cq || fq;
    if (!q) {
      resultsEl.hidden = true;
      return;
    }
    fetch(urls.search + "?mode=" + mode + "&q=" + encodeURIComponent(q))
      .then(function (r) {
        return r.json();
      })
      .then(function (data) {
        if (!data.ok) {
          setStatus(data.error || "Search failed");
          return;
        }
        var html = "<div class='rw-pane-head'>Results (" + data.count + ")</div>";
        (data.matches || []).forEach(function (m) {
          html +=
            '<button type="button" class="rw-file" data-path="' +
            escapeAttr(m.path) +
            '">' +
            escapeHtml(m.path) +
            (m.line ? " :" + m.line : "") +
            (m.snippet ? '<div class="muted">' + escapeHtml(m.snippet) + "</div>" : "") +
            "</button>";
        });
        resultsEl.innerHTML = html || "<p class='muted'>No matches</p>";
        resultsEl.hidden = false;
        resultsEl.querySelectorAll(".rw-file").forEach(function (btn) {
          btn.addEventListener("click", function () {
            openFile(btn.getAttribute("data-path"));
          });
        });
      });
  }

  function openExternal(target) {
    fetch(urls.open, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ target: target, path: state.path || null }),
    })
      .then(function (r) {
        return r.json();
      })
      .then(function (data) {
        setStatus(data.ok ? "Opened in " + target : data.error || "Open failed");
      });
  }

  function requestSave() {
    if (!state.editable || !state.path) return;
    fetch(urls.previewSave, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ path: state.path, content: editor.value }),
    })
      .then(function (r) {
        return r.json();
      })
      .then(function (data) {
        if (!data.ok) {
          setStatus(data.error || "Diff preview failed");
          return;
        }
        if (!data.changed) {
          setStatus("No changes to save");
          setDirty(false);
          return;
        }
        diffBody.textContent = data.diff || "(empty diff)";
        diffDialog.showModal();
      });
  }

  function confirmSave() {
    fetch(urls.save, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ path: state.path, content: editor.value, confirm: true }),
    })
      .then(function (r) {
        return r.json();
      })
      .then(function (data) {
        if (!data.ok) {
          setStatus(data.error || "Save failed");
          return;
        }
        state.original = editor.value;
        setDirty(false);
        setStatus("Saved");
        loadTree();
      });
  }

  editor.addEventListener("input", function () {
    if (!state.editable) return;
    setDirty(editor.value !== state.original);
    updateLineNumbers();
  });
  editor.addEventListener("scroll", function () {
    linenums.scrollTop = editor.scrollTop;
  });

  document.addEventListener("keydown", function (ev) {
    if ((ev.ctrlKey || ev.metaKey) && ev.key.toLowerCase() === "s") {
      if (state.editable && state.dirty) {
        ev.preventDefault();
        requestSave();
      }
    }
  });

  window.addEventListener("beforeunload", function (ev) {
    if (state.dirty) {
      ev.preventDefault();
      ev.returnValue = "";
    }
  });

  document.getElementById("rw-search-btn").addEventListener("click", runSearch);
  document.getElementById("rw-save").addEventListener("click", requestSave);
  document.getElementById("rw-diff-confirm").addEventListener("click", function (ev) {
    ev.preventDefault();
    diffDialog.close();
    confirmSave();
  });
  document.getElementById("rw-revert").addEventListener("click", function () {
    if (!state.path) return;
    fetch(urls.revert, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ path: state.path }),
    })
      .then(function (r) {
        return r.json();
      })
      .then(function (data) {
        if (!data.ok) {
          setStatus(data.error || "Revert failed");
          return;
        }
        editor.value = data.content || "";
        state.original = editor.value;
        setDirty(false);
        updateLineNumbers();
        setStatus("Reverted to last saved content");
      });
  });
  document.getElementById("rw-new-file").addEventListener("click", function () {
    var path = window.prompt("New file path (relative to repo root):");
    if (!path) return;
    if (!window.confirm("Create " + path + "?")) return;
    fetch(urls.create, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ path: path, content: "", confirm: true }),
    })
      .then(function (r) {
        return r.json();
      })
      .then(function (data) {
        if (!data.ok) {
          setStatus(data.error || "Create failed");
          return;
        }
        loadTree().then(function () {
          openFile(data.path);
        });
      });
  });
  document.getElementById("rw-rename").addEventListener("click", function () {
    if (!state.path) return;
    var next = window.prompt("Rename to:", state.path);
    if (!next || next === state.path) return;
    if (!window.confirm("Rename " + state.path + " → " + next + "?")) return;
    fetch(urls.rename, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ path: state.path, new_path: next, confirm: true }),
    })
      .then(function (r) {
        return r.json();
      })
      .then(function (data) {
        if (!data.ok) {
          setStatus(data.error || "Rename failed");
          return;
        }
        loadTree().then(function () {
          openFile(data.to);
        });
      });
  });
  document.getElementById("rw-delete").addEventListener("click", function () {
    if (!state.path) return;
    if (!window.confirm("Delete " + state.path + " permanently?")) return;
    fetch(urls.delete, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ path: state.path, confirm: true }),
    })
      .then(function (r) {
        return r.json();
      })
      .then(function (data) {
        if (!data.ok) {
          setStatus(data.error || "Delete failed");
          return;
        }
        state.path = "";
        editor.value = "";
        setDirty(false);
        pathLabel.textContent = "Select a file";
        loadTree();
        setStatus("Deleted");
      });
  });
  document.getElementById("rw-open-code").addEventListener("click", function () {
    openExternal("vscode");
  });
  document.getElementById("rw-open-cursor").addEventListener("click", function () {
    openExternal("cursor");
  });
  document.getElementById("rw-open-explorer").addEventListener("click", function () {
    openExternal("explorer");
  });

  document.getElementById("rw-replace-one").addEventListener("click", function () {
    var find = document.getElementById("rw-find").value;
    var rep = document.getElementById("rw-replace").value;
    if (!find || editor.readOnly) return;
    var idx = editor.value.indexOf(find, editor.selectionStart);
    if (idx < 0) idx = editor.value.indexOf(find);
    if (idx < 0) {
      setStatus("No match");
      return;
    }
    editor.value = editor.value.slice(0, idx) + rep + editor.value.slice(idx + find.length);
    setDirty(editor.value !== state.original);
    updateLineNumbers();
  });
  document.getElementById("rw-replace-all").addEventListener("click", function () {
    var find = document.getElementById("rw-find").value;
    var rep = document.getElementById("rw-replace").value;
    if (!find || editor.readOnly) return;
    if (editor.value.indexOf(find) < 0) {
      setStatus("No match");
      return;
    }
    editor.value = editor.value.split(find).join(rep);
    setDirty(true);
    updateLineNumbers();
  });

  // Resize splitter
  var resize = document.getElementById("rw-resize");
  var treePane = document.getElementById("rw-tree-pane");
  var dragging = false;
  resize.addEventListener("mousedown", function (ev) {
    dragging = true;
    ev.preventDefault();
  });
  window.addEventListener("mousemove", function (ev) {
    if (!dragging) return;
    var rect = shell.getBoundingClientRect();
    var width = Math.max(180, Math.min(480, ev.clientX - rect.left));
    treePane.style.flex = "0 0 " + width + "px";
  });
  window.addEventListener("mouseup", function () {
    dragging = false;
  });

  loadTree().then(function () {
    var initial = shell.getAttribute("data-initial-path");
    if (initial) openFile(initial);
  });
})();
