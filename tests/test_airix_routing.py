"""AiriX Smart Routing Phase 3 — history, findings, retry/escalation, analytics."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

from hub.agent_center.db import AgentCenterDb
from hub.agent_center.routing import AgentRouterService
from hub.agent_center.routing.context import build_minimal_context_preview
from hub.agent_center.routing.execution import prompt_only_fingerprint
from hub.agent_center.routing.findings import select_relevant_findings
from hub.agent_center.routing.history import RoutingHistoryStore
from hub.agent_center.routing.models import RoutingSettings
from hub.agent_center.routing.router import recommend_route
from hub.agent_center.routing.settings import load_routing_settings, save_routing_settings
from hub.agent_center.service import AgentCenterError
from hub.notebook.db import NotebookDatabase


class _FakeAgentCenter:
    """Minimal stand-in so RouteExecutor never needs live providers."""

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
            "answer": f"answer from {payload.get('agent_id')}",
            "agent_id": payload.get("agent_id"),
            "prompt": payload.get("prompt"),
            "tool_ids": list(payload.get("tool_ids") or []),
            "repository_ids": list(payload.get("repository_ids") or []),
            "finished_at": "2026-08-10T00:00:00+00:00",
            "usage": {"total_tokens": 42},
        }
        self.started.append(payload)
        self.runs[run_id] = row
        return dict(row)

    def cancel_run(self, run_id: str, *, profile_id: str = "okarun") -> dict[str, Any]:
        self.cancelled.append(run_id)
        row = self.runs.get(run_id) or {"id": run_id}
        row = {**row, "status": "cancelled", "error": "Cancelled"}
        self.runs[run_id] = row
        return dict(row)

    def get_run(self, run_id: str, *, profile_id: str = "okarun") -> dict[str, Any]:
        if run_id not in self.runs:
            raise AgentCenterError("not found", code="not_found")
        return dict(self.runs[run_id])

    def get_agent(self, agent_id: str) -> Any:
        return MagicMock() if agent_id else None

    def repositories(self, profile_id: str = "okarun") -> list[dict[str, Any]]:
        return [{"id": "sample-cli", "name": "sample-cli", "selectable": True}]

    def list_agents(self, **kwargs: Any) -> list[dict[str, Any]]:
        return [
            {"id": "grok", "status": "available", "runnable": True},
            {"id": "hub-simulator", "status": "available", "runnable": True},
            {"id": "codex", "status": "unavailable", "runnable": False},
        ]


def _availability(extra: dict[str, dict[str, Any]] | None = None) -> dict[str, dict[str, Any]]:
    base = {
        "grok": {"id": "grok", "status": "available", "runnable": True},
        "hub-simulator": {"id": "hub-simulator", "status": "available", "runnable": True},
        "codex": {"id": "codex", "status": "available", "runnable": True},
        "openai-api": {"id": "openai-api", "status": "available", "runnable": True},
    }
    if extra:
        base.update(extra)
    return base


class AirixRoutingUnitTests(unittest.TestCase):
    def setUp(self) -> None:
        self.router = AgentRouterService()

    def test_simple_lookup_routes_t0_deterministic(self) -> None:
        prompt = "Look up the UID for Philippines and show me the status of recent jobs"
        rec = self.router.recommend_route(prompt)
        self.assertEqual(rec.recommended_agent, "deterministic")
        self.assertEqual(rec.recommended_tier, "T0")
        self.assertFalse(rec.approval_required)

    def test_sql_dhis2_investigation_prefers_grok(self) -> None:
        prompt = (
            "Investigate why the DHIS2 analytics SQL query for program indicators "
            "returns empty rows for this org unit — debug the join and UID mapping"
        )
        rec = self.router.recommend_route(prompt)
        self.assertEqual(rec.recommended_agent, "grok")
        self.assertEqual(rec.recommended_tier, "T2")

    def test_large_architecture_refactor_routes_codex_without_provider_approval(self) -> None:
        prompt = (
            "Redesign the architecture and perform a large refactor across 12 modules "
            "in the entire codebase, including cross-module ownership boundaries and "
            "a breaking change migration plan"
        )
        rec = self.router.recommend_route(prompt)
        self.assertEqual(rec.recommended_agent, "codex")
        self.assertFalse(rec.approval_required)
        self.assertIn("explanation", rec.public())

    def test_build_execution_plan_phase3(self) -> None:
        plan = self.router.build_execution_plan("Look up recent audit events")
        public = plan.public()
        self.assertTrue(public["execute"])
        self.assertEqual(public["phase"], 5)
        self.assertIn("explanation", public)

    def test_settings_persist_use_history(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        db = NotebookDatabase(Path(tmp.name) / "notebook.db")
        saved = save_routing_settings(
            db,
            "work",
            {"mode": "balanced", "use_history": False, "max_retries": 1},
        )
        self.assertFalse(saved.use_history)
        self.assertEqual(load_routing_settings(db, "work").use_history, False)


class AirixRoutingPhase3HistoryTests(unittest.TestCase):
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

    def test_insufficient_history_falls_back_to_normal_rules(self) -> None:
        self.history.record_event(
            {
                "provider_id": "grok",
                "tier": "T2",
                "task_type": "dhis2_investigation",
                "status": "failed",
                "outcome": "failure",
                "prompt_fingerprint": "x",
            }
        )
        prompt = (
            "Investigate why the DHIS2 analytics SQL query for program indicators "
            "returns empty rows for this org unit — debug the join and UID mapping"
        )
        rec = self.router.recommend_route(prompt)
        self.assertEqual(rec.recommended_agent, "grok")
        self.assertIsNone(rec.explanation.historical_success_rate if rec.explanation else None)

    def test_history_influences_routing(self) -> None:
        for _ in range(4):
            self.history.record_event(
                {
                    "provider_id": "grok",
                    "tier": "T2",
                    "task_type": "css_ui",
                    "status": "failed",
                    "outcome": "failure",
                }
            )
        for _ in range(4):
            self.history.record_event(
                {
                    "provider_id": "low-cost",
                    "tier": "T1",
                    "task_type": "css_ui",
                    "status": "completed",
                    "outcome": "success",
                }
            )
        classification = self.router.classify_request(
            "Fix the CSS padding on the dashboard button style so the hover color matches"
        )
        rec = recommend_route(
            classification,
            settings=RoutingSettings(mode="cheapest", prefer_deterministic=False, use_history=True),
            provider_stats=self.history.provider_stats(task_type="css_ui"),
        )
        self.assertTrue(rec.history_influenced)
        self.assertEqual(rec.recommended_agent, "low-cost")

    def test_relevant_prior_findings_included_unrelated_excluded(self) -> None:
        self.history.save_finding(
            {
                "task_type": "dhis2_investigation",
                "keywords": ["dhis2", "analytics", "uid", "org"],
                "summary": "Org unit UID mapping mismatch caused empty analytics rows.",
                "provider_id": "grok",
            }
        )
        self.history.save_finding(
            {
                "task_type": "css_ui",
                "keywords": ["css", "padding", "button"],
                "summary": "Button padding used rem units inconsistently.",
                "provider_id": "low-cost",
            }
        )
        prompt = (
            "Investigate why the DHIS2 analytics SQL query for program indicators "
            "returns empty rows for this org unit — debug the join and UID mapping"
        )
        rec = self.router.recommend_route(prompt)
        findings = self.history.list_findings(task_type=rec.task_type)
        selected = select_relevant_findings(
            findings, prompt=prompt, classification=rec.classification
        )
        self.assertTrue(selected)
        self.assertTrue(all("css" not in (f.get("summary") or "").lower() for f in selected))
        self.assertTrue(any("analytics" in (f.get("summary") or "").lower() for f in selected))
        ctx = build_minimal_context_preview(
            prompt=prompt,
            classification=rec.classification,
            recommendation=rec,
            candidate_findings=self.history.list_findings(limit=40),
        )
        self.assertTrue(ctx["prior_findings"])
        self.assertTrue(all(f["task_type"] == rec.task_type for f in ctx["prior_findings"]))

    def test_escalation_recommended_after_repeated_failure(self) -> None:
        prompt = (
            "Investigate why the DHIS2 analytics SQL query for program indicators "
            "returns empty rows for this org unit — debug the join and UID mapping"
        )
        fp = prompt_only_fingerprint(prompt)
        for _ in range(3):
            self.history.record_event(
                {
                    "provider_id": "grok",
                    "tier": "T2",
                    "task_type": "dhis2_investigation",
                    "status": "failed",
                    "outcome": "failure",
                    "prompt_fingerprint": fp,
                    "error_code": "execution_failed",
                }
            )
        rec = self.router.recommend_route(prompt)
        self.assertTrue(rec.history_influenced)
        self.assertIsNotNone(rec.escalation_reason)
        self.assertEqual(rec.recommended_agent, "codex")
        self.assertFalse(rec.approval_required)

    def test_codex_no_longer_blocked_by_provider_approval(self) -> None:
        prompt = (
            "Redesign the architecture and perform a large refactor across 12 modules "
            "in the entire codebase, including cross-module ownership boundaries and "
            "a breaking change migration plan"
        )
        # Provider identity alone must not raise approval_required.
        try:
            self.router.execute_route(prompt, approve_codex=False, orchestrate=False)
        except AgentCenterError as exc:
            self.assertNotEqual(exc.code, "approval_required")
            return
        # If execute proceeds (fake/unavailable), that is also fine for this gate.

    def test_retry_limit_enforced(self) -> None:
        prompt = "Investigate why the DHIS2 analytics SQL query returns empty rows"
        with self.assertRaises(AgentCenterError) as ctx:
            self.router.execute_route(prompt, agent_override="grok", attempt=3, orchestrate=False)
        self.assertEqual(ctx.exception.code, "retry_limit")

    def test_identical_retry_blocked(self) -> None:
        prompt = (
            "Investigate why the DHIS2 analytics SQL query for program indicators "
            "returns empty rows for this org unit"
        )
        fp = prompt_only_fingerprint(prompt)
        self.history.record_event(
            {
                "provider_id": "grok",
                "tier": "T2",
                "task_type": "dhis2_investigation",
                "status": "failed",
                "outcome": "failure",
                "prompt_fingerprint": fp,
            }
        )
        with self.assertRaises(AgentCenterError) as ctx:
            self.router.execute_route(prompt, agent_override="grok", attempt=1, orchestrate=False)
        self.assertEqual(ctx.exception.code, "identical_retry_blocked")

    def test_metrics_recorded_and_t0_savings(self) -> None:
        prompt = "Look up the UID for Philippines and show me the status of recent jobs"
        result = self.router.execute_route(prompt, orchestrate=False)
        self.assertEqual(result["execution"]["status"], "completed")
        analytics = self.history.analytics()
        self.assertGreaterEqual(analytics["executions_total"], 1)
        self.assertGreaterEqual(analytics["t0_llm_avoided"], 1)
        stats = self.history.provider_stats(task_type="lookup")
        det = next(s for s in stats if s["provider_id"] == "deterministic")
        self.assertGreaterEqual(det["successes"], 1)
        self.assertGreaterEqual(det["t0_avoided"], 1)

    def test_grok_metrics_record_tokens(self) -> None:
        prompt = (
            "Investigate why the DHIS2 analytics SQL query for program indicators "
            "returns empty rows for this org unit — debug the join and UID mapping"
        )
        result = self.router.execute_route(prompt, orchestrate=False, agent_override="grok")
        self.assertEqual(result["execution"]["adapter_id"], "grok")
        analytics = self.history.analytics()
        self.assertGreaterEqual(analytics["actual_tokens_total"], 42)


class AirixRoutingExecutionTests(unittest.TestCase):
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

    def test_t0_executes_without_ai(self) -> None:
        prompt = "Look up the UID for Philippines and show me the status of recent jobs"
        result = self.router.execute_route(prompt, orchestrate=False)
        self.assertEqual(result["execution"]["mode"], "deterministic")
        self.assertEqual(self.fake.started, [])

    def test_manual_override_works(self) -> None:
        prompt = "Look up the UID for Philippines"
        result = self.router.execute_route(prompt, agent_override="grok", orchestrate=False)
        self.assertTrue(result["execution"]["manual_override"])
        self.assertEqual(self.fake.started[0]["agent_id"], "grok")


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

    def test_recommend_api_phase3(self) -> None:
        resp = self.client.post(
            "/api/assistants/airix/routing/recommend",
            json={"prompt": "Look up the UID for Philippines"},
        )
        self.assertEqual(resp.status_code, 200)
        body = resp.get_json()
        self.assertEqual(body["phase"], 5)
        self.assertIn("explanation", body["recommendation"])
        self.assertTrue(body["plan"]["execute"])

    def test_analytics_api(self) -> None:
        self.client.post(
            "/api/assistants/airix/routing/execute",
            json={"prompt": "Look up the UID for Philippines and list recent jobs"},
        )
        resp = self.client.get("/api/assistants/airix/routing/analytics")
        self.assertEqual(resp.status_code, 200)
        analytics = resp.get_json()["analytics"]
        self.assertGreaterEqual(analytics["executions_total"], 1)
        self.assertGreaterEqual(analytics["t0_llm_avoided"], 1)

    def test_execute_codex_no_provider_approval_api(self) -> None:
        prompt = (
            "Redesign the architecture and perform a large refactor across 12 modules "
            "in the entire codebase, including cross-module ownership boundaries and "
            "a breaking change migration plan"
        )
        resp = self.client.post(
            "/api/assistants/airix/routing/execute",
            json={
                "prompt": prompt,
                "agent_override": "codex",
                "approve_codex": False,
                "orchestrate": False,
                "repository_ids": ["sample-cli"],
            },
        )
        # Must not be blocked solely because Codex was selected.
        if resp.status_code >= 400:
            self.assertNotEqual(resp.get_json().get("code"), "approval_required")
        else:
            body = resp.get_json() or {}
            exec_row = body.get("execution") or {}
            self.assertNotEqual(exec_row.get("status"), "paused_for_approval")
            self.assertNotEqual(exec_row.get("error_code"), "approval_required")

    def test_work_dock_includes_phase3(self) -> None:
        html = self.client.get("/work").get_data(as_text=True)
        self.assertIn("Phase 5", html)
        self.assertIn("analytics_url", html)
        self.assertIn("/api/assistants/airix/routing/", html)


if __name__ == "__main__":
    unittest.main()
