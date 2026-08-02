"""Capture before/after style header screenshots for standardization."""
from __future__ import annotations

import argparse
import threading
import time
from pathlib import Path
from wsgiref.simple_server import make_server

from playwright.sync_api import sync_playwright

from app import create_app

PAGES = [
    ("dhis2_overview", "/dhis2"),
    ("repositories", "/repositories"),
    ("sql_workspace", "/sql"),
]


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--label", required=True, choices=("before", "after"))
    parser.add_argument("--port", type=int, default=8776)
    args = parser.parse_args()

    out = Path("docs/screenshots/header-standard") / args.label
    out.mkdir(parents=True, exist_ok=True)

    app = create_app()
    httpd = make_server("127.0.0.1", args.port, app)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    time.sleep(0.4)
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            for width_name, width, height in (("desktop", 1440, 900), ("narrow", 900, 900)):
                page = browser.new_page(viewport={"width": width, "height": height})
                for slug, path in PAGES:
                    page.goto(f"http://127.0.0.1:{args.port}{path}", wait_until="domcontentloaded")
                    page.wait_for_timeout(350)
                    header = page.locator("[data-section-header]").first
                    box = header.bounding_box()
                    main = page.locator("main.content").first
                    mbox = main.bounding_box()
                    clip = None
                    if mbox:
                        clip = {
                            "x": max(mbox["x"], 0),
                            "y": max(mbox["y"], 0),
                            "width": min(mbox["width"], width - max(mbox["x"], 0)),
                            "height": min(240, mbox["height"], height - max(mbox["y"], 0)),
                        }
                    target = out / f"{slug}__{width_name}.png"
                    page.screenshot(path=str(target), clip=clip)
                    print(
                        f"{args.label} {slug} {width_name} header_h="
                        f"{round(box['height']) if box else None} -> {target}"
                    )
                page.close()
            browser.close()
    finally:
        httpd.shutdown()


if __name__ == "__main__":
    main()
