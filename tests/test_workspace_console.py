"""Focused tests for the VS Code-style Workspace Console."""

from __future__ import annotations

import json
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from hub.agent_center.redact import redact_text
from hub.notebook.db import NotebookDatabase
from hub.workspace_console.prefs import (
    clamp_height,
    console_shell_bootstrap,
    load_console_prefs,
    normalize_tab,
    save_console_prefs,
)
from hub.workspace_console.service import WorkspaceConsoleService


class ConsolePrefsTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.db = NotebookDatabase(Path(self.temp.name) / "notebook.db")

    def tearDown(self):
        self.temp.cleanup()

    def test_height_clamp_and_tab_normalize(self):
        self.assertEqual(clamp_height(40), 160)
        self.assertEqual(clamp_height(9999), 640)
        self.assertEqual(normalize_tab("PORTS"), "ports")
        self.assertEqual(normalize_tab("nope"), "problems")

    def test_prefs_isolated_per_workspace(self):
        save_console_prefs(self.db, "work", {"open": True, "height": 320, "tab": "terminal"})
        save_console_prefs(self.db, "personal", {"open": False, "height": 200, "tab": "output"})
        work = load_console_prefs(self.db, "work")
        personal = load_console_prefs(self.db, "personal")
        self.assertTrue(work["open"])
        self.assertEqual(work["height"], 320)
        self.assertEqual(work["tab"], "terminal")
        self.assertFalse(personal["open"])
        self.assertEqual(personal["tab"], "output")

    def test_bootstrap_is_lightweight(self):
        payload = console_shell_bootstrap(self.db, workspace="work")
        self.assertIn("prefs", payload)
        self.assertIn("tabs", payload)
        # Collapsed by default until the user opens it (Ctrl+J / rail).
        self.assertFalse(payload["prefs"]["open"])
        self.assertFalse(payload["prefs"]["minimized"])
        self.assertEqual(payload["prefs"]["tab"], "problems")
        self.assertNotIn("ports", payload)
        self.assertNotIn("problems", payload)
        self.assertFalse(payload["safety"]["free_shell"])
        self.assertTrue(payload["safety"]["controlled_terminal"])


class ConsoleServiceTests(unittest.TestCase):
    def test_problems_aggregate_jobs_and_audit(self):
        class Jobs:
            def list_recent(self, limit=50, status=None):
                return [
                    {
                        "id": "j1",
                        "status": "failed",
                        "error": "boom token=secret-value",
                        "capability_id": "cap",
                        "repository_id": "demo",
                        "updated_at": "t1",
                    }
                ]

        class Audit:
            def list_recent(self, limit=100):
                return [{"action": "X", "ok": False, "detail": "failed OPENAI_API_KEY=sk-abc", "timestamp": "t2", "metadata": {}}]

        class Processes:
            def list_runs(self, repo_id=None, refresh=False):
                return []

        class RepoWs:
            processes = Processes()

        svc = WorkspaceConsoleService(
            registry=None,
            repo_workspace=RepoWs(),
            job_store=Jobs(),
            audit=Audit(),
        )
        data = svc.problems()
        self.assertGreaterEqual(data["count"], 2)
        blob = json.dumps(data)
        self.assertNotIn("secret-value", blob)
        self.assertNotIn("sk-abc", blob)

    def test_terminal_catalog_has_no_free_shell(self):
        class Registry:
            def enabled_repositories(self):
                return []

        svc = WorkspaceConsoleService(registry=Registry(), repo_workspace=object())
        catalog = svc.terminal_catalog()
        self.assertFalse(catalog["free_shell"])

    def test_ports_reuses_summarize_and_marks_ownership(self):
        class Registry:
            repositories = []

        class RepoWs:
            def summarize_local_processes(self, repositories):
                return [
                    {
                        "port": 8080,
                        "pid": 12,
                        "command_redacted": "python app.py",
                        "repo_id": "demo",
                        "repository_name": "Demo",
                        "managed_by_hub": True,
                        "confidence": "High",
                        "view_only": False,
                        "stoppable": True,
                        "identity_token": "abc",
                        "detection_reasons": ["cwd"],
                    }
                ]

        svc = WorkspaceConsoleService(registry=Registry(), repo_workspace=RepoWs())
        ports = svc.ports()
        self.assertEqual(ports["count"], 1)
        self.assertTrue(ports["ports"][0]["managed_by_hub"])
        self.assertFalse(ports["ports"][0]["external"])


class ConsoleRouteTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from app import create_app

        cls.app = create_app()
        cls.app.config.update(TESTING=True)
        cls.client = cls.app.test_client()

    def test_shell_mounts_console_and_css(self):
        html = self.client.get("/work").get_data(as_text=True)
        self.assertIn('id="workspace-console-host"', html)
        self.assertIn("workspace_console.js", html)
        self.assertIn("Problems", html)
        self.assertIn("Debug Console", html)
        self.assertIn("Terminal", html)
        self.assertIn("Ports", html)
        self.assertIn("Ctrl+J", html)
        self.assertIn('id="ar-console"', html)
        self.assertIn("activity-rail", html)
        css = (ROOT / "static" / "css" / "style.css").read_text(encoding="utf-8")
        self.assertIn(".wc-host", css)
        self.assertIn("padding-bottom: var(--wc-height", css)
        self.assertIn("is-ad-open", css)
        self.assertIn(".activity-rail", css)
        js = (ROOT / "static" / "js" / "workspace_console.js").read_text(encoding="utf-8")
        self.assertIn('toLowerCase() === "j"', js)
        self.assertIn("ctrlKey", js)
        self.assertIn("stopPolling", js)
        self.assertIn("document.hidden", js)
        self.assertIn("dedupeFetch", js)

    def test_prefs_api_persists_height_and_tab(self):
        put = self.client.put(
            "/api/workspace-console/prefs",
            json={"open": True, "height": 360, "tab": "ports", "minimized": False},
        )
        self.assertEqual(put.status_code, 200)
        get = self.client.get("/api/workspace-console/prefs").get_json()
        self.assertTrue(get["prefs"]["open"])
        self.assertEqual(get["prefs"]["height"], 360)
        self.assertEqual(get["prefs"]["tab"], "ports")

    def test_bootstrap_does_not_scan_ports(self):
        with mock.patch.object(
            self.app.config["WORKSPACE_CONSOLE"],
            "ports",
            side_effect=AssertionError("ports must stay lazy"),
        ):
            page = self.client.get("/work")
            boot = self.client.get("/api/workspace-console/bootstrap")
        self.assertEqual(page.status_code, 200)
        self.assertEqual(boot.status_code, 200)
        self.assertIn("prefs", boot.get_json())

    def test_terminal_rejects_missing_profile(self):
        # Owner auth may be required; either 400 validation or 401/403 is acceptable.
        resp = self.client.post(
            "/api/workspace-console/terminal/start",
            json={"repository_id": "", "profile_id": ""},
        )
        self.assertIn(resp.status_code, {400, 401, 403})

    def test_ports_stop_requires_identity(self):
        resp = self.client.post(
            "/api/workspace-console/ports/stop",
            json={"repository_id": "missing", "pid": 1, "identity_token": "x", "confirm": True},
        )
        self.assertIn(resp.status_code, {400, 401, 403, 404})

    def test_mobile_css_present(self):
        css = (ROOT / "static" / "css" / "style.css").read_text(encoding="utf-8")
        self.assertIn("is-wc-mobile", css)
        self.assertIn("@media (max-width: 960px)", css)

    def test_no_performance_regression_shell(self):
        with mock.patch.object(
            self.app.config["REPO_WORKSPACE"],
            "summarize_local_processes",
            side_effect=AssertionError("shell must not scan processes"),
        ):
            times = []
            for _ in range(5):
                start = time.perf_counter()
                resp = self.client.get("/work")
                times.append((time.perf_counter() - start) * 1000)
                self.assertEqual(resp.status_code, 200)
            times.sort()
            p95 = times[int(len(times) * 0.95) - 1]
            self.assertLessEqual(p95, 1000.0)


class ConsoleRedactionTests(unittest.TestCase):
    def test_output_redacts_env_secrets(self):
        text = redact_text("Loading .env\nOPENAI_API_KEY=sk-should-hide\nready")
        self.assertNotIn("sk-should-hide", text)


if __name__ == "__main__":
    unittest.main()
