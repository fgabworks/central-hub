"""UI wiring tests for Repository Intelligence nested navigation."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

from flask import Flask, render_template_string

from hub.agent_center.db import AgentCenterDb
from hub.agent_center.repository_intelligence import RepositoryIntelligenceService
from hub.agent_center.routes import register_agent_center_routes
from hub.registry.models import Registry, Repository
from hub.registry.status import ui_repo_status
from hub.repository_workspace.routes import register_repository_workspace_routes


ROOT = Path(__file__).resolve().parents[1]


class RepositoryIntelligenceUiTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        root = Path(self.temp.name) / "repo"
        root.mkdir()
        (root / "README.md").write_text("# Demo\n", encoding="utf-8")
        self.repo = Repository(
            id="demo",
            name="Demo Repo",
            type="command",
            enabled=True,
            local_path=str(root),
            working_directory=str(root),
        )
        self.registry = Registry(repositories=[self.repo])
        self.service = RepositoryIntelligenceService(
            AgentCenterDb(Path(self.temp.name) / "agent.db"),
            self.registry,
        )
        self.root = root

        self.app = Flask(__name__, template_folder=str(ROOT / "templates"))
        self.app.secret_key = "ri-ui-tests"
        self.app.config["REGISTRY"] = self.registry
        self.app.config["ADAPTERS"] = None
        self.app.config["AUDIT"] = SimpleNamespace(append=lambda **kwargs: None)
        self.app.config["AGENT_CENTER"] = SimpleNamespace(repository_intelligence=self.service)
        self.app.config["OWNER_TOKEN"] = ""
        ws = MagicMock()
        ws.availability.return_value = {
            "available": True,
            "root": str(root),
            "message": "ok",
        }
        ws.list_runs.return_value = []
        self.app.config["REPO_WORKSPACE"] = ws

        @self.app.get("/repositories/sections/intelligence")
        def repositories_intelligence():
            return "ok", 200

        @self.app.context_processor
        def _inject():
            return {
                "app_name": "Central Hub",
                "app_version": "test",
                "workspace": "work",
                "workspace_labels": {"work": "Work", "personal": "Personal"},
                "nav_items": [],
                "assistant_dock": {
                    "enabled": False,
                    "prefs": {"open": False, "minimized": False, "width": 400},
                    "profile": {"id": "okarun", "label": "AiriX"},
                    "safety": {"message": "read-only"},
                },
                "workspace_console": {
                    "enabled": False,
                    "prefs": {"open": False, "minimized": False, "maximized": False, "height": 280},
                },
                "current_user": None,
                "owner_authenticated": True,
                "topbar_dhis2": {"label": "DHIS2", "class": "badge-disabled"},
                "skip_assistant_dock": True,
            }

        register_repository_workspace_routes(self.app)
        register_agent_center_routes(self.app)
        self.client = self.app.test_client()

    def tearDown(self) -> None:
        self.temp.cleanup()

    def test_intelligence_table_template_is_compact(self) -> None:
        html = (ROOT / "templates" / "repositories_intelligence.html").read_text(encoding="utf-8")
        self.assertIn("ri-table", html)
        self.assertIn("Intelligence Status", html)
        self.assertIn("Indexed Commit", html)
        self.assertIn("Scan &amp; Learn", html)
        self.assertIn("Deep AI Analysis", html)
        self.assertIn("data-ri-action=\"refresh\"", html)
        self.assertIn("ri-more", html)
        self.assertNotIn("ri-grid", html)
        self.assertNotIn("ri-card", html)
        tabs = (ROOT / "templates" / "partials" / "repositories_section_tabs.html").read_text(
            encoding="utf-8"
        )
        self.assertIn("Repositories sections", tabs)

    def test_repository_intelligence_detail_route(self) -> None:
        self.service.scan("demo")
        response = self.client.get("/repositories/demo/intelligence")
        self.assertEqual(response.status_code, 200)
        html = response.get_data(as_text=True)
        self.assertIn("Repository Intelligence", html)
        self.assertIn('data-ri-repo="demo"', html)
        self.assertIn("Last learned", html)
        self.assertIn("Learned categories", html)
        self.assertIn("Recent activity", html)
        self.assertIn("Latest scan telemetry", html)
        self.assertIn("LLM Invoked", html)
        self.assertIn("AI tokens in/out/cached/total", html)
        self.assertIn("Files scanned/indexed/changed", html)
        self.assertIn("Deep AI Analysis", html)
        self.assertIn(">General<", html)
        self.assertIn(">Connection<", html)
        self.assertIn(">Files &amp; Changes<", html)
        self.assertIn(">Logs &amp; History<", html)
        self.assertIn("Refresh Intelligence", html)

    def test_per_repo_tabs_include_intelligence_endpoint(self) -> None:
        # Render tab partial through the detail route context shape.
        with self.app.test_request_context():
            from flask import render_template

            html = render_template(
                "partials/repository_tabs.html",
                repository=self.repo,
                workspace={"available": True},
                active_tab="intelligence",
                inline_section_tabs=True,
                tabs=[
                    {"id": "general", "label": "General", "endpoint": "repository_detail"},
                    {"id": "connection", "label": "Connection", "endpoint": "repository_connect"},
                    {
                        "id": "intelligence",
                        "label": "Repository Intelligence",
                        "endpoint": "repository_intelligence",
                    },
                    {
                        "id": "files_changes",
                        "label": "Files & Changes",
                        "endpoint": "repository_files",
                    },
                    {"id": "settings", "label": "Settings", "endpoint": "repository_settings"},
                    {"id": "logs", "label": "Logs & History", "endpoint": "repository_logs"},
                ],
            )
        self.assertIn("Repository Intelligence", html)
        self.assertIn("is-active", html)
        self.assertIn("/repositories/demo/intelligence", html)

    def test_status_helper_labels_cover_ui_contract(self) -> None:
        status = ui_repo_status(self.repo, None)
        self.assertTrue(status)
        labels = {
            "Current",
            "Learning",
            "Update Available",
            "Not Learned",
            "Failed",
        }
        from hub.agent_center.repository_intelligence import STATUS_LABELS

        self.assertTrue(labels.issubset(set(STATUS_LABELS.values())))


if __name__ == "__main__":
    unittest.main()
