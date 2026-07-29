/**
 * Find Missing UIDs — client selection store (survives filter/pagination).
 * Does not call DHIS2 or mutate the index; only manages checkbox UI state.
 */
(function (global) {
  "use strict";

  function unique(list) {
    var out = [];
    var seen = Object.create(null);
    (list || []).forEach(function (uid) {
      var key = String(uid || "").trim();
      if (!key || seen[key]) return;
      seen[key] = true;
      out.push(key);
    });
    return out;
  }

  function createSelectionStore(storageKey, storage) {
    var key = String(storageKey || "dhis2-find-missing-selection");
    var store = storage || (typeof sessionStorage !== "undefined" ? sessionStorage : null);
    var selected = Object.create(null);

    function load() {
      selected = Object.create(null);
      if (!store) return;
      try {
        var raw = store.getItem(key);
        if (!raw) return;
        var parsed = JSON.parse(raw);
        if (!Array.isArray(parsed)) return;
        parsed.forEach(function (uid) {
          var id = String(uid || "").trim();
          if (id) selected[id] = true;
        });
      } catch (_err) {
        selected = Object.create(null);
      }
    }

    function save() {
      if (!store) return;
      try {
        store.setItem(key, JSON.stringify(toArray()));
      } catch (_err) {
        /* ignore quota / private mode */
      }
    }

    function toArray() {
      return Object.keys(selected).sort();
    }

    function size() {
      return Object.keys(selected).length;
    }

    function has(uid) {
      return !!selected[String(uid || "").trim()];
    }

    function clear() {
      selected = Object.create(null);
      save();
      return toArray();
    }

    function addMany(uids) {
      unique(uids).forEach(function (uid) {
        selected[uid] = true;
      });
      save();
      return toArray();
    }

    function removeMany(uids) {
      unique(uids).forEach(function (uid) {
        delete selected[uid];
      });
      save();
      return toArray();
    }

    function selectVisible(visibleUids) {
      return addMany(visibleUids);
    }

    function deselectVisible(visibleUids) {
      return removeMany(visibleUids);
    }

    function selectAllFiltered(filteredUids) {
      return addMany(filteredUids);
    }

    function visibleState(visibleUids) {
      var ids = unique(visibleUids);
      if (!ids.length) {
        return { all: false, some: false, none: true };
      }
      var selectedCount = 0;
      ids.forEach(function (uid) {
        if (has(uid)) selectedCount += 1;
      });
      return {
        all: selectedCount === ids.length,
        some: selectedCount > 0 && selectedCount < ids.length,
        none: selectedCount === 0,
        selectedCount: selectedCount,
        visibleCount: ids.length,
      };
    }

    load();
    return {
      load: load,
      save: save,
      clear: clear,
      addMany: addMany,
      removeMany: removeMany,
      selectVisible: selectVisible,
      deselectVisible: deselectVisible,
      selectAllFiltered: selectAllFiltered,
      has: has,
      size: size,
      toArray: toArray,
      visibleState: visibleState,
    };
  }

  function parseJsonAttr(el, name) {
    if (!el) return [];
    try {
      var raw = el.getAttribute(name) || "[]";
      var parsed = JSON.parse(raw);
      return Array.isArray(parsed) ? parsed : [];
    } catch (_err) {
      return [];
    }
  }

  function initFindMissingPage(root) {
    if (!root) return null;
    var scanId = root.getAttribute("data-scan-id") || "none";
    var storageKey = "dhis2-find-missing:" + scanId;
    var store = createSelectionStore(storageKey);
    var filteredUids = parseJsonAttr(root, "data-filtered-uids");
    var visibleUids = parseJsonAttr(root, "data-visible-uids");
    var countEl = root.querySelector("[data-selected-count]");
    var addBtn = root.querySelector("[data-action='preview-selected']");
    var clearBtn = root.querySelector("[data-action='clear-selection']");
    var selectFilteredBtn = root.querySelector("[data-action='select-filtered']");
    var selectVisibleBtn = root.querySelector("[data-action='select-visible']");
    var headerCb = root.querySelector("[data-select-all-visible]");
    var form = root.querySelector("[data-preview-form]");
    var checkboxes = Array.prototype.slice.call(
      root.querySelectorAll('input[type="checkbox"][data-uid-check]')
    );

    function refreshUi() {
      checkboxes.forEach(function (cb) {
        cb.checked = store.has(cb.value);
      });
      var state = store.visibleState(visibleUids);
      if (headerCb) {
        headerCb.checked = state.all;
        headerCb.indeterminate = state.some;
      }
      var n = store.size();
      if (countEl) countEl.textContent = String(n);
      if (addBtn) addBtn.disabled = n < 1;
      root.setAttribute("data-selected-size", String(n));
    }

    function syncHiddenInputs() {
      if (!form) return;
      Array.prototype.slice
        .call(form.querySelectorAll('input[data-selected-uid]'))
        .forEach(function (node) {
          node.parentNode.removeChild(node);
        });
      store.toArray().forEach(function (uid) {
        var input = document.createElement("input");
        input.type = "hidden";
        input.name = "uid";
        input.value = uid;
        input.setAttribute("data-selected-uid", "1");
        form.appendChild(input);
      });
    }

    checkboxes.forEach(function (cb) {
      cb.addEventListener("change", function () {
        if (cb.checked) store.addMany([cb.value]);
        else store.removeMany([cb.value]);
        refreshUi();
      });
    });

    if (headerCb) {
      headerCb.addEventListener("change", function () {
        if (headerCb.checked) store.selectVisible(visibleUids);
        else store.deselectVisible(visibleUids);
        refreshUi();
      });
    }

    if (selectVisibleBtn) {
      selectVisibleBtn.addEventListener("click", function () {
        store.selectVisible(visibleUids);
        refreshUi();
      });
    }

    if (selectFilteredBtn) {
      selectFilteredBtn.addEventListener("click", function () {
        store.selectAllFiltered(filteredUids);
        refreshUi();
      });
    }

    if (clearBtn) {
      clearBtn.addEventListener("click", function () {
        store.clear();
        refreshUi();
      });
    }

    if (form) {
      form.addEventListener("submit", function (event) {
        syncHiddenInputs();
        if (store.size() < 1) {
          event.preventDefault();
          refreshUi();
        }
      });
    }

    refreshUi();
    return { store: store, refreshUi: refreshUi, syncHiddenInputs: syncHiddenInputs };
  }

  global.__dhis2FindMissingSelection = {
    createSelectionStore: createSelectionStore,
    initFindMissingPage: initFindMissingPage,
    unique: unique,
  };

  if (typeof document !== "undefined") {
    document.addEventListener("DOMContentLoaded", function () {
      var root = document.getElementById("find-missing-root");
      if (root) initFindMissingPage(root);
    });
  }
})(typeof window !== "undefined" ? window : globalThis);
