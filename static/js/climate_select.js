/**
 * Shared CLIMATE custom-select. Native <select> popups stay OS-white on Windows,
 * so provider/model menus use a themed listbox while the hidden select remains
 * the source of truth (exact-model value, change events, no silent fallback).
 */
(function (global) {
  "use strict";

  function escapeHtml(value) {
    return String(value == null ? "" : value).replace(/[&<>"']/g, function (c) {
      return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c];
    });
  }
  function optionIcon(name) {
    var key = String(name || "").trim().toLowerCase();
    if (key === "globe") {
      return '<svg class="climate-dd-glyph" viewBox="0 0 16 16" aria-hidden="true"><circle cx="8" cy="8" r="6" fill="none" stroke="currentColor" stroke-width="1.4"/><path d="M2 8h12M8 2c2 2.2 2 9.8 0 12M8 2C6 4.2 6 11.8 8 14" fill="none" stroke="currentColor" stroke-width="1.3"/></svg>';
    }
    if (key === "search") {
      return '<svg class="climate-dd-glyph" viewBox="0 0 16 16" aria-hidden="true"><circle cx="7" cy="7" r="4.5" fill="none" stroke="currentColor" stroke-width="1.4"/><path d="M10.5 10.5L14 14" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round"/></svg>';
    }
    if (key === "folder") {
      return '<svg class="climate-dd-glyph" viewBox="0 0 16 16" aria-hidden="true"><path d="M2.5 4.5h4l1.2 1.5H13.5v7H2.5z" fill="none" stroke="currentColor" stroke-width="1.4" stroke-linejoin="round"/></svg>';
    }
    return "";
  }
  function closeClimateDropdowns(except) {
    document.querySelectorAll(".climate-dd.is-open").forEach(function (dd) {
      if (except && dd === except) return;
      dd.classList.remove("is-open");
      var trigger = dd.querySelector(".climate-dd-trigger");
      if (trigger) trigger.setAttribute("aria-expanded", "false");
      var menu = dd._menu || dd.querySelector(".climate-dd-menu");
      if (!menu) return;
      menu.hidden = true;
      menu.classList.remove("is-portal", "is-up");
      menu.style.position = "";
      menu.style.top = "";
      menu.style.left = "";
      menu.style.right = "";
      menu.style.bottom = "";
      menu.style.width = "";
      menu.style.maxHeight = "";
      menu.style.zIndex = "";
      if (menu.parentNode !== dd) dd.appendChild(menu);
    });
  }

  function themeSource() {
    return document.querySelector(".climate-shell") || document.querySelector(".ax-chat") || document.body;
  }

  function positionClimateDropdownMenu(wrap, menu) {
    var trigger = wrap.querySelector(".climate-dd-trigger");
    if (!trigger || !menu) return;
    var rect = trigger.getBoundingClientRect();
    var gap = 4;
    var maxH = 240;
    var viewportH = window.innerHeight || document.documentElement.clientHeight;
    var viewportW = window.innerWidth || document.documentElement.clientWidth;
    var spaceBelow = Math.max(0, viewportH - rect.bottom - gap);
    var spaceAbove = Math.max(0, rect.top - gap);
    var preferUp = spaceBelow < Math.min(maxH, 160) && spaceAbove > spaceBelow;
    if (menu.parentNode !== document.body) document.body.appendChild(menu);
    menu.classList.add("is-portal");
    var shell = themeSource();
    if (shell) {
      var cs = getComputedStyle(shell);
      var accent = (cs.getPropertyValue("--cl-accent") || cs.getPropertyValue("--workspace-accent")).trim();
      var border = (cs.getPropertyValue("--cl-border") || cs.getPropertyValue("--climate-border")).trim();
      if (accent) menu.style.setProperty("--cl-accent", accent);
      if (border) menu.style.setProperty("--cl-border", border);
    }
    menu.hidden = false;
    menu.style.position = "fixed";
    menu.style.zIndex = "10050";
    menu.style.right = "auto";
    menu.style.bottom = "auto";
    var native = wrap.querySelector("select.climate-dd-native") || wrap.querySelector("select");
    var minWidth = parseInt((native && native.getAttribute("data-menu-min-width")) || "0", 10) || 0;
    var width = Math.max(rect.width, minWidth || 168);
    var left = rect.left;
    if (left + width > viewportW - 8) left = Math.max(8, viewportW - width - 8);
    if (left < 8) left = 8;
    menu.style.left = Math.round(left) + "px";
    menu.style.width = Math.round(width) + "px";
    var avail = preferUp ? spaceAbove : spaceBelow;
    var capped = Math.max(96, Math.min(maxH, avail || maxH));
    menu.style.maxHeight = capped + "px";
    var menuH = Math.min(menu.scrollHeight || capped, capped);
    if (!preferUp && spaceBelow < Math.min(menuH, 120) && spaceAbove > spaceBelow) {
      preferUp = true;
      capped = Math.max(96, Math.min(maxH, spaceAbove));
      menu.style.maxHeight = capped + "px";
      menuH = Math.min(menu.scrollHeight || capped, capped);
    }
    if (preferUp) {
      menu.classList.add("is-up");
      menu.style.top = Math.round(Math.max(8, rect.top - gap - menuH)) + "px";
    } else {
      menu.classList.remove("is-up");
      menu.style.top = Math.round(rect.bottom + gap) + "px";
      if (rect.bottom + gap + menuH > viewportH - 8) {
        menu.style.maxHeight = Math.max(96, Math.min(maxH, viewportH - rect.bottom - gap - 8)) + "px";
      }
    }
  }

  function openClimateDropdown(selectEl) {
    if (!selectEl || !selectEl._climateDd) return;
    var wrap = selectEl._climateDd;
    var menu = wrap._menu || wrap.querySelector(".climate-dd-menu");
    var trigger = wrap.querySelector(".climate-dd-trigger");
    if (!menu || !trigger) return;
    syncClimateDropdown(selectEl);
    wrap.classList.add("is-open");
    trigger.setAttribute("aria-expanded", "true");
    positionClimateDropdownMenu(wrap, menu);
  }

  function syncClimateDropdown(selectEl) {
    if (!selectEl || !selectEl._climateDd) return;
    var dd = selectEl._climateDd;
    var valueEl = dd.querySelector(".climate-dd-value");
    var menu = dd._menu || dd.querySelector(".climate-dd-menu");
    if (!menu || !valueEl) return;
    var selected = selectEl.options[selectEl.selectedIndex];
    var selectedIcon = selected ? optionIcon(selected.getAttribute("data-icon")) : "";
    valueEl.innerHTML = selectedIcon + '<span class="climate-dd-label">' +
      escapeHtml(selected ? selected.textContent : (selectEl.value || "Select")) + "</span>";
    valueEl.classList.toggle("is-placeholder", !selectEl.value);
    dd.classList.toggle("is-disabled", !!selectEl.disabled);
    dd.setAttribute("data-scope", selectEl.value || "general");
    if (selectEl.getAttribute("data-rich-menu")) {
      menu.classList.add("is-rich");
    }
    menu.innerHTML = "";
    var lastGroup = "";
    Array.prototype.forEach.call(selectEl.options, function (opt) {
      var group = opt.parentElement;
      if (group && group.tagName === "OPTGROUP" && group.label && group.label !== lastGroup) {
        lastGroup = group.label;
        var hdr = document.createElement("div");
        hdr.className = "climate-dd-group";
        hdr.textContent = group.label;
        menu.appendChild(hdr);
      }
      var btn = document.createElement("button");
      btn.type = "button";
      btn.className = "climate-dd-option" + (opt.selected ? " is-selected" : "") + (opt.disabled ? " is-disabled" : "");
      btn.setAttribute("role", "option");
      btn.setAttribute("data-value", opt.value);
      if (opt.selected) btn.setAttribute("aria-selected", "true");
      if (opt.disabled) btn.disabled = true;
      if (opt.getAttribute("data-unavailable")) {
        btn.setAttribute("data-unavailable", opt.getAttribute("data-unavailable"));
      }
      var icon = optionIcon(opt.getAttribute("data-icon"));
      var desc = opt.getAttribute("data-description") || "";
      if (icon || desc) {
        btn.classList.add("is-rich");
        btn.innerHTML = (icon || '<span class="climate-dd-glyph"></span>') +
          '<span class="climate-dd-copy"><span class="climate-dd-title">' + escapeHtml(opt.textContent) + "</span>" +
          (desc ? "<small>" + escapeHtml(desc) + "</small>" : "") + "</span>";
      } else {
        btn.textContent = opt.textContent;
      }
      btn.addEventListener("click", function (event) {
        event.preventDefault();
        event.stopPropagation();
        if (opt.disabled) return;
        selectEl.value = opt.value;
        selectEl.dispatchEvent(new Event("change", { bubbles: true }));
        syncClimateDropdown(selectEl);
        closeClimateDropdowns();
      });
      menu.appendChild(btn);
    });
  }

  function enhanceClimateSelect(selectEl) {
    if (!selectEl) return;
    if (selectEl._climateDd) {
      syncClimateDropdown(selectEl);
      return;
    }
    var wrap = document.createElement("div");
    wrap.className = "climate-dd";
    wrap.setAttribute("data-for", selectEl.id || "");
    selectEl.parentNode.insertBefore(wrap, selectEl);
    wrap.appendChild(selectEl);
    selectEl.classList.add("climate-dd-native");
    var trigger = document.createElement("button");
    trigger.type = "button";
    trigger.className = "climate-dd-trigger";
    trigger.setAttribute("aria-haspopup", "listbox");
    trigger.setAttribute("aria-expanded", "false");
    if (selectEl.getAttribute("aria-label")) {
      trigger.setAttribute("aria-label", selectEl.getAttribute("aria-label"));
    }
    trigger.innerHTML = '<span class="climate-dd-value"></span><span class="climate-dd-caret" aria-hidden="true">▾</span>';
    var menu = document.createElement("div");
    menu.className = "climate-dd-menu";
    menu.setAttribute("role", "listbox");
    menu.hidden = true;
    wrap.appendChild(trigger);
    wrap.appendChild(menu);
    wrap._menu = menu;
    menu._ownerDd = wrap;
    selectEl._climateDd = wrap;
    trigger.addEventListener("click", function (event) {
      event.preventDefault();
      event.stopPropagation();
      if (selectEl.disabled) return;
      var willOpen = menu.hidden || !wrap.classList.contains("is-open");
      closeClimateDropdowns();
      if (willOpen) openClimateDropdown(selectEl);
    });
    selectEl.addEventListener("change", function () { syncClimateDropdown(selectEl); });
    syncClimateDropdown(selectEl);
  }

  function enhanceClimateSelects(ids) {
    (ids || []).forEach(function (id) {
      var el = typeof id === "string" ? document.getElementById(id) : id;
      if (el) enhanceClimateSelect(el);
    });
  }

  if (!global._climateSelectDocBound) {
    global._climateSelectDocBound = true;
    document.addEventListener("click", function (event) {
      if (!event.target.closest(".climate-dd") && !event.target.closest(".climate-dd-menu")) {
        closeClimateDropdowns();
      }
    });
    document.addEventListener("keydown", function (event) {
      if (event.key === "Escape") closeClimateDropdowns();
    });
    window.addEventListener("resize", function () { closeClimateDropdowns(); });
    document.addEventListener("scroll", function (event) {
      if (!document.querySelector(".climate-dd.is-open")) return;
      if (event.target && event.target.closest && event.target.closest(".climate-dd-menu")) return;
      closeClimateDropdowns();
    }, true);
  }

  global.ClimateSelect = {
    enhance: enhanceClimateSelect,
    enhanceAll: enhanceClimateSelects,
    sync: syncClimateDropdown,
    close: closeClimateDropdowns
  };
})(window);
