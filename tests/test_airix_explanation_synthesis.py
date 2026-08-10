"""T0 → AI explanation synthesis answer propagation and Hybrid telemetry."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

from hub.agent_center.db import AgentCenterDb
from hub.agent_center.routing.execution import RouteExecutor, extract_provider_answer
from hub.agent_center.routing.history import RoutingHistoryStore
from hub.agent_center.routing.models import PromptClassification, RouteRecommendation, RoutingSettings
from hub.agent_center.routing.telemetry import (
    EXEC_HYBRID,
    attach_execution_telemetry,
    build_execution_telemetry,
)


def _rec(**kwargs: Any) -> RouteRecommendation:
    c = PromptClassification(
        task_type=kwargs.get("task_type", "architecture"),
        complexity=2,
        risk="low",
        estimated_scope_files=2,
        context_size="small",
        needs_coding=False,
        needs_testing=False,
        needs_architecture=True,
        deterministic_capable=True,
        signals=list(
            kwargs.get(
                "signals",
                ["deterministic_capable", "selected_repo", "project_lookup", "code"],
            )
        ),
    )
    return RouteRecommendation(
        task_type=c.task_type,
        complexity=c.complexity,
        risk=c.risk,
        recommended_agent="deterministic",
        recommended_label="Deterministic",
        recommended_tier="T0",
        alternative_agent="openai-api",
        alternative_label="OpenAI API",
        confidence=0.9,
        reason="inspect explanation",
        estimated_usage="Very Low",
        approval_required=False,
        classification=c,
    )


def _evidence_packet() -> dict[str, Any]:
    return {
        "usable": True,
        "hits": [
            {
                "source": "repository_intelligence:live-processing-local",
                "repository_id": "live-processing-local",
                "path": "intake.py",
                "name": "intake.py",
                "summary": "validate_batch filters intake rows",
                "authority": "cached_repository_context",
            }
        ],
        "sources": [
            "tool:repository_intelligence",
            "repository_intelligence:live-processing-local:intake.py",
        ],
        "tool_results": [
            {
                "tool": "repository_intelligence",
                "ok": True,
                "result": {"used": True, "freshness": "current", "item_count": 1},
            }
        ],
        "errors": [],
        "summary": "1 evidence hit(s) from repository intelligence",
    }


class ExtractProviderAnswerTests(unittest.TestCase):
    def test_extracts_nested_agent_run_answer(self) -> None:
        self.assertEqual(
            extract_provider_answer(
                {"answer": "", "agent_run": {"answer": "Synthesized explanation from evidence."}}
            ),
            "Synthesized explanation from evidence.",
        )


class ExplanationSynthesisPropagationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.fake = MagicMock()
        self.fake.registry = MagicMock()
        self.fake.notebook = None
        self.fake.sql_store = None
        self.fake.uid_index = None
        self.fake.email = None
        self.fake.calendar = None
        self.fake.job_store = None
        self.fake.audit_store = None
        self.fake.dhis2_reports = None
        self.fake.notepad_factory = None
        self.fake.repositories = MagicMock(
            return_value=[
                {
                    "id": "live-processing-local",
                    "selectable": True,
                    "path": str(Path(self.tmp.name)),
                }
            ]
        )
        self.fake.repository_intelligence = MagicMock()
        self.fake.repository_intelligence.retrieve.return_value = {
            "profiles": [{"repository_id": "live-processing-local"}],
            "items": [
                {
                    "repository_id": "live-processing-local",
                    "path": "intake.py",
                    "category": "business_logic",
                    "summary": "validate_batch filters intake rows",
                }
            ],
            "item_count": 1,
            "include_full_index": False,
            "diagnostics": {
                "used": True,
                "repository_ids": ["live-processing-local"],
                "knowledge_entries_used": 1,
                "freshness": "current",
                "context_chars_contributed": 40,
            },
        }
        self.history = RoutingHistoryStore(AgentCenterDb(Path(self.tmp.name) / "ac.db"))
        self.executor = RouteExecutor(
            self.fake,
            history=self.history,
            availability_loader=lambda: {
                "openai-api": {"status": "available", "runnable": True},
                "grok": {"status": "available", "runnable": True},
            },
        )
        self.executor.poll_interval_seconds = 0.01
        self.executor.step_wait_seconds = 2.0

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def _seed_t0_escalate_row(self, execution_id: str) -> None:
        packet = _evidence_packet()
        with self.executor._lock:
            self.executor._active[execution_id] = {
                "id": execution_id,
                "status": "completed",
                "provider_id": "deterministic",
                "adapter_id": None,
                "tier": "T0",
                "prompt": "Explain how validate_batch works in this repository",
                "workspace": "work",
                "actor": "owner",
                "t0_capability_escalate": True,
                "t0_unsolved": True,
                "t0_failure_reason": "t0_explanation_synthesis",
                "next_capability": "ai_escalate",
                "evidence_packet": packet,
                "tool_results": list(packet["tool_results"]),
                "repository_intelligence_diagnostics": {
                    "used": True,
                    "repository_ids": ["live-processing-local"],
                    "knowledge_entries_used": 1,
                    "freshness": "current",
                    "context_chars_contributed": 40,
                },
                "grounding": {
                    "evidence_found": True,
                    "evidence_found_label": "Yes",
                    "task_solved": False,
                    "task_solved_label": "No",
                    "grounded": False,
                    "grounded_label": "No",
                    "answer_grounded": False,
                },
                "context": {
                    "repository_ids": ["live-processing-local"],
                    "repository_intelligence": self.fake.repository_intelligence.retrieve.return_value,
                    "tool_ids": ["repo_search"],
                },
                "answer": "",
            }

    def test_t0_evidence_ai_synthesis_answer_propagated(self) -> None:
        execution_id = "synth-ok"
        self._seed_t0_escalate_row(execution_id)
        child_answer = (
            "validate_batch filters intake rows using the repository's "
            "business rules from intake.py so only ok rows continue."
        )
        self.fake.start_run = MagicMock(
            return_value={
                "id": "child-run-1",
                "status": "completed",
                "answer": child_answer,
                "model": "gpt-5.6-terra",
                "agent_id": "openai-api",
                "usage": {"input_tokens": 12, "output_tokens": 34, "total_tokens": 46},
                "context": {
                    "grounding": {
                        "evidence_found": True,
                        "task_solved": False,
                        "grounded": False,
                    },
                    "evidence_packet": _evidence_packet(),
                    "repository_intelligence": self.fake.repository_intelligence.retrieve.return_value,
                    "packed_prompt_chars": 800,
                },
                "finished_at": "2026-08-10T00:00:02+00:00",
            }
        )

        prior = self.executor.get_status(execution_id) or {}
        # Call the private escalate path via execute's helper by invoking _execute_agent
        # through a thin wrapper that mirrors _escalate_to_ai.
        with self.executor._lock:
            row = self.executor._active[execution_id]
            row["status"] = "running"
            row["fallback_from"] = "deterministic"
            row["fallback_reason"] = "t0_explanation_synthesis"
            row["ai_escalation_occurred"] = True
            row["synthesis_escalation"] = True
            row["adapter_id"] = "openai-api"
            row["resolved_provider"] = "openai-api"

        context = {
            **(prior.get("context") or {}),
            "evidence_packet": prior.get("evidence_packet"),
            "bounded_evidence_only": True,
            "synthesis_escalation": True,
            "t0_failure_reason": "t0_explanation_synthesis",
            "repository_intelligence": self.fake.repository_intelligence.retrieve.return_value,
            "repository_intelligence_diagnostics": prior.get(
                "repository_intelligence_diagnostics"
            ),
            "model": "gpt-5.6-terra",
            "interaction_mode": "inspect",
        }
        result = self.executor._execute_agent(
            execution_id,
            "Explain how validate_batch works in this repository",
            _rec(),
            context,
            adapter_id="openai-api",
            repository_ids=["live-processing-local"],
            settings=RoutingSettings(prefer_deterministic=True, require_approval_before_codex=False),
            manual_override=False,
        )
        self.assertEqual(result.get("status"), "completed")
        self.assertIn("validate_batch", str(result.get("answer") or ""))
        self.assertNotIn("(no answer)", str(result.get("answer") or ""))
        grounding = result.get("grounding") or {}
        self.assertEqual(grounding.get("evidence_found_label"), "Yes")
        self.assertEqual(grounding.get("task_solved_label"), "Yes")
        self.assertEqual(grounding.get("grounded_label"), "Yes")
        ri = result.get("repository_intelligence_diagnostics") or {}
        self.assertTrue(ri.get("used"))
        self.assertEqual(ri.get("freshness"), "current")
        self.assertIn(
            "repository_intelligence:live-processing-local:intake.py",
            (result.get("evidence_packet") or {}).get("sources") or [],
        )
        tel = (result.get("telemetry") or {})
        self.assertEqual(tel.get("execution_type"), EXEC_HYBRID)
        self.assertTrue(tel.get("llm_invoked"))
        self.assertEqual(tel.get("provider"), "openai-api")
        self.assertEqual(tel.get("model"), "gpt-5.6-terra")
        self.assertEqual(tel.get("child_ai_run_id"), "child-run-1")
        self.assertTrue(str(tel.get("route_path") or "").startswith("T0 →"))

    def test_empty_child_response_marks_synthesis_failed(self) -> None:
        execution_id = "synth-empty"
        self._seed_t0_escalate_row(execution_id)
        with self.executor._lock:
            row = self.executor._active[execution_id]
            row["status"] = "running"
            row["fallback_from"] = "deterministic"
            row["synthesis_escalation"] = True
            row["adapter_id"] = "openai-api"
        self.fake.start_run = MagicMock(
            return_value={
                "id": "child-empty",
                "status": "completed",
                "answer": "",
                "model": "gpt-5.6-terra",
                "agent_id": "openai-api",
                "usage": {},
                "context": {"evidence_packet": _evidence_packet()},
                "finished_at": "2026-08-10T00:00:02+00:00",
            }
        )
        result = self.executor._execute_agent(
            execution_id,
            "Explain how validate_batch works in this repository",
            _rec(),
            {
                "evidence_packet": _evidence_packet(),
                "bounded_evidence_only": True,
                "synthesis_escalation": True,
                "t0_failure_reason": "t0_explanation_synthesis",
                "repository_intelligence": self.fake.repository_intelligence.retrieve.return_value,
                "repository_ids": ["live-processing-local"],
                "interaction_mode": "inspect",
            },
            adapter_id="openai-api",
            repository_ids=["live-processing-local"],
            settings=RoutingSettings(prefer_deterministic=True, require_approval_before_codex=False),
        )
        self.assertEqual(result.get("status"), "failed")
        self.assertEqual(result.get("error_code"), "synthesis_failed")
        self.assertIn("synthesis_failed", str(result.get("error") or ""))
        self.assertFalse(str(result.get("answer") or "").strip())

    def test_hybrid_telemetry_and_usage_unavailable(self) -> None:
        row = attach_execution_telemetry(
            {
                "mode": "ask",
                "tier": "T2",
                "provider_id": "openai-api",
                "adapter_id": "openai-api",
                "fallback_from": "deterministic",
                "ai_escalation_occurred": True,
                "t0_failure_reason": "t0_explanation_synthesis",
                "agent_run_id": "child-2",
                "model": "gpt-5.6-terra",
                "usage": {},
                "route_path": "T0 → openai-api/gpt-5.6-terra",
                "tool_results": [{"tool": "repository_intelligence", "ok": True}],
                "repository_intelligence_diagnostics": {
                    "used": True,
                    "freshness": "current",
                    "knowledge_entries_used": 1,
                    "repository_ids": ["live-processing-local"],
                },
                "started_at": "2026-08-10T00:00:00+00:00",
                "finished_at": "2026-08-10T00:00:03+00:00",
            }
        )
        tel = row["telemetry"]
        self.assertEqual(tel["execution_type"], EXEC_HYBRID)
        self.assertTrue(tel["llm_invoked"])
        self.assertEqual(tel["usage_source"], "unavailable")
        self.assertIsNone(tel["total_ai_tokens"])
        self.assertEqual(tel["child_ai_run_id"], "child-2")
        self.assertEqual(tel["route_path"], "T0 → openai-api/gpt-5.6-terra")
        self.assertTrue(tel["repository_intelligence"]["used"])

    def test_ri_and_grounding_preserved_through_parent_finalize(self) -> None:
        execution_id = "synth-preserve"
        self._seed_t0_escalate_row(execution_id)
        packet = _evidence_packet()
        child_answer = (
            "Based on repository intelligence for intake.py, validate_batch "
            "keeps only rows marked ok before ledger write."
        )
        out = self.executor._finalize_synthesis_or_agent_answer(
            execution_id,
            prompt="Explain how validate_batch works in this repository",
            run={
                "id": "child-3",
                "status": "completed",
                "answer": child_answer,
                "model": "gpt-5.6-terra",
                "agent_id": "openai-api",
                "usage": {"input_tokens": 5, "output_tokens": 9, "total_tokens": 14},
            },
            repository_ids=["live-processing-local"],
            evidence_packet=packet,
            synthesis_escalation=True,
            chosen="openai-api",
        )
        self.assertEqual(out.get("status"), "completed")
        self.assertIn("validate_batch", str(out.get("answer") or ""))
        self.assertIn("intake.py", str(out.get("answer") or ""))
        g = out.get("grounding") or {}
        self.assertEqual(g.get("evidence_found_label"), "Yes")
        self.assertEqual(g.get("task_solved_label"), "Yes")
        self.assertEqual(g.get("grounded_label"), "Yes")
        sources = (out.get("evidence_packet") or {}).get("sources") or []
        self.assertTrue(any("repository_intelligence" in str(s) for s in sources))
        self.assertTrue(
            (out.get("repository_intelligence_diagnostics") or {}).get("used")
        )


class HybridTelemetryUnitTests(unittest.TestCase):
    def test_build_marks_hybrid_from_fallback(self) -> None:
        tel = build_execution_telemetry(
            {
                "mode": "ask",
                "provider_id": "openai-api",
                "adapter_id": "openai-api",
                "fallback_from": "deterministic",
                "agent_run_id": "c1",
                "model": "gpt-5.6-terra",
                "usage": {"input_tokens": 1, "output_tokens": 2, "total_tokens": 3},
            }
        )
        self.assertEqual(tel["execution_type"], EXEC_HYBRID)
        self.assertTrue(tel["llm_invoked"])
        self.assertTrue(str(tel.get("route_path") or "").startswith("T0 →"))


if __name__ == "__main__":
    unittest.main()
