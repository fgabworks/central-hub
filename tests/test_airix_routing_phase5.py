"""AiriX Smart Routing Phase 5 — cost, RBAC, findings relevance, isolation."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

from hub.agent_center.db import AgentCenterDb
from hub.agent_center.routing import AgentRouterService
from hub.agent_center.routing.budget import assert_budget_allows, budget_snapshot
from hub.agent_center.routing.cost import (
    cost_intelligence,
    estimate_cost_usd,
    parse_usage,
    usage_variance,
)
from hub.agent_center.routing.findings import select_relevant_findings
from hub.agent_center.routing.history import RoutingHistoryStore
from hub.agent_center.routing.models import PromptClassification, RoutingSettings
from hub.agent_center.routing.rbac import (
    RoutingAclStore,
    check_execution_allowed,
    permissions_for_role,
)
from hub.agent_center.service import AgentCenterError


class _FakeAgentCenter:
    def __init__(self, usage: dict[str, Any] | None = None) -> None:
        self.started: list[dict[str, Any]] = []
        self.cancelled: list[str] = []
        self.runs: dict[str, dict[str, Any]] = {}
        self.registry = MagicMock()
        self.notebook = None
        self.sql_store = None
        self.uid_index = None
        self.email = None
        self.calendar = None
        self.job_store = None
        self.audit_store = None
        self.dhis2_reports = None
        self.notepad_factory = None
        self._n = 0
        self.usage = usage or {
            "input_tokens": 40,
            "output_tokens": 80,
            "total_tokens": 120,
        }

    def start_run(self, payload: dict[str, Any]) -> dict[str, Any]:
        self._n += 1
        run_id = f"run-{self._n}"
        row = {
            "id": run_id,
            "status": "succeeded",
            "answer": f"Detailed analysis from {payload.get('agent_id')} with enough content.",
            "agent_id": payload.get("agent_id"),
            "prompt": payload.get("prompt"),
            "tool_ids": list(payload.get("tool_ids") or []),
            "repository_ids": list(payload.get("repository_ids") or []),
            "finished_at": "2026-08-10T00:00:00+00:00",
            "usage": dict(self.usage),
        }
        self.started.append(payload)
        self.runs[run_id] = row
        return dict(row)

    def cancel_run(self, run_id: str, *, profile_id: str = "okarun") -> dict[str, Any]:
        self.cancelled.append(run_id)
        return {"id": run_id, "status": "cancelled"}

    def get_run(self, run_id: str, *, profile_id: str = "okarun") -> dict[str, Any]:
        if run_id not in self.runs:
            raise AgentCenterError("not found", code="not_found")
        return dict(self.runs[run_id])

    def get_agent(self, agent_id: str) -> Any:
        return MagicMock() if agent_id else None

    def repositories(self, profile_id: str = "okarun") -> list[dict[str, Any]]:
        return [{"id": "sample-cli"}]


def _availability() -> dict[str, dict[str, Any]]:
    return {
        "grok": {"id": "grok", "status": "available", "runnable": True},
        "hub-simulator": {"id": "hub-simulator", "status": "available", "runnable": True},
        "codex": {"id": "codex", "status": "available", "runnable": True},
    }


def _classification(**overrides: Any) -> PromptClassification:
    base = dict(
        task_type="dhis2_investigation",
        complexity=40,
        risk="medium",
        estimated_scope_files=2,
        context_size="medium",
        needs_coding=False,
        needs_testing=False,
        needs_architecture=False,
        deterministic_capable=False,
        signals=["dhis2", "uid"],
    )
    base.update(overrides)
    return PromptClassification(**base)


class AirixPhase5Tests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.db = AgentCenterDb(Path(self.tmp.name) / "agent.db")
        self.history = RoutingHistoryStore(self.db)
        self.acl = RoutingAclStore(self.db)
        self.fake = _FakeAgentCenter()
        self.router = AgentRouterService(
            availability_loader=_availability,
            agent_center=self.fake,  # type: ignore[arg-type]
            history=self.history,
            acl=self.acl,
        )

    def test_actual_usage_recording(self) -> None:
        prompt = (
            "Investigate why the DHIS2 analytics SQL query for program indicators "
            "returns empty rows for this org unit — debug the join and UID mapping"
        )
        result = self.router.execute_route(
            prompt, orchestrate=False, agent_override="grok"
        )
        self.assertEqual(result["phase"], 5)
        usage = result["execution"].get("usage") or {}
        self.assertEqual(usage.get("total_tokens"), 120)
        self.assertEqual(usage.get("input_tokens"), 40)
        self.assertEqual(usage.get("output_tokens"), 80)
        self.assertEqual(usage.get("usage_source"), "actual")
        events = self.history.list_events(workspace="work", actor="owner")
        self.assertTrue(events)
        self.assertEqual(events[0].get("actual_tokens"), 120)
        self.assertEqual(events[0].get("usage_source"), "actual")

    def test_estimated_fallback_when_provider_usage_unavailable(self) -> None:
        self.fake.usage = {}
        prompt = (
            "Investigate why the DHIS2 analytics SQL query for program indicators "
            "returns empty rows for this org unit — debug the join and UID mapping"
        )
        result = self.router.execute_route(
            prompt, orchestrate=False, agent_override="grok"
        )
        usage = result["execution"].get("usage") or {}
        self.assertEqual(usage.get("usage_source"), "estimate")
        self.assertIsNone(usage.get("total_tokens"))
        events = self.history.list_events(workspace="work", actor="owner")
        self.assertEqual(events[0].get("usage_source"), "estimate")
        self.assertIsNone(events[0].get("actual_tokens"))
        self.assertIsNotNone(events[0].get("estimated_tokens"))

    def test_budget_calculations_and_cost_metrics(self) -> None:
        settings = RoutingSettings(
            daily_token_budget=1000,
            monthly_token_budget=5000,
            per_task_max_tokens=800,
            enable_cost_estimates=True,
            price_per_mtok={"grok": 2.0, "default": 1.0},
        )
        self.history.record_event(
            {
                "provider_id": "grok",
                "tier": "T2",
                "task_type": "coding",
                "status": "completed",
                "outcome": "success",
                "actual_tokens": 200,
                "estimated_tokens": 250,
                "estimated_usage": "Low",
                "t0_llm_avoided": 0,
                "actor": "owner",
            }
        )
        self.history.record_event(
            {
                "provider_id": "deterministic",
                "tier": "T0",
                "task_type": "lookup",
                "status": "completed",
                "outcome": "success",
                "actual_tokens": 0,
                "estimated_tokens": 200,
                "t0_llm_avoided": 1,
                "actor": "owner",
            }
        )
        events = self.history.list_events(workspace="work", actor="owner")
        snap = budget_snapshot(events, settings, task_estimated_tokens=100)
        self.assertEqual(snap["daily_used"], 200)
        self.assertEqual(snap["daily_remaining"], 800)
        cost = cost_intelligence(events, settings, budget=snap)
        self.assertEqual(cost["t0_savings"]["runs_avoided"], 1)
        self.assertGreater(cost["t0_savings"]["estimated_tokens_avoided"], 0)
        self.assertTrue(cost["pricing_configured"])
        self.assertAlmostEqual(
            estimate_cost_usd(1_000_000, provider_id="grok", settings=settings) or 0,
            2.0,
        )
        var = usage_variance(250, 200)
        self.assertEqual(var["delta_tokens"], -50)
        parsed = parse_usage({"prompt_tokens": 10, "completion_tokens": 5})
        self.assertEqual(parsed["total_tokens"], 15)
        with self.assertRaises(AgentCenterError) as ctx:
            assert_budget_allows(
                budget_snapshot(events, settings, task_estimated_tokens=900),
                additional_tokens=0,
            )
        self.assertEqual(ctx.exception.code, "budget_exceeded")

    def test_rbac_allow_deny(self) -> None:
        self.acl.set_role("alice", "viewer", workspace="work")
        self.acl.set_role("bob", "analyst", workspace="work")
        viewer = permissions_for_role("viewer")
        analyst = permissions_for_role("analyst")
        ok, _ = check_execution_allowed(perms=viewer, provider_id="grok")
        self.assertFalse(ok)
        ok2, _ = check_execution_allowed(perms=analyst, provider_id="grok")
        self.assertTrue(ok2)
        ok3, reason = check_execution_allowed(perms=analyst, provider_id="codex")
        self.assertFalse(ok3)
        self.assertIn("provider.codex", reason)

        with self.assertRaises(AgentCenterError) as ctx:
            self.router.execute_route(
                "Look up the UID for Philippines",
                actor="alice",
                orchestrate=False,
            )
        self.assertEqual(ctx.exception.code, "permission_denied")

        result = self.router.execute_route(
            "Look up the UID for Philippines",
            actor="bob",
            orchestrate=True,
        )
        self.assertIn(result["orchestration"]["status"], {"completed", "paused_for_approval", "paused"})

    def test_codex_approval_permission(self) -> None:
        self.acl.set_role("dev1", "developer", workspace="work")
        perms = permissions_for_role("developer")
        ok, _ = check_execution_allowed(
            perms=perms, provider_id="codex", approve_codex=True
        )
        self.assertTrue(ok)
        self.acl.set_role("ana1", "analyst", workspace="work")
        with self.assertRaises(AgentCenterError) as ctx:
            self.router.execute_route(
                "Investigate DHIS2 SQL join for indicators",
                actor="ana1",
                orchestrate=False,
                agent_override="codex",
                approve_codex=True,
            )
        self.assertEqual(ctx.exception.code, "permission_denied")

    def test_live_and_tool_restrictions(self) -> None:
        self.acl.set_role("dev2", "developer", workspace="work")
        perms = permissions_for_role("developer")
        ok, reason = check_execution_allowed(
            perms=perms,
            provider_id="grok",
            live_requested=True,
        )
        self.assertFalse(ok)
        self.assertIn("live.access", reason)
        ok2, reason2 = check_execution_allowed(
            perms=permissions_for_role("analyst"),
            provider_id="deterministic",
            tool_ids=["repo_search"],
        )
        self.assertFalse(ok2)
        self.assertIn("tools.repository", reason2)

        with self.assertRaises(AgentCenterError) as ctx:
            self.router.execute_route(
                "Query the live DHIS2 production server for org unit UIDs",
                actor="dev2",
                orchestrate=False,
                agent_override="grok",
            )
        self.assertEqual(ctx.exception.code, "permission_denied")

    def test_relevant_finding_retrieval_and_exclusion(self) -> None:
        related = {
            "id": "f1",
            "task_type": "dhis2_investigation",
            "keywords": ["dhis2", "uid", "orgunit", "indicator"],
            "summary": "DHIS2 org unit UID mapping failed for indicator join.",
            "provider_id": "grok",
            "hit_count": 0,
        }
        unrelated = {
            "id": "f2",
            "task_type": "css_ui",
            "keywords": ["padding", "button", "css"],
            "summary": "Dashboard button padding was too large on mobile.",
            "provider_id": "grok",
            "hit_count": 0,
        }
        selected = select_relevant_findings(
            [related, unrelated],
            prompt="Investigate DHIS2 org unit UID mapping for indicators",
            classification=_classification(),
            max_items=3,
        )
        ids = {s["id"] for s in selected}
        self.assertIn("f1", ids)
        self.assertNotIn("f2", ids)
        self.assertTrue(selected[0].get("reused"))
        self.assertGreater(selected[0].get("relevance_score") or 0, 1.0)

    def test_findings_actor_isolation(self) -> None:
        self.history.save_finding(
            {
                "task_type": "dhis2_investigation",
                "keywords": ["dhis2", "uid"],
                "summary": "Alice-only DHIS2 UID finding about org units.",
                "provider_id": "grok",
            },
            workspace="work",
            actor="alice",
        )
        alice = self.history.list_findings(
            workspace="work", task_type="dhis2_investigation", actor="alice"
        )
        bob = self.history.list_findings(
            workspace="work", task_type="dhis2_investigation", actor="bob"
        )
        self.assertEqual(len(alice), 1)
        self.assertEqual(bob, [])

    def test_routing_respects_permissions_and_budgets(self) -> None:
        settings = RoutingSettings(
            daily_token_budget=50,
            monthly_token_budget=0,
            per_task_max_tokens=0,
            enable_orchestration=False,
        )
        self.router.get_settings = lambda workspace="work": settings  # type: ignore[method-assign]
        self.history.record_event(
            {
                "provider_id": "grok",
                "actual_tokens": 60,
                "status": "completed",
                "outcome": "success",
                "actor": "owner",
                "task_type": "coding",
            }
        )
        with self.assertRaises(AgentCenterError) as ctx:
            self.router.execute_route(
                "Investigate DHIS2 SQL join for indicators",
                orchestrate=False,
                agent_override="grok",
            )
        self.assertEqual(ctx.exception.code, "budget_exceeded")

        # History must not override permission deny.
        self.acl.set_role("viewer1", "viewer", workspace="work")
        with self.assertRaises(AgentCenterError) as ctx2:
            self.router.execute_route(
                "Look up the UID for Philippines",
                actor="viewer1",
                orchestrate=False,
            )
        self.assertEqual(ctx2.exception.code, "permission_denied")

    def test_t0_savings_metrics(self) -> None:
        for _ in range(2):
            self.history.record_event(
                {
                    "provider_id": "deterministic",
                    "tier": "T0",
                    "task_type": "lookup",
                    "status": "completed",
                    "outcome": "success",
                    "t0_llm_avoided": 1,
                    "actual_tokens": 0,
                    "estimated_usage": "Very Low",
                    "actor": "owner",
                }
            )
        analytics = self.router.analytics(workspace="work", actor="owner")
        self.assertEqual(analytics["phase"], 5)
        self.assertEqual(analytics["t0_llm_avoided"], 2)
        self.assertEqual(analytics["cost"]["t0_savings"]["runs_avoided"], 2)
        self.assertGreater(analytics["cost"]["t0_savings"]["estimated_tokens_avoided"], 0)

    def test_prior_findings_shown_on_recommend(self) -> None:
        self.history.save_finding(
            {
                "task_type": "dhis2_investigation",
                "keywords": ["dhis2", "uid", "orgunit", "indicator", "join"],
                "summary": "UID join failed because org unit level filter was wrong.",
                "provider_id": "grok",
            },
            actor="owner",
        )
        rec = self.router.recommend_route(
            "Investigate DHIS2 org unit UID mapping for indicators"
        )
        self.assertTrue(rec.prior_findings)
        self.assertTrue(rec.prior_findings[0].get("reused"))
        self.assertIn("permissions", rec.public())


class AirixPhase5RouteTests(unittest.TestCase):
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
        os.environ["CENTRAL_HUB_OWNER_TOKEN"] = ""
        import app as app_mod
        import hub.settings as settings_mod

        importlib.reload(settings_mod)
        importlib.reload(app_mod)
        from app import create_app

        cls.app = create_app()
        cls.client = cls.app.test_client()

    @classmethod
    def tearDownClass(cls) -> None:
        cls._tmp.cleanup()

    def test_recommend_phase5_shape(self) -> None:
        resp = self.client.post(
            "/api/assistants/airix/routing/recommend",
            json={"prompt": "Look up the UID for Philippines"},
        )
        self.assertEqual(resp.status_code, 200)
        body = resp.get_json()
        self.assertEqual(body["phase"], 5)
        self.assertIn("permissions", body["recommendation"])
        self.assertIn("budget", body["recommendation"])

    def test_permissions_api(self) -> None:
        resp = self.client.get("/api/assistants/airix/routing/permissions")
        self.assertEqual(resp.status_code, 200)
        body = resp.get_json()
        self.assertEqual(body["permissions"]["role_id"], "admin")
        self.assertIn("ai.execute", body["permissions"]["permissions"])

    def test_dock_phase5(self) -> None:
        html = self.client.get("/work").get_data(as_text=True)
        self.assertIn("Phase 5", html)
        self.assertIn("permissions_url", html)


if __name__ == "__main__":
    unittest.main()
