"""AiriX Smart Routing Phase 4 — budgets, orchestration, roles, resume, isolation."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

from hub.agent_center.db import AgentCenterDb
from hub.agent_center.routing import AgentRouterService
from hub.agent_center.routing.budget import assert_budget_allows, budget_snapshot
from hub.agent_center.routing.execution import prompt_only_fingerprint
from hub.agent_center.routing.history import RoutingHistoryStore
from hub.agent_center.routing.models import RoutingSettings
from hub.agent_center.routing.orchestrate import build_orchestration_plan
from hub.agent_center.routing.roles import detect_role
from hub.agent_center.service import AgentCenterError


class _FakeAgentCenter:
    def __init__(self) -> None:
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
            "usage": {"total_tokens": 120},
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


class AirixPhase4Tests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.history = RoutingHistoryStore(AgentCenterDb(Path(self.tmp.name) / "agent.db"))
        self.fake = _FakeAgentCenter()
        self.router = AgentRouterService(
            availability_loader=_availability,
            agent_center=self.fake,  # type: ignore[arg-type]
            history=self.history,
        )

    def test_budget_enforcement_hard_stop(self) -> None:
        settings = RoutingSettings(
            daily_token_budget=100,
            monthly_token_budget=0,
            per_task_max_tokens=0,
            enable_orchestration=False,
        )
        # Seed usage over daily budget.
        for _ in range(3):
            self.history.record_event(
                {
                    "provider_id": "grok",
                    "tier": "T2",
                    "task_type": "coding",
                    "status": "completed",
                    "outcome": "success",
                    "actual_tokens": 50,
                    "actor": "owner",
                    "workspace": "work",
                }
            )
        events = self.history.list_events(workspace="work", actor="owner")
        snap = budget_snapshot(events, settings, task_estimated_tokens=800)
        with self.assertRaises(AgentCenterError) as ctx:
            assert_budget_allows(snap, additional_tokens=800)
        self.assertEqual(ctx.exception.code, "budget_exceeded")

        # Wire through execute_route with tiny budget settings via save on fake notebook db.
        # Directly patch get_settings.
        self.router.get_settings = lambda workspace="work": settings  # type: ignore[method-assign]
        prompt = (
            "Investigate why the DHIS2 analytics SQL query for program indicators "
            "returns empty rows for this org unit — debug the join and UID mapping"
        )
        with self.assertRaises(AgentCenterError) as ctx2:
            self.router.execute_route(prompt, orchestrate=False, agent_override="grok")
        self.assertEqual(ctx2.exception.code, "budget_exceeded")

    def test_orchestration_order_deterministic_first(self) -> None:
        prompt = (
            "Investigate why the DHIS2 analytics SQL query for program indicators "
            "returns empty rows for this org unit — debug the join and UID mapping"
        )
        rec = self.router.recommend_route(prompt)
        role = detect_role(prompt, rec.classification)
        steps = build_orchestration_plan(
            recommendation=rec,
            role=role,
            settings=RoutingSettings(enable_orchestration=True, max_orchestration_steps=4),
        )
        self.assertGreaterEqual(len(steps), 2)
        self.assertEqual(steps[0].provider_id, "deterministic")
        self.assertEqual(steps[0].id, "step_tool_lookup")
        kinds = [s.kind for s in steps]
        self.assertIn("tool", kinds)
        # Codex never first and never without approval flag when present.
        for s in steps:
            if s.provider_id == "codex":
                self.assertTrue(s.approval_required)
                self.assertNotEqual(steps[0].provider_id, "codex")

    def test_stop_when_lookup_solves_task(self) -> None:
        prompt = "Look up the UID for Philippines and show me the status of recent jobs"
        result = self.router.execute_route(prompt, orchestrate=True)
        orch = result["orchestration"]
        self.assertEqual(orch["status"], "completed")
        self.assertTrue(orch["completed_steps"])
        self.assertEqual(orch["completed_steps"][0], "step_tool_lookup")
        # Should not have started Grok for a solved T0 lookup.
        self.assertEqual(self.fake.started, [])
        self.assertIn("Solved by deterministic", orch.get("stopped_reason") or "")

    def test_role_routing(self) -> None:
        dhis = detect_role(
            "Investigate DHIS2 org unit UID mapping",
            self.router.classify_request("Investigate DHIS2 org unit UID mapping"),
        )
        self.assertEqual(dhis.id, "dhis2")
        sql = detect_role(
            "Debug the SQL join for the analytics table",
            self.router.classify_request("Debug the SQL join for the analytics table"),
        )
        self.assertEqual(sql.id, "sql_data")
        ui = detect_role(
            "Fix CSS padding on the dashboard button",
            self.router.classify_request("Fix CSS padding on the dashboard button"),
        )
        self.assertEqual(ui.id, "ui_playwright")
        rec = self.router.recommend_route("Investigate DHIS2 org unit UID mapping")
        self.assertEqual(rec.role_id, "dhis2")

    def test_resume_skips_completed_expensive_steps(self) -> None:
        prompt = (
            "Investigate why the DHIS2 analytics SQL query for program indicators "
            "returns empty rows for this org unit — debug the join and UID mapping"
        )
        fp = prompt_only_fingerprint(prompt)
        self.history.save_session(
            {
                "workspace": "work",
                "actor": "owner",
                "prompt_fingerprint": fp,
                "prompt_preview": prompt[:80],
                "role_id": "dhis2",
                "status": "paused",
                "completed_steps": ["step_tool_lookup", "step_repo_search", "step_ai_analysis"],
                "findings": [{"summary": "prior join mismatch", "step_id": "step_ai_analysis"}],
                "partial_summary": "prior join mismatch",
                "actual_tokens": 120,
            }
        )
        rec = self.router.recommend_route(prompt)
        # With completed AI analysis, only Codex may remain pending.
        pending = [s for s in rec.orchestration if s.get("status") == "pending"]
        skipped = [s for s in rec.orchestration if s.get("status") == "skipped"]
        self.assertTrue(skipped)
        self.assertTrue(all(s["id"] != "step_ai_analysis" or s["status"] == "skipped" for s in rec.orchestration))
        # Resume execute should not re-run Grok for skipped analysis.
        result = self.router.execute_route(prompt, orchestrate=True, approve_codex=False)
        # Should pause at Codex without re-running prior AI if skipped.
        self.assertIn(result["orchestration"]["status"], {"paused_for_approval", "completed", "failed", "paused", "blocked"})
        # No duplicate grok starts from skipped analysis step.
        grok_starts = [s for s in self.fake.started if s.get("agent_id") == "grok"]
        self.assertEqual(len(grok_starts), 0)

    def test_codex_still_requires_approval(self) -> None:
        prompt = (
            "Redesign the architecture and perform a large refactor across 12 modules "
            "in the entire codebase, including cross-module ownership boundaries and "
            "a breaking change migration plan"
        )
        result = self.router.execute_route(prompt, orchestrate=True, approve_codex=False)
        self.assertEqual(result["orchestration"]["status"], "paused_for_approval")
        self.assertIn("approval", (result["orchestration"].get("stopped_reason") or "").lower())
        codex_starts = [s for s in self.fake.started if s.get("agent_id") == "codex"]
        self.assertEqual(codex_starts, [])
        self.assertEqual(result["execution"]["status"], "paused_for_approval")
        self.assertTrue(result["execution"].get("terminal"))

    def test_isolation_by_actor(self) -> None:
        self.history.record_event(
            {
                "provider_id": "grok",
                "tier": "T2",
                "task_type": "coding",
                "status": "completed",
                "outcome": "success",
                "actual_tokens": 900,
                "actor": "alice",
                "workspace": "work",
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
                "t0_llm_avoided": True,
                "actor": "bob",
                "workspace": "work",
            }
        )
        alice = self.history.list_events(workspace="work", actor="alice")
        bob = self.history.list_events(workspace="work", actor="bob")
        self.assertEqual(len(alice), 1)
        self.assertEqual(alice[0]["actor"], "alice")
        self.assertEqual(len(bob), 1)
        self.assertEqual(bob[0]["actor"], "bob")
        sess_a = self.history.save_session(
            {
                "workspace": "work",
                "actor": "alice",
                "prompt_fingerprint": "fp-a",
                "status": "active",
                "completed_steps": ["step_tool_lookup"],
            }
        )
        self.assertIsNone(
            self.history.get_session(sess_a["id"], workspace="work", actor="bob")
        )
        self.assertIsNotNone(
            self.history.get_session(sess_a["id"], workspace="work", actor="alice")
        )


class AirixPhase4RouteTests(unittest.TestCase):
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
        cls.app.config["TESTING"] = True
        cls.client = cls.app.test_client()

    @classmethod
    def tearDownClass(cls) -> None:
        cls._tmp.cleanup()

    def test_recommend_includes_role_and_orchestration(self) -> None:
        resp = self.client.post(
            "/api/assistants/airix/routing/recommend",
            json={"prompt": "Look up the UID for Philippines"},
        )
        self.assertEqual(resp.status_code, 200)
        body = resp.get_json()
        self.assertEqual(body["phase"], 5)
        self.assertIn("role_id", body["recommendation"])
        self.assertIn("orchestration", body["recommendation"])
        self.assertIn("budget", body["recommendation"])

    def test_roles_api(self) -> None:
        resp = self.client.get("/api/assistants/airix/routing/roles")
        self.assertEqual(resp.status_code, 200)
        ids = {r["id"] for r in resp.get_json()["roles"]}
        self.assertIn("dhis2", ids)
        self.assertIn("repository", ids)
        self.assertIn("ui_playwright", ids)

    def test_dock_phase4(self) -> None:
        html = self.client.get("/work").get_data(as_text=True)
        self.assertIn("Phase 5", html)
        self.assertIn("roles_url", html)


if __name__ == "__main__":
    unittest.main()
