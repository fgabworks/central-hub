"""Focused UI, isolation, persistence, and lazy-load tests for the assistant dock."""

from __future__ import annotations

import json
import re
import tempfile
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
        self.assertEqual(clamp_width("bad"), 380)

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
        self.assertIn("Okarun", html)
        self.assertIn("Read-only mode. No actions are executed.", html)
        self.assertIn("ad-tab-conversation", html)
        self.assertIn("ad-tab-output", html)
        self.assertIn('id="ad-prompt"', html)
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

    def test_assistant_center_skips_dock(self) -> None:
        self._set_workspace("work")
        html = self.client.get("/work/okarun").get_data(as_text=True)
        self.assertNotIn('id="assistant-dock-host"', html)
        self.assertNotIn("assistant_dock.js", html)

    def test_desktop_resize_classes_present(self) -> None:
        css = (Path(__file__).resolve().parents[1] / "static" / "css" / "style.css").read_text(
            encoding="utf-8"
        )
        self.assertIn(".app-shell.is-ad-open:not(.is-ad-mobile)", css)
        self.assertIn(
            "grid-template-columns: var(--sidebar-w) minmax(0, 1fr) var(--ad-width",
            css,
        )
        self.assertIn(".app-shell.is-ad-mobile.is-ad-open .ad-host", css)
        self.assertIn("@media (max-width: 960px)", css)

    def test_prefs_api_persists_per_workspace(self) -> None:
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

    def test_js_lazy_loads_agents_only_on_open(self) -> None:
        js_path = Path(__file__).resolve().parents[1] / "static" / "js" / "assistant_dock.js"
        js = js_path.read_text(encoding="utf-8")
        self.assertIn("function ensureAgents", js)
        self.assertIn("if (prefs.open) ensureAgents()", js)
        self.assertIn('apiBase + "/agents"', js)
        self.assertNotIn("speechSynthesis", js)
        self.assertNotIn("webkitSpeechRecognition", js)

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
