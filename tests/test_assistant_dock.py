"""Focused UI, isolation, persistence, and layout tests for the assistant dock."""

from __future__ import annotations

import json
import re
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

from app import create_app
from hub.agent_center.dock import (
    clamp_width,
    dock_shell_bootstrap,
    load_dock_prefs,
    page_aware_suggestions,
    save_dock_prefs,
)
from hub.agent_center.profiles import profile_for_workspace
from hub.notebook.db import NotebookDatabase
from hub.notebook.store import NotebookStore
from hub.notebook.workspace import persist_workspace


ROOT = Path(__file__).resolve().parents[1]


class DockUnitTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.db = NotebookDatabase(Path(self.tmp.name) / "notebook.db")

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_profile_maps_workspace(self) -> None:
        self.assertEqual(profile_for_workspace("personal").id, "aira")
        self.assertEqual(profile_for_workspace("work").id, "okarun")
        self.assertEqual(profile_for_workspace(None).id, "okarun")

    def test_prefs_isolated_per_workspace(self) -> None:
        save_dock_prefs(
            self.db,
            "personal",
            {"open": True, "pinned": False, "minimized": False, "width": 420},
        )
        save_dock_prefs(
            self.db,
            "work",
            {"open": False, "pinned": True, "minimized": True, "width": 320},
        )
        personal = load_dock_prefs(self.db, "personal")
        work = load_dock_prefs(self.db, "work")
        self.assertTrue(personal["open"])
        self.assertFalse(personal["pinned"])
        self.assertEqual(personal["width"], 420)
        self.assertEqual(personal["profile_id"], "aira")
        self.assertFalse(work["open"])
        self.assertTrue(work["pinned"])
        self.assertTrue(work["minimized"])
        self.assertEqual(work["width"], 320)
        self.assertEqual(work["profile_id"], "okarun")

    def test_width_clamped(self) -> None:
        self.assertEqual(clamp_width(10), 300)
        self.assertEqual(clamp_width(9999), 560)
        self.assertEqual(clamp_width("bad"), 400)

    def test_bootstrap_is_lightweight(self) -> None:
        boot = dock_shell_bootstrap(self.db, workspace="work", endpoint="dhis2")
        self.assertTrue(boot["ok"])
        self.assertEqual(boot["profile"]["id"], "okarun")
        self.assertTrue(boot["safety"]["read_only"])
        self.assertTrue(boot["safety"]["voice_disabled"])
        self.assertTrue(any("DHIS2" in s["label"] for s in boot["suggestions"]))
        self.assertNotIn("agents", boot)
        self.assertIn("lazy_agents_url", boot)

    def test_page_aware_suggestions_personal_vs_work(self) -> None:
        personal = page_aware_suggestions("aira", "personal_email")
        work = page_aware_suggestions("okarun", "sql_workspace")
        self.assertTrue(any("personal email" in s["label"].lower() for s in personal))
        self.assertTrue(any("sql" in s["label"].lower() for s in work))
        self.assertFalse(any("dhis2" in s["label"].lower() for s in personal))


class DockRouteTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.db_path = Path(self.tmp.name) / "notebook.db"
        self.app = create_app()
        self.app.config["NOTEBOOK"] = NotebookStore(NotebookDatabase(self.db_path))
        self.app.config["TESTING"] = True
        self.client = self.app.test_client()

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def _set_workspace(self, workspace: str) -> None:
        persist_workspace(self.app.config["NOTEBOOK"].db, workspace)
        self.client.get(f"/workspace/{workspace}")

    def test_work_pages_mount_okarun_dock(self) -> None:
        self._set_workspace("work")
        html = self.client.get("/work").get_data(as_text=True)
        self.assertIn('id="assistant-dock-host"', html)
        self.assertIn("assistant_dock.js", html)
        self.assertIn("has-activity-rail", html)
        self.assertIn('id="ar-assistant"', html)
        self.assertIn('id="ad-topbar-toggle"', html)
        self.assertNotIn('id="ad-toggle"', html)
        self.assertNotIn("ad-rail", html)
        self.assertIn("Okarun", html)
        self.assertIn("Read-only mode. No actions are executed.", html)
        self.assertIn("ad-tab-conversation", html)
        self.assertIn("ad-tab-output", html)
        self.assertIn('id="ad-prompt"', html)
        self.assertIn('id="ad-context-btn"', html)
        self.assertNotIn("speechSynthesis", html)
        self.assertNotIn("webkitSpeechRecognition", html)

    def test_personal_pages_mount_aira_dock(self) -> None:
        self._set_workspace("personal")
        html = self.client.get("/personal").get_data(as_text=True)
        self.assertIn('id="assistant-dock-host"', html)
        self.assertIn("Aira", html)
        boot = self._bootstrap_from_html(html)
        self.assertEqual(boot["profile"]["id"], "aira")
        self.assertEqual(boot["workspace"], "personal")

    def test_assistant_center_keeps_dock_and_full_page(self) -> None:
        """Assistant Center is history/management; dock still mounts for new prompts."""
        self._set_workspace("work")
        html = self.client.get("/work/okarun").get_data(as_text=True)
        self.assertIn('id="assistant-dock-host"', html)
        self.assertIn("assistant_dock.js", html)
        self.assertIn("has-activity-rail", html)
        self.assertIn("Assistant Center", html)
        self.assertIn("ac-engine-card", html)
        self.assertIn("data-ac-tab=\"answer\"", html)
        self.assertIn("Recent Runs", html)
        self.assertIn("Saved Prompts", html)

    def test_right_docked_placement_css(self) -> None:
        css = (ROOT / "static" / "css" / "style.css").read_text(encoding="utf-8")
        self.assertIn(".activity-rail", css)
        self.assertNotIn(".ad-rail {", css)
        self.assertIn("position: fixed", css)
        self.assertIn("padding-right: var(--activity-rail-w, 48px)", css)
        self.assertIn(
            "padding-right: calc(var(--activity-rail-w, 48px) + var(--ad-width, 400px))",
            css,
        )
        self.assertIn(".ad-host", css)
        self.assertIn("right: var(--activity-rail-w, 48px)", css)

    def test_mobile_drawer_behavior_css(self) -> None:
        css = (ROOT / "static" / "css" / "style.css").read_text(encoding="utf-8")
        self.assertIn("@media (max-width: 960px)", css)
        self.assertIn(".app-shell.is-ad-mobile.is-ad-open .ad-host", css)
        self.assertIn("right: var(--activity-rail-w, 48px)", css)
        self.assertIn(".ad-backdrop", css)

    def test_js_toggle_open_close(self) -> None:
        js = (ROOT / "static" / "js" / "assistant_dock.js").read_text(encoding="utf-8")
        base = (ROOT / "templates" / "base.html").read_text(encoding="utf-8")
        self.assertIn("function toggle()", js)
        self.assertIn("setOpen(!prefs.open)", js)
        self.assertIn('id="ar-assistant"', base)
        self.assertIn('id="ad-topbar-toggle"', base)
        self.assertNotIn('id="ad-toggle"', base)
        self.assertIn('toggleBtn.addEventListener("click", toggle)', js)
        self.assertIn("ad-resize", js)
        self.assertIn("/agents?probe=1", js)
        self.assertNotIn("speechSynthesis", js)

    def test_prefs_api_persists_width_and_visibility(self) -> None:
        self._set_workspace("work")
        put = self.client.put(
            "/api/assistant-dock/prefs",
            data=json.dumps(
                {"open": True, "width": 440, "pinned": True, "minimized": False}
            ),
            content_type="application/json",
        )
        self.assertEqual(put.status_code, 200)
        body = put.get_json()
        self.assertTrue(body["prefs"]["open"])
        self.assertEqual(body["prefs"]["width"], 440)

        self._set_workspace("personal")
        personal_get = self.client.get("/api/assistant-dock/prefs").get_json()
        self.assertFalse(personal_get["prefs"]["open"])
        self.assertEqual(personal_get["prefs"]["profile_id"], "aira")

        self._set_workspace("work")
        work_get = self.client.get("/api/assistant-dock/prefs").get_json()
        self.assertTrue(work_get["prefs"]["open"])
        self.assertEqual(work_get["prefs"]["width"], 440)
        self.assertEqual(work_get["prefs"]["profile_id"], "okarun")

        html = self.client.get("/health").get_data(as_text=True)
        self.assertIn("is-ad-open", html)
        self.assertIn("--ad-width: 440px", html)
        self.assertIn("has-activity-rail", html)

    def test_page_content_resizes_with_open_class(self) -> None:
        self._set_workspace("work")
        self.client.put(
            "/api/assistant-dock/prefs",
            data=json.dumps({"open": True, "width": 400, "minimized": False}),
            content_type="application/json",
        )
        html = self.client.get("/repositories").get_data(as_text=True)
        self.assertIn("is-ad-open", html)
        self.assertIn("has-activity-rail", html)
        self.assertIn('id="ad-panel"', html)
        # When open and not minimized, panel is not hidden.
        self.assertNotRegex(html, r'id="ad-panel"[^>]*hidden')

    def test_bootstrap_api_does_not_probe_providers(self) -> None:
        self._set_workspace("work")
        with mock.patch(
            "hub.agent_center.service.AgentCenterService.list_agents"
        ) as listed:
            listed.side_effect = AssertionError("providers must stay lazy")
            with mock.patch(
                "hub.agent_center.connections.AgentConnectionRegistry.list"
            ) as conn_list:
                conn_list.side_effect = AssertionError("connections must stay lazy")
                resp = self.client.get("/api/assistant-dock/bootstrap")
        self.assertEqual(resp.status_code, 200)
        data = resp.get_json()
        self.assertEqual(data["profile"]["id"], "okarun")
        self.assertNotIn("agents", data)
        listed.assert_not_called()
        conn_list.assert_not_called()

    def test_no_performance_regression_with_dock(self) -> None:
        self._set_workspace("work")
        for _ in range(1):
            self.client.get("/work")
        timings = []
        for _ in range(3):
            start = time.perf_counter()
            resp = self.client.get("/work")
            timings.append((time.perf_counter() - start) * 1000.0)
            self.assertEqual(resp.status_code, 200)
            self.assertIn("Server-Timing", resp.headers)
        timings.sort()
        p95 = timings[-1]
        self.assertLessEqual(p95, 1000.0, msg=f"/work p95 {p95:.1f}ms")

    def test_full_height_layout_and_composer_positioning(self) -> None:
        """Dock fills host height; composer is a fixed footer; only messages scroll."""
        self._set_workspace("work")
        html = self.client.get("/work").get_data(as_text=True)
        self.assertIn('id="ad-panel"', html)
        self.assertIn('class="ad-composer"', html)
        self.assertIn('class="ad-body"', html)
        self.assertIn('id="ad-messages"', html)
        self.assertIn("No conversation yet. Select a suggestion or ask Okarun a question.", html)
        self.assertIn('id="ad-more"', html)
        self.assertIn('id="ad-menu-pop"', html)
        self.assertIn('id="ad-pin"', html)
        self.assertIn('id="ad-minimize"', html)
        self.assertIn('id="ad-close"', html)
        self.assertIn('id="ad-cancel"', html)
        self.assertIn('id="ad-retry"', html)
        self.assertIn('class="ad-composer-selects"', html)
        self.assertIn('id="ad-agent"', html)
        self.assertIn('id="ad-model"', html)
        # Composer follows the scroll body in markup (footer after body).
        body_idx = html.find('class="ad-body"')
        composer_idx = html.find('class="ad-composer"')
        self.assertGreater(composer_idx, body_idx)
        # No obsolete blank footer spacer after composer.
        after = html[composer_idx : composer_idx + 1200]
        self.assertNotIn("ad-footer-spacer", after)
        self.assertNotIn("ad-blank", after)

        css = (ROOT / "static" / "css" / "style.css").read_text(encoding="utf-8")
        self.assertIn(".ad-host {", css)
        self.assertIn("bottom: var(--wc-open-inset, 0px)", css)
        self.assertRegex(css, r"\.ad-panel\s*\{[^}]*flex:\s*1\s+1\s+auto")
        self.assertRegex(css, r"\.ad-panel\s*\{[^}]*height:\s*100%")
        self.assertRegex(css, r"\.ad-body\s*\{[^}]*flex:\s*1\s+1\s+auto")
        self.assertRegex(css, r"\.ad-messages,\s*\.ad-output\s*\{[^}]*overflow-y:\s*auto")
        self.assertRegex(css, r"\.ad-composer\s*\{[^}]*flex:\s*0\s+0\s+auto")
        self.assertIn(".ad-composer-selects", css)
        self.assertIn("grid-template-columns: minmax(0, 1fr) minmax(0, 1fr)", css)
        self.assertIn("text-overflow: ellipsis", css)
        self.assertIn("max-width: none", css)

        js = (ROOT / "static" / "js" / "assistant_dock.js").read_text(encoding="utf-8")
        self.assertIn("function setRunControls", js)
        self.assertIn("cancelActiveRun", js)
        self.assertIn("retryActiveRun", js)
        self.assertIn("clearEmptyState", js)
        self.assertIn("document.visibilityState === \"hidden\"", js)
        self.assertIn("if (!expanded()) return", js)

    def test_quick_notepad_rail_placement_without_overlap(self) -> None:
        """Notepad launches from the rail and docks beside Okarun, not over the composer."""
        self._set_workspace("work")
        html = self.client.get("/work").get_data(as_text=True)
        self.assertIn('id="ar-notepad"', html)
        self.assertIn("qn-global-host", html)
        self.assertIn("qn-from-rail", html)
        self.assertIn("qn-open-btn sr-only", html)
        self.assertNotRegex(html, r'class="qn-open-btn btn')

        css = (ROOT / "static" / "css" / "style.css").read_text(encoding="utf-8")
        self.assertIn(".qn-open-btn {", css)
        self.assertIn("display: none !important", css)
        self.assertIn(
            "right: calc(var(--activity-rail-w, 48px) + var(--ad-width, 400px))",
            css,
        )
        # Global notepad is a sibling drawer; ensure overlap rule exists for open dock.
        self.assertIn("body:has(.app-shell.is-ad-open", css)

    @staticmethod
    def _bootstrap_from_html(html: str) -> dict:
        match = re.search(r"data-ad-bootstrap='([^']*)'", html)
        if not match:
            match = re.search(r'data-ad-bootstrap="([^"]*)"', html)
        assert match, "assistant dock bootstrap attribute missing"
        raw = (
            match.group(1)
            .replace("&#34;", '"')
            .replace("&quot;", '"')
            .replace("&#39;", "'")
        )
        return json.loads(raw)


if __name__ == "__main__":
    unittest.main()
