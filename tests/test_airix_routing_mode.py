"""AiriX Routing Mode: Smart Routing vs Direct Agent — Efficient."""

from __future__ import annotations

import threading
import unittest
from typing import Any
from unittest.mock import MagicMock, patch

from hub.agent_center.dock import default_dock_state, load_dock_prefs, save_dock_prefs
from hub.agent_center.routing.context import (
    ROUTING_MODE_DIRECT,
    ROUTING_MODE_SMART,
    build_direct_agent_recommendation,
    build_minimal_context_preview,
    normalize_routing_mode,
    provider_to_adapter_id,
)
from hub.agent_center.routing.models import RoutingSettings
from hub.notebook.db import NotebookDatabase


class RoutingModeNormalizeTests(unittest.TestCase):
    def test_normalize(self) -> None:
        self.assertEqual(normalize_routing_mode("smart"), ROUTING_MODE_SMART)
        self.assertEqual(normalize_routing_mode("direct"), ROUTING_MODE_DIRECT)
        self.assertEqual(normalize_routing_mode("direct_agent"), ROUTING_MODE_DIRECT)
        self.assertEqual(normalize_routing_mode(None), ROUTING_MODE_SMART)


class DockPrefsRoutingModeTests(unittest.TestCase):
    def test_persist_routing_mode_per_workspace(self) -> None:
        import tempfile
        from pathlib import Path

        with tempfile.TemporaryDirectory() as tmp:
            db = NotebookDatabase(Path(tmp) / "n.db")
            save_dock_prefs(db, "work", {"routing_mode": "direct"})
            prefs = load_dock_prefs(db, "work")
            self.assertEqual(prefs["routing_mode"], "direct")
            # Personal workspace stays independent default.
            personal = load_dock_prefs(db, "personal")
            self.assertEqual(personal.get("routing_mode") or "smart", "smart")
            self.assertEqual(default_dock_state("work")["routing_mode"], "smart")


class DirectRecommendationTests(unittest.TestCase):
    def test_direct_recommendation_uses_selected_provider(self) -> None:
        rec = build_direct_agent_recommendation(
            "Refactor the login form CSS",
            provider_id="codex",
            model="gpt-5.3-codex",
            repository_ids=["demo-repo"],
        )
        self.assertEqual(rec.recommended_agent, "codex")
        self.assertEqual(rec.recommended_model, "gpt-5.3-codex")
        self.assertIn("Direct Agent", rec.reason)
        self.assertIsNone(provider_to_adapter_id("deterministic"))
        with self.assertRaises(ValueError):
            build_direct_agent_recommendation("hi", provider_id="deterministic")

    def test_minimal_context_still_applied(self) -> None:
        rec = build_direct_agent_recommendation(
            "Look up the organisation unit for SampleRegion",
            provider_id="grok",
            repository_ids=["demo-repo"],
        )
        preview = build_minimal_context_preview(
            prompt="Look up the organisation unit for SampleRegion",
            classification=rec.classification,
            recommendation=rec,
            repository_ids=["demo-repo"],
            agent_override="grok",
        )
        self.assertFalse(preview.get("include_whole_repo"))
        self.assertEqual(preview.get("strategy"), "minimal")
        self.assertTrue(preview.get("tool_ids"))
        self.assertLessEqual(len(preview.get("tool_ids") or []), 6)


class DirectExecutePathTests(unittest.TestCase):
    def _fake_center(self) -> MagicMock:
        fake = MagicMock()
        fake.registry = MagicMock()
        fake.notebook = None
        fake.sql_store = None
        fake.sql_executor = None
        fake.sql_connections = None
        fake.uid_index = None
        fake.email = None
        fake.calendar = None
        fake.job_store = None
        fake.audit_store = None
        fake.dhis2_reports = None
        fake.notepad_factory = None
        fake.repositories = MagicMock(return_value=[{"id": "demo", "selectable": True, "path": "/tmp/demo"}])
        return fake

    def test_direct_skips_t0_and_uses_exact_provider_model(self) -> None:
        from hub.agent_center.routing.execution import RouteExecutor

        fake = self._fake_center()
        fake.start_run = MagicMock(
            return_value={
                "id": "run-1",
                "status": "completed",
                "answer": "Direct answer",
                "model": "gpt-5.3-codex",
                "conversation_id": "conv-123",
                "context": {
                    "grounding": {
                        "grounded": True,
                        "grounded_label": "Yes",
                        "task_solved": True,
                        "answer_grounded": True,
                        "evidence_found": True,
                        "source": "tool:repo_search",
                    },
                    "files": [{"repo_id": "demo", "path": "a.py"}],
                    "packed_prompt_chars": 420,
                },
                "packed_prompt": "x" * 420,
            }
        )
        ex = RouteExecutor(
            fake,
            availability_loader=lambda: {"codex": {"status": "available", "runnable": True}},
        )
        rec = build_direct_agent_recommendation(
            "Explain the repository layout",
            provider_id="codex",
            model="gpt-5.3-codex",
            repository_ids=["demo"],
        )
        t0 = MagicMock(side_effect=AssertionError("T0 must not run in Direct mode"))
        ex._execute_t0 = t0  # type: ignore[method-assign]
        with patch(
            "hub.agent_center.grounding.collect_evidence_packet",
            return_value={
                "usable": True,
                "hits": [{"source": "repository", "path": "a.py"}],
                "sources": ["tool:repo_search"],
                "tool_results": [{"tool": "repo_search", "ok": True}],
                "errors": [],
                "summary": "hints only",
            },
        ), patch(
            "hub.agent_center.repository_context.resolve_repository_context",
            return_value={"ok": True, "repository_ids": ["demo"]},
        ):
            out = ex.execute(
                prompt="Explain the repository layout",
                recommendation=rec,
                settings=RoutingSettings(prefer_deterministic=True, require_approval_before_codex=False),
                agent_override="codex",
                repository_ids=["demo"],
                selected_repository_id="demo",
                model="gpt-5.3-codex",
                manual_override=True,
                approve_codex=True,
                routing_mode="direct",
                conversation_id="conv-123",
                context_fingerprint="direct|codex|gpt-5.3-codex|demo|Explain",
            )
        t0.assert_not_called()
        self.assertEqual(out.get("routing_mode"), "direct")
        self.assertEqual(out.get("resolved_provider") or out.get("adapter_id"), "codex")
        self.assertEqual(out.get("resolved_model") or out.get("model"), "gpt-5.3-codex")
        self.assertTrue(out.get("session_reused"))
        self.assertEqual(out.get("conversation_id"), "conv-123")
        self.assertIn("demo", " ".join(out.get("context_items") or []))
        # start_run must receive exact provider/model and conversation resume.
        payload = fake.start_run.call_args[0][0]
        self.assertEqual(payload.get("agent_id"), "codex")
        self.assertEqual(payload.get("model"), "gpt-5.3-codex")
        self.assertEqual(payload.get("conversation_id"), "conv-123")
        self.assertEqual(payload.get("routing_mode"), "direct")
        self.assertEqual(payload.get("repository_ids"), ["demo"])

    def test_direct_unavailable_no_silent_fallback(self) -> None:
        from hub.agent_center.routing.execution import RouteExecutor

        fake = self._fake_center()
        fake.start_run = MagicMock(side_effect=AssertionError("must not start"))
        ex = RouteExecutor(
            fake,
            availability_loader=lambda: {"codex": {"status": "unavailable", "runnable": False, "detail": "offline"}},
        )
        rec = build_direct_agent_recommendation(
            "Say hello",
            provider_id="codex",
            model="gpt-5.3-codex",
        )
        out = ex.execute(
            prompt="Say hello",
            recommendation=rec,
            settings=RoutingSettings(require_approval_before_codex=False),
            agent_override="codex",
            model="gpt-5.3-codex",
            manual_override=True,
            approve_codex=True,
            routing_mode="direct",
        )
        self.assertEqual(out.get("status"), "failed")
        self.assertIn("unavailable", (out.get("error") or "").lower())
        self.assertNotIn("hub-simulator", (out.get("adapter_id") or "").lower())
        fake.start_run.assert_not_called()

    def test_smart_mode_still_can_run_t0(self) -> None:
        from hub.agent_center.routing.classifier import classify_prompt
        from hub.agent_center.routing.execution import RouteExecutor
        from hub.agent_center.routing.models import RouteRecommendation

        fake = self._fake_center()
        fake.start_run = MagicMock(side_effect=AssertionError("smart T0 should not call AI here"))
        ex = RouteExecutor(fake)
        c = classify_prompt("What are the provinces for Region III?", repository_ids=["demo"])
        rec = RouteRecommendation(
            task_type=c.task_type,
            complexity=c.complexity,
            risk=c.risk,
            recommended_agent="deterministic",
            recommended_label="T0",
            recommended_tier="T0",
            alternative_agent="grok",
            alternative_label="Grok",
            confidence=0.9,
            reason="lookup",
            estimated_usage="Very Low",
            approval_required=False,
            classification=c,
        )
        with ex._lock:
            ex._active["x"] = {"id": "x", "status": "queued", "provider_id": "deterministic"}
        with patch(
            "hub.agent_center.grounding.collect_evidence_packet",
            return_value={
                "usable": True,
                "hits": [
                    {"source": "dhis2:org_unit_child", "name": "Alpha", "uid": "uidA"},
                ],
                "sources": ["tool:org_unit_lookup"],
                "tool_results": [{"tool": "org_unit_lookup", "ok": True}],
                "errors": [],
                "summary": "ou",
            },
        ):
            out = ex.execute(
                prompt="What are the provinces for Region III?",
                recommendation=rec,
                settings=RoutingSettings(prefer_deterministic=True),
                repository_ids=["demo"],
                routing_mode="smart",
            )
        self.assertEqual(out.get("routing_mode") or "smart", "smart")
        self.assertEqual(out.get("mode"), "deterministic")
        self.assertIn("Alpha", out.get("answer") or "")
        fake.start_run.assert_not_called()

    def test_direct_context_prep_does_not_terminate_without_ai(self) -> None:
        """Direct mode packs evidence but still starts the selected agent."""
        from hub.agent_center.routing.execution import RouteExecutor

        fake = self._fake_center()
        fake.start_run = MagicMock(
            return_value={
                "id": "run-2",
                "status": "completed",
                "answer": "Model answered with grounding rules",
                "model": "grok-model",
                "conversation_id": "c2",
                "context": {
                    "grounding": {
                        "grounded": False,
                        "grounded_label": "No",
                        "task_solved": True,
                        "answer_grounded": False,
                        "evidence_found": False,
                        "required": True,
                        "source": "general knowledge",
                    },
                    "files": [],
                    "packed_prompt_chars": 100,
                },
            }
        )
        ex = RouteExecutor(
            fake,
            availability_loader=lambda: {"grok": {"status": "available", "runnable": True}},
        )
        rec = build_direct_agent_recommendation(
            "What are the provinces for Region III - Central Luzon?",
            provider_id="grok",
            repository_ids=["demo"],
        )
        with patch(
            "hub.agent_center.grounding.collect_evidence_packet",
            return_value={
                "usable": False,
                "hits": [],
                "sources": [],
                "tool_results": [],
                "errors": [],
                "summary": "No usable hits",
            },
        ), patch(
            "hub.agent_center.repository_context.resolve_repository_context",
            return_value={"ok": True, "repository_ids": ["demo"]},
        ):
            out = ex.execute(
                prompt="What are the provinces for Region III - Central Luzon?",
                recommendation=rec,
                settings=RoutingSettings(),
                agent_override="grok",
                repository_ids=["demo"],
                selected_repository_id="demo",
                manual_override=True,
                routing_mode="direct",
            )
        fake.start_run.assert_called_once()
        self.assertNotEqual(out.get("mode"), "grounding_gate")
        self.assertIn("Model answered", out.get("answer") or "")
        payload = fake.start_run.call_args[0][0]
        # Grounding rules still attached; Direct does not enable GK by default.
        self.assertTrue(payload.get("grounding_rules"))
        self.assertFalse(payload.get("allow_general_knowledge"))
        self.assertEqual(payload.get("repository_ids"), ["demo"])

    def test_direct_grounding_rules_remain_active(self) -> None:
        from hub.agent_center.routing.execution import RouteExecutor

        fake = self._fake_center()
        fake.start_run = MagicMock(
            return_value={
                "id": "run-g",
                "status": "completed",
                "answer": "I guess the answer without evidence",
                "model": "gpt-5.3-codex",
                "context": {
                    "grounding": {
                        "grounded": False,
                        "grounded_label": "No",
                        "task_solved": False,
                        "answer_grounded": False,
                        "evidence_found": False,
                        "required": True,
                        "source": "general knowledge",
                    }
                },
            }
        )
        ex = RouteExecutor(
            fake,
            availability_loader=lambda: {"codex": {"status": "available", "runnable": True}},
        )
        rec = build_direct_agent_recommendation(
            "How many eligible beneficiaries in Brgy. Sample for 2026 Q2?",
            provider_id="codex",
            model="gpt-5.3-codex",
            repository_ids=["demo"],
        )
        with patch(
            "hub.agent_center.grounding.collect_evidence_packet",
            return_value={
                "usable": False,
                "hits": [],
                "sources": [],
                "tool_results": [],
                "errors": [],
                "summary": "no hits",
            },
        ), patch(
            "hub.agent_center.repository_context.resolve_repository_context",
            return_value={"ok": True, "repository_ids": ["demo"]},
        ):
            out = ex.execute(
                prompt="How many eligible beneficiaries in Brgy. Sample for 2026 Q2?",
                recommendation=rec,
                settings=RoutingSettings(require_approval_before_codex=False),
                agent_override="codex",
                repository_ids=["demo"],
                selected_repository_id="demo",
                model="gpt-5.3-codex",
                manual_override=True,
                approve_codex=True,
                routing_mode="direct",
            )
        self.assertEqual(out.get("routing_mode"), "direct")
        g = out.get("grounding") or {}
        self.assertFalse(g.get("grounded"))
        payload = fake.start_run.call_args[0][0]
        self.assertTrue(payload.get("grounding_rules"))
        self.assertFalse(payload.get("allow_general_knowledge"))


class DirectServiceExecuteTests(unittest.TestCase):
    def test_execute_route_direct_requires_provider(self) -> None:
        from hub.agent_center.routing.service import AgentRouterService
        from hub.agent_center.service import AgentCenterError

        svc = AgentRouterService(availability_loader=lambda: {})
        svc.executor = MagicMock()
        with self.assertRaises(AgentCenterError):
            svc.execute_route("hello", routing_mode="direct", agent_override=None)

    def test_execute_route_direct_calls_executor_with_flags(self) -> None:
        from hub.agent_center.routing.service import AgentRouterService

        svc = AgentRouterService(
            availability_loader=lambda: {"grok": {"status": "available", "runnable": True}}
        )
        captured: dict[str, Any] = {}

        def _exec(**kwargs: Any) -> dict[str, Any]:
            captured.update(kwargs)
            return {
                "id": "e1",
                "status": "completed",
                "answer": "ok",
                "provider_id": "grok",
                "routing_mode": "direct",
            }

        svc.executor = MagicMock()
        svc.executor.execute = _exec
        svc.get_settings = MagicMock(return_value=RoutingSettings())  # type: ignore[method-assign]
        svc.acl.get_role = MagicMock(return_value="admin")  # type: ignore[method-assign]
        svc.build_execution_plan = MagicMock(  # type: ignore[method-assign]
            return_value=MagicMock(
                public=lambda: {},
                permissions={},
                prior_findings=[],
            )
        )
        svc._budget_for = MagicMock(  # type: ignore[method-assign]
            return_value={"ok": True, "remaining_tokens": 99999, "limit_tokens": 99999}
        )
        with patch("hub.agent_center.routing.service.assert_execution_allowed"), patch(
            "hub.agent_center.routing.service.assert_budget_allows"
        ), patch(
            "hub.agent_center.routing.service.estimate_cost_usd", return_value=0.0
        ):
            out = svc.execute_route(
                "hello from direct",
                agent_override="grok",
                model="grok-3",
                routing_mode="direct",
                conversation_id="conv-9",
                repository_ids=["demo"],
            )
        self.assertEqual(out.get("routing_mode"), "direct")
        self.assertEqual(captured.get("routing_mode"), "direct")
        self.assertEqual(captured.get("agent_override"), "grok")
        self.assertEqual(captured.get("model"), "grok-3")
        self.assertTrue(captured.get("manual_override"))
        self.assertEqual(captured.get("conversation_id"), "conv-9")


if __name__ == "__main__":
    unittest.main()
