"""Capture and validate the AI Connections settings page."""

from __future__ import annotations

import json
import sys
import threading
import time
from pathlib import Path
from wsgiref.simple_server import make_server

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from playwright.sync_api import sync_playwright

from app import create_app

OUT = ROOT / "tmp" / "climate-ui" / "ai-connections-after"
WIDTHS = (("desktop", 1440, 900), ("medium", 980, 900), ("narrow", 720, 900))


def _rgb(value: str) -> tuple[int, int, int] | None:
    if not value:
        return None
    m = __import__("re").search(r"rgba?\((\d+),\s*(\d+),\s*(\d+)", value)
    if not m:
        return None
    return int(m.group(1)), int(m.group(2)), int(m.group(3))


def _luma(rgb: tuple[int, int, int] | None) -> float | None:
    if not rgb:
        return None
    r, g, b = rgb
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    app = create_app()
    httpd = make_server("127.0.0.1", 8783, app)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    base = "http://127.0.0.1:8783"
    time.sleep(0.4)
    report: dict = {"pages": {}}
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            for name, width, height in WIDTHS:
                page = browser.new_page(viewport={"width": width, "height": height})
                page.goto(base + "/system/ai-connections", wait_until="networkidle", timeout=60000)
                page.wait_for_timeout(400)
                stats = page.evaluate(
                    """() => {
                      const cs = (el, prop) => el ? getComputedStyle(el).getPropertyValue(prop) : "";
                      const q = (s) => document.querySelector(s);
                      const qa = (s) => Array.from(document.querySelectorAll(s));
                      const fields = qa(".aic-page select, .aic-page .aic-exact-input, .aic-page .climate-dd-trigger");
                      const notice = q(".aic-notice");
                      const providers = q("#aic-providers-title");
                      const defaults = q("#aic-defaults-title");
                      const save = qa(".aic-defaults-actions .btn-primary")[0];
                      const reset = q("#coding-defaults-reset");
                      const chat = q("#aic-chat-defaults-title");
                      const workspace = q("#aic-workspace-defaults-title");
                      const overrides = q("#aic-overrides-title");
                      return {
                        title: (q(".section-header-title") || {}).textContent || "",
                        lede: (q(".aic-lede") || {}).textContent || "",
                        defaultsTitle: (defaults && defaults.textContent) || "",
                        chatTitle: (chat && chat.textContent) || "",
                        workspaceTitle: (workspace && workspace.textContent) || "",
                        overridesTitle: (overrides && overrides.textContent) || "",
                        chatProvider: !!q("#chat-default-provider"),
                        workspaceProvider: !!q("#workspace-default-provider"),
                        legacyMergedProvider: !!q("#coding-default-provider"),
                        chatBeforeWorkspace: !!(chat && workspace &&
                          chat.compareDocumentPosition(workspace) & Node.DOCUMENT_POSITION_FOLLOWING),
                        workspaceBeforeOverrides: !!(workspace && overrides &&
                          workspace.compareDocumentPosition(overrides) & Node.DOCUMENT_POSITION_FOLLOWING),
                        noticeBg: cs(notice, "background-color"),
                        noticeColor: cs(notice, "color"),
                        noticeText: (notice && notice.innerText) || "",
                        providerCount: qa("#ai-connections [data-provider-id]").length,
                        logos: qa("#ai-connections .aic-logo, #ai-connections .aic-logo-fallback").length,
                        manage: qa("#ai-connections [data-action='manage']").length,
                        methods: qa("#ai-connections .aic-method").map((el) => el.textContent.trim()),
                        keyDialog: !!q("#ai-provider-key-dialog"),
                        providersBeforeDefaults: !!(providers && defaults &&
                          providers.compareDocumentPosition(defaults) & Node.DOCUMENT_POSITION_FOLLOWING),
                        fieldBgs: fields.map((el) => cs(el, "background-color")),
                        fieldColors: fields.map((el) => cs(el, "color")),
                        saveWidth: save ? Math.round(save.getBoundingClientRect().width) : 0,
                        saveText: save ? save.textContent.trim() : "",
                        resetText: reset ? reset.textContent.trim() : "",
                        pageWidth: q(".aic-page") ? Math.round(q(".aic-page").getBoundingClientRect().width) : 0,
                        gridCols: getComputedStyle(q(".aic-provider-grid")).gridTemplateColumns,
                        modelCols: getComputedStyle(q(".aic-model-grid")).gridTemplateColumns,
                        surfaceCols: q(".aic-surface") ? getComputedStyle(q(".aic-surface")).gridTemplateColumns : "",
                        chatProviderWidth: (function () {
                          const el = q("#chat-default-provider");
                          const trigger = el && ((el.closest(".climate-dd") || {}).querySelector
                            ? (el.closest(".climate-dd").querySelector(".climate-dd-trigger") || el)
                            : el);
                          return trigger ? Math.round(trigger.getBoundingClientRect().width) : 0;
                        })(),
                        hasAgentSafety: !!q(".agent-safety"),
                        reset: !!(q("#coding-defaults-reset")),
                        modeSwitches: qa(".aic-mode-switch").length,
                      };
                    }"""
                )
                fields_dark = all((_luma(_rgb(bg)) or 255) < 90 for bg in stats["fieldBgs"] or [""])
                notice_not_red = True
                nbg = _rgb(stats["noticeBg"])
                if nbg:
                    notice_not_red = nbg[2] >= nbg[0]
                report["pages"][name] = {
                    **stats,
                    "fields_dark": fields_dark,
                    "notice_not_red": notice_not_red,
                    "save_compact": stats["saveWidth"] < 280,
                }
                page.screenshot(path=str(OUT / f"{name}.png"), full_page=True)
                page.close()
            surfaces = {}
            for route, key in (("/work/chat", "chat"), ("/work/climate", "workspace")):
                page = browser.new_page(viewport={"width": 1440, "height": 900})
                page.goto(base + route, wait_until="domcontentloaded", timeout=60000)
                page.wait_for_timeout(500)
                surfaces[key] = page.evaluate(
                    """() => {
                      const root = document.getElementById("ax-chat") || document.getElementById("climate-shell");
                      let boot = {};
                      try { boot = JSON.parse((root && root.getAttribute("data-bootstrap")) || "{}"); }
                      catch (_err) { boot = {}; }
                      const defaults = boot.coding_defaults || {};
                      return {
                        url: location.pathname,
                        hasChatSelects: !!(document.getElementById("ax-provider") && document.getElementById("ax-model")),
                        hasWorkspaceSelects: !!(document.getElementById("climate-provider") && document.getElementById("climate-model")),
                        hasModeSwitch: !!document.querySelector(".climate-mode-switch [data-execution-mode='climate_assisted']")
                          && !!document.querySelector(".climate-mode-switch [data-execution-mode='direct']"),
                        chatRepoOptional: !!(document.getElementById("ax-context-scope") &&
                          Array.from(document.getElementById("ax-context-scope").options || []).some((opt) => opt.value === "general")),
                        workspaceRepoExplicit: !!(document.getElementById("climate-context-scope") &&
                          Array.from(document.getElementById("climate-context-scope").options || []).some((opt) => opt.value === "general") &&
                          Array.from(document.getElementById("climate-context-scope").options || []).some((opt) => opt.value === "all")),
                        chatProvider: (defaults.chat || {}).default_provider || "",
                        workspaceProvider: (defaults.workspace || {}).default_provider || "",
                        chatMode: (defaults.chat || {}).default_mode || "",
                        workspaceMode: (defaults.workspace || {}).default_mode || "",
                        hasChat: !!(defaults.chat && "default_provider" in defaults.chat && "default_mode" in defaults.chat),
                        hasWorkspace: !!(defaults.workspace && "default_provider" in defaults.workspace && "default_mode" in defaults.workspace),
                        aliasProvider: defaults.default_provider || "",
                      };
                    }"""
                )
                page.screenshot(path=str(OUT / f"{key}_surface.png"), full_page=False)
                page.close()
            report["surfaces"] = surfaces
            browser.close()
    finally:
        httpd.shutdown()
    (OUT / "report.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(json.dumps(report, indent=2))
    failed = []
    for name, row in report["pages"].items():
        if not row.get("providersBeforeDefaults"):
            failed.append(f"{name}: providers not before defaults")
        if row.get("hasAgentSafety"):
            failed.append(f"{name}: red warning banner still present")
        if not row.get("fields_dark"):
            failed.append(f"{name}: bright form fields remain")
        if not row.get("notice_not_red"):
            failed.append(f"{name}: notice is not blue/neutral")
        if not row.get("save_compact"):
            failed.append(f"{name}: Save is oversized")
        if "Save changes" not in (row.get("saveText") or ""):
            failed.append(f"{name}: missing Save changes")
        if "Reset to defaults" not in (row.get("resetText") or ""):
            failed.append(f"{name}: missing Reset to defaults")
        if row.get("providerCount", 0) < 4:
            failed.append(f"{name}: expected 4 provider cards")
        if row.get("logos", 0) < 4:
            failed.append(f"{name}: missing provider logos")
        if row.get("manage", 0) < 4:
            failed.append(f"{name}: missing Manage actions")
        if "API Key" not in (row.get("methods") or []) or "CLI" not in (row.get("methods") or []):
            failed.append(f"{name}: missing connection method badges")
        if not row.get("keyDialog"):
            failed.append(f"{name}: missing API key dialog")
        if row.get("defaultsTitle") != "AI Defaults":
            failed.append(f"{name}: defaults title is not AI Defaults")
        if row.get("chatTitle") != "CLIMATE Chat (General)":
            failed.append(f"{name}: missing CLIMATE Chat defaults")
        if row.get("workspaceTitle") != "Code Workspace (Coding)":
            failed.append(f"{name}: missing Code Workspace defaults")
        if row.get("overridesTitle") != "Provider Overrides (Auto)":
            failed.append(f"{name}: missing Provider Overrides")
        if not row.get("chatProvider") or not row.get("workspaceProvider"):
            failed.append(f"{name}: missing split provider selectors")
        if row.get("legacyMergedProvider"):
            failed.append(f"{name}: merged coding-default-provider is still present")
        if not row.get("chatBeforeWorkspace"):
            failed.append(f"{name}: Chat defaults are not above Code Workspace")
        if name == "desktop":
            if len((row.get("modelCols") or "").split()) != 4:
                failed.append(f"{name}: provider overrides are not one compact 4-column row")
            if len((row.get("surfaceCols") or "").split()) < 4:
                failed.append(f"{name}: Chat/Workspace defaults are not a compact 4-column row")
            if (row.get("pageWidth") or 0) < 1000:
                failed.append(f"{name}: page is not using the full content width")
        if (row.get("chatProviderWidth") or 0) > 280:
            failed.append(f"{name}: provider dropdown is oversized")
        if not row.get("workspaceBeforeOverrides"):
            failed.append(f"{name}: Code Workspace defaults are not above Provider Overrides")
        if row.get("modeSwitches", 0) < 2:
            failed.append(f"{name}: missing Chat/Workspace mode switches")
        if "encrypted" in (row.get("noticeText") or "").lower() and "not encrypted" not in (row.get("noticeText") or "").lower():
            failed.append(f"{name}: notice claims encryption")
    chat = (report.get("surfaces") or {}).get("chat") or {}
    workspace = (report.get("surfaces") or {}).get("workspace") or {}
    if not chat.get("hasChatSelects"):
        failed.append("chat: missing provider/model selectors")
    if not workspace.get("hasWorkspaceSelects"):
        failed.append("workspace: missing provider/model selectors")
    if not chat.get("hasChat") or not chat.get("hasWorkspace"):
        failed.append("chat: bootstrap is missing split coding_defaults.chat/workspace")
    if not workspace.get("hasChat") or not workspace.get("hasWorkspace"):
        failed.append("workspace: bootstrap is missing split coding_defaults.chat/workspace")
    if not chat.get("hasModeSwitch"):
        failed.append("chat: missing AiriX/Direct switch")
    if not workspace.get("hasModeSwitch"):
        failed.append("workspace: missing AiriX/Direct switch")
    if not chat.get("chatRepoOptional"):
        failed.append("chat: repository context is not optional")
    if not workspace.get("workspaceRepoExplicit"):
        failed.append("workspace: missing explicit repository context control")
    if failed:
        raise SystemExit("AI Connections UI validation failed:\n- " + "\n- ".join(failed))
    print("AI Connections UI validation passed")


if __name__ == "__main__":
    main()
