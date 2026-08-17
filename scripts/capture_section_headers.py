"""Capture section-header screenshots for before/after validation."""
from __future__ import annotations

import argparse
import threading
import time
from pathlib import Path
from wsgiref.simple_server import make_server

from playwright.sync_api import sync_playwright

from app import create_app


PAGES = [
    ("personal_dashboard", "/personal", "Personal"),
    ("personal_notebook", "/personal/notebook", "Personal"),
    ("personal_tasks", "/personal/tasks", "Personal"),
    ("work_dashboard", "/work", "Work"),
    ("repositories", "/repositories", "Work"),
    ("data_explorer", "/data-explorer", "Work"),
    ("dhis2_reports", "/dhis2/reports", "Work"),
    ("settings", "/settings", "System"),
    ("ai_providers", "/settings/ai-providers", "System"),
    ("audit", "/audit", "System"),
    ("ai_connections", "/system/ai-connections", "System"),
]

WIDTHS = [
    ("desktop", 1440, 900),
    ("narrow", 900, 900),
]


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--label", required=True, choices=("before", "after"))
    parser.add_argument("--port", type=int, default=8765)
    args = parser.parse_args()

    out = Path("tmp/section_header_shots") / args.label
    out.mkdir(parents=True, exist_ok=True)

    app = create_app()
    httpd = make_server("127.0.0.1", args.port, app)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    base = f"http://127.0.0.1:{args.port}"
    time.sleep(0.4)

    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            for width_name, width, height in WIDTHS:
                page = browser.new_page(viewport={"width": width, "height": height})
                for slug, path, _section in PAGES:
                    page.goto(base + path, wait_until="domcontentloaded", timeout=60000)
                    page.wait_for_timeout(250)
                    main = page.locator("main.content").first
                    box = main.bounding_box()
                    clip = None
                    if box:
                        clip = {
                            "x": max(box["x"], 0),
                            "y": max(box["y"], 0),
                            "width": min(box["width"], width - max(box["x"], 0)),
                            "height": min(260, box["height"], height - max(box["y"], 0)),
                        }
                    target = out / f"{slug}__{width_name}.png"
                    page.screenshot(path=str(target), clip=clip)
                    print(f"wrote {target}")
                page.close()
            browser.close()
    finally:
        httpd.shutdown()


if __name__ == "__main__":
    main()
