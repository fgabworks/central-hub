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
    httpd = make_server("127.0.0.1", 8778, app)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    base = "http://127.0.0.1:8778"
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
                      return {
                        title: (q(".section-header-title") || {}).textContent || "",
                        lede: (q(".aic-lede") || {}).textContent || "",
                        noticeBg: cs(notice, "background-color"),
                        noticeColor: cs(notice, "color"),
                        noticeText: (notice && notice.innerText) || "",
                        providerCount: qa("#ai-connections [data-provider-id]").length,
                        providersBeforeDefaults: !!(providers && defaults &&
                          providers.compareDocumentPosition(defaults) & Node.DOCUMENT_POSITION_FOLLOWING),
                        fieldBgs: fields.map((el) => cs(el, "background-color")),
                        fieldColors: fields.map((el) => cs(el, "color")),
                        saveWidth: save ? Math.round(save.getBoundingClientRect().width) : 0,
                        saveText: save ? save.textContent.trim() : "",
                        pageWidth: q(".aic-page") ? Math.round(q(".aic-page").getBoundingClientRect().width) : 0,
                        gridCols: getComputedStyle(q(".aic-provider-grid")).gridTemplateColumns,
                        modelCols: getComputedStyle(q(".aic-model-grid")).gridTemplateColumns,
                        hasAgentSafety: !!q(".agent-safety"),
                        reset: !!(q("#coding-defaults-reset")),
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
    if failed:
        raise SystemExit("AI Connections UI validation failed:\n- " + "\n- ".join(failed))
    print("AI Connections UI validation passed")


if __name__ == "__main__":
    main()
