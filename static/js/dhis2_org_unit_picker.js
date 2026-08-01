/**
 * Central Hub organisation-unit picker — Live Processing cascade + search.
 * Levels: Region → Province → Municipality/City → Barangay
 * Data: GET /api/dhis2/reports/org-units (level / parent_id / q).
 * Stage maintenance: show clear message, serve cached metadata with synced_at,
 * Retry + Refresh Organisation Units are user-initiated only (no Stage polling loop).
 * Local SQLite cache (env-scoped) serves hierarchy/search immediately; DHIS2 refreshes in background.
 * Stage/Live caches stay isolated — never mix Live rows into Stage.
 */
(function (global) {
  "use strict";

  var OU_LEVELS = [
    { id: "region", label: "Region", level: 2, limit: 50 },
    { id: "province", label: "Province", level: 3, limit: 100 },
    { id: "municipality", label: "Municipality/City", level: 4, limit: 200 },
    { id: "barangay", label: "Barangay", level: 5, limit: 250 },
  ];
  var FETCH_MS = 8000;
  var SEARCH_DEBOUNCE_MS = 280;
  var MAINTENANCE_MSG = "Stage is temporarily unavailable due to maintenance.";

  function isValidUid(value) {
    return /^[A-Za-z0-9]{11}$/.test(String(value || "").trim());
  }

  function createPicker(options) {
    var opts = options || {};
    var root = opts.root;
    var hiddenEl = opts.hiddenEl;
    var pathEl = opts.pathEl;
    var chipRow = opts.chipRow;
    var chipLabel = opts.chipLabel;
    var clearBtn = opts.clearBtn;
    var retryBtn = opts.retryBtn;
    var refreshMetaBtn = opts.refreshMetaBtn;
    var errorEl = opts.errorEl;
    var syncEl = opts.syncEl;
    var searchEl = opts.searchEl;
    var searchResultsEl = opts.searchResultsEl;
    var apiUrl = opts.apiUrl || "";
    var refreshUrl = opts.refreshUrl || "";
    if (!refreshUrl && apiUrl) {
      refreshUrl = apiUrl.replace(/\/?(\?.*)?$/, "") + "/refresh";
    }
    var getEnvironment = opts.getEnvironment || function () { return "stage"; };
    var storagePrefix = opts.storagePrefix || "centralhub.ou.";
    var onChange = opts.onChange || function () {};
    var onEnvironmentStatus = opts.onEnvironmentStatus || function () {};
    var lazyRoots = opts.lazyRoots !== false;
    var inFlight = {};
    var abortControllers = {};
    var cache = {};
    var selects = [];
    var committed = { uid: "", name: "", path: "" };
    var selectionSource = ""; // "cascade" | "search" | ""
    var lastFailed = null;
    var rootsLoaded = false;
    var rootsLoading = false;
    var searchTimer = null;
    var lastSyncedAt = {};

    function env() {
      return getEnvironment() || "stage";
    }

    function setError(msg, canRetry) {
      if (!errorEl) return;
      if (!msg) {
        errorEl.hidden = true;
        errorEl.textContent = "";
        if (retryBtn) retryBtn.hidden = true;
        return;
      }
      errorEl.hidden = false;
      errorEl.textContent = msg;
      if (retryBtn) retryBtn.hidden = !canRetry;
    }

    function setSyncLabel(syncedAt, cacheState, maintenance) {
      if (!syncEl) return;
      if (!syncedAt && !maintenance) {
        syncEl.hidden = true;
        syncEl.textContent = "";
        return;
      }
      var parts = [];
      if (maintenance) parts.push("Stage maintenance");
      if (syncedAt) {
        parts.push(
          (cacheState === "stale" ? "Cached (stale) since " : "Last synced ") + syncedAt
        );
      } else if (maintenance) {
        parts.push("No cached organisation-unit metadata for Stage");
      }
      syncEl.textContent = parts.join(" · ");
      syncEl.hidden = !parts.length;
    }

    function cacheGet(key) {
      return (cache[env()] || {})[key];
    }

    function cacheSet(key, rows) {
      if (!cache[env()]) cache[env()] = {};
      cache[env()][key] = rows;
    }

    function clearEnvCache() {
      cache[env()] = {};
      Object.keys(inFlight).forEach(function (k) {
        if (k.indexOf(env() + ":") === 0) {
          if (abortControllers[k]) {
            try { abortControllers[k].abort(); } catch (e) {}
            delete abortControllers[k];
          }
          delete inFlight[k];
        }
      });
    }

    function dedupeFetch(key, url) {
      if (inFlight[key]) return inFlight[key];
      if (abortControllers[key]) {
        try { abortControllers[key].abort(); } catch (e) {}
      }
      var ctrl = typeof AbortController !== "undefined" ? new AbortController() : null;
      if (ctrl) abortControllers[key] = ctrl;
      var timer = null;
      if (ctrl) {
        timer = setTimeout(function () {
          try { ctrl.abort(); } catch (e) {}
        }, FETCH_MS);
      }
      var p = fetch(url, {
        credentials: "same-origin",
        signal: ctrl ? ctrl.signal : undefined,
      })
        .then(function (r) {
          return r.json().then(function (data) {
            if (!r.ok && (!data || data.ok !== false)) {
              return {
                ok: false,
                code: r.status === 503 ? "maintenance" : "unavailable",
                error:
                  (data && data.error) ||
                  ("Organisation unit request failed (" + r.status + ")."),
              };
            }
            return data;
          });
        })
        .finally(function () {
          if (timer) clearTimeout(timer);
          delete inFlight[key];
          delete abortControllers[key];
        });
      inFlight[key] = p;
      return p;
    }

    function applyAvailability(data) {
      var maintenance =
        !!(data && (data.maintenance || data.code === "maintenance")) ||
        (env() === "stage" &&
          data &&
          data.environment_status === "maintenance");
      var msg =
        (data && (data.maintenance_message || (data.code === "maintenance" && data.error))) ||
        (maintenance ? MAINTENANCE_MSG : "");
      var synced = (data && data.synced_at) || lastSyncedAt[env()] || "";
      if (data && data.synced_at) lastSyncedAt[env()] = data.synced_at;
      setSyncLabel(synced, data && data.cache, maintenance);
      if (refreshMetaBtn) refreshMetaBtn.hidden = false;
      onEnvironmentStatus({
        environment: env(),
        maintenance: maintenance,
        message: msg || null,
        synced_at: synced || null,
        cache: data && data.cache,
      });
      return { maintenance: maintenance, message: msg };
    }

    function cascadeSelection() {
      var parts = [];
      var uid = "";
      var name = "";
      selects.forEach(function (sel) {
        if (!sel || !sel.value) return;
        uid = sel.value;
        name = (sel.selectedOptions[0] && sel.selectedOptions[0].textContent) || "";
        if (name) parts.push(name);
      });
      return { uid: uid, name: name, path: parts.join(" › ") };
    }

    function commitSelection(uid, name, path, source) {
      var id = String(uid || "").trim();
      if (!isValidUid(id)) {
        committed = { uid: "", name: "", path: "" };
        selectionSource = "";
      } else {
        committed = {
          uid: id,
          name: name || id,
          path: path || name || id,
        };
        if (source) selectionSource = source;
      }
      if (hiddenEl) hiddenEl.value = committed.uid || "";
      return committed;
    }

    function selectedPath() {
      var cascade = cascadeSelection();
      if (isValidUid(cascade.uid)) {
        return cascade;
      }
      if (selectionSource === "search" && isValidUid(committed.uid)) {
        return {
          uid: committed.uid,
          name: committed.name || committed.uid,
          path: committed.path || committed.name || committed.uid,
        };
      }
      var hidden = hiddenEl && String(hiddenEl.value || "").trim();
      if (isValidUid(hidden) && selectionSource === "search") {
        var label =
          (chipLabel && chipLabel.textContent) ||
          (pathEl && pathEl.textContent) ||
          hidden;
        return { uid: hidden, name: label, path: label };
      }
      return { uid: "", name: "", path: "" };
    }

    function selectedUid() {
      var ou = selectedPath();
      return isValidUid(ou.uid) ? ou.uid : "";
    }

    function syncSelection() {
      var cascade = cascadeSelection();
      var ou;
      if (isValidUid(cascade.uid)) {
        ou = commitSelection(cascade.uid, cascade.name, cascade.path, "cascade");
      } else if (selectionSource === "search" && isValidUid(committed.uid)) {
        // Keep search selection while cascade resolve is best-effort / empty.
        ou = committed;
        if (hiddenEl) hiddenEl.value = ou.uid;
      } else {
        ou = commitSelection("", "", "", "");
      }
      if (pathEl) {
        pathEl.textContent = ou.path || "";
        pathEl.hidden = !ou.path;
      }
      if (chipLabel) {
        if (!ou.uid) {
          chipLabel.textContent = "";
        } else {
          // Compact path chip: "Region VII › Cebu › …" (no UID suffix).
          chipLabel.textContent = ou.path || ou.name || ou.uid;
        }
      }
      if (chipRow) chipRow.hidden = !ou.uid;
      if (ou.uid) setError("");
      onChange(ou.uid || "");
      if (ou.uid) {
        rememberFrequent({ id: ou.uid, uid: ou.uid, name: ou.name, path: ou.path });
      }
      try {
        localStorage.setItem(
          storagePrefix + "cascade." + env(),
          JSON.stringify({
            region: selects[0] && selects[0].value,
            province: selects[1] && selects[1].value,
            municipality: selects[2] && selects[2].value,
            barangay: selects[3] && selects[3].value,
            uid: ou.uid || "",
            path: ou.path,
            at: Date.now(),
          })
        );
      } catch (e) {}
    }

    function resetFrom(index) {
      for (var i = index; i < selects.length; i++) {
        var sel = selects[i];
        if (!sel) continue;
        sel.innerHTML = '<option value="">' + OU_LEVELS[i].label + "…</option>";
        sel.disabled = i !== 0;
      }
    }

    function fillSelect(sel, label, rows, keepDisabled) {
      sel.innerHTML = '<option value="">' + label + "…</option>";
      (rows || []).forEach(function (ou) {
        var id = ou.id || ou.uid || "";
        if (!id) return;
        var opt = document.createElement("option");
        opt.value = id;
        opt.textContent = ou.name || id;
        opt.dataset.hasChildren = ou.has_children ? "1" : "0";
        if (ou.level != null) opt.dataset.level = String(ou.level);
        sel.appendChild(opt);
      });
      sel.disabled = !!keepDisabled || !(rows || []).length;
    }

    function buildUrl(params) {
      var qs = Object.keys(params)
        .filter(function (k) { return params[k] != null && params[k] !== ""; })
        .map(function (k) {
          return encodeURIComponent(k) + "=" + encodeURIComponent(String(params[k]));
        })
        .join("&");
      return apiUrl + "?" + qs;
    }

    function handleFetchError(data, label, sel) {
      var avail = applyAvailability(data || {});
      if (sel) fillSelect(sel, label + " (failed)", [], true);
      if (avail.maintenance) {
        setError(avail.message || MAINTENANCE_MSG, true);
        return [];
      }
      var err = (data && data.error) || "Organisation unit load failed.";
      if (/timed out/i.test(err)) {
        err = "DHIS2 " + env() + " timed out. Switch environment or Retry.";
      }
      setError(err, true);
      return [];
    }

    function loadOptions(index, parentUid, refresh) {
      var sel = selects[index];
      if (!sel) return Promise.resolve();
      var meta = OU_LEVELS[index];
      var label = meta.label;
      sel.innerHTML = '<option value="">Loading…</option>';
      sel.disabled = true;
      var key =
        parentUid
          ? "parent:" + parentUid
          : "level:" + meta.level;
      if (!refresh) {
        var cached = cacheGet(key);
        if (cached) {
          fillSelect(sel, label, cached, false);
          lastFailed = null;
          return Promise.resolve(cached);
        }
      } else {
        cacheSet(key, null);
        delete (cache[env()] || {})[key];
      }
      var url = buildUrl({
        environment: env(),
        limit: meta.limit,
        parent_id: parentUid || "",
        level: parentUid ? "" : meta.level,
        refresh: refresh ? "1" : "",
      });
      var reqKey = env() + ":" + key + (refresh ? ":refresh" : "");
      lastFailed = { index: index, parentUid: parentUid || null };
      return dedupeFetch(reqKey, url)
        .then(function (data) {
          applyAvailability(data || {});
          if (data && data.ok === false) {
            return handleFetchError(data, label, sel);
          }
          var rows = (data && (data.org_units || data.orgunits)) || [];
          cacheSet(key, rows);
          fillSelect(sel, label, rows, false);
          lastFailed = null;
          if (data && data.maintenance) {
            setError(data.maintenance_message || MAINTENANCE_MSG, true);
          } else {
            setError(rows.length ? "" : "No " + label.toLowerCase() + " options found.");
          }
          return rows;
        })
        .catch(function (err) {
          var aborted = err && (err.name === "AbortError" || err.message === "The user aborted a request.");
          fillSelect(sel, label + " (failed)", [], true);
          setError(
            aborted
              ? "Organisation unit load timed out. Try again."
              : "Organisation unit load failed. Try again.",
            true
          );
          return [];
        });
    }

    function retryLast() {
      if (!lastFailed || lastFailed.index === 0) {
        rootsLoaded = false;
        ensureRoots(true, false).then(function () {
          syncSelection();
        });
        return;
      }
      loadOptions(lastFailed.index, lastFailed.parentUid, false).then(function () {
        syncSelection();
      });
    }

    function refreshMetadata() {
      rootsLoaded = false;
      clearEnvCache();
      resetFrom(0);
      setError("", false);
      if (syncEl) {
        syncEl.hidden = false;
        syncEl.textContent = "Refreshing organisation units…";
      }
      var startRefresh = Promise.resolve({ ok: true });
      if (refreshUrl) {
        var qs = "?environment=" + encodeURIComponent(env()) + "&level=2";
        startRefresh = fetch(refreshUrl + qs, {
          method: "POST",
          credentials: "same-origin",
          headers: { Accept: "application/json" },
        })
          .then(function (r) {
            return r.json().catch(function () {
              return { ok: false };
            });
          })
          .catch(function () {
            return { ok: false };
          });
      }
      return startRefresh.then(function (meta) {
        if (meta && meta.maintenance) {
          setError(meta.maintenance_message || MAINTENANCE_MSG, true);
        } else if (meta && meta.inflight && !meta.started) {
          if (syncEl) {
            syncEl.hidden = false;
            syncEl.textContent = meta.message || "Organisation unit sync already running.";
          }
        }
        // Reload from local cache immediately — do not block the page on DHIS2.
        return ensureRoots(true, false).then(function () {
          syncSelection();
        });
      });
    }

    function ensureRoots(force, refresh) {
      if (rootsLoaded && !force && !refresh) return Promise.resolve();
      if (rootsLoading && !force && !refresh) {
        return inFlight[env() + ":level:2"] || Promise.resolve();
      }
      rootsLoading = true;
      return loadOptions(0, null, !!refresh).then(function (rows) {
        rootsLoaded = !!(rows && rows.length);
        rootsLoading = false;
        return rows;
      }).catch(function (err) {
        rootsLoading = false;
        throw err;
      });
    }

    function onLevelChange(index) {
      var sel = selects[index];
      var uid = sel && sel.value;
      var opt = sel && sel.selectedOptions && sel.selectedOptions[0];
      resetFrom(index + 1);
      if (!uid) {
        // Explicit cascade clear — do not keep a prior search UID.
        if (index === 0) commitSelection("", "", "", "");
        else {
          var parent = cascadeSelection();
          if (isValidUid(parent.uid)) {
            commitSelection(parent.uid, parent.name, parent.path, "cascade");
          } else {
            commitSelection("", "", "", "");
          }
        }
        syncSelection();
        return;
      }
      var hasChildren = !opt || opt.dataset.hasChildren !== "0";
      if (hasChildren && index + 1 < selects.length) {
        loadOptions(index + 1, uid, false).then(function () {
          syncSelection();
        });
      } else {
        syncSelection();
      }
    }

    function clearSelection() {
      commitSelection("", "", "", "");
      if (selects[0]) selects[0].value = "";
      resetFrom(1);
      if (searchEl) searchEl.value = "";
      hideSearchResults();
      syncSelection();
      setError("");
    }

    function onEnvironmentChange() {
      clearEnvCache();
      lastFailed = null;
      rootsLoaded = false;
      rootsLoading = false;
      hideSearchResults();
      if (searchEl) searchEl.value = "";
      commitSelection("", "", "", "");
      resetFrom(0);
      if (!lazyRoots) {
        ensureRoots(true, false).then(function () { syncSelection(); });
      } else {
        if (selects[0]) {
          selects[0].innerHTML = '<option value="">Region…</option>';
          selects[0].disabled = false;
        }
        setSyncLabel(lastSyncedAt[env()] || "", "", false);
        syncSelection();
      }
    }

    function hideSearchResults() {
      if (!searchResultsEl) return;
      searchResultsEl.hidden = true;
      searchResultsEl.innerHTML = "";
      if (searchEl) searchEl.setAttribute("aria-expanded", "false");
    }

    function renderSearchResults(rows) {
      if (!searchResultsEl) return;
      searchResultsEl.innerHTML = "";
      if (!(rows || []).length) {
        var empty = document.createElement("button");
        empty.type = "button";
        empty.disabled = true;
        empty.textContent = "No matches";
        searchResultsEl.appendChild(empty);
        searchResultsEl.hidden = false;
        return;
      }
      rows.forEach(function (ou) {
        var id = ou.id || ou.uid || "";
        if (!id) return;
        var btn = document.createElement("button");
        btn.type = "button";
        btn.setAttribute("role", "option");
        btn.dataset.uid = id;
        var title = document.createElement("span");
        title.textContent = ou.name || id;
        btn.appendChild(title);
        var meta = document.createElement("span");
        meta.className = "hcsc-ou-search-meta";
        meta.textContent = [ou.code, ou.path, id].filter(Boolean).join(" · ");
        btn.appendChild(meta);
        btn.addEventListener("click", function () {
          selectSearchHit(ou);
        });
        searchResultsEl.appendChild(btn);
      });
      searchResultsEl.hidden = false;
      if (searchEl) searchEl.setAttribute("aria-expanded", "true");
    }

    function rememberFrequent(ou) {
      if (!ou || !isValidUid(ou.uid || ou.id)) return;
      try {
        var key = storagePrefix + "frequent." + env();
        var list = JSON.parse(localStorage.getItem(key) || "[]");
        if (!Array.isArray(list)) list = [];
        var id = ou.uid || ou.id;
        list = list.filter(function (r) { return r && r.id !== id; });
        list.unshift({
          id: id,
          name: ou.name || id,
          path: ou.path || "",
          at: Date.now(),
        });
        localStorage.setItem(key, JSON.stringify(list.slice(0, 8)));
      } catch (e) {}
    }

    function recentAndFrequent() {
      try {
        var key = storagePrefix + "frequent." + env();
        var list = JSON.parse(localStorage.getItem(key) || "[]");
        return Array.isArray(list) ? list : [];
      } catch (e) {
        return [];
      }
    }

    function selectSearchHit(ou) {
      var id = ou.id || ou.uid || "";
      if (!isValidUid(id)) return;
      hideSearchResults();
      if (searchEl) searchEl.value = ou.name || id;
      var pathLabel = ou.path_label || ou.path || ou.name || id;
      rememberFrequent({ id: id, uid: id, name: ou.name, path: pathLabel });
      // Keep committed UID authoritative even while cascade resolve is best-effort.
      commitSelection(id, ou.name || id, pathLabel, "search");
      resetFrom(0);
      if (selects[0]) {
        selects[0].innerHTML = '<option value="">Region…</option>';
        selects[0].disabled = false;
      }
      syncSelection();
      rootsLoaded = false;
      // Resolve cascade from path labels when possible (best-effort).
      resolveHierarchyFromPath(pathLabel, id);
    }

    function resolveHierarchyFromPath(pathLabel, leafUid) {
      var parts = String(pathLabel || "")
        .split(/\s*[›>/]\s*/)
        .map(function (p) { return p.trim(); })
        .filter(Boolean);
      if (!parts.length) {
        if (leafUid) commitSelection(leafUid, committed.name, committed.path, "search");
        syncSelection();
        return;
      }
      ensureRoots(false, false).then(function () {
        var chain = Promise.resolve();
        parts.forEach(function (name, idx) {
          if (idx >= selects.length) return;
          chain = chain.then(function () {
            var sel = selects[idx];
            if (!sel) return;
            var match = Array.prototype.find.call(sel.options || [], function (opt) {
              return opt.value && opt.textContent === name;
            });
            if (!match && idx === parts.length - 1 && leafUid) {
              match = Array.prototype.find.call(sel.options || [], function (opt) {
                return opt.value === leafUid;
              });
            }
            if (!match) return;
            sel.value = match.value;
            if (idx + 1 < selects.length && match.dataset.hasChildren !== "0") {
              return loadOptions(idx + 1, match.value, false);
            }
          });
        });
        return chain.then(function () {
          // Never drop the search-selected UID if cascade resolve was partial.
          if (leafUid) {
            var cascade = cascadeSelection();
            if (isValidUid(cascade.uid) && cascade.uid === leafUid) {
              commitSelection(cascade.uid, cascade.name, cascade.path, "cascade");
            } else {
              commitSelection(
                leafUid,
                committed.name || leafUid,
                committed.path || pathLabel || leafUid,
                "search"
              );
            }
          }
          syncSelection();
        });
      });
    }

    function runSearch(q, refresh) {
      var needle = String(q || "").trim();
      if (!needle) {
        var recent = recentAndFrequent();
        if (recent.length) {
          renderSearchResults(
            recent.map(function (r) {
              return {
                id: r.id,
                name: r.name,
                path: r.path || "Recent / frequent",
                has_children: false,
              };
            })
          );
        } else {
          hideSearchResults();
        }
        return;
      }
      if (needle.length < 2 && !(needle.length === 11 && isValidUid(needle))) {
        hideSearchResults();
        return;
      }
      var key = "q:" + needle.toLowerCase();
      if (!refresh) {
        var cached = cacheGet(key);
        if (cached) {
          renderSearchResults(cached);
          return;
        }
      }
      var url = buildUrl({
        environment: env(),
        q: needle,
        limit: 25,
        refresh: refresh ? "1" : "",
      });
      dedupeFetch(env() + ":" + key + (refresh ? ":refresh" : ""), url).then(function (data) {
        applyAvailability(data || {});
        if (data && data.ok === false) {
          handleFetchError(data, "Search", null);
          hideSearchResults();
          return;
        }
        var rows = (data && (data.org_units || data.orgunits)) || [];
        cacheSet(key, rows);
        renderSearchResults(rows);
        if (data && data.maintenance) {
          setError(data.maintenance_message || MAINTENANCE_MSG, true);
          if (refreshMetaBtn) refreshMetaBtn.hidden = false;
        }
      }).catch(function () {
        hideSearchResults();
        setError("Organisation unit search failed. Try again.", true);
      });
    }

    OU_LEVELS.forEach(function (lvl, i) {
      var el =
        (opts.selects && opts.selects[lvl.id]) ||
        (root && root.querySelector('[data-ou-level="' + i + '"]')) ||
        document.getElementById((opts.idPrefix || "hcsc-ou-") + lvl.id);
      selects[i] = el;
      if (el) {
        el.addEventListener("change", function () {
          onLevelChange(i);
        });
        if (i === 0 && lazyRoots) {
          el.addEventListener("focus", function () { ensureRoots(false, false); });
          el.addEventListener("mousedown", function () { ensureRoots(false, false); });
        }
      }
    });

    if (clearBtn) {
      clearBtn.addEventListener("click", function () {
        clearSelection();
        if (selects[0] && selects[0].options.length <= 1) {
          ensureRoots(true, false);
        }
      });
    }
    if (retryBtn) {
      retryBtn.hidden = true;
      retryBtn.addEventListener("click", function () {
        retryLast();
      });
    }
    if (refreshMetaBtn) {
      refreshMetaBtn.addEventListener("click", function () {
        refreshMetadata();
      });
    }
    if (searchEl) {
      searchEl.addEventListener("input", function () {
        clearTimeout(searchTimer);
        searchTimer = setTimeout(function () {
          runSearch(searchEl.value, false);
        }, SEARCH_DEBOUNCE_MS);
      });
      searchEl.addEventListener("focus", function () {
        if (!String(searchEl.value || "").trim()) runSearch("", false);
      });
      searchEl.addEventListener("keydown", function (ev) {
        if (ev.key === "Escape") {
          hideSearchResults();
          return;
        }
        // Prevent Enter from submitting the HCSC filter form.
        if (ev.key === "Enter") {
          ev.preventDefault();
          var first =
            searchResultsEl &&
            searchResultsEl.querySelector('button[role="option"]:not([disabled])');
          if (first) first.click();
        }
      });
      document.addEventListener("click", function (ev) {
        var t = ev.target;
        if (root && root.contains(t)) return;
        if (searchEl && (t === searchEl || searchEl.contains(t))) return;
        if (searchResultsEl && (t === searchResultsEl || searchResultsEl.contains(t))) return;
        hideSearchResults();
      });
    }

    resetFrom(0);
    if (lazyRoots) {
      if (selects[0]) {
        selects[0].innerHTML = '<option value="">Region…</option>';
        selects[0].disabled = false;
      }
      syncSelection();
    } else {
      ensureRoots(false, false).then(function () {
        syncSelection();
      });
    }

    return {
      selectedUid: selectedUid,
      selectedPath: selectedPath,
      isValidUid: isValidUid,
      clearSelection: clearSelection,
      onEnvironmentChange: onEnvironmentChange,
      retryLast: retryLast,
      refreshMetadata: refreshMetadata,
      ensureRoots: ensureRoots,
      setSelection: function (id, name, path) {
        if (!isValidUid(id)) {
          clearSelection();
          return;
        }
        commitSelection(id, name || id, path || name || id, "search");
        syncSelection();
        onChange(id);
      },
    };
  }

  global.CentralHubOuPicker = {
    create: createPicker,
    isValidUid: isValidUid,
    LEVELS: OU_LEVELS,
    MAINTENANCE_MESSAGE: MAINTENANCE_MSG,
  };
})(window);
