"""Capture Process Manager screenshots after the async table loads."""
from __future__ import annotations

import threading
import time
from pathlib import Path
from wsgiref.simple_server import make_server

from playwright.sync_api import sync_playwright

from app import create_app


def main() -> None:
    out = Path("docs/screenshots/process-manager")
    out.mkdir(parents=True, exist_ok=True)
    app = create_app()
    httpd = make_server("127.0.0.1", 8772, app)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    time.sleep(0.5)
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            for name, width, height in (("desktop", 1440, 1200), ("narrow", 900, 1200)):
                page = browser.new_page(viewport={"width": width, "height": height})
                page.goto("http://127.0.0.1:8772/health", wait_until="domcontentloaded")
                page.locator("[data-central-hub-processes]").scroll_into_view_if_needed()
                page.wait_for_function(
                    """() => {
                      const t = document.querySelector('#hub-processes-body');
                      return t && !t.innerText.includes('Scanning');
                    }""",
                    timeout=60000,
                )
                page.wait_for_timeout(400)
                box = page.locator("[data-central-hub-processes]").bounding_box()
                if box:
                    page.screenshot(
                        path=str(out / f"process-manager-{name}.png"),
                        clip={
                            "x": max(box["x"], 0),
                            "y": max(box["y"], 0),
                            "width": min(box["width"], width - max(box["x"], 0)),
                            "height": min(box["height"] + 12, 820, height - max(box["y"], 0)),
                        },
                    )
                print("wrote", out / f"process-manager-{name}.png")
                print(page.locator("#hub-processes-body").inner_text()[:240])
                page.close()
            browser.close()
    finally:
        httpd.shutdown()


if __name__ == "__main__":
    main()
