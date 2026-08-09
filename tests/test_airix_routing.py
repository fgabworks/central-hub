"""AiriX Smart Routing Phase 1 — classify + recommend only."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from hub.agent_center.routing import AgentRouterService
from hub.agent_center.routing.models import RoutingSettings
from hub.agent_center.routing.providers import ProviderRegistry
from hub.agent_center.routing.settings import load_routing_settings, save_routing_settings
from hub.notebook.db import NotebookDatabase


class AirixRoutingUnitTests(unittest.TestCase):
    def setUp(self) -> None:
        self.router = AgentRouterService()

    def test_simple_lookup_routes_t0_deterministic(self) -> None:
        prompt = "Look up the UID for Philippines and show me the status of recent jobs"
        rec = self.router.recommend_route(prompt)
        self.assertEqual(rec.task_type, "lookup")
        self.assertLessEqual(rec.complexity, 30)
        self.assertEqual(rec.risk, "low")
        self.assertEqual(rec.recommended_agent, "deterministic")
        self.assertEqual(rec.recommended_tier, "T0")
        self.assertFalse(rec.approval_required)
        self.assertIn(rec.estimated_usage, {"Very Low", "Low"})
        self.assertGreaterEqual(rec.confidence, 0.5)
        self.assertTrue(rec.classification.deterministic_capable)

    def test_css_fix_routes_low_cost_or_grok(self) -> None:
        prompt = "Fix the CSS padding on the dashboard button style so the hover color matches"
        rec = self.router.recommend_route(prompt)
        self.assertEqual(rec.task_type, "css_ui")
        self.assertLess(rec.complexity, 50)
        self.assertIn(rec.recommended_agent, {"low-cost", "grok", "openai-api"})
        self.assertIn(rec.recommended_tier, {"T1", "T2"})
        self.assertNotEqual(rec.recommended_agent, "codex")
        self.assertFalse(rec.approval_required)

    def test_sql_dhis2_investigation_prefers_grok(self) -> None:
        prompt = (
            "Investigate why the DHIS2 analytics SQL query for program indicators "
            "returns empty rows for this org unit — debug the join and UID mapping"
        )
        rec = self.router.recommend_route(prompt)
        self.assertIn(rec.task_type, {"dhis2_investigation", "sql_investigation"})
        self.assertGreaterEqual(rec.complexity, 40)
        self.assertEqual(rec.recommended_agent, "grok")
        self.assertEqual(rec.recommended_tier, "T2")
        self.assertNotEqual(rec.recommended_agent, "codex")
        self.assertIn(rec.estimated_usage, {"Low", "Moderate", "High"})

    def test_large_architecture_refactor_routes_codex_with_approval(self) -> None:
        prompt = (
            "Redesign the architecture and perform a large refactor across 12 modules "
            "in the entire codebase, including cross-module ownership boundaries and "
            "a breaking change migration plan"
        )
        rec = self.router.recommend_route(prompt)
        self.assertIn(rec.task_type, {"architecture", "refactor"})
        self.assertGreaterEqual(rec.complexity, 70)
        self.assertEqual(rec.risk, "high")
        self.assertEqual(rec.recommended_agent, "codex")
        self.assertEqual(rec.recommended_tier, "T3")
        self.assertTrue(rec.approval_required)
        self.assertEqual(rec.estimated_usage, "High")

    def test_never_auto_prefer_codex_for_simple_work(self) -> None:
        for prompt in (
            "What is the status of open notebook notes?",
            "Change button CSS color to blue",
            "Explain this Python function briefly",
        ):
            rec = self.router.recommend_route(prompt)
            self.assertNotEqual(rec.recommended_agent, "codex", prompt)

    def test_list_providers_and_estimate_usage(self) -> None:
        providers = self.router.list_available_providers()
        ids = {p["id"] for p in providers}
        self.assertIn("deterministic", ids)
        self.assertIn("grok", ids)
        self.assertIn("codex", ids)
        usage = self.router.estimate_usage(prompt="Look up UID DcGhhRsspFX")
        self.assertIn(usage["estimated_usage"], {"Very Low", "Low", "Moderate", "High"})

    def test_build_execution_plan_is_non_executing(self) -> None:
        plan = self.router.build_execution_plan("Look up recent audit events")
        public = plan.public()
        self.assertEqual(public["status"], "planned")
        self.assertFalse(public["execute"])
        self.assertEqual(public["phase"], 1)
        self.assertIn("Phase 2", " ".join(plan.steps))

    def test_cheapest_mode_keeps_lookup_on_t0(self) -> None:
        rec = self.router.recommend_route(
            "Find UID for the facility and list recent jobs",
            settings=RoutingSettings(mode="cheapest", prefer_deterministic=True),
        )
        self.assertEqual(rec.recommended_tier, "T0")

    def test_settings_persist(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        db = NotebookDatabase(Path(tmp.name) / "notebook.db")
        saved = save_routing_settings(
            db,
            "work",
            {
                "mode": "best_quality",
                "prefer_deterministic": False,
                "prefer_grok_for_routine": True,
                "require_approval_before_codex": True,
                "allow_escalation": False,
                "max_retries": 3,
            },
        )
        self.assertEqual(saved.mode, "best_quality")
        self.assertFalse(saved.prefer_deterministic)
        self.assertEqual(saved.max_retries, 3)
        loaded = load_routing_settings(db, "work")
        self.assertEqual(loaded.mode, "best_quality")
        self.assertFalse(loaded.allow_escalation)

    def test_provider_registry_exposes_required_fields(self) -> None:
        row = ProviderRegistry().get("grok")
        assert row is not None
        pub = row.public()
        for key in (
            "id",
            "label",
            "tier",
            "cost_tier",
            "speed",
            "context_capacity",
            "capabilities",
            "tools",
            "requires_approval",
            "available",
        ):
            self.assertIn(key, pub)


class AirixRoutingRouteTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        import importlib
        import os
        import tempfile
        from pathlib import Path

        cls._tmp = tempfile.TemporaryDirectory()
        root = Path(cls._tmp.name)
        os.environ["CENTRAL_HUB_NOTEBOOK_DATABASE"] = str(root / "notebook.db")
        os.environ["CENTRAL_HUB_AGENT_DATABASE"] = str(root / "agent_center.db")
        os.environ["CENTRAL_HUB_DATABASE"] = str(root / "hub.db")
        import app as app_mod
        import hub.settings as settings_mod

        importlib.reload(settings_mod)
        importlib.reload(app_mod)
        from app import create_app

        cls.app = create_app()
        cls.app.config["TESTING"] = True
        cls.client = cls.app.test_client()

    @classmethod
    def tearDownClass(cls) -> None:
        cls._tmp.cleanup()

    def test_recommend_api_phase1(self) -> None:
        resp = self.client.post(
            "/api/assistants/okarun/routing/recommend",
            json={"prompt": "Look up the UID for Philippines"},
        )
        self.assertEqual(resp.status_code, 200)
        body = resp.get_json()
        self.assertTrue(body["ok"])
        self.assertEqual(body["phase"], 1)
        rec = body["recommendation"]
        self.assertEqual(rec["recommended_agent"], "deterministic")
        self.assertEqual(body["plan"]["execute"], False)
        self.assertEqual(body["plan"]["phase"], 1)
    def test_providers_and_settings_api(self) -> None:
        providers = self.client.get("/api/assistants/okarun/routing/providers")
        self.assertEqual(providers.status_code, 200)
        ids = {p["id"] for p in providers.get_json()["providers"]}
        self.assertIn("deterministic", ids)
        self.assertIn("codex", ids)

        put = self.client.put(
            "/api/assistants/okarun/routing/settings",
            json={"mode": "cheapest", "max_retries": 2},
        )
        self.assertEqual(put.status_code, 200)
        self.assertEqual(put.get_json()["settings"]["mode"], "cheapest")

        get = self.client.get("/api/assistants/okarun/routing/settings")
        self.assertEqual(get.get_json()["settings"]["mode"], "cheapest")

    def test_aira_recommend_rejected(self) -> None:
        resp = self.client.post(
            "/api/assistants/aira/routing/recommend",
            json={"prompt": "hello"},
        )
        self.assertEqual(resp.status_code, 400)

    def test_work_dock_includes_smart_routing_ui(self) -> None:
        html = self.client.get("/work").get_data(as_text=True)
        self.assertIn("ad-routing-card", html)
        self.assertIn("Use Recommended", html)
        self.assertIn("Choose Agent", html)
        self.assertIn("smart_routing", html)
        self.assertIn("AiriX Smart Routing", html)


if __name__ == "__main__":
    unittest.main()
